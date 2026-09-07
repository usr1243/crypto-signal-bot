"""Tests fuer run_loop.py: Heartbeat/Totmann-Schalter (PLAN Stufe A3) und dass
Job-Fehler nicht nur in stdout verschwinden, sondern per Telegram gemeldet
werden -- der reale 18h-Netzausfall am 04.09. blieb genau deshalb unbemerkt."""

from __future__ import annotations

from src import run_loop


def test_heartbeat_sends_message_and_resets_counters(monkeypatch):
    sent = []
    monkeypatch.setattr(run_loop, "send_telegram", lambda text: sent.append(text) or True)

    run_loop._heartbeat_stats["checks"] = 7
    run_loop._heartbeat_stats["candidates"] = 2

    run_loop.heartbeat_job()

    assert len(sent) == 1
    assert "7 Signal-Check" in sent[0]
    assert "2 Kandidat" in sent[0]
    assert run_loop._heartbeat_stats == {"checks": 0, "candidates": 0}


def test_heartbeat_survives_telegram_failure(monkeypatch, capsys):
    def boom(text):
        raise RuntimeError("Netz weg")
    monkeypatch.setattr(run_loop, "send_telegram", boom)

    run_loop.heartbeat_job()  # darf nicht werfen

    captured = capsys.readouterr()
    assert "FEHLER in heartbeat_job" in captured.out


def test_alert_error_sends_telegram_message(monkeypatch):
    sent = []
    monkeypatch.setattr(run_loop, "send_telegram", lambda text: sent.append(text) or True)

    run_loop._alert_error("signal_job", ValueError("kaputt"))

    assert len(sent) == 1
    assert "signal_job" in sent[0]
    assert "kaputt" in sent[0]


def test_alert_error_does_not_raise_when_telegram_itself_fails(monkeypatch, capsys):
    def boom(text):
        raise RuntimeError("Netz weg")
    monkeypatch.setattr(run_loop, "send_telegram", boom)

    run_loop._alert_error("signal_job", ValueError("kaputt"))  # darf nicht werfen

    captured = capsys.readouterr()
    assert "FEHLER in signal_job" in captured.out
    assert "Telegram-Alarm selbst fehlgeschlagen" in captured.out
