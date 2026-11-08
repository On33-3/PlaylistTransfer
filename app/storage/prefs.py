"""Preferencias del usuario (navegador elegido, umbrales). Primer uso: valores por defecto."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.matching import SCORE_MIN, SCORE_OK

DEFAULTS: dict = {
    "browser": None,          # {"name", "kind", "exe"}: se pregunta en el primer login
    "score_ok": SCORE_OK,
    "score_min": SCORE_MIN,
}


def load(path: Path) -> dict:
    """Las preferencias guardadas sobre los valores por defecto. Si no hay archivo, o está roto,
    devuelve los valores por defecto (sin pisar el archivo roto)."""
    prefs = dict(DEFAULTS)
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return prefs
    if isinstance(saved, dict):
        prefs.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return prefs


def save(path: Path, prefs: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({k: prefs[k] for k in DEFAULTS if k in prefs}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)     # nunca queda un archivo a medio escribir
