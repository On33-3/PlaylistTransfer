"""Borrar todo lo que la app guardó en esta PC (Ajustes › Borrar todo): sesiones de cada servicio, historial,
preferencias, búsquedas guardadas, perfiles de navegador propios (tienen la sesión de Google) y el log.
La app vuelve a quedar como la primera vez. Las playlists de los servicios no se tocan.

Lo que está en uso mientras la app sigue abierta (la carpeta de la ventana, `webview`) no se puede borrar en
Windows: se devuelve en la lista y se vuelve a intentar al cerrar la app (`app/main.py`).
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def _close_logs_inside(folder: Path) -> None:
    """Windows no deja borrar un archivo abierto: se cierra el log (se vuelve a abrir solo al escribir)."""
    for h in logging.getLogger().handlers:
        name = getattr(h, "baseFilename", None)
        if name and Path(name).resolve().is_relative_to(folder.resolve()):
            h.close()


def wipe(folder: Path) -> list[str]:
    """Borra el contenido de `folder`. Devuelve lo que no se pudo borrar (en uso)."""
    folder = Path(folder)
    if not folder.exists():
        return []
    _close_logs_inside(folder)
    left = []
    for item in sorted(folder.iterdir()):
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            # Una carpeta en uso puede quedar a medias: se borra lo que se pueda y se anota
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            if item.exists():
                left.append(item.name)
    return left
