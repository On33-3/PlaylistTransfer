"""Conector de YouTube Music (ytmusicapi, no oficial), con sesión de navegador."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.auth import cdp_login, firefox
from app.auth.browsers import Browser
from app.auth.cdp_login import LoginError
from app.connectors.base import (BATCH, LIKED, RATE_LIMIT_WAITS, Connector, ConnectorError, NotConnectedError, NotOwnerError,
                                 Status, liked_info, require_confirmation)
from app.core.matching import queries_for
from app.core.models import Duplicate, PlaylistInfo, Song
from app.i18n import t as tr

log = logging.getLogger(__name__)

LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&passive=true&continue="
             "https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26app%3Ddesktop"
             "%26next%3Dhttps%253A%252F%252Fmusic.youtube.com%252F")
ORIGIN = "https://music.youtube.com"
SESSION_COOKIES = {"__Secure-3PAPISID", "SAPISID"}
LIKED_LIST = "LM"               # "Me gusta" de YouTube Music: se lee como lista; se agrega marcando "Me gusta"
SKIP_PLAYLISTS = {"SE"}         # "Episodios": no admite agregar temas


def sapisid_hash(sapisid: str, origin: str = ORIGIN, ts: int | None = None) -> str:
    """Header 'authorization' que usa YouTube. ytmusicapi lo necesita para reconocer la sesión de
    navegador: sin él la toma como OAuth y falla con "oauth_credentials not provided"."""
    ts = str(int(time.time()) if ts is None else ts)
    return f"SAPISIDHASH {ts}_{hashlib.sha1(f'{ts} {sapisid} {origin}'.encode()).hexdigest()}"


def browser_headers(jar: dict[str, str], user_agent: str, ts: int | None = None) -> str:
    """Headers en texto (como los copiarías del navegador) a partir de las cookies de YouTube."""
    sapisid = jar.get("__Secure-3PAPISID") or jar.get("SAPISID")
    if not sapisid:
        raise LoginError(tr("err.google_session_missing"))
    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    if "__Secure-3PAPISID" not in jar:   # ytmusicapi busca esta cookie por nombre
        cookie += f"; __Secure-3PAPISID={sapisid}"
    return "\n".join([
        f"cookie: {cookie}",
        f"authorization: {sapisid_hash(sapisid, ts=ts)}",
        "x-goog-authuser: 0",
        f"user-agent: {user_agent}",
        "accept: */*",
        f"origin: {ORIGIN}",
    ])


def parse_count(count) -> int | None:
    """'1.234 canciones' / '25 songs' / 25 → número."""
    try:
        return int(str(count).replace(".", "").replace(",", "").split()[0]) if count else None
    except (ValueError, IndexError):
        return None


class YTMusicConnector(Connector):
    key = "ytmusic"
    name = "YouTube Music"

    def __init__(self, browser_file: Path, profiles_dir: Path, client=None):
        """`client` permite pasar un YTMusic falso en los tests."""
        self.browser_file = Path(browser_file)
        self.profiles_dir = Path(profiles_dir)
        self.yt = client

    # ---- sesión

    def _open(self):
        """Abre browser.json y prueba la sesión con un pedido liviano. None si no sirve.
        Una respuesta ilegible de paso no es una sesión vencida (pasó: se pedía reconectar sin motivo)."""
        from ytmusicapi import YTMusic
        try:
            yt = YTMusic(str(self.browser_file))
            self._steady(yt.get_library_playlists, 1)
            return yt
        except Exception as e:
            log.info("YouTube Music rechazó la sesión: %s: %s", type(e).__name__, e)
            return None

    def status(self) -> Status:
        if self.yt is not None:
            return Status.CONNECTED
        if not self.browser_file.exists():
            return Status.DISCONNECTED
        self.yt = self._open()
        return Status.CONNECTED if self.yt is not None else Status.EXPIRED

    def connect(self, browser: Browser | None = None, cancel: threading.Event | None = None) -> None:
        if browser is None:
            raise ConnectorError(tr("err.yt_choose_browser"))
        if browser.is_firefox:
            jar, ua = firefox.login_and_read_cookies(LOGIN_URL, "youtube.com", SESSION_COOKIES, cancel=cancel,
                                                     exe=browser.exe)
        else:
            jar, ua = cdp_login.login_and_read_cookies(browser.exe, browser.name, self.profiles_dir,
                                                       LOGIN_URL, "youtube.com", SESSION_COOKIES, cancel=cancel)
        self.save_session(jar, ua)
        self.yt = self._open()
        if self.yt is None:
            raise ConnectorError(tr("err.yt_session_rejected"))

    def login_steps(self, browser: Browser | None) -> list[str]:
        # Con Firefox se usa su sesión (no se abre una ventana aparte): los pasos son otros
        if browser is not None and browser.is_firefox:
            return [f"login.ytmusic_firefox.{i}" for i in range(1, 4)]
        return [f"login.ytmusic.{i}" for i in range(1, 5)]

    def save_session(self, jar: dict[str, str], user_agent: str) -> None:
        import ytmusicapi
        self.browser_file.parent.mkdir(parents=True, exist_ok=True)
        ytmusicapi.setup(filepath=str(self.browser_file), headers_raw=browser_headers(jar, user_agent))
        try:
            os.chmod(self.browser_file, 0o600)
        except OSError:
            pass

    def disconnect(self) -> None:
        self.browser_file.unlink(missing_ok=True)
        self.yt = None

    def forget_session(self) -> None:
        self.yt = None

    def _ready(self):
        if self.yt is None and self.status() is not Status.CONNECTED:
            raise NotConnectedError(tr("err.not_connected", service=self.name))
        return self.yt

    # ---- ids

    @staticmethod
    def parse_id(value: str) -> str:
        value = (value or "").strip()
        if "://" in value:
            q = parse_qs(urlparse(value).query)
            if "list" in q:
                return q["list"][0]
            raise ConnectorError(tr("err.yt_url_without_list", value=value))
        if not value:
            raise ConnectorError(tr("err.yt_missing_id"))
        return value

    @staticmethod
    def url(playlist_id: str) -> str:
        return f"https://music.youtube.com/playlist?list={LIKED_LIST if playlist_id == LIKED else playlist_id}"

    # ---- lectura

    @staticmethod
    def to_song(item: dict) -> Song:
        return Song(title=item.get("title") or "",
                    artists=[a["name"] for a in (item.get("artists") or []) if a.get("name")],
                    album=(item.get("album") or {}).get("name"),
                    duration=item.get("duration_seconds"),
                    id=item.get("videoId"), set_id=item.get("setVideoId"))

    def list_playlists(self) -> list[PlaylistInfo]:
        """Las canciones que te gustan (primero) y las playlists de la biblioteca."""
        liked, out = None, []
        for p in self._steady(self._ready().get_library_playlists, None):
            if p.get("playlistId") == LIKED_LIST:
                liked = parse_count(p.get("count"))
            elif p.get("playlistId") not in SKIP_PLAYLISTS:
                out.append(PlaylistInfo(p["playlistId"], p.get("title") or tr("playlist.untitled"), parse_count(p.get("count"))))
        return [liked_info(liked if liked is not None else self._liked_count())] + out

    def _steady(self, call, *args):
        """Leer una lista larga son muchos pedidos seguidos, y YouTube a veces contesta uno vacío o sin JSON
        (ytmusicapi falla al interpretarlo, antes de mirar el código HTTP). Se reintenta la lectura entera."""
        for wait in RATE_LIMIT_WAITS:
            try:
                return self.patient(call, *args)
            except json.JSONDecodeError as e:
                log.warning("YouTube Music: respuesta ilegible (%d caracteres)", len(e.doc))
                self.pause(wait, "retry")
        return self.patient(call, *args)

    def _liked_count(self) -> int | None:
        """La lista "Me gusta" es automática: en la biblioteca no trae cantidad (se veía como vacía)."""
        try:
            count = self._ready().get_playlist(LIKED_LIST, limit=1).get("trackCount")
        except Exception as e:
            log.info("YouTube Music: no pude contar los Me gusta: %s", e)
            return None
        return count if isinstance(count, int) else None

    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        pl = self._steady(self._ready().get_playlist, LIKED_LIST if playlist_id == LIKED else playlist_id, None)
        songs = [self.to_song(t) for t in pl.get("tracks", []) if t.get("title")]
        if playlist_id == LIKED:
            return tr("playlist.liked"), songs
        return pl.get("title") or tr("playlist.untitled"), songs

    def search(self, song: Song) -> list[Song]:
        yt = self._ready()
        found: dict[str, Song] = {}
        for flt in ("songs", "videos"):        # primero canciones; si no aparece, videos
            def fetch(q, flt=flt):
                return [self.to_song(item) for item in yt.search(q, filter=flt, limit=10) if item.get("videoId")]
            self.collect(song, queries_for(song), fetch, found)
            if self.good_enough(song, found):
                break
        return list(found.values())

    # ---- escritura

    def create(self, name: str, description: str, public: bool = False) -> str:
        pid = self._ready().create_playlist(name, description, privacy_status="PUBLIC" if public else "PRIVATE")
        if not isinstance(pid, str):
            raise ConnectorError(tr("err.yt_create_failed", detail=pid))
        return pid

    def add(self, playlist_id: str, songs: list[Song]) -> None:
        ids = [s.id for s in songs if s.id]
        yt = self._ready()
        if playlist_id == LIKED:
            # Se marca "Me gusta" uno por uno. Lo último que se marca queda arriba: se marcan al revés,
            # así el primero de la lista queda primero
            for k, vid in enumerate(reversed(ids), 1):
                self.patient(yt.rate_song, vid, "LIKE")
                self.adding(k, len(ids))
                time.sleep(self.search_pause)
            return
        for i in range(0, len(ids), BATCH):
            res = self.patient(lambda: yt.add_playlist_items(playlist_id, ids[i:i + BATCH], duplicates=False))
            if isinstance(res, dict) and res.get("status") not in (None, "STATUS_SUCCEEDED"):
                raise NotOwnerError(tr("err.yt_add_failed", status=res.get("status")))
            self.adding(min(i + BATCH, len(ids)), len(ids))

    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        """Hace falta el setVideoId de cada aparición: con el videoId solo se quitarían todas las copias."""
        require_confirmation(confirmed)
        if playlist_id == LIKED:                  # en "Me gusta" cada tema está una vez: se le saca el "Me gusta"
            yt = self._ready()
            for d in items:
                self.patient(yt.rate_song, d.song.id, "INDIFFERENT")
                time.sleep(self.search_pause)
            return
        payload = [{"videoId": d.song.id, "setVideoId": d.song.set_id}
                   for d in items if d.song.id and d.song.set_id]
        if len(payload) < len(items):
            raise ConnectorError(tr("err.yt_missing_set_id"))
        yt = self._ready()
        for i in range(0, len(payload), BATCH):
            yt.remove_playlist_items(playlist_id, payload[i:i + BATCH])
