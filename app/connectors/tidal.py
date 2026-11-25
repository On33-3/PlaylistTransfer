"""Tidal connector (tidalapi, unofficial)."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from app.auth.browsers import Browser, open_url
from app.connectors.base import (BATCH, LIKED, Connector, ConnectorError, NotConnectedError, NotOwnerError,
                                 Status, liked_info, require_confirmation)
from app.core.matching import queries_for
from app.core.models import Duplicate, PlaylistInfo, Song
from app.core.models import Cancelled
from app.i18n import t as tr

log = logging.getLogger(__name__)

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_LINK = re.compile(r"(https?://)?(link\.tidal\.com/[A-Za-z0-9]+)")


def login_link(text: str) -> str | None:
    """tidalapi reports a text that contains the authorization link: this extracts it."""
    m = _LINK.search(text) or re.search(r"https?://\S+", text)
    if not m:
        return None
    url = m.group(0).rstrip(".,")
    return url if url.startswith("http") else f"https://{url}"


class TidalConnector(Connector):
    key = "tidal"
    name = "Tidal"

    def __init__(self, session_file: Path, session=None):
        """`session` lets the tests pass a fake session; otherwise a tidalapi one is created."""
        self.session_file = Path(session_file)
        self._s = session
        self._loaded = session is not None

    # ---- session

    @property
    def _tidalapi(self):
        import tidalapi
        return tidalapi

    def _session(self):
        if self._s is None:
            self._s = self._tidalapi.Session()
        return self._s

    def _load_saved(self) -> bool:
        if not self.session_file.exists():
            return False
        try:
            d = json.loads(self.session_file.read_text(encoding="utf-8"))
            expiry = datetime.fromisoformat(d["expiry_time"]) if d.get("expiry_time") else None
            s = self._session()
            return bool(s.load_oauth_session(d["token_type"], d["access_token"],
                                             d.get("refresh_token"), expiry)) and s.check_login()
        except Exception as e:  # expired token or broken file
            log.info("Couldn't reuse the Tidal session: %s", e)
            return False

    def _save(self) -> None:
        s = self._session()
        expiry = s.expiry_time
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(json.dumps({
            "token_type": s.token_type,
            "access_token": s.access_token,
            "refresh_token": s.refresh_token,
            "expiry_time": expiry.isoformat() if expiry else None,
        }), encoding="utf-8")
        try:
            os.chmod(self.session_file, 0o600)
        except OSError:
            pass

    def status(self) -> Status:
        if self._loaded and self._session().check_login():
            return Status.CONNECTED
        if not self.session_file.exists():
            return Status.DISCONNECTED
        if self._load_saved():
            self._loaded = True
            self._save()            # tidalapi was able to refresh the token
            return Status.CONNECTED
        return Status.EXPIRED

    def connect(self, browser: Browser | None = None, cancel: threading.Event | None = None) -> None:
        """Sign-in by link (the device one): it opens in the chosen browser and Tidal is asked every few
        seconds until the user approves it, the link expires or it is cancelled.
        (We don't use `login_oauth_simple`: it leaves a thread waiting up to 5 minutes even if cancelled.)"""
        s = self._session()
        link = s.get_link_login()
        url = login_link(link.verification_uri_complete)
        if not url:
            raise ConnectorError(tr("err.tidal_login_failed"))
        open_url(url, browser)
        deadline = time.monotonic() + link.expires_in
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise ConnectorError(tr("err.login_timeout"))
            asked = time.monotonic()
            try:
                s.process_link_login(link, until_expiry=False)    # one check (waits for the interval)
                break
            except TimeoutError:                                   # not approved yet...
                # ...or the link expired: then tidalapi answers at once, without waiting for the interval
                if time.monotonic() - asked < getattr(link, "interval", 2) / 2:
                    raise ConnectorError(tr("err.login_timeout")) from None
        if not s.check_login():
            raise ConnectorError(tr("err.tidal_login_failed"))
        self._loaded = True
        self._save()

    def login_steps(self, browser: Browser | None) -> list[str]:
        return [f"login.tidal.{i}" for i in range(1, 5)]

    def disconnect(self) -> None:
        self.session_file.unlink(missing_ok=True)
        self._s, self._loaded = None, False

    def forget_session(self) -> None:
        self._loaded = False

    def _ready(self):
        if not self._loaded and self.status() is not Status.CONNECTED:
            raise NotConnectedError(tr("err.not_connected", service=self.name))
        return self._session()

    # ---- ids

    @staticmethod
    def parse_id(value: str) -> str:
        m = _UUID.search(value or "")
        if not m:
            raise ConnectorError(tr("err.tidal_bad_id", value=value))
        return m.group(0)

    @staticmethod
    def url(playlist_id: str) -> str:
        if playlist_id == LIKED:
            return "https://tidal.com/my-collection/tracks"
        return f"https://tidal.com/playlist/{playlist_id}"

    # ---- reading

    @staticmethod
    def to_song(t) -> Song:
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
        """Liked songs (first) and the playlists the user created (the only ones songs can be
        added to)."""
        user, seen, offset = self._ready().user, set(), 0
        try:
            liked = int(user.favorites.get_tracks_count())
        except Exception as e:                    # unknown how many: offered anyway
            log.info("Tidal: couldn't count the favorites: %s", e)
            liked = None
        out = [liked_info(liked)]
        while True:
            try:
                batch = user.playlists(limit=50, offset=offset)
            except TypeError:                     # version without pagination
                batch = user.playlists()
            new = [p for p in batch or [] if str(p.id) not in seen]
            for p in new:
                seen.add(str(p.id))
                out.append(PlaylistInfo(str(p.id), p.name, getattr(p, "num_tracks", None)))
            if not new or len(batch) < 50:
                break
            offset += len(batch)
        return out

    def _favorites(self):
        return self._ready().user.favorites

    def _read_liked(self) -> tuple[str, list[Song]]:
        """The favorites, newest to oldest (as Tidal shows them)."""
        fav, songs, offset = self._favorites(), [], 0
        try:
            from tidalapi.types import ItemOrder, OrderDirection
            order = {"order": ItemOrder.Date, "order_direction": OrderDirection.Descending}
        except ImportError:                       # tests without tidalapi
            order = {}
        while True:
            batch = fav.tracks(limit=100, offset=offset, **order)
            if not batch:
                break
            songs += [self.to_song(t) for t in batch]
            offset += len(batch)
            if len(batch) < 100:
                break
        return tr("playlist.liked"), songs

    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        if playlist_id == LIKED:
            return self._read_liked()
        pl = self._ready().playlist(playlist_id)
        songs, offset = [], 0
        while True:
            batch = pl.tracks(limit=100, offset=offset)
            if not batch:
                break
            songs += [self.to_song(t) for t in batch]
            offset += len(batch)
            if len(batch) < 100:
                break
        return pl.name, songs

    def search(self, song: Song) -> list[Song]:
        s = self._ready()
        found: dict[str, Song] = {}
        # 1) By ISRC, if the tidalapi version supports it (exact match)
        if song.isrc and hasattr(s, "get_tracks_by_isrc"):
            try:
                for t in s.get_tracks_by_isrc(song.isrc) or []:
                    c = self.to_song(t)
                    found[c.id] = c
            except Exception as e:
                log.warning("Tidal: ISRC search %s failed: %s", song.isrc, e)
        # 2) By text
        def fetch(q):
            res = s.search(q, models=[self._track_model()], limit=10)
            tracks = res.get("tracks", []) if isinstance(res, dict) else getattr(res, "tracks", [])
            return [self.to_song(t) for t in tracks or []]
        return list(self.collect(song, queries_for(song), fetch, found).values())

    def _track_model(self):
        try:
            return self._tidalapi.Track
        except ModuleNotFoundError:   # tests with a fake session and without tidalapi
            return "Track"

    # ---- writing

    def create(self, name: str, description: str, public: bool = False) -> str:
        return str(self._ready().user.create_playlist(name, description).id)

    def _editable(self, playlist_id: str):
        pl = self._ready().playlist(playlist_id)
        if not hasattr(pl, "add") and hasattr(pl, "factory"):
            pl = pl.factory()
        if not hasattr(pl, "add"):
            raise NotOwnerError(tr("err.tidal_not_owner", id=playlist_id))
        return pl

    def add(self, playlist_id: str, songs: list[Song]) -> None:
        ids = [s.id for s in songs if s.id]
        if not ids:
            return
        if playlist_id == LIKED:
            # Each request goes in at the top; inside one request Tidal decides the order (measured with the real
            # account on 2026-11-25). They are sent in reverse: the top batch holds the first songs of the list
            fav = self._favorites()
            ids.reverse()
            for i in range(0, len(ids), BATCH):
                if self.patient(fav.add_track, ids[i:i + BATCH]) is False:
                    raise ConnectorError(tr("err.liked_add_failed", service=self.name))
                self.adding(min(i + BATCH, len(ids)), len(ids))
            return
        pl = self._editable(playlist_id)
        for i in range(0, len(ids), BATCH):
            self.patient(pl.add, ids[i:i + BATCH])
            self.adding(min(i + BATCH, len(ids)), len(ids))

    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        require_confirmation(confirmed)
        if not items:
            return
        if playlist_id == LIKED:                  # in favorites each song is there once: removed by id
            fav = self._favorites()
            for d in items:
                fav.remove_track(d.song.id)
            return
        pl = self._editable(playlist_id)
        for index in sorted({d.index for d in items}, reverse=True):   # from the back to the front
            pl.remove_by_index(index)
