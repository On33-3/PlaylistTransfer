"""Idiomas de la app (español e inglés). Todos los textos viven en app/locales/<idioma>.json:
"ui" para la ventana y "py" para los mensajes que arma Python (errores, CSV, nombres de archivo).

Agregar un idioma = agregar un .json con las mismas claves (hay un test que lo verifica).
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

LANGUAGES = ("es", "en")
DEFAULT = "es"
_current = DEFAULT


def _locales_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))   # PyInstaller
    return base / "app" / "locales"


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict:
    return json.loads((_locales_dir() / f"{lang}.json").read_text(encoding="utf-8"))


def set_language(lang: str) -> None:
    global _current
    if lang not in LANGUAGES:
        raise ValueError(lang)
    _current = lang


def get_language() -> str:
    return _current


def t(key: str, n: int | None = None, **values) -> str:
    """Mensaje en el idioma actual. Con `n`, usa la variante _one / _other si existe."""
    msgs = catalog(_current)["py"]
    if n is not None:
        key_n = f"{key}_one" if n == 1 else f"{key}_other"
        if key_n in msgs:
            key = key_n
        values.setdefault("n", n)
    text = msgs.get(key) or catalog(DEFAULT)["py"].get(key) or key
    return text.format(**values) if values else text


def ui_strings(lang: str | None = None) -> dict:
    return catalog(lang or _current)["ui"]


def language_names() -> list[dict]:
    return [{"code": code, "name": catalog(code)["language_name"]} for code in LANGUAGES]


def system_language() -> str:
    """El idioma de Windows: "es" si está en castellano (cualquier país), si no "en"."""
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()   # type: ignore[attr-defined]
        return "es" if (langid & 0x3FF) == 0x0A else "en"             # 0x0A = LANG_SPANISH
    except Exception:
        import locale
        code = (locale.getlocale()[0] or "").lower()
        return "es" if code.startswith(("es", "spanish")) else "en"
