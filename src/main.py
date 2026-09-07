"""
Kompletter Signal-Lauf: Schicht A (Technik, immer) + optional Schicht B
(Makro/LLM, --with-macro) -> Gate-Kopplung -> SQLite -> Telegram/Dry-Run.

Schicht B ist standardmaessig AUS (--with-macro zum Einschalten), weil sie
einen LLM-Zugang braucht (siehe README: LLM_BACKEND=cli oder api in .env).
Ohne --with-macro laeuft main.py wie in Stufe 0/1: reine Technik.

`generate_signal()` ist der wiederverwendbare Kern (Schicht A + Gate +
Schicht B, ohne Telegram-Versand) -- run_loop.py (Stufe 4, Dauerbetrieb
mit Paper-Trading) ruft dieselbe Funktion auf statt die Logik zu duplizieren.

Aufruf:
    ./.venv/bin/python -m src.main
    ./.venv/bin/python -m src.main --symbol ETH/USD --timeframe 4h
    ./.venv/bin/python -m src.main --with-macro
    ./.venv/bin/python -m src.main --with-macro --force-macro-refresh
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .adapters.crypto_ccxt import CCXTAdapter
from . import db
from .notify import format_signal_message, send_telegram
from .signals import TechnicalSignal, TradeProposal, apply_trend_filter, build_technical_signal, build_trade_proposal

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "trading_bot.sqlite3"

# Grobe Zuordnung Entry-Timeframe -> Trend-Timeframe fuer die Multi-Timeframe-Kopplung.
TREND_TF_MAP = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}


@dataclass
class SignalRun:
    signal: TechnicalSignal
    proposal: TradeProposal | None
    macro: dict | None
    signal_id: int


def _get_macro_state(conn: sqlite3.Connection, force_refresh: bool, llm_backend_kind: str) -> dict | None:
    """
    Schicht B: nur einmal pro gueltigem Fenster (valid_until, siehe macro.py)
    neu berechnen, sonst den letzten Snapshot aus SQLite wiederverwenden --
    kein LLM-Call bei jedem Lauf.
    """
    if not force_refresh:
        cached = db.get_fresh_macro_state(conn)
        if cached:
            print(f"[macro] verwende gecachten Snapshot vom {cached.get('generated_at')} "
                  f"(Regime {cached.get('regime')})")
            return cached

    try:
        from .llm.backend import get_llm_backend
        from .macro import build_macro_state
        from .sentiment import get_sentiment_backend

        llm = get_llm_backend(llm_backend_kind)
        sentiment = get_sentiment_backend("auto")
        state = build_macro_state(llm, sentiment)
        db.save_macro_state(conn, state)
        return state
    except Exception as exc:
        print(f"[macro] Schicht B nicht verfuegbar ({exc.__class__.__name__}: {exc}) -- "
              f"laeuft ohne Makro-Filter weiter (Schicht A entscheidet allein).")
        return None


def generate_signal(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    exchange_id: str,
    trend_timeframe: str | None = "auto",
    with_macro: bool = False,
    force_macro_refresh: bool = False,
    llm_backend_kind: str = "auto",
    reward_risk_ratio: float | None = None,
    atr_multiple: float | None = None,
) -> SignalRun:
    """
    Schicht A + Multi-Timeframe-Filter + Gate/Schicht B + DB-Log.
    KEIN Telegram-Versand hier -- das entscheidet der Aufrufer (main.run()
    schickt immer, run_loop.py nur bei echtem Kandidaten/Ereignis).

    reward_risk_ratio / atr_multiple: None = Wert aus .env (REWARD_RISK_RATIO /
    ATR_MULTIPLE), sonst der uebergebene. Vorher war beides im Live-Pfad hart
    auf die Defaults von build_trade_proposal() verdrahtet -- optimize.py konnte
    zwar bessere Werte finden, aber es gab keinen Weg, sie in den Betrieb zu
    uebernehmen, ohne Code zu aendern.
    """
    rr = reward_risk_ratio if reward_risk_ratio is not None else float(os.environ.get("REWARD_RISK_RATIO", "3.0"))
    atr_m = atr_multiple if atr_multiple is not None else float(os.environ.get("ATR_MULTIPLE", "2.0"))
    adapter = CCXTAdapter(exchange_id=exchange_id)
    if trend_timeframe is None:
        trend_tf = None
    elif trend_timeframe == "auto":
        trend_tf = TREND_TF_MAP.get(timeframe)
    else:
        trend_tf = trend_timeframe

    print(f"[{exchange_id}] hole OHLCV fuer {symbol} ({timeframe}) ...")
    ohlcv = adapter.ohlcv(symbol, timeframe, limit=300)
    print(f"  {len(ohlcv)} Kerzen, letzte: {ohlcv.index[-1]} @ {ohlcv['close'].iloc[-1]:,.2f}")

    sig = build_technical_signal(ohlcv, symbol=symbol, timeframe=timeframe)
    print(f"  Score {sig.score}/100 -> Kandidat (vor Trend-Filter): {sig.candidate}")

    if trend_tf:
        print(f"  hole {trend_tf}-Trendfilter ...")
        trend_ohlcv = adapter.ohlcv(symbol, trend_tf, limit=300)
        trend_sig = build_technical_signal(trend_ohlcv, symbol=symbol, timeframe=trend_tf)
        sig = apply_trend_filter(sig, trend_sig)
        if sig.trend.get("filtered_reason"):
            print(f"  -> vom Trendfilter geblockt: {sig.trend['filtered_reason']}")

    print(f"  Kandidat final (Schicht A): {sig.candidate}")

    macro_state = None
    size_multiplier = 1.0
    if with_macro and sig.candidate != "NONE":
        macro_state = _get_macro_state(conn, force_macro_refresh, llm_backend_kind)
        if macro_state:
            veto_key = "veto_long" if sig.candidate == "LONG" else "veto_short"
            if macro_state.get(veto_key):
                print(f"  -> Schicht B VETO ({macro_state['regime']}): {macro_state.get('reasoning')}")
                sig.trend["macro_veto_reason"] = macro_state.get("reasoning")
                sig.candidate = "NONE"
            else:
                size_multiplier = float(macro_state.get("size_multiplier", 1.0))
    elif with_macro and sig.candidate == "NONE":
        # Kein technisches Signal -> Schicht B wird laut Gate-Regel gar nicht erst gebraucht
        # (sie kann nur bremsen, nicht selbst ausloesen). Spart einen LLM-Call.
        print("  Schicht B uebersprungen (kein technisches Signal zum Filtern).")

    print(f"  Kandidat final (nach Schicht B): {sig.candidate}")
    proposal = build_trade_proposal(
        sig, size_multiplier=size_multiplier,
        reward_risk_ratio=rr, atr_multiple=atr_m,
    )

    signal_id = db.save_signal(
        conn,
        technical=sig.to_dict(),
        trade_proposal=proposal.__dict__ if proposal else None,
        macro=macro_state,
    )
    print(f"  Signal #{signal_id} gespeichert.")

    return SignalRun(signal=sig, proposal=proposal, macro=macro_state, signal_id=signal_id)


def run(
    symbol: str,
    timeframe: str,
    exchange_id: str,
    trend_timeframe: str | None = "auto",
    with_macro: bool = False,
    force_macro_refresh: bool = False,
    llm_backend_kind: str = "auto",
    reward_risk_ratio: float | None = None,
    atr_multiple: float | None = None,
) -> None:
    """CLI-Einstieg: ein einzelner Lauf, Nachricht geht immer raus (auch bei NONE)."""
    conn = db.connect(DB_PATH)
    result = generate_signal(
        conn, symbol, timeframe, exchange_id, trend_timeframe,
        with_macro, force_macro_refresh, llm_backend_kind,
        reward_risk_ratio=reward_risk_ratio, atr_multiple=atr_multiple,
    )

    message = format_signal_message(result.signal, result.proposal, macro=result.macro, macro_enabled=with_macro)
    sent = send_telegram(message)
    if sent:
        print("  -> per Telegram verschickt.")
    else:
        print("  -> Dry-Run (siehe Ausgabe oben). TELEGRAM_BOT_TOKEN/CHAT_ID in .env setzen fuer echten Versand.")


def main() -> None:
    load_dotenv(BASE_DIR / ".env")

    parser = argparse.ArgumentParser(description="Trading-Bot -- Signal-Lauf")
    parser.add_argument("--symbol", default=os.environ.get("SYMBOL", "BTC/USD"))
    parser.add_argument("--timeframe", default=os.environ.get("TIMEFRAME", "1h"))
    parser.add_argument("--exchange", default=os.environ.get("EXCHANGE", "kraken"))
    parser.add_argument("--trend-timeframe", default="auto",
                        help="Uebergeordnete Zeitebene fuer den Trendfilter (Default: Auto-Mapping)")
    parser.add_argument("--no-trend-filter", action="store_true",
                        help="Multi-Timeframe-Filter deaktivieren (nur Entry-TF)")
    parser.add_argument("--with-macro", action="store_true",
                        help="Schicht B (Makro/LLM) als Gate/Veto aktivieren -- braucht LLM_BACKEND in .env")
    parser.add_argument("--force-macro-refresh", action="store_true",
                        help="Makro-Snapshot neu berechnen statt gecachten (siehe valid_until) zu nutzen")
    parser.add_argument("--llm-backend", default=os.environ.get("LLM_BACKEND", "auto"),
                        choices=["auto", "cli", "api", "groq", "mock"])
    parser.add_argument("--rr", type=float, default=None,
                        help="Reward:Risk pro Trade (Ziel = X-fache Stop-Distanz). Default: REWARD_RISK_RATIO aus .env, sonst 3.0")
    parser.add_argument("--atr-multiple", type=float, default=None,
                        help="Stop-Distanz in ATR-Vielfachen. Default: ATR_MULTIPLE aus .env, sonst 2.0")
    args = parser.parse_args()

    run(
        symbol=args.symbol,
        timeframe=args.timeframe,
        exchange_id=args.exchange,
        trend_timeframe=None if args.no_trend_filter else args.trend_timeframe,
        with_macro=args.with_macro,
        force_macro_refresh=args.force_macro_refresh,
        llm_backend_kind=args.llm_backend,
        reward_risk_ratio=args.rr,
        atr_multiple=args.atr_multiple,
    )


if __name__ == "__main__":
    main()
