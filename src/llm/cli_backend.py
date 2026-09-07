"""
Weg 1 aus PLAN.md: Claude Code headless als Subprozess. 0 CHF extra,
laeuft ueber das bestehende Claude-Pro-Kontingent. Funktioniert auf einer
normal eingeloggten `claude`-Installation (Lorenz' Mac) -- in der
Entwicklungs-Sandbox, in der dieser Bot gebaut wurde, ist der Token
abgelaufen, das ist eine Sandbox-Eigenheit, kein Code-Fehler (siehe
tasks/todo.md).
"""

from __future__ import annotations

import json
import subprocess

from .backend import LLMResponse


class CLIBackend:
    name = "cli"

    def __init__(self, claude_bin: str = "claude", timeout_s: int = 90) -> None:
        self.claude_bin = claude_bin
        self.timeout_s = timeout_s

    def ask(self, system: str, user: str) -> LLMResponse:
        prompt = f"{system}\n\n{user}"
        try:
            proc = subprocess.run(
                [self.claude_bin, "-p", "--output-format", "json"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "`claude`-CLI nicht gefunden. Ist Claude Code installiert und im PATH? "
                "Alternative: LLM_BACKEND=api in .env setzen."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude -p hat nach {self.timeout_s}s nicht geantwortet.") from exc

        if proc.returncode != 0:
            raise RuntimeError(f"claude -p Fehler (exit {proc.returncode}): {proc.stderr or proc.stdout}")

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p Ausgabe war kein valides JSON: {proc.stdout[:300]}") from exc

        if envelope.get("is_error"):
            raise RuntimeError(f"claude -p meldet Fehler: {envelope.get('result')}")

        result_text = envelope.get("result", "")
        parsed = _try_parse_json(result_text)
        return LLMResponse(text=result_text, parsed=parsed, backend=self.name)


def _try_parse_json(text: str) -> dict | None:
    text = text.strip()
    # Claude haengt manchmal ```json ... ``` drumherum, auch wenn man JSON-only anfordert
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None
