"""
Weg 3: Groq -- auf Wunsch, nicht an einen Anbieter gebunden zu sein.
Groq betreibt eigene Hardware fuer schnelle Inferenz offener Modelle
(Llama, Kimi, GPT-OSS etc.), kein Anthropic-Hoster. Free Tier ganz ohne
Kreditkarte, Ratenlimits reichen fuer diesen Bot bei weitem (Schicht B
braucht laut PLAN.md ~1-8 Calls/Tag, Groq Free Tier erlaubt ca. 1000/Tag
fuer llama-3.3-70b-versatile, Stand der Recherche in dieser Konversation).

Kein eigenes SDK-Paket noetig -- Groq spricht eine OpenAI-kompatible REST-API,
ein einfacher requests-Call reicht, passt zum Rest des Projekts (kein
zusaetzliches SDK fuer cli_backend.py/api_backend.py noetig).

Modellwahl (Stand der Recherche 2026-09-02, siehe Konversation): `openai/gpt-oss-120b`
statt eines Llama-Modells -- ein offenes REASONING-Modell von OpenAI selbst,
im Groq-Gratis-Tier enthalten (bestaetigt), mit einstellbarem Denkaufwand
(`reasoning_effort`). Fuer die Makro-Synthese (Urteilsarbeit ueber
widerspruechliche Signale) passender als ein reines Allzweck-Chat-Modell wie
Llama 3.3. Bleibt trotzdem vermutlich unter Sonnet 5 -- aber bei 0 CHF/Monat
eine legitime Kosten-Qualitaets-Abwaegung, keine, die der Bot fuer den Nutzer
treffen sollte (siehe README fuer weitere Kandidaten wie qwen3-32b).
"""

from __future__ import annotations

import json
import os

import requests

from .backend import LLMResponse

BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqBackend:
    name = "groq"

    def __init__(self, model: str = DEFAULT_MODEL, reasoning_effort: str = "high") -> None:
        self.api_key = os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "GROQ_API_KEY nicht gesetzt. Gratis-Key ohne Kreditkarte: "
                "https://console.groq.com/keys -- dann LLM_BACKEND=groq in .env."
            )
        self.model = model
        self.reasoning_effort = reasoning_effort  # nur bei Reasoning-Modellen (gpt-oss-*) wirksam

    def ask(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,  # deterministischer, passend zur Claim-Validation in macro.py
        }
        if "gpt-oss" in self.model:
            payload["reasoning_effort"] = self.reasoning_effort
            payload["include_reasoning"] = False  # Reasoning-Text landet sonst in einem Extra-Feld, hier ungenutzt

        resp = requests.post(
            BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Groq-API Fehler {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        parsed = _try_parse_json(text)
        return LLMResponse(text=text, parsed=parsed, backend=self.name)


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
