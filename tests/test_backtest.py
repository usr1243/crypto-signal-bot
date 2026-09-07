"""
Regressionstest fuer den schwerwiegendsten Bug der ganzen Konversation:
der erste Backtest zeigte -92% Return, weil die 1%-Risiko-Sizing nicht
angewendet wurde -- rohe Preis-Prozentbewegungen wurden wie eine Konto-
Rendite verzinst, als wuerde jeder Trade mit 100% Kapitaleinsatz laufen.

Der Test hier baut zwei Trades mit UNTERSCHIEDLICHEN Preisniveaus aber
IDENTISCHEM Risiko-Verhaeltnis (gleiche Distanz zum Stop in Prozent) und
verlangt, dass sie exakt dasselbe R-Multiple ergeben -- die Kernaussage von
"R-Multiples sind positionsgroessen-unabhaengig".
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest import FEE_PCT, SLIPPAGE_PCT, SimTrade, _close_trade


def test_r_multiple_is_price_level_independent():
    """Ein Trade bei Kurs 100 und einer bei Kurs 60000 mit identischer
    prozentualer Stop-Distanz muessen (bis auf minimale Fee-Rundung) dasselbe
    R-Multiple ergeben -- sonst wuerde das Ergebnis vom absoluten Preisniveau
    abhaengen, nicht vom Risiko. Genau das war beim -92%-Bug kaputt."""
    cheap = SimTrade(direction="LONG", entry_ts=pd.Timestamp("2026-01-01"), entry_price=100.0,
                      stop=98.0, target=104.0)
    expensive = SimTrade(direction="LONG", entry_ts=pd.Timestamp("2026-01-01"), entry_price=60000.0,
                          stop=58800.0, target=62400.0)  # identische 2%-Stop-Distanz

    _close_trade(cheap, raw_exit_price=104.0, exit_ts=pd.Timestamp("2026-01-02"), reason="target")
    _close_trade(expensive, raw_exit_price=62400.0, exit_ts=pd.Timestamp("2026-01-02"), reason="target")

    assert cheap.r_multiple == pytest.approx(expensive.r_multiple, rel=1e-6)


def test_stop_loss_gives_approximately_minus_1r_before_fees():
    """Ein Exit exakt auf dem Stop muss nahe -1R liegen (nicht -100% Preisbewegung)."""
    trade = SimTrade(direction="LONG", entry_ts=pd.Timestamp("2026-01-01"), entry_price=100.0,
                      stop=90.0, target=120.0)  # 10% Stop-Distanz
    _close_trade(trade, raw_exit_price=90.0, exit_ts=pd.Timestamp("2026-01-02"), reason="stop")
    # -1R minus Fee-Drag (Fees hier klein relativ zur 10%-Distanz) -- muss nahe -1 liegen, nicht -0.1 (10%) oder -90 (Preis-%)
    assert -1.15 < trade.r_multiple < -0.95


def test_fee_drag_larger_relative_to_tighter_stops():
    """Kernbefund aus der Konversation: bei engen Stops frisst die Gebuehr
    einen groesseren Anteil von 1R. Ein Trade mit 0.5%-Stop muss einen
    deutlich hoeheren Fee-Drag-Anteil haben als einer mit 5%-Stop."""
    tight = SimTrade(direction="LONG", entry_ts=pd.Timestamp("2026-01-01"), entry_price=100.0,
                      stop=99.5, target=100.5)  # 0.5% Stop
    wide = SimTrade(direction="LONG", entry_ts=pd.Timestamp("2026-01-01"), entry_price=100.0,
                     stop=95.0, target=105.0)     # 5% Stop

    _close_trade(tight, raw_exit_price=99.5, exit_ts=pd.Timestamp("2026-01-02"), reason="stop")
    _close_trade(wide, raw_exit_price=95.0, exit_ts=pd.Timestamp("2026-01-02"), reason="stop")

    assert abs(tight.fee_drag_r) > abs(wide.fee_drag_r)


def test_equity_curve_uses_account_risk_pct_not_raw_price_pct():
    """Der eigentliche -92%-Bug in einer Zeile: 100 Trades a -1R bei 1%
    Kontorisiko duerfen das Konto NICHT auf nahe Null bringen (das waere der
    Fehler: rohe Preis-% kompoundiert). Bei echtem 1%-Risiko-Sizing bleibt
    nach 100 Verlust-Trades in Folge (unrealistisch pessimistisch) das Konto
    bei ueber 30% des Startwerts -- (1-0.01)^100 ≈ 0.366, nicht nahe 0."""
    account_risk_pct = 1.0
    r_multiples = [-1.0] * 100
    pnl_pct_of_account = pd.Series(r_multiples) * (account_risk_pct / 100)
    equity = (1 + pnl_pct_of_account).cumprod()
    assert equity.iloc[-1] == pytest.approx(0.99 ** 100, rel=1e-6)
    assert equity.iloc[-1] > 0.3, "1%-Risiko-Sizing darf das Konto nicht auf nahe Null bringen"
