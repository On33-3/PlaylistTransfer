"""
Pasa temas de una playlist de Tidal a YouTube Music, o al revés, desde tu propia compu.
Podés elegir el destino: una playlist nueva, una existente o varias a la vez.
Los temas que ya están en la playlist de destino no se repiten.

Usa dos librerías no oficiales:
  - tidalapi    (Tidal)
  - ytmusicapi  (YouTube Music)

La forma más simple: doble clic en "Transferir playlists.bat" (instala lo necesario la
primera vez y muestra menús). La primera vez se abre una ventana para iniciar sesión en
Google (YouTube Music) y un link para autorizar Tidal; después ya no hace falta.

Uso por consola:
    py transfer_playlist.py                         (menús para todo)
    py transfer_playlist.py tidal2yt [URL o ID de origen] [opciones]
    py transfer_playlist.py yt2tidal [URL o ID de origen] [opciones]
    py transfer_playlist.py setup-yt [--manual]     (volver a conectar YouTube Music)

  Si no pasás la playlist de origen, el script te muestra tus playlists para elegir.

Destino (si no pasás ninguna de estas opciones, te muestra un menú):
    --to URL_O_ID     agregar a esa playlist existente (se puede repetir: --to A --to B)
    --new             crear una playlist nueva
    --name "Nombre"   nombre de la playlist nueva (por defecto, el mismo del origen)

Otras opciones:
    --dry-run         solo busca coincidencias y genera el reporte; no modifica nada
    --public          (YouTube Music) la playlist nueva se crea pública; por defecto es privada

Siempre genera reporte_<fecha>.csv con cada tema, su coincidencia y el puntaje.
No compartas tidal_session.json, browser.json ni oauth.json: son tus credenciales.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TIDAL_SESSION_FILE = Path("tidal_session.json")
YT_BROWSER_FILE = Path("browser.json")
YT_OAUTH_FILE = Path("oauth.json")
YT_PROFILE_DIR = Path(".yt-login")   # perfiles de navegador propios del script (guardan tu sesión de Google)
YT_LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&passive=true&continue="
                "https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26app%3Ddesktop"
                "%26next%3Dhttps%253A%252F%252Fmusic.youtube.com%252F")
YT_LOGIN_TIMEOUT = 600   # segundos para iniciar sesión en la ventana
PREFS_FILE = Path("preferencias.json")   # recuerda qué navegador elegiste

SCORE_OK = 0.75      # desde acá se agrega sin dudas
SCORE_MIN = 0.60     # entre MIN y OK se agrega, pero queda marcado como DUDOSO
SEARCH_PAUSE = 0.3   # segundos entre búsquedas, para no saturar las APIs
BATCH = 50           # temas por pedido al agregar


# ---------------------------------------------------------------- modelo

@dataclass
class Song:
    title: str
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    duration: int | None = None   # segundos
    isrc: str | None = None
    id: str | None = None         # id de Tidal o videoId de YouTube
    set_id: str | None = None     # (YouTube Music) id de esa aparición en la playlist, para quitarla

    @property
    def artist(self) -> str:
        return self.artists[0] if self.artists else ""

    def label(self) -> str:
        return f"{', '.join(self.artists) or '?'} - {self.title}"


@dataclass
class PlaylistInfo:
    id: str
    name: str
    count: int | None = None


# ---------------------------------------------------------------- matching

_NOISE_PAREN = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|with|remaster(ed)?|official|video|audio|lyrics?|visualizer|hd|4k)\b[^\)\]]*[\)\]]",
    re.I,
)
_NOISE_DASH = re.compile(r"\s+-\s+[^-]*\b(remaster(ed)?|official|video|audio|lyrics?)\b.*$", re.I)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?)\s+.*$", re.I)


def clean(text: str) -> str:
    """Saca ruido típico de títulos: (Official Video), - Remastered 2011, feat. X."""
    text = _NOISE_PAREN.sub(" ", text or "")
    text = _NOISE_DASH.sub(" ", text)
    text = _FEAT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm(text: str) -> str:
    """Minúsculas, sin acentos, sin puntuación (respeta alfabetos no latinos)."""
    text = unicodedata.normalize("NFKD", clean(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        ratio = max(ratio, 0.9)
    return ratio


def score(src: Song, cand: Song) -> float:
    """0 a 1. Pondera título, artista y duración. ISRC idéntico = 1."""
    if src.isrc and cand.isrc and src.isrc.upper() == cand.isrc.upper():
        return 1.0

    t = sim(src.title, cand.title)

    a = 0.0
    for sa in src.artists or [""]:
        for ca in cand.artists or [""]:
            a = max(a, sim(sa, ca))
    # En YouTube a veces el artista viene en el título del video: "Artista - Tema"
    if src.artist and norm(src.artist) in norm(cand.title):
        a = max(a, 0.9)

    if src.duration and cand.duration:
        diff = abs(src.duration - cand.duration)
        d = 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / 27)
    else:
        d = 0.5

    return round(0.5 * t + 0.35 * a + 0.15 * d, 3)


def best_match(src: Song, candidates: list[Song]) -> tuple[Song | None, float]:
    best, best_score = None, 0.0
    for c in candidates:
        s = score(src, c)
        if s > best_score:
            best, best_score = c, s
    return best, best_score


def queries_for(src: Song) -> list[str]:
    title = clean(src.title)
    qs = [f"{src.artist} {title}".strip(), title]
    return list(dict.fromkeys(q for q in qs if q))   # sin repetidos, en orden


# ---------------------------------------------------------------- misma canción, otra versión

_ANY_BRACKETS = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_PROD = re.compile(r"\s+(prod\.?|produced by)\s+.*$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")


def title_keys(song: Song) -> set[str]:
    """Título base, sin lo que cambia entre versiones: (Official Video), [Remastered], (acoustic),
    feat. X, prod. X, '- Remastered 2011'. En YouTube también prueba sin el 'Artista - ' del principio."""
    t = song.title or ""
    variants = [t] + _DASH.split(t, maxsplit=1)
    keys = set()
    for v in variants:
        v = _PROD.sub(" ", _FEAT.sub(" ", _ANY_BRACKETS.sub(" ", v)))
        k = norm(v)
        if k:
            keys.add(k)
    return keys


def _same_artist(a: Song, b: Song) -> bool:
    aa = {norm(x) for x in a.artists if norm(x)}
    bb = {norm(x) for x in b.artists if norm(x)}
    if aa and bb and any(x == y or SequenceMatcher(None, x, y).ratio() >= 0.85 for x in aa for y in bb):
        return True
    ta, tb = norm(a.title), norm(b.title)          # videos tipo "Artista - Tema"
    if any(x in tb for x in aa) or any(y in ta for y in bb):
        return True
    if not aa or not bb:                           # sin artista: que la duración sea casi igual
        return bool(a.duration and b.duration and abs(a.duration - b.duration) <= 5)
    return False


def same_song(a: Song, b: Song) -> bool:
    """¿Es la misma canción aunque sea otra versión (otro video, portada, remaster, acústica)?"""
    if a.id and b.id and a.id == b.id:
        return True
    if not _same_artist(a, b):
        return False
    ka, kb = title_keys(a), title_keys(b)
    return bool(ka & kb) or any(SequenceMatcher(None, x, y).ratio() >= 0.92 for x in ka for y in kb)


def find_duplicates(songs: list[Song]) -> list[tuple[int, Song, Song]]:
    """(posición, copia repetida, la que queda). Queda siempre la que está primero en la playlist."""
    kept: list[Song] = []
    dups = []
    for i, s in enumerate(songs):
        twin = next((k for k in kept if same_song(s, k)), None)
        if twin:
            dups.append((i, s, twin))
        else:
            kept.append(s)
    return dups


# ---------------------------------------------------------------- IDs

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def tidal_playlist_id(value: str) -> str:
    m = _UUID.search(value)
    if not m:
        sys.exit(f"No encontré un ID de playlist de Tidal en: {value}")
    return m.group(0)


def yt_playlist_id(value: str) -> str:
    if "://" in value:
        q = parse_qs(urlparse(value).query)
        if "list" in q:
            return q["list"][0]
        sys.exit(f"La URL de YouTube Music no tiene el parámetro list=: {value}")
    return value.strip()


# ---------------------------------------------------------------- dependencias

def require(module: str):
    """Importa una librería o explica cómo instalarla, en vez de mostrar un traceback."""
    import importlib
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        if e.name and e.name != module:
            raise   # la librería está instalada, pero a ella le falta otra: mostrar el error real
        sys.exit(f"Falta la librería '{module}' en este Python:\n    {sys.executable}\n\n"
                 f"Instalala con el mismo Python que corre el script:\n\n"
                 f"    \"{sys.executable}\" -m pip install -r requirements.txt\n\n"
                 f"(en la carpeta del script) y volvé a correrlo. Más detalles en README.md.")


# ---------------------------------------------------------------- inicio de sesión

def open_login_link(text: str) -> None:
    """tidalapi avisa con un texto que trae el link: lo mostramos y lo abrimos en el navegador."""
    print(text)
    m = re.search(r"(https?://)?(link\.tidal\.com/[A-Za-z0-9]+)", text) or re.search(r"https?://\S+", text)
    if m:
        url = m.group(0).rstrip(".,")
        open_url(url if url.startswith("http") else f"https://{url}")


def open_url(url: str) -> None:
    """Abre un link en el navegador que elegiste (en tu perfil normal); si no, en el predeterminado."""
    b = choose_browser(interactive=False)
    if b and b.exe.exists():
        try:
            subprocess.Popen([str(b.exe), url])
            return
        except OSError:
            pass
    webbrowser.open(url)


def yt_open(YTMusic, show_error: bool = False):
    """Abre browser.json y prueba la sesión con un pedido liviano. Devuelve None si no sirve."""
    try:
        yt = YTMusic(str(YT_BROWSER_FILE))
        yt.get_library_playlists(limit=1)
        return yt
    except Exception as e:
        if show_error:
            print(f"YouTube Music rechazó la sesión: {type(e).__name__}: {e}")
        return None


def sapisid_hash(sapisid: str, origin: str = "https://music.youtube.com") -> str:
    """Header 'authorization' que usa YouTube (y que ytmusicapi necesita para reconocer la sesión)."""
    ts = str(int(time.time()))
    return f"SAPISIDHASH {ts}_{hashlib.sha1(f'{ts} {sapisid} {origin}'.encode()).hexdigest()}"


# ---- navegador predeterminado de Windows

_CHROMIUM = {"chrome": "Chrome", "msedge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi",
             "opera": "Opera", "launcher": "Opera", "chromium": "Chromium", "arc": "Arc", "thorium": "Thorium"}


def default_browser() -> tuple[str, Path | None]:
    """('chrome' | 'firefox' | ..., ruta del .exe) del navegador predeterminado. ('', None) si no se sabe."""
    try:
        import winreg
    except ImportError:          # no es Windows
        return "", None
    base = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https"
    progid = None
    for sub, value in ((r"\UserChoiceLatest\ProgId", "ProgId"), (r"\UserChoiceLatest", "ProgId"),
                       (r"\UserChoice", "ProgId")):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + sub) as k:
                progid = winreg.QueryValueEx(k, value)[0]
                break
        except OSError:
            continue
    if not progid:
        return "", None
    exe = None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd)
        if m:
            exe = Path(m.group(1) or m.group(2))
    except OSError:
        pass
    from pathlib import PureWindowsPath
    stem = PureWindowsPath(str(exe)).stem.lower() if exe else ""
    if "firefox" in progid.lower() or stem == "firefox":
        return "firefox", exe
    return (stem or progid.lower()), exe


@dataclass
class Browser:
    name: str            # "Vivaldi", "Google Chrome"...
    kind: str            # "vivaldi", "chrome", "firefox"...
    exe: Path
    default: bool = False


def _exe_from_command(cmd: str) -> Path | None:
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd or "")
    return Path(m.group(1) or m.group(2)) if m else None


def installed_browsers() -> list[Browser]:
    """Navegadores registrados en Windows (los que aparecen en "Aplicaciones predeterminadas")."""
    try:
        import winreg
    except ImportError:
        return []
    from pathlib import PureWindowsPath
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
                        exe = _exe_from_command(winreg.QueryValueEx(k, "")[0])
                except OSError:
                    continue
                if not exe or not exe.exists():
                    continue
                kind = PureWindowsPath(str(exe)).stem.lower()
                if kind in _CHROMIUM or kind == "firefox":
                    found.setdefault(str(exe).lower(), Browser(name, kind, exe))
    browsers = list(found.values())
    _, default_exe = default_browser()
    for b in browsers:
        b.default = bool(default_exe) and str(b.exe).lower() == str(default_exe).lower()
    browsers.sort(key=lambda b: (not b.default, b.name.lower()))   # el predeterminado primero
    return browsers


def load_prefs() -> dict:
    try:
        return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict) -> None:
    PREFS_FILE.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


def choose_browser(ask_again: bool = False, interactive: bool = True) -> Browser | None:
    """El navegador para abrir las ventanas de inicio de sesión: el que elegiste antes o, si no,
    te pregunta (una sola vez). Con interactive=False solo devuelve el guardado, sin preguntar."""
    saved = load_prefs().get("yt_browser")
    if saved and not ask_again and Path(saved["exe"]).exists():
        return Browser(saved["name"], saved["kind"], Path(saved["exe"]))
    if not interactive:
        return None
    browsers = installed_browsers()
    if not browsers:
        return None
    if len(browsers) == 1:
        chosen = browsers[0]
    else:
        print("\n¿En qué navegador abro las ventanas para iniciar sesión (Tidal y YouTube Music)?")
        for i, b in enumerate(browsers, 1):
            print(f"  {i}. {b.name}{' (predeterminado)' if b.default else ''}")
        while True:
            c = ask("Elegí [1]: ") or "1"
            if c.isdigit() and 1 <= int(c) <= len(browsers):
                chosen = browsers[int(c) - 1]
                break
            print("  Escribí un número de la lista.")
    prefs = load_prefs()
    prefs["yt_browser"] = {"name": chosen.name, "kind": chosen.kind, "exe": str(chosen.exe)}
    save_prefs(prefs)
    print(f"Listo: uso {chosen.name}. Lo podés cambiar desde el menú principal.")
    return chosen


def build_browser_json(jar: dict[str, str], user_agent: str) -> bool:
    """Arma browser.json a partir de las cookies de YouTube."""
    ytmusicapi = require("ytmusicapi")
    sapisid = jar.get("__Secure-3PAPISID") or jar.get("SAPISID")
    if not sapisid:
        print("No encontré la sesión de Google. ¿Iniciaste sesión?")
        return False
    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    if "__Secure-3PAPISID" not in jar:   # ytmusicapi busca esta cookie por nombre
        cookie += f"; __Secure-3PAPISID={sapisid}"
    headers_raw = "\n".join([
        f"cookie: {cookie}",
        f"authorization: {sapisid_hash(sapisid)}",
        "x-goog-authuser: 0",
        f"user-agent: {user_agent}",
        "accept: */*",
        "origin: https://music.youtube.com",
    ])
    ytmusicapi.setup(filepath=str(YT_BROWSER_FILE), headers_raw=headers_raw)
    try:
        os.chmod(YT_BROWSER_FILE, 0o600)
    except OSError:
        pass
    print("YouTube Music conectado.")
    return True


# ---- Firefox: se leen las cookies de tu perfil (Firefox no las cifra)

FIREFOX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


def firefox_cookie_dbs() -> list[Path]:
    roots = [Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles",
             Path.home() / ".mozilla" / "firefox"]
    dbs = [db for r in roots if r.is_dir() for db in r.glob("*/cookies.sqlite")]
    return sorted(dbs, key=lambda d: d.stat().st_mtime, reverse=True)   # el perfil más usado primero


def firefox_youtube_cookies() -> dict[str, str]:
    import shutil
    import sqlite3
    import tempfile
    for db in firefox_cookie_dbs():
        with tempfile.TemporaryDirectory() as tmp:
            # Firefox tiene el archivo abierto: se copia (junto con el -wal, que tiene lo más reciente)
            try:
                for suffix in ("", "-wal"):
                    src = db.with_name(db.name + suffix)
                    if src.exists():
                        shutil.copy2(src, Path(tmp) / src.name)
                con = sqlite3.connect(Path(tmp) / db.name)
                rows = con.execute(
                    "SELECT name, value FROM moz_cookies WHERE host LIKE '%youtube.com' "
                    "ORDER BY originAttributes != '', expiry").fetchall()   # sin contenedores, primero
                con.close()
            except Exception:
                continue
        jar: dict[str, str] = {}
        for name, value in rows:
            jar.setdefault(name, value)
        if "__Secure-3PAPISID" in jar or "SAPISID" in jar:
            return jar
    return {}


def yt_connect_firefox() -> bool:
    jar = firefox_youtube_cookies()
    if jar:
        print("\nYouTube Music: uso la sesión que ya tenés abierta en Firefox.")
        return build_browser_json(jar, FIREFOX_UA)
    print("\nYouTube Music: se abre Firefox. Iniciá sesión con tu cuenta de Google y volvé acá\n"
          "(el script lo detecta solo; puede tardar unos segundos).")
    webbrowser.open(YT_LOGIN_URL)
    deadline = time.time() + YT_LOGIN_TIMEOUT
    while time.time() < deadline:
        time.sleep(3)
        jar = firefox_youtube_cookies()
        if jar:
            return build_browser_json(jar, FIREFOX_UA)
    print("Se acabó el tiempo para iniciar sesión.")
    return False


# ---- Chrome, Brave, Edge, etc.: ventana propia con un perfil aparte

def _yt_logged_in(ctx) -> bool:
    names = {c["name"] for c in ctx.cookies(["https://music.youtube.com"])}
    return bool(names & {"__Secure-3PAPISID", "SAPISID"})


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CDP:
    """Cliente mínimo del protocolo de depuración de Chrome (DevTools), sin dependencias.
    Solo hace falta para pedirle las cookies al navegador y cerrarlo."""

    def __init__(self, ws_url: str, timeout: float = 15):
        import base64
        import socket
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("el navegador cortó la conexión")
            resp += chunk
        status = resp.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status:
            raise ConnectionError(f"el navegador rechazó la conexión: {status}")
        self.buf = resp.split(b"\r\n\r\n", 1)[1]
        self.msg_id = 0

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("se cerró el navegador")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, data: bytes, opcode: int = 0x1) -> None:
        import struct
        n, mask = len(data), os.urandom(4)
        header = bytearray([0x80 | opcode])
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv_message(self) -> str:
        import struct
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("se cerró el navegador")
            if opcode == 0x9:                      # ping -> pong
                self._send_frame(payload, 0xA)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b1 & 0x80:
                    return message.decode("utf-8", errors="replace")

    def send(self, method: str, params: dict | None = None) -> dict:
        self.msg_id += 1
        self._send_frame(json.dumps({"id": self.msg_id, "method": method, "params": params or {}}).encode())
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") == self.msg_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", msg["error"]))
                return msg.get("result", {})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _devtools_info(port: int) -> dict:
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost: sin proxy
    with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
        return json.loads(r.read().decode())


def yt_connect_direct(exe: Path, name: str) -> bool:
    """Abre el navegador elegido como un programa común (perfil aparte, directo en el inicio de
    sesión de Google) y le pide las cookies por el protocolo de DevTools. No maneja pestañas ni
    páginas, así que funciona aunque el navegador no deje que un programa lo controle (Vivaldi)."""
    port = _free_port()
    profile = YT_PROFILE_DIR / re.sub(r"\W", "", name.lower())
    profile.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen([str(exe), f"--remote-debugging-port={port}",
                                 f"--user-data-dir={profile.resolve()}", "--no-first-run",
                                 "--no-default-browser-check", "--new-window", YT_LOGIN_URL])
    except OSError as e:
        print(f"No pude abrir {name}: {e}")
        return False

    cdp, info, last_error = None, None, None
    deadline = time.time() + 40
    while time.time() < deadline and cdp is None:
        try:
            info = _devtools_info(port)
            cdp = CDP(info["webSocketDebuggerUrl"])
        except Exception as e:
            last_error = e
            time.sleep(0.5)
    if cdp is None:
        print(f"{name} no respondió: {last_error}\n"
              "Probá de nuevo, o elegí otro navegador con la opción 3 del menú principal.")
        try:
            proc.kill()
        except OSError:
            pass
        return False

    print(f"\nYouTube Music: se abrió una ventana de {name}.\n"
          "Si te lo pide, iniciá sesión con tu cuenta de Google. La ventana se cierra sola.")

    def youtube_jar() -> dict[str, str]:
        cookies = cdp.send("Storage.getCookies").get("cookies", [])
        yt = [c for c in cookies if c.get("domain", "").lstrip(".").endswith("youtube.com")]
        yt.sort(key=lambda c: c.get("domain") != ".youtube.com")   # las de .youtube.com primero
        jar: dict[str, str] = {}
        for c in yt:
            jar.setdefault(c["name"], c["value"])
        return jar

    try:
        deadline = time.time() + YT_LOGIN_TIMEOUT
        jar = youtube_jar()
        while not ({"__Secure-3PAPISID", "SAPISID"} & jar.keys()):
            if time.time() > deadline:
                print("Se acabó el tiempo para iniciar sesión.")
                return False
            time.sleep(1.5)
            jar = youtube_jar()
        time.sleep(3)              # deja terminar la redirección a YouTube Music
        jar = youtube_jar()
        user_agent = cdp.send("Browser.getVersion").get("userAgent") or info.get("User-Agent", "")
    except Exception as e:         # por ejemplo, si cerraste la ventana antes de terminar
        print(f"Se interrumpió el inicio de sesión: {e}")
        return False
    finally:
        try:
            cdp.send("Browser.close")   # cierre prolijo: el perfil guarda la sesión para la próxima
        except Exception:
            pass
        cdp.close()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    return build_browser_json(jar, user_agent)


def _launch_playwright(pw, profile: Path, **opts):
    ctx = pw.chromium.launch_persistent_context(
        str(profile.resolve()), headless=False, no_viewport=True,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"], **opts)
    return ctx, ctx.close


def _web_page(ctx, timeout: float = 30):
    """Una pestaña web (no las internas, como la interfaz de Vivaldi). Primero se espera la que
    abre el navegador al arrancar; pedir una nueva queda como último recurso."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for pg in reversed(ctx.pages):
            if not pg.is_closed() and pg.url.startswith(("http", "about:", "chrome-error:", "data:")):
                return pg
        try:
            ctx.wait_for_event("page", timeout=1000)
        except Exception:
            pass
    return ctx.new_page()


def yt_connect_chromium(kind: str, exe: Path | None, name: str = "", only_this: bool = False) -> bool:
    # El navegador elegido (o el predeterminado) se abre como un programa común, sin Playwright
    if exe and exe.exists():
        ok = yt_connect_direct(exe, name or _CHROMIUM.get(kind, kind))
        if ok or only_this:
            return ok
    if only_this:
        print(f"No encontré {name or 'el navegador elegido'}. Elegí otro con la opción 3 del menú principal.")
        return False
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        print("Falta la librería 'playwright' para la conexión automática. Instalala con:\n"
              f'    "{sys.executable}" -m pip install -r requirements.txt')
        return False

    # Sin navegador elegido: Chrome, Edge o el de Playwright
    attempts = [("Chrome", lambda pw, prof: _launch_playwright(pw, prof, channel="chrome")),
                ("Edge", lambda pw, prof: _launch_playwright(pw, prof, channel="msedge")),
                ("Chromium", lambda pw, prof: _launch_playwright(pw, prof))]

    with sync_playwright() as pw:
        ctx = close = None
        name, last_error = "", None
        for name, launch in attempts:
            profile = YT_PROFILE_DIR / re.sub(r"\W", "", name.lower())   # un perfil por navegador
            try:
                ctx, close = launch(pw, profile)
                break
            except Exception as e:
                last_error = e
        if ctx is None:
            print(f"No pude abrir el navegador: {last_error}")
            if only_this:
                print("Probá de nuevo, o elegí otro navegador con la opción 3 del menú principal.")
            return False
        print(f"\nYouTube Music: se abrió una ventana de {name}.\n"
              "Si te lo pide, iniciá sesión con tu cuenta de Google. La ventana se cierra sola.")
        try:
            page = _web_page(ctx)
            if "music.youtube.com" not in page.url:
                page.goto("https://music.youtube.com/", wait_until="domcontentloaded")
            if not _yt_logged_in(ctx):
                page.goto(YT_LOGIN_URL, wait_until="domcontentloaded")
                deadline = time.time() + YT_LOGIN_TIMEOUT
                while not _yt_logged_in(ctx):
                    if time.time() > deadline:
                        print("Se acabó el tiempo para iniciar sesión.")
                        return False
                    time.sleep(1)
                page = page if not page.is_closed() else _web_page(ctx)
                page.goto("https://music.youtube.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)   # deja que terminen de grabarse las cookies
            user_agent = page.evaluate("navigator.userAgent")
            cookies = ctx.cookies(["https://music.youtube.com"])
        except Exception as e:   # por ejemplo, si se cerró la ventana antes de terminar
            print(f"Se interrumpió el inicio de sesión: {e}")
            return False
        finally:
            try:
                close()
            except Exception:
                pass
    return build_browser_json({c["name"]: c["value"] for c in cookies}, user_agent)


def yt_connect(ask_again: bool = False) -> bool:
    """Conecta YouTube Music con el navegador que elijas y guarda la sesión en browser.json."""
    chosen = choose_browser(ask_again)
    if chosen is None:                       # no se pudo listar: usar el predeterminado
        kind, exe = default_browser()
        if kind == "firefox" and yt_connect_firefox():
            return True
        return yt_connect_chromium(kind, exe)
    if chosen.kind == "firefox":
        return yt_connect_firefox()
    return yt_connect_chromium(chosen.kind, chosen.exe, chosen.name, only_this=True)


def yt_manual_setup() -> None:
    ytmusicapi = require("ytmusicapi")
    print("Pegá los request headers de music.youtube.com (ver README.md).\n"
          "Para terminar: Enter, después Ctrl+Z y Enter otra vez.\n")
    ytmusicapi.setup(filepath=str(YT_BROWSER_FILE))
    print(f"\nListo: se creó {YT_BROWSER_FILE}.")


# ---------------------------------------------------------------- Tidal

class Tidal:
    label = "Tidal"

    def __init__(self):
        tidalapi = require("tidalapi")
        self._tidalapi = tidalapi
        self.s = tidalapi.Session()
        if not self._load_saved():
            print("\nTidal: se abre una página para autorizar el acceso. Aceptá y volvé acá.")
            self.s.login_oauth_simple(open_login_link)
            if not self.s.check_login():
                sys.exit("No se pudo iniciar sesión en Tidal.")
        self._save()

    def _load_saved(self) -> bool:
        if not TIDAL_SESSION_FILE.exists():
            return False
        try:
            d = json.loads(TIDAL_SESSION_FILE.read_text(encoding="utf-8"))
            expiry = datetime.fromisoformat(d["expiry_time"]) if d.get("expiry_time") else None
            return bool(self.s.load_oauth_session(d["token_type"], d["access_token"],
                                                  d.get("refresh_token"), expiry)) and self.s.check_login()
        except Exception as e:  # token vencido o archivo roto: se vuelve a loguear
            print(f"(No pude reutilizar la sesión de Tidal: {e}. Hay que loguearse de nuevo.)")
            return False

    def _save(self) -> None:
        expiry = self.s.expiry_time
        TIDAL_SESSION_FILE.write_text(json.dumps({
            "token_type": self.s.token_type,
            "access_token": self.s.access_token,
            "refresh_token": self.s.refresh_token,
            "expiry_time": expiry.isoformat() if expiry else None,
        }), encoding="utf-8")
        try:
            os.chmod(TIDAL_SESSION_FILE, 0o600)
        except OSError:
            pass

    @staticmethod
    def parse_id(value: str) -> str:
        return tidal_playlist_id(value)

    @staticmethod
    def url(pid: str) -> str:
        return f"https://tidal.com/playlist/{pid}"

    @staticmethod
    def _song(t) -> Song:
        artists = [a.name for a in (getattr(t, "artists", None) or []) if getattr(a, "name", None)]
        if not artists and getattr(t, "artist", None):
            artists = [t.artist.name]
        title = t.name
        version = getattr(t, "version", None)
        if version and version.lower() not in title.lower():
            title = f"{title} ({version})"
        return Song(title=title, artists=artists,
                    album=t.album.name if getattr(t, "album", None) else None,
                    duration=getattr(t, "duration", None),
                    isrc=getattr(t, "isrc", None), id=str(t.id))

    def list_playlists(self) -> list[PlaylistInfo]:
        """Playlists creadas por vos (son las únicas a las que se les puede agregar temas)."""
        user, seen, out, offset = self.s.user, set(), [], 0
        while True:
            try:
                batch = user.playlists(limit=50, offset=offset)
            except TypeError:                     # versión sin paginado
                batch = user.playlists()
            new = [p for p in batch or [] if str(p.id) not in seen]
            for p in new:
                seen.add(str(p.id))
                out.append(PlaylistInfo(str(p.id), p.name, getattr(p, "num_tracks", None)))
            if not new or len(batch) < 50:
                break
            offset += len(batch)
        return out

    def read(self, pid: str) -> tuple[str, list[Song]]:
        pl = self.s.playlist(pid)
        songs, offset = [], 0
        while True:
            batch = pl.tracks(limit=100, offset=offset)
            if not batch:
                break
            songs += [self._song(t) for t in batch]
            offset += len(batch)
            if len(batch) < 100:
                break
        return pl.name, songs

    def search(self, src: Song) -> list[Song]:
        found: dict[str, Song] = {}
        # 1) Por ISRC, si la versión de tidalapi lo soporta (coincidencia exacta)
        if src.isrc and hasattr(self.s, "get_tracks_by_isrc"):
            try:
                for t in self.s.get_tracks_by_isrc(src.isrc) or []:
                    s = self._song(t)
                    found[s.id] = s
            except Exception:
                pass
        # 2) Por texto
        for q in queries_for(src):
            try:
                res = self.s.search(q, models=[self._tidalapi.Track], limit=10)
            except Exception as e:
                print(f"   (búsqueda falló: {e})")
                continue
            tracks = res.get("tracks", []) if isinstance(res, dict) else getattr(res, "tracks", [])
            for t in tracks or []:
                s = self._song(t)
                found.setdefault(s.id, s)
            time.sleep(SEARCH_PAUSE)
            if found and best_match(src, list(found.values()))[1] >= SCORE_OK:
                break
        return list(found.values())

    def create(self, name: str, description: str, public: bool = False) -> str:
        return str(self.s.user.create_playlist(name, description).id)

    def _editable(self, pid: str):
        pl = self.s.playlist(pid)
        if not hasattr(pl, "add") and hasattr(pl, "factory"):
            pl = pl.factory()
        if not hasattr(pl, "add"):
            sys.exit(f"La playlist {pid} de Tidal no es tuya: solo se pueden modificar playlists propias.")
        return pl

    def add(self, pid: str, ids: list[str]) -> None:
        pl = self._editable(pid)
        for i in range(0, len(ids), BATCH):
            pl.add(ids[i:i + BATCH])

    def remove(self, pid: str, dups: list[tuple[int, Song, Song]]) -> None:
        pl = self._editable(pid)
        for index in sorted((i for i, _, _ in dups), reverse=True):   # de atrás para adelante
            pl.remove_by_index(index)


# ---------------------------------------------------------------- YouTube Music

class YTMusicService:
    label = "YouTube Music"
    _SKIP = {"LM", "SE"}   # "Me gusta" y "Episodios": no admiten agregar temas a mano

    def __init__(self):
        YTMusic = require("ytmusicapi").YTMusic
        if YT_OAUTH_FILE.exists() and not YT_BROWSER_FILE.exists():
            from ytmusicapi import OAuthCredentials
            cid, secret = os.environ.get("YT_CLIENT_ID"), os.environ.get("YT_CLIENT_SECRET")
            if not cid or not secret:
                sys.exit("Usás oauth.json: definí YT_CLIENT_ID y YT_CLIENT_SECRET antes de correr el script.")
            self.yt = YTMusic(str(YT_OAUTH_FILE),
                              oauth_credentials=OAuthCredentials(client_id=cid, client_secret=secret))
            return

        # Sesión guardada; si no hay, venció o el archivo no sirve, se reconecta sola
        yt = yt_open(YTMusic) if YT_BROWSER_FILE.exists() else None
        if yt is None:
            if YT_BROWSER_FILE.exists():
                print("\nLa sesión guardada de YouTube Music no sirve o venció. Se reconecta automáticamente.")
            yt = yt_open(YTMusic, show_error=True) if yt_connect() else None
        if yt is None:
            sys.exit("No pude conectar YouTube Music. Probá de nuevo, o usá el método manual:\n"
                     "    py transfer_playlist.py setup-yt --manual\n(ver README.md)")
        self.yt = yt

    @staticmethod
    def parse_id(value: str) -> str:
        return yt_playlist_id(value)

    @staticmethod
    def url(pid: str) -> str:
        return f"https://music.youtube.com/playlist?list={pid}"

    @staticmethod
    def _song(item: dict) -> Song:
        return Song(title=item.get("title") or "",
                    artists=[a["name"] for a in (item.get("artists") or []) if a.get("name")],
                    album=(item.get("album") or {}).get("name"),
                    duration=item.get("duration_seconds"),
                    id=item.get("videoId"), set_id=item.get("setVideoId"))

    def list_playlists(self) -> list[PlaylistInfo]:
        out = []
        for p in self.yt.get_library_playlists(limit=None):
            if p.get("playlistId") in self._SKIP:
                continue
            count = p.get("count")
            try:
                count = int(str(count).replace(".", "").replace(",", "").split()[0]) if count else None
            except ValueError:
                count = None
            out.append(PlaylistInfo(p["playlistId"], p.get("title") or "(sin nombre)", count))
        return out

    def read(self, pid: str) -> tuple[str, list[Song]]:
        pl = self.yt.get_playlist(pid, limit=None)
        songs = [self._song(t) for t in pl.get("tracks", []) if t.get("title")]
        return pl.get("title") or "Playlist", songs

    def search(self, src: Song) -> list[Song]:
        found: dict[str, Song] = {}
        for flt in ("songs", "videos"):        # primero canciones; si no aparece, videos
            for q in queries_for(src):
                try:
                    results = self.yt.search(q, filter=flt, limit=10)
                except Exception as e:
                    print(f"   (búsqueda falló: {e})")
                    continue
                for item in results:
                    if item.get("videoId"):
                        s = self._song(item)
                        found.setdefault(s.id, s)
                time.sleep(SEARCH_PAUSE)
                if found and best_match(src, list(found.values()))[1] >= SCORE_OK:
                    return list(found.values())
        return list(found.values())

    def create(self, name: str, description: str, public: bool = False) -> str:
        pid = self.yt.create_playlist(name, description, privacy_status="PUBLIC" if public else "PRIVATE")
        if not isinstance(pid, str):
            sys.exit(f"YouTube Music no creó la playlist: {pid}")
        return pid

    def add(self, pid: str, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            res = self.yt.add_playlist_items(pid, ids[i:i + BATCH], duplicates=False)
            if isinstance(res, dict) and res.get("status") not in (None, "STATUS_SUCCEEDED"):
                print(f"   (YouTube Music respondió: {res.get('status')}. ¿La playlist es tuya?)")

    def remove(self, pid: str, dups: list[tuple[int, Song, Song]]) -> None:
        items = [{"videoId": s.id, "setVideoId": s.set_id} for _, s, _ in dups if s.id and s.set_id]
        for i in range(0, len(items), BATCH):
            self.yt.remove_playlist_items(pid, items[i:i + BATCH])


# ---------------------------------------------------------------- menús

def temas(n: int) -> str:
    return f"{n} tema" if n == 1 else f"{n} temas"


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit("\nCancelado.")


def show_playlists(pls: list[PlaylistInfo]) -> None:
    width = len(str(len(pls)))
    for i, p in enumerate(pls, 1):
        count = f"  ({temas(p.count)})" if p.count is not None else ""
        print(f"  {i:>{width}}. {p.name}{count}")


def parse_choice(text: str, maximum: int) -> list[int] | None:
    """'2' → [2]; '1,3' o '1 3' → [1, 3]; '0' → [0]. None si no es válido."""
    parts = [x for x in re.split(r"[,\s]+", text) if x]
    if not parts or not all(x.isdigit() for x in parts):
        return None
    nums = list(dict.fromkeys(int(x) for x in parts))
    if any(n < 0 or n > maximum for n in nums) or (0 in nums and len(nums) > 1):
        return None
    return nums


def pick_source(svc, prompt: str = "¿Cuál querés pasar? (número): ") -> str:
    pls = svc.list_playlists()
    if not pls:
        sys.exit(f"No encontré playlists en tu cuenta de {svc.label}. Pasá la URL de origen como argumento.")
    print(f"\nTus playlists en {svc.label}:")
    show_playlists(pls)
    while True:
        choice = parse_choice(ask(prompt), len(pls))
        if choice and len(choice) == 1 and choice[0] > 0:
            return pls[choice[0] - 1].id
        print("  Escribí un solo número de la lista.")


@dataclass
class Target:
    name: str
    id: str | None = None      # None = playlist nueva (se crea al final)


def pick_targets(svc, default_name: str) -> list[Target]:
    pls = svc.list_playlists()
    print(f"\n¿A qué playlist de {svc.label} los agrego?")
    print(f"  0. Crear una playlist nueva")
    show_playlists(pls)
    print("  (Podés elegir varias separadas por coma, por ejemplo: 2,5)")
    while True:
        choice = parse_choice(ask("Elegí: "), len(pls))
        if choice is not None:
            break
        print("  Opción inválida.")
    if choice == [0]:
        name = ask(f"Nombre de la playlist nueva [{default_name}]: ") or default_name
        return [Target(name)]
    return [Target(pls[n - 1].name, pls[n - 1].id) for n in choice]


# ---------------------------------------------------------------- flujo

def match_all(songs: list[Song], dest) -> tuple[list[dict], list[Song | None]]:
    rows, matches = [], []
    for n, src in enumerate(songs, 1):
        match, sc = best_match(src, dest.search(src))
        if match and sc >= SCORE_OK:
            estado = "OK"
        elif match and sc >= SCORE_MIN:
            estado = "DUDOSO"
        else:
            estado = "NO ENCONTRADO"
        marca = {"OK": "✓", "DUDOSO": "?", "NO ENCONTRADO": "✗"}[estado]
        destino = match.label() if match else "-"
        print(f"{n:>3}. {marca} {src.label()}  →  {destino}  [{sc:.2f}]")
        rows.append({
            "n": n, "estado": estado, "puntaje": f"{sc:.2f}",
            "origen": src.label(), "origen_duracion": src.duration or "",
            "destino": destino, "destino_duracion": (match.duration if match else "") or "",
            "destino_id": match.id if match and estado != "NO ENCONTRADO" else "",
        })
        matches.append(match if estado != "NO ENCONTRADO" else None)
    return rows, matches


def clean_duplicates(svc) -> None:
    pid = pick_source(svc, "¿De qué playlist quito los repetidos? (número): ")
    name, songs = svc.read(pid)
    dups = find_duplicates(songs)
    if not dups:
        print(f"\n«{name}» no tiene temas repetidos.")
        return
    n = len(dups)
    print(f"\nEn «{name}» hay {temas(n)} {'repetido' if n == 1 else 'repetidos'} "
          "(cuenta también otras versiones de la misma canción).\n"
          "Se quita la copia y queda la que estaba primero en la playlist:\n")
    for _, s, k in dups:
        print(f"  - {s.label()}\n      queda: {k.label()}")
    if ask(f"\n¿Quito {'este' if len(dups) == 1 else 'estos'} {temas(len(dups))}? (s/n): ").lower() not in ("s", "si", "sí"):
        print("No se quitó nada.")
        return
    svc.remove(pid, dups)
    print(f"Listo: se quitaron {temas(len(dups))} de «{name}».")


def write_report(rows: list[dict]) -> Path:
    report = Path(f"reporte_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with report.open("w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel lo abre bien
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["n"])
        w.writeheader()
        w.writerows(rows)
    return report


def run(source, dest, src_value: str | None, to: list[str], new: bool, new_name: str | None,
        dry_run: bool, public: bool) -> None:
    src_id = source.parse_id(src_value) if src_value else pick_source(source)
    src_name, songs = source.read(src_id)
    print(f"\nOrigen: «{src_name}» en {source.label} ({temas(len(songs))})")

    # Destino
    if to:
        targets = [Target(t, dest.parse_id(t)) for t in to]
    elif new:
        targets = [Target(new_name or src_name)]
    else:
        targets = pick_targets(dest, new_name or src_name)

    print(f"\nBuscando los temas en {dest.label}...\n")
    rows, matches = match_all(songs, dest)
    report = write_report(rows)

    ok = sum(r["estado"] == "OK" for r in rows)
    dudoso = sum(r["estado"] == "DUDOSO" for r in rows)
    print(f"\nCoincidencias: {ok} OK, {dudoso} dudosos, {len(rows) - ok - dudoso} no encontrados. Reporte: {report}")

    found: list[Song] = []
    for m in matches:
        if m and m.id and all(m.id != f.id for f in found):
            found.append(m)
    if not found:
        print("No hay temas para agregar.")
        return

    for t in targets:
        current: list[Song] = []
        if t.id is not None:
            t.name, current = dest.read(t.id)   # nombre real (útil con --to) y temas que ya tiene
        to_add: list[Song] = []
        already, versions = 0, []
        for m in found:
            twin = next((c for c in current if same_song(m, c)), None)
            if twin is None:
                twin = next((a for a in to_add if same_song(m, a)), None)
                if twin is not None:
                    versions.append((m, twin, "repetida en el origen"))
                    continue
            elif twin.id == m.id:
                already += 1
                continue
            else:
                versions.append((m, twin, "ya estaba"))
                continue
            to_add.append(m)

        where = f"«{t.name}» (nueva)" if t.id is None else f"«{t.name}»"
        skipped = already + len(versions)
        detail = f"{skipped} ya estaban" + (f" ({len(versions)} en otra versión)" if versions else "")
        if dry_run:
            print(f"[dry-run] {where}: se agregarían {temas(len(to_add))}; {detail}.")
        elif not to_add:
            print(f"{where}: no hay nada nuevo que agregar ({detail}).")
        else:
            if t.id is None:
                desc = f"Transferida desde {source.label} el {datetime.now():%d/%m/%Y}"
                t.id = dest.create(t.name, desc, public)
            dest.add(t.id, [m.id for m in to_add])
            verb = "agregado" if len(to_add) == 1 else "agregados"
            print(f"Listo: {temas(len(to_add))} {verb} a {where}; {detail}.  {dest.url(t.id)}")
        for m, twin, why in versions:
            print(f"   ≈ no agregué «{m.label()}»: {why} como «{twin.label()}»")

    if dry_run:
        print("Modo --dry-run: no se modificó nada.")
    elif dudoso:
        print("Revisá los temas marcados con ? : puede ser otra versión (en vivo, remix, video).")


def main() -> None:
    # En Windows, si la salida va a un archivo o a una consola vieja, evita que ✓ ? ✗ rompan el script
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="Transfiere temas entre playlists de Tidal y YouTube Music.")
    p.add_argument("direction", nargs="?", choices=["tidal2yt", "yt2tidal", "setup-yt"],
                   help="tidal2yt, yt2tidal o setup-yt (volver a conectar YouTube Music). Si falta, hay menú.")
    p.add_argument("playlist", nargs="?", help="URL o ID de la playlist de origen (si falta, se elige de una lista)")
    dest = p.add_mutually_exclusive_group()
    dest.add_argument("--to", action="append", default=[], metavar="URL_O_ID",
                      help="Playlist de destino existente (se puede repetir)")
    dest.add_argument("--new", action="store_true", help="Crear una playlist nueva")
    p.add_argument("--name", help="Nombre de la playlist nueva")
    p.add_argument("--dry-run", action="store_true", help="Solo genera el reporte, no modifica nada")
    p.add_argument("--public", action="store_true", help="(YouTube Music) playlist nueva pública")
    p.add_argument("--manual", action="store_true", help="(setup-yt) pegar los headers a mano")
    args = p.parse_args()

    if args.direction == "setup-yt":
        if args.manual:
            yt_manual_setup()
        elif not yt_connect(ask_again=True) or yt_open(require("ytmusicapi").YTMusic, show_error=True) is None:
            sys.exit("No se pudo conectar. Probá: py transfer_playlist.py setup-yt --manual")
        return

    browser = choose_browser()   # la primera vez pregunta; después usa el guardado
    while args.direction is None:
        actual = f" (ahora: {browser.name})" if browser else ""
        print("\n¿Qué querés hacer?\n"
              "  1. Pasar temas de Tidal a YouTube Music\n"
              "  2. Pasar temas de YouTube Music a Tidal\n"
              f"  3. Cambiar el navegador{actual}\n"
              "  4. Quitar temas repetidos de una playlist")
        c = ask("Elegí 1, 2, 3 o 4: ")
        if c in ("1", "2"):
            args.direction = "tidal2yt" if c == "1" else "yt2tidal"
        elif c == "3":
            browser = choose_browser(ask_again=True)
        elif c == "4":
            print("\n¿En qué servicio?\n  1. Tidal\n  2. YouTube Music")
            while (c := ask("Elegí 1 o 2: ")) not in ("1", "2"):
                pass
            clean_duplicates(Tidal() if c == "1" else YTMusicService())
            return

    if args.direction == "tidal2yt":
        source, target = Tidal(), YTMusicService()
    else:
        source, target = YTMusicService(), Tidal()
    run(source, target, args.playlist, args.to, args.new, args.name, args.dry_run, args.public)


if __name__ == "__main__":
    main()
