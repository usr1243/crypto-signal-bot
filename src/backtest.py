"""
Stufe 2: der ehrliche Backtest -- PLAN.md nennt das "die entscheidende Zahl".

Lauft die exakt gleiche Scoring-Logik wie main.py (signals.py:
compute_features + score_and_candidate) Kerze fuer Kerze durch die
Historie, mit echten Gebuehren + Slippage, ohne Look-ahead-Bias:

  * Kausale Indikatoren (EMA/RSI/MACD/BB/ATR/ADX/OBV) werden einmal ueber
    die volle Historie berechnet -- das ist sicher, siehe signals.py-Docstring.
  * Pivots (Struktur/Divergenz) werden einmal fuer die volle Historie
    gefunden, aber bei Kerze i nur die Pivots verwendet, die bis Kerze i
    bereits bestaetigt waren (structure.confirmed_pivots_at). Ohne das
    wuerde der Backtest bei Kerze i heimlich schon wissen, was danach
    passiert ist -- genau der Fehler, vor dem PLAN.md Abschnitt 5 warnt.
  * Ein Trade wird ERST auf der naechsten Kerze nach dem Signal eroeffnet
    (kein "Kauf zum exakt gleichen Schlusskurs, der das Signal ausgeloest
    hat" -- das waere ein weiterer Look-ahead-Fehler).
  * Gebuehren + Slippage werden vom Kraken-Taker-Fee-Niveau abgeleitet
    (siehe FEE_PCT/SLIPPAGE_PCT) und auf Entry UND Exit angewendet.

Aufruf:
    ./.venv/bin/python -m src.backtest --symbol BTC/USD --timeframe 1h --days 730
    ./.venv/bin/python -m src.backtest --symbol ETH/USD --timeframe 4h --days 730
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import chart_patterns
from . import structure as struct
from .adapters.crypto_ccxt import CCXTAdapter
from .signals import Features, compute_features, score_and_candidate

BASE_DIR = Path(__file__).resolve().parent.parent

FEE_PCT = 0.0026       # Kraken Taker-Fee ~0.26%, konservativ (Maker waere guenstiger)
SLIPPAGE_PCT = 0.0005  # 0.05% -- konservative Annahme fuer liquide Paare, kein HFT-Setup
WARMUP_BARS = 210      # EMA200 + etwas Puffer, bevor die erste Kerze gewertet wird


def fetch_history(adapter: CCXTAdapter, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """
    Paginierter Abruf via ccxt.

    WICHTIG: Krakens oeffentlicher OHLC-Endpunkt ignoriert `since` faktisch
    und liefert immer nur die letzten ~720 Kerzen, egal wie weit `since`
    zurueckliegt (getestet, kein ccxt-Bug -- Kraken-API-Eigenheit). Fuer
    echte Tiefen-Historie deshalb Binance als History-Quelle verwenden
    (siehe `--exchange` Default in main() dieser Datei) -- das ist
    unabhaengig davon, welche Boerse main.py fuer Live-Signale nutzt.
    """
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(timeframe)
    if tf_minutes is None:
        raise ValueError(f"Unbekannte Timeframe '{timeframe}' fuer History-Fetch")

    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    all_rows: list[list] = []
    exchange = adapter.exchange

    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        next_since = last_ts + tf_minutes * 60 * 1000
        if next_since <= since or len(batch) < 2:
            break
        since = next_since
        if len(all_rows) > 20000:  # Sicherheitsnetz gegen Endlosschleifen
            break

    df = pd.DataFrame(all_rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="ts").sort_values("ts")
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


@dataclass
class SimTrade:
    direction: str
    entry_ts: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "stop" | "target" | "eod"
    r_multiple: float | None = None  # PnL in Vielfachen des Anfangsrisikos, nach Gebuehren+Slippage
    fee_drag_r: float | None = None  # wie viel von 1R allein durch Gebuehren verloren geht


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    bars: int
    trades: list[SimTrade] = field(default_factory=list)

    @property
    def closed_trades(self) -> list[SimTrade]:
        return [t for t in self.trades if t.exit_price is not None]


def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    atr_multiple: float = 2.0,
    reward_risk_ratio: float = 2.0,
    pivot_left: int = 3,
    pivot_right: int = 3,
    signal_fn=None,
    bracket_fn=None,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
) -> BacktestResult:
    """
    HINWEIS zu `account_risk_pct`: der Parameter stand frueher hier in der
    Signatur, wurde im Rumpf aber NIE benutzt -- die Signatur hat gelogen.
    Das Risiko-Sizing wirkt ausschliesslich in summarize(), weil r_multiple
    per Konstruktion positionsgroessen-unabhaengig ist. Parameter entfernt,
    statt ihn wirkungslos stehen zu lassen.

    `signal_fn` / `bracket_fn` sind Injektionspunkte fuer die Motor-Gegenprobe
    gegen freqtrade (siehe crosscheck/). Default None = unveraendertes
    Verhalten. Zweck: der Vergleichslauf muss durch GENAU DIESE Schleife
    gehen -- wer die Logik ins Vergleichsskript kopiert, validiert die Kopie
    statt den Motor.
      signal_fn(df, feat, i)                      -> "LONG" | "SHORT" | "NONE"
      bracket_fn(direction, entry_price, feat, i) -> (stop, target)

    `fee_pct`/`slippage_pct`: frueher nur Modul-Globals, die _close_trade zur
    Laufzeit las. Ein Global, das das Ergebnis bestimmt, ist eine Zeitbombe --
    jetzt pro Lauf setzbar (None = Modul-Default).
    """
    fee = FEE_PCT if fee_pct is None else fee_pct
    slip = SLIPPAGE_PCT if slippage_pct is None else slippage_pct

    feat: Features = compute_features(df)
    # Pivots sind nur fuer den eingebauten Scoring-Pfad noetig. Mit injiziertem
    # signal_fn waere das reine Rechenzeit-Verschwendung (Gegenprobe laeuft
    # ueber 17'000 Kerzen).
    all_pivots = struct.find_pivots(df, left=pivot_left, right=pivot_right) if signal_fn is None else []

    result = BacktestResult(symbol=symbol, timeframe=timeframe, bars=len(df))
    open_trade: SimTrade | None = None

    n = len(df)
    for i in range(WARMUP_BARS, n):
        # --- offene Position pruefen: Stop/Ziel auf DIESER Kerze getroffen? ---
        if open_trade is not None:
            bar_high, bar_low = float(df["high"].iloc[i]), float(df["low"].iloc[i])
            hit_stop = (
                bar_low <= open_trade.stop if open_trade.direction == "LONG" else bar_high >= open_trade.stop
            )
            hit_target = (
                bar_high >= open_trade.target if open_trade.direction == "LONG" else bar_low <= open_trade.target
            )
            # konservativ: wenn beides in derselben Kerze passiert, zaehlt der Stop (worst case)
            if hit_stop:
                _close_trade(open_trade, open_trade.stop, df.index[i], "stop", fee=fee, slippage=slip)
                open_trade = None
            elif hit_target:
                _close_trade(open_trade, open_trade.target, df.index[i], "target", fee=fee, slippage=slip)
                open_trade = None

        if open_trade is not None:
            continue  # solange eine Position offen ist, kein neues Signal (kein Pyramiding im Erstentwurf)

        # --- neues Signal auf Kerze i pruefen ---
        if signal_fn is not None:
            # Gegenprobe-Pfad: der teure Scoring-Block wird komplett uebersprungen,
            # alles danach (Entry, Exit, Fees, R-Rechnung) bleibt identisch.
            candidate = signal_fn(df, feat, i)
            if candidate == "NONE" or i + 1 >= n:
                continue
            entry_price = float(df["open"].iloc[i + 1])
            if bracket_fn is not None:
                stop, target = bracket_fn(candidate, entry_price, feat, i)
            else:
                direction_sign = 1 if candidate == "LONG" else -1
                atr_abs = float(feat.atr.iloc[i])
                stop = entry_price - direction_sign * atr_multiple * atr_abs
                target = entry_price + direction_sign * abs(entry_price - stop) * reward_risk_ratio
            open_trade = SimTrade(
                direction=candidate, entry_ts=df.index[i + 1], entry_price=entry_price,
                stop=stop, target=target,
            )
            result.trades.append(open_trade)
            continue

        confirmed = struct.confirmed_pivots_at(all_pivots, i, right=pivot_right)
        pattern_info = struct.classify_pattern(confirmed, lookback_pivots=4)
        divergence = struct.divergence_from_pivots(confirmed, feat.rsi)

        trend_regime = "up" if feat.ema50.iloc[i] > feat.ema200.iloc[i] else "down"
        price_i = float(df["close"].iloc[i])
        atr_val_i = float(feat.atr.iloc[i])
        swing_high = pattern_info["last_swing_high"]
        swing_low = pattern_info["last_swing_low"]
        near_breakout = bool(swing_high and price_i >= swing_high * 0.999)
        near_breakdown = bool(swing_low and price_i <= swing_low * 1.001)

        sar_i = float(feat.sar.iloc[i]) if pd.notna(feat.sar.iloc[i]) else None
        vwap_i = float(feat.vwap.iloc[i]) if pd.notna(feat.vwap.iloc[i]) else None

        chart_pattern_info = struct.detect_chart_pattern(confirmed, atr_val_i, price_i)
        if not chart_pattern_info["confirmed"]:
            chart_pattern_info = chart_patterns.detect_wide_pattern(df, i, atr_val_i)

        score, candidate = score_and_candidate(
            trend_regime, float(feat.adx.iloc[i]), float(feat.macd_hist.iloc[i]),
            float(feat.rsi.iloc[i]), float(feat.vol_z.iloc[i]),
            divergence, pattern_info["pattern"], near_breakout, near_breakdown,
            stoch_k_val=float(feat.stoch_k.iloc[i]) if pd.notna(feat.stoch_k.iloc[i]) else 50.0,
            cci_val=float(feat.cci.iloc[i]) if pd.notna(feat.cci.iloc[i]) else 0.0,
            williams_r_val=float(feat.williams_r.iloc[i]) if pd.notna(feat.williams_r.iloc[i]) else -50.0,
            price_above_sar=(price_i > sar_i) if sar_i is not None else None,
            price_above_vwap=(price_i > vwap_i) if vwap_i is not None else None,
            candle_pattern=str(feat.candle_pattern.iloc[i]),
            chart_pattern=chart_pattern_info["pattern"] if chart_pattern_info["confirmed"] else "none",
        )
        if candidate == "NONE" or i + 1 >= n:
            continue

        # Entry auf der NAECHSTEN Kerze (Open), nicht auf der Signal-Kerze selbst -- kein Look-ahead.
        entry_price = float(df["open"].iloc[i + 1])
        atr_abs = float(feat.atr.iloc[i])
        direction_sign = 1 if candidate == "LONG" else -1
        stop = entry_price - direction_sign * atr_multiple * atr_abs
        target = entry_price + direction_sign * abs(entry_price - stop) * reward_risk_ratio

        open_trade = SimTrade(
            direction=candidate, entry_ts=df.index[i + 1], entry_price=entry_price,
            stop=stop, target=target,
        )
        result.trades.append(open_trade)

    return result


def _close_trade(trade: SimTrade, raw_exit_price: float, exit_ts: pd.Timestamp, reason: str,
                  fee: float | None = None, slippage: float | None = None) -> None:
    """
    PnL in R-Multiples (Vielfache des Anfangsrisikos), nicht in rohem
    Preis-Prozent. Wichtig: die Positionsgroesse ist ueber die
    1-%-Risiko-Regel so bemessen, dass ein Stop-Loss genau ein R
    (= `account_risk_pct` des Kontos, angewendet in summarize()) kostet --
    nicht die volle Preisbewegung in Prozent. Wuerde man rohe Preis-% wie
    eine Konto-Rendite verzinsen, waere das so, als setze der Bot bei jedem
    Trade das gesamte Konto ungehebelt ein. Das ist NICHT das im Plan
    beschriebene Risikomodell und wuerde Drawdown/Return massiv verzerren.

    fee/slippage: None = Modul-Default (FEE_PCT/SLIPPAGE_PCT). Explizit
    setzbar fuer die Gegenprobe gegen freqtrade, das keine Slippage
    modelliert (dort: slippage=0).
    """
    fee = FEE_PCT if fee is None else fee
    slippage = SLIPPAGE_PCT if slippage is None else slippage
    direction_sign = 1 if trade.direction == "LONG" else -1

    # Slippage: Entry etwas schlechter, Exit etwas schlechter -- realistisch, nie zugunsten des Bots.
    entry_eff = trade.entry_price * (1 + direction_sign * slippage)
    exit_eff = raw_exit_price * (1 - direction_sign * slippage)

    gross_price_pnl = direction_sign * (exit_eff - entry_eff)
    fee_drag = fee * (trade.entry_price + raw_exit_price)  # Fee je Seite, auf Notional beider Legs
    net_price_pnl = gross_price_pnl - fee_drag

    initial_risk_distance = abs(trade.entry_price - trade.stop)
    r_multiple = net_price_pnl / initial_risk_distance if initial_risk_distance > 0 else 0.0
    fee_drag_r = fee_drag / initial_risk_distance if initial_risk_distance > 0 else 0.0

    trade.exit_price = raw_exit_price
    trade.exit_ts = exit_ts
    trade.exit_reason = reason
    trade.r_multiple = r_multiple
    trade.fee_drag_r = fee_drag_r


# ---------------------------------------------------------------------------
# Auswertung: Walk-Forward-Split + Kennzahlen inkl. Probabilistic Sharpe Ratio
# ---------------------------------------------------------------------------

def walk_forward_windows(df: pd.DataFrame, n_windows: int = 4) -> list[tuple[pd.DataFrame, str]]:
    """Teilt die Historie in n_windows aufeinanderfolgende, nicht ueberlappende Abschnitte.

    Kein Parameter-Fitting hier (das Scoring ist fix, siehe PLAN.md "additiv,
    keine Gewichtsoptimierung") -- der Sinn ist, zu sehen, ob das Ergebnis
    ueber verschiedene Marktphasen stabil ist oder nur in einem einzigen
    guenstigen Fenster funktioniert (Data-Snooping-Warnsignal).
    """
    n = len(df)
    size = n // n_windows
    windows = []
    for w in range(n_windows):
        start = w * size
        end = n if w == n_windows - 1 else (w + 1) * size
        sub = df.iloc[max(0, start - WARMUP_BARS) : end]  # Warmup-Puffer vor jedem Fenster

        # Das Label muss den ERSTEN TATSAECHLICH GEHANDELTEN Zeitpunkt zeigen, nicht
        # den Fensteranfang. Fenster 0 hat keinen Warmup-Puffer davor (max(0, ...)),
        # deshalb handelt run_backtest() dort erst ab Bar WARMUP_BARS von `sub` --
        # das Label behauptete aber den Fensteranfang. Fuer Fenster 1+ faellt beides
        # zusammen; nur Fenster 0 war falsch beschriftet.
        first_traded = sub.index[WARMUP_BARS] if len(sub) > WARMUP_BARS else sub.index[-1]
        label = f"{first_traded.date()} .. {df.index[end - 1].date()}"
        windows.append((sub, label))
    return windows


def probabilistic_sharpe_ratio(returns: pd.Series, benchmark_sr: float = 0.0) -> float | None:
    """
    Lopez de Prado's PSR: Wahrscheinlichkeit, dass die wahre Sharpe Ratio
    ueber `benchmark_sr` liegt, unter Beruecksichtigung von Skew/Kurtosis
    und Stichprobengroesse. PLAN.md Stufe 2 nennt das explizit als Pruefung,
    ob ein gemessener Sharpe real oder Rauschen ist.
    """
    n = len(returns)
    if n < 10 or returns.std(ddof=1) == 0:
        return None
    sr = returns.mean() / returns.std(ddof=1)
    skew = returns.skew()
    kurt = returns.kurtosis() + 3  # pandas liefert Excess-Kurtosis, PSR-Formel braucht die "normale"

    numerator = (sr - benchmark_sr) * math.sqrt(n - 1)
    denominator = math.sqrt(max(1e-12, 1 - skew * sr + ((kurt - 1) / 4) * sr**2))
    z = numerator / denominator
    return float(0.5 * (1 + math.erf(z / math.sqrt(2))))  # Standardnormal-CDF ohne scipy-Abhaengigkeit


def summarize(result: BacktestResult, label: str = "", account_risk_pct: float = 1.0) -> dict:
    """
    account_risk_pct: wie viel Prozent des Kontos ein einzelner Stop-Loss
    kostet (1 R = account_risk_pct % Konto) -- muss zum Sizing aus
    signals.build_trade_proposal() passen, Default dort ist ebenfalls 1.0.
    """
    closed = result.closed_trades
    if not closed:
        return {"label": label, "n_trades": 0, "note": "keine abgeschlossenen Trades in diesem Fenster"}

    r = pd.Series([t.r_multiple for t in closed])
    pnl_pct_of_account = r * (account_risk_pct / 100)  # fraktional, fuer Equity-Kurve
    wins = r[r > 0]
    losses = r[r <= 0]

    equity = (1 + pnl_pct_of_account).cumprod()
    running_max = equity.cummax()
    drawdown = (equity / running_max) - 1
    max_dd = float(drawdown.min())

    total_return = float(equity.iloc[-1] - 1)
    win_rate = float(len(wins) / len(r))
    avg_win_r = float(wins.mean()) if len(wins) else 0.0
    avg_loss_r = float(losses.mean()) if len(losses) else 0.0
    profit_factor = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    sharpe_raw = float(r.mean() / r.std(ddof=1)) if r.std(ddof=1) > 0 else 0.0
    psr = probabilistic_sharpe_ratio(r)
    avg_fee_drag_r = float(pd.Series([t.fee_drag_r for t in closed]).mean())

    return {
        "label": label,
        "n_trades": len(closed),
        "win_rate_pct": round(win_rate * 100, 1),
        "total_return_pct": round(total_return * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "avg_fee_drag_r": round(avg_fee_drag_r, 2),
        "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else None,
        "sharpe_per_trade_raw": round(sharpe_raw, 3),
        "probabilistic_sharpe_ratio": round(psr, 3) if psr is not None else None,
    }


def print_report(windows_summary: list[dict], full_summary: dict, symbol: str, timeframe: str) -> None:
    print(f"\n{'='*70}\nBACKTEST-REPORT — {symbol} · {timeframe}\n{'='*70}")
    print(f"Gebuehren: {FEE_PCT*100:.2f}% je Seite · Slippage: {SLIPPAGE_PCT*100:.2f}% je Seite\n")

    print("-- Walk-Forward (nicht ueberlappende Fenster) --")
    for w in windows_summary:
        if w["n_trades"] == 0:
            print(f"  [{w['label']}] keine Trades")
            continue
        print(
            f"  [{w['label']}] {w['n_trades']:>3} Trades · "
            f"Winrate {w['win_rate_pct']:>5.1f}% · "
            f"Return {w['total_return_pct']:>+7.2f}% · "
            f"MaxDD {w['max_drawdown_pct']:>+6.2f}% · "
            f"PF {w['profit_factor']}"
        )

    print("\n-- Gesamtzeitraum --")
    if full_summary["n_trades"] == 0:
        print("  Keine Trades ausgeloest -- die Scoring-Schwelle (±40) wurde nie erreicht.")
    else:
        print(f"  Trades:              {full_summary['n_trades']}")
        print(f"  Winrate:             {full_summary['win_rate_pct']}%")
        print(f"  Gesamt-Return:       {full_summary['total_return_pct']}%  (Trades hintereinander, kein Compounding ueber Zeit-Overlap)")
        print(f"  Max Drawdown:        {full_summary['max_drawdown_pct']}%")
        print(f"  Profit Factor:       {full_summary['profit_factor']}")
        print(f"  Sharpe (pro Trade):  {full_summary['sharpe_per_trade_raw']}")
        psr = full_summary["probabilistic_sharpe_ratio"]
        print(f"  Probabilistic SR:    {psr}  ({'>50% -> vermutlich echt' if psr and psr > 0.5 else 'unter/um 50% -> vermutlich Rauschen'})")
        print(f"  Ø Gewinn / Verlust:  +{full_summary['avg_win_r']}R / {full_summary['avg_loss_r']}R")
        fee_drag = full_summary["avg_fee_drag_r"]
        print(f"  Ø Fee-Drag:          {fee_drag}R  (Anteil von 1R, den allein die Gebuehr auffrisst)")
        if fee_drag > 0.3:
            print(f"  -> Der 2×ATR-Stop ist bei dieser Vola/diesem Fee-Level SEHR eng: die Gebuehr")
            print(f"     allein kostet {fee_drag*100:.0f}% eines Risikoeinheit-Multiples. Das ist unabhaengig")
            print(f"     vom Scoring ein struktureller Nachteil -- siehe Einordnung unten.")

    print(f"\n{'='*70}")
    print("EINORDNUNG (PLAN.md §9, Abbruchkriterium):")
    if full_summary["n_trades"] < 20:
        print("  Zu wenige Trades fuer eine belastbare Aussage. Laengeren Zeitraum testen")
        print("  oder mehr Symbole/Timeframes kombinieren, bevor irgendeine Schlussfolgerung gezogen wird.")
    elif full_summary.get("avg_fee_drag_r", 0) > 0.3:
        print("  Ein grosser Teil des Verlusts ist NICHT das Scoring, sondern die Stop-Distanz:")
        print("  bei 2×ATR-Stops auf dieser Zeitebene frisst die Taker-Gebuehr (0.26%/Seite) einen")
        print("  substanziellen Teil von 1R. Vor einer Bewertung des Scorings selbst: groesseren")
        print("  ATR-Multiple (z.B. 3-4x) oder groebere Zeitebene (4h/1d) testen, wo der Stop relativ")
        print("  zur Gebuehr weiter weg liegt. Erst DANACH ist ein Urteil ueber die Scoring-Regeln fair.")
    elif full_summary.get("probabilistic_sharpe_ratio") and full_summary["probabilistic_sharpe_ratio"] > 0.5 and full_summary["total_return_pct"] > 0:
        print("  Score-Regeln zeigen in diesem Test einen Vorteil nach Kosten. Das ist kein")
        print("  Freibrief fuer Live-Geld -- Out-of-Sample auf weiteren Symbolen/Zeitraeumen")
        print("  pruefen, dann erst Stufe 4 (Paper-Trading).")
    else:
        print("  Kein robuster Vorteil nach Kosten erkennbar. Laut PLAN.md §9: Schicht B")
        print("  (Makro/LLM) wird das nicht retten -- entweder Scoring-Regeln neu denken")
        print("  oder das Projektziel ehrlich auf ein Makro-Briefing-Tool umstellen.")
    print(f"{'='*70}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stufe 2 -- ehrlicher Backtest von Schicht A")
    parser.add_argument("--symbol", default="BTC/USDT",
                        help="Default passt zu --exchange binance. Fuer Kraken-Symbole z.B. BTC/USD verwenden.")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--exchange", default="binance",
                        help="History-Quelle. Binance liefert echte Tiefen-Historie; Kraken deckelt "
                             "den oeffentlichen OHLC-Endpunkt auf ~720 Kerzen, egal wie weit --days zurueckgeht.")
    parser.add_argument("--days", type=int, default=730, help="Historie in Tagen (Default 2 Jahre)")
    parser.add_argument("--windows", type=int, default=4, help="Anzahl Walk-Forward-Fenster")
    parser.add_argument("--atr-multiple", type=float, default=2.0, help="Stop-Distanz in ATR-Vielfachen")
    parser.add_argument("--rr", type=float, default=2.0, help="Reward:Risk-Verhaeltnis fuer das Ziel")
    parser.add_argument("--account-risk-pct", type=float, default=1.0, help="Risiko pro Trade in % des Kontos")
    args = parser.parse_args()

    adapter = CCXTAdapter(exchange_id=args.exchange)
    print(f"Lade Historie: {args.symbol} {args.timeframe}, {args.days} Tage ...")
    df = fetch_history(adapter, args.symbol, args.timeframe, args.days)
    print(f"  {len(df)} Kerzen geladen ({df.index[0]} .. {df.index[-1]})")
    print(f"  Stop {args.atr_multiple}xATR · Ziel {args.rr}:1 R:R · Risiko/Trade {args.account_risk_pct}%")

    if len(df) < WARMUP_BARS + 50:
        print(f"  Zu wenig Historie fuer einen sinnvollen Test (< {WARMUP_BARS + 50} Kerzen). Abbruch.")
        return

    windows_summary = []
    for sub_df, label in walk_forward_windows(df, n_windows=args.windows):
        res = run_backtest(sub_df, args.symbol, args.timeframe, atr_multiple=args.atr_multiple, reward_risk_ratio=args.rr)
        windows_summary.append(summarize(res, label=label, account_risk_pct=args.account_risk_pct))

    full_result = run_backtest(df, args.symbol, args.timeframe, atr_multiple=args.atr_multiple, reward_risk_ratio=args.rr)
    full_summary = summarize(full_result, label="gesamt", account_risk_pct=args.account_risk_pct)

    print_report(windows_summary, full_summary, args.symbol, args.timeframe)


if __name__ == "__main__":
    main()
