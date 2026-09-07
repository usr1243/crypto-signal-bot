# 📡 Crypto Signal Bot

**Ein Krypto-Trading-Signal-Bot, der ehrlich zugibt, dass er (noch) keine bewiesene Kante hat — und genau das mit Zahlen belegt, statt es zu verstecken.**
_A crypto trading signal bot that honestly admits it does not (yet) have a proven edge — and backs that up with numbers instead of hiding it._

---

## 🇩🇪 Deutsch

### Das Problem
Die meisten "AI-Trading-Bot"-Projekte im Netz behaupten, profitabel zu sein, zeigen aber nie den echten Backtest, nie die Gebührenrechnung, nie den Vergleich gegen ein etabliertes Referenzsystem. Wer nachfragt, bekommt Ausflüchte statt Zahlen.

### Die Lösung
Ein zweischichtiger Signal-Bot (Technik ohne KI entscheidet, ein LLM darf nur bremsen, nie selbst kaufen) für einen befreundeten Trader — gebaut mit dem Anspruch, jede Behauptung **messbar** zu machen: ehrlicher Walk-Forward-Backtest, Probabilistic Sharpe Ratio statt Bauchgefühl, und eine vollständige Cross-Validierung gegen [freqtrade](https://github.com/freqtrade/freqtrade) (54.000 GitHub-Sterne), um dem eigenen Code nicht blind zu vertrauen. Das Ergebnis ist unbequem: **nach realistischen Gebühren keine bewiesene Kante** — das steht hier genauso wie die Erfolge, siehe [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md).

### Wie funktioniert es?
- **Schicht A — Technik, deterministisch:** ~25 Indikatoren (EMA/RSI/MACD/ADX/Bollinger/ATR/Stochastik/CCI/Williams %R/Parabolic SAR/VWAP), Kerzen- und 16 Chart-Muster (Doppel-Top/-Boden, Schulter-Kopf-Schulter, Dreiecke, Keile, Cup&Handle + Spiegelbild, Flagge/Wimpel), additiv zu einem Score von −100 bis +100 verdichtet. Jeder Beitrag ist einzeln nachvollziehbar (`ScoreContribution`), keine Blackbox-Zahl.
- **Schicht B — Makro/LLM als Veto, nie als Signalgeber:** liest Zinsen, Dollar-Index, Fear-&-Greed-Index und News, darf einen Kandidaten nur blockieren oder verkleinern — die Architektur erzwingt das strukturell, ein LLM kann nie selbst einen Trade auslösen.
- **Lookahead-sichere Backtests:** Pivot-Bestätigung mit Verzögerung, Entry auf der nächsten Kerze (nie zum Signalpreis selbst), R-Multiple-PnL statt roher Preis-Prozente, Walk-Forward-Fenster statt eines einzelnen Testlaufs.
- **Cross-validiert gegen freqtrade:** identische Strategie, identische Daten, 50/50 Trades exakt übereinstimmend (Details + zwei dabei gefundene Bugs in der Methodik selbst: [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md)).
- **Telegram-Fernsteuerung mit Rollentrennung:** ein Admin genehmigt, alles andere landet als Änderungswunsch in einer Warteschlange — nie automatische Ausführung durch eine dritte Person.
- **Docker-Betrieb:** zwei Services (Signal-Loop + Telegram-Listener), `restart: unless-stopped`, Secrets nie im Image.

## 🇬🇧 English

### The problem
Most "AI trading bot" projects online claim profitability but never show the real backtest, the real fee math, or a comparison against an established reference system. Ask for numbers and you get excuses.

### The solution
A two-layer signal bot (deterministic technical scoring decides, an LLM may only veto, never buy) built for a trader friend — with the explicit goal of making every claim **measurable**: an honest walk-forward backtest, Probabilistic Sharpe Ratio instead of gut feeling, and a full cross-validation against [freqtrade](https://github.com/freqtrade/freqtrade) (54,000 GitHub stars) instead of blindly trusting the custom engine. The result is uncomfortable: **no proven edge after realistic fees** — reported here just like the wins, see [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md).

### How it works
- **Layer A — technical, deterministic:** ~25 indicators (EMA/RSI/MACD/ADX/Bollinger/ATR/Stochastic/CCI/Williams %R/Parabolic SAR/VWAP), candlestick and 16 chart patterns (double top/bottom, head & shoulders, triangles, wedges, cup & handle + its mirror, flag/pennant), combined additively into a score from −100 to +100. Every contribution is individually traceable (`ScoreContribution`), never a black-box number.
- **Layer B — macro/LLM as veto only, never a signal source:** reads interest rates, the dollar index, the Fear & Greed index and news, and may only block or shrink a candidate — the architecture enforces this structurally, an LLM can never trigger a trade by itself.
- **Lookahead-safe backtesting:** delayed pivot confirmation, entry on the next candle (never at the signal price itself), R-multiple PnL instead of raw price percentages, walk-forward windows instead of a single test run.
- **Cross-validated against freqtrade:** identical strategy, identical data, 50/50 trades matching exactly (details + two bugs found in the comparison methodology itself: [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md)).
- **Telegram remote control with role separation:** an admin approves, everything else queues as a change request — never automatic execution by a third party.
- **Docker deployment:** two services (signal loop + Telegram listener), `restart: unless-stopped`, secrets never baked into the image.

---

## Tech
`Python` · `pandas/numpy` · `ccxt` · `freqtrade` · `Docker` · `SQLite` · `pytest` (125 Tests) · Telegram Bot API · Anthropic/Groq (LLM-Backend, austauschbar)

## Setup / Run
Details in [`DOCKER.md`](DOCKER.md). Kurzform:
```bash
cp .env.example .env   # ausfüllen: Telegram-Token, optional LLM-Key
docker compose up -d
```

> ⚠️ **Reines Paper-Trading.** `LIVE_TRADING_ENABLED=false` per Default, keine echte Order ohne bewussten manuellen Eingriff. Kein Finanzrat — dieses Projekt zeigt Methodik, nicht eine Gewinnstrategie.
> ⚠️ **Paper-trading only.** `LIVE_TRADING_ENABLED=false` by default, no real order without a deliberate manual step. Not financial advice — this project demonstrates methodology, not a profitable strategy.
