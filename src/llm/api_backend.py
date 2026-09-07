"""
Weg 2 aus PLAN.md: Anthropic-API direkt, ~2-5 USD/Monat bei den in macro.py
vorgesehenen ~1-8 Calls/Tag. claude-sonnet-5 fest gewaehlt (siehe PLAN.md
Abschnitt 3: Makro-Synthese ist Urteilsarbeit, bei diesen Kosten lohnt sich
Sparen auf Haiku nicht). Braucht ANTHROPIC_API_KEY in der Umgebung.
"""

from __future__ import annotations

import json
import os

from .backend import LLMResponse

MODEL = "claude-sonnet-5"


class APIBackend:
    name = "api"

    def __init__(self, model: str = MODEL) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY nicht gesetzt. Fuer Weg 2 (API-Backend) in .env eintragen, "
                "oder LLM_BACKEND=cli fuer Weg 1 (0 CHF, Claude-Pro-Kontingent)."
            )
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def ask(self, system: str, user: str) -> LLMResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text_parts = [block.text for block in response.content if block.type == "text"]
        result_text = "\n".join(text_parts)
        parsed = _try_parse_json(result_text)
        return LLMResponse(text=result_text, parsed=parsed, backend=self.name)


def _try_parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None
