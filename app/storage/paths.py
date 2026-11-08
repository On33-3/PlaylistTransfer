"""Dónde guarda la app los datos del usuario: %LOCALAPPDATA%\\PlaylistTransfer (fuera del repo)."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "PlaylistTransfer"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def tidal_session_file() -> Path:
    return data_dir() / "tidal_session.json"


def yt_browser_file() -> Path:
    return data_dir() / "browser.json"


def prefs_file() -> Path:
    return data_dir() / "preferencias.json"


def history_file() -> Path:
    return data_dir() / "historial.sqlite"


def browser_profiles_dir() -> Path:
    """Perfiles de navegador propios de la app (guardan la sesión de Google)."""
    return data_dir() / "browser-profiles"
