"""
Schicht A: verdichtet Indikatoren zu einem Signal-Kandidaten.

Bewusst simpel gehalten (Erstentwurf, siehe PLAN.md Stufe 1) --
ein additives Scoring, keine Gewichtsoptimierung. Kein LLM in dieser Datei.

Architektur-Hinweis (wichtig fuer Stufe 2 / backtest.py):
`compute_features()` und `score_and_candidate()` sind bewusst von
`build_technical_signal()` (Live-Pfad: immer die *letzte* Kerze) getrennt.
Der Backtest ruft dieselben zwei Funktionen fuer *jede* historische Kerze
auf -- Live und Backtest laufen damit garantiert durch denselben Code,
nie durch eine zweite, leicht abweichende Kopie der Scoring-Regeln.
Genau das verhindert die "Backtest testet etwas anderes als live laeuft"-
Falle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from . import candles
from . import chart_patterns
from . import indicators as ind
from . import structure as struct


@dataclass
class Features:
    """Vollstaendige, kausale Indikator-Serien ueber den ganzen DataFrame.

    Kausal = jeder Wert bei Index i haengt nur von Daten <= i ab (ewm/rolling).
    Deshalb ist es sicher, das EINMAL ueber die volle Historie zu rechnen,
    auch fuer den Backtest -- kein Look-ahead-Risiko in dieser Klasse.
    Das einzige Look-ahead-Risiko im ganzen Modul sind Pivots (structure.py),
    die extra behandelt werden (siehe confirmed_pivots_at).
    """

    ema50: pd.Series
    ema200: pd.Series
    adx: pd.Series
    rsi: pd.Series
    macd_hist: pd.Series
    bb_percent_b: pd.Series
    bb_width: pd.Series
    atr: pd.Series
    vol_z: pd.Series
    obv: pd.Series
    # Erweiterung auf Wunsch des Kollegen ("mehr davon wie RSI/MACD") -- weitere
    # gaengige Chart-Werkzeuge, siehe indicators.py und candles.py.
    stoch_k: pd.Series
    cci: pd.Series
    williams_r: pd.Series
    sar: pd.Series
    vwap: pd.Series
    candle_pattern: pd.Series


def compute_features(df: pd.DataFrame) -> Features:
    close = df["close"]
    macd_df = ind.macd(close)
    bb = ind.bollinger(close, 20)
    stoch = ind.stochastic(df, 14, 3)
    return Features(
        ema50=ind.ema(close, 50),
        ema200=ind.ema(close, 200),
        adx=ind.adx(df, 14),
        rsi=ind.rsi(close, 14),
        macd_hist=macd_df["hist"],
        bb_percent_b=bb["percent_b"],
        bb_width=bb["width"],
        atr=ind.atr(df, 14),
        vol_z=ind.volume_zscore(df["volume"], 20),
        obv=ind.obv(df),
        stoch_k=stoch["k"],
        cci=ind.cci(df, 20),
        williams_r=ind.williams_r(df, 14),
        sar=ind.parabolic_sar(df),
        vwap=ind.rolling_vwap(df, 20),
        candle_pattern=candles.detect_patterns(df),
    )


@dataclass
class ScoreContribution:
    """Ein einzelner Beitrag zum Gesamtscore -- welche Regel, wie viele Punkte, warum.

    Existiert, weil der Bot sonst eine Blackbox ist: man sah nur "Score 23/100",
    aber nicht, welche der ~20 Regeln das verursacht hat. Wird in der DB
    mitgespeichert, damit eine Entscheidung auch Monate spaeter noch erklaerbar
    ist -- und zwar aus den Daten selbst, nicht durch Nachbauen der Formel.
    """

    rule: str      # Kurzname der Regel, z.B. "trend", "macd", "chart_pattern"
    points: int    # Beitrag zum Score, z.B. +20, -15
    detail: str    # Klartext-Begruendung, fuer Menschen lesbar

    def to_dict(self) -> dict:
        return {"rule": self.rule, "points": self.points, "detail": self.detail}


def score_with_breakdown(
    trend_regime: str,
    adx_val: float,
    macd_hist_val: float,
    rsi_val: float,
    vol_z_val: float,
    divergence: str,
    pattern: str,
    near_breakout: bool,
    near_breakdown: bool = False,
    stoch_k_val: float = 50.0,
    cci_val: float = 0.0,
    williams_r_val: float = -50.0,
    price_above_sar: bool | None = None,
    price_above_vwap: bool | None = None,
    candle_pattern: str = "none",
    chart_pattern: str = "none",
) -> tuple[int, str, list[ScoreContribution]]:
    """
    Die additive Scoring-Formel -- einzige Quelle der Wahrheit, live wie im Backtest.
    Liefert zusaetzlich die Aufschluesselung, welche Regel wie viel beigetragen hat.

    Die Parameter Stochastik, CCI, Williams %R, SAR, VWAP und Kerzenmuster sind
    auf Wunsch des Kollegen dazugekommen ("mehr davon wie RSI/MACD") -- siehe
    indicators.py und candles.py. Bewusst kleiner gewichtet (5-10 statt 15-20)
    als die urspruenglichen Kernregeln: mehr Signale bedeuten nicht automatisch
    mehr Aussagekraft, und mehrere davon (Stochastik/Williams %R) messen
    aehnliches wie RSI -- sie sollen den Score verfeinern, nicht dominieren.
    Default-Werte sind neutral, falls ein Aufrufer sie nicht liefert.
    """
    parts: list[ScoreContribution] = []

    def add(rule: str, points: int, detail: str) -> None:
        if points:
            parts.append(ScoreContribution(rule, points, detail))

    if trend_regime == "up":
        add("trend", 20, "EMA50 über EMA200 (Aufwärtstrend)")
    else:
        add("trend", -20, "EMA50 unter EMA200 (Abwärtstrend)")

    if adx_val > 20:
        add("adx", 10 if trend_regime == "up" else -10,
            f"ADX {adx_val:.1f} > 20 — Trend hat Kraft, verstärkt die Trendrichtung")

    if macd_hist_val > 0:
        add("macd", 15, "MACD-Histogramm positiv (Momentum aufwärts)")
    else:
        add("macd", -15, "MACD-Histogramm negativ (Momentum abwärts)")

    # Band 2026-09-07 von 45-65 auf 40-60 zentriert und richtungsabhaengig gemacht:
    # 45-65 lag um 55 statt um den RSI-Neutralpunkt 50, dadurch bekam z.B. RSI 64
    # +10, sein Spiegelbild 36 aber 0 (siehe tests/test_score_symmetry.py). Ein
    # "gesunder" RSI bestaetigt ausserdem die vorherrschende Richtung -- er ist
    # kein pauschales Long-Argument.
    if 40 <= rsi_val <= 60:
        add("rsi", 10 if trend_regime == "up" else -10,
            f"RSI {rsi_val:.1f} im gesunden Bereich (40-60), bestätigt den Trend")
    elif rsi_val > 70:
        add("rsi", -10, f"RSI {rsi_val:.1f} überkauft (>70)")
    elif rsi_val < 30:
        add("rsi", 10, f"RSI {rsi_val:.1f} überverkauft (<30) — möglicher Reversal nach oben")

    # 2026-09-07 richtungsabhaengig gemacht: hohes Volumen BESTAETIGT die laufende
    # Bewegung -- im Abwaertstrend spricht es also gegen einen Long, nicht dafuer.
    # Vorher gab es pauschal +10, unabhaengig von der Richtung.
    if vol_z_val > 1.0:
        add("volumen", 10 if trend_regime == "up" else -10,
            f"Volumen {vol_z_val:.2f}σ über dem Schnitt, bestätigt die Bewegung")

    if divergence == "bullish":
        add("divergenz", 15, "Bullische RSI-Divergenz (Preis tiefer, RSI höher)")
    elif divergence == "bearish":
        add("divergenz", -15, "Bärische RSI-Divergenz (Preis höher, RSI tiefer)")

    if pattern == "higher_high_higher_low":
        add("struktur", 15, "Höhere Hochs und höhere Tiefs")
    elif pattern == "lower_high_lower_low":
        add("struktur", -15, "Tiefere Hochs und tiefere Tiefs")

    # Aus den Kursunterlagen (siehe wissensbasis_makro_ta.md Abschnitt 4):
    # "Durchbruch mit hohem Volumen = zuverlässig" -- ein Ausbruch ohne
    # ueberdurchschnittliches Volumen ist deutlich weniger aussagekraeftig.
    # 2026-09-07 gespiegelt: bis dahin gab es NUR den Ausbruch nach oben (aus
    # last_swing_high), kein Gegenstueck fuer den Bruch unter das letzte
    # Swing-Tief -- eine strukturelle Long-Schieflage, die sich nicht einmal
    # ueber die Funktionssignatur ausdruecken liess.
    if near_breakout:
        if vol_z_val > 1.0:
            add("ausbruch", 12, "Nahe am Ausbruchsniveau MIT überdurchschnittlichem Volumen")
        else:
            add("ausbruch", 5, "Nahe am Ausbruchsniveau, aber ohne Volumenbestätigung")
    elif near_breakdown:
        if vol_z_val > 1.0:
            add("ausbruch", -12, "Nahe am Abwärts-Durchbruch MIT überdurchschnittlichem Volumen")
        else:
            add("ausbruch", -5, "Nahe am Abwärts-Durchbruch, aber ohne Volumenbestätigung")

    if stoch_k_val < 20:
        add("stochastik", 8, f"Stochastik {stoch_k_val:.1f} überverkauft (<20)")
    elif stoch_k_val > 80:
        add("stochastik", -8, f"Stochastik {stoch_k_val:.1f} überkauft (>80)")

    if cci_val > 100:
        add("cci", 8, f"CCI {cci_val:.1f} > 100 — ungewöhnlich starke Aufwärtsbewegung")
    elif cci_val < -100:
        add("cci", -8, f"CCI {cci_val:.1f} < -100 — ungewöhnlich starke Abwärtsbewegung")

    # Williams %R fliesst BEWUSST NICHT in den Score ein: die Formel ist
    # mathematisch identisch zur Stochastik, nur andere Skala (%R = %K - 100,
    # an echten Daten bestaetigt -- exakt gleiche Trefferzahlen). Beides zu
    # werten wuerde dieselbe Beobachtung doppelt zaehlen. Wird trotzdem
    # berechnet und angezeigt, nur eben nicht gewichtet.

    if price_above_sar is True:
        add("sar", 8, "Kurs über dem Parabolic-SAR-Punkt (Aufwärtstrend)")
    elif price_above_sar is False:
        add("sar", -8, "Kurs unter dem Parabolic-SAR-Punkt (Abwärtstrend)")

    if price_above_vwap is True:
        add("vwap", 5, "Kurs über dem volumengewichteten Durchschnitt")
    elif price_above_vwap is False:
        add("vwap", -5, "Kurs unter dem volumengewichteten Durchschnitt")

    if candle_pattern in ("bullish_engulfing", "hammer"):
        add("kerzenmuster", 8, f"Bullisches Kerzenmuster: {candle_pattern}")
    elif candle_pattern in ("bearish_engulfing", "shooting_star"):
        add("kerzenmuster", -8, f"Bärisches Kerzenmuster: {candle_pattern}")

    # Nur BESTAETIGTE (Ausbruch bereits erfolgt) Chart-Muster zaehlen, siehe
    # structure.detect_chart_pattern -- unbestaetigte Muster sind reine
    # Beobachtung, kein Signal. Schulter-Kopf-Schulter etwas hoeher gewichtet,
    # gilt in der Literatur als eines der zuverlaessigsten Umkehrmuster.
    _CHART_POINTS = {
        "double_bottom": 18, "inverse_head_and_shoulders": 18, "falling_wedge": 12,
        "double_top": -18, "head_and_shoulders": -18, "rising_wedge": -12,
        "ascending_triangle": 12, "descending_triangle": -12,
        "cup_and_handle": 15, "inverse_cup_and_handle": -15,
        "rounding_bottom": 12, "rounding_top": -12,
        "bull_flag": 10, "bull_pennant": 10, "bear_flag": -10, "bear_pennant": -10,
    }
    if chart_pattern in _CHART_POINTS:
        pts = _CHART_POINTS[chart_pattern]
        add("chartmuster", pts, f"Bestätigtes Chartmuster: {chart_pattern}")

    raw_score = sum(p.points for p in parts)
    score = max(-100, min(100, raw_score))
    if score != raw_score:
        parts.append(ScoreContribution(
            "begrenzung", score - raw_score,
            f"Rohsumme {raw_score} auf {score} begrenzt (Score bleibt in -100..100)",
        ))

    candidate = "NONE"
    if score >= 40:
        candidate = "LONG"
    elif score <= -40:
        candidate = "SHORT"
    return score, candidate, parts


def score_and_candidate(*args, **kwargs) -> tuple[int, str]:
    """Duenner Wrapper -- unveraenderte Signatur fuer Aufrufer, die die
    Aufschluesselung nicht brauchen (z.B. backtest.py, das ueber 17'000 Kerzen
    laeuft und pro Kerze keine Beitragsliste bauen soll)."""
    score, candidate, _ = score_with_breakdown(*args, **kwargs)
    return score, candidate


@dataclass
class TechnicalSignal:
    symbol: str
    timeframe: str
    ts: str
    price: float
    trend: dict = field(default_factory=dict)
    momentum: dict = field(default_factory=dict)
    volatility: dict = field(default_factory=dict)
    volume: dict = field(default_factory=dict)
    structure: dict = field(default_factory=dict)
    divergence: dict = field(default_factory=dict)
    score: int = 0
    candidate: str = "NONE"  # LONG | SHORT | NONE
    score_breakdown: list[ScoreContribution] = field(default_factory=list)

    def top_drivers(self, n: int = 3) -> list[ScoreContribution]:
        """Die n gewichtigsten Beitraege (nach Betrag), fuer die Telegram-Nachricht."""
        return sorted(self.score_breakdown, key=lambda c: abs(c.points), reverse=True)[:n]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "tf": self.timeframe,
            "ts": self.ts,
            "price": self.price,
            "trend": self.trend,
            "momentum": self.momentum,
            "volatility": self.volatility,
            "volume": self.volume,
            "structure": self.structure,
            "divergence": self.divergence,
            "score": self.score,
            "candidate": self.candidate,
            "score_breakdown": [c.to_dict() for c in self.score_breakdown],
        }


def _snapshot_at(
    df: pd.DataFrame,
    feat: Features,
    i: int,
    symbol: str,
    timeframe: str,
    ts: str,
    pattern_info: dict,
    divergence: str,
    pivots: list[struct.Pivot],
) -> TechnicalSignal:
    """Baut ein TechnicalSignal fuer Kerze i aus vorab berechneten Features + Struktur-Info.

    Gemeinsamer Kern fuer build_technical_signal() (i = letzte Kerze) und
    backtest.py (i = jede historische Kerze). `pivots` muss bereits
    Look-ahead-sicher sein -- im Backtest via struct.confirmed_pivots_at()
    gefiltert, live automatisch dank auf "jetzt" abgeschnittenem df.
    """
    price = float(df["close"].iloc[i])
    ema50, ema200 = float(feat.ema50.iloc[i]), float(feat.ema200.iloc[i])
    adx_val, rsi_val = float(feat.adx.iloc[i]), float(feat.rsi.iloc[i])
    macd_hist_val = float(feat.macd_hist.iloc[i])
    prev_macd_hist = float(feat.macd_hist.iloc[i - 1]) if i > 0 else macd_hist_val
    atr_val = float(feat.atr.iloc[i])
    bb_pctb, bb_width = float(feat.bb_percent_b.iloc[i]), float(feat.bb_width.iloc[i])
    vol_z_val = float(feat.vol_z.iloc[i])
    obv_slope = "rising" if i >= 5 and feat.obv.iloc[i] > feat.obv.iloc[i - 5] else "falling"

    stoch_k_val = float(feat.stoch_k.iloc[i]) if pd.notna(feat.stoch_k.iloc[i]) else 50.0
    cci_val = float(feat.cci.iloc[i]) if pd.notna(feat.cci.iloc[i]) else 0.0
    williams_val = float(feat.williams_r.iloc[i]) if pd.notna(feat.williams_r.iloc[i]) else -50.0
    sar_val = float(feat.sar.iloc[i]) if pd.notna(feat.sar.iloc[i]) else None
    vwap_val = float(feat.vwap.iloc[i]) if pd.notna(feat.vwap.iloc[i]) else None
    candle_pattern_val = str(feat.candle_pattern.iloc[i])
    price_above_sar = (price > sar_val) if sar_val is not None else None
    price_above_vwap = (price > vwap_val) if vwap_val is not None else None

    trend_regime = "up" if ema50 > ema200 else "down"
    macd_cross = "bull" if macd_hist_val > 0 and prev_macd_hist <= 0 else (
        "bear" if macd_hist_val < 0 and prev_macd_hist >= 0 else
        ("bullish" if macd_hist_val > 0 else "bearish")
    )

    swing_high = pattern_info["last_swing_high"]
    swing_low = pattern_info["last_swing_low"]
    near_breakout = bool(swing_high and price >= swing_high * 0.999)
    near_breakdown = bool(swing_low and price <= swing_low * 1.001)

    chart_pattern_info = struct.detect_chart_pattern(pivots, atr_val, price)
    if not chart_pattern_info["confirmed"]:
        # Pivot-basierte Muster (spezifischer/verlaesslicher) haben nichts gefunden --
        # als Rueckfall die "breiten" Muster (Flagge/Wimpel/Cup&Handle/Rounding) pruefen.
        chart_pattern_info = chart_patterns.detect_wide_pattern(df, i, atr_val)

    score, candidate, breakdown = score_with_breakdown(
        trend_regime, adx_val, macd_hist_val, rsi_val, vol_z_val,
        divergence, pattern_info["pattern"], near_breakout, near_breakdown,
        stoch_k_val=stoch_k_val, cci_val=cci_val, williams_r_val=williams_val,
        price_above_sar=price_above_sar, price_above_vwap=price_above_vwap,
        candle_pattern=candle_pattern_val,
        chart_pattern=chart_pattern_info["pattern"] if chart_pattern_info["confirmed"] else "none",
    )

    return TechnicalSignal(
        symbol=symbol,
        timeframe=timeframe,
        ts=ts,
        price=round(price, 2),
        trend={"ema50": round(ema50, 2), "ema200": round(ema200, 2),
               "adx": round(adx_val, 1), "regime": trend_regime},
        momentum={"rsi14": round(rsi_val, 1), "macd_hist": round(macd_hist_val, 4),
                  "macd_cross": macd_cross},
        volatility={"atr14": round(atr_val, 4), "atr_pct": round(atr_val / price * 100, 2),
                    "bb_percent_b": round(bb_pctb, 2), "bb_width": round(bb_width, 4)},
        volume={"vol_z": round(vol_z_val, 2), "obv_slope": obv_slope},
        structure={"pattern": pattern_info["pattern"],
                   "swing_high": swing_high, "swing_low": pattern_info["last_swing_low"],
                   "pivots_used": pattern_info["pivots_used"],
                   "chart_pattern": chart_pattern_info["pattern"],
                   "chart_pattern_confirmed": chart_pattern_info["confirmed"],
                   "chart_pattern_level": chart_pattern_info["level"],
                   "candle_pattern": candle_pattern_val,
                   "stoch_k": round(stoch_k_val, 1), "cci": round(cci_val, 1),
                   "williams_r": round(williams_val, 1)},
        divergence={"rsi_divergence": divergence},
        score=score,
        candidate=candidate,
        score_breakdown=breakdown,
    )


def build_technical_signal(df: pd.DataFrame, symbol: str, timeframe: str) -> TechnicalSignal:
    """df: OHLCV-DataFrame, aufsteigend sortiert, min. ~250 Kerzen fuer EMA200. Live-Pfad."""
    feat = compute_features(df)
    pivots = struct.find_pivots(df, left=3, right=3)
    pattern_info = struct.classify_pattern(pivots, lookback_pivots=4)
    divergence = struct.divergence_from_pivots(pivots, feat.rsi)

    i = len(df) - 1
    return _snapshot_at(
        df, feat, i, symbol, timeframe,
        ts=datetime.now(timezone.utc).isoformat(),
        pattern_info=pattern_info, divergence=divergence, pivots=pivots,
    )


def apply_trend_filter(entry_signal: TechnicalSignal, trend_signal: TechnicalSignal) -> TechnicalSignal:
    """
    Multi-Timeframe-Kopplung (PLAN.md Stufe 1): grosse Zeitebene bestimmt
    die erlaubte Richtung, kleine Zeitebene liefert den Einstiegs-Trigger.

    LONG auf 1h wird nur durchgelassen, wenn 4h-Regime "up" ist (und
    umgekehrt fuer SHORT). Sonst wird der Kandidat auf NONE herabgestuft --
    die Zahlen/der Score bleiben sichtbar, nur die Handlungsempfehlung
    aendert sich. Kein zusaetzliches LLM, rein regelbasiert.
    """
    if entry_signal.candidate == "NONE":
        return entry_signal

    trend_ok = (
        (entry_signal.candidate == "LONG" and trend_signal.trend["regime"] == "up")
        or (entry_signal.candidate == "SHORT" and trend_signal.trend["regime"] == "down")
    )
    entry_signal.trend["higher_tf_regime"] = trend_signal.trend["regime"]
    entry_signal.trend["higher_tf_timeframe"] = trend_signal.timeframe
    if not trend_ok:
        entry_signal.trend["filtered_reason"] = (
            f"{entry_signal.candidate} auf {entry_signal.timeframe} widerspricht "
            f"{trend_signal.timeframe}-Trend ({trend_signal.trend['regime']})"
        )
        entry_signal.candidate = "NONE"
    return entry_signal


@dataclass
class TradeProposal:
    entry: float
    stop: float
    target: float
    risk_pct_of_price: float
    reward_risk_ratio: float
    size_pct_of_account: float


def build_trade_proposal(
    sig: TechnicalSignal,
    account_risk_pct: float = 1.0,
    atr_multiple: float = 2.0,
    reward_risk_ratio: float = 2.0,
    size_multiplier: float = 1.0,
) -> TradeProposal | None:
    """
    Stop ATR-basiert, Positionsgroesse aus Stop-Distanz (nicht Bauchgefuehl).
    size_multiplier kommt aus Schicht B (Makro-Gate); ohne Schicht B (Stufe 0/1) fix 1.0.
    """
    if sig.candidate == "NONE":
        return None

    atr_abs = sig.volatility["atr14"]
    entry = sig.price
    direction = 1 if sig.candidate == "LONG" else -1

    stop = entry - direction * atr_multiple * atr_abs
    stop_distance = abs(entry - stop)
    target = entry + direction * stop_distance * reward_risk_ratio

    risk_pct_of_price = round(stop_distance / entry * 100, 2)
    size_pct_of_account = round(account_risk_pct * size_multiplier, 3)

    return TradeProposal(
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        risk_pct_of_price=risk_pct_of_price,
        reward_risk_ratio=reward_risk_ratio,
        size_pct_of_account=size_pct_of_account,
    )
