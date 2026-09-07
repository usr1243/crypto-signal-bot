"""
PLAN wild-booping-firefly.md, Stufe C10: duenner REST-Client fuer freqtrades
eigene API (/api/v1/*) -- das Gegenstueck zu execution.py fuer das NEUE
freqtrade-System, aber bewusst nur fuer Admin-Steuerbefehle (Status/Pause/
Not-Aus). Das Platzieren/Verwalten von Orders bleibt vollstaendig freqtrades
eigener Sache (genau der Grund fuer den Umstieg in Stufe B, siehe PLAN) --
dieser Client mischt sich da nicht ein.

Contract gegen den echten freqtrade-Quellcode verifiziert, nicht geraten:
- Auth: `http_basic_or_jwt_token` (rpc/api_server/api_auth.py) akzeptiert
  normale HTTP-Basic-Auth direkt auf jedem Endpunkt -- kein Login-/Token-Tanz
  fuer einen internen Admin-Client noetig.
- /pause setzt State.PAUSED (keine neuen Einstiege, offene Positionen bleiben
  ueberwacht) -- exaktes Gegenstueck zu unserem bisherigen db.set_paused().
- /start setzt State.RUNNING zurueck -- das ist "resume", NICHT /reload_config
  (das laedt nur die Konfigurationsdatei neu, aendert den Pause-Zustand nicht;
  ein naheliegender, aber falscher erster Griff, siehe rpc.py:_rpc_reload_config).
- /forceexit erwartet {"tradeid": <id oder "all">} (ForceExitPayload in
  api_schemas.py) -- schliesst eine oder alle Positionen sofort.
- /stop ist der harte Stopp (State.STOPPED, freqtrade handelt gar nicht mehr,
  auch keine offenen Positionen mehr ueberwacht) -- fuer den eigentlichen
  Not-Aus (PLAN C4) braucht es forceexit("all") GEFOLGT von stop(), siehe
  panic() unten. Nur stop() allein liesse offene Positionen ungeschuetzt
  zurueck, genau der Fehler, den Stufe C beheben sollte.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests


class FreqtradeAPIError(RuntimeError):
    """freqtrade-API antwortete mit einem Fehler (HTTP != 2xx) oder war nicht erreichbar."""


@dataclass
class FreqtradeClient:
    base_url: str  # z.B. "http://tradingbot-freqtrade-dryrun:8080" (Docker-Servicename)
    username: str
    password: str
    timeout: float = 10.0

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        url = f"{self.base_url.rstrip('/')}/api/v1/{path.lstrip('/')}"
        try:
            resp = requests.request(
                method, url, auth=(self.username, self.password),
                timeout=self.timeout, **kwargs,
            )
        except requests.exceptions.RequestException as exc:
            raise FreqtradeAPIError(f"freqtrade-API nicht erreichbar ({url}): {exc}") from exc
        if not resp.ok:
            raise FreqtradeAPIError(
                f"freqtrade-API-Fehler {resp.status_code} bei {path}: {resp.text[:300]}"
            )
        return resp.json()

    def ping(self) -> bool:
        """Erreichbarkeits-Check ohne Auth-Anforderung (siehe api_v1.py: /ping ist oeffentlich)."""
        try:
            self._request("GET", "ping")
            return True
        except FreqtradeAPIError:
            return False

    def status(self) -> list[dict]:
        """Offene Positionen -- Gegenstueck zu paper_trading.get_open_trades()."""
        return self._request("GET", "status")

    def balance(self) -> dict:
        return self._request("GET", "balance")

    def pause(self) -> dict:
        return self._request("POST", "pause")

    def resume(self) -> dict:
        return self._request("POST", "start")

    def force_exit(self, trade_id: int | str = "all") -> dict:
        return self._request("POST", "forceexit", json={"tradeid": str(trade_id)})

    def hard_stop(self) -> dict:
        """Stoppt den Bot komplett -- danach werden auch offene Positionen NICHT
        mehr ueberwacht. Fast nie das, was man einzeln will -- siehe panic()."""
        return self._request("POST", "stop")

    def panic(self) -> dict:
        """Der eigentliche Not-Aus (PLAN C4): erst alle Positionen sofort schliessen,
        DANACH den Bot stoppen -- in dieser Reihenfolge, sonst blieben offene
        Positionen beim Stopp ungeschuetzt zurueck (kein Monitoring mehr)."""
        exit_result = self.force_exit("all")
        stop_result = self.hard_stop()
        return {"force_exit": exit_result, "stop": stop_result}
