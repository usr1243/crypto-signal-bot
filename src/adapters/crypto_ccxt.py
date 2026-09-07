"""
Krypto-Adapter via CCXT. Nutzt nur öffentliche Endpunkte (OHLCV, Ticker) --
kein API-Key noetig fuer die Signal-Erzeugung. Keys kommen erst in Stufe 5
ins Spiel, wenn tatsaechlich Orders platziert werden.
"""

from __future__ import annotations

import ccxt
import pandas as pd


class CCXTAdapter:
    def __init__(self, exchange_id: str = "kraken") -> None:
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange: ccxt.Exchange = exchange_class({"enableRateLimit": True})
        self.exchange_id = exchange_id

    def ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts").sort_index()
        return df

    def symbols(self) -> list[str]:
        markets = self.exchange.load_markets()
        return sorted(markets.keys())

    def last_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])
