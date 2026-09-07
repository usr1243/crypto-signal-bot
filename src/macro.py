"""
Schicht B: verdichtet Makro-Daten, News und Sentiment zu einem Regime-Urteil.

Kopplung an Schicht A ist strikt Gate/Veto (PLAN.md Abschnitt 2):
Schicht B darf ein Signal aus Schicht A blocken oder seine Groesse
reduzieren, aber NIE selbst einen Einstieg erzeugen. Deshalb hat diese
Datei keine Funktion, die einen "candidate" zurueckgibt -- nur
regime/veto_long/veto_short/size_multiplier.

Jede Zahl, die das LLM in seiner Begruendung nennt, wird gegen die
tatsaechlich uebergebenen Werte geprueft (Claim Validation, Muster aus
dem LLM_trader-Repo, siehe PLAN.md Abschnitt 3). Weicht sie zu stark ab,
wird das Signal nicht verworfen, aber deutlich als "unvalidiert" markiert
-- lieber sichtbar unsicher als still falsch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .adapters import fear_greed, macro_fred, macro_yfinance, news_cryptopanic, news_rss
from .llm.backend import LLMBackend
from .sentiment import SentimentBackend, aggregate_sentiment

SYSTEM_PROMPT = """Du bist der Makro-Layer eines regelbasierten Trading-Signal-Bots (Schicht B).

Deine einzige Aufgabe: aus den gelieferten Makro-Daten, News und Sentiment-Werten ein
Marktregime-Urteil zu bilden. Du erzeugst NIE eigene Kauf-/Verkaufsempfehlungen -- die
kommen aus einer separaten, deterministischen technischen Schicht. Du darfst deren
Signale nur BLOCKEN (Veto) oder ihre Groesse reduzieren (size_multiplier), nie selbst
Einstiege vorschlagen.

Grundprinzipien fuer deine Einschaetzung (aus Fundamentalanalyse-/TA-Kursunterlagen,
siehe Claude-Brain/projects/trading-bot/wissensbasis_makro_ta.md fuer die volle Herleitung):

1. Geldpolitik: sinkende Zinsen -> tiefere Finanzierungskosten -> mehr Konsum/
   Investitionen -> steigende Aktien-, Anleihen- UND Risikoanlagen-Preise (Risk-On).
   Steigende Zinsen wirken umgekehrt (Risk-Off).
2. Zinskurve (yield_curve_10y_2y): eine Inversion (negativer Wert) ab ca. -1.0
   Prozentpunkten (100 Basispunkte) gilt als deutlich aussagekraeftigeres
   Rezessionssignal als eine kleinere Inversion.
3. Intermarket-Kette Dollar -> Krypto: Rohstoffe und Risikoanlagen wie Krypto
   werden ueberwiegend in USD gehandelt. Ein schwaecherer Dollar (DXY faellt)
   ist historisch tendenziell positiv fuer Bitcoin/Krypto, ein staerkerer
   Dollar tendenziell negativ.
4. Fear & Greed ist ein KONTRAINDIKATOR, kein Bestaetigungssignal: extreme Werte
   in beide Richtungen (sehr hohe Gier UND sehr hohe Angst) gelten als
   Warnsignal fuer eine moegliche Trendwende, nicht als "weiter in diese
   Richtung investieren".

Wende diese Prinzipien auf die gelieferten Daten an, erfinde aber keine neuen
Zahlen -- nur die unten explizit gegebenen Werte verwenden.

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt in genau diesem Format, keine Erklaerung
davor oder danach, keine Markdown-Codebloecke:

{
  "regime": "RISK_ON" | "RISK_OFF" | "NEUTRAL",
  "confidence": <0.0-1.0>,
  "drivers": ["kurzer Grund 1", "kurzer Grund 2", "..."],
  "veto_long": true|false,
  "veto_short": true|false,
  "size_multiplier": <0.0-1.0>,
  "reasoning": "1-3 Saetze Begruendung"
}

Nenne in "drivers" und "reasoning" nur Zahlen, die dir explizit in den Daten unten
gegeben wurden. Erfinde keine Werte."""


@dataclass
class MacroContext:
    yfinance: dict
    fred: dict
    fear_greed: dict | None
    headlines: list[dict]
    sentiment_summary: dict
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def gather_macro_context(sentiment_backend: SentimentBackend, max_headlines: int = 12) -> MacroContext:
    print("[macro] hole yfinance-Makrodaten ...")
    yf_snapshot = macro_yfinance.fetch_macro_snapshot()

    print("[macro] hole FRED (Zinsen/CPI) ...")
    fred_data = macro_fred.fetch_macro_series()

    print("[macro] hole Fear&Greed ...")
    fng = fear_greed.fetch_fear_greed()

    print("[macro] hole News (CryptoPanic + RSS) ...")
    headlines = news_cryptopanic.fetch_headlines() + news_rss.fetch_headlines()
    headlines = headlines[:max_headlines]

    print(f"[macro] Sentiment-Scoring ueber {len(headlines)} Headlines ({sentiment_backend.name}) ...")
    sentiment_results = [sentiment_backend.score(h["title"]) for h in headlines if h.get("title")]
    sentiment_summary = aggregate_sentiment(sentiment_results)

    return MacroContext(
        yfinance=yf_snapshot, fred=fred_data, fear_greed=fng,
        headlines=headlines, sentiment_summary=sentiment_summary,
    )


def build_user_prompt(ctx: MacroContext) -> str:
    lines = ["MARKTDATEN (letzter Stand + Wochenveraenderung):"]
    for key, val in ctx.yfinance.items():
        if "error" in val:
            continue
        lines.append(f"- {key.upper()}: {val['last']} ({val['week_change_pct']:+.2f}% seit letzter Woche)")

    if ctx.fred.get("available"):
        lines.append("\nZINSEN/INFLATION (FRED):")
        for key, val in ctx.fred.items():
            if key == "available" or "error" in val:
                continue
            extra = f", YoY {val['yoy_pct']}%" if "yoy_pct" in val else ""
            lines.append(f"- {key}: {val['value']} (Stand {val['date']}{extra})")
    else:
        lines.append("\nZINSEN/INFLATION: nicht verfuegbar (FRED_API_KEY nicht konfiguriert)")

    if ctx.fear_greed:
        lines.append(f"\nFEAR & GREED INDEX: {ctx.fear_greed['value']} ({ctx.fear_greed['classification']})")

    s = ctx.sentiment_summary
    lines.append(f"\nNEWS-SENTIMENT ueber {s['n']} Headlines: "
                 f"{s['positive']} positiv / {s['negative']} negativ / {s['neutral']} neutral, "
                 f"Netto-Score {s['net_score']} (-1 stark bearish .. +1 stark bullish)")

    if ctx.headlines:
        lines.append("\nAKTUELLE HEADLINES (Auswahl):")
        for h in ctx.headlines[:8]:
            lines.append(f"- [{h['source']}] {h['title']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claim Validation -- jede im LLM-Text genannte Zahl gegen die echten Werte pruefen
# ---------------------------------------------------------------------------

def _reference_numbers(ctx: MacroContext) -> list[float]:
    nums: list[float] = []
    for val in ctx.yfinance.values():
        if "error" in val:
            continue
        nums.append(val["last"])
        if val.get("week_change_pct") is not None:
            nums.append(val["week_change_pct"])
    if ctx.fear_greed:
        nums.append(float(ctx.fear_greed["value"]))
    for val in ctx.fred.values():
        if isinstance(val, dict) and "value" in val:
            nums.append(val["value"])
            if "yoy_pct" in val:
                nums.append(val["yoy_pct"])
    nums.append(float(ctx.sentiment_summary.get("n", 0)))
    return nums


def validate_claims(parsed: dict, ctx: MacroContext, rel_tolerance: float = 0.05, abs_tolerance: float = 1.0) -> dict:
    """
    Extrahiert alle Zahlen aus drivers+reasoning und prueft, ob jede davon
    (innerhalb einer Toleranz) irgendeiner echten Referenzzahl entspricht.
    Kein Ersatz fuer eine zweite LLM-Pruef-Instanz, aber faengt die
    haeufigste Fehlerklasse: erfundene/verwechselte Zahlen.
    """
    text = " ".join(parsed.get("drivers", [])) + " " + parsed.get("reasoning", "")
    claimed = [float(x) for x in re.findall(r"-?\d+\.?\d*", text)]
    references = _reference_numbers(ctx)

    unverified = []
    for c in claimed:
        ok = any(
            abs(c - r) <= max(abs_tolerance, abs(r) * rel_tolerance)
            for r in references
        )
        if not ok:
            unverified.append(c)

    return {
        "n_claimed_numbers": len(claimed),
        "n_unverified": len(unverified),
        "unverified_numbers": unverified,
        "all_verified": len(unverified) == 0,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_macro_state(llm_backend: LLMBackend, sentiment_backend: SentimentBackend) -> dict:
    ctx = gather_macro_context(sentiment_backend)
    user_prompt = build_user_prompt(ctx)

    print(f"[macro] frage LLM-Backend '{llm_backend.name}' ...")
    response = llm_backend.ask(SYSTEM_PROMPT, user_prompt)

    if response.parsed is None:
        print(f"[macro] LLM-Antwort war kein valides JSON, verwende Fallback-Neutral-Regime.")
        return _fallback_state(ctx, reason="llm_response_not_json", raw_text=response.text)

    parsed = response.parsed
    required = {"regime", "confidence", "drivers", "veto_long", "veto_short", "size_multiplier", "reasoning"}
    if not required.issubset(parsed.keys()):
        missing = required - parsed.keys()
        print(f"[macro] LLM-JSON fehlen Felder {missing}, verwende Fallback-Neutral-Regime.")
        return _fallback_state(ctx, reason=f"missing_fields:{missing}", raw_text=response.text)

    validation = validate_claims(parsed, ctx)
    if not validation["all_verified"]:
        print(f"[macro] Claim Validation: {validation['n_unverified']}/{validation['n_claimed_numbers']} "
              f"Zahlen nicht in den Originaldaten wiedergefunden: {validation['unverified_numbers']}")

    state = {
        "regime": parsed["regime"],
        "confidence": float(parsed["confidence"]),
        "drivers": parsed["drivers"],
        "veto_long": bool(parsed["veto_long"]),
        "veto_short": bool(parsed["veto_short"]),
        "size_multiplier": float(parsed["size_multiplier"]),
        "reasoning": parsed["reasoning"],
        # War 18h -- zu lang. Zinsen/Dollar/Aktienmaerkte aendern sich nicht
        # staendig, aber Nachrichten (Hack, Fed-Entscheid, Regulierung) koennen
        # jederzeit passieren, nicht nur einmal am Tag. 4h ist ein Kompromiss:
        # deutlich frischer, ohne bei jedem einzelnen Signal-Check neu zu fragen
        # (Groq ist zwar kostenlos, aber haeufiger als noetig bleibt unnoetig).
        # Siehe run_loop.py: macro_refresh_job haelt das proaktiv aktuell,
        # unabhaengig davon, ob gerade ein technisches Signal vorliegt.
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "llm_backend": response.backend,
        "claim_validation": validation,
        "generated_at": ctx.fetched_at,
    }
    return state


def _fallback_state(ctx: MacroContext, reason: str, raw_text: str) -> dict:
    """
    Sicherer Rueckfall, wenn das LLM nicht brauchbar geantwortet hat:
    NEUTRAL, kein Veto, volle Groesse -- Schicht A entscheidet dann
    effektiv allein, statt dass ein kaputter Makro-Layer den ganzen Bot
    lahmlegt oder (schlimmer) unbegruendet blockiert.
    """
    return {
        "regime": "NEUTRAL", "confidence": 0.0,
        "drivers": [f"Makro-Layer nicht verfuegbar ({reason})"],
        "veto_long": False, "veto_short": False, "size_multiplier": 1.0,
        "reasoning": "Fallback: LLM-Antwort unbrauchbar, Schicht A entscheidet ohne Makro-Filter.",
        "valid_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "llm_backend": "fallback", "claim_validation": None,
        "generated_at": ctx.fetched_at, "raw_llm_text": raw_text[:500],
    }
