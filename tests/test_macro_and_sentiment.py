"""Tests fuer Claim Validation (macro.py) und das abhaengigkeitsfreie
Lexicon-Sentiment-Backend -- beide laufen ohne Netzwerk/LLM."""

from __future__ import annotations

from src.macro import MacroContext, validate_claims
from src.sentiment import LexiconBackend, aggregate_sentiment


def _ctx(**overrides) -> MacroContext:
    defaults = dict(
        yfinance={"vix": {"last": 16.4, "week_change_pct": 2.1}},
        fred={"available": False},
        fear_greed={"value": 63, "classification": "Greed"},
        headlines=[],
        sentiment_summary={"n": 5, "positive": 2, "negative": 1, "neutral": 2, "net_score": 0.2},
    )
    defaults.update(overrides)
    return MacroContext(**defaults)


def test_claim_validation_passes_for_numbers_actually_given():
    ctx = _ctx()
    parsed = {"drivers": ["VIX bei 16.4, ruhig", "Fear&Greed bei 63"], "reasoning": "Alles im gruenen Bereich."}
    result = validate_claims(parsed, ctx)
    assert result["all_verified"] is True
    assert result["n_unverified"] == 0


def test_claim_validation_catches_fabricated_number():
    ctx = _ctx()
    parsed = {"drivers": ["VIX bei 45.0, sehr hoch"], "reasoning": ""}  # 45.0 kommt in ctx nirgends vor
    result = validate_claims(parsed, ctx)
    assert result["all_verified"] is False
    assert 45.0 in result["unverified_numbers"]


def test_claim_validation_tolerates_small_rounding():
    ctx = _ctx()
    parsed = {"drivers": ["VIX bei 16.5"], "reasoning": ""}  # 16.5 statt 16.4 -- Rundungsdifferenz
    result = validate_claims(parsed, ctx)
    assert result["all_verified"] is True


def test_lexicon_backend_positive_headline():
    backend = LexiconBackend()
    result = backend.score("Bitcoin rallies to new all-time high on institutional demand")
    assert result.label == "positive"


def test_lexicon_backend_negative_headline():
    backend = LexiconBackend()
    result = backend.score("Regulatory crackdown triggers panic selling and market crash")
    assert result.label == "negative"


def test_lexicon_backend_neutral_headline():
    backend = LexiconBackend()
    result = backend.score("Quarterly earnings report scheduled for next Tuesday")
    assert result.label == "neutral"


def test_aggregate_sentiment_net_score_sign():
    backend = LexiconBackend()
    results = [
        backend.score("Bitcoin surges to record high"),
        backend.score("Market crash triggers panic"),
        backend.score("Bitcoin rallies on institutional adoption"),
    ]
    summary = aggregate_sentiment(results)
    assert summary["n"] == 3
    assert summary["net_score"] > 0  # 2 von 3 klar positiv
