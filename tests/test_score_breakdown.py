"""
Tests fuer die Score-Aufschluesselung.

Kernanspruch: die Aufschluesselung ist keine huebsche Nebenausgabe, sondern
muss den Score EXAKT erklaeren. Wenn die Summe der Einzelbeitraege nicht dem
gemeldeten Score entspricht, ist die Erklaerung wertlos -- dann behauptet der
Bot etwas anderes, als er tut.
"""

from __future__ import annotations

import pytest

from src.signals import score_and_candidate, score_with_breakdown


def _bullish_kwargs(**overrides):
    base = dict(
        trend_regime="up", adx_val=25.0, macd_hist_val=1.0, rsi_val=55.0,
        vol_z_val=1.5, divergence="bullish", pattern="higher_high_higher_low",
        near_breakout=True,
    )
    base.update(overrides)
    return base


def test_beitraege_summieren_sich_exakt_auf_den_score():
    score, _, parts = score_with_breakdown(**_bullish_kwargs())
    assert sum(p.points for p in parts) == score


def test_beitraege_summieren_sich_auch_im_baerischen_fall():
    score, _, parts = score_with_breakdown(
        trend_regime="down", adx_val=30.0, macd_hist_val=-1.0, rsi_val=75.0,
        vol_z_val=0.1, divergence="bearish", pattern="lower_high_lower_low",
        near_breakout=False, stoch_k_val=90.0, cci_val=-150.0,
        price_above_sar=False, price_above_vwap=False,
        candle_pattern="bearish_engulfing", chart_pattern="head_and_shoulders",
    )
    assert sum(p.points for p in parts) == score


def test_begrenzung_wird_als_eigener_beitrag_ausgewiesen():
    """Wenn die Rohsumme ueber 100 liegt, muss die Kappung sichtbar sein --
    sonst summieren sich die Beitraege scheinbar falsch."""
    score, _, parts = score_with_breakdown(
        **_bullish_kwargs(),
        stoch_k_val=10.0, cci_val=150.0, price_above_sar=True,
        price_above_vwap=True, candle_pattern="hammer", chart_pattern="double_bottom",
    )
    assert score == 100
    assert sum(p.points for p in parts) == 100
    assert any(p.rule == "begrenzung" for p in parts), "Kappung muss als Beitrag auftauchen"


def test_wrapper_liefert_identisches_ergebnis():
    kwargs = _bullish_kwargs()
    score_a, cand_a = score_and_candidate(**kwargs)
    score_b, cand_b, _ = score_with_breakdown(**kwargs)
    assert (score_a, cand_a) == (score_b, cand_b)


def test_williams_r_erzeugt_keinen_beitrag():
    """Williams %R ist mathematisch identisch zur Stochastik -- es darf keinen
    eigenen Beitrag geben, sonst wird dieselbe Beobachtung doppelt gezaehlt."""
    _, _, parts = score_with_breakdown(**_bullish_kwargs(), williams_r_val=-95.0)
    assert not any(p.rule == "williams" for p in parts)


def test_jeder_beitrag_hat_lesbare_begruendung():
    _, _, parts = score_with_breakdown(**_bullish_kwargs())
    assert parts, "Es muss ueberhaupt Beitraege geben"
    for p in parts:
        assert p.detail and len(p.detail) > 5, f"Regel '{p.rule}' ohne verstaendliche Begruendung"
        assert p.points != 0, "Nullbeitraege gehoeren nicht in die Aufschluesselung"


def test_top_drivers_sortiert_nach_gewicht():
    from src.signals import TechnicalSignal

    _, _, parts = score_with_breakdown(**_bullish_kwargs())
    sig = TechnicalSignal(symbol="X", timeframe="1h", ts="t", price=1.0, score_breakdown=parts)
    top = sig.top_drivers(3)
    assert len(top) == 3
    gewichte = [abs(c.points) for c in top]
    assert gewichte == sorted(gewichte, reverse=True)


def test_breakdown_landet_serialisiert_im_dict():
    from src.signals import TechnicalSignal

    _, _, parts = score_with_breakdown(**_bullish_kwargs())
    sig = TechnicalSignal(symbol="X", timeframe="1h", ts="t", price=1.0, score_breakdown=parts)
    d = sig.to_dict()
    assert "score_breakdown" in d
    assert len(d["score_breakdown"]) == len(parts)
    assert set(d["score_breakdown"][0]) == {"rule", "points", "detail"}
