"""
LLMBackend-Protocol -- nicht an einen Anbieter gebunden. macro.py haengt
nur an diesem Protocol, nie an einer konkreten Implementierung. Welches
Modell die Makro-Synthese macht, ist eine .env-Zeile (LLM_BACKEND=...),
kein Code-Umbau -- genau deshalb liess sich Groq nachtraeglich dazustecken,
ohne macro.py oder sonst irgendwas ausserhalb von llm/ anzufassen.

Wege (alle hinter demselben Interface):
  cli   -- Claude Code headless (`claude -p`), 0 CHF, Pro-Kontingent
  api   -- Anthropic API direkt, claude-sonnet-5, ~2-5 USD/Monat
  groq  -- Groq (offene Modelle wie Llama), 0 CHF, Gratis-Tier ohne Kreditkarte
  mock  -- kein echtes LLM, fuer Pipeline-Tests ohne Zugang
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

_KNOWN = ("cli", "api", "groq", "mock")


@dataclass
class LLMResponse:
    text: str          # Rohtext der Antwort (fuer Logging/Debug)
    parsed: dict | None  # geparstes JSON, falls die Antwort valides JSON war
    backend: str         # "cli" | "api" | "groq" | "mock" -- fuer Nachvollziehbarkeit im Log


class LLMBackend(Protocol):
    name: str

    def ask(self, system: str, user: str) -> LLMResponse: ...


def get_llm_backend(kind: str = "auto") -> LLMBackend:
    """
    kind: "auto" (LLM_BACKEND aus .env, sonst erster verfuegbarer Key in der
          Reihenfolge Anthropic -> Groq, sonst CLI), oder direkt "cli"/"api"/
          "groq"/"mock".
    """
    if kind == "mock":
        from .mock_backend import MockBackend
        return MockBackend()

    if kind == "cli":
        from .cli_backend import CLIBackend
        return CLIBackend()

    if kind == "api":
        from .api_backend import APIBackend
        return APIBackend()

    if kind == "groq":
        from .groq_backend import GroqBackend
        return GroqBackend()

    if kind == "auto":
        import os
        configured = os.environ.get("LLM_BACKEND", "").strip().lower()
        if configured in _KNOWN:
            return get_llm_backend(configured)
        if os.environ.get("ANTHROPIC_API_KEY"):
            return get_llm_backend("api")
        if os.environ.get("GROQ_API_KEY"):
            return get_llm_backend("groq")
        return get_llm_backend("cli")

    raise ValueError(f"Unbekanntes LLM-Backend: {kind} (bekannt: auto, {', '.join(_KNOWN)})")
