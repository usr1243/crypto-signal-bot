"""
Sentiment-Scoring fuer News-Ueberschriften. Zwei Backends hinter einem
gemeinsamen Interface:

  - FinBertBackend: ProsusAI/finbert, lokal via transformers/torch.
    Laut Recherche (PLAN.md Abschnitt 3) der robusteste Sentiment-
    Klassifikator fuer Finanztexte, quasi kostenlos pro Inferenz.
  - LexiconBackend: reine Python-Keyword-Heuristik, keine Abhaengigkeiten.
    Fallback, falls transformers/torch auf der Zielmaschine nicht
    installiert sind (z.B. beim Kollegen, bevor er sich entscheidet,
    ob er den ~350MB-Download will).

get_sentiment_backend() waehlt automatisch FinBERT, wenn verfuegbar,
sonst Lexicon -- mit klarer Log-Meldung, welches Backend aktiv ist.
Kein LLM hier: Sentiment-Scoring einzelner Headlines ist laut Recherche
eine Aufgabe, bei der spezialisierte Modelle Allzweck-LLMs schlagen und
lokal quasi gratis sind (PLAN.md Abschnitt 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SentimentResult:
    label: str  # "positive" | "negative" | "neutral"
    score: float  # 0..1, Konfidenz des Labels


class SentimentBackend(Protocol):
    name: str

    def score(self, text: str) -> SentimentResult: ...


# --------------------------------------------------------------------------
# Lexicon-Fallback -- keine Abhaengigkeiten, funktioniert immer
# --------------------------------------------------------------------------

_BULLISH_WORDS = {
    "rally", "surge", "soar", "breakout", "approval", "adoption", "bullish",
    "upgrade", "inflow", "record high", "all-time high", "partnership",
    "institutional demand", "buy", "gain", "rebound", "recovery", "green light",
}
_BEARISH_WORDS = {
    "crash", "plunge", "sell-off", "selloff", "panic", "ban", "crackdown",
    "hack", "exploit", "lawsuit", "bearish", "downgrade", "outflow",
    "liquidation", "collapse", "fraud", "investigation", "recession", "default",
}


class LexiconBackend:
    name = "lexicon"

    def score(self, text: str) -> SentimentResult:
        t = text.lower()
        bull_hits = sum(1 for w in _BULLISH_WORDS if w in t)
        bear_hits = sum(1 for w in _BEARISH_WORDS if w in t)

        if bull_hits == 0 and bear_hits == 0:
            return SentimentResult("neutral", 0.5)
        if bull_hits > bear_hits:
            confidence = min(0.5 + 0.15 * (bull_hits - bear_hits), 0.95)
            return SentimentResult("positive", confidence)
        if bear_hits > bull_hits:
            confidence = min(0.5 + 0.15 * (bear_hits - bull_hits), 0.95)
            return SentimentResult("negative", confidence)
        return SentimentResult("neutral", 0.5)


# --------------------------------------------------------------------------
# FinBERT -- lazy import, damit ein fehlendes torch/transformers nicht schon
# beim Modul-Import den ganzen Bot zum Absturz bringt
# --------------------------------------------------------------------------

class FinBertBackend:
    name = "finbert"

    def __init__(self) -> None:
        from transformers import pipeline  # lazy: nur laden, wenn wirklich gebraucht

        self._pipe = pipeline("sentiment-analysis", model="ProsusAI/finbert")

    def score(self, text: str) -> SentimentResult:
        out = self._pipe(text[:512])[0]  # FinBERT-Kontextfenster begrenzt lange Headlines eh
        return SentimentResult(label=out["label"], score=float(out["score"]))


def get_sentiment_backend(prefer: str = "auto") -> SentimentBackend:
    """
    prefer: "auto" (FinBERT wenn verfuegbar, sonst Lexicon), "finbert", "lexicon".
    """
    if prefer == "lexicon":
        return LexiconBackend()

    if prefer in ("auto", "finbert"):
        try:
            return FinBertBackend()
        except Exception as exc:  # transformers/torch fehlt oder Modell-Download schlaegt fehl
            if prefer == "finbert":
                raise
            print(f"[sentiment] FinBERT nicht verfuegbar ({exc.__class__.__name__}: {exc}), "
                  f"falle auf Lexicon-Backend zurueck.")
            return LexiconBackend()

    raise ValueError(f"Unbekanntes Sentiment-Backend: {prefer}")


def aggregate_sentiment(results: list[SentimentResult]) -> dict:
    """Verdichtet mehrere Headline-Scores zu einem Gesamtbild fuer den Makro-Prompt."""
    if not results:
        return {"n": 0, "positive": 0, "negative": 0, "neutral": 0, "net_score": 0.0}

    pos = sum(1 for r in results if r.label == "positive")
    neg = sum(1 for r in results if r.label == "negative")
    neu = sum(1 for r in results if r.label == "neutral")
    # net_score: -1 (alles bearish, hohe Konfidenz) .. +1 (alles bullish, hohe Konfidenz)
    net = sum((1 if r.label == "positive" else (-1 if r.label == "negative" else 0)) * r.score for r in results)
    return {
        "n": len(results),
        "positive": pos, "negative": neg, "neutral": neu,
        "net_score": round(net / len(results), 3),
    }
