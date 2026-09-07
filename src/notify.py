"""
Formatiert die Signal-Nachricht (Format aus PLAN.md Abschnitt 6) und schickt
sie via Telegram Bot API. Ohne TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID laeuft
alles im Dry-Run: die Nachricht wird nur auf der Konsole ausgegeben.

Bewusst rohe Telegram-Bot-API ueber requests statt python-telegram-bot --
fuer "eine Nachricht raus schicken" ist die grosse Async-Library unnoetiges
Gewicht. Falls spaeter Inline-Buttons (Stufe 5) noetig werden, lohnt sich
der Wechsel dann.
"""

from __future__ import annotations

import os

import requests

from .signals import TechnicalSignal, TradeProposal


def format_signal_message(
    sig: TechnicalSignal, proposal: TradeProposal | None,
    macro: dict | None = None, macro_enabled: bool = False,
) -> str:
    header = {
        "LONG": "🟢 LONG-SIGNAL",
        "SHORT": "🔴 SHORT-SIGNAL",
        "NONE": "⚪ KEIN SIGNAL",
    }[sig.candidate]
    lines = [
        f"{header} · {sig.symbol} · {sig.timeframe}",
        f"Preis {sig.price:,.2f}",
        "",
        f"TECHNIK (Score {sig.score}/100)",
        *(  # die staerksten Treiber ausformuliert, nicht nur als Regelname+Punkte --
            # ein Kuerzel wie "trend -20" erklaert nichts, wenn man den Code nicht kennt.
            ["Warum:"] + [f"{c.points:+4d}  {c.detail}" for c in sig.top_drivers(4)] + [""]
            if sig.score_breakdown else []
        ),
        f"Trend    {'▲' if sig.trend['regime']=='up' else '▼'} EMA50 {'>' if sig.trend['regime']=='up' else '<'} EMA200, ADX {sig.trend['adx']}",
        f"Momentum RSI {sig.momentum['rsi14']} · MACD {sig.momentum['macd_cross']}",
        f"Vola     ATR {sig.volatility['atr_pct']}% · BB %B {sig.volatility['bb_percent_b']}",
        f"Volumen  {sig.volume['vol_z']}σ vs. Schnitt · OBV {sig.volume['obv_slope']}",
        f"Extra    Stoch {sig.structure['stoch_k']} · CCI {sig.structure['cci']} · Williams%R {sig.structure['williams_r']}",
    ]
    if sig.divergence["rsi_divergence"] != "none":
        lines.append(f"Divergenz {sig.divergence['rsi_divergence']}")
    if sig.structure.get("candle_pattern", "none") != "none":
        lines.append(f"Kerze    {sig.structure['candle_pattern']}")
    if sig.structure.get("chart_pattern", "none") != "none" and sig.structure.get("chart_pattern_confirmed"):
        lines.append(f"Muster   {sig.structure['chart_pattern']} (bestätigt @ {sig.structure['chart_pattern_level']:,.2f})")

    if macro:
        lines += [
            "",
            f"MAKRO ({macro.get('regime', '?')}, {macro.get('confidence', 0)*100:.0f}%)",
            *[f"· {d}" for d in macro.get("drivers", [])],
        ]
        veto_key = "veto_long" if sig.candidate == "LONG" else "veto_short"
        if macro.get(veto_key):
            lines.append(f"→ VETO: {macro.get('reasoning', 'kein Grund angegeben')}")
        else:
            lines.append(f"→ Kein Veto. Size-Faktor {macro.get('size_multiplier', 1.0)}")
    elif macro_enabled:
        lines += ["", "MAKRO  wird nur bei einem Signal befragt (spart unnoetige KI-Anfragen) -- aktuell keins."]
    else:
        lines += ["", "MAKRO  ausgeschaltet (main.py ohne --with-macro / run_loop ohne WITH_MACRO=true aufgerufen)"]

    if proposal:
        lines += [
            "",
            "VORSCHLAG",
            f"Entry  {proposal.entry:,.2f}",
            f"Stop   {proposal.stop:,.2f}  ({proposal.risk_pct_of_price}%)",
            f"Ziel   {proposal.target:,.2f}  (R:R {proposal.reward_risk_ratio})",
            f"Size   {proposal.size_pct_of_account}% des Kontos",
        ]

    lines += ["", "⚠️ Kein Finanzrat. Prüf es selbst."]
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    """True = wirklich verschickt, False = Dry-Run (kein Token gesetzt)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("--- DRY RUN (kein TELEGRAM_BOT_TOKEN/CHAT_ID gesetzt) ---")
        print(text)
        print("--- ENDE ---")
        return False

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    resp.raise_for_status()
    return True


def send_telegram_with_actions(text: str, signal_id: int) -> bool:
    """
    Stufe 5: Signal-Nachricht mit Inline-Buttons [Ausfuehren] [Ignorieren]
    [Spaeter]. Der Button-Klick kommt bei telegram_listener.py als
    callback_query zurueck (data = "execute:<signal_id>" etc.).

    Ohne Token: gleicher Dry-Run wie send_telegram(), Buttons werden nur
    textuell angedeutet.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("--- DRY RUN (kein TELEGRAM_BOT_TOKEN/CHAT_ID gesetzt) ---")
        print(text)
        print(f"[Ausfuehren] [Ignorieren] [Spaeter]  (signal_id={signal_id})")
        print("--- ENDE ---")
        return False

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Ausführen", "callback_data": f"execute:{signal_id}"},
            {"text": "❌ Ignorieren", "callback_data": f"ignore:{signal_id}"},
            {"text": "⏳ Später", "callback_data": f"later:{signal_id}"},
        ]]
    }
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "reply_markup": keyboard},
        timeout=10,
    )
    resp.raise_for_status()
    return True
