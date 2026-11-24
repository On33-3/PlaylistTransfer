"""Los servicios: estado, navegador, conectar y playlists."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from app.auth import browsers as browsers_mod
from app.connectors import registry
from app.connectors.base import ConnectorError, Status
from app.i18n import t as tr

from app.api.base import ApiBase

log = logging.getLogger(__name__)

SLOW_STATUS = 5     # segundos: un servicio que tarda más en decir si hay sesión queda anotado en el log


class ServicesApi(ApiBase):

    def services(self) -> list[dict]:
        """Los servicios en el orden en que se muestran, y cuáles se pueden usar. Es inmediato (no
        consulta a nadie): la UI lo pide al arrancar, antes de `status()`."""
        out = []
        for s in registry.SERVICES:
            c = self._connectors.get(s.key)
            d = {"key": s.key, "name": s.name, "available": c is not None}
            if getattr(c, "needs_setup", False):          # Spotify: el usuario carga su Client ID
                d["setup"] = c.setup_info()
            out.append(d)
        return out

    def set_client_id(self, key: str, client_id: str) -> dict:
        """Guarda el Client ID de la app del usuario (Spotify). Después, la UI conecta."""
        def work():
            c = self._connector(key)
            if not getattr(c, "needs_setup", False):
                raise ConnectorError(tr("err.service_unavailable"))
            return {"ok": True, "client_id": c.set_client_id(str(client_id or "")), "status": c.status().value}
        return self._safe(work)

    def _status_of(self, s) -> dict:
        c = self._connectors.get(s.key)
        if c is None:
            return {"key": s.key, "name": s.name, "status": "soon"}
        started = time.monotonic()
        try:
            st = c.status().value
        except Exception as e:
            log.warning("No pude ver el estado de %s: %s", s.name, e)
            st = Status.EXPIRED.value
        took = time.monotonic() - started
        if took > SLOW_STATUS:          # pasó al arrancar (2026-11-24) y no quedaba dicho cuál fue
            log.warning("%s tardó %.0f s en contestar si hay sesión (%s)", s.name, took, st)
        return {"key": s.key, "name": s.name, "status": st}

    def status(self) -> list[dict]:
        """Cada servicio se consulta en su propio hilo (cada uno es un pedido por internet): al abrir la
        app, tarda lo que el más lento y no la suma."""
        with ThreadPoolExecutor(max_workers=len(registry.SERVICES)) as pool:
            return list(pool.map(self._status_of, registry.SERVICES))

    def browsers(self) -> dict:
        found = self._list_browsers()
        chosen = self._browser(found)
        suggested = browsers_mod.suggested_browser(found)
        return {"browsers": [{"name": b.name, "kind": b.kind, "exe": str(b.exe), "default": b.default} for b in found],
                "chosen": str(chosen.exe) if chosen else None,
                "suggested": str(suggested.exe) if suggested else None}

    def login_steps(self) -> dict:
        """{servicio: [claves de los pasos]} para el navegador que se va a usar (el elegido o el propuesto).
        Ajustes los muestra antes de conectar, así se leen antes de que se abra nada."""
        found = self._list_browsers()
        browser = self._browser(found) or browsers_mod.suggested_browser(found)
        return {s.key: self._connectors[s.key].login_steps(browser)
                for s in registry.SERVICES if s.key in self._connectors}

    def choose_browser(self, exe: str) -> dict:
        b = next((b for b in self._list_browsers() if str(b.exe).lower() == str(exe).lower()), None)
        if b is None:
            return {"error": tr("err.browser_not_found")}
        p = self._prefs()
        p["browser"] = {"name": b.name, "kind": b.kind, "exe": str(b.exe)}
        self._save_prefs(p)
        return {"ok": True, "name": b.name}

    def connect(self, key: str) -> dict:
        def work(emit, cancel):
            c = self._connector(key)
            installed = self._list_browsers()
            browser = self._browser(installed)
            if browser is None:
                suggested = browsers_mod.suggested_browser(installed)
                if suggested is None:
                    raise ConnectorError(tr("err.choose_browser_first"))
                self.choose_browser(str(suggested.exe))
                browser = suggested
            emit({"type": "login", "service": c.name, "browser": browser.name, "steps": c.login_steps(browser)})
            c.connect(browser, cancel=cancel)
            return {"status": c.status().value}
        return self._start("login", work)

    def disconnect(self, key: str) -> dict:
        return self._safe(lambda: (self._connector(key).disconnect(), {"ok": True})[1])

    def playlists(self, key: str) -> list[dict] | dict:
        def work():
            return [{"id": p.id, "name": p.name, "count": p.count} for p in self._connector(key).list_playlists()]
        return self._safe(work)
