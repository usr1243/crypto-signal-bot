"""
RSS-News -- kostenlos, kein Key, keine Limits (PLAN.md Abschnitt 2:
"unterschaetzt, oft ausreichend"). Laeuft unabhaengig davon, ob CryptoPanic
konfiguriert ist -- das macht macro.py robust gegen einen einzelnen
fehlenden/kaputten Key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import feedparser

FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "reuters_markets": "https://feeds.reuters.com/reuters/businessNews",
}


def fetch_headlines(max_age_hours: int = 24, limit_per_feed: int = 10) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    headlines: list[dict] = []

    for source, url in FEEDS.items():
        try:
            parsed = feedparser.parse(url)
        except Exception:
            continue

        for entry in parsed.entries[:limit_per_feed]:
            published = _parse_entry_time(entry)
            if published and published < cutoff:
                continue
            headlines.append({
                "title": entry.get("title", "").strip(),
                "source": source,
                "published_at": published.isoformat() if published else None,
                "url": entry.get("link"),
            })

    return headlines


def _parse_entry_time(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None
