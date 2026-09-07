"""
Kerzenmuster-Erkennung (Candlestick-Patterns) -- die "Bildchen", die man auf
jedem Chart sieht: eine einzelne Kerze oder ein Kerzenpaar, das laut
TA-Literatur oft eine Trendwende ankuendigt. Rein geometrisch aus
Open/High/Low/Close berechnet, kein LLM, kausal (Muster bei Kerze i braucht
nur Kerze i und i-1 -- kein Look-ahead-Risiko).

Ehrlichkeit vorweg: Kerzenmuster sind in der Literatur die am wenigsten
zuverlaessige Kategorie technischer Signale (noch weniger belastbar als
RSI/MACD) -- deshalb hier klein gewichtet in signals.py, nicht als
Haupttreiber.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Schwellenwerte als Anteil der Kerzen-Handelsspanne (High-Low), Standardwerte
# aus der gaengigen TA-Literatur, keine Optimierung auf diese Daten.
_SMALL_BODY = 0.3   # Koerper (|Open-Close|) hoechstens 30% der Spanne
_LONG_WICK = 0.6    # ein Docht mindestens 60% der Spanne
_SHORT_WICK = 0.15  # der andere Docht hoechstens 15% der Spanne
_DOJI_BODY = 0.1    # Koerper hoechstens 10% der Spanne -- fast ein Strich


def detect_patterns(df: pd.DataFrame) -> pd.Series:
    """Gibt pro Kerze ein Label zurueck: 'bullish_engulfing', 'bearish_engulfing',
    'hammer', 'shooting_star', 'doji', oder 'none'. Bei Ueberlappung gewinnt
    diese Prioritaet: Engulfing > Hammer/Shooting-Star > Doji."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, 1e-12)
    body = (c - o).abs()
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l

    body_pct = body / rng
    upper_pct = upper_wick / rng
    lower_pct = lower_wick / rng

    prev_o, prev_c = o.shift(1), c.shift(1)
    prev_bearish = prev_c < prev_o
    curr_bullish = c > o
    bullish_engulfing = prev_bearish & curr_bullish & (o <= prev_c) & (c >= prev_o)

    prev_bullish = prev_c > prev_o
    curr_bearish = c < o
    bearish_engulfing = prev_bullish & curr_bearish & (o >= prev_c) & (c <= prev_o)

    hammer = (lower_pct >= _LONG_WICK) & (body_pct <= _SMALL_BODY) & (upper_pct <= _SHORT_WICK)
    shooting_star = (upper_pct >= _LONG_WICK) & (body_pct <= _SMALL_BODY) & (lower_pct <= _SHORT_WICK)
    doji = body_pct <= _DOJI_BODY

    labels = pd.Series("none", index=df.index)
    labels = labels.mask(doji, "doji")
    labels = labels.mask(shooting_star, "shooting_star")
    labels = labels.mask(hammer, "hammer")
    labels = labels.mask(bearish_engulfing, "bearish_engulfing")
    labels = labels.mask(bullish_engulfing, "bullish_engulfing")
    return labels
