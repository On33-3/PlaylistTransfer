"""Spotify con su Web API (cambios de 2026 incluidos), sin librerías aparte: `requests` alcanza.

- Cada usuario usa **su propia app** de Spotify (modo desarrollo: hasta 5 usuarios y el dueño con
  Premium). Su Client ID se guarda en `spotify_app.json`; los tokens, en `spotify_session.json`.
- Login: Authorization Code con PKCE y un servidor en 127.0.0.1 (ver `app/auth/loopback.py`).
- Playlists: `/items` (antes `/tracks`). Solo se leen las propias o colaborativas.
- Quitar: Spotify quita por URI y saca **todas** las copias. Para una copia exacta se quitan todas y
  se vuelve a agregar la que queda en su lugar; después se relee para verificar (ver `remove`).
- Refresh token: vence a los 6 meses de la primera autorización → estado "venció" → Reconectar.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

from app.auth import loopback
from app.auth.browsers import Browser, open_url
from app.connectors.base import (LIKED, Connector, ConnectorError, NoNetwork, NotConnectedError, NotOwnerError,
                                 Status, clock_after, human_wait, is_network_error, liked_info,
                                 require_confirmation)
from app.core.matching import queries_for
from app.core.models import Duplicate, PlaylistInfo, Song
from app.i18n import t as tr

log = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
AUTHORIZE = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
DASHBOARD = "https://developer.spotify.com/dashboard"     # "tus apps"
CREATE_APP = DASHBOARD + "/create"
PREMIUM = "https://www.spotify.com/premium/"
# Lo que el usuario registra en su app. Con puerto fijo: el panel de Spotify rechaza "http://127.0.0.1/callback"
# (sin puerto) con "This redirect URI is not secure" y no deja guardar, aunque su documentación diga que vale
# (comprobado por un usuario el 2026-11-08). Con puerto, sí lo acepta.
CALLBACK_PORT = 8973
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}{loopback.CALLBACK_PATH}"
# user-library-*: las canciones que te gustan (leer, guardar y quitar)
SCOPES = ("playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public "
          "user-library-read user-library-modify")
LIBRARY_SCOPES = {"user-library-read", "user-library-modify"}
CLIENT_ID = re.compile(r"^[0-9a-f]{32}$")
_PLAYLIST_ID = re.compile(r"(?:playlist[/:])?([A-Za-z0-9]{22})(?:\b|$)")

PAGE = 50              # temas o playlists por pedido (máximo de Spotify)
ADD_BATCH = 100        # temas por pedido al agregar (máximo de Spotify)
LIBRARY_BATCH = 40     # temas por pedido al guardar o quitar de "Me gusta" (/me/library, desde 2026)
SEARCH_LIMIT = 10      # máximo desde febrero de 2026
MAX_WAIT = 300         # segundos: hasta 5 minutos se espera (con cuenta regresiva); más, se corta y se avisa
# Más despacio que los otros: las apps en modo desarrollo (cada persona crea la suya) tienen un límite bajo,
# y con miles de temas se llegaba (se pedían horas de pausa). Cada pedido de pausa lo baja más.
SEARCH_PAUSE_SPOTIFY = 0.8
MAX_SEARCH_PAUSE = 4.0


class TokenExpired(ConnectorError):
    """El permiso venció (a los 6 meses) o se revocó: hay que volver a conectar."""


class RateLimited(ConnectorError):
    """Spotify pidió esperar más de lo razonable: se corta todo (no es "no encontrado")."""


class Forbidden(ConnectorError):
    """403: Premium vencido, fuera de la lista de usuarios de la app o una playlist ajena."""


def valid_client_id(value: str) -> str:
    cid = (value or "").strip().lower()
    if not CLIENT_ID.match(cid):
        raise ConnectorError(tr("err.spotify_bad_client_id"))
    return cid


class SpotifyConnector(Connector):
    key = "spotify"
    name = "Spotify"
    search_pause = SEARCH_PAUSE_SPOTIFY
    needs_setup = True
    removes_all_copies = True

    def __init__(self, session_file: Path, app_file: Path, http=None, open_browser=open_url,
                 callback_server=loopback.CallbackServer):
        """`http` (algo con `.request(method, url, **kw)` como requests.Session), `open_browser` y
        `callback_server` se reemplazan en los tests."""
        self.session_file = Path(session_file)
        self.app_file = Path(app_file)
        self._http = http
        self._open_browser = open_browser
        self._callback_server = callback_server
        self._token: dict | None = None
        self._me: str | None = None
        self._lock = threading.Lock()

    # ---- archivos

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    @property
    def client_id(self) -> str | None:
        cid = (self._read_json(self.app_file) or {}).get("client_id")
        return cid if isinstance(cid, str) and CLIENT_ID.match(cid) else None

    def set_client_id(self, value: str) -> str:
        """Guarda el Client ID de la app del usuario. Si cambia, la sesión anterior ya no sirve."""
        cid = valid_client_id(value)
        if cid != self.client_id:
            self.disconnect()
        self._write_json(self.app_file, {"client_id": cid})
        return cid

    def login_steps(self, browser) -> list[str]:
        return [f"login.spotify.{i}" for i in range(1, 4)]

    def setup_info(self) -> dict:
        """Los pasos para configurar Spotify, uno por acción y en el orden del formulario de Spotify.
        La UI solo los dibuja. Cada paso: `text` (clave del diccionario), y si hace falta `button` (el botón
        que el texto nombra como {button}: el mismo texto sale de una sola clave), `link` (ese botón abre
        esa página), `copy` / `copy_key` (lo que se pega, con su botón Copiar), `input` (acá se pega el
        Client ID) y `note` (un aviso debajo). Los nombres entre «» son los de la pantalla de Spotify."""
        p = "settings.sp."
        return {"client_id": self.client_id, "sections": [
            {"title": p + "s1", "steps": [
                {"text": p + "premium", "button": p + "premium_btn", "link": PREMIUM}]},
            {"title": p + "s2", "steps": [
                {"text": p + "open_form", "button": p + "open_form_btn", "link": CREATE_APP},
                {"text": p + "login", "button": p + "open_form_btn"},
                {"text": p + "app_name", "copy_key": p + "app_name_value"},
                {"text": p + "app_desc", "copy_key": p + "app_desc_value"},
                {"text": p + "website"},
                {"text": p + "redirect", "copy": REDIRECT_URI, "note": p + "redirect_note"},
                {"text": p + "web_api"},
                {"text": p + "terms"},
                {"text": p + "save"}]},
            {"title": p + "s3", "steps": [
                {"text": p + "settings", "button": p + "apps_btn", "link": DASHBOARD},
                {"text": p + "client_id"},
                {"text": p + "paste", "button": p + "save_btn", "input": True},
                {"text": p + "agree"}]},
        ]}

    # ---- HTTP

    def _session(self):
        if self._http is None:
            import requests
            self._http = requests.Session()
        return self._http

    def _request(self, method: str, url: str, **kw):
        try:
            return self._session().request(method, url, timeout=30, **kw)
        except Exception as e:
            if is_network_error(e):
                raise NoNetwork(tr("err.no_network", service=self.name)) from e
            raise

    def _token_request(self, data: dict) -> dict:
        r = self._request("POST", TOKEN, data=data)
        body = _json(r)
        if r.status_code != 200:
            if body.get("error") == "invalid_grant":
                raise TokenExpired(tr("err.spotify_expired"))
            raise ConnectorError(tr("err.spotify_login_failed", detail=body.get("error_description") or body.get("error") or r.status_code))
        return body

    def _store(self, body: dict, first: bool = False) -> None:
        old = self._token or {}
        tok = {"access_token": body["access_token"],
               "refresh_token": body.get("refresh_token") or old.get("refresh_token"),
               "expires_at": time.time() + int(body.get("expires_in", 3600)),
               "authorized_at": time.time() if first else old.get("authorized_at", time.time()),
               "scope": body.get("scope") or old.get("scope")}
        self._token = tok
        self._write_json(self.session_file, tok)

    def _access_token(self) -> str:
        with self._lock:
            if self._token is None:
                self._token = self._read_json(self.session_file)
            tok = self._token
            if not tok or not tok.get("access_token"):
                raise NotConnectedError(tr("err.not_connected", service=self.name))
            if tok.get("expires_at", 0) - 60 < time.time():
                self._refresh()
            return self._token["access_token"]

    def _refresh(self) -> None:
        tok, cid = self._token or {}, self.client_id
        if not tok.get("refresh_token") or not cid:
            raise TokenExpired(tr("err.spotify_expired"))
        self._store(self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                         "client_id": cid}))

    def _call(self, method: str, path: str, *, params=None, body=None, retry: bool = True):
        """Un pedido a la API. Renueva el token una vez si venció y respeta "esperá" (429)."""
        url = path if path.startswith("http") else API + path
        for attempt in range(4):
            r = self._request(method, url, params=params, json=body,
                              headers={"Authorization": f"Bearer {self._access_token()}"})
            code = r.status_code
            if code in (200, 201):
                return _json(r)
            if code == 204:
                return {}
            if code == 401 and retry and attempt == 0:
                with self._lock:
                    self._refresh()
                continue
            if code == 429:
                wait = _retry_after(r)
                self.search_pause = min(max(self.search_pause, SEARCH_PAUSE_SPOTIFY) * 2, MAX_SEARCH_PAUSE)                     if self.search_pause else 0      # de acá en adelante, más despacio
                log.warning("Spotify pidió esperar %s s (%s); pausa entre búsquedas: %s s", wait,
                            _json(r).get("error") or "", self.search_pause)
                if wait > MAX_WAIT or attempt == 3:
                    raise RateLimited(tr("err.spotify_wait", wait=human_wait(wait), clock=clock_after(wait)))
                self.pause(wait)
                continue
            if code >= 500 and attempt < 2:
                self.pause(1 + attempt, "retry")
                continue
            msg = (_json(r).get("error") or {})
            msg = msg.get("message", "") if isinstance(msg, dict) else str(msg)
            log.info("Spotify %s %s → %s %s", method, path, code, msg)
            if code == 401:
                raise TokenExpired(tr("err.spotify_expired"))
            if code == 403:
                raise Forbidden(tr("err.spotify_forbidden"))
            if code == 404:
                raise ConnectorError(tr("err.spotify_not_found"))
            raise ConnectorError(tr("err.spotify_error", code=code, detail=msg))
        raise ConnectorError(tr("err.spotify_error", code="?", detail=""))

    def _pages(self, path: str, params: dict | None = None):
        """Recorre una lista paginada de Spotify (sigue el link "next")."""
        page = self._call("GET", path, params={"limit": PAGE, **(params or {})})
        while True:
            yield from page.get("items") or []
            if not page.get("next"):
                return
            page = self._call("GET", page["next"])

    # ---- sesión

    def _lacks_library_permission(self) -> bool:
        """Sesión de antes de "Me gusta": se ve conectada, pero Spotify no da la lista hasta volver a autorizar."""
        tok = self._token or self._read_json(self.session_file)
        return bool(tok) and not LIBRARY_SCOPES <= set((tok.get("scope") or "").split())

    def status(self) -> Status:
        if not self.client_id:
            return Status.SETUP
        if self._lacks_library_permission():
            return Status.EXPIRED
        if self._me is not None:
            return Status.CONNECTED
        if not self.session_file.exists():
            return Status.DISCONNECTED
        try:
            self._me = str(self._call("GET", "/me").get("id") or "")
            return Status.CONNECTED
        except TokenExpired:
            return Status.EXPIRED
        except ConnectorError as e:            # 403 (Premium vencido, fuera de la lista de usuarios)...
            log.info("Spotify no aceptó la sesión: %s", e)
            return Status.EXPIRED

    def connect(self, browser: Browser | None = None, cancel: threading.Event | None = None) -> None:
        cid = self.client_id
        if not cid:
            raise ConnectorError(tr("err.spotify_setup_first"))
        verifier, state = loopback.make_verifier(), secrets.token_urlsafe(16)
        try:
            server = self._callback_server(port=CALLBACK_PORT)
        except OSError as e:                 # otro programa usa el puerto
            raise ConnectorError(tr("err.spotify_port_busy", port=CALLBACK_PORT)) from e
        with server as cb:
            self._open_browser(AUTHORIZE + "?" + urlencode({
                "client_id": cid, "response_type": "code", "redirect_uri": cb.redirect_uri,
                "code_challenge_method": "S256", "code_challenge": loopback.challenge(verifier),
                "scope": SCOPES, "state": state}), browser)
            try:
                code = cb.wait(state, cancel=cancel)
            except loopback.LoginFailed as e:
                raise ConnectorError(str(e)) from e
            redirect = cb.redirect_uri
        self._token = None
        self._store(self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                         "client_id": cid, "code_verifier": verifier}), first=True)
        self._me = None
        try:
            self._me = str(self._call("GET", "/me").get("id") or "")
        except ConnectorError:
            # Spotify dio el permiso pero no deja usar la API (sin Premium, fuera de la lista de
            # usuarios...): no se guarda una sesión que no sirve (si no, diría "venció")
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """Borra la sesión (los tokens). El Client ID queda: es la app del usuario, no un secreto."""
        self.session_file.unlink(missing_ok=True)
        self._token, self._me = None, None

    def forget_session(self) -> None:
        self._me = None

    def _ready(self) -> str:
        if self._me is None and self.status() is not Status.CONNECTED:
            raise NotConnectedError(tr("err.not_connected", service=self.name))
        return self._me or ""

    # ---- ids

    @staticmethod
    def parse_id(value: str) -> str:
        m = _PLAYLIST_ID.search((value or "").strip())
        if not m:
            raise ConnectorError(tr("err.spotify_bad_id", value=value))
        return m.group(1)

    @staticmethod
    def url(playlist_id: str) -> str:
        if playlist_id == LIKED:
            return "https://open.spotify.com/collection/tracks"
        return f"https://open.spotify.com/playlist/{playlist_id}"

    # ---- lectura

    @staticmethod
    def to_song(t: dict) -> Song:
        """Un tema de la API. El id es su URI ("spotify:track:…"): es lo que piden agregar y quitar."""
        album = t.get("album") or {}
        dur = t.get("duration_ms")
        return Song(title=t.get("name") or "", artists=[a["name"] for a in t.get("artists") or [] if a.get("name")],
                    album=album.get("name") or None, duration=round(dur / 1000) if dur else None,
                    isrc=((t.get("external_ids") or {}).get("isrc") or None), id=t.get("uri") or None)

    def list_playlists(self) -> list[PlaylistInfo]:
        """Las propias y las colaborativas: de las demás, Spotify no deja leer ni cambiar los temas."""
        me = self._ready()
        out = []
        try:                                       # las canciones que te gustan, primero
            out.append(liked_info(self._call("GET", "/me/tracks", params={"limit": 1}).get("total")))
        except Forbidden:                          # sesión de antes de pedir ese permiso: reconectar lo arregla
            log.info("Spotify: la sesión no tiene permiso para leer las canciones que te gustan")
        for p in self._pages("/me/playlists"):
            if not p or not ((p.get("owner") or {}).get("id") == me or p.get("collaborative")):
                continue
            items = p.get("items") or p.get("tracks") or {}
            total = items.get("total") if isinstance(items, dict) else None
            if total is not None and total < 2:
                # Comprobado el 2026-11-24: esta lista sigue diciendo 0 de una playlist recién creada aunque
                # ya tenga temas, y Quitar repetidos la escondía. Con 0 o 1 se pregunta la cantidad real
                total = self._call("GET", f"/playlists/{p['id']}/items", params={"limit": 1}).get("total", total)
            out.append(PlaylistInfo(p["id"], p.get("name") or "", total))
        return out

    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        self._ready()
        if playlist_id == LIKED:                   # de la más reciente a la más vieja, como en Spotify
            songs = [self.to_song(e["track"]) for e in self._pages("/me/tracks")
                     if (e or {}).get("track") and not e["track"].get("is_local")]
            return tr("playlist.liked"), songs
        name = self._call("GET", f"/playlists/{playlist_id}", params={"fields": "name"}).get("name") or ""
        songs = []
        for entry in self._pages(f"/playlists/{playlist_id}/items"):
            t = (entry or {}).get("item") or (entry or {}).get("track")
            if t and t.get("type", "track") == "track":
                songs.append(self.to_song(t))       # los podcasts no se transfieren
        return name, songs

    def search(self, song: Song) -> list[Song]:
        self._ready()

        def fetch(q):
            res = self._call("GET", "/search", params={"q": q, "type": "track", "limit": SEARCH_LIMIT})
            return [self.to_song(t) for t in (res.get("tracks") or {}).get("items") or [] if t and not t.get("is_local")]
        queries = ([f"isrc:{song.isrc}"] if song.isrc else []) + list(queries_for(song))
        return list(self.collect(song, queries, fetch, fatal=(TokenExpired, NotConnectedError, RateLimited, Forbidden, NoNetwork),
                                 recoverable=ConnectorError).values())

    # ---- escritura

    def create(self, name: str, description: str, public: bool = False) -> str:
        self._ready()
        return self._call("POST", "/me/playlists", body={"name": name, "description": description, "public": public})["id"]

    def add(self, playlist_id: str, songs: list[Song]) -> None:
        uris = [s.id for s in songs if s.id and s.id.startswith("spotify:track:")]
        self._ready()
        if playlist_id == LIKED:
            # Lo último que se guarda queda arriba: se guardan al revés, así el primero de la lista queda primero
            uris.reverse()
            for i in range(0, len(uris), LIBRARY_BATCH):
                self._call("PUT", "/me/library", params={"uris": ",".join(uris[i:i + LIBRARY_BATCH])})
                self.adding(min(i + LIBRARY_BATCH, len(uris)), len(uris))
            return
        for i in range(0, len(uris), ADD_BATCH):
            try:
                self._call("POST", f"/playlists/{playlist_id}/items", body={"uris": uris[i:i + ADD_BATCH]})
            except Forbidden as e:
                raise NotOwnerError(tr("err.spotify_not_owner")) from e
            self.adding(min(i + ADD_BATCH, len(uris)), len(uris))

    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        """Quita esas apariciones. Spotify quita por URI (todas las copias): si de una URI tiene que
        quedar alguna copia, se quitan todas y se vuelven a agregar las que quedan, en su lugar.
        Al final se relee la playlist; si no quedó como se esperaba, se arregla lo que falte y se avisa."""
        require_confirmation(confirmed)
        if not items:
            return
        if playlist_id == LIKED:                   # en "Me gusta" cada tema está una vez: se quita por URI
            uris = sorted({d.song.id for d in items if d.song.id})
            for i in range(0, len(uris), LIBRARY_BATCH):
                self._call("DELETE", "/me/library", params={"uris": ",".join(uris[i:i + LIBRARY_BATCH])})
            return
        _, now = self.read(playlist_id)
        current = [s.id for s in now]
        drop = {d.index for d in items}
        if any(not (0 <= d.index < len(current)) or current[d.index] != d.song.id for d in items):
            raise ConnectorError(tr("err.playlist_changed"))
        final = [u for i, u in enumerate(current) if i not in drop]
        gone = {current[i] for i in drop}
        readd = [(pos, u) for pos, u in enumerate(final) if u in gone]     # copias que tienen que quedar
        uris = sorted(gone)
        for i in range(0, len(uris), ADD_BATCH):
            self._call("DELETE", f"/playlists/{playlist_id}/items", body={"items": [{"uri": u} for u in uris[i:i + ADD_BATCH]]})
        for pos, u in readd:                     # de adelante para atrás: cada una cae en su lugar final
            self._call("POST", f"/playlists/{playlist_id}/items", body={"uris": [u], "position": pos})
        _, after = self.read(playlist_id)
        missing = Counter(final) - Counter(s.id for s in after)
        if missing:
            lost = list(missing.elements())
            self._call("POST", f"/playlists/{playlist_id}/items", body={"uris": lost})
            titles = {s.id: s.title for s in now}
            raise ConnectorError(tr("err.spotify_readded", titles=", ".join(dict.fromkeys(titles.get(u, u) for u in lost))))


def _retry_after(r) -> int:
    """Los segundos que pide Spotify (1 si no lo dice o no se entiende)."""
    try:
        return max(1, int(float(r.headers.get("Retry-After") or 1)))
    except (TypeError, ValueError):
        return 1


def _json(r) -> dict:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
