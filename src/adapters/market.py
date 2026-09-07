"""
MarketAdapter-Protocol.

Schicht A (Technik) hängt nur an diesem Interface, nie direkt an ccxt/yfinance.
Marktwechsel (Krypto -> Aktien/Forex) kostet dadurch eine neue Adapter-Datei,
nicht einen Umbau der Signal-Logik. Siehe PLAN.md Abschnitt 5.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class MarketAdapter(Protocol):
    """Jede Datenquelle (Krypto-Börse, Aktien, Forex, ...) implementiert das hier."""

    def ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """
        Gibt ein DataFrame mit Spalten [open, high, low, close, volume] zurück,
        Index = UTC-Zeitstempel, aufsteigend sortiert, neueste Kerze zuletzt.
        """
        ...

    def symbols(self) -> list[str]:
        """Verfügbare Handelspaare/Ticker für diesen Adapter."""
        ...

    def last_price(self, symbol: str) -> float:
        """Aktueller Preis, für die Anzeige in der Signal-Nachricht."""
        ...
