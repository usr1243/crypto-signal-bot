"""
Stufe 5: ECHTE ORDER-AUSFUEHRUNG. Bewusst der letzte Baustein, mehrfach
abgesichert -- siehe PLAN.md Abschnitt 9 (Abbruchkriterien) und Abschnitt 7
(Risikomanagement).

Dieser Code wurde in der Entwicklung NIE gegen ein echtes Konto ausgefuehrt
-- es gibt in dieser Umgebung keine Exchange-Keys, und das ist Absicht,
nicht eine Luecke. Bevor irgendjemand das hier scharf schaltet, MUESSEN
gelten:

  1. Stufe 4 (Paper-Trading, mindestens 4-8 Wochen) ist abgeschlossen und
     das Ergebnis rechtfertigt Live-Kapital (PLAN.md §9).
  2. Der API-Key hat NUR "Trade"-Rechte, niemals "Withdrawal".
  3. LIVE_TRADING_ENABLED=true steht explizit in .env -- Standard ist AUS.
  4. Es sind die Keys des Kontoinhabers selbst (Kollege), nie die von Lorenz.

Ohne LIVE_TRADING_ENABLED=true tut place_order() NIENAND -- es loggt nur,
was es getan HAETTE, und gibt einen "simulated"-Status zurueck. Das macht
den Telegram-Button-Flow (Stufe 5 UX) testbar, ohne dass aus Versehen
echtes Geld bewegt wird.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import ccxt


@dataclass
class OrderResult:
    status: str  # "simulated" | "placed" | "error"
    order_id: str | None
    detail: str


def live_trading_enabled() -> bool:
    return os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true"


def place_order(
    exchange_id: str,
    symbol: str,
    direction: str,  # "LONG" | "SHORT"
    amount_base_currency: float,
    stop_price: float,
    target_price: float,
) -> OrderResult:
    """
    Platziert eine Market-Order + OCO-artige Stop/Ziel-Absicherung (sofern
    die Boerse das unterstuetzt -- sonst muesste run_loop.py Stop/Ziel
    selbst ueberwachen und Exit-Orders manuell nachschicken, wie es
    check_and_close() im Paper-Modus bereits simuliert).

    Ohne LIVE_TRADING_ENABLED=true: reiner Log, kein API-Call, kein Risiko.
    """
    side = "buy" if direction == "LONG" else "sell"

    if not live_trading_enabled():
        msg = (f"[SIMULATION] wuerde {side} {amount_base_currency} {symbol} auf {exchange_id} "
               f"platzieren, Stop {stop_price}, Ziel {target_price}. "
               f"LIVE_TRADING_ENABLED ist nicht 'true' -- keine echte Order gesendet.")
        print(msg)
        return OrderResult(status="simulated", order_id=None, detail=msg)

    api_key = os.environ.get("EXCHANGE_API_KEY")
    api_secret = os.environ.get("EXCHANGE_API_SECRET")
    if not api_key or not api_secret:
        detail = "LIVE_TRADING_ENABLED=true, aber EXCHANGE_API_KEY/SECRET fehlen. Abgebrochen."
        print(f"[FEHLER] {detail}")
        return OrderResult(status="error", order_id=None, detail=detail)

    try:
        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class({
            "apiKey": api_key, "secret": api_secret, "enableRateLimit": True,
        })
        order = exchange.create_order(symbol, "market", side, amount_base_currency)
        detail = f"Order platziert: {order.get('id')}. Stop {stop_price}/Ziel {target_price} MUESSEN separat ueberwacht werden."
        print(f"[LIVE] {detail}")
        return OrderResult(status="placed", order_id=order.get("id"), detail=detail)
    except Exception as exc:
        detail = f"Order fehlgeschlagen: {exc.__class__.__name__}: {exc}"
        print(f"[FEHLER] {detail}")
        return OrderResult(status="error", order_id=None, detail=detail)
