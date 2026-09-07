"""
Zinsen/Inflation via FRED (Federal Reserve Economic Data) -- kostenlos,
braucht aber einen (gratis) API-Key: https://fred.stlouisfed.org/docs/api/api_key.html

Ohne FRED_API_KEY liefert fetch_macro_series() ein leeres Dict statt eines
Fehlers -- macro.py behandelt fehlende Fundamentaldaten als "nicht verfuegbar",
nicht als Absturzgrund. Siehe PLAN.md: "Gratis-Keys holen: FRED, CryptoPanic".
"""

from __future__ import annotations

import os

import requests

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "fed_funds_rate": "FEDFUNDS",  # Leitzins
    "cpi_yoy": "CPIAUCSL",           # Verbraucherpreisindex (Rohwert, YoY wird hier berechnet)
    "unemployment": "UNRATE",           # Arbeitslosenquote
    "yield_curve_10y_2y": "T10Y2Y",       # Zinskurve 10J-2J (negativ = invertiert = Rezessionssignal)
}


def _fetch_series(series_id: str, api_key: str, limit: int = 13) -> list[dict]:
    resp = requests.get(
        BASE_URL,
        params={"series_id": series_id, "api_key": api_key, "file_type": "json",
                "sort_order": "desc", "limit": limit},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("observations", [])


def fetch_macro_series() -> dict:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return {"available": False, "reason": "FRED_API_KEY nicht gesetzt"}

    out: dict = {"available": True}
    for key, series_id in SERIES.items():
        try:
            obs = _fetch_series(series_id, api_key)
            values = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]
            if not values:
                continue
            latest_date, latest_val = values[0]
            entry = {"date": latest_date, "value": latest_val}
            if key == "cpi_yoy" and len(values) >= 13:
                # grobe YoY-Naeherung: aktueller Indexwert vs. Wert vor ~12 Monatsbeobachtungen
                year_ago_val = values[12][1]
                entry["yoy_pct"] = round((latest_val - year_ago_val) / year_ago_val * 100, 2)
            out[key] = entry
        except Exception as exc:
            out[key] = {"error": str(exc)}
    return out
