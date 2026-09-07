"""Fear & Greed Index via alternative.me -- kostenlos, kein Key."""

from __future__ import annotations

import requests

URL = "https://api.alternative.me/fng/"


def fetch_fear_greed() -> dict | None:
    try:
        resp = requests.get(URL, params={"limit": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        entry = data[0]
        return {"value": int(entry["value"]), "classification": entry["value_classification"]}
    except Exception:
        return None
