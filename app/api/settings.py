"""Datos de la app, idioma y ajustes."""
from __future__ import annotations

import os

from app import __version__, i18n
from app.connectors import registry
from app.i18n import t as tr
from app.storage import prefs

from app.api.base import ApiBase


# Lo que se muestra en Ajustes › Acerca de (verificar al actualizar requirements.txt)
LICENSES = [
    {"name": "tidalapi", "license": "LGPL-3.0", "url": "https://github.com/tamland/python-tidal"},
    {"name": "ytmusicapi", "license": "MIT", "url": "https://github.com/sigma67/ytmusicapi"},
    {"name": "pywebview", "license": "BSD-3-Clause", "url": "https://github.com/r0x0r/pywebview"},
]


class SettingsApi(ApiBase):

    def info(self) -> dict:
        return {"version": __version__, "data_dir": str(self._data_dir), "licenses": LICENSES,
                "language": i18n.get_language()}


    def ui_strings(self) -> dict:
        """Los textos de la ventana en el idioma actual (la UI los pide al arrancar)."""
        return {"language": i18n.get_language(), "strings": i18n.ui_strings()}

    def languages(self) -> dict:
        return {"current": i18n.get_language(), "available": i18n.language_names()}

    def set_language(self, code: str) -> dict:
        if code not in i18n.LANGUAGES:
            return {"error": tr("err.bad_language")}
        if self._jobs.busy():
            return {"error": tr("err.busy", kind=tr(f"job.{self._jobs.busy().kind}"))}
        p = self._prefs()
        p["language"] = code
        self._save_prefs(p)
        i18n.set_language(code)
        return {"ok": True, "language": code}


    def settings(self) -> dict:
        p = self._prefs()
        return {"score_ok": p["score_ok"], "score_min": p["score_min"],
                "defaults": {"score_ok": prefs.DEFAULTS["score_ok"], "score_min": prefs.DEFAULTS["score_min"]},
                "limits": prefs.threshold_limits()}

    def save_thresholds(self, score_ok, score_min) -> dict:
        try:
            ok, mn = prefs.validate_thresholds(score_ok, score_min)
        except ValueError as e:
            return {"error": str(e)}
        p = self._prefs()
        p["score_ok"], p["score_min"] = ok, mn
        self._save_prefs(p)
        return {"ok": True, "score_ok": ok, "score_min": mn}

    def ui_ready(self) -> dict:
        """La UI avisa que cargó (lo usa la prueba automática de arranque)."""
        cb = getattr(self, "_on_ready", None)
        if cb:
            cb()
        return {"ok": True}

    def open_data_dir(self) -> dict:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self._data_dir)  # type: ignore[attr-defined]  (solo Windows)
        return {"ok": True}

    def open_url(self, url: str) -> dict:
        if not registry.is_allowed_link(url):
            return {"error": tr("err.only_music_links")}
        self._open_url(url, self._browser())
        return {"ok": True}
