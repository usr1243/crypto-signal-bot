"""
Tests fuer die Admin/Kollege-Rollentrennung (telegram_listener.py + db.py).

Kernanspruch, der hier geprueft wird: eine Nachricht von jemandem, der NICHT
der Admin ist, darf NIEMALS direkt etwas bewirken -- sie landet immer als
change_request, niemals als sofortige Aktion. Das ist die zentrale
Sicherheitsgrenze der ganzen Datei, deshalb eigene Tests statt nur Mitlaufen.
"""

from __future__ import annotations

import os

import pytest

from src import db
from src import telegram_listener
from src.telegram_listener import _is_admin


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.sqlite3")


def test_is_admin_true_when_no_admin_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_TELEGRAM_USER_ID", raising=False)
    assert _is_admin(12345) is True  # rueckwaertskompatibel: keine Rollen-Trennung konfiguriert


def test_is_admin_only_matches_configured_id(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_USER_ID", "111")
    assert _is_admin(111) is True
    assert _is_admin(222) is False


def test_change_request_created_with_pending_status(conn):
    request_id = db.create_change_request(conn, 999, "Kollege", "Stell R:R auf 5")
    pending = db.get_pending_change_requests(conn)
    assert len(pending) == 1
    assert pending[0]["id"] == request_id
    assert pending[0]["message"] == "Stell R:R auf 5"


def test_approve_moves_from_pending_to_approved(conn):
    request_id = db.create_change_request(conn, 999, "Kollege", "Test")
    ok = db.decide_change_request(conn, request_id, "approved")
    assert ok is True
    assert db.get_pending_change_requests(conn) == []
    approved = db.get_approved_change_requests(conn)
    assert len(approved) == 1
    assert approved[0]["id"] == request_id


def test_cannot_decide_same_request_twice(conn):
    """Verhindert doppeltes /approve -- z.B. wenn der Admin die Nachricht zweimal schickt."""
    request_id = db.create_change_request(conn, 999, "Kollege", "Test")
    assert db.decide_change_request(conn, request_id, "approved") is True
    assert db.decide_change_request(conn, request_id, "rejected") is False  # schon entschieden


def test_reject_does_not_appear_as_approved(conn):
    request_id = db.create_change_request(conn, 999, "Kollege", "Riskanter Wunsch")
    db.decide_change_request(conn, request_id, "rejected", admin_note="zu riskant")
    assert db.get_approved_change_requests(conn) == []
    assert db.get_pending_change_requests(conn) == []


def test_mark_done_removes_from_approved_list(conn):
    request_id = db.create_change_request(conn, 999, "Kollege", "Test")
    db.decide_change_request(conn, request_id, "approved")
    db.mark_change_request_done(conn, request_id)
    assert db.get_approved_change_requests(conn) == []


# ---------------------------------------------------------------------------
# PLAN Stufe A2 -- /reset_breaker ist ebenfalls admin-only, wie /pause & Co.
# ---------------------------------------------------------------------------

def test_reset_breaker_command_sets_reset_timestamp_for_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(telegram_listener, "DB_PATH", db_path)
    monkeypatch.setenv("ADMIN_TELEGRAM_USER_ID", "111")
    sent = []
    monkeypatch.setattr(telegram_listener, "_send_text", lambda token, chat_id, text: sent.append(text))

    telegram_listener.handle_command("tok", 1, "/reset_breaker", from_id=111, from_name="Lorenz")

    result_conn = db.connect(db_path)
    row = result_conn.execute("SELECT value FROM bot_state WHERE key='circuit_breaker_reset_at'").fetchone()
    assert row is not None
    assert any("zurueckgesetzt" in s for s in sent)


def test_reset_breaker_command_refused_for_non_admin(tmp_path, monkeypatch):
    """Wie /pause /resume /approve /reject: nur der Admin darf zuruecksetzen --
    sonst koennte der Kollege in der gemeinsamen Gruppe den Circuit-Breaker
    selbst aufheben, genau die Sicherheitsgrenze, um die es in dieser Datei geht."""
    db_path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(telegram_listener, "DB_PATH", db_path)
    monkeypatch.setenv("ADMIN_TELEGRAM_USER_ID", "111")
    sent = []
    monkeypatch.setattr(telegram_listener, "_send_text", lambda token, chat_id, text: sent.append(text))

    telegram_listener.handle_command("tok", 1, "/reset_breaker", from_id=222, from_name="Kollege")

    result_conn = db.connect(db_path)
    row = result_conn.execute("SELECT value FROM bot_state WHERE key='circuit_breaker_reset_at'").fetchone()
    assert row is None
    assert any("nur der Admin" in s for s in sent)
