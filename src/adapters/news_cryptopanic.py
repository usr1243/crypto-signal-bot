"""
CryptoPanic -- aggregierte Krypto-News mit Community-Bullish/Bearish-Tags.
Gratis-Dev-Key: https://cryptopanic.com/developers/api/

Ohne CRYPTOPANIC_API_KEY liefert fetch_headlines() eine leere Liste statt
eines Fehlers (macro.py nutzt dann nur RSS + Fear&Greed).
"""

from __future__ import annotations

import os

import requests

BASE_URL = "https://cryptopanic.com/api/v1/posts/"


def fetch_headlines(limit: int = 15) -> list[dict]:
    api_key = os.environ.get("CRYPTOPANIC_API_KEY")
    if not api_key:
        return []

    try:
        resp = requests.get(
            BASE_URL,
            params={"auth_token": api_key, "public": "true", "kind": "news"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])[:limit]
    except Exception as exc:
        return [{"title": f"[CryptoPanic-Fehler] {exc}", "source": "cryptopanic", "error": True}]

    return [
        {
            "title": r.get("title", ""),
            "source": "cryptopanic",
            "published_at": r.get("published_at"),
            "community_votes": r.get("votes", {}),
            "url": r.get("url"),
        }
        for r in results
    ]
