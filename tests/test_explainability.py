"""Tests fuer die C9-Erklaerbarkeits-Bruecke (src/explainability.py): eine
freqtrade-Position traegt nur eine kurze Referenz-ID in enter_tag, die volle
Score-Aufschluesselung muss darueber wieder auffindbar sein -- sonst waere
die freqtrade-Strategie live eine Blackbox."""

from __future__ import annotations

import pytest

from src import explainability


@pytest.fixture
def conn(tmp_path):
    return explainability.connect(tmp_path / "test.sqlite3")


def _sample_breakdown() -> list[dict]:
    return [
        {"rule": "trend", "points": 20, "detail": "EMA50 über EMA200 (Aufwärtstrend)"},
        {"rule": "macd", "points": 15, "detail": "MACD-Histogramm positiv"},
        {"rule": "rsi", "points": -10, "detail": "RSI 72.0 überkauft (>70)"},
    ]


def test_make_ref_id_stays_well_under_enter_tag_limit():
    ref_id = explainability.make_ref_id("BTC/USD", "2026-09-05T14:00:00+00:00")
    assert ref_id == "BTC/USD@2026-09-05T14:00:00+00:00"
    assert len(ref_id) < 64  # freqtrades enter_tag-Kappung


def test_save_and_get_breakdown_roundtrip(conn):
    rows = [("BTC/USD", "2026-09-05T14:00:00+00:00", 81000.5, 25, "LONG", _sample_breakdown())]
    explainability.save_breakdowns_bulk(conn, rows)

    ref_id = explainability.make_ref_id("BTC/USD", "2026-09-05T14:00:00+00:00")
    entry = explainability.get_breakdown(conn, ref_id)

    assert entry is not None
    assert entry["pair"] == "BTC/USD"
    assert entry["score"] == 25
    assert entry["candidate"] == "LONG"
    assert entry["breakdown"] == _sample_breakdown()


def test_get_breakdown_returns_none_for_unknown_ref_id(conn):
    assert explainability.get_breakdown(conn, "ETH/USD@2026-01-01T00:00:00+00:00") is None


def test_bulk_save_is_idempotent_on_rerun(conn):
    """populate_indicators() laeuft bei freqtrade jeden Zyklus erneut ueber
    dasselbe Fenster -- ein erneuter Schreibvorgang mit denselben Werten darf
    keine Duplikate erzeugen und keinen Fehler werfen."""
    rows = [("BTC/USD", "2026-09-05T14:00:00+00:00", 81000.5, 25, "LONG", _sample_breakdown())]
    explainability.save_breakdowns_bulk(conn, rows)
    explainability.save_breakdowns_bulk(conn, rows)

    count = conn.execute("SELECT COUNT(*) FROM score_breakdowns").fetchone()[0]
    assert count == 1


def test_bulk_save_updates_on_rescoring(conn):
    """Aendert sich der Score fuer denselben Zeitpunkt (z.B. weil ein Pivot
    inzwischen anders klassifiziert wird), muss der neue Stand gewinnen."""
    ref_id = explainability.make_ref_id("BTC/USD", "2026-09-05T14:00:00+00:00")
    explainability.save_breakdowns_bulk(
        conn, [("BTC/USD", "2026-09-05T14:00:00+00:00", 81000.5, 25, "LONG", _sample_breakdown())]
    )
    explainability.save_breakdowns_bulk(
        conn, [("BTC/USD", "2026-09-05T14:00:00+00:00", 81000.5, 45, "LONG", [{"rule": "trend", "points": 45, "detail": "geaendert"}])]
    )

    entry = explainability.get_breakdown(conn, ref_id)
    assert entry["score"] == 45
    assert entry["breakdown"] == [{"rule": "trend", "points": 45, "detail": "geaendert"}]


def test_save_breakdowns_bulk_with_empty_rows_does_not_error(conn):
    explainability.save_breakdowns_bulk(conn, [])
    count = conn.execute("SELECT COUNT(*) FROM score_breakdowns").fetchone()[0]
    assert count == 0


def test_format_breakdown_text_sorts_by_magnitude_and_shows_sign():
    entry = {
        "pair": "BTC/USD", "ts": "2026-09-05T14:00:00+00:00", "price": 81000.5,
        "score": 25, "candidate": "LONG", "breakdown": _sample_breakdown(),
    }
    text = explainability.format_breakdown_text(entry)
    lines = text.splitlines()
    assert "BTC/USD" in lines[0]
    assert "25" in lines[1] and "LONG" in lines[1]
    assert lines[2] == "Warum:"
    # staerkster Beitrag (Betrag 20) zuerst, dann 15, dann -10
    assert "+20" in lines[3]
    assert "+15" in lines[4]
    assert "-10" in lines[5]
