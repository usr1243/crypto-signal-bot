"""
Stufe 5: hoert per Long-Polling (getUpdates) auf Telegram-Button-Klicks
(siehe notify.send_telegram_with_actions) und reagiert:

  execute -> execution.place_order() (nur wenn LIVE_TRADING_ENABLED=true,
             sonst simuliert execution.py automatisch und sicher)
  ignore  -> nur geloggt
  later   -> nur geloggt (kein Reminder-Mechanismus im Erstentwurf)

Beantwortet ausserdem freie Fragen (kein "/"-Befehl) ueber das konfigurierte
LLM-Backend -- z.B. "was ist deine Strategie?". WICHTIG: das ist eine reine
Erklaerungs-Funktion, keine zweite Entscheidungsinstanz. Der System-Prompt
beschreibt nur, was der Bot tatsaechlich tut (siehe STRATEGY_CONTEXT) --
das Modell soll nicht raten oder Fantasie-Features erfinden.

Rollen-Trennung (2026-09-03): wenn ADMIN_TELEGRAM_USER_ID gesetzt ist, werden
freie Nachrichten von JEDEM ANDEREN Absender (z.B. dem Trading-Kollegen in
einer gemeinsamen Gruppe) NICHT direkt beantwortet, sondern als
Aenderungswunsch gespeichert (change_requests) und dem Admin zur Freigabe
vorgelegt (/approve <id> / /reject <id>). Erst freigegebene Anfragen werden
sichtbar (get_approved_change_requests), damit sie in einem echten Claude-
Code-Gespraech gemeinsam mit dem Admin umgesetzt werden -- NIEMALS automatisch
von diesem Skript selbst. Ohne ADMIN_TELEGRAM_USER_ID: unveraendertes
Verhalten wie bisher (jeder wird direkt vom LLM beantwortet).

Bewusst kein Fremdcode/keine externe "Claude-in-Telegram"-Bruecke -- eine
kurz geprüfte Alternative hatte eine undokumentierte SSH-Funktion und einen
nicht bestaetigten Freigabe-Mechanismus (siehe Konversation vom 2026-09-03).

Getrennt von run_loop.py, damit ein Fehler im Button-Handling nie den
Signal-/Paper-Trading-Loop mitreisst. Beide muessen parallel laufen -- ohne
diesen Prozess kommt auf KEINE Nachricht (Befehl oder freie Frage) eine
Antwort, auch wenn run_loop.py laeuft (zwei getrennte Prozesse!).

PLAN Stufe C10 (2026-09-05): zusaetzlich admin-only Steuerbefehle fuer das
NEUE freqtrade-System (siehe bot/freqtrade/) -- /ft_status /ft_pause
/ft_resume /ft_forceexit /ft_panic sprechen ueber freqtrade_client.py mit
freqtrades eigener REST-API, NICHT mit execution.py (das bleibt fuer das
bisherige System). Der change_requests-Freigabe-Flow (Kollege schreibt,
Admin genehmigt) bleibt unveraendert -- das war nie als Auto-Executor gedacht
(siehe handle_freetext), sondern als Vorlage fuer ein gemeinsames Gespraech.

Aufruf:
    ./.venv/bin/python -m src.telegram_listener
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from . import db, execution, paper_trading
from .freqtrade_client import FreqtradeAPIError, FreqtradeClient
from .main import DB_PATH

BASE_DIR = Path(__file__).resolve().parent.parent

STRATEGY_CONTEXT = """Du beantwortest Fragen ÜBER einen Trading-Signal-Bot, den du selbst nicht steuerst.
Antworte kurz (3-6 Saetze), ehrlich, in einfacher Sprache -- der Nutzer ist nicht technisch.
Erfinde KEINE Faehigkeiten, die hier nicht stehen.

WAS DER BOT WIRKLICH TUT:
- Schicht A (Technik, kein LLM, jede Stunde): berechnet ca. 15 Chart-Werkzeuge
  (RSI, MACD, Bollinger, EMA-Trend, ATR, ADX, OBV, Stochastik, CCI, Williams %R,
  Parabolic SAR, VWAP, Kerzenmuster, sowie groessere Chart-Muster wie Doppel-Top/
  -Boden, Schulter-Kopf-Schulter, Dreiecke, Keile, Flaggen, Cup&Handle, Rundungen)
  und verdichtet das zu einem Score von -100 bis +100. Erst ab +/-40 gibt es
  ueberhaupt einen Kandidaten (Long/Short).
- Schicht B (Makro/KI, nur bei einem Kandidaten): liest Zinsen, Dollar-Kurs,
  Aktienmarkt, Angst&Gier-Index und News, und darf einen Kandidaten NUR
  blockieren oder seine Groesse verkleinern -- nie selbst einen erzeugen.
- Risiko: jeder simulierte Trade riskiert 1% des (fiktiven) Kontos, Stop-Loss
  automatisch aus der Volatilitaet berechnet.
- WICHTIG: Es werden NUR Uebungstrades in einer Datenbank gefuehrt. Es gibt
  KEINEN echten Auftrag an einer Boerse, kein echtes Geld ist im Spiel.
- Ehrlicher Backtest ueber 2 Jahre BTC-Historie zeigt bisher KEINEN klaren
  statistischen Vorteil nach Gebuehren -- der Bot ist eine laufende Testphase,
  kein bewiesenes Gewinn-System.
- Befehle, die der Bot direkt versteht: /status /pause /resume /help -- alles
  andere beantwortest du als Erklaerung, du triffst keine Trading-Entscheidung."""


def _admin_id() -> int | None:
    raw = os.environ.get("ADMIN_TELEGRAM_USER_ID")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _is_admin(from_id: int) -> bool:
    admin_id = _admin_id()
    return admin_id is None or admin_id == from_id  # None = Rollen-Trennung noch nicht konfiguriert


def _freqtrade_client() -> FreqtradeClient | None:
    """None, solange die freqtrade-API nicht konfiguriert ist (z.B. weil das
    neue System noch nicht gestartet wurde) -- die aufrufenden /ft_*-Befehle
    antworten dann mit einer klaren Meldung statt mit einem Absturz."""
    base_url = os.environ.get("FREQTRADE_API_URL")
    user = os.environ.get("FREQTRADE_API_USER")
    password = os.environ.get("FREQTRADE_API_PASS")
    if not base_url or not user or not password:
        return None
    return FreqtradeClient(base_url=base_url, username=user, password=password)


def _answer_callback(token: str, callback_query_id: str, text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=10,
    )


def _send_text(token: str, chat_id, text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def handle_command(token: str, chat_id, text: str, from_id: int, from_name: str) -> None:
    """
    Von freqtrade inspiriert (siehe README/PLAN.md): einfache Text-Befehle
    zur Fernsteuerung, nicht nur passiver Alert-Empfang. Bewusst kein /stop,
    das den Prozess wirklich beendet -- run_loop.py und telegram_listener.py
    sind getrennte Prozesse, ein "hartes Stop" per Chat-Nachricht ueber
    Prozessgrenzen hinweg ist eine groessere, fehleranfaelligere Baustelle als
    hier gerechtfertigt. /pause deckt den eigentlichen Bedarf ("keine neuen
    Trades mehr") ab, ohne den Prozess selbst anzufassen.
    """
    conn = db.connect(DB_PATH)
    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].lower() if parts else ""
    is_admin = _is_admin(from_id)

    if cmd == "/status":
        paused = db.is_paused(conn)
        halted, halt_reason = paper_trading.check_circuit_breakers(conn)
        open_trades = paper_trading.get_open_trades(conn)
        pending = db.get_pending_change_requests(conn)
        lines = [
            "🤖 STATUS",
            f"Pausiert: {'ja' if paused else 'nein'}",
            f"Circuit-Breaker: {'AKTIV -- ' + halt_reason if halted else 'nicht ausgeloest'}",
            f"Offene Paper-Trades: {len(open_trades)}",
        ]
        for p in open_trades[:10]:
            lines.append(f"  · {p.symbol} {p.direction} @ {p.entry:,.2f} (Stop {p.stop:,.2f} / Ziel {p.target:,.2f})")
        if pending:
            lines.append(f"Offene Aenderungswuensche: {len(pending)} (/requests zum Ansehen)")
        _send_text(token, chat_id, "\n".join(lines))

    elif cmd == "/pause" and is_admin:
        db.set_paused(conn, True)
        _send_text(token, chat_id, "⏸ Pausiert. Keine neuen Signale/Trades mehr, offene Paper-Trades werden "
                                    "weiter ueberwacht. /resume zum Fortsetzen.")

    elif cmd == "/resume" and is_admin:
        db.set_paused(conn, False)
        _send_text(token, chat_id, "▶️ Fortgesetzt. Signal-Checks laufen wieder normal.")

    elif cmd == "/reset_breaker" and is_admin:
        paper_trading.reset_circuit_breaker(conn)
        _send_text(token, chat_id, "🔄 Circuit-Breaker manuell zurueckgesetzt. Trades vor jetzt zaehlen "
                                    "nicht mehr fuer Tagesverlust/Max-Drawdown.")

    elif cmd == "/requests":
        pending = db.get_pending_change_requests(conn)
        if not pending:
            _send_text(token, chat_id, "Keine offenen Aenderungswuensche.")
        else:
            lines = ["📋 OFFENE AENDERUNGSWUENSCHE"]
            for r in pending:
                lines.append(f"#{r['id']} von {r['requester_name']} ({r['created_at'][:16]}):\n  \"{r['message']}\"")
            lines.append("\nFreigeben: /approve <id> · Ablehnen: /reject <id> [Grund]")
            _send_text(token, chat_id, "\n\n".join(lines))

    elif cmd == "/approve" and is_admin:
        if len(parts) < 2 or not parts[1].isdigit():
            _send_text(token, chat_id, "Nutzung: /approve <id> -- die Nummer steht bei /requests.")
        else:
            ok = db.decide_change_request(conn, int(parts[1]), "approved")
            _send_text(token, chat_id,
                       f"✅ Anfrage #{parts[1]} freigegeben -- wird beim naechsten Claude-Code-Gespraech umgesetzt."
                       if ok else f"Anfrage #{parts[1]} nicht gefunden oder schon entschieden.")

    elif cmd == "/reject" and is_admin:
        if len(parts) < 2 or not parts[1].isdigit():
            _send_text(token, chat_id, "Nutzung: /reject <id> [Grund]")
        else:
            reason = parts[2] if len(parts) > 2 else None
            ok = db.decide_change_request(conn, int(parts[1]), "rejected", admin_note=reason)
            _send_text(token, chat_id, f"❌ Anfrage #{parts[1]} abgelehnt." if ok
                       else f"Anfrage #{parts[1]} nicht gefunden oder schon entschieden.")

    elif cmd == "/ft_status" and is_admin:
        client = _freqtrade_client()
        if client is None:
            _send_text(token, chat_id, "freqtrade-API nicht konfiguriert (FREQTRADE_API_URL/_USER/_PASS "
                                        "fehlen in .env) -- das neue System laeuft noch nicht.")
        else:
            try:
                trades = client.status()
            except FreqtradeAPIError as exc:
                _send_text(token, chat_id, f"freqtrade-API-Fehler: {exc}")
            else:
                if not trades:
                    _send_text(token, chat_id, "🤖 FREQTRADE: keine offenen Positionen.")
                else:
                    lines = ["🤖 FREQTRADE STATUS"]
                    for t in trades:
                        direction = "SHORT" if t.get("is_short") else "LONG"
                        stop = t.get("stop_loss_abs")
                        stop_str = f"{stop:,.2f}" if stop is not None else "?"
                        lines.append(f"  #{t.get('trade_id')} · {t.get('pair')} {direction} "
                                     f"@ {t.get('open_rate', 0):,.2f} (Stop {stop_str})")
                    _send_text(token, chat_id, "\n".join(lines))

    elif cmd == "/ft_pause" and is_admin:
        client = _freqtrade_client()
        if client is None:
            _send_text(token, chat_id, "freqtrade-API nicht konfiguriert.")
        else:
            try:
                client.pause()
                _send_text(token, chat_id, "⏸ freqtrade pausiert. Keine neuen Einstiege, offene "
                                            "Positionen bleiben ueberwacht. /ft_resume zum Fortsetzen.")
            except FreqtradeAPIError as exc:
                _send_text(token, chat_id, f"freqtrade-API-Fehler: {exc}")

    elif cmd == "/ft_resume" and is_admin:
        client = _freqtrade_client()
        if client is None:
            _send_text(token, chat_id, "freqtrade-API nicht konfiguriert.")
        else:
            try:
                client.resume()
                _send_text(token, chat_id, "▶️ freqtrade fortgesetzt.")
            except FreqtradeAPIError as exc:
                _send_text(token, chat_id, f"freqtrade-API-Fehler: {exc}")

    elif cmd == "/ft_forceexit" and is_admin:
        client = _freqtrade_client()
        if client is None:
            _send_text(token, chat_id, "freqtrade-API nicht konfiguriert.")
        else:
            trade_id = parts[1] if len(parts) > 1 else "all"
            try:
                client.force_exit(trade_id)
                _send_text(token, chat_id, f"✅ Schliess-Auftrag fuer {trade_id} an freqtrade gesendet.")
            except FreqtradeAPIError as exc:
                _send_text(token, chat_id, f"freqtrade-API-Fehler: {exc}")

    elif cmd == "/ft_panic" and is_admin:
        client = _freqtrade_client()
        if client is None:
            _send_text(token, chat_id, "freqtrade-API nicht konfiguriert.")
        else:
            try:
                client.panic()
                _send_text(token, chat_id, "🛑 NOT-AUS: alle freqtrade-Positionen geschlossen, "
                                            "Bot komplett gestoppt. Neustart nur manuell in Docker.")
            except FreqtradeAPIError as exc:
                _send_text(token, chat_id, f"freqtrade-API-Fehler: {exc}")

    elif cmd in ("/pause", "/resume", "/approve", "/reject", "/reset_breaker",
                 "/ft_status", "/ft_pause", "/ft_resume", "/ft_forceexit", "/ft_panic") and not is_admin:
        _send_text(token, chat_id, "Das kann nur der Admin. Schreib deinen Wunsch einfach als normale "
                                    "Nachricht, er wird zur Freigabe vorgelegt.")

    elif cmd == "/help":
        lines = ["Befehle:", "/status -- aktueller Zustand", "/requests -- offene Aenderungswuensche"]
        if is_admin:
            lines += ["/pause -- keine neuen Trades", "/resume -- wieder aktivieren",
                      "/approve <id> -- Aenderungswunsch freigeben", "/reject <id> [Grund] -- ablehnen",
                      "/reset_breaker -- Circuit-Breaker manuell zuruecksetzen",
                      "--- neues freqtrade-System ---",
                      "/ft_status -- offene Positionen bei freqtrade",
                      "/ft_pause -- freqtrade: keine neuen Einstiege",
                      "/ft_resume -- freqtrade: wieder aktivieren",
                      "/ft_forceexit [id|all] -- eine oder alle Positionen sofort schliessen",
                      "/ft_panic -- NOT-AUS: alles schliessen UND freqtrade stoppen"]
        lines.append("/help -- diese Liste")
        _send_text(token, chat_id, "\n".join(lines))


def handle_freetext(token: str, chat_id, text: str, from_id: int, from_name: str) -> None:
    """
    Admin: direkte Antwort vom LLM-Backend (Erklaerung, keine Entscheidung).
    Nicht-Admin (z.B. der Kollege, sobald ADMIN_TELEGRAM_USER_ID gesetzt ist):
    NICHTS wird direkt umgesetzt oder auch nur vom LLM final beantwortet --
    die Nachricht wird als change_request gespeichert und dem Admin zur
    Freigabe vorgelegt. Das ist die zentrale Sicherheitsgrenze dieser Datei.
    """
    if not _is_admin(from_id):
        conn = db.connect(DB_PATH)
        request_id = db.create_change_request(conn, from_id, from_name, text)
        _send_text(token, chat_id,
                   f"📨 Danke, weitergeleitet an den Admin zur Pruefung (Anfrage #{request_id}). "
                   f"Nichts wird automatisch umgesetzt.")
        return

    try:
        from .llm.backend import get_llm_backend
        llm = get_llm_backend("auto")
        response = llm.ask(STRATEGY_CONTEXT, text)
        answer = response.text.strip() or "Konnte dazu keine Antwort formulieren."
    except Exception as exc:
        answer = (f"Konnte die Frage nicht beantworten (LLM-Backend nicht erreichbar: "
                  f"{exc.__class__.__name__}). Befehle, die immer funktionieren: /status /pause /resume /help")
    _send_text(token, chat_id, answer)


def handle_callback(token: str, chat_id, callback_query: dict) -> None:
    data = callback_query.get("data", "")
    action, _, signal_id_str = data.partition(":")
    if not signal_id_str.isdigit():
        return
    signal_id = int(signal_id_str)

    conn = db.connect(DB_PATH)
    signal_row = db.get_signal(conn, signal_id)
    if signal_row is None:
        _answer_callback(token, callback_query["id"], "Signal nicht gefunden.")
        return

    if action == "ignore":
        db.record_telegram_action(conn, signal_id, "ignore")
        _answer_callback(token, callback_query["id"], "Ignoriert, kein Trade.")

    elif action == "later":
        db.record_telegram_action(conn, signal_id, "later")
        _answer_callback(token, callback_query["id"], "Notiert -- kein automatischer Reminder im Erstentwurf.")

    elif action == "execute":
        proposal = signal_row["trade_proposal"]
        technical = signal_row["technical"]
        if not proposal:
            _answer_callback(token, callback_query["id"], "Kein Trade-Vorschlag zu diesem Signal.")
            return

        # Positionsgroesse in Basiswaehrung: (Konto-Groesse * size_pct/100) / Stop-Distanz.
        # ACCOUNT_SIZE muss der Kontoinhaber in .env eintragen -- ohne das keine Berechnung.
        account_size = os.environ.get("ACCOUNT_SIZE_QUOTE_CCY")
        if not account_size:
            _answer_callback(token, callback_query["id"],
                              "ACCOUNT_SIZE_QUOTE_CCY nicht in .env gesetzt -- Ausfuehrung abgebrochen.")
            return

        risk_amount = float(account_size) * (proposal["size_pct_of_account"] / 100)
        stop_distance = abs(proposal["entry"] - proposal["stop"])
        amount_base = round(risk_amount / stop_distance, 6) if stop_distance > 0 else 0

        result = execution.place_order(
            exchange_id=os.environ.get("EXCHANGE", "kraken"),
            symbol=technical["symbol"],
            direction=technical["candidate"],
            amount_base_currency=amount_base,
            stop_price=proposal["stop"],
            target_price=proposal["target"],
        )
        db.record_telegram_action(conn, signal_id, "execute", result=result.detail)
        _answer_callback(token, callback_query["id"], f"{result.status}: {result.detail[:150]}")


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN/CHAT_ID nicht gesetzt -- telegram_listener braucht echten Telegram-Zugang.")
        return

    admin_id = _admin_id()
    print("Telegram-Listener gestartet (Ctrl+C zum Beenden). "
          f"LIVE_TRADING_ENABLED={execution.live_trading_enabled()} · "
          + (f"Admin-Rolle aktiv (User-ID {admin_id})" if admin_id else
             "KEINE Rollen-Trennung (ADMIN_TELEGRAM_USER_ID nicht gesetzt) -- jeder wird direkt beantwortet"))
    offset = 0
    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                cq = update.get("callback_query")
                if cq:
                    cq_from = cq.get("from", {})
                    print(f"[{datetime.now(timezone.utc).isoformat()}] Callback von "
                          f"{cq_from.get('first_name') or cq_from.get('username') or 'Unbekannt'} "
                          f"({cq_from.get('id')}): {cq.get('data')!r}")
                    handle_callback(token, cq["message"]["chat"]["id"], cq)
                    continue
                msg = update.get("message")
                text = str(msg.get("text", "")) if msg else ""
                if not text or not msg:
                    continue
                msg_chat_id = msg["chat"]["id"]
                sender = msg.get("from", {})
                from_id = sender.get("id", 0)
                from_name = sender.get("first_name") or sender.get("username") or "Unbekannt"
                # Bisher gab es HIER keine einzige Log-Zeile fuer eingehende Nachrichten
                # (die ganze Datei hatte nur 4 print()s ueberhaupt) -- deshalb war die
                # Fehlersuche am 04.09. reines Raten. Jetzt: jede eingehende Nachricht
                # inkl. Chat-ID, damit ein Gruppen-Chat-ID sich auch ohne Telegram-UI
                # aus den Logs ablesen laesst.
                print(f"[{datetime.now(timezone.utc).isoformat()}] Nachricht von {from_name} "
                      f"({from_id}) in Chat {msg_chat_id}: {text!r}")
                if text.startswith("/"):
                    handle_command(token, msg_chat_id, text, from_id, from_name)
                else:
                    handle_freetext(token, msg_chat_id, text, from_id, from_name)
        except requests.exceptions.RequestException as exc:
            print(f"[FEHLER] Telegram-Poll: {exc}")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nBeendet.")
            break


if __name__ == "__main__":
    main()
