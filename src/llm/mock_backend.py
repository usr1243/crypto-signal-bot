"""
MockBackend -- kein echtes LLM, sondern eine deterministische Regelantwort.

Zweck: die Verdrahtung von macro.py (Prompt bauen -> LLM fragen -> JSON
parsen -> Claim Validation -> Gate-Kopplung mit Schicht A) end-to-end
beweisen zu koennen, OHNE einen echten LLM-Zugang zu brauchen. In dieser
Entwicklungsumgebung ist weder das CLI-Backend (Sandbox-Token abgelaufen)
noch das API-Backend (kein ANTHROPIC_API_KEY) live testbar -- siehe
tasks/todo.md. Das hier ersetzt keinen echten Test, sondern zeigt: "der
Rest der Pipeline funktioniert, sobald irgendein Backend antwortet".
"""

from __future__ import annotations

import json
import re

from .backend import LLMResponse


class MockBackend:
    name = "mock"

    def ask(self, system: str, user: str) -> LLMResponse:
        # Liest die Zahlen, die macro.py selbst in den Prompt geschrieben hat,
        # und spiegelt sie in einer plausiblen Struktur zurueck -- damit
        # die Claim-Validation in macro.py (die genau diese Zahlen gegen
        # die Originalwerte prueft) etwas zu tun hat und nicht leerlaeuft.
        vix_match = re.search(r"VIX[^0-9\-]*([\d.]+)", user, re.IGNORECASE)
        vix = float(vix_match.group(1)) if vix_match else 18.0
        fng_match = re.search(r"FEAR\s*&\s*GREED[^0-9\-]*([\d]+)", user, re.IGNORECASE)
        fng = int(fng_match.group(1)) if fng_match else 50

        regime = "RISK_ON" if vix < 20 and fng > 45 else "RISK_OFF"
        parsed = {
            "regime": regime,
            "confidence": 0.6,
            "drivers": [
                f"VIX bei {vix} ({'ruhig' if vix < 20 else 'erhoeht'})",
                f"Fear&Greed bei {fng} ({'Greed' if fng > 55 else 'Neutral/Fear'})",
                "[MOCK-Backend -- keine echte Analyse, nur Verdrahtungstest]",
            ],
            "veto_long": regime == "RISK_OFF",
            "veto_short": regime == "RISK_ON",
            "size_multiplier": 0.8 if regime == "RISK_ON" else 0.5,
            "reasoning": f"[MOCK] Regelbasierte Platzhalterantwort aus VIX={vix} und F&G={fng}.",
        }
        text = json.dumps(parsed, ensure_ascii=False)
        return LLMResponse(text=text, parsed=parsed, backend=self.name)
