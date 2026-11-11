"""Lo que comparten todas las partes de la API: el estado, los servicios y las tareas en segundo plano."""
from __future__ import annotations

import itertools
import logging
import os
from pathlib import Path
from typing import Callable

from app import i18n
from app.auth import browsers as browsers_mod
from app.auth.browsers import Browser
from app.connectors import registry
from app.connectors.base import Connector, ConnectorError
from app.core.transfer import TransferResult
from app.i18n import t as tr
from app.jobs import BusyError, Jobs, unexpected_message
from app.storage import paths, prefs
from app.storage.history import History

log = logging.getLogger(__name__)


KEEP_RESULTS = 5     # transferencias y búsquedas de repetidos que se recuerdan para actuar sobre ellas


def _remember(store: dict, key: str, value) -> None:
    """Guarda sin crecer para siempre: la UI solo actúa sobre lo último."""
    store[key] = value
    while len(store) > KEEP_RESULTS:
        del store[next(iter(store))]


def _artist(s) -> str:
    return ", ".join(s.artists) if s is not None else ""


class ApiBase:
    def __init__(self, connectors: dict[str, Connector], history: History, prefs_path: Path, data_dir: Path,
                 list_browsers: Callable[[], list[Browser]] = browsers_mod.installed_browsers,
                 open_url: Callable[[str, Browser | None], None] = browsers_mod.open_url,
                 save_dialog: Callable[[str], str | None] | None = None,
                 open_file: Callable[[Path], None] | None = None,
                 system_language: Callable[[], str] = i18n.system_language):
        self._save_dialog = save_dialog          # main.py pone el diálogo "Guardar como" de la ventana
        self._open_file = open_file or (lambda p: os.startfile(p))  # type: ignore[attr-defined]
        self._last_export: Path | None = None
        self._connectors = connectors
        self._history = history
        self._prefs_path = Path(prefs_path)
        self._data_dir = Path(data_dir)
        self._list_browsers = list_browsers
        self._open_url = open_url
        self._jobs = Jobs()
        self._transfers: dict[str, TransferResult] = {}
        self._scans: dict[str, dict] = {}
        self._ids = itertools.count(1)
        i18n.set_language(self._resolve_language(system_language))

    def _resolve_language(self, system_language: Callable[[], str]) -> str:
        """El elegido en Ajustes. Si nunca se eligió: castellano para quien ya usaba la app (antes
        solo existía en castellano) y el idioma de Windows para una instalación nueva."""
        chosen = self._prefs().get("language")
        if chosen in i18n.LANGUAGES:
            return chosen
        if self._prefs_path.exists():
            return "es"
        lang = system_language()
        return lang if lang in i18n.LANGUAGES else "en"

    @classmethod
    def create(cls) -> "ApiBase":
        return cls(registry.build_connectors(), History(paths.history_file()), paths.prefs_file(), paths.data_dir())


    def _connector(self, key: str) -> Connector:
        c = self._connectors.get(key)
        if c is None:
            raise ConnectorError(tr("err.service_unavailable"))
        return c

    def _start(self, kind: str, fn) -> dict:
        def watched(emit, cancel):
            # Mientras corre la tarea, cualquier espera de un servicio se ve en pantalla y se puede cancelar
            # (corre una tarea por vez: no se pisan)
            for c in self._connectors.values():
                c.listen(emit, cancel)
            try:
                return fn(emit, cancel)
            finally:
                for c in self._connectors.values():
                    c.listen(None, None)
        try:
            return {"job": self._jobs.start(kind, watched)}
        except BusyError as e:
            return {"error": str(e)}

    def _prefs(self) -> dict:
        return prefs.load(self._prefs_path)

    def _save_prefs(self, p: dict) -> None:
        """Guarda siempre el idioma en uso: si no, una instalación nueva en inglés volvería a
        castellano la próxima vez (el archivo ya existiría, sin idioma)."""
        p["language"] = p.get("language") or i18n.get_language()
        prefs.save(self._prefs_path, p)

    def _browser(self, installed: list[Browser] | None = None) -> Browser | None:
        """El navegador elegido, si sigue instalado. Si lo desinstalaron, None: se vuelve a proponer uno."""
        b = self._prefs().get("browser")
        if not (b and b.get("exe")):
            return None
        installed = self._list_browsers() if installed is None else installed
        return next((x for x in installed if str(x.exe).lower() == str(b["exe"]).lower()), None)

    @staticmethod
    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ConnectorError as e:
            return {"error": str(e)}
        except Exception as e:   # nunca dejar a la UI sin respuesta
            log.exception("Error en la API")
            return {"error": unexpected_message(e)}


    def poll(self, job_id: str, since: int = 0) -> dict:
        return self._jobs.poll(job_id, int(since or 0))

    def cancel(self, job_id: str) -> dict:
        self._jobs.cancel(job_id)
        return {"ok": True}
