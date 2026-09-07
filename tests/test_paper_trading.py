"""Tests fuer die Paper-Trading-Engine: Trade-Lebenszyklus und Circuit-Breaker
(PLAN.md Abschnitt 7 -- Tagesverlust -3%, Max-Drawdown -15%)."""

from __future__ import annotations

from src import db, paper_trading
from src.signals import TechnicalSignal, TradeProposal


def _open_and_close(conn, entry, stop, target, exit_price, candidate="LONG"):
    sig = TechnicalSignal(symbol="BTC/USD", timeframe="1h", ts="x", price=entry, candidate=candidate)
    proposal = TradeProposal(entry=entry, stop=stop, target=target, risk_pct_of_price=1.0,
                              reward_risk_ratio=2.0, size_pct_of_account=1.0)
    signal_id = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
    paper_trading.open_trade(conn, signal_id, sig, proposal)
    return paper_trading.check_and_close(conn, "BTC/USD", exit_price)


def test_open_trade_appears_in_open_trades(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    sig = TechnicalSignal(symbol="ETH/USD", timeframe="1h", ts="x", price=2000, candidate="LONG")
    proposal = TradeProposal(entry=2000, stop=1950, target=2100, risk_pct_of_price=2.5,
                              reward_risk_ratio=2.0, size_pct_of_account=1.0)
    signal_id = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
    paper_trading.open_trade(conn, signal_id, sig, proposal)

    open_trades = paper_trading.get_open_trades(conn)
    assert len(open_trades) == 1
    assert open_trades[0].symbol == "ETH/USD"


def test_stop_hit_closes_trade_with_negative_pnl(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    closed = _open_and_close(conn, entry=60000, stop=59000, target=62000, exit_price=58900)
    assert len(closed) == 1
    assert closed[0]["reason"] == "stop"
    assert closed[0]["pnl_pct"] < 0
    assert paper_trading.get_open_trades(conn) == []


def test_target_hit_closes_trade_with_positive_pnl(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    closed = _open_and_close(conn, entry=60000, stop=59000, target=62000, exit_price=62100)
    assert closed[0]["reason"] == "target"
    assert closed[0]["pnl_pct"] > 0


def test_price_between_stop_and_target_does_not_close(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    closed = _open_and_close(conn, entry=60000, stop=59000, target=62000, exit_price=60500)
    assert closed == []
    assert len(paper_trading.get_open_trades(conn)) == 1


def test_circuit_breaker_off_with_no_history(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    halted, reason = paper_trading.check_circuit_breakers(conn)
    assert halted is False
    assert reason is None


def test_daily_loss_limit_triggers_circuit_breaker(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    # 5 Verlust-Trades heute a ca. -1.x% Konto -- muss die -3%-Tagesgrenze reissen
    for _ in range(5):
        _open_and_close(conn, entry=60000, stop=59000, target=62000, exit_price=58900)

    halted, reason = paper_trading.check_circuit_breakers(conn)
    assert halted is True
    assert "Tagesverlust" in reason


def test_pause_state_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    assert db.is_paused(conn) is False
    db.set_paused(conn, True)
    assert db.is_paused(conn) is True
    db.set_paused(conn, False)
    assert db.is_paused(conn) is False


# ---------------------------------------------------------------------------
# PLAN Stufe A1 -- Dubletten-Sperre / Positions-Obergrenze
# ---------------------------------------------------------------------------

def _make_signal_and_proposal(symbol="BTC/USD", entry=60000, stop=59000, target=62000):
    sig = TechnicalSignal(symbol=symbol, timeframe="1h", ts="x", price=entry, candidate="LONG")
    proposal = TradeProposal(entry=entry, stop=stop, target=target, risk_pct_of_price=1.0,
                              reward_risk_ratio=2.0, size_pct_of_account=1.0)
    return sig, proposal


def test_second_signal_on_same_symbol_does_not_open_second_trade(tmp_path):
    """Regression fuer den echten Bug: TIMEFRAMES=1h,15m eroeffnete dieselbe
    Position doppelt (belegt: Signale 77/79, beide ETH/USD, 2026-09-04 08:57:57)."""
    conn = db.connect(tmp_path / "t.sqlite3")
    sig, proposal = _make_signal_and_proposal()

    signal_id_1 = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
    first = paper_trading.open_trade(conn, signal_id_1, sig, proposal)
    assert first is not None

    signal_id_2 = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
    second = paper_trading.open_trade(conn, signal_id_2, sig, proposal)
    assert second is None

    assert len(paper_trading.get_open_trades(conn, symbol="BTC/USD")) == 1


def test_can_open_trade_reports_duplicate_reason(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    sig, proposal = _make_signal_and_proposal()
    signal_id = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
    paper_trading.open_trade(conn, signal_id, sig, proposal)

    allowed, reason = paper_trading.can_open_trade(conn, "BTC/USD")
    assert allowed is False
    assert "bereits offene Position" in reason


def test_position_cap_blocks_further_trades_across_symbols(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    symbols = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"]
    opened = []
    for sym in symbols:
        sig, proposal = _make_signal_and_proposal(symbol=sym)
        signal_id = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
        opened.append(paper_trading.open_trade(conn, signal_id, sig, proposal))

    # Die ersten MAX_OPEN_POSITIONS (3) gehen durch, der vierte (anderes Symbol,
    # also nicht von der Dubletten-Sperre betroffen) wird von der Obergrenze gestoppt.
    assert opened[: paper_trading.MAX_OPEN_POSITIONS].count(None) == 0
    assert opened[paper_trading.MAX_OPEN_POSITIONS] is None
    assert len(paper_trading.get_open_trades(conn)) == paper_trading.MAX_OPEN_POSITIONS


# ---------------------------------------------------------------------------
# PLAN Stufe A2 -- Circuit-Breaker: unrealisierte Verluste + Reset
# ---------------------------------------------------------------------------

def test_open_position_worst_case_triggers_daily_loss_breaker(tmp_path):
    """Vorher zaehlten offene (unrealisierte) Positionen NULL fuer den
    Circuit-Breaker -- man konnte mit mehreren offenen Positionen tief im Minus
    stehen, ohne dass er ausloest, weil nichts GESCHLOSSEN war."""
    conn = db.connect(tmp_path / "t.sqlite3")
    # 4 offene Positionen a 1% Konto-Risiko = 4% Worst-Case, reisst die -3%-Grenze,
    # ohne dass auch nur ein Trade geschlossen wurde.
    for i in range(paper_trading.MAX_OPEN_POSITIONS + 1):
        sig, proposal = _make_signal_and_proposal(symbol=f"SYM{i}/USD")
        signal_id = db.save_signal(conn, sig.to_dict(), proposal.__dict__)
        paper_trading.open_trade(conn, signal_id, sig, proposal)

    halted, reason = paper_trading.check_circuit_breakers(conn)
    assert halted is True
    assert "Tagesverlust" in reason
    assert "Worst-Case" in reason


def test_reset_circuit_breaker_clears_daily_loss_halt(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite3")
    for _ in range(5):
        _open_and_close(conn, entry=60000, stop=59000, target=62000, exit_price=58900)

    halted, _ = paper_trading.check_circuit_breakers(conn)
    assert halted is True

    paper_trading.reset_circuit_breaker(conn)

    halted_after, reason_after = paper_trading.check_circuit_breakers(conn)
    assert halted_after is False
    assert reason_after is None
