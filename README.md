# 📡 Crypto Signal Bot

**Ein Signal-Bot, der einen Trader ersetzen soll, der selbst keine Zeit zum Traden hat — gebaut für einen befreundeten Krypto-Trader, auf freqtrade aufgesetzt, und ehrlich vermessen statt schöngeredet.**
_A signal bot meant to stand in for a trader who doesn't have time to trade himself — built for a trader friend, running on top of freqtrade, and measured honestly instead of oversold._

---

## 🇩🇪 Deutsch

### Das Problem
Ein befreundeter Trader kennt die Märkte, hat aber nicht die Zeit, den ganzen Tag Charts zu beobachten und selbst am Bildschirm zu sitzen — der klassische Konflikt zwischen "weiß, was zu tun wäre" und "kann nicht ständig dabei sein". Fertige Trading-Bots lösen das selten wirklich: sie verkaufen eine Blackbox-Strategie und verschweigen, ob die überhaupt funktioniert.

### Die Lösung
Ein Bot, der die Rolle des Traders übernimmt, nicht nur seine Befehle ausführt: er beobachtet den Markt rund um die Uhr, bewertet Setups nach denselben Kriterien, die ein erfahrener Trader nutzen würde (~25 Indikatoren, 16 Chart-Muster), und meldet oder platziert Trades — mit einem LLM, das nur bremsen darf, nie selbst entscheidet. Gebaut zuerst für Kryptowährungen (rund um die Uhr handelbar, gut über APIs zugänglich) und auf [freqtrade](https://github.com/freqtrade/freqtrade) aufgesetzt, statt Börsen-Anbindung, Order-Absicherung und Absturz-Wiederherstellung selbst neu zu erfinden. Der Anspruch: jede Behauptung **messbar** machen — ehrlicher Walk-Forward-Backtest, Probabilistic Sharpe Ratio statt Bauchgefühl, vollständige Cross-Validierung gegen freqtrade, um dem eigenen Code nicht blind zu vertrauen. Das Ergebnis ist unbequem: **nach realistischen Gebühren aktuell keine bewiesene Kante** — das steht hier genauso wie die Erfolge, siehe [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md).

### Wie funktioniert es?
- **Schicht A — Technik, deterministisch:** ~25 Indikatoren (EMA/RSI/MACD/ADX/Bollinger/ATR/Stochastik/CCI/Williams %R/Parabolic SAR/VWAP), Kerzen- und 16 Chart-Muster (Doppel-Top/-Boden, Schulter-Kopf-Schulter, Dreiecke, Keile, Cup&Handle + Spiegelbild, Flagge/Wimpel), additiv zu einem Score von −100 bis +100 verdichtet. Jeder Beitrag ist einzeln nachvollziehbar (`ScoreContribution`), keine Blackbox-Zahl.
- **Schicht B — Makro/LLM als Veto, nie als Signalgeber:** liest Zinsen, Dollar-Index, Fear-&-Greed-Index und News, darf einen Kandidaten nur blockieren oder verkleinern — die Architektur erzwingt das strukturell, ein LLM kann nie selbst einen Trade auslösen.
- **Ausführung auf freqtrade:** eigene Scoring-Logik läuft als Strategie *innerhalb* von freqtrade — Stop-Loss als echte Börsen-Order, automatische Wiederherstellung nach Absturz, Retry-Logik bei Verbindungsabbrüchen. Das muss nicht selbst gebaut werden, es ist an einem 54.000-Sterne-Projekt bereits gelöst.
- **Lookahead-sichere Backtests:** Pivot-Bestätigung mit Verzögerung, Entry auf der nächsten Kerze (nie zum Signalpreis selbst), R-Multiple-PnL statt roher Preis-Prozente, Walk-Forward-Fenster statt eines einzelnen Testlaufs.
- **Cross-validiert gegen freqtrade:** identische Strategie, identische Daten, 50/50 Trades exakt übereinstimmend (Details + zwei dabei gefundene Bugs in der Methodik selbst: [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md)).
- **Telegram-Fernsteuerung mit Rollentrennung:** ein Admin genehmigt, alles andere landet als Änderungswunsch in einer Warteschlange — nie automatische Ausführung durch eine dritte Person.
- **Docker-Betrieb:** zwei Services (Signal-Loop + Telegram-Listener), `restart: unless-stopped`, Secrets nie im Image.

## 🇬🇧 English

### The problem
A trader friend knows the markets but doesn't have the time to watch charts all day and sit at the screen himself — the classic conflict between "knows what to do" and "can't always be there." Off-the-shelf trading bots rarely solve this: they sell a black-box strategy and never say whether it actually works.

### The solution
A bot that takes over the trader's role, not just his button-presses: it watches the market around the clock, evaluates setups using the same criteria an experienced trader would (~25 indicators, 16 chart patterns), and reports or places trades — with an LLM that may only veto, never decide on its own. Built first for crypto (tradeable 24/7, well served by APIs) and running on top of [freqtrade](https://github.com/freqtrade/freqtrade) instead of reinventing exchange connectivity, order safety, and crash recovery from scratch. The explicit goal: make every claim **measurable** — an honest walk-forward backtest, Probabilistic Sharpe Ratio instead of gut feeling, and a full cross-validation against freqtrade instead of blindly trusting the custom engine. The result is uncomfortable: **no proven edge after realistic fees, yet** — reported here just like the wins, see [`docs/BACKTEST_METHODOLOGY.md`](docs/BACKTEST_METHODOLOGY.md).

### How it works
- **Layer A — technical, deterministic:** ~25 indicators (EMA/RSI/MACD/ADX/Bollinger/ATR/Stochastic/CCI/Williams %R/Parabolic SAR/VWAP), candlestick and 16 chart patterns (double top/bottom, head & shoulders, triangles, wedges, cup & handle + its mirror, flag/pennant), combined additively into a score from −100 to +100. Every contribution is individually traceable (`ScoreContribution`), never a black-box number.
- **Layer B — macro/LLM as veto only, never a signal source:** reads interest rates, the dollar index, the Fear & Greed index and news, and may only block or shrink a candidate — the architecture enforces this structurally, an LLM can never trigger a trade by itself.
- **Execution on freqtrade:** the custom scoring logic runs as a strategy *inside* freqtrade — stop-loss as a real exchange order, automatic recovery after a crash, retry logic on connection drops. No need to build that in-house, a 54,000-star project already solved it.
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
