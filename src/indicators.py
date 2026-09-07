"""
Technische Indikatoren -- bewusst selbst gerechnet statt via pandas-ta.

Grund: pandas-ta hat wacklige numpy-Versionskompatibilitaet, und die
Formeln selbst sind Standardliteratur (kein Grund, eine fragile
Abhaengigkeit dafuer reinzuziehen). Alles hier ist deterministisch,
kein LLM beteiligt -- siehe PLAN.md Abschnitt 2, Schicht A.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid
    percent_b = (series - lower) / (upper - lower).replace(0, 1e-12)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width, "percent_b": percent_b})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move.clip(lower=0)
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move.clip(lower=0)

    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
    ).max(axis=1)
    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, 1e-12))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_smooth.replace(0, 1e-12))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-12)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = df["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * df["volume"]).cumsum()


def volume_zscore(volume: pd.Series, period: int = 20) -> pd.Series:
    mean = volume.rolling(period).mean()
    std = volume.rolling(period).std()
    return (volume - mean) / std.replace(0, 1e-12)


# ---------------------------------------------------------------------------
# Erweiterung auf Wunsch des Kollegen: "mehr davon wie RSI/MACD" -- weitere
# gaengige, bekannte Chart-Werkzeuge. Alle kausal (nur Vergangenheit), keine
# neue Look-ahead-Problematik.
# ---------------------------------------------------------------------------

def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """Stochastik-Oszillator -- aehnliche Idee wie RSI (ueberkauft/ueberverkauft),
    vergleicht aber den Schlusskurs mit der Handelsspanne der letzten k_period Kerzen."""
    low_n = df["low"].rolling(k_period).min()
    high_n = df["high"].rolling(k_period).max()
    percent_k = 100 * (df["close"] - low_n) / (high_n - low_n).replace(0, 1e-12)
    percent_d = percent_k.rolling(d_period).mean()
    return pd.DataFrame({"k": percent_k, "d": percent_d})


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index -- misst, wie weit der Kurs vom eigenen Durchschnitt
    abweicht. Ueber +100 = ungewoehnlich stark, unter -100 = ungewoehnlich schwach."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    sma = typical_price.rolling(period).mean()
    mean_dev = typical_price.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (typical_price - sma) / (0.015 * mean_dev.replace(0, 1e-12))


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R -- naher Verwandter der Stochastik, andere Skala (-100..0).
    Unter -80 = ueberverkauft, ueber -20 = ueberkauft."""
    high_n = df["high"].rolling(period).max()
    low_n = df["low"].rolling(period).min()
    return -100 * (high_n - df["close"]) / (high_n - low_n).replace(0, 1e-12)


def parabolic_sar(df: pd.DataFrame, af_step: float = 0.02, af_max: float = 0.2) -> pd.Series:
    """Parabolic SAR -- Punkte ueber/unter dem Kurs, die den Trend markieren und bei
    einer Trendwende auf die andere Seite springen. Klassisches Trailing-Stop-Werkzeug."""
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    n = len(df)
    sar = pd.Series(index=df.index, dtype=float)
    if n < 2:
        return sar

    uptrend = close[1] >= close[0]
    sar.iloc[0] = low[0] if uptrend else high[0]
    ep = high[0] if uptrend else low[0]  # Extreme Point
    af = af_step

    for i in range(1, n):
        prev_sar = sar.iloc[i - 1]
        new_sar = prev_sar + af * (ep - prev_sar)

        if uptrend:
            new_sar = min(new_sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < new_sar:
                uptrend, new_sar, ep, af = False, ep, low[i], af_step
            elif high[i] > ep:
                ep, af = high[i], min(af + af_step, af_max)
        else:
            new_sar = max(new_sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > new_sar:
                uptrend, new_sar, ep, af = True, ep, high[i], af_step
            elif low[i] < ep:
                ep, af = low[i], min(af + af_step, af_max)

        sar.iloc[i] = new_sar

    return sar


def rolling_vwap(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Gleitender VWAP (Volume Weighted Average Price) ueber die letzten `period`
    Kerzen -- wo der Grossteil des Handelsvolumens zuletzt stattgefunden hat,
    nicht nur der reine Durchschnittskurs."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical_price * df["volume"]
    return pv.rolling(period).sum() / df["volume"].rolling(period).sum().replace(0, 1e-12)
