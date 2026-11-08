"""Navegadores instalados en Windows y el predeterminado (registro de Windows)."""
from __future__ import annotations

import re
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

CHROMIUM = {"chrome": "Chrome", "msedge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi",
            "opera": "Opera", "launcher": "Opera", "chromium": "Chromium", "arc": "Arc", "thorium": "Thorium"}

# Chrome es el navegador del trabajo del usuario: nunca se elige solo (ver CLAUDE.md)
NOT_BY_DEFAULT = {"chrome"}


@dataclass
class Browser:
    name: str            # "Vivaldi", "Google Chrome"...
    kind: str            # "vivaldi", "chrome", "firefox"...
    exe: Path
    default: bool = False

    @property
    def is_firefox(self) -> bool:
        return self.kind == "firefox"


def exe_from_command(cmd: str) -> Path | None:
    """'"C:\\...\\vivaldi.exe" --single-argument %1' → Path('C:\\...\\vivaldi.exe')."""
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd or "")
    return Path(m.group(1) or m.group(2)) if m else None


def kind_of(exe: Path | str) -> str:
    return PureWindowsPath(str(exe)).stem.lower()


def default_browser() -> tuple[str, Path | None]:
    """('vivaldi' | 'firefox' | ..., ruta del .exe) del navegador predeterminado. ('', None) si no se sabe."""
    try:
        import winreg
    except ImportError:          # no es Windows
        return "", None
    base = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https"
    progid = None
    for sub in (r"\UserChoiceLatest\ProgId", r"\UserChoiceLatest", r"\UserChoice"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + sub) as k:
                progid = winreg.QueryValueEx(k, "ProgId")[0]
                break
        except OSError:
            continue
    if not progid:
        return "", None
    exe = None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as k:
            exe = exe_from_command(winreg.QueryValueEx(k, "")[0])
    except OSError:
        pass
    stem = kind_of(exe) if exe else ""
    if "firefox" in progid.lower() or stem == "firefox":
        return "firefox", exe
    return (stem or progid.lower()), exe


def installed_browsers() -> list[Browser]:
    """Navegadores registrados en Windows (los de "Aplicaciones predeterminadas"), el predeterminado primero."""
    try:
        import winreg
    except ImportError:
        return []
    found: dict[str, Browser] = {}
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for path in (r"SOFTWARE\Clients\StartMenuInternet", r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet"):
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(key, sub) as k:
                        name = winreg.QueryValueEx(k, "")[0] or sub
                except OSError:
                    name = sub
                try:
                    with winreg.OpenKey(key, sub + r"\shell\open\command") as k:
                        exe = exe_from_command(winreg.QueryValueEx(k, "")[0])
                except OSError:
                    continue
                if not exe or not exe.exists():
                    continue
                kind = kind_of(exe)
                if kind in CHROMIUM or kind == "firefox":
                    found.setdefault(str(exe).lower(), Browser(name, kind, exe))
    _, default_exe = default_browser()
    return sort_browsers(list(found.values()), default_exe)


def sort_browsers(browsers: list[Browser], default_exe: Path | None) -> list[Browser]:
    for b in browsers:
        b.default = bool(default_exe) and str(b.exe).lower() == str(default_exe).lower()
    return sorted(browsers, key=lambda b: (not b.default, b.name.lower()))


def suggested_browser(browsers: list[Browser]) -> Browser | None:
    """El que se propone si el usuario todavía no eligió: el predeterminado, salvo Chrome."""
    for b in browsers:
        if b.kind not in NOT_BY_DEFAULT:
            return b
    return None


def open_url(url: str, browser: Browser | None = None) -> None:
    """Abre un link en el navegador elegido (en su perfil normal); si no hay, en el predeterminado."""
    if browser and browser.exe.exists():
        try:
            subprocess.Popen([str(browser.exe), url])
            return
        except OSError:
            pass
    webbrowser.open(url)
