"""
Stufe 4: Paper-Trading-Engine. Simuliert Positionen mit echten Kosten
(gleiche Fee/Slippage-Annahmen wie backtest.py -- eine Quelle der Wahrheit),
ohne echtes Geld. PLAN.md: mindestens 4, besser 8 Wochen, bevor ueberhaupt
ueber Live-Kapital nachgedacht wird.

Enthaelt auch die Circuit-Breaker aus PLAN.md Abschnitt 7:
  - Tagesverlust-Limit: -3% -> keine neuen Trades bis zum naechsten Tag
  - Max-Drawdown: -15% vom Peak -> Bot stoppt komplett, manueller Reset noetig
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from .backtest import FEE_PCT, SLIPPAGE_PCT
from .signals import TechnicalSignal, TradeProposal

DAILY_LOSS_LIMIT_PCT = -3.0
MAX_DRAWDOWN_PCT = -15.0

# PLAN.md Abschnitt 7 fordert eine harte Obergrenze gegen Kontrollverlust.
# Vorher gab es GAR KEINE Sperre: TIMEFRAMES=1h,15m eroeffnete auf demselben
# Symbol zwei unabhaengige Positionen (belegt: Signale 77/79, beide ETH/USD,
# beide 2026-09-04 08:57:57 -- zwei getrennte Trades). Das macht jede seitdem
# gesammelte Paper-Trading-Statistik als Nachweis wertlos.
MAX_OPEN_POSITIONS = 3


def can_open_trade(conn: sqlite3.Connection, symbol: str) -> tuple[bool, str | None]:
    """Prueft die Positions-Sicherheitsregeln VOR einer Eroeffnung. Getrennt von
    open_trade(), damit der Aufrufer (z.B. run_loop.signal_job) den Grund fuer
    ein Ueberspringen loggen/melden kann, statt nur stillschweigend nichts zu tun."""
    if get_open_trades(conn, symbol=symbol):
        return False, f"bereits offene Position auf {symbol} -- kein Doppel-Einstieg"
    if len(get_open_trades(conn)) >= MAX_OPEN_POSITIONS:
        return False, f"Positions-Obergrenze erreicht ({MAX_OPEN_POSITIONS} gleichzeitig offene Trades)"
    return True, None


def open_trade(conn: sqlite3.Connection, signal_id: int, sig: TechnicalSignal, proposal: TradeProposal,
                atr_multiple: float | None = None) -> int | None:
    """atr_multiple wird nur zur Protokollierung mitgegeben -- der Stop steckt
    bereits fertig im proposal. Zweck: ein Trade muss auch Monate spaeter noch
    erklaerbar sein, ohne die damalige .env zu kennen.

    Gibt None zurueck (statt eine zweite/zu-viele Position zu eroeffnen), wenn
    can_open_trade() die Eroeffnung ablehnt. Die Pruefung sitzt HIER, nicht nur
    im Aufrufer -- sonst kann sie ein zukuenftiger neuer Aufrufer (z.B. eine
    spaetere echte Ausfuehrung) versehentlich umgehen."""
    allowed, _reason = can_open_trade(conn, sig.symbol)
    if not allowed:
        return None
    cur = conn.execute(
        """INSERT INTO paper_trades
           (signal_id, symbol, direction, entry, stop, target, size_pct,
            reward_risk_ratio, atr_multiple, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
        (signal_id, sig.symbol, sig.candidate, proposal.entry, proposal.stop, proposal.target,
         proposal.size_pct_of_account, proposal.reward_risk_ratio, atr_multiple),
    )
    conn.commit()
    return int(cur.lastrowid)


@dataclass
class OpenPosition:
    id: int
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    size_pct: float


def get_open_trades(conn: sqlite3.Connection, symbol: str | None = None) -> list[OpenPosition]:
    q = "SELECT id, symbol, direction, entry, stop, target, size_pct FROM paper_trades WHERE status='open'"
    params: tuple = ()
    if symbol:
        q += " AND symbol=?"
        params = (symbol,)
    rows = conn.execute(q, params).fetchall()
    return [OpenPosition(*r) for r in rows]


def check_and_close(conn: sqlite3.Connection, symbol: str, current_price: float) -> list[dict]:
    """
    Grobe Pruefung anhand des aktuellen Preises (nicht der echten Kerzen-
    High/Low wie im Backtest -- fuer den Live/Paper-Loop reicht ein
    regelmaessiger Preis-Poll; siehe README fuer die bekannte Einschraenkung:
    ein kurzer Spike zwischen zwei Polls, der Stop/Ziel beruehrt und wieder
    zurueckkommt, wird nicht erfasst).

    Gibt eine Liste geschlossener Trades (als dict) zurueck, fuer Telegram-
    Exit-Alerts im Aufrufer.
    """
    closed = []
    for pos in get_open_trades(conn, symbol=symbol):
        hit_stop = current_price <= pos.stop if pos.direction == "LONG" else current_price >= pos.stop
        hit_target = current_price >= pos.target if pos.direction == "LONG" else current_price <= pos.target

        if not (hit_stop or hit_target):
            continue

        reason = "stop" if hit_stop else "target"  # konservativ: Stop zuerst pruefen
        exit_price = pos.stop if hit_stop else pos.target
        pnl_pct = _r_multiple(pos, exit_price) * pos.size_pct

        conn.execute(
            """UPDATE paper_trades SET status=?, closed_price=?, pnl_pct=?, closed_at=?
               WHERE id=?""",
            (reason, exit_price, pnl_pct, datetime.now(timezone.utc).isoformat(), pos.id),
        )
        conn.commit()
        closed.append({
            "id": pos.id, "symbol": pos.symbol, "direction": pos.direction,
            "reason": reason, "exit_price": exit_price, "pnl_pct": round(pnl_pct, 3),
        })
    return closed


def _r_multiple(pos: OpenPosition, exit_price: float) -> float:
    direction_sign = 1 if pos.direction == "LONG" else -1
    entry_eff = pos.entry * (1 + direction_sign * SLIPPAGE_PCT)
    exit_eff = exit_price * (1 - direction_sign * SLIPPAGE_PCT)
    gross = direction_sign * (exit_eff - entry_eff)
    fee_drag = FEE_PCT * (pos.entry + exit_price)
    net = gross - fee_drag
    risk_distance = abs(pos.entry - pos.stop)
    return net / risk_distance if risk_distance > 0 else 0.0


# ---------------------------------------------------------------------------
# Circuit-Breaker
# ---------------------------------------------------------------------------

# Rollierendes Fenster statt "fuer immer": vorher lief equity.cummax() ueber die
# GESAMTE Historie -- ein einmal erreichter Max-Drawdown blieb dadurch dauerhaft
# aktiv, obwohl der Text schon "manueller Reset noetig" versprach (es gab aber
# gar keinen Reset-Mechanismus im ganzen Projekt). Jetzt: automatisches
# 30-Tage-Fenster PLUS echter manueller Reset (reset_circuit_breaker).
DRAWDOWN_WINDOW_DAYS = 30


def _breaker_reset_at(conn: sqlite3.Connection) -> str:
    """Aeltester Zeitpunkt, ab dem ein Trade noch fuer den Circuit-Breaker zaehlt --
    der juengere von (a) einem manuellen /reset_breaker und (b) dem rollierenden
    Fenster, damit ein einmaliger Drawdown nicht ohne aktives Zutun fuer immer
    aktiv bleibt."""
    row = conn.execute("SELECT value FROM bot_state WHERE key='circuit_breaker_reset_at'").fetchone()
    manual_reset = row[0] if row else "1970-01-01T00:00:00+00:00"
    window_start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=DRAWDOWN_WINDOW_DAYS)).isoformat()
    return max(manual_reset, window_start)


def reset_circuit_breaker(conn: sqlite3.Connection) -> None:
    """Setzt den Circuit-Breaker manuell zurueck (Telegram: /reset_breaker, nur
    Admin). Trades vor diesem Zeitpunkt zaehlen danach nicht mehr fuer
    Tagesverlust/Max-Drawdown -- ein bewusster menschlicher Schritt, kein
    stillschweigendes Verschwinden des Alarms."""
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES ('circuit_breaker_reset_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (pd.Timestamp.now(tz="UTC").isoformat(),),
    )
    conn.commit()


def check_circuit_breakers(conn: sqlite3.Connection) -> tuple[bool, str | None]:
    """Gibt (gestoppt: bool, grund) zurueck. gestoppt=True -> main-Loop soll KEINE neuen Trades oeffnen.

    Zwei Korrekturen ggue. der Erstversion:
    1. Unrealisierte Verluste zaehlten vorher NULL (nur status IN ('stop','target')
       wurde abgefragt) -- offene Positionen gehen jetzt mit ihrem Worst-Case
       (Verlust bei Stop-Treffer = -size_pct% Konto je Position) in die
       Tagesverlust-Pruefung ein. Ohne Live-Preis pro Symbol ist das eine
       konservative Naeherung, keine exakte unrealisierte PnL -- fuer einen
       Sicherheits-Breaker ist "eher zu vorsichtig" die richtige Richtung.
    2. Der Drawdown lief ueber die gesamte Historie und blieb nach einmaligem
       Ausloesen fuer immer aktiv -- siehe _breaker_reset_at/reset_circuit_breaker.

    Gilt bisher nur fuer den Paper-Pfad (aufgerufen aus signal_job) -- ein
    Ausfuehrungspfad, den er zusaetzlich absichern muesste, existiert noch
    nicht (siehe PLAN Stufe C5: die Pruefung gehoert dort in place_order()
    selbst, sobald es das gibt)."""
    since = _breaker_reset_at(conn)
    rows = conn.execute(
        "SELECT pnl_pct, closed_at FROM paper_trades WHERE status IN ('stop','target') "
        "AND closed_at > ? ORDER BY closed_at",
        (since,),
    ).fetchall()

    open_positions = get_open_trades(conn)
    open_worst_case = -sum(p.size_pct for p in open_positions)

    today = pd.Timestamp.now(tz="UTC").date()
    if rows:
        pnl = pd.Series([r[0] for r in rows])
        closed_at = pd.to_datetime([r[1] for r in rows], utc=True)
        today_realized = float(pnl[closed_at.date == today].sum())
    else:
        pnl = pd.Series(dtype=float)
        today_realized = 0.0

    today_total = today_realized + open_worst_case
    if today_total <= DAILY_LOSS_LIMIT_PCT:
        return True, (
            f"Tagesverlust-Limit erreicht: {today_realized:+.2f}% realisiert "
            f"{open_worst_case:+.2f}% Worst-Case aus {len(open_positions)} offener Position(en) "
            f"= {today_total:+.2f}% (Limit {DAILY_LOSS_LIMIT_PCT}%)"
        )

    if rows:
        equity = (1 + pnl / 100).cumprod()
        drawdown = (equity / equity.cummax() - 1) * 100
        max_dd = float(drawdown.min())
        if max_dd <= MAX_DRAWDOWN_PCT:
            return True, (
                f"Max-Drawdown-Circuit-Breaker: {max_dd:.2f}% (Limit {MAX_DRAWDOWN_PCT}%, "
                f"Fenster seit {since[:10]}) -- /reset_breaker (Admin) zum manuellen Zuruecksetzen"
            )

    return False, None


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def weekly_report(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """SELECT symbol, direction, pnl_pct, status, closed_at FROM paper_trades
           WHERE status IN ('stop','target') AND closed_at >= datetime('now', '-7 days')
           ORDER BY closed_at"""
    ).fetchall()

    if not rows:
        return "📊 WOCHENREPORT (Paper-Trading)\n\nKeine abgeschlossenen Trades in den letzten 7 Tagen."

    pnl = pd.Series([r[2] for r in rows])
    wins = pnl[pnl > 0]
    win_rate = len(wins) / len(pnl) * 100
    total = pnl.sum()

    lines = [
        "📊 WOCHENREPORT (Paper-Trading)", "",
        f"Trades: {len(rows)} · Winrate {win_rate:.0f}% · Summe {total:+.2f}% Konto",
        "",
    ]
    for symbol, direction, pnl_pct, status, closed_at in rows[-10:]:
        icon = "✅" if pnl_pct > 0 else "❌"
        lines.append(f"{icon} {symbol} {direction} · {pnl_pct:+.2f}% · {status} · {closed_at[:10]}")

    halted, reason = check_circuit_breakers(conn)
    if halted:
        lines += ["", f"🛑 Circuit-Breaker aktiv: {reason}"]

    return "\n".join(lines)


def daily_macro_briefing(macro_state: dict | None) -> str:
    if not macro_state:
        return "🌅 MAKRO-BRIEFING\n\nSchicht B nicht verfuegbar heute (siehe Log)."

    lines = [
        "🌅 MAKRO-BRIEFING", "",
        f"Regime: {macro_state['regime']} (Konfidenz {macro_state['confidence']*100:.0f}%)", "",
    ]
    for d in macro_state.get("drivers", []):
        lines.append(f"· {d}")
    lines += ["", macro_state.get("reasoning", "")]
    return "\n".join(lines)
