"""Tests fuer Schicht A: Scoring-Schwellen, Trade-Vorschlag-Mathematik,
Multi-Timeframe-Filter, und der Williams-%R-Doppelzaehlungs-Fix."""

from __future__ import annotations

import pytest

from src.signals import (
    TechnicalSignal, apply_trend_filter, build_technical_signal,
    build_trade_proposal, score_and_candidate,
)
from tests.conftest import make_ohlcv, trending_series


def test_score_clamped_to_range():
    score, _ = score_and_candidate(
        "up", adx_val=50, macd_hist_val=10, rsi_val=50, vol_z_val=5,
        divergence="bullish", pattern="higher_high_higher_low", near_breakout=True,
        stoch_k_val=10, cci_val=200, chart_pattern="double_bottom",
    )
    assert -100 <= score <= 100


def test_candidate_none_below_threshold():
    score, candidate = score_and_candidate(
        "up", adx_val=10, macd_hist_val=-1, rsi_val=50, vol_z_val=0,
        divergence="none", pattern="mixed", near_breakout=False,
    )
    assert candidate == "NONE"


def test_candidate_long_above_positive_threshold():
    score, candidate = score_and_candidate(
        "up", adx_val=25, macd_hist_val=1, rsi_val=50, vol_z_val=2,
        divergence="bullish", pattern="higher_high_higher_low", near_breakout=True,
    )
    assert score >= 40
    assert candidate == "LONG"


def test_candidate_short_below_negative_threshold():
    score, candidate = score_and_candidate(
        "down", adx_val=25, macd_hist_val=-1, rsi_val=50, vol_z_val=2,
        divergence="bearish", pattern="lower_high_lower_low", near_breakout=False,
        chart_pattern="double_top",
    )
    assert score <= -40
    assert candidate == "SHORT"


def test_williams_r_does_not_change_score():
    """Regressionstest: Williams %R wurde bewusst aus der Score-Formel entfernt
    (mathematisch identisch zur Stochastik, haette dieselbe Beobachtung doppelt
    gezaehlt). Ein extremer Williams-Wert darf den Score nicht mehr beeinflussen."""
    base_args = dict(
        trend_regime="up", adx_val=15, macd_hist_val=0.5, rsi_val=50, vol_z_val=0,
        divergence="none", pattern="mixed", near_breakout=False, stoch_k_val=50,
    )
    score_neutral_williams, _ = score_and_candidate(**base_args, williams_r_val=-50.0)
    score_extreme_williams, _ = score_and_candidate(**base_args, williams_r_val=-95.0)
    assert score_neutral_williams == score_extreme_williams


def test_build_technical_signal_runs_end_to_end_on_uptrend(uptrend_df):
    sig = build_technical_signal(uptrend_df, symbol="TEST/USD", timeframe="1h")
    assert sig.trend["regime"] == "up"
    assert isinstance(sig.score, int)
    assert sig.candidate in ("LONG", "SHORT", "NONE")


def test_trade_proposal_none_for_none_candidate():
    sig = TechnicalSignal(symbol="X", timeframe="1h", ts="", price=100, candidate="NONE")
    assert build_trade_proposal(sig) is None


def test_trade_proposal_long_stop_below_entry():
    sig = TechnicalSignal(
        symbol="X", timeframe="1h", ts="", price=100, candidate="LONG",
        volatility={"atr14": 2.0},
    )
    proposal = build_trade_proposal(sig, atr_multiple=2.0, reward_risk_ratio=2.0)
    assert proposal.stop < proposal.entry < proposal.target
    assert proposal.entry - proposal.stop == pytest.approx(4.0)  # 2x ATR
    assert proposal.target - proposal.entry == pytest.approx(8.0)  # 2:1 R:R


def test_trade_proposal_short_stop_above_entry():
    sig = TechnicalSignal(
        symbol="X", timeframe="1h", ts="", price=100, candidate="SHORT",
        volatility={"atr14": 2.0},
    )
    proposal = build_trade_proposal(sig, atr_multiple=2.0, reward_risk_ratio=2.0)
    assert proposal.target < proposal.entry < proposal.stop


def test_trade_proposal_size_scales_with_macro_multiplier():
    sig = TechnicalSignal(
        symbol="X", timeframe="1h", ts="", price=100, candidate="LONG",
        volatility={"atr14": 2.0},
    )
    full = build_trade_proposal(sig, account_risk_pct=1.0, size_multiplier=1.0)
    halved = build_trade_proposal(sig, account_risk_pct=1.0, size_multiplier=0.5)
    assert halved.size_pct_of_account == pytest.approx(full.size_pct_of_account / 2)


def test_trend_filter_blocks_long_against_down_higher_tf(uptrend_df, downtrend_df):
    entry_sig = build_technical_signal(uptrend_df, "X", "1h")
    entry_sig.candidate = "LONG"  # erzwungen fuer den Test
    trend_sig = build_technical_signal(downtrend_df, "X", "4h")
    filtered = apply_trend_filter(entry_sig, trend_sig)
    assert filtered.candidate == "NONE"
    assert "filtered_reason" in filtered.trend


def test_trend_filter_allows_long_with_up_higher_tf(uptrend_df):
    entry_sig = build_technical_signal(uptrend_df, "X", "1h")
    entry_sig.candidate = "LONG"
    trend_sig = build_technical_signal(uptrend_df, "X", "4h")
    filtered = apply_trend_filter(entry_sig, trend_sig)
    assert filtered.candidate == "LONG"
