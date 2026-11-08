"""Firefox: las cookies se leen del perfil del usuario (Firefox no las cifra en Windows)."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from app.auth.cdp_login import LoginError
from app.core.models import Cancelled
from app.i18n import t as tr

FIREFOX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


def cookie_dbs() -> list[Path]:
    roots = [Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles",
             Path.home() / ".mozilla" / "firefox"]
    dbs = [db for r in roots if r.is_dir() for db in r.glob("*/cookies.sqlite")]
    return sorted(dbs, key=lambda d: d.stat().st_mtime, reverse=True)   # el perfil más usado primero


def read_cookies(domain: str, wanted: set[str], dbs: list[Path] | None = None) -> dict[str, str]:
    """Cookies de `domain` del primer perfil que tenga alguna de `wanted`. {} si ninguno."""
    for db in cookie_dbs() if dbs is None else dbs:
        rows = []
        with tempfile.TemporaryDirectory() as tmp:
            # Firefox tiene el archivo abierto: se copia (junto con el -wal, que tiene lo más reciente)
            try:
                for suffix in ("", "-wal"):
                    src = db.with_name(db.name + suffix)
                    if src.exists():
                        shutil.copy2(src, Path(tmp) / src.name)
                con = sqlite3.connect(Path(tmp) / db.name)
                rows = con.execute(
                    "SELECT name, value FROM moz_cookies WHERE host LIKE ? "
                    "ORDER BY originAttributes != '', expiry", (f"%{domain}",)).fetchall()  # sin contenedores, primero
                con.close()
            except Exception:
                continue
        jar: dict[str, str] = {}
        for name, value in rows:
            jar.setdefault(name, value)
        if wanted & jar.keys():
            return jar
    return {}


def login_and_read_cookies(login_url: str, domain: str, wanted: set[str], timeout: float = 600,
                           cancel: threading.Event | None = None, exe: Path | None = None) -> tuple[dict[str, str], str]:
    """Usa la sesión ya abierta en Firefox; si no hay, abre el login *en Firefox* y espera a que aparezca.
    (Con `webbrowser.open` se abría en el predeterminado: si era otro, la sesión nunca llegaba a Firefox.)"""
    jar = read_cookies(domain, wanted)
    if jar:
        return jar, FIREFOX_UA
    opened = False
    if exe is not None:
        try:
            subprocess.Popen([str(exe), login_url])
            opened = True
        except OSError:
            pass
    if not opened:
        webbrowser.open(login_url)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (cancel or threading.Event()).wait(3):       # espera 3 s, o corta si se canceló
            raise Cancelled()
        jar = read_cookies(domain, wanted)
        if jar:
            return jar, FIREFOX_UA
    raise LoginError(tr("err.login_timeout"))
