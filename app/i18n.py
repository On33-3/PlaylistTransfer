"""The app's languages (Spanish and English). Every text lives in app/locales/<language>.json:
"ui" for the window and "py" for the messages Python builds (errors, CSV, file names).

Adding a language = adding a .json with the same keys (a test checks it).
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
    """A message in the current language. With `n`, it uses the _one / _other variant if there is one."""
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
    """The Windows language: "es" if it's Spanish (any country), otherwise "en"."""
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()   # type: ignore[attr-defined]
        return "es" if (langid & 0x3FF) == 0x0A else "en"             # 0x0A = LANG_SPANISH
    except Exception:
        import locale
        code = (locale.getlocale()[0] or "").lower()
        return "es" if code.startswith(("es", "spanish")) else "en"
