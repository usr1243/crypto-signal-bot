"""
"Breite" Chart-Muster, die sich NICHT sauber aus einzelnen Pivot-Punkten
ableiten lassen wie die in structure.py (Doppel-Top/-Boden, Dreiecke,
Schulter-Kopf-Schulter) -- die brauchen eine andere Erkennungsmethode:

  - Flagge/Wimpel: eine starke, schnelle Bewegung ("Fahnenstange"), gefolgt
    von einer engen Konsolidierung, dann Ausbruch in dieselbe Richtung.
  - Cup & Handle / Rounding Top & Bottom: eine allmaehliche, runde Kurs-
    bewegung -- kein einzelner Umkehrpunkt wie bei Doppel-Top, sondern ein
    Bogen ueber viele Kerzen. Erkennung ueber eine Parabel-Kurvenanpassung
    (numpy polyfit, Grad 2) an die Schlusskurse: passt eine Parabel gut
    (hohes R²), ist das ein rundes Muster; oeffnet sie nach oben = Boden
    (rounding_bottom), nach unten = Top (rounding_top). Cup & Handle ist ein
    rounding_bottom plus ein kleiner, flacher Ruecksetzer danach (die
    "Henkel").

Beide Ansaetze sind Standardtechnik (Kurvenanpassung fuer Rundungsformen
wird z.B. auch von kommerziellen Pattern-Scannern verwendet), keine
Erfindung -- aber ehrlich gesagt weicher/unschaerfer als die Pivot-Methode
in structure.py, weil "wie rund ist rund genug" eine Ermessensfrage ist.
Alles hier arbeitet nur mit Daten bis zur uebergebenen Kerze `i` -- kein
Look-ahead, genau wie der Rest von Schicht A.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NONE_RESULT = {"pattern": "none", "confirmed": False, "level": None}


def detect_rounding(
    df: pd.DataFrame, i: int, atr_val: float, window: int = 40, gap_bars: int = 3,
    min_r2: float = 0.6, min_r2_improvement: float = 0.15, min_bow_atr_mult: float = 1.5,
) -> dict:
    """
    Passt eine Parabel an die Schlusskurse der `window` Kerzen VOR einer
    kleinen Luecke (`gap_bars`) zu Kerze i an -- die Luecke sorgt dafuer,
    dass "confirmed" (Ausbruch ueber/unter den Rand) etwas ist, das
    tatsaechlich NACH der geformten Rundung passiert, nicht Teil der
    Kurvenanpassung selbst ist.

    Zwei zusaetzliche Filter (Nachkalibrierung -- ohne die feuerte das Muster
    auf echten Daten in ~55% aller Fenster, weil eine Parabel sich auch an
    einen ganz normalen Trend anschmiegt, ohne dass eine echte "Rundung" da
    ist -- eine Gerade ist mathematisch ein Sonderfall einer Parabel):
      1. `min_r2_improvement`: die Parabel muss die Kursbewegung MERKLICH
         besser erklaeren als eine simple Gerade (R2_quadratisch minus
         R2_linear) -- sonst ist es einfach nur ein Trend, kein Bogen.
      2. `min_bow_atr_mult`: die "Woelbung" der Kurve (Abweichung der Mitte
         von der direkten Verbindungslinie Anfang-Ende) muss wirtschaftlich
         relevant sein, mindestens das X-fache der ATR -- sonst ist die
         Kruemmung zwar statistisch da, aber zu winzig, um ein echtes Muster
         zu sein. Mit beiden Filtern: ~8% Fehlerrate statt 55% (gemessen an
         echten BTC/USD-1h-Daten, 257 Fenster).
    """
    if atr_val is None or atr_val <= 0 or i < window + gap_bars:
        return {**NONE_RESULT, "r2": 0.0}

    fit_window = df.iloc[i - window - gap_bars: i - gap_bars]
    y = fit_window["close"].values
    x = np.arange(len(y))

    coeffs = np.polyfit(x, y, 2)
    fitted_quad = np.polyval(coeffs, x)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2_quad = 1 - float(np.sum((y - fitted_quad) ** 2)) / ss_tot if ss_tot > 0 else 0.0

    lin_coeffs = np.polyfit(x, y, 1)
    fitted_lin = np.polyval(lin_coeffs, x)
    r2_lin = 1 - float(np.sum((y - fitted_lin) ** 2)) / ss_tot if ss_tot > 0 else 0.0

    if r2_quad < min_r2 or (r2_quad - r2_lin) < min_r2_improvement:
        return {**NONE_RESULT, "r2": r2_quad}

    mid = len(y) // 2
    bow = abs(float(y[mid] - fitted_lin[mid]))
    if bow < min_bow_atr_mult * atr_val:
        return {**NONE_RESULT, "r2": r2_quad}

    leading_coeff = coeffs[0]
    current_price = float(df["close"].iloc[i])
    if leading_coeff > 0:
        rim = max(y[0], y[-1])  # Boden-Rundung: Rand = das hoehere der beiden Enden
        return {"pattern": "rounding_bottom", "confirmed": current_price > rim, "level": float(rim), "r2": r2_quad}
    else:
        rim = min(y[0], y[-1])  # Top-Rundung: Rand = das niedrigere der beiden Enden
        return {"pattern": "rounding_top", "confirmed": current_price < rim, "level": float(rim), "r2": r2_quad}


def detect_cup_and_handle(df: pd.DataFrame, i: int, atr_val: float, cup_window: int = 40, handle_bars: int = 8) -> dict:
    """Cup = rounding_bottom (siehe oben), Handle = kleiner, flacher Ruecksetzer danach."""
    if atr_val is None or atr_val <= 0 or i < cup_window + handle_bars:
        return NONE_RESULT

    cup = detect_rounding(df, i - handle_bars, atr_val, window=cup_window, gap_bars=1)
    if cup["pattern"] != "rounding_bottom":
        return NONE_RESULT

    handle = df.iloc[i - handle_bars + 1: i + 1]
    handle_depth = float(handle["high"].max() - handle["low"].min())
    cup_low = float(df.iloc[max(0, i - handle_bars - cup_window): i - handle_bars]["close"].min())
    cup_depth = cup["level"] - cup_low

    # Henkel muss flacher/kuerzer sein als der Cup selbst -- sonst ist es kein
    # Henkel, sondern eine neue, eigene Bewegung.
    if handle_depth > max(atr_val * 1.5, 0.5 * cup_depth):
        return NONE_RESULT

    current_price = float(df["close"].iloc[i])
    return {"pattern": "cup_and_handle", "confirmed": current_price > cup["level"], "level": cup["level"]}


def detect_inverse_cup_and_handle(df: pd.DataFrame, i: int, atr_val: float,
                                   cup_window: int = 40, handle_bars: int = 8) -> dict:
    """Spiegelbild von detect_cup_and_handle: Cup = rounding_top, Henkel = kleiner
    Aufwaertsruecksetzer danach, bestaetigt beim Bruch UNTER den Rand.

    Ergaenzt 2026-09-07: cup_and_handle war das einzige der 15 Chartmuster ohne
    baerisches Gegenstueck (+15 unpaariert, siehe tests/test_score_symmetry.py).
    Das verzerrte den Score systematisch zugunsten von Long -- und war einer der
    Gruende, warum die Short-Seite live nie zum Zug kam.
    """
    if atr_val is None or atr_val <= 0 or i < cup_window + handle_bars:
        return NONE_RESULT

    cup = detect_rounding(df, i - handle_bars, atr_val, window=cup_window, gap_bars=1)
    if cup["pattern"] != "rounding_top":
        return NONE_RESULT

    handle = df.iloc[i - handle_bars + 1: i + 1]
    handle_depth = float(handle["high"].max() - handle["low"].min())
    cup_high = float(df.iloc[max(0, i - handle_bars - cup_window): i - handle_bars]["close"].max())
    cup_depth = cup_high - cup["level"]

    if handle_depth > max(atr_val * 1.5, 0.5 * cup_depth):
        return NONE_RESULT

    current_price = float(df["close"].iloc[i])
    return {"pattern": "inverse_cup_and_handle", "confirmed": current_price < cup["level"],
            "level": cup["level"]}


def detect_flag_pennant(
    df: pd.DataFrame, i: int, atr_val: float,
    pole_bars: int = 10, consolidation_bars: int = 10,
    pole_min_atr_mult: float = 2.0, consolidation_max_atr_mult: float = 2.5,
) -> dict:
    """
    Fahnenstange (starke Bewegung ueber `pole_bars` Kerzen) + enge
    Konsolidierung (`consolidation_bars` Kerzen) direkt danach + Ausbruch in
    Fahnenstangen-Richtung = Flagge/Wimpel (Fortsetzungsmuster).
    Wimpel = die Konsolidierung verengt sich sichtbar (zweite Haelfte deutlich
    enger als erste); sonst Flagge.

    Zwei Nachkalibrierungen (beide per Messung an echten Daten gefunden, siehe
    Konversation vom 2026-09-02 -- Ersatz fuer erratene Zahlen):

    1. `consolidation_max_atr_mult` stand auf 1.5 -- das ist die Spannweite von
       10 Kerzen HINTEREINANDER, nicht einer einzelnen. Selbst die ruhigste
       gemessene 10-Kerzen-Konsolidierung in echten Daten (BTC/ETH/SOL,
       15m/1h/4h) lag nie unter 1.8xATR. Neu kalibriert an echten Perzentilen
       (2.5x ATR ~ unterstes Viertel aller Konsolidierungs-Ranges).

    2. Schwerwiegender: die Bestaetigungs-Kerze `i` war Teil des Konsolidierungs-
       Fensters selbst (`upper`/`lower` wurden AUS Kerze i mitberechnet). Ein
       Schlusskurs kann nie ueber das eigene Kerzenhoch steigen -- die
       Ausbruchs-Bedingung war dadurch strukturell fast unerfuellbar,
       unabhaengig von den Schwellenwerten (der Erkenner feuerte deshalb in
       keinem der 5760 getesteten Fenster, selbst nach Fix 1 nicht). Jetzt:
       Konsolidierung endet bei Kerze i-1, Kerze i ist ausschliesslich die
       Bestaetigungs-/Ausbruchskerze -- gleiches Prinzip wie die Luecke in
       detect_rounding().
    """
    if atr_val is None or atr_val <= 0 or i < pole_bars + consolidation_bars:
        return NONE_RESULT

    consolidation = df.iloc[i - consolidation_bars: i]           # bis Kerze i-1
    pole = df.iloc[i - consolidation_bars - pole_bars: i - consolidation_bars]

    pole_move = float(pole["close"].iloc[-1] - pole["close"].iloc[0])
    if abs(pole_move) < pole_min_atr_mult * atr_val:
        return NONE_RESULT

    cons_range = float(consolidation["high"].max() - consolidation["low"].min())
    if cons_range > consolidation_max_atr_mult * atr_val:
        return NONE_RESULT

    half = len(consolidation) // 2
    first_half_range = float(consolidation.iloc[:half]["high"].max() - consolidation.iloc[:half]["low"].min()) if half else cons_range
    second_half_range = float(consolidation.iloc[half:]["high"].max() - consolidation.iloc[half:]["low"].min())
    shape = "pennant" if first_half_range > 0 and second_half_range < first_half_range * 0.7 else "flag"

    direction = 1 if pole_move > 0 else -1
    upper, lower = float(consolidation["high"].max()), float(consolidation["low"].min())
    current_price = float(df["close"].iloc[i])  # Kerze i: NICHT Teil der Konsolidierung, reine Bestaetigungskerze

    if direction > 0:
        return {"pattern": f"bull_{shape}", "confirmed": current_price > upper, "level": upper}
    else:
        return {"pattern": f"bear_{shape}", "confirmed": current_price < lower, "level": lower}


def detect_wide_pattern(df: pd.DataFrame, i: int, atr_val: float) -> dict:
    """Kombiniert alle drei Erkenner hier in einer Prioritaet (spezifischer/
    verlaesslicher zuerst): Cup & Handle -> Rounding -> Flagge/Wimpel.
    Wird von signals.py nur aufgerufen, wenn structure.detect_chart_pattern
    (die Pivot-basierten Muster) nichts Bestaetigtes gefunden hat."""
    cup = detect_cup_and_handle(df, i, atr_val)
    if cup["confirmed"]:
        return cup
    inverse_cup = detect_inverse_cup_and_handle(df, i, atr_val)
    if inverse_cup["confirmed"]:
        return inverse_cup
    rounding = detect_rounding(df, i, atr_val)
    if rounding["confirmed"]:
        return rounding
    flag = detect_flag_pennant(df, i, atr_val)
    if flag["confirmed"]:
        return flag
    return NONE_RESULT
