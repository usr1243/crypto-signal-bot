"""Sanity-Tests fuer die Indikator-Formeln -- keine Referenzimplementierung
zum Abgleichen, aber jede Formel muss sich in bekannten Extremfaellen
plausibel verhalten (RSI=100 bei nur Gewinnen, EMA konvergiert zum Kurs, etc.)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import indicators as ind
from tests.conftest import make_ohlcv, trending_series


def test_rsi_pure_uptrend_approaches_100():
    df = make_ohlcv(trending_series(60, start=100, step=1.0, noise=0.0))
    rsi = ind.rsi(df["close"], 14)
    assert rsi.iloc[-1] > 95, "reiner Aufwaertstrend ohne einzigen Ruecksetzer muss RSI nahe 100 ergeben"


def test_rsi_pure_downtrend_approaches_0():
    df = make_ohlcv(trending_series(60, start=100, step=-1.0, noise=0.0))
    rsi = ind.rsi(df["close"], 14)
    assert rsi.iloc[-1] < 5


def test_rsi_bounded_0_100():
    df = make_ohlcv(trending_series(200, start=100, step=0.2, noise=3.0, seed=1))
    rsi = ind.rsi(df["close"], 14)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_ema_converges_to_constant_price():
    df = make_ohlcv([100.0] * 100)
    ema = ind.ema(df["close"], 20)
    assert ema.iloc[-1] == pytest.approx(100.0, abs=0.01)


def test_macd_hist_positive_in_uptrend_after_warmup():
    df = make_ohlcv(trending_series(100, start=100, step=0.5, noise=0.0))
    macd_df = ind.macd(df["close"])
    assert macd_df["hist"].iloc[-1] > 0


def test_bollinger_percent_b_above_1_on_new_high_breakout():
    # 30 Kerzen seitwaerts, dann ein deutlicher Sprung nach oben -- Kurs muss ueber das obere Band schiessen
    flat = [100.0] * 30
    breakout = [100 + i * 3 for i in range(1, 5)]
    df = make_ohlcv(flat + breakout)
    bb = ind.bollinger(df["close"], period=20)
    assert bb["percent_b"].iloc[-1] > 1.0


def test_atr_zero_range_candles_give_zero_atr():
    df = make_ohlcv([100.0] * 30, high_pad=0.0, low_pad=0.0)  # keine Spanne -> ATR muss 0 sein
    atr = ind.atr(df, 14)
    assert atr.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_atr_scales_with_volatility():
    calm = make_ohlcv(trending_series(60, start=100, step=0.0, noise=0.2, seed=2), high_pad=0.3, low_pad=0.3)
    wild = make_ohlcv(trending_series(60, start=100, step=0.0, noise=5.0, seed=2), high_pad=3.0, low_pad=3.0)
    assert ind.atr(wild, 14).iloc[-1] > ind.atr(calm, 14).iloc[-1]


def test_stochastic_k_bounded_0_100():
    df = make_ohlcv(trending_series(100, start=100, step=0.4, noise=2.0, seed=3))
    stoch = ind.stochastic(df, 14, 3)
    valid = stoch["k"].dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_williams_r_is_stochastic_minus_100():
    """Regressionstest fuer den in der Konversation gefundenen Fund: Williams %R
    und Stochastik-%K sind exakt dieselbe Formel, nur verschoben -- deshalb
    zaehlt signals.score_and_candidate Williams %R bewusst nicht mehr separat."""
    df = make_ohlcv(trending_series(100, start=100, step=0.4, noise=2.0, seed=3))
    stoch_k = ind.stochastic(df, 14, 3)["k"]
    williams = ind.williams_r(df, 14)
    diff = (williams - (stoch_k - 100)).dropna()
    assert np.allclose(diff.values, 0.0, atol=1e-6)


def test_parabolic_sar_flips_side_on_trend_reversal():
    up = trending_series(60, start=100, step=1.0, noise=0.0)
    down = trending_series(60, start=up[-1], step=-1.0, noise=0.0)
    df = make_ohlcv(up + down)
    sar = ind.parabolic_sar(df)
    price = df["close"]
    # waehrend des Aufwaertstrends muss SAR unter dem Kurs liegen, im Abwaertstrend darueber
    assert sar.iloc[40] < price.iloc[40]
    assert sar.iloc[-1] > price.iloc[-1]


def test_rolling_vwap_between_min_and_max_close():
    df = make_ohlcv(trending_series(100, start=100, step=0.3, noise=4.0, seed=5))
    vwap = ind.rolling_vwap(df, 20).dropna()
    window_close = df["close"].iloc[-len(vwap):]
    assert vwap.iloc[-1] >= window_close.tail(20).min() - 1e-6
    assert vwap.iloc[-1] <= window_close.tail(20).max() + 1e-6
