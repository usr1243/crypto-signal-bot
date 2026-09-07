"""Tests fuer die Backend-Auswahl (llm/backend.py) -- kein echter Netzwerk-Call,
nur die Dispatch-Logik. Bestaetigt: der Bot ist nicht an einen Anbieter
gebunden, LLM_BACKEND (oder ein gesetzter Key) entscheidet."""

from __future__ import annotations

import pytest

from src.llm.backend import get_llm_backend
from src.llm.mock_backend import MockBackend


def test_explicit_mock_backend():
    backend = get_llm_backend("mock")
    assert isinstance(backend, MockBackend)


def test_unknown_backend_raises_clear_error():
    with pytest.raises(ValueError, match="Unbekanntes LLM-Backend"):
        get_llm_backend("does-not-exist")


def test_groq_backend_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        get_llm_backend("groq")


def test_api_backend_requires_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_llm_backend("api")


def test_auto_prefers_explicit_llm_backend_env(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "mock")
    backend = get_llm_backend("auto")
    assert isinstance(backend, MockBackend)


def test_auto_picks_groq_when_only_groq_key_present(monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    backend = get_llm_backend("auto")
    assert backend.name == "groq"


def test_auto_prefers_anthropic_over_groq_when_both_present(monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    backend = get_llm_backend("auto")
    assert backend.name == "api"
