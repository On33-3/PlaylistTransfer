"""Conector de YouTube Music (ytmusicapi, no oficial), con sesión de navegador."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.auth import cdp_login, firefox
from app.auth.browsers import Browser
from app.auth.cdp_login import LoginError
from app.connectors.base import (BATCH, Connector, ConnectorError, NotConnectedError, NotOwnerError,
                                 Status, require_confirmation)
from app.core.matching import SCORE_OK, best_match, queries_for
from app.core.models import Duplicate, PlaylistInfo, Song
from app.i18n import t as tr

log = logging.getLogger(__name__)

LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube&passive=true&continue="
             "https%3A%2F%2Fwww.youtube.com%2Fsignin%3Faction_handle_signin%3Dtrue%26app%3Ddesktop"
             "%26next%3Dhttps%253A%252F%252Fmusic.youtube.com%252F")
ORIGIN = "https://music.youtube.com"
SESSION_COOKIES = {"__Secure-3PAPISID", "SAPISID"}
SKIP_PLAYLISTS = {"LM", "SE"}   # "Me gusta" y "Episodios": no admiten agregar temas a mano


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
        """Abre browser.json y prueba la sesión con un pedido liviano. None si no sirve."""
        from ytmusicapi import YTMusic
        try:
            yt = YTMusic(str(self.browser_file))
            yt.get_library_playlists(limit=1)
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
            jar, ua = firefox.login_and_read_cookies(LOGIN_URL, "youtube.com", SESSION_COOKIES, cancel=cancel)
        else:
            jar, ua = cdp_login.login_and_read_cookies(browser.exe, browser.name, self.profiles_dir,
                                                       LOGIN_URL, "youtube.com", SESSION_COOKIES, cancel=cancel)
        self.save_session(jar, ua)
        self.yt = self._open()
        if self.yt is None:
            raise ConnectorError(tr("err.yt_session_rejected"))

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
        return f"https://music.youtube.com/playlist?list={playlist_id}"

    # ---- lectura

    @staticmethod
    def to_song(item: dict) -> Song:
        return Song(title=item.get("title") or "",
                    artists=[a["name"] for a in (item.get("artists") or []) if a.get("name")],
                    album=(item.get("album") or {}).get("name"),
                    duration=item.get("duration_seconds"),
                    id=item.get("videoId"), set_id=item.get("setVideoId"))

    def list_playlists(self) -> list[PlaylistInfo]:
        out = []
        for p in self._ready().get_library_playlists(limit=None):
            if p.get("playlistId") in SKIP_PLAYLISTS:
                continue
            out.append(PlaylistInfo(p["playlistId"], p.get("title") or "(sin nombre)", parse_count(p.get("count"))))
        return out

    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        pl = self._ready().get_playlist(playlist_id, limit=None)
        songs = [self.to_song(t) for t in pl.get("tracks", []) if t.get("title")]
        return pl.get("title") or "Playlist", songs

    def search(self, song: Song) -> list[Song]:
        yt = self._ready()
        found: dict[str, Song] = {}
        for flt in ("songs", "videos"):        # primero canciones; si no aparece, videos
            for q in queries_for(song):
                try:
                    results = yt.search(q, filter=flt, limit=10)
                except Exception as e:
                    log.warning("YouTube Music: falló la búsqueda %r: %s", q, e)
                    continue
                for item in results:
                    if item.get("videoId"):
                        c = self.to_song(item)
                        found.setdefault(c.id, c)
                time.sleep(self.search_pause)
                if found and best_match(song, list(found.values()))[1] >= SCORE_OK:
                    return list(found.values())
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
        for i in range(0, len(ids), BATCH):
            res = yt.add_playlist_items(playlist_id, ids[i:i + BATCH], duplicates=False)
            if isinstance(res, dict) and res.get("status") not in (None, "STATUS_SUCCEEDED"):
                raise NotOwnerError(tr("err.yt_add_failed", status=res.get("status")))

    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        """Hace falta el setVideoId de cada aparición: con el videoId solo se quitarían todas las copias."""
        require_confirmation(confirmed)
        payload = [{"videoId": d.song.id, "setVideoId": d.song.set_id}
                   for d in items if d.song.id and d.song.set_id]
        if len(payload) < len(items):
            raise ConnectorError(tr("err.yt_missing_set_id"))
        yt = self._ready()
        for i in range(0, len(payload), BATCH):
            yt.remove_playlist_items(playlist_id, payload[i:i + BATCH])
