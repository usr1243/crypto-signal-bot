# Methodik & ehrliche Ergebnisse / Methodology & Honest Results

Dieses Dokument ist der eigentliche Kern des Projekts: nicht "ich habe einen
Trading-Bot gebaut", sondern **wie prüft man rigoros, ob eine Strategie
überhaupt eine Kante hat — und was macht man, wenn die Antwort Nein ist.**

*This document is the actual core of the project: not "I built a trading
bot," but **how do you rigorously test whether a strategy has any edge at
all — and what do you do when the answer is no.***

---

## 🇩🇪 Deutsch

### Backtest: kein Edge nach Kosten

2 Jahre BTC/USDT-Historie (Binance), Standard-Setup (2×ATR-Stop, 2:1
Reward:Risk): **Profit Factor 0.53, Probabilistic Sharpe Ratio ≈ 0.**

Wichtiger Fund: Bei so engen Stops frisst allein die Taker-Gebühr (0.26%/Seite)
**~43% eines Risiko-Multiples** — ein struktureller Nachteil, unabhängig vom
Scoring. Mit weiterem Stop (4×ATR) sinkt der Gebühren-Anteil auf ~0.2R,
Profit Factor steigt auf ~0.87 — aber **weiterhin kein statistisch
abgesicherter Vorteil** (PSR klar unter 50%).

### Mehr Indikatoren haben es nicht verbessert

Nach Erweiterung von ~10 auf ~25 Indikatoren (Stochastik, CCI, Williams %R,
Parabolic SAR, VWAP, 15 Chart-Muster): Profit Factor sank auf **0.76** statt
zu steigen. Ehrlich gemessen, nicht schöngeredet: **mehr Signale lösen nicht
automatisch das Grundproblem.**

### Score-Asymmetrien gefunden und behoben

Ein Symmetrie-Test (jede Regel gespiegelt, der Score muss sich exakt umkehren)
fand vier Regeln, die die bärische Seite systematisch benachteiligten — u.a.
ein Chartmuster mit bullischem Bonus ohne bärisches Gegenstück, und ein
"gesundes RSI-Band", das nicht um den Neutralpunkt 50 zentriert war. Live
bedeutete das: von 86 Signalen war **kein einziges** ein Short. Nach der
Reparatur: Winrate stieg leicht (31%→34%), Profit Factor blieb bei ~0.73 —
**die Kante war das Problem, nicht die Symmetrie.** Beide Ergebnisse stehen
hier, nicht nur das positive.

### Cross-Validierung gegen freqtrade (54k★ Referenz-Framework)

Bevor eigenem Code vertraut wird, sollte man ihn gegen ein etabliertes System
prüfen. Eigene Engine und [freqtrade](https://github.com/freqtrade/freqtrade)
liefen mit identischer Strategie auf identischen Daten:

- EMA-Serien: **0.0 Differenz** (bitidentisch)
- 50/50 Trades, **100% Deckung** der Einstiegszeitpunkte
- Exit-Grund: 0 Abweichungen auf 50 Trades
- R-Multiple: max. Abweichung 0.000013, **kein systematisches Vorzeichen**
- Winrate: **44.0% in beiden Motoren, exakt**

**Zwei echte Bugs dabei gefunden — in der eigenen Vergleichs-Methodik, nicht
im Motor:** (1) Ein Short-Trade blockierte fälschlich denselben Slot wie ein
Long im eigenen Vergleichsskript — kein Motor-Fehler, ein Fehler im Vergleich
selbst. (2) freqtrades `custom_roi()` erwartet eine **gebührenbereinigte
Netto-Rendite**, die eigene Zielberechnung war ein roher Preis ohne
Gebührenverrechnung — am echten freqtrade-Quellcode nachgerechnet und
korrigiert. Beide Funde zeigen den Wert des Abgleichs: nicht nur "stimmt der
Motor", sondern "stimmt der *Vergleich* selbst".

### Konsequenz: Ausführung auf freqtrade migriert, Strategie eigenständig

Nach der Validierung: die Handelslogik (Scoring, Muster-Erkennung) läuft
weiterhin eigenständig — aber als Strategie **innerhalb** von freqtrade
(`freqtrade/user_data/strategies/`), das die Börsen-Absicherung übernimmt
(Stop-Loss als echte Börsen-Order, Wiederherstellung nach Absturz, Retry-Logik).
Das eigene System bleibt parallel bestehen (Makro-Schicht ist dort noch nicht
portiert) — bewusst zwei Systeme, kein Kompromiss zwischen ihnen.

### Warum das für echtes Geld nicht reicht

Bei einer Trefferquote von 28% und Ziel 3:1 liegt der Bruttovorteil bei
**+0.13R pro Trade** — und genau das ist das gesamte Budget für Kosten.
Gemessene Gebühren-Kosten je nach Kerzengröße: 0.09R (Tageskerzen) bis 1.13R
(15-Minuten-Kerzen) — bis zum Neunfachen der gesamten Kante. Das lässt sich
aus Trefferquote und Gebührenstruktur **exakt vorhersagen**, nicht nur
beobachten. Konsequenz: `LIVE_TRADING_ENABLED` bleibt auf `false`, solange
kein aus Trainingsdaten unabhängiger Beweis für einen Vorteil nach Kosten
vorliegt.

---

## 🇬🇧 English

### Backtest: no edge after costs

2 years of BTC/USDT history (Binance), standard setup (2×ATR stop, 2:1
reward:risk): **Profit Factor 0.53, Probabilistic Sharpe Ratio ≈ 0.**

Key finding: with stops this tight, the taker fee alone (0.26%/side) eats
**~43% of one risk-multiple** — a structural disadvantage independent of the
scoring. With a wider stop (4×ATR), fee drag drops to ~0.2R and profit factor
rises to ~0.87 — but **still no statistically robust edge** (PSR clearly
below 50%).

### More indicators didn't help

After expanding from ~10 to ~25 indicators (Stochastic, CCI, Williams %R,
Parabolic SAR, VWAP, 15 chart patterns): profit factor *dropped* to **0.76**
instead of rising. Measured honestly, not spun: **more signals don't
automatically fix the underlying problem.**

### Score asymmetries found and fixed

A symmetry test (mirror every rule's inputs, the score must exactly negate)
found four rules that systematically disadvantaged the bearish side —
including a chart pattern with a bullish bonus and no bearish counterpart,
and a "healthy RSI band" not centered on the neutral point 50. Live, this
meant zero of 86 signals were ever a short. After the fix: win rate rose
slightly (31%→34%), profit factor stayed at ~0.73 — **the edge was the
problem, not the symmetry.** Both results are reported here, not just the
positive one.

### Cross-validated against freqtrade (54k★ reference framework)

Before trusting your own code, check it against an established system. The
custom engine and [freqtrade](https://github.com/freqtrade/freqtrade) ran the
identical strategy on identical data:

- EMA series: **0.0 difference** (bit-identical)
- 50/50 trades, **100% coverage** of entry timestamps
- Exit reason: 0 mismatches across 50 trades
- R-multiple: max deviation 0.000013, **no systematic sign bias**
- Win rate: **44.0% in both engines, exactly**

**Two real bugs found — in the comparison methodology, not the engine:**
(1) A short trade wrongly occupied the same slot as a long in the comparison
script's own state machine — not an engine bug, a comparison bug. (2)
freqtrade's `custom_roi()` expects a **fee-adjusted net return**, while the
own target calculation was a raw price with no fee accounting — traced
through freqtrade's actual source and corrected. Both findings show the value
of cross-checking: not just "is the engine right," but "is the *comparison*
itself right."

### Consequence: execution migrated to freqtrade, strategy stays independent

After validation, the trading logic (scoring, pattern detection) still runs
independently — but as a strategy **inside** freqtrade
(`freqtrade/user_data/strategies/`), which handles exchange-side safety
(stop-loss as a real exchange order, crash recovery, retry logic). The
original system stays running in parallel (the macro layer isn't ported yet)
— deliberately two systems, not a compromise between them.

### Why this isn't enough for real money

At a 28% win rate and a 3:1 target, the gross edge is **+0.13R per trade** —
and that's the entire budget available to pay for costs. Measured fee costs
depending on candle size: 0.09R (daily candles) to 1.13R (15-minute candles)
— up to nine times the entire edge. This is **exactly predictable** from win
rate and fee structure, not just observed after the fact. Consequence:
`LIVE_TRADING_ENABLED` stays `false` until there's evidence of a post-cost
edge on data independent of training.
