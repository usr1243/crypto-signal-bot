"""
Gegenprobe: unser Motor (src/backtest.py) gegen freqtrade, auf DERSELBEN Datei,
mit DERSELBEN Strategie (EMA50/EMA200-Kreuzung, Stop -2%, Ziel +4%).

Nicht Teil des Produktivcodes -- Einmal-Skript fuer den Methodenvergleich,
siehe Plan wild-booping-firefly.md Schritt 1.
"""
import json
import sys

import pandas as pd

sys.path.insert(0, ".")
from src.backtest import run_backtest, summarize  # noqa: E402

FT_DATA = "/Users/Lorenz/ft-crosscheck/user_data/data/binance/BTC_USDT-1h.json"


def load_freqtrade_json(path: str) -> pd.DataFrame:
    with open(path) as f:
        raw = json.load(f)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts").sort_index()


def signal_fn(df, feat, i):
    if i < 1:
        return "NONE"
    prev_below = feat.ema50.iloc[i - 1] <= feat.ema200.iloc[i - 1]
    now_above = feat.ema50.iloc[i] > feat.ema200.iloc[i]
    return "LONG" if (prev_below and now_above) else "NONE"


def bracket_fn(direction, entry_price, feat, i):
    return entry_price * 0.98, entry_price * 1.04  # -2% / +4%, wie freqtrades stoploss/minimal_roi


def main():
    df = load_freqtrade_json(FT_DATA)
    print(f"Kerzen: {len(df)} | {df.index[0]} .. {df.index[-1]}")

    for label, fee in [("Lauf A (fee=0)", 0.0), ("Lauf B (fee=0.0026)", 0.0026)]:
        result = run_backtest(
            df, "BTC/USDT", "1h",
            signal_fn=signal_fn, bracket_fn=bracket_fn,
            fee_pct=fee, slippage_pct=0.0,
        )
        closed = result.closed_trades
        print(f"\n=== {label} ===")
        print(f"Trades gesamt: {len(result.trades)} | geschlossen: {len(closed)} | offen: {len(result.trades) - len(closed)}")

        rows = []
        for t in closed:
            rows.append({
                "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "exit_reason": t.exit_reason, "r_multiple": t.r_multiple,
            })
        our_df = pd.DataFrame(rows)
        out = f"/tmp/our_trades_{'a' if fee == 0 else 'b'}.csv"
        our_df.to_csv(out, index=False)
        print(f"-> {out}")

        wins = sum(1 for t in closed if t.r_multiple and t.r_multiple > 0)
        print(f"Winrate: {wins}/{len(closed)} = {wins/len(closed)*100:.1f}%" if closed else "keine Trades")


if __name__ == "__main__":
    main()
