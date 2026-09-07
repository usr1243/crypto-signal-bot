"""
Freqtrade-inspiriert: Hyperopt-artige Parametersuche statt manuellem
Herumprobieren mit --atr-multiple/--rr auf der Kommandozeile (was PLAN.md
und README bisher vorgesehen hatten).

WICHTIG -- der Punkt, an dem eine Parametersuche leicht zur Selbsttaeuschung
wird: wer die "besten" Parameter auf genau den Daten sucht, an denen er sie
danach bewertet, findet garantiert etwas, das gut aussieht -- das ist reines
Data-Snooping/Overfitting, exakt die Falle aus PLAN.md Abschnitt 5. Deshalb
hier eine echte Trainings-/Kontroll-Aufteilung:

  1. TRAIN (die ersten `train_frac` der Historie): hier wird das Raster
     (ATR-Multiple x Reward:Risk) durchprobiert und nach Probabilistic
     Sharpe Ratio sortiert.
  2. TEST (der Rest, den TRAIN nie gesehen hat): die beste TRAIN-Kombination
     wird hier NOCHMAL laufen gelassen. Nur wenn sie dort auch noch
     einigermassen haelt, ist das Ergebnis mehr als Zufall/Ueberanpassung.

Wenn TEST deutlich schlechter ist als TRAIN, ist DAS das eigentliche
Ergebnis der Uebung -- nicht die TRAIN-Zahl. Wird so berichtet, nicht schoengeredet.

Aufruf:
    ./.venv/bin/python -m src.optimize --symbol BTC/USDT --timeframe 1h --days 730
"""

from __future__ import annotations

import argparse

from .backtest import WARMUP_BARS, fetch_history, run_backtest, summarize
from .adapters.crypto_ccxt import CCXTAdapter

ATR_MULTIPLES = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
REWARD_RISK_RATIOS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]  # bis 4.0 erweitert (Wunsch des Kollegen: "3x oder max 4x")


def grid_search(df, symbol, timeframe, train_frac=0.75, min_trades=15):
    split = int(len(df) * train_frac)
    train_df = df.iloc[:split]
    test_df = df.iloc[max(0, split - WARMUP_BARS):]  # Warmup-Puffer, damit EMA200 etc. am Anfang von TEST stimmen

    results = []
    for atr_m in ATR_MULTIPLES:
        for rr in REWARD_RISK_RATIOS:
            res = run_backtest(train_df, symbol, timeframe, atr_multiple=atr_m, reward_risk_ratio=rr)
            summary = summarize(res, label=f"atr={atr_m} rr={rr}")
            if summary["n_trades"] < min_trades:
                continue
            results.append((atr_m, rr, summary))

    results.sort(key=lambda r: (r[2].get("probabilistic_sharpe_ratio") or -1), reverse=True)
    return results, train_df, test_df


def print_report(results, best, test_summary, symbol, timeframe, train_bars, test_bars):
    print(f"\n{'='*72}\nPARAMETERSUCHE — {symbol} · {timeframe}\n{'='*72}")
    print(f"TRAIN: {train_bars} Kerzen · TEST (nie gesehen): {test_bars} Kerzen\n")

    if not results:
        print("Kein Parameter-Kombination hatte genug Trades (min_trades) im TRAIN-Zeitraum.")
        print("Laengeren Zeitraum oder --min-trades senken.")
        return

    print(f"{'ATR×':>6} {'R:R':>5} {'Trades':>7} {'Winrate':>8} {'PF':>6} {'PSR':>6} {'Return':>8}")
    for atr_m, rr, s in results[:8]:
        print(f"{atr_m:>6.1f} {rr:>5.1f} {s['n_trades']:>7} {s['win_rate_pct']:>7.1f}% "
              f"{s['profit_factor']:>6} {s['probabilistic_sharpe_ratio']:>6} {s['total_return_pct']:>+7.2f}%")

    best_atr, best_rr, best_train_summary = best
    print(f"\nBeste TRAIN-Kombination: {best_atr}×ATR, {best_rr}:1 R:R "
          f"(PSR {best_train_summary['probabilistic_sharpe_ratio']}, PF {best_train_summary['profit_factor']})")

    print(f"\n-- Bestaetigung auf TEST-Zeitraum (diese Kombination hat diese Daten NIE gesehen) --")
    if test_summary["n_trades"] == 0:
        print("  Keine Trades im TEST-Zeitraum -- keine Aussage moeglich.")
    else:
        print(f"  Trades: {test_summary['n_trades']} · Winrate {test_summary['win_rate_pct']}% · "
              f"PF {test_summary['profit_factor']} · PSR {test_summary['probabilistic_sharpe_ratio']} · "
              f"Return {test_summary['total_return_pct']:+.2f}%")

    print(f"\n{'='*72}")
    print("EINORDNUNG:")
    train_pf = best_train_summary.get("profit_factor") or 0
    test_pf = test_summary.get("profit_factor") or 0
    if test_summary["n_trades"] < 10:
        print("  Zu wenige TEST-Trades fuer eine Aussage. Laengeren Gesamtzeitraum verwenden.")
    elif test_pf >= train_pf * 0.7 and test_pf > 1.0:
        print("  Die TRAIN-Parameter halten im TEST-Zeitraum groessenordnungsmaessig -- kein")
        print("  Beweis fuer echten Edge, aber auch kein Anzeichen fuer reines Ueberanpassen.")
    else:
        print("  Die TRAIN-Parameter wirken im TEST-Zeitraum deutlich schwaecher (oder halten gar nicht).")
        print("  Das ist der erwartbare Fall bei einem simplen, additiven Scoring ohne echten Edge --")
        print("  siehe PLAN.md §9. Die 'beste' Kombination oben ist wahrscheinlich Ueberanpassung an")
        print("  Zufallsmuster im TRAIN-Zeitraum, keine echte Verbesserung.")
    print(f"{'='*72}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Freqtrade-inspirierte Parametersuche mit Train/Test-Split")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--train-frac", type=float, default=0.75)
    parser.add_argument("--min-trades", type=int, default=15)
    args = parser.parse_args()

    adapter = CCXTAdapter(exchange_id=args.exchange)
    print(f"Lade Historie: {args.symbol} {args.timeframe}, {args.days} Tage ...")
    df = fetch_history(adapter, args.symbol, args.timeframe, args.days)
    print(f"  {len(df)} Kerzen geladen")

    if len(df) < WARMUP_BARS + 100:
        print("  Zu wenig Historie fuer eine sinnvolle Train/Test-Aufteilung. Abbruch.")
        return

    results, train_df, test_df = grid_search(df, args.symbol, args.timeframe, args.train_frac, args.min_trades)
    if not results:
        print_report(results, None, {}, args.symbol, args.timeframe, len(train_df), len(test_df))
        return

    best = results[0]
    test_res = run_backtest(test_df, args.symbol, args.timeframe, atr_multiple=best[0], reward_risk_ratio=best[1])
    test_summary = summarize(test_res)

    print_report(results, best, test_summary, args.symbol, args.timeframe, len(train_df), len(test_df))


if __name__ == "__main__":
    main()
