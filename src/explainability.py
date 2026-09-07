"""
PLAN wild-booping-firefly.md, Stufe C9: die ScoreContribution-Aufschluesselung
(siehe signals.py) geht beim Umzug auf freqtrade NICHT automatisch mit --
freqtrades `enter_tag` ist auf ~64 Zeichen gekappt, viel zu kurz fuer ~20
Regelbegruendungen im Klartext. Ohne das hier waere die freqtrade-Strategie
live eine Blackbox, genau der Zustand, den der Kollege ausdruecklich nicht
wollte ("Man muss ueberpruefen koennen wie er handelt, denkt und tradet").

Loesung: eine eigene, kleine SQLite-Tabelle haelt die VOLLE Aufschluesselung
unter einer kurzen Referenz-ID ("<Paar>@<Kerzen-Zeitstempel>"). `enter_tag`
traegt nur diese ID -- SignalCoreStrategy.py schreibt sie in populate_indicators()
fuer jede Kerze mit einem Kandidaten, report_freqtrade.py liest sie ueber die
ID aus dem `enter_tag` eines echten freqtrade-Trades zurueck.

Bewusst UNABHAENGIG von db.py (das Schema fuer den bisherigen Docker-Stack
run_loop.py/telegram_listener.py) -- eigene, schlanke Datei, damit diese
Tabelle nicht an das alte System gekoppelt ist und eigenstaendig neben
freqtrades eigener trades-Tabelle laeuft.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS score_breakdowns (
    ref_id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    ts TEXT NOT NULL,
    price REAL,
    score INTEGER NOT NULL,
    candidate TEXT NOT NULL,
    breakdown_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def make_ref_id(pair: str, ts: str) -> str:
    """Kurze, eindeutige Referenz-ID -- passt sicher in freqtrades enter_tag
    (~64 Zeichen Kappung). Beispiel: "BTC/USD@2026-09-05T14:00:00+00:00"
    (35 Zeichen)."""
    return f"{pair}@{ts}"


def save_breakdowns_bulk(
    conn: sqlite3.Connection,
    rows: list[tuple[str, str, float, int, str, list[dict]]],
) -> None:
    """Schreibt viele Zeilen in EINEM Commit -- populate_indicators() laeuft
    ueber ein ganzes Fenster (im Live-Betrieb typischerweise einige hundert
    Kerzen pro Zyklus), ein Commit pro Zeile waere unnoetig teuer.

    Jede Zeile: (pair, ts, price, score, candidate, breakdown).
    breakdown ist eine Liste von dicts mit "rule"/"points"/"detail" (siehe
    signals.ScoreContribution.to_dict()) -- absichtlich kein Import von
    ScoreContribution hier, damit diese Datei ohne Zirkelbezug zu signals.py
    auskommt und mit jedem Objekt funktioniert, das to_dict()-kompatibel ist.

    ON CONFLICT/REPLACE: derselbe Kerzen-Zeitstempel wird bei jedem neuen
    Zyklus erneut berechnet (freqtrade fuehrt populate_indicators() ueber das
    ganze geladene Fenster neu aus) -- idempotent, keine Duplikate, keine
    wachsende Tabelle bei unveraenderten Werten.
    """
    if not rows:
        return
    payload = [
        (make_ref_id(pair, ts), pair, ts, price, score, candidate,
         json.dumps(breakdown, ensure_ascii=False))
        for pair, ts, price, score, candidate, breakdown in rows
    ]
    conn.executemany(
        """INSERT INTO score_breakdowns (ref_id, pair, ts, price, score, candidate, breakdown_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(ref_id) DO UPDATE SET
             price=excluded.price, score=excluded.score, candidate=excluded.candidate,
             breakdown_json=excluded.breakdown_json, saved_at=datetime('now')""",
        payload,
    )
    conn.commit()


def get_breakdown(conn: sqlite3.Connection, ref_id: str) -> dict | None:
    row = conn.execute(
        "SELECT pair, ts, price, score, candidate, breakdown_json FROM score_breakdowns WHERE ref_id=?",
        (ref_id,),
    ).fetchone()
    if row is None:
        return None
    pair, ts, price, score, candidate, breakdown_json = row
    return {
        "pair": pair, "ts": ts, "price": price, "score": score, "candidate": candidate,
        "breakdown": json.loads(breakdown_json),
    }


def format_breakdown_text(entry: dict) -> str:
    """Menschenlesbare Darstellung -- gleiches Format wie notify.py's
    "Warum"-Zeilen im bisherigen System, damit die Erklaerung fuer den
    Kollegen gleich aussieht, egal welches System sie erzeugt hat."""
    price = entry.get("price")
    price_str = f"{price:,.2f}" if price is not None else "?"
    lines = [
        f"{entry['pair']} · {entry['ts']} · Preis {price_str}",
        f"Score {entry['score']:+d}/100 -> {entry['candidate']}",
        "Warum:",
    ]
    for c in sorted(entry["breakdown"], key=lambda c: abs(c["points"]), reverse=True):
        lines.append(f"{c['points']:+4d}  {c['detail']}")
    return "\n".join(lines)
