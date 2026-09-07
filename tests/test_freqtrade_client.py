"""Tests fuer den C10-REST-Client (src/freqtrade_client.py). Kein echtes
Netzwerk -- requests.request wird gemockt, damit die Tests deterministisch
und offline laufen (Konvention wie in den anderen Test-Dateien)."""

from __future__ import annotations

import pytest

from src.freqtrade_client import FreqtradeAPIError, FreqtradeClient


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json_data


@pytest.fixture
def client():
    return FreqtradeClient(base_url="http://fake-freqtrade:8080", username="admin", password="secret")


def test_status_calls_correct_url_and_auth(monkeypatch, client):
    captured = {}

    def fake_request(method, url, auth=None, timeout=None, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["auth"] = auth
        return FakeResponse(json_data=[{"pair": "BTC/USD"}])

    monkeypatch.setattr("src.freqtrade_client.requests.request", fake_request)

    result = client.status()

    assert captured["method"] == "GET"
    assert captured["url"] == "http://fake-freqtrade:8080/api/v1/status"
    assert captured["auth"] == ("admin", "secret")
    assert result == [{"pair": "BTC/USD"}]


def test_pause_posts_to_pause_endpoint(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(
        "src.freqtrade_client.requests.request",
        lambda method, url, **kw: captured.update(method=method, url=url) or FakeResponse(json_data={"status": "ok"}),
    )
    client.pause()
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/pause")


def test_resume_posts_to_start_not_reload_config(monkeypatch, client):
    """Regression: /reload_config laedt nur die Config neu, aendert NICHT den
    Pause-Zustand -- resume() muss /start aufrufen (siehe Modul-Docstring)."""
    captured = {}
    monkeypatch.setattr(
        "src.freqtrade_client.requests.request",
        lambda method, url, **kw: captured.update(url=url) or FakeResponse(json_data={"status": "ok"}),
    )
    client.resume()
    assert captured["url"].endswith("/api/v1/start")


def test_force_exit_sends_tradeid_in_body(monkeypatch, client):
    captured = {}

    def fake_request(method, url, auth=None, timeout=None, json=None, **kwargs):
        captured["json"] = json
        captured["url"] = url
        return FakeResponse(json_data={"result": "closed"})

    monkeypatch.setattr("src.freqtrade_client.requests.request", fake_request)

    client.force_exit(42)

    assert captured["url"].endswith("/api/v1/forceexit")
    assert captured["json"] == {"tradeid": "42"}


def test_force_exit_defaults_to_all(monkeypatch, client):
    captured = {}
    monkeypatch.setattr(
        "src.freqtrade_client.requests.request",
        lambda method, url, json=None, **kw: captured.update(json=json) or FakeResponse(json_data={}),
    )
    client.force_exit()
    assert captured["json"] == {"tradeid": "all"}


def test_panic_calls_force_exit_all_then_stop_in_order(monkeypatch, client):
    calls = []

    def fake_request(method, url, json=None, **kw):
        calls.append((method, url.rsplit("/", 1)[-1], json))
        return FakeResponse(json_data={"status": "ok"})

    monkeypatch.setattr("src.freqtrade_client.requests.request", fake_request)

    client.panic()

    assert calls == [
        ("POST", "forceexit", {"tradeid": "all"}),
        ("POST", "stop", None),
    ]


def test_ping_returns_true_on_success(monkeypatch, client):
    monkeypatch.setattr(
        "src.freqtrade_client.requests.request",
        lambda method, url, **kw: FakeResponse(json_data={"status": "pong"}),
    )
    assert client.ping() is True


def test_ping_returns_false_when_unreachable(monkeypatch, client):
    import requests as requests_module

    def fake_request(method, url, **kw):
        raise requests_module.exceptions.ConnectionError("kein Netz")

    monkeypatch.setattr("src.freqtrade_client.requests.request", fake_request)
    assert client.ping() is False


def test_non_ok_response_raises_freqtrade_api_error(monkeypatch, client):
    monkeypatch.setattr(
        "src.freqtrade_client.requests.request",
        lambda method, url, **kw: FakeResponse(status_code=401, text="Unauthorized"),
    )
    with pytest.raises(FreqtradeAPIError, match="401"):
        client.status()


def test_network_error_raises_freqtrade_api_error(monkeypatch, client):
    import requests as requests_module

    def fake_request(method, url, **kw):
        raise requests_module.exceptions.Timeout("zu langsam")

    monkeypatch.setattr("src.freqtrade_client.requests.request", fake_request)
    with pytest.raises(FreqtradeAPIError, match="nicht erreichbar"):
        client.status()
