"""
SQLite-Speicher. Schema: signals, macro_state, paper_trades, telegram_actions.

WICHTIG -- keine Migrationen: `CREATE TABLE IF NOT EXISTS` legt eine
Tabelle nur an, wenn sie fehlt; nachtraeglich hinzugefuegte Spalten an
einer bereits existierenden Tabelle werden NICHT automatisch ergaenzt.
Aendert sich das Schema hier (neue Spalte etc.), muss data/trading_bot.sqlite3
geloescht werden (Testdaten, kein Problem) oder eine echte Migration
geschrieben werden (relevant, sobald in Stufe 4 echte Paper-Trading-Historie
wertvoll wird und nicht mehr wegwerfbar ist).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts TEXT NOT NULL,
    price REAL NOT NULL,
    candidate TEXT NOT NULL,
    score INTEGER NOT NULL,
    technical_json TEXT NOT NULL,
    macro_json TEXT,
    trade_proposal_json TEXT,
    sent_to_telegram INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS macro_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    valid_until TEXT,
    regime TEXT,
    confidence REAL,
    veto_long INTEGER,
    veto_short INTEGER,
    size_multiplier REAL,
    reasoning TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS telegram_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    action TEXT NOT NULL,  -- "execute" | "ignore" | "later"
    telegram_message_id INTEGER,
    result TEXT,
    acted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Anfragen von nicht-Admin-Nutzern (z.B. dem Trading-Kollegen) in einer
-- gemeinsamen Telegram-Gruppe. Werden NIE automatisch umgesetzt -- landen
-- hier, gehen als Freigabe-Bitte an den Admin (Lorenz), und werden erst nach
-- /approve beim naechsten Claude-Code-Gespraech gemeinsam angeschaut.
-- Ersatz fuer eine fremde "Claude-in-Telegram"-Bruecke, die eine unklare
-- SSH-Funktion und unbestaetigte Freigabe-Logik hatte (siehe Konversation
-- vom 2026-09-03) -- das hier ist Code, den wir vollstaendig kennen.
CREATE TABLE IF NOT EXISTS change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_user_id INTEGER NOT NULL,
    requester_name TEXT,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | done
    admin_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    size_pct REAL NOT NULL,
    -- Einstellungen mitspeichern: sonst ist spaeter nicht mehr nachvollziehbar,
    -- mit welchem R:R / ATR-Multiple ein historischer Trade entstanden ist.
    reward_risk_ratio REAL,
    atr_multiple REAL,
    status TEXT NOT NULL DEFAULT 'open',
    closed_price REAL,
    pnl_pct REAL,
    opened_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Fehlende Spalten nachtraeglich ergaenzen.

    `CREATE TABLE IF NOT EXISTS` legt eine Tabelle nur an, wenn sie fehlt --
    neue Spalten an einer BESTEHENDEN Tabelle ergaenzt es NICHT. Ohne das hier
    muesste man bei jeder Schemaerweiterung die DB loeschen und die bisherige
    Historie wegwerfen. SQLite kann `ALTER TABLE ADD COLUMN`, das reicht fuer
    additive Aenderungen (mehr braucht dieses Projekt bisher nicht).
    """
    erwartet = {
        "paper_trades": {
            "reward_risk_ratio": "REAL",
            "atr_multiple": "REAL",
        },
    }
    for tabelle, spalten in erwartet.items():
        vorhanden = {row[1] for row in conn.execute(f"PRAGMA table_info({tabelle})")}
        if not vorhanden:
            continue  # Tabelle existiert noch nicht -- SCHEMA legt sie gleich vollstaendig an
        for name, typ in spalten.items():
            if name not in vorhanden:
                conn.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {typ}")
    conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)  # fehlende Spalten an bereits bestehenden Tabellen ergaenzen
    return conn


def connect_with_retry(db_path: str | Path, attempts: int = 3, delay_s: float = 2.0) -> sqlite3.Connection:
    """
    Wie connect(), aber mit kurzen Wiederholversuchen bei "unable to open
    database file" (SQLITE_CANTOPEN) -- beobachtet im Dauerbetrieb, vermutlich
    ein kurzes Aussetzen des Dateisystemzugriffs (siehe run_loop.py). Ein
    transientes CANTOPEN loest sich in aller Regel beim naechsten Versuch von
    selbst; erst nach `attempts` Fehlschlaegen wird der Fehler weitergereicht.
    """
    import time
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return connect(db_path)
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(delay_s)
    raise last_exc


def save_signal(
    conn: sqlite3.Connection,
    technical: dict,
    trade_proposal: dict | None,
    macro: dict | None = None,
    sent_to_telegram: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO signals
           (symbol, timeframe, ts, price, candidate, score,
            technical_json, macro_json, trade_proposal_json, sent_to_telegram)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            technical["symbol"], technical["tf"], technical["ts"], technical["price"],
            technical["candidate"], technical["score"],
            json.dumps(technical, ensure_ascii=False),
            json.dumps(macro, ensure_ascii=False) if macro else None,
            json.dumps(trade_proposal, ensure_ascii=False) if trade_proposal else None,
            int(sent_to_telegram),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def save_macro_state(conn: sqlite3.Connection, state: dict) -> int:
    cur = conn.execute(
        """INSERT INTO macro_state
           (ts, valid_until, regime, confidence, veto_long, veto_short, size_multiplier, reasoning, raw_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            state.get("generated_at"), state.get("valid_until"), state.get("regime"),
            state.get("confidence"), int(state.get("veto_long", False)), int(state.get("veto_short", False)),
            state.get("size_multiplier"), state.get("reasoning"), json.dumps(state, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_signal(conn: sqlite3.Connection, signal_id: int) -> dict | None:
    row = conn.execute(
        "SELECT technical_json, trade_proposal_json, macro_json FROM signals WHERE id=?", (signal_id,)
    ).fetchone()
    if row is None:
        return None
    technical_json, trade_proposal_json, macro_json = row
    return {
        "technical": json.loads(technical_json),
        "trade_proposal": json.loads(trade_proposal_json) if trade_proposal_json else None,
        "macro": json.loads(macro_json) if macro_json else None,
    }


def record_telegram_action(conn: sqlite3.Connection, signal_id: int, action: str,
                            telegram_message_id: int | None = None, result: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO telegram_actions (signal_id, action, telegram_message_id, result)
           VALUES (?, ?, ?, ?)""",
        (signal_id, action, telegram_message_id, result),
    )
    conn.commit()
    return int(cur.lastrowid)


def create_change_request(conn: sqlite3.Connection, requester_user_id: int, requester_name: str, message: str) -> int:
    cur = conn.execute(
        "INSERT INTO change_requests (requester_user_id, requester_name, message) VALUES (?, ?, ?)",
        (requester_user_id, requester_name, message),
    )
    conn.commit()
    return int(cur.lastrowid)


def decide_change_request(conn: sqlite3.Connection, request_id: int, status: str, admin_note: str | None = None) -> bool:
    """status: 'approved' | 'rejected'. Gibt False zurueck, wenn die ID nicht existiert oder
    schon entschieden wurde (verhindert doppeltes /approve auf dieselbe Anfrage)."""
    cur = conn.execute(
        "UPDATE change_requests SET status=?, admin_note=?, decided_at=datetime('now') "
        "WHERE id=? AND status='pending'",
        (status, admin_note, request_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_pending_change_requests(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, requester_name, message, created_at FROM change_requests "
        "WHERE status='pending' ORDER BY id"
    ).fetchall()
    return [{"id": r[0], "requester_name": r[1], "message": r[2], "created_at": r[3]} for r in rows]


def get_approved_change_requests(conn: sqlite3.Connection) -> list[dict]:
    """Freigegebene, aber noch nicht bearbeitete Anfragen -- das liest main.py/Claude
    zu Beginn eines Gespraechs, um zu sehen, was der Kollege vorgeschlagen hat."""
    rows = conn.execute(
        "SELECT id, requester_name, message, created_at FROM change_requests "
        "WHERE status='approved' ORDER BY id"
    ).fetchall()
    return [{"id": r[0], "requester_name": r[1], "message": r[2], "created_at": r[3]} for r in rows]


def mark_change_request_done(conn: sqlite3.Connection, request_id: int) -> None:
    conn.execute("UPDATE change_requests SET status='done' WHERE id=?", (request_id,))
    conn.commit()


def is_paused(conn: sqlite3.Connection) -> bool:
    """Freqtrade-inspiriert: /pause und /resume ueber Telegram (siehe telegram_listener.py).
    Pausiert nur NEUE Signale/Trades -- offene Paper-Trades werden weiter ueberwacht
    (check_trades_job in run_loop.py fragt das hier nicht ab, absichtlich)."""
    row = conn.execute("SELECT value FROM bot_state WHERE key='paused'").fetchone()
    return row is not None and row[0] == "1"


def set_paused(conn: sqlite3.Connection, paused: bool) -> None:
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES ('paused', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("1" if paused else "0",),
    )
    conn.commit()


def get_fresh_macro_state(conn: sqlite3.Connection) -> dict | None:
    """Letzten Makro-Snapshot zurueckgeben, falls er noch innerhalb valid_until liegt -- sonst None.

    Verhindert, dass main.py bei jedem Lauf einen neuen LLM-Call ausloest;
    Schicht B soll laut PLAN.md nur ~1x/Tag laufen, nicht bei jedem Signal-Check.
    """
    row = conn.execute(
        "SELECT raw_json, valid_until FROM macro_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    raw_json, valid_until = row
    if valid_until is None:
        return None
    from datetime import datetime, timezone
    try:
        if datetime.fromisoformat(valid_until) < datetime.now(timezone.utc):
            return None
    except ValueError:
        return None
    return json.loads(raw_json)
