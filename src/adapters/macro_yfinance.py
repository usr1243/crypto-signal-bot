"""
Makro-Marktdaten via yfinance -- kein Key noetig. Liefert die klassischen
"Fundamentalanalyse"-Grössen aus dem Auftrag des Kollegen: Zinsumfeld
(US10Y als Proxy), Aktienmaerkte (SPX), Rohstoffe (Gold, Oel), USD-Staerke
(DXY) und Risikostimmung (VIX).
"""

from __future__ import annotations

import yfinance as yf

TICKERS = {
    "dxy": "DX-Y.NYB",   # US-Dollar-Index
    "spx": "^GSPC",       # S&P 500
    "vix": "^VIX",         # Volatility Index
    "us10y": "^TNX",        # 10-Jahres-Treasury-Rendite (in %, x10 skaliert von Yahoo)
    "gold": "GC=F",           # Gold-Future
    "oil": "CL=F",              # WTI-Crude-Future
}


def fetch_macro_snapshot() -> dict:
    """
    Gibt fuer jeden Ticker den letzten Schlusskurs + Wochenveraenderung zurueck.
    Einzelne fehlgeschlagene Ticker werden uebersprungen statt den ganzen
    Snapshot zum Absturz zu bringen (Yahoo-Endpunkte sind gelegentlich instabil).
    """
    snapshot: dict = {}
    for key, ticker in TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="7d")
            if hist.empty:
                continue
            last = float(hist["Close"].iloc[-1])
            first = float(hist["Close"].iloc[0])
            change_pct = round((last - first) / first * 100, 2) if first else None
            snapshot[key] = {"last": round(last, 2), "week_change_pct": change_pct}
        except Exception as exc:
            snapshot[key] = {"error": str(exc)}
    return snapshot
