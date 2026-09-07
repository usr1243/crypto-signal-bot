"""
Tests fuer Pivot-/Struktur-/Chart-Muster-Erkennung -- mit besonderem Fokus
auf die beiden echten Bugs, die in der Konversation gefunden wurden:

  1. Rundungsmuster feuerte auf reinen Trends (keine echte Kruemmung noetig).
  2. Flaggen/Wimpel feuerten NIE, weil die Bestaetigungskerze Teil ihres
     eigenen Konsolidierungsfensters war (Ausbruch gegen das eigene Hoch).

Beide sind hier als Regressionstests festgehalten, nicht nur als einmalige
Handmessung in der Konversation.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import chart_patterns, structure as struct
from tests.conftest import make_ohlcv, trending_series


# ---------------------------------------------------------------------------
# Pivots / Struktur
# ---------------------------------------------------------------------------

def _v_shape_df():
    """Reines V (runter, dann rauf). Der make_ohlcv-Helfer setzt open[i] = close[i-1],
    wodurch am Wendepunkt min(open,close) strukturell fuer die Wende-Kerze UND die
    Folgekerze identisch ist (beide = Wende-Close) -- ein Artefakt des einfachen
    Test-Generators, kein Verhalten echter Marktdaten. Der Tiefpunkt wird deshalb
    hier explizit um einen eindeutigen Betrag vertieft, damit ein echtes,
    unzweideutiges Pivot entsteht."""
    down = trending_series(10, start=110, step=-1.0, noise=0.0)
    up = trending_series(10, start=down[-1] + 1.0, step=1.0, noise=0.0)
    df = make_ohlcv(down + up)
    df.iloc[9, df.columns.get_loc("low")] -= 1.0
    return df


def test_find_pivots_detects_simple_v_shape():
    df = _v_shape_df()
    pivots = struct.find_pivots(df, left=3, right=3)
    lows = [p for p in pivots if p.kind == "low"]
    assert len(lows) >= 1
    assert lows[0].index == 9  # das Tief liegt am Uebergang down->up


def test_confirmed_pivots_at_excludes_unconfirmed_recent_pivots():
    df = _v_shape_df()
    pivots = struct.find_pivots(df, left=3, right=3)
    # bei Kerze 9 (dem Pivot selbst) ist es noch NICHT bestaetigt (braucht 3 Kerzen danach)
    assert struct.confirmed_pivots_at(pivots, as_of_index=9, right=3) == []
    # bei Kerze 12 (9+3) ist es bestaetigt
    assert len(struct.confirmed_pivots_at(pivots, as_of_index=12, right=3)) >= 1


def test_classify_pattern_higher_high_higher_low():
    highs = [struct.Pivot(0, None, 100, "high"), struct.Pivot(2, None, 110, "high")]
    lows = [struct.Pivot(1, None, 95, "low"), struct.Pivot(3, None, 102, "low")]
    pivots = sorted(highs + lows, key=lambda p: p.index)
    result = struct.classify_pattern(pivots, lookback_pivots=4)
    assert result["pattern"] == "higher_high_higher_low"


def test_double_top_detected_and_confirmed_on_breakdown():
    pivots = [
        struct.Pivot(0, None, 100, "high"),
        struct.Pivot(1, None, 90, "low"),
        struct.Pivot(2, None, 100.5, "high"),  # zweites Hoch, praktisch gleiches Niveau
    ]
    result = struct.detect_chart_pattern(pivots, atr_val=2.0, current_price=89.0)  # unter die Nackenlinie gefallen
    assert result["pattern"] == "double_top"
    assert result["confirmed"] is True


def test_double_top_not_confirmed_without_breakdown():
    pivots = [
        struct.Pivot(0, None, 100, "high"),
        struct.Pivot(1, None, 90, "low"),
        struct.Pivot(2, None, 100.5, "high"),
    ]
    result = struct.detect_chart_pattern(pivots, atr_val=2.0, current_price=95.0)  # noch ueber der Nackenlinie
    assert result["pattern"] == "double_top"
    assert result["confirmed"] is False


def test_head_and_shoulders_textbook_example():
    """Aus der Konversation: konstruiertes Lehrbuchbeispiel, das den Live-Fund
    (0 Treffer in der Stichprobe) als 'kein Bug, nur selten' bestaetigt hat."""
    pivots = [
        struct.Pivot(0, None, 100, "high"),
        struct.Pivot(1, None, 90, "low"),
        struct.Pivot(2, None, 110, "high"),
        struct.Pivot(3, None, 91, "low"),
        struct.Pivot(4, None, 101, "high"),
    ]
    result = struct.detect_chart_pattern(pivots, atr_val=2.0, current_price=89.0)
    assert result["pattern"] == "head_and_shoulders"
    assert result["confirmed"] is True


# ---------------------------------------------------------------------------
# Regressionstest 1: Rundungsmuster darf nicht auf reinem Trend feuern
# ---------------------------------------------------------------------------

def test_rounding_does_not_fire_on_pure_linear_trend():
    """Der urspruengliche Bug: eine Parabel passt auch an eine Gerade gut,
    weil eine Gerade mathematisch ein Sonderfall einer Parabel ist. Ohne die
    Kruemmungs-/Woelbungs-Filter haette das hier faelschlich 'rounding_bottom'
    oder 'rounding_top' ausgeloest."""
    df = make_ohlcv(trending_series(60, start=100, step=1.0, noise=0.0))
    from src.signals import compute_features
    feat = compute_features(df)
    atr_val = float(feat.atr.iloc[45])
    result = chart_patterns.detect_rounding(df, i=45, atr_val=atr_val)
    assert result["pattern"] == "none"


def test_rounding_fires_on_genuine_bowl_shape():
    """Gegenprobe: eine echte parabelfoermige Bewegung (runter, Boden, rauf)
    mit deutlicher Woelbung MUSS weiterhin erkannt werden -- der Fix darf den
    Erkenner nicht kaputt-restriktiv gemacht haben."""
    n = 50
    x = np.linspace(-1, 1, n)
    bowl = 100 + 20 * x**2  # Parabel, oeffnet nach oben -> rounding_bottom
    df = make_ohlcv(list(bowl) + [bowl[-1] + 25])  # + Ausbruch ueber den Rand
    from src.signals import compute_features
    feat = compute_features(df)
    atr_val = float(feat.atr.iloc[-2])
    result = chart_patterns.detect_rounding(df, i=len(df) - 1, atr_val=atr_val)
    assert result["pattern"] == "rounding_bottom"


# ---------------------------------------------------------------------------
# Regressionstest 2: Flagge/Wimpel darf nicht strukturell tot sein
# ---------------------------------------------------------------------------

def test_flag_pennant_confirms_on_genuine_breakout():
    """Der urspruengliche Bug: die Bestaetigungskerze war Teil des eigenen
    Konsolidierungsfensters, ein Ausbruch ueber das eigene Kerzenhoch ist
    unmoeglich. Dieser Test baut Pole + enge Konsolidierung + eine ECHTE
    Ausbruchskerze DANACH und verlangt, dass es feuert."""
    pole = trending_series(10, start=100, step=2.0, noise=0.0)          # starke Fahnenstange nach oben
    consolidation = trending_series(10, start=pole[-1], step=0.05, noise=0.05, seed=9)  # enge Seitwaerts-Phase
    breakout = [consolidation[-1] + 10]                                  # klarer Ausbruch ueber die Konsolidierung
    df = make_ohlcv(pole + consolidation + breakout)

    from src.signals import compute_features
    feat = compute_features(df)
    i = len(df) - 1
    atr_val = float(feat.atr.iloc[i])
    result = chart_patterns.detect_flag_pennant(df, i, atr_val)
    assert result["pattern"] in ("bull_flag", "bull_pennant")
    assert result["confirmed"] is True


def test_flag_pennant_confirmation_bar_excluded_from_consolidation_range():
    """Regressionstest fuer den eigentlichen Bug: das Konsolidierungsfenster
    darf die Bestaetigungskerze NICHT enthalten. Wenn man absichtlich eine
    riesige letzte Kerze anhaengt, darf sie das gemessene cons_range nicht
    verzerren -- sie ist reine Bestaetigung, kein Teil der Range-Messung."""
    pole = trending_series(10, start=100, step=2.0, noise=0.0)
    consolidation = trending_series(10, start=pole[-1], step=0.05, noise=0.05, seed=9)
    df_without_breakout = make_ohlcv(pole + consolidation)
    from src.signals import compute_features

    # Konsolidierungsrange OHNE die (noch nicht existierende) Ausbruchskerze:
    cons_only = df_without_breakout.iloc[-10:]
    range_without_confirmation_bar = float(cons_only["high"].max() - cons_only["low"].min())

    breakout = [consolidation[-1] + 50]  # extremer Ausbruch, wuerde bei Selbstbezug die Range sprengen
    df = make_ohlcv(pole + consolidation + breakout)
    feat = compute_features(df)
    i = len(df) - 1
    atr_val = float(feat.atr.iloc[i])

    result = chart_patterns.detect_flag_pennant(df, i, atr_val)
    # Trotz extremem Ausbruch muss es weiterhin als Flagge/Wimpel bestaetigt werden --
    # waere die Ausbruchskerze Teil der Konsolidierungs-Range, wuerde cons_range
    # explodieren und die Bedingung "eng genug" faelschlich verletzen.
    assert result["confirmed"] is True
