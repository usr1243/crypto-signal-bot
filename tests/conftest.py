"""
Gemeinsame Test-Hilfsmittel. Synthetische OHLCV-Reihen statt echter Live-Daten
-- Tests muessen deterministisch sein und ohne Netzwerk laufen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(closes: list[float], start: str = "2026-01-01", freq: str = "1h",
               high_pad: float = 0.5, low_pad: float = 0.5, volume: float = 100.0) -> pd.DataFrame:
    """Baut einen minimalen, plausiblen OHLCV-DataFrame aus einer Liste Schlusskurse.
    high/low werden leicht um close herum gepolstert (ATR/Range darf nie exakt 0 sein)."""
    n = len(closes)
    idx = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    closes = np.array(closes, dtype=float)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) + high_pad
    lows = np.minimum(opens, closes) - low_pad
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.full(n, volume),
    }, index=idx)


def trending_series(n: int, start: float = 100.0, step: float = 1.0, noise: float = 0.0, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    trend = start + np.arange(n) * step
    if noise:
        trend = trend + rng.normal(0, noise, n)
    return list(trend)


@pytest.fixture
def uptrend_df() -> pd.DataFrame:
    """250+ Kerzen klarer Aufwaertstrend -- genug fuer EMA200-Warmup."""
    return make_ohlcv(trending_series(260, start=100, step=0.3, noise=0.3))


@pytest.fixture
def downtrend_df() -> pd.DataFrame:
    return make_ohlcv(trending_series(260, start=200, step=-0.3, noise=0.3))


@pytest.fixture
def flat_df() -> pd.DataFrame:
    """Seitwaerts, etwas Rauschen -- fuer Tests, die explizit KEIN starkes Signal erwarten."""
    return make_ohlcv(trending_series(260, start=100, step=0.0, noise=0.5, seed=7))
