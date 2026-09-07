"""
PLAN wild-booping-firefly.md, Stufe C: Produktiv-Strategie auf freqtrade.

Herkunft: identisch zu `~/ft-crosscheck/user_data/strategies/SignalCoreStrategy.py`,
dort in Stufe B gegen unseren eigenen Motor (src/backtest.py) validiert --
304 von 304 gemeinsamen Trades exakt uebereinstimmend (Entry/Exit-Zeitpunkt,
Exit-Grund, R-Multiple-Abweichung 3.7e-5 ohne Vorzeichen-Bias, Winrate 28.3%
in beiden). Diese Kopie hier ist die, die tatsaechlich lief -- Aenderungen an
der Scoring-/ATR-Logik MUESSEN zuerst gegen die Sandbox-Kopie erneut geprueft
werden, sonst verliert die Validierung ihre Gueltigkeit.

STATUS (siehe PLAN, Gate-Abschnitt): dry_run=true in config.json, LAEUFT NICHT
parallel zum bisherigen Docker-Setup (run_loop.py/telegram_listener.py bleiben
die Produktivquelle fuer Telegram-Alerts, bis dieses System sich bewaehrt hat).
Kein Cutover ohne expliziten Beschluss.

C9 (Erklaerbarkeit) ist umgesetzt: die volle ScoreContribution-Aufschluesselung
geht ueber `explainability.save_breakdowns_bulk()` in eine eigene SQLite-
Tabelle (`user_data/score_breakdowns.sqlite3`), `enter_tag` traegt nur eine
kurze Referenz-ID ("<Paar>@<Kerzen-Zeitstempel>", siehe explainability.py).
`report_freqtrade.py` liest sie ueber `enter_tag` eines echten Trades zurueck.

WICHTIG: Diese Strategie KOPIERT die Scoring-Logik NICHT. Sie importiert die
PRODUKTIVEN Funktionen aus src/signals.py, src/structure.py, src/chart_patterns.py
direkt (sys.path-Trick unten) und ruft sie GENAU SO auf wie backtest.py es in
seinem eingebauten (nicht-injizierten) Pfad tut. Eine Kopie wuerde nur sich
selbst validieren, nicht unseren echten Motor -- siehe der Kommentar zu
signal_fn/bracket_fn in backtest.run_backtest(), gleiches Prinzip hier.

Bewusst NUR LONG (can_short=False): unsere Live-Kette kann SHORT auf einem
Spot-Markt strukturell nicht sauber ausfuehren (siehe execution.py: SHORT wird
zu einem blossen "sell", kein echtes Short) -- das ist ein offener, bereits
dokumentierter Punkt (PLAN Stufe C8), keine neue Einschraenkung. Diese Strategie
validiert deshalb nur die Seite, die live tatsaechlich schon funktioniert.

ATR-Regeln (siehe Analyse in der Konversation, gegen den echten freqtrade-
Quellcode verifiziert, nicht geraten):
  - Stop:  custom_stoploss() + use_custom_stoploss=True. Fixer Preis, nicht
    trailing -- die Ratio wird bei jedem Aufruf aus dem AKTUELLEN Kurs neu
    berechnet, rekonstruiert sich aber rechnerisch immer exakt auf denselben
    fixen Stop-Preis (adjust_stop_loss() in trade_model.py macht
    new_loss = current_price*(1-abs(ratio)), was bei
    ratio=(stop_price-current_price)/current_price IMMER stop_price ergibt).
  - Ziel:  custom_roi() + use_custom_roi=True. minimal_roi MUSS absurd hoch
    stehen (100.0): min_roi_reached_entry() in interface.py:1714 nimmt das
    MINIMUM aus minimal_roi und custom_roi, nicht das Maximum (Docstring-
    Beispiel an der Stelle ist irrefuehrend formuliert) -- ein niedriges
    minimal_roi wuerde also VOR unserem echten Ziel greifen.
  - Beide lesen den ATR-Wert der SIGNAL-Kerze (nicht der Entry-Kerze) ueber
    self.dp.get_analyzed_dataframe() + Zeitfilter < trade.open_date_utc --
    lookahead-sicher, weil freqtrade diesen Cache waehrend des Backtests
    selbst auf "bis jetzt" begrenzt (_set_dataframe_max_index in
    optimize/backtesting.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

# Konfigurierbar statt hart auf einen Host-Pfad verdrahtet: lokal (Mac) ist
# das der echte Repo-Pfad, im Docker-Container (siehe docker-compose.yml)
# wird bot/ als Volume nach /bot_src gemountet und SIGNALCORE_BOT_SRC=/bot_src
# gesetzt -- dieselbe Strategie-Datei laeuft so an beiden Orten unveraendert.
BOT_SRC = os.environ.get(
    "SIGNALCORE_BOT_SRC",
    "/Users/Lorenz/Downloads/code/Claude-Brain/projects/trading-bot/bot",
)
if BOT_SRC not in sys.path:
    sys.path.insert(0, BOT_SRC)

from src import chart_patterns  # noqa: E402
from src import explainability  # noqa: E402
from src import structure as struct  # noqa: E402
from src.signals import compute_features, score_with_breakdown  # noqa: E402

ATR_MULTIPLE = 2.0
REWARD_RISK_RATIO = 3.0
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
WARMUP_BARS = 210  # muss zu backtest.py:WARMUP_BARS passen


def _explain_db_path(strategy_self) -> str:
    """Modulfunktion statt gebundener Methode, damit sie auch funktioniert,
    wenn populate_indicators() manuell mit self=None aufgerufen wird (siehe
    Stufe-B-Validierungsmuster) -- im echten freqtrade-Betrieb liegt die
    Datei im selben user_data-Verzeichnis wie freqtrades eigene trades-DB,
    damit report_freqtrade.py beide ohne Zusatzkonfiguration findet."""
    if strategy_self is not None:
        config = getattr(strategy_self, "config", None)
        user_data_dir = config.get("user_data_dir") if config else None
        if user_data_dir:
            return str(Path(user_data_dir) / "score_breakdowns.sqlite3")
    return os.environ.get("SIGNALCORE_EXPLAIN_DB", "/tmp/signalcore_score_breakdowns.sqlite3")


class SignalCoreStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False  # siehe Modul-Docstring
    startup_candle_count = WARMUP_BARS
    process_only_new_candles = True

    # PLAN Stufe C5: ersetzt unseren eigenen (reparierten) Circuit-Breaker aus
    # Stufe A durch freqtrades erprobte Variante. CooldownPeriod verhindert
    # sofortiges Wiedereinsteigen direkt nach einem Exit; StoplossGuard pausiert
    # nach mehreren Stops in kurzer Zeit (Serie schlechter Signale); MaxDrawdown
    # ist das direkte Aequivalent zu PLAN.md §7 (-15% Grenze).
    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 2},
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 24,
            "trade_limit": 2,
            "stop_duration_candles": 12,
            "only_per_pair": False,
        },
        {
            "method": "MaxDrawdown",
            "lookback_period_candles": 48,
            "trade_limit": 5,
            "stop_duration_candles": 24,
            "max_allowed_drawdown": 0.15,
        },
    ]

    # Nur custom_roi/custom_stoploss sollen das Ziel/den Stop bestimmen.
    # minimal_roi absurd hoch, siehe Modul-Docstring (MIN statt MAX-Logik).
    minimal_roi = {"0": 100.0}
    stoploss = -0.99  # permissiver Hard-Floor, custom_stoploss uebernimmt real
    use_custom_stoploss = True
    use_custom_roi = True

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        df = dataframe
        pair = metadata.get("pair", "?")
        feat = compute_features(df)
        all_pivots = struct.find_pivots(df, left=PIVOT_LEFT, right=PIVOT_RIGHT)

        n = len(df)
        scores = [0] * n
        candidates = ["NONE"] * n
        ref_ids = [""] * n
        # PLAN Stufe C9: volle Aufschluesselung sammeln, aber nur fuer Kerzen
        # mit einem echten Kandidaten persistieren -- Tausende neutrale Score-0-
        # Zeilen braeuchte niemand, ein Trade kann ohnehin nur auf einer
        # Kandidaten-Kerze entstehen.
        breakdown_rows: list[tuple[str, str, float, int, str, list[dict]]] = []

        for i in range(WARMUP_BARS, n):
            confirmed = struct.confirmed_pivots_at(all_pivots, i, right=PIVOT_RIGHT)
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

            score, candidate, breakdown = score_with_breakdown(
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
            scores[i] = score
            candidates[i] = candidate

            if candidate != "NONE":
                ts_iso = df["date"].iloc[i].isoformat()
                ref_ids[i] = explainability.make_ref_id(pair, ts_iso)
                breakdown_rows.append((pair, ts_iso, price_i, score, candidate,
                                        [c.to_dict() for c in breakdown]))

        dataframe["score"] = scores
        dataframe["candidate"] = candidates
        dataframe["ref_id"] = ref_ids
        dataframe["atr"] = feat.atr.values

        if breakdown_rows:
            conn = explainability.connect(_explain_db_path(self))
            try:
                explainability.save_breakdowns_bulk(conn, breakdown_rows)
            finally:
                conn.close()

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        is_long = dataframe["candidate"] == "LONG"
        dataframe.loc[is_long, "enter_long"] = 1
        dataframe.loc[is_long, "enter_tag"] = dataframe.loc[is_long, "ref_id"]
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Kein Signal-Exit -- Exits laufen ausschliesslich ueber custom_stoploss/
        # custom_roi, exakt wie backtest.py (kein Pyramiding, keine
        # signalgetriebene Schliessung, siehe run_backtest()).
        return dataframe

    def _entry_atr(self, pair: str, trade: Trade) -> float | None:
        """ATR-Wert der SIGNAL-Kerze (die Kerze VOR dem Entry-Fill) -- passend zu
        backtest.py, das atr_val bei Kerze i (Signal) nimmt, waehrend der Entry
        erst auf Kerze i+1 (Open) gefuellt wird."""
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return None
        prior = df.loc[df["date"] < trade.open_date_utc]
        if prior.empty:
            return None
        atr_val = prior["atr"].iloc[-1]
        return float(atr_val) if pd.notna(atr_val) else None

    def _fixed_stop_price(self, pair: str, trade: Trade) -> float | None:
        atr_val = self._entry_atr(pair, trade)
        if atr_val is None or atr_val <= 0:
            return None
        direction = -1 if trade.is_short else 1
        return trade.open_rate - direction * ATR_MULTIPLE * atr_val

    def custom_stoploss(self, pair: str, trade: Trade, current_time, current_rate: float,
                         current_profit: float, after_fill: bool = False, **kwargs) -> float | None:
        stop_price = self._fixed_stop_price(pair, trade)
        if stop_price is None or current_rate == 0:
            return None  # Fallback: self.stoploss (Hard-Floor -0.99) greift
        ratio = (stop_price - current_rate) / current_rate
        # abs() wird von freqtrade intern ohnehin angewendet (trade_model.py
        # adjust_stop_loss) -- die Klammer hier verhindert nur einen exakten
        # Nulldurchgang (falsy-Check in interface.py), kein Vorzeichentrick noetig.
        if abs(ratio) < 1e-6:
            ratio = -1e-6
        return ratio

    def custom_roi(self, pair: str, trade: Trade, current_time, trade_duration: int,
                    entry_tag: str | None, side: str, **kwargs) -> float | None:
        """
        WICHTIG (2026-09-05, nach fehlgeschlagenem erstem Versuch gefunden):
        custom_roi() gibt KEINE rohe Preis-Ratio zurueck, sondern eine bereits
        gebuehren-bereinigte Netto-Ratio -- freqtrade loest daraus ueber
        calc_close_rate_for_roi() den Kurs, der GENAU DIESEN NETTO-Gewinn
        ergibt (_calc_base_close/_calc_open_trade_value in trade_model.py:
        open_value=amount*open_rate*(1+fee_open), close_value=amount*rate*
        (1-fee_close), ratio=close_value/open_value-1). Unser eigener
        target-Preis in backtest.py ist dagegen ein ROHER Preis-Level ohne
        Gebuehren (Gebuehren wirken dort erst auf die REALISIERTE PnL, siehe
        _close_trade()). Ohne diese Korrektur akzeptiert freqtrade unsere
        naive Ratio als NETTO-Ziel und verlangt dafuer einen HOEHEREN Kurs
        als unser eigentliches Ziel -- beobachtet als 27300.00 statt 27158.41
        bei sonst identischem Setup, Differenz exakt in der Groessenordnung
        von 2x Gebuehr. Fix: Zielpreis wie backtest.py berechnen, dann in die
        Netto-Ratio umrechnen, die REZIPROK zu obiger Formel genau diesen
        Preis ergibt.
        """
        atr_val = self._entry_atr(pair, trade)
        if atr_val is None or atr_val <= 0:
            return None
        stop_distance = ATR_MULTIPLE * atr_val
        direction = -1 if trade.is_short else 1
        target_price = trade.open_rate + direction * stop_distance * REWARD_RISK_RATIO

        fee_open = trade.fee_open or 0.0
        fee_close = trade.fee_close or 0.0
        if trade.is_short:
            # open_value=amount*open_rate*(1-fee_open), close_value=amount*rate*(1+fee_close),
            # profit_ratio=(1-close_value/open_value) -- nach rate aufgeloest:
            ratio = 1 - (target_price * (1 + fee_close)) / (trade.open_rate * (1 - fee_open))
        else:
            ratio = (target_price * (1 - fee_close)) / (trade.open_rate * (1 + fee_open)) - 1
        return ratio
