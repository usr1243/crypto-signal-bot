"""
Echte Pivot-basierte Struktur- und Divergenz-Erkennung (Ersatz fuer die
grobe Heuristik aus signals.py v1).

Ein Pivot-Hoch bei Index i ist ein Kerze, deren High das Maximum in einem
Fenster von `left`+`right` Nachbarn ist (klassische "Fraktal"-Definition,
Standard in der TA-Literatur). Aus der Folge der Pivots leitet sich die
Struktur ab: Higher-High/Higher-Low = Aufwaertstrend-Struktur,
Lower-High/Lower-Low = Abwaertstrend-Struktur.

Kein LLM, deterministisch, reproduzierbar -- wichtig fuer den Backtest.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Pivot:
    index: int
    ts: pd.Timestamp
    price: float
    kind: str  # "high" | "low"


def find_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[Pivot]:
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    pivots: list[Pivot] = []

    for i in range(left, n - right):
        window_high = highs[i - left : i + right + 1]
        if highs[i] == window_high.max() and (window_high == highs[i]).sum() == 1:
            pivots.append(Pivot(i, df.index[i], float(highs[i]), "high"))

        window_low = lows[i - left : i + right + 1]
        if lows[i] == window_low.min() and (window_low == lows[i]).sum() == 1:
            pivots.append(Pivot(i, df.index[i], float(lows[i]), "low"))

    pivots.sort(key=lambda p: p.index)
    return pivots


def confirmed_pivots_at(all_pivots: list[Pivot], as_of_index: int, right: int) -> list[Pivot]:
    """
    Filtert Pivots auf die, die zum Zeitpunkt `as_of_index` bereits bestaetigt
    waren (ein Pivot bei Index j braucht `right` nachfolgende Kerzen, um als
    Pivot erkannt zu werden -- also erst ab Kerze j+right "bekannt").

    Das ist der Kern der Look-ahead-Vermeidung im Backtest: alle Pivots
    werden einmal fuer die ganze Historie berechnet (billig, O(n)), aber
    bei der Ruecksimulation "als ob wir bei Kerze i stehen" duerfen nur
    die zu diesem Zeitpunkt schon bestaetigten Pivots verwendet werden.
    Im Live-Betrieb (main.py) ist df ohnehin auf "jetzt" abgeschnitten,
    das Problem existiert dort strukturell nicht -- diese Funktion wird
    ausschliesslich vom Backtest gebraucht.
    """
    return [p for p in all_pivots if p.index + right <= as_of_index]


def classify_pattern(pivots: list[Pivot], lookback_pivots: int = 4) -> dict:
    """Reine Klassifikationsfunktion -- nimmt eine (ggf. schon gefilterte) Pivot-Liste."""
    if len(pivots) < 2:
        return {
            "pattern": "insufficient_data",
            "last_swing_high": None,
            "last_swing_low": None,
            "pivots_used": len(pivots),
        }

    recent = pivots[-lookback_pivots:]
    highs = [p for p in recent if p.kind == "high"]
    lows = [p for p in recent if p.kind == "low"]

    higher_highs = all(highs[i].price > highs[i - 1].price for i in range(1, len(highs))) if len(highs) >= 2 else None
    higher_lows = all(lows[i].price > lows[i - 1].price for i in range(1, len(lows))) if len(lows) >= 2 else None
    lower_highs = all(highs[i].price < highs[i - 1].price for i in range(1, len(highs))) if len(highs) >= 2 else None
    lower_lows = all(lows[i].price < lows[i - 1].price for i in range(1, len(lows))) if len(lows) >= 2 else None

    if higher_highs and higher_lows:
        pattern = "higher_high_higher_low"
    elif lower_highs and lower_lows:
        pattern = "lower_high_lower_low"
    else:
        pattern = "mixed"

    # juengstes Pivot-Hoch/-Tief als Breakout-/Stop-Referenz
    last_swing_high = highs[-1].price if highs else None
    last_swing_low = lows[-1].price if lows else None

    return {
        "pattern": pattern,
        "last_swing_high": round(last_swing_high, 2) if last_swing_high else None,
        "last_swing_low": round(last_swing_low, 2) if last_swing_low else None,
        "pivots_used": len(recent),
    }


def divergence_from_pivots(pivots: list[Pivot], rsi_series: pd.Series) -> str:
    """
    Reine Divergenz-Klassifikation -- nimmt eine (ggf. schon gefilterte) Pivot-Liste.
    Vergleicht Preis und RSI an den letzten zwei gleichartigen Pivots
    (High-zu-High oder Low-zu-Low).

    bearish: Preis macht hoeheres Hoch, RSI macht niedrigeres Hoch
    bullish: Preis macht niedrigeres Tief, RSI macht hoeheres Tief
    """
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]

    if len(highs) >= 2:
        p1, p2 = highs[-2], highs[-1]
        rsi1, rsi2 = rsi_series.iloc[p1.index], rsi_series.iloc[p2.index]
        if p2.price > p1.price and rsi2 < rsi1:
            return "bearish"

    if len(lows) >= 2:
        p1, p2 = lows[-2], lows[-1]
        rsi1, rsi2 = rsi_series.iloc[p1.index], rsi_series.iloc[p2.index]
        if p2.price < p1.price and rsi2 > rsi1:
            return "bullish"

    return "none"


# ---------------------------------------------------------------------------
# Groessere Chart-Muster (Doppel-Top/-Boden, Dreiecke) -- auf Wunsch des
# Kollegen ergaenzt. Standardansatz laut TA-Literatur: Pivots vergleichen,
# Toleranz fuer "praktisch gleiches Preisniveau" an der ATR (Volatilitaet)
# festmachen statt an einem festen Prozentwert, damit es bei ruhigen wie
# wilden Maerkten gleich streng bleibt. Erst mit Ausbruch (Kurs durchbricht
# die Nackenlinie/Flanke) gilt ein Muster als "confirmed" -- unbestaetigte
# Muster fliessen NICHT ins Scoring ein, das ist die uebliche Praxis in der
# Literatur, keine Erfindung.
# ---------------------------------------------------------------------------

def detect_chart_pattern(
    pivots: list[Pivot], atr_val: float, current_price: float,
    tolerance_atr_mult: float = 0.5, min_depth_atr_mult: float = 1.0,
    triangle_lookback: int = 4, triangle_slope_atr_mult: float = 0.5,
) -> dict:
    """
    Erkennt (in dieser Prioritaet, spezifischstes Muster zuerst):
    Schulter-Kopf-Schulter/invers (letzte 5 Pivots) -> Doppel-Top/-Boden
    (letzte 3 Pivots) -> Dreieck/Keil (letzte `triangle_lookback` Pivots).
    Gibt hoechstens EIN Muster zurueck.

    Rueckgabe: {"pattern": "head_and_shoulders"|"inverse_head_and_shoulders"|
    "double_top"|"double_bottom"|"ascending_triangle"|"descending_triangle"|
    "rising_wedge"|"falling_wedge"|"none", "confirmed": bool, "level": float|None}

    Bewusst NICHT dabei: Flaggen/Wimpel, Cup&Handle, Rounding Top/Bottom --
    die brauchen eine andere Erkennungsmethode (Trendkanal/Rundungsform statt
    einfacher Pivot-Vergleich) und wurden nicht ueberstuerzt nachgebaut, um
    keine falsche Praezision vorzutaeuschen. Koennen bei Bedarf ergaenzt werden.

    Keil-Erkennung ist eine Vereinfachung: prueft, dass beide Trendlinien in
    dieselbe Richtung laufen (Definitionsmerkmal von Keilen gegenueber
    Dreiecken), erzwingt aber keine echte geometrische Konvergenz -- fuer den
    Erstentwurf bewusst so belassen.
    """
    none_result = {"pattern": "none", "confirmed": False, "level": None}
    if atr_val is None or atr_val <= 0 or len(pivots) < 3:
        return none_result

    tol = tolerance_atr_mult * atr_val
    depth = min_depth_atr_mult * atr_val

    # --- Schulter-Kopf-Schulter (5 Pivots) ---
    if len(pivots) >= 5:
        last5 = pivots[-5:]
        kinds5 = [p.kind for p in last5]
        if kinds5 == ["high", "low", "high", "low", "high"]:
            s1, t1, head, t2, s2 = last5
            shoulders_similar = abs(s1.price - s2.price) <= tol
            head_higher = (head.price - max(s1.price, s2.price)) >= depth
            if shoulders_similar and head_higher:
                neckline = (t1.price + t2.price) / 2
                return {"pattern": "head_and_shoulders", "confirmed": current_price < neckline, "level": neckline}
        elif kinds5 == ["low", "high", "low", "high", "low"]:
            s1, t1, head, t2, s2 = last5
            shoulders_similar = abs(s1.price - s2.price) <= tol
            head_lower = (min(s1.price, s2.price) - head.price) >= depth
            if shoulders_similar and head_lower:
                neckline = (t1.price + t2.price) / 2
                return {"pattern": "inverse_head_and_shoulders", "confirmed": current_price > neckline, "level": neckline}

    # --- Doppel-Top/-Boden (3 Pivots) ---
    last3 = pivots[-3:]
    kinds3 = [p.kind for p in last3]
    if kinds3 == ["high", "low", "high"]:
        h1, low, h2 = last3
        if abs(h1.price - h2.price) <= tol and (min(h1.price, h2.price) - low.price) >= depth:
            return {"pattern": "double_top", "confirmed": current_price < low.price, "level": low.price}
    elif kinds3 == ["low", "high", "low"]:
        l1, high, l2 = last3
        if abs(l1.price - l2.price) <= tol and (high.price - max(l1.price, l2.price)) >= depth:
            return {"pattern": "double_bottom", "confirmed": current_price > high.price, "level": high.price}

    # --- Dreieck (eine Flanke flach) / Keil (beide Flanken gleiche Richtung) ---
    recent = pivots[-triangle_lookback:]
    highs = [p for p in recent if p.kind == "high"]
    lows = [p for p in recent if p.kind == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        min_slope = triangle_slope_atr_mult * atr_val
        h1, h2 = highs[-2], highs[-1]
        l1, l2 = lows[-2], lows[-1]
        highs_flat = abs(h2.price - h1.price) <= tol
        highs_rising = (h2.price - h1.price) >= min_slope
        highs_falling = (h1.price - h2.price) >= min_slope
        lows_flat = abs(l2.price - l1.price) <= tol
        lows_rising = (l2.price - l1.price) >= min_slope
        lows_falling = (l1.price - l2.price) >= min_slope

        if highs_flat and lows_rising:
            level = (h1.price + h2.price) / 2
            return {"pattern": "ascending_triangle", "confirmed": current_price > level, "level": level}
        if lows_flat and highs_falling:
            level = (l1.price + l2.price) / 2
            return {"pattern": "descending_triangle", "confirmed": current_price < level, "level": level}
        if highs_rising and lows_rising:
            # steigender Keil -- optisch bullish, gilt in der TA-Literatur klassisch als
            # bearishes Umkehrmuster (Aufwaertsdynamik erschoepft sich, Range verengt sich)
            return {"pattern": "rising_wedge", "confirmed": current_price < l2.price, "level": l2.price}
        if highs_falling and lows_falling:
            return {"pattern": "falling_wedge", "confirmed": current_price > h2.price, "level": h2.price}

    return none_result


# ---- Duenne Live-Wrapper: rechnen find_pivots + Klassifikation in einem Schritt ----
# Backtest ruft die Bausteine oben (find_pivots einmal, dann classify_pattern /
# divergence_from_pivots pro simulierter Kerze mit gefilterten Pivots) direkt auf.

def structure_pattern(df: pd.DataFrame, left: int = 3, right: int = 3, lookback_pivots: int = 4) -> dict:
    """Klassifiziert die juengste Marktstruktur -- fuer den Live-Pfad (main.py)."""
    pivots = find_pivots(df, left=left, right=right)
    return classify_pattern(pivots, lookback_pivots=lookback_pivots)


def rsi_divergence_from_pivots(df: pd.DataFrame, rsi_series: pd.Series, left: int = 3, right: int = 3) -> str:
    """Divergenz-Erkennung -- fuer den Live-Pfad (main.py)."""
    pivots = find_pivots(df, left=left, right=right)
    return divergence_from_pivots(pivots, rsi_series)
