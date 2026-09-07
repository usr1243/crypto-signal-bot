"""Tests fuer die C10-Telegram-Befehle (/ft_status /ft_pause /ft_resume
/ft_forceexit /ft_panic) -- admin-only, sprechen ueber freqtrade_client.py
mit freqtrade, nicht mit unserem eigenen execution.py."""

from __future__ import annotations

import pytest

from src import telegram_listener
from src.freqtrade_client import FreqtradeAPIError


class FakeFreqtradeClient:
    def __init__(self):
        self.calls = []
        self.status_return = []
        self.raise_error = False

    def _maybe_raise(self):
        if self.raise_error:
            raise FreqtradeAPIError("freqtrade nicht erreichbar (Test)")

    def status(self):
        self.calls.append(("status",))
        self._maybe_raise()
        return self.status_return

    def pause(self):
        self.calls.append(("pause",))
        self._maybe_raise()

    def resume(self):
        self.calls.append(("resume",))
        self._maybe_raise()

    def force_exit(self, trade_id="all"):
        self.calls.append(("force_exit", trade_id))
        self._maybe_raise()

    def panic(self):
        self.calls.append(("panic",))
        self._maybe_raise()


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(telegram_listener, "_send_text", lambda token, chat_id, text: messages.append(text))
    return messages


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite3"
    monkeypatch.setattr(telegram_listener, "DB_PATH", path)
    return path


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeFreqtradeClient()
    monkeypatch.setattr(telegram_listener, "_freqtrade_client", lambda: client)
    return client


ADMIN_ID = 111
COLLEAGUE_ID = 222


@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_USER_ID", str(ADMIN_ID))


def test_ft_status_not_configured(sent, db_path, monkeypatch):
    monkeypatch.setattr(telegram_listener, "_freqtrade_client", lambda: None)
    telegram_listener.handle_command("tok", 1, "/ft_status", from_id=ADMIN_ID, from_name="Lorenz")
    assert any("nicht konfiguriert" in m for m in sent)


def test_ft_status_no_open_trades(sent, db_path, fake_client):
    telegram_listener.handle_command("tok", 1, "/ft_status", from_id=ADMIN_ID, from_name="Lorenz")
    assert fake_client.calls == [("status",)]
    assert any("keine offenen Positionen" in m for m in sent)


def test_ft_status_formats_open_trades(sent, db_path, fake_client):
    fake_client.status_return = [
        {"trade_id": 7, "pair": "BTC/USD", "is_short": False, "open_rate": 81000.5, "stop_loss_abs": 80200.1},
    ]
    telegram_listener.handle_command("tok", 1, "/ft_status", from_id=ADMIN_ID, from_name="Lorenz")
    text = sent[-1]
    assert "#7" in text and "BTC/USD" in text and "LONG" in text
    assert "81,000.5" in text or "81,000.50" in text


def test_ft_pause_calls_client(sent, db_path, fake_client):
    telegram_listener.handle_command("tok", 1, "/ft_pause", from_id=ADMIN_ID, from_name="Lorenz")
    assert fake_client.calls == [("pause",)]
    assert any("pausiert" in m for m in sent)


def test_ft_resume_calls_client(sent, db_path, fake_client):
    telegram_listener.handle_command("tok", 1, "/ft_resume", from_id=ADMIN_ID, from_name="Lorenz")
    assert fake_client.calls == [("resume",)]
    assert any("fortgesetzt" in m for m in sent)


def test_ft_forceexit_defaults_to_all(sent, db_path, fake_client):
    telegram_listener.handle_command("tok", 1, "/ft_forceexit", from_id=ADMIN_ID, from_name="Lorenz")
    assert fake_client.calls == [("force_exit", "all")]


def test_ft_forceexit_with_explicit_id(sent, db_path, fake_client):
    telegram_listener.handle_command("tok", 1, "/ft_forceexit 7", from_id=ADMIN_ID, from_name="Lorenz")
    assert fake_client.calls == [("force_exit", "7")]


def test_ft_panic_calls_client(sent, db_path, fake_client):
    telegram_listener.handle_command("tok", 1, "/ft_panic", from_id=ADMIN_ID, from_name="Lorenz")
    assert fake_client.calls == [("panic",)]
    assert any("NOT-AUS" in m for m in sent)


def test_ft_commands_refused_for_non_admin(sent, db_path, fake_client):
    for cmd in ["/ft_status", "/ft_pause", "/ft_resume", "/ft_forceexit", "/ft_panic"]:
        telegram_listener.handle_command("tok", 1, cmd, from_id=COLLEAGUE_ID, from_name="Kollege")
    assert fake_client.calls == []  # kein einziger Aufruf durchgekommen
    assert all("nur der Admin" in m for m in sent)


def test_ft_api_error_is_shown_not_raised(sent, db_path, fake_client):
    fake_client.raise_error = True
    telegram_listener.handle_command("tok", 1, "/ft_status", from_id=ADMIN_ID, from_name="Lorenz")
    assert any("freqtrade-API-Fehler" in m for m in sent)
