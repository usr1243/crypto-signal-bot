"""
Symmetrie-Tests fuer das Scoring (signals.score_with_breakdown).

Geschichte dieser Datei: sie entstand am 2026-09-06 als CHARAKTERISIERUNGS-Test,
der vier bekannte Schieflagen nur dokumentierte. Am 2026-09-07 wurden alle vier
behoben -- die Tests pruefen jetzt das Gegenteil, naemlich dass die Regeln
tatsaechlich spiegeln. Behoben wurden:

1. `volumen` gab pauschal +10, unabhaengig von der Richtung -> jetzt
   richtungsabhaengig (hohes Volumen bestaetigt die laufende Bewegung).
2. Das "gesunde" RSI-Band lag bei 45-65 (um 55 zentriert statt um 50)
   -> jetzt 40-60 und ebenfalls richtungsabhaengig.
3. `ausbruch` kannte nur den Ausbruch nach oben; es gab nicht einmal einen
   Parameter fuer den Bruch nach unten -> neuer Parameter `near_breakdown`
   mit gespiegelten Punkten.
4. `cup_and_handle` (+15) war das einzige unpaarige Chartmuster -> neuer
   Erkenner `chart_patterns.detect_inverse_cup_and_handle` und Eintrag
   `inverse_cup_and_handle: -15`.

Warum das wichtig war (auch wenn es die Rentabilitaet nicht rettet, siehe die
Kostenanalyse im PLAN): zusammen benachteiligten diese vier Regeln die
baerische Seite um bis zu 47 Punkte bei einer Ausloeseschwelle von 40. Solange
das so war, konnte die Short-Seite gar nicht fair gemessen werden -- live
entstand in 86 Signalen kein einziger Short.
"""

from __future__ import annotations

from src.signals import score_with_breakdown

# Muss synchron bleiben mit _CHART_POINTS in signals.py:score_with_breakdown
# (dort lokal definiert, deshalb hier nicht importierbar).
_CHART_PATTERNS = [
    "double_bottom", "inverse_head_and_shoulders", "falling_wedge",
    "double_top", "head_and_shoulders", "rising_wedge",
    "ascending_triangle", "descending_triangle",
    "cup_and_handle", "inverse_cup_and_handle",
    "rounding_bottom", "rounding_top",
    "bull_flag", "bull_pennant", "bear_flag", "bear_pennant",
]


def _rule_points(rule: str, **kwargs) -> int:
    """Score-Beitrag EINER Regel aus der Aufschluesselung, Rest neutral per Default."""
    defaults = dict(
        trend_regime="up", adx_val=0.0, macd_hist_val=0.0, rsi_val=50.0,
        vol_z_val=0.0, divergence="none", pattern="mixed", near_breakout=False,
    )
    defaults.update(kwargs)
    _, _, breakdown = score_with_breakdown(**defaults)
    for c in breakdown:
        if c.rule == rule:
            return c.points
    return 0


def test_volume_bonus_follows_trend_direction():
    """Hohes Volumen bestaetigt die laufende Bewegung -- im Abwaertstrend
    spricht es gegen einen Long, nicht dafuer."""
    assert _rule_points("volumen", trend_regime="up", vol_z_val=2.0) == 10
    assert _rule_points("volumen", trend_regime="down", vol_z_val=2.0) == -10


def test_rsi_healthy_band_is_centered_on_fifty():
    """Das gesunde Band (40-60) liegt symmetrisch um den RSI-Neutralpunkt 50:
    ein Wert und sein Spiegelbild (100-x) bekommen denselben Betrag."""
    assert abs(_rule_points("rsi", rsi_val=58.0)) == abs(_rule_points("rsi", rsi_val=42.0))
    # ausserhalb des Bands, aber noch nicht im Extrembereich: beide Seiten 0
    assert _rule_points("rsi", rsi_val=64.0) == 0
    assert _rule_points("rsi", rsi_val=36.0) == 0
    # Extrembereiche bleiben gespiegelt
    assert _rule_points("rsi", rsi_val=75.0) == -10
    assert _rule_points("rsi", rsi_val=25.0) == 10


def test_rsi_healthy_band_follows_trend_direction():
    assert _rule_points("rsi", trend_regime="up", rsi_val=50.0) == 10
    assert _rule_points("rsi", trend_regime="down", rsi_val=50.0) == -10


def test_breakout_and_breakdown_are_mirrored():
    """Der Bruch unter das letzte Swing-Tief zaehlt jetzt genau so viel negativ
    wie der Ausbruch ueber das letzte Swing-Hoch positiv zaehlt."""
    assert _rule_points("ausbruch", near_breakout=True) == 5
    assert _rule_points("ausbruch", near_breakdown=True) == -5
    # mit Volumenbestaetigung ebenfalls gespiegelt
    assert _rule_points("ausbruch", near_breakout=True, vol_z_val=2.0) == 12
    assert _rule_points("ausbruch", near_breakdown=True, vol_z_val=2.0) == -12


def test_breakout_wins_over_breakdown_when_both_flagged():
    """Beide gleichzeitig ist praktisch unmoeglich (Kurs kann nicht zugleich am
    Hoch und am Tief kleben), aber das Verhalten soll definiert sein: der
    Ausbruch nach oben wird zuerst geprueft und gewinnt."""
    assert _rule_points("ausbruch", near_breakout=True, near_breakdown=True) == 5


def test_every_chart_pattern_has_a_mirrored_counterpart():
    """Alle 16 Chartmuster bilden saubere Paare mit entgegengesetztem
    Vorzeichen und gleichem Betrag -- kein unpaariger Rest mehr."""
    points = {p: _rule_points("chartmuster", chart_pattern=p) for p in _CHART_PATTERNS}

    positive_magnitudes = sorted(v for v in points.values() if v > 0)
    negative_magnitudes = sorted(abs(v) for v in points.values() if v < 0)

    assert positive_magnitudes == negative_magnitudes
    assert points["cup_and_handle"] == 15
    assert points["inverse_cup_and_handle"] == -15


def test_full_mirror_of_a_bullish_setup_gives_the_negated_score():
    """Der eigentliche Symmetrie-Test: ein komplett gespiegeltes Setup muss den
    exakt negierten Score ergeben. Das faengt kuenftige Einbahnstrassen-Regeln
    automatisch ab, ohne dass jede einzeln getestet werden muss."""
    bullish, bullish_candidate, _ = score_with_breakdown(
        trend_regime="up", adx_val=30.0, macd_hist_val=1.5, rsi_val=55.0,
        vol_z_val=2.0, divergence="bullish", pattern="higher_high_higher_low",
        near_breakout=True, near_breakdown=False,
        stoch_k_val=15.0, cci_val=150.0,
        price_above_sar=True, price_above_vwap=True,
        candle_pattern="hammer", chart_pattern="double_bottom",
    )
    bearish, bearish_candidate, _ = score_with_breakdown(
        trend_regime="down", adx_val=30.0, macd_hist_val=-1.5, rsi_val=45.0,
        vol_z_val=2.0, divergence="bearish", pattern="lower_high_lower_low",
        near_breakout=False, near_breakdown=True,
        stoch_k_val=85.0, cci_val=-150.0,
        price_above_sar=False, price_above_vwap=False,
        candle_pattern="shooting_star", chart_pattern="double_top",
    )

    assert bearish == -bullish
    assert bullish_candidate == "LONG"
    assert bearish_candidate == "SHORT"
