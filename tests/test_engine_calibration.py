"""
Kalibrier-Faelle fuer den Backtest-Motor: fuenf konstruierte Situationen, deren
richtige Antwort VORHER von Hand ausgerechnet wurde.

Warum das existiert: bevor man den eigenen Motor gegen freqtrade vergleicht,
muss man wissen, was der eigene Motor ueberhaupt tut. Faelle C, D und E sind
die Stellen, an denen sich Backtest-Engines typischerweise unterscheiden --
und an denen freqtrades Verhalten aus der Doku NICHT belegbar war. Hier wird
unser Verhalten festgenagelt, damit ein spaeterer Vergleich eine klare
Erwartung hat statt einer Vermutung.

Alle Faelle laufen mit fee=0 und slippage=0 -- reine Geometrie, keine
Rundungsdiskussion. Erwartung deshalb: exakte Gleichheit, keine Toleranz.

Rechenweg (gilt fuer alle Faelle, aus _close_trade mit fee=slippage=0):
    r_multiple = (exit_preis - entry_preis) / |entry_preis - stop|
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest import WARMUP_BARS, run_backtest

ENTRY_SIGNAL_BAR = WARMUP_BARS + 10      # Kerze, auf der signal_fn "LONG" meldet
ENTRY_BAR = ENTRY_SIGNAL_BAR + 1          # Entry passiert auf dem OPEN der Folgekerze
FLAT_PRICE = 100.0
STOP = 98.0                                # -> Risiko-Distanz = 2.0
TARGET = 104.0                             # -> Ziel-Distanz = 4.0 = 2R


def _flat_bars(n: int, price: float = FLAT_PRICE) -> list[dict]:
    return [{"open": price, "high": price, "low": price, "close": price, "volume": 1000.0}
            for _ in range(n)]


def _build_df(bars: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(bars), freq="1h", tz="UTC")
    return pd.DataFrame(bars, index=idx)


def _long_on(bar_indices: set[int]):
    """signal_fn, die genau auf den angegebenen Kerzen LONG meldet."""
    def signal_fn(df, feat, i):
        return "LONG" if i in bar_indices else "NONE"
    return signal_fn


def _fixed_bracket(direction, entry_price, feat, i):
    return STOP, TARGET


def _run(bars: list[dict], signal_bars: set[int] | None = None):
    return run_backtest(
        _build_df(bars), "TEST/USD", "1h",
        signal_fn=_long_on(signal_bars or {ENTRY_SIGNAL_BAR}),
        bracket_fn=_fixed_bracket,
        fee_pct=0.0, slippage_pct=0.0,
    )


# ---------------------------------------------------------------------------
# Fall A -- sauberer Ziel-Treffer mehrere Kerzen spaeter
# ---------------------------------------------------------------------------

def test_fall_a_sauberer_ziel_treffer():
    bars = _flat_bars(ENTRY_BAR)                       # Filler bis einschliesslich Signalkerze
    bars.append({"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000.0})  # Entry-Kerze
    bars.append({"open": 100.5, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1000.0})  # nichts
    bars.append({"open": 100.0, "high": 105.0, "low": 99.5, "close": 104.5, "volume": 1000.0})  # Ziel getroffen
    bars += _flat_bars(3, 104.0)

    trades = _run(bars).closed_trades
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 100.0            # Open der Kerze NACH dem Signal
    assert t.exit_reason == "target"
    assert t.exit_price == TARGET            # Fill exakt auf dem Zielpreis
    assert t.r_multiple == pytest.approx(2.0)   # (104-100)/2


# ---------------------------------------------------------------------------
# Fall B -- sauberer Stop-Treffer
# ---------------------------------------------------------------------------

def test_fall_b_sauberer_stop_treffer():
    bars = _flat_bars(ENTRY_BAR)
    bars.append({"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1000.0})  # Entry-Kerze
    bars.append({"open": 100.0, "high": 100.5, "low": 97.0, "close": 97.5, "volume": 1000.0})   # Stop getroffen
    bars += _flat_bars(3, 97.0)

    trades = _run(bars).closed_trades
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "stop"
    assert t.exit_price == STOP
    assert t.r_multiple == pytest.approx(-1.0)  # (98-100)/2


# ---------------------------------------------------------------------------
# Fall C -- Stop UND Ziel in DERSELBEN Kerze
# Unsere Regel: konservativ, der Stop gewinnt (worst case). Diese Annahme ist
# die wichtigste Einzelentscheidung des Motors -- eine Engine mit umgekehrter
# Prioritaet sieht bei gleicher Trade-Anzahl voellig anders aus.
# ---------------------------------------------------------------------------

def test_fall_c_stop_und_ziel_in_derselben_kerze_stop_gewinnt():
    bars = _flat_bars(ENTRY_BAR)
    bars.append({"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1000.0})  # Entry-Kerze
    bars.append({"open": 100.0, "high": 105.0, "low": 97.0, "close": 101.0, "volume": 1000.0})  # BEIDES
    bars += _flat_bars(3, 101.0)

    trades = _run(bars).closed_trades
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop"
    assert trades[0].r_multiple == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Fall D -- Stop bereits auf der EINSTIEGSKERZE getroffen
# Klaert das Off-by-one im Entry-Handling: wird die Kerze, auf deren Open
# eingestiegen wurde, schon auf Stop/Ziel geprueft? In unserem Motor: JA.
# ---------------------------------------------------------------------------

def test_fall_d_stop_auf_der_einstiegskerze():
    bars = _flat_bars(ENTRY_BAR)
    # Entry auf Open=100, und dieselbe Kerze faellt bis 97 -> Stop bei 98 liegt dazwischen
    bars.append({"open": 100.0, "high": 100.2, "low": 97.0, "close": 97.5, "volume": 1000.0})
    bars += _flat_bars(3, 97.0)

    trades = _run(bars).closed_trades
    assert len(trades) == 1, "Der Motor muss die Einstiegskerze selbst schon pruefen"
    t = trades[0]
    assert t.entry_price == 100.0
    assert t.exit_reason == "stop"
    assert t.exit_ts == _build_df(bars).index[ENTRY_BAR], "Exit muss auf der Einstiegskerze liegen"
    assert t.r_multiple == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Fall E -- Exit und neues Signal auf DERSELBEN Kerze
# Darf der Motor sofort wieder einsteigen, oder muss er eine Kerze warten?
# In unserem Motor: sofortiger Wiedereinstieg ist erlaubt.
# ---------------------------------------------------------------------------

def test_fall_e_wiedereinstieg_auf_der_exit_kerze():
    exit_bar = ENTRY_BAR + 1
    bars = _flat_bars(ENTRY_BAR)
    bars.append({"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 1000.0})  # Entry-Kerze
    bars.append({"open": 100.0, "high": 100.5, "low": 97.0, "close": 98.5, "volume": 1000.0})   # Exit (Stop)
    bars.append({"open": 99.0, "high": 100.0, "low": 98.8, "close": 99.5, "volume": 1000.0})    # 2. Entry hier
    bars += _flat_bars(3, 99.0)

    # Signal sowohl auf der urspruenglichen Kerze als auch auf der Exit-Kerze
    result = _run(bars, signal_bars={ENTRY_SIGNAL_BAR, exit_bar})
    assert len(result.trades) == 2, "Nach dem Exit muss auf derselben Kerze ein neues Signal greifen duerfen"
    assert result.trades[1].entry_price == 99.0, "Zweiter Entry auf dem Open der Folgekerze"


# ---------------------------------------------------------------------------
# Zusatz: Gebuehren wirken auch ueber den Parameter, nicht nur ueber das Global
# ---------------------------------------------------------------------------

def test_fee_parameter_wirkt_und_verschlechtert_das_ergebnis():
    bars = _flat_bars(ENTRY_BAR)
    bars.append({"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000.0})
    bars.append({"open": 100.0, "high": 105.0, "low": 99.5, "close": 104.5, "volume": 1000.0})
    bars += _flat_bars(3, 104.0)

    df = _build_df(bars)
    common = dict(signal_fn=_long_on({ENTRY_SIGNAL_BAR}), bracket_fn=_fixed_bracket, slippage_pct=0.0)

    ohne = run_backtest(df, "TEST/USD", "1h", fee_pct=0.0, **common).closed_trades[0]
    mit = run_backtest(df, "TEST/USD", "1h", fee_pct=0.0026, **common).closed_trades[0]

    assert ohne.r_multiple == pytest.approx(2.0)
    # fee_drag = 0.0026 * (100 + 104) = 0.5304 Preis-Einheiten -> /2 = 0.2652 R
    assert mit.r_multiple == pytest.approx(2.0 - 0.2652)
    assert mit.r_multiple < ohne.r_multiple
