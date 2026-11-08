"""Preferencias del usuario (navegador elegido, umbrales). Primer uso: valores por defecto."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.matching import SCORE_MIN, SCORE_OK
from app.i18n import t as tr

DEFAULTS: dict = {
    "browser": None,          # {"name", "kind", "exe"}: se pregunta en el primer login
    "score_ok": SCORE_OK,
    "score_min": SCORE_MIN,
    "add_doubtful": True,     # agregar también los "?" (pueden ser otra versión); el usuario lo elige en Transferir
    "language": None,         # "es" | "en"; None = todavía no se eligió (ver Api._resolve_language)
}


def threshold_limits() -> dict:
    """Rangos permitidos para los umbrales.

    "Agregar sin dudar" tiene que quedar por encima del tope de "otra versión" (en vivo, remix):
    si no, esas versiones pasarían como OK. "Agregar para revisar" tiene que quedar por encima del
    tope de "otra canción" (otro artista, cover): si no, se agregarían como dudosas."""
    from app.core.matching import NO_MATCH_CAP, VERSION_CAP
    return {"ok_min": round(VERSION_CAP + 0.01, 2), "ok_max": 0.99,
            "min_min": round(NO_MATCH_CAP + 0.01, 2), "gap": 0.05}


def validate_thresholds(score_ok, score_min) -> tuple[float, float]:
    lim = threshold_limits()
    try:
        ok, mn = round(float(score_ok), 2), round(float(score_min), 2)
    except (TypeError, ValueError):
        raise ValueError(tr("err.th_not_numbers"))
    if not lim["ok_min"] <= ok <= lim["ok_max"]:
        raise ValueError(tr("err.th_ok_range", ok_min=f"{lim['ok_min']:.2f}", ok_max=f"{lim['ok_max']:.2f}"))
    if mn < lim["min_min"]:
        raise ValueError(tr("err.th_min_low", min_min=f"{lim['min_min']:.2f}"))
    if mn > round(ok - lim["gap"], 2):
        raise ValueError(tr("err.th_gap", gap=f"{lim['gap']:.2f}"))
    return ok, mn


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
    try:   # umbrales editados a mano y fuera de rango: se usan los de fábrica
        prefs["score_ok"], prefs["score_min"] = validate_thresholds(prefs["score_ok"], prefs["score_min"])
    except ValueError:
        prefs["score_ok"], prefs["score_min"] = DEFAULTS["score_ok"], DEFAULTS["score_min"]
    return prefs


def save(path: Path, prefs: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({k: prefs[k] for k in DEFAULTS if k in prefs}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)     # nunca queda un archivo a medio escribir
