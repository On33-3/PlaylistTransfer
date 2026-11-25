"""Spotify through its Web API (2026 changes included), with no extra library: `requests` is enough.

- Each user uses **their own** Spotify app (development mode: up to 5 users, and the owner needs
  Premium). Its Client ID is saved in `spotify_app.json`; the tokens, in `spotify_session.json`.
- Sign-in: Authorization Code with PKCE and a server on 127.0.0.1 (see `app/auth/loopback.py`).
- Playlists: `/items` (formerly `/tracks`). Only your own or collaborative ones can be read.
- Removing: Spotify removes by URI and takes out **every** copy. For one exact copy, all of them are
  removed and the one that stays is added back in its place; then it is read again to verify (see `remove`).
- Refresh token: expires 6 months after the first authorization → "expired" status → Reconnect.
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
DASHBOARD = "https://developer.spotify.com/dashboard"     # "your apps"
CREATE_APP = DASHBOARD + "/create"
PREMIUM = "https://www.spotify.com/premium/"
# What the user registers in their app. With a fixed port: Spotify's dashboard rejects "http://127.0.0.1/callback"
# (no port) with "This redirect URI is not secure" and won't save, even though its documentation says it's valid
# (seen by a user on 2026-11-08). With a port, it is accepted.
CALLBACK_PORT = 8973
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}{loopback.CALLBACK_PATH}"
# user-library-*: liked songs (read, save and remove)
SCOPES = ("playlist-read-private playlist-read-collaborative playlist-modify-private playlist-modify-public "
          "user-library-read user-library-modify")
LIBRARY_SCOPES = {"user-library-read", "user-library-modify"}
CLIENT_ID = re.compile(r"^[0-9a-f]{32}$")
_PLAYLIST_ID = re.compile(r"(?:playlist[/:])?([A-Za-z0-9]{22})(?:\b|$)")

PAGE = 50              # songs or playlists per request (Spotify's maximum)
ADD_BATCH = 100        # songs per request when adding (Spotify's maximum)
LIBRARY_BATCH = 40     # songs per request when saving to or removing from liked songs (/me/library, since 2026)
SEARCH_LIMIT = 10      # maximum since February 2026
MAX_WAIT = 300         # seconds: up to 5 minutes it waits (with a countdown); longer, it stops and says so
# Slower than the others: apps in development mode (each person creates their own) have a low limit,
# and thousands of songs reached it (hours of pause were asked). Each pause request slows it down further.
SEARCH_PAUSE_SPOTIFY = 0.8
MAX_SEARCH_PAUSE = 4.0


class TokenExpired(ConnectorError):
    """The permission expired (after 6 months) or was revoked: connecting again is needed."""


class RateLimited(ConnectorError):
    """Spotify asked to wait longer than is reasonable: everything stops (it is not "not found")."""


class Forbidden(ConnectorError):
    """403: Premium expired, not on the app's user list, or someone else's playlist."""


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
        """`http` (something with `.request(method, url, **kw)` like requests.Session), `open_browser` and
        `callback_server` are replaced in the tests."""
        self.session_file = Path(session_file)
        self.app_file = Path(app_file)
        self._http = http
        self._open_browser = open_browser
        self._callback_server = callback_server
        self._token: dict | None = None
        self._me: str | None = None
        self._lock = threading.Lock()

    # ---- files

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
        """Saves the Client ID of the user's app. If it changes, the previous session is no longer valid."""
        cid = valid_client_id(value)
        if cid != self.client_id:
            self.disconnect()
        self._write_json(self.app_file, {"client_id": cid})
        return cid

    def login_steps(self, browser) -> list[str]:
        return [f"login.spotify.{i}" for i in range(1, 4)]

    def setup_info(self) -> dict:
        """The steps to set up Spotify, one per action and in the order of Spotify's form.
        The UI only draws them. Each step: `text` (a dictionary key), and when needed `button` (the button
        the text names as {button}: the same text comes from a single key), `link` (that button opens
        that page), `copy` / `copy_key` (what gets pasted, with its Copy button), `input` (the Client ID
        is pasted here) and `note` (a notice below). Names in «» are the ones on Spotify's screen."""
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
        """One request to the API. Renews the token once if it expired and honors "wait" (429)."""
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
                self.search_pause = min(max(self.search_pause, SEARCH_PAUSE_SPOTIFY) * 2, MAX_SEARCH_PAUSE)                     if self.search_pause else 0      # from here on, slower
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
        """Walks a paginated Spotify list (follows the "next" link)."""
        page = self._call("GET", path, params={"limit": PAGE, **(params or {})})
        while True:
            yield from page.get("items") or []
            if not page.get("next"):
                return
            page = self._call("GET", page["next"])

    # ---- session

    def _lacks_library_permission(self) -> bool:
        """A session from before liked songs: it looks connected, but Spotify won't give the list until it's authorized again."""
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
        except ConnectorError as e:            # 403 (Premium expired, not on the user list)...
            log.info("Spotify no aceptó la sesión: %s", e)
            return Status.EXPIRED

    def connect(self, browser: Browser | None = None, cancel: threading.Event | None = None) -> None:
        cid = self.client_id
        if not cid:
            raise ConnectorError(tr("err.spotify_setup_first"))
        verifier, state = loopback.make_verifier(), secrets.token_urlsafe(16)
        try:
            server = self._callback_server(port=CALLBACK_PORT)
        except OSError as e:                 # another program is using the port
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
            # Spotify gave permission but won't allow using the API (no Premium, not on the user
            # list...): a session that doesn't work is not saved (otherwise it would say "expired")
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """Deletes the session (the tokens). The Client ID stays: it's the user's app, not a secret."""
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

    # ---- reading

    @staticmethod
    def to_song(t: dict) -> Song:
        """A song from the API. The id is its URI ("spotify:track:…"): that's what adding and removing ask for."""
        album = t.get("album") or {}
        dur = t.get("duration_ms")
        return Song(title=t.get("name") or "", artists=[a["name"] for a in t.get("artists") or [] if a.get("name")],
                    album=album.get("name") or None, duration=round(dur / 1000) if dur else None,
                    isrc=((t.get("external_ids") or {}).get("isrc") or None), id=t.get("uri") or None)

    def list_playlists(self) -> list[PlaylistInfo]:
        """Your own and collaborative ones: for the rest, Spotify doesn't allow reading or changing the songs."""
        me = self._ready()
        out = []
        try:                                       # liked songs, first
            out.append(liked_info(self._call("GET", "/me/tracks", params={"limit": 1}).get("total")))
        except Forbidden:                          # a session from before that permission was asked for: reconnecting fixes it
            log.info("Spotify: la sesión no tiene permiso para leer las canciones que te gustan")
        for p in self._pages("/me/playlists"):
            if not p or not ((p.get("owner") or {}).get("id") == me or p.get("collaborative")):
                continue
            items = p.get("items") or p.get("tracks") or {}
            total = items.get("total") if isinstance(items, dict) else None
            if total is not None and total < 2:
                # Seen on 2026-11-24: this list keeps saying 0 for a just-created playlist even when it
                # already has songs, and Remove duplicates hid it. With 0 or 1 the real count is asked for
                total = self._call("GET", f"/playlists/{p['id']}/items", params={"limit": 1}).get("total", total)
            out.append(PlaylistInfo(p["id"], p.get("name") or "", total))
        return out

    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        self._ready()
        if playlist_id == LIKED:                   # newest to oldest, as in Spotify
            songs = [self.to_song(e["track"]) for e in self._pages("/me/tracks")
                     if (e or {}).get("track") and not e["track"].get("is_local")]
            return tr("playlist.liked"), songs
        name = self._call("GET", f"/playlists/{playlist_id}", params={"fields": "name"}).get("name") or ""
        songs = []
        for entry in self._pages(f"/playlists/{playlist_id}/items"):
            t = (entry or {}).get("item") or (entry or {}).get("track")
            if t and t.get("type", "track") == "track":
                songs.append(self.to_song(t))       # podcasts are not transferred
        return name, songs

    def search(self, song: Song) -> list[Song]:
        self._ready()

        def fetch(q):
            res = self._call("GET", "/search", params={"q": q, "type": "track", "limit": SEARCH_LIMIT})
            return [self.to_song(t) for t in (res.get("tracks") or {}).get("items") or [] if t and not t.get("is_local")]
        queries = ([f"isrc:{song.isrc}"] if song.isrc else []) + list(queries_for(song))
        return list(self.collect(song, queries, fetch, fatal=(TokenExpired, NotConnectedError, RateLimited, Forbidden, NoNetwork),
                                 recoverable=ConnectorError).values())

    # ---- writing

    def create(self, name: str, description: str, public: bool = False) -> str:
        self._ready()
        return self._call("POST", "/me/playlists", body={"name": name, "description": description, "public": public})["id"]

    def add(self, playlist_id: str, songs: list[Song]) -> None:
        uris = [s.id for s in songs if s.id and s.id.startswith("spotify:track:")]
        self._ready()
        if playlist_id == LIKED:
            # Each request goes in at the top; inside one request Spotify decides the order (measured with the
            # real account on 2026-11-25). They are sent in reverse: the top batch holds the first songs of the list
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
        """Removes those occurrences. Spotify removes by URI (every copy): if some copy of a URI has to
        stay, all of them are removed and the ones that stay are added back, in their place.
        At the end the playlist is read again; if it isn't as expected, what's missing is fixed and reported."""
        require_confirmation(confirmed)
        if not items:
            return
        if playlist_id == LIKED:                   # in liked songs each song is there once: removed by URI
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
        readd = [(pos, u) for pos, u in enumerate(final) if u in gone]     # copies that have to stay
        uris = sorted(gone)
        for i in range(0, len(uris), ADD_BATCH):
            self._call("DELETE", f"/playlists/{playlist_id}/items", body={"items": [{"uri": u} for u in uris[i:i + ADD_BATCH]]})
        for pos, u in readd:                     # from the front to the back: each one lands in its final place
            self._call("POST", f"/playlists/{playlist_id}/items", body={"uris": [u], "position": pos})
        _, after = self.read(playlist_id)
        missing = Counter(final) - Counter(s.id for s in after)
        if missing:
            lost = list(missing.elements())
            self._call("POST", f"/playlists/{playlist_id}/items", body={"uris": lost})
            titles = {s.id: s.title for s in now}
            raise ConnectorError(tr("err.spotify_readded", titles=", ".join(dict.fromkeys(titles.get(u, u) for u in lost))))


def _retry_after(r) -> int:
    """The seconds Spotify asks for (1 if it doesn't say or it can't be understood)."""
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
