"""
Stufe 4: Dauerbetrieb. Fuehrt main.generate_signal() auf einem Zeitplan aus,
verwaltet Paper-Trades, prueft Circuit-Breaker, verschickt Telegram-Alerts
und Reports. Das ist der Prozess, der 4-8 Wochen am Stueck laufen soll
(PLAN.md Stufe 4) -- lokal auf einem Mac/Raspberry Pi oder spaeter auf
einem Hetzner-VPS (siehe PLAN.md Abschnitt 4/9).

WICHTIG: das hier platziert NIE echte Orders. Alles bleibt Paper-Trading
(SQLite-Simulation) und Telegram-Alerts, bis Stufe 5 (execution.py)
explizit von einem Menschen freigeschaltet wird.

Aufruf (laeuft dauerhaft im Vordergrund, mit z.B. `screen`/`tmux`/launchd
im Hintergrund halten):
    ./.venv/bin/python -m src.run_loop
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from . import db, paper_trading
from .adapters.crypto_ccxt import CCXTAdapter
from .main import DB_PATH, generate_signal
from .notify import format_signal_message, send_telegram
from .paper_trading import daily_macro_briefing, weekly_report

BASE_DIR = Path(__file__).resolve().parent.parent

# Grobe Minuten-Intervalle fuer den Signal-Check je Timeframe (nicht exakt
# an Kerzenschluss ausgerichtet -- fuer den Erstentwurf ausreichend, siehe README).
CHECK_INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}

# Totmann-Schalter (PLAN Stufe A3): reine In-Memory-Zaehler fuer den Heartbeat.
# Kein DB-Aufwand fuer eine Lebenszeichen-Metrik, und ein Reset auf 0 bei jedem
# Prozess-Neustart ist bei "seit dem letzten Heartbeat" kein Problem.
_heartbeat_stats = {"checks": 0, "candidates": 0}


def _alert_error(context: str, exc: Exception) -> None:
    """Fehler in einem Scheduler-Job gingen bisher NUR in stdout -- in ein
    Docker-Log, das niemand liest. Genau deshalb blieb der 18h-Netzausfall am
    04.09. unbemerkt: ein toter und ein ruhiger Bot sahen von aussen gleich
    aus. Jetzt zusaetzlich per Telegram, best-effort -- schlaegt sogar der
    Telegram-Versand fehl, darf das den urspruenglichen Fehler nicht schlucken."""
    print(f"  [FEHLER in {context}] {exc.__class__.__name__}: {exc}")
    try:
        send_telegram(f"⚠️ FEHLER in {context}\n{exc.__class__.__name__}: {exc}")
    except Exception as telegram_exc:
        print(f"  [FEHLER] Telegram-Alarm selbst fehlgeschlagen: {telegram_exc}")


def signal_job(symbol: str, timeframe: str, exchange_id: str, with_macro: bool, llm_backend_kind: str) -> None:
    print(f"\n[{datetime.now(timezone.utc).isoformat()}] signal_job {symbol} {timeframe}")
    try:
        conn = db.connect_with_retry(DB_PATH)
        _heartbeat_stats["checks"] += 1

        if db.is_paused(conn):
            print("  /pause aktiv (Telegram) -- kein neuer Signal-Check.")
            return

        halted, reason = paper_trading.check_circuit_breakers(conn)
        if halted:
            print(f"  Circuit-Breaker aktiv, keine neuen Trades: {reason}")
            return

        result = generate_signal(
            conn, symbol, timeframe, exchange_id,
            with_macro=with_macro, llm_backend_kind=llm_backend_kind,
        )

        if result.signal.candidate == "NONE":
            print("  kein Kandidat -- keine Telegram-Nachricht (kein Spam bei jedem Lauf).")
            return

        _heartbeat_stats["candidates"] += 1
        message = format_signal_message(result.signal, result.proposal, macro=result.macro, macro_enabled=with_macro)
        send_telegram(message)

        if result.proposal:
            # Sperre sitzt in paper_trading.open_trade() selbst (gibt None statt
            # einer zweiten Position zurueck) -- can_open_trade() hier nur, um den
            # Grund fuers Ueberspringen konkret zu loggen statt stillschweigend
            # nichts zu tun. Siehe PLAN Stufe A1: TIMEFRAMES=1h,15m eroeffnete
            # bisher dieselbe Position doppelt.
            allowed, block_reason = paper_trading.can_open_trade(conn, result.signal.symbol)
            if not allowed:
                print(f"  Kein Paper-Trade: {block_reason}")
            else:
                trade_id = paper_trading.open_trade(
                    conn, result.signal_id, result.signal, result.proposal,
                    atr_multiple=float(os.environ.get("ATR_MULTIPLE", "2.0")),
                )
                print(f"  Paper-Trade #{trade_id} eroeffnet.")
    except Exception as exc:
        _alert_error("signal_job", exc)


def check_trades_job(symbols: list[str], exchange_id: str) -> None:
    try:
        conn = db.connect_with_retry(DB_PATH)
        adapter = CCXTAdapter(exchange_id=exchange_id)
        for symbol in symbols:
            if not paper_trading.get_open_trades(conn, symbol=symbol):
                continue
            price = adapter.last_price(symbol)
            closed = paper_trading.check_and_close(conn, symbol, price)
            for c in closed:
                icon = "✅" if c["pnl_pct"] > 0 else "❌"
                send_telegram(
                    f"{icon} PAPER-TRADE GESCHLOSSEN · {c['symbol']} {c['direction']}\n"
                    f"Grund: {c['reason']} @ {c['exit_price']:,.2f}\n"
                    f"PnL: {c['pnl_pct']:+.2f}% Konto"
                )
    except Exception as exc:
        _alert_error("check_trades_job", exc)


def heartbeat_job() -> None:
    """Totmann-Schalter (PLAN Stufe A3): meldet sich periodisch selbst, egal ob
    es etwas zu berichten gibt. Bleibt diese Meldung aus, ist etwas kaputt --
    das faellt dann in Stunden auf statt wie am 04.09. nach 18 Stunden."""
    try:
        checks = _heartbeat_stats["checks"]
        candidates = _heartbeat_stats["candidates"]
        send_telegram(
            f"🟢 Bot lebt · seit letztem Heartbeat: {checks} Signal-Check(s), "
            f"{candidates} Kandidat(en)"
        )
        _heartbeat_stats["checks"] = 0
        _heartbeat_stats["candidates"] = 0
    except Exception as exc:
        print(f"  [FEHLER in heartbeat_job] {exc.__class__.__name__}: {exc}")


def macro_refresh_job(llm_backend_kind: str) -> None:
    """
    Haelt Schicht B proaktiv frisch, UNABHAENGIG davon, ob gerade ein
    technisches Signal vorliegt -- ohne das wuerde ein Makro-Snapshot erst
    dann neu geholt, wenn zufaellig ein Kandidat auftaucht UND der Cache
    (siehe macro.py: valid_until, 4h) gerade abgelaufen ist. Nachrichten
    koennen aber jederzeit passieren, nicht nur wenn Schicht A anschlaegt.

    Schickt KEINE Telegram-Nachricht (das macht weiterhin nur
    daily_briefing_job 1x/Tag) -- sonst wuerde das alle paar Stunden spammen.
    Aktualisiert nur den Cache, damit die naechste echte Signal-Pruefung
    einen aktuellen Stand vorfindet statt einen von vor bis zu 4 Stunden.
    """
    try:
        conn = db.connect_with_retry(DB_PATH)
        from .main import _get_macro_state
        state = _get_macro_state(conn, force_refresh=True, llm_backend_kind=llm_backend_kind)
        if state:
            print(f"  [macro_refresh_job] aktualisiert: {state.get('regime')} "
                  f"(gueltig bis {state.get('valid_until', '?')[:16]})")
    except Exception as exc:
        _alert_error("macro_refresh_job", exc)


def daily_briefing_job(symbol: str, timeframe: str, exchange_id: str, llm_backend_kind: str) -> None:
    try:
        conn = db.connect_with_retry(DB_PATH)
        from .main import _get_macro_state
        macro_state = _get_macro_state(conn, force_refresh=True, llm_backend_kind=llm_backend_kind)
        send_telegram(daily_macro_briefing(macro_state))
    except Exception as exc:
        _alert_error("daily_briefing_job", exc)


def weekly_report_job() -> None:
    try:
        conn = db.connect_with_retry(DB_PATH)
        send_telegram(weekly_report(conn))
    except Exception as exc:
        _alert_error("weekly_report_job", exc)


def main() -> None:
    load_dotenv(BASE_DIR / ".env")

    symbol = os.environ.get("SYMBOL", "BTC/USD")
    symbols = [s.strip() for s in os.environ.get("SYMBOLS", symbol).split(",") if s.strip()]
    # TIMEFRAMES (Mehrzahl) haengt zusaetzliche, UNABHAENGIGE Zeitebenen an --
    # jede bekommt ihren eigenen Scheduler-Job mit eigenem Takt und eigenen
    # Telegram-Alerts, statt sie wie freqtrades "Informative Pairs" in EINE
    # Entscheidung zu verschmelzen (das macht main.py schon separat ueber den
    # Trendfilter, siehe apply_trend_filter). Default: nur TIMEFRAME, wie bisher.
    timeframe = os.environ.get("TIMEFRAME", "1h")
    timeframes = [tf.strip() for tf in os.environ.get("TIMEFRAMES", timeframe).split(",") if tf.strip()]
    exchange_id = os.environ.get("EXCHANGE", "kraken")
    with_macro = os.environ.get("WITH_MACRO", "false").lower() == "true"
    llm_backend_kind = os.environ.get("LLM_BACKEND", "auto")
    heartbeat_hours = float(os.environ.get("HEARTBEAT_INTERVAL_HOURS", "6"))

    print("=" * 60)
    print("Trading-Bot -- Dauerbetrieb (Stufe 4, Paper-Trading)")
    print(f"Symbole: {symbols} · Zeitebenen: {timeframes} · Boerse: {exchange_id}")
    print(f"Schicht B: {'AN' if with_macro else 'AUS'} · LLM-Backend: {llm_backend_kind}")
    for tf in timeframes:
        print(f"  {tf}: Signal-Check alle {CHECK_INTERVAL_MINUTES.get(tf, 60)} Minuten")
    print("Positions-Check alle 5 Minuten"
          + (" · Makro-Refresh alle 4 Stunden" if with_macro else ""))
    print(f"Heartbeat alle {heartbeat_hours:g} Stunden · max. {paper_trading.MAX_OPEN_POSITIONS} gleichzeitig offene Positionen")
    print("KEINE echten Orders -- reines Paper-Trading + Telegram-Alerts.")
    print("=" * 60)

    scheduler = BlockingScheduler(timezone="UTC")

    for tf in timeframes:
        interval_min = CHECK_INTERVAL_MINUTES.get(tf, 60)
        for sym in symbols:
            scheduler.add_job(
                signal_job, IntervalTrigger(minutes=interval_min),
                args=[sym, tf, exchange_id, with_macro, llm_backend_kind],
                id=f"signal_{sym}_{tf}", next_run_time=datetime.now(timezone.utc),
            )

    scheduler.add_job(
        check_trades_job, IntervalTrigger(minutes=5),
        args=[symbols, exchange_id], id="check_trades",
    )

    scheduler.add_job(
        heartbeat_job, IntervalTrigger(hours=heartbeat_hours),
        id="heartbeat", next_run_time=datetime.now(timezone.utc),
    )

    if with_macro:
        # Alle 4h proaktiv aktualisieren (== macro.py valid_until) statt nur
        # traege nachzuladen, wenn zufaellig ein technisches Signal ansteht.
        # Kein Telegram-Spam, nur der Cache wird frisch gehalten.
        scheduler.add_job(
            macro_refresh_job, IntervalTrigger(hours=4),
            args=[llm_backend_kind], id="macro_refresh",
            next_run_time=datetime.now(timezone.utc),
        )

    scheduler.add_job(
        daily_briefing_job, CronTrigger(hour=7, minute=0, timezone="UTC"),
        args=[symbols[0], timeframe, exchange_id, llm_backend_kind], id="daily_briefing",
    )

    scheduler.add_job(
        weekly_report_job, CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="UTC"),
        id="weekly_report",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nBeendet.")


if __name__ == "__main__":
    main()
