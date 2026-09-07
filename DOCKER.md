# Bot per Klick starten/stoppen (Docker)

Statt Terminal offen halten oder Autostart bei jedem Login: der Bot läuft in
zwei Docker-Containern (Signal-Loop + Telegram-Zuhörer), die du in Docker
Desktop mit einem Klick startest und stoppst.

## Einmalig einrichten

Voraussetzung: `.env` ist bereits ausgefüllt (Telegram-Token, Keys etc.).

```bash
cd Claude-Brain/projects/trading-bot/bot
docker compose build
```

Dauert beim ersten Mal ein paar Minuten (lädt Python-Pakete inkl. FinBERT-
Abhängigkeiten). Danach nie wieder nötig, ausser der Code ändert sich.

## Starten

**Terminal:**
```bash
docker compose up -d
```

**Docker Desktop:** App öffnen → "Containers" → die Gruppe **tradingbot** →
▶️-Button oben auf der Gruppe klickt beide Container gleichzeitig an
(`tradingbot-run-loop` + `tradingbot-telegram-listener`).

## Stoppen

**Terminal:** `docker compose down`
**Docker Desktop:** ⏹-Button auf der Gruppe **tradingbot** — beide Container
stoppen zusammen, sauber.

## Status / Logs ansehen

Docker Desktop → Gruppe **tradingbot** aufklappen → auf einen Container
klicken → Tab "Logs" — dieselbe Ausgabe, die vorher im Terminal lief.

Terminal-Alternative:
```bash
docker compose logs -f run_loop
docker compose logs -f telegram_listener
```

## Was bleibt gleich

- **Reines Paper-Trading.** Docker ändert nichts an `LIVE_TRADING_ENABLED` —
  das steht weiter auf `false`, keine echten Orders.
- **`.env` bleibt auf deinem Mac**, nie im Container-Image (siehe `.dockerignore`
  und `docker-compose.yml`) — nur zur Laufzeit eingebunden.
- **Die Datenbank (`data/trading_bot.sqlite3`) übersteht Neustarts** — sie liegt
  auf deinem Mac, der Container schreibt nur rein (Volume-Mount), verliert bei
  einem Stopp nichts.
- Der Report (`python -m src.report`) läuft weiterhin lokal in deiner normalen
  `.venv`, nicht im Container — er braucht nur Lesezugriff auf dieselbe
  `data/`-Datei, die auch im Container liegt.

## Falls `docker compose build` einen Fehler wirft

Meistens: Docker Desktop läuft nicht. App öffnen, kurz warten, nochmal
versuchen.

## Das neue freqtrade-System (Stufe C, noch nicht live)

Seit 2026-09-05 gibt es einen zweiten, komplett getrennten Docker-Stack unter
`freqtrade/docker-compose.yml` (siehe `PLAN` in
`~/.claude/plans/wild-booping-firefly.md`, Stufe C). **Dieser hier
(`bot/docker-compose.yml`) bleibt das Produktivsystem** — der neue Stack läuft
noch nicht, braucht noch echte Börsen-Keys und einen bewussten Beschluss, bevor
er überhaupt gestartet wird.

Damit `telegram_listener.py` das neue System später per `/ft_status`
`/ft_pause` `/ft_resume` `/ft_forceexit` `/ft_panic` erreichen kann (siehe
`.env.example`, Abschnitt "PLAN Stufe C10"), müssen beide Compose-Stacks im
selben Docker-Netzwerk hängen — das ist **einmalig** einzurichten:

```bash
docker network create tradingbot-shared
```

(Auf diesem Mac bereits erledigt, 2026-09-05.) Ohne dieses Netzwerk laufen
beide Systeme trotzdem unabhängig voneinander — nur die `/ft_*`-Befehle würden
dann "freqtrade-API nicht erreichbar" melden, sobald der neue Stack einmal
läuft.

**Nach dem Anlegen des Netzwerks:** `telegram_listener` muss einmal neu
gestartet werden, damit es dem Netzwerk beitritt (`docker compose up -d
--force-recreate telegram_listener`) — kurzer, unschädlicher Neustart wie bei
jedem Code-Update.
