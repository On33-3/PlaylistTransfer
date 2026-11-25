"""The interface every music service implements. The rest of the app only talks to this."""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from enum import Enum

from app.auth.browsers import Browser
from app.core.matching import SCORE_OK, best_match
from app.core.models import Cancelled, Duplicate, PlaylistInfo, Song
from app.i18n import t as tr

log = logging.getLogger(__name__)

BATCH = 50           # songs per request when adding or removing
# Liked songs ("Me gusta", Liked Songs, favorites): they aren't a playlist in any service,
# but the app shows them as one more, first in the list, with this id (each connector translates it)
LIKED = "__liked__"


def liked_info(count: int | None) -> PlaylistInfo:
    return PlaylistInfo(LIKED, tr("playlist.liked"), count)
SEARCH_PAUSE = 0.3   # seconds between searches, so the APIs aren't flooded
RATE_LIMIT_WAITS = (2, 5, 15)   # seconds to wait if the service asks to slow down; after that, it counts as failed
MAX_RATE_LIMIT_WAIT = 60        # even if the service asks for more
FAILURES_IN_A_ROW = 3   # search rounds in a row without any query answering: the service isn't responding


def rate_limit_pause(e: BaseException) -> float | None:
    """If the error is "too many requests, wait": how many seconds it asks for (0 if it doesn't say). Otherwise None.
    tidalapi raises TooManyRequests with `retry_after`; requests, an HTTPError 429; ytmusicapi says it in the text."""
    response = getattr(e, "response", None)
    if type(e).__name__ == "TooManyRequests" or getattr(response, "status_code", None) == 429 or "HTTP 429" in str(e):
        for value in (getattr(e, "retry_after", None), getattr(response, "headers", {}).get("Retry-After") if response is not None else None):
            try:
                if value is not None and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):       # Retry-After can also be a date
                pass
        return 0
    return None


def human_wait(seconds: float) -> str:
    """"45 s", "12 min", "3 h 20 min": how long a service asked to wait."""
    s = max(1, round(seconds))
    if s < 60:
        return tr("time.seconds", n=s)
    m = -(-s // 60)
    if m < 60:
        return tr("time.minutes", n=m)
    h, m = divmod(m, 60)
    return tr("time.hours_minutes", h=h, m=m) if m else tr("time.hours", h=h)


def clock_after(seconds: float, now=None) -> str:
    """The time the wait ends ("18:40"); with the date if it's another day ("11/11 18:40")."""
    from datetime import datetime, timedelta
    now = now or datetime.now()
    end = now + timedelta(seconds=seconds)
    return f"{end:%H:%M}" if end.date() == now.date() else f"{end:%d/%m %H:%M}"


class Status(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"            # there is a saved session, but the service no longer accepts it
    DISCONNECTED = "disconnected"
    SETUP = "setup"                # something has to be set up before connecting (Spotify: the Client ID)


class ConnectorError(Exception):
    """An error that can be shown to the user as it is."""


class NotConnectedError(ConnectorError):
    pass


class NoNetwork(ConnectorError):
    """Could not talk to the service (no internet, or it doesn't answer)."""


def is_network_error(e: BaseException) -> bool:
    """Connection errors from `requests` (used by tidalapi, ytmusicapi and Spotify): no internet or no answer."""
    try:
        import requests
    except ImportError:
        return False
    return isinstance(e, (requests.ConnectionError, requests.Timeout))


class NotOwnerError(ConnectorError):
    """The playlist isn't the user's: songs can't be added to it or removed from it."""


class RemovalNotConfirmedError(ConnectorError):
    """Removing songs was attempted without the user's explicit confirmation."""


class Connector(ABC):
    key: str          # "tidal", "ytmusic"...
    name: str         # "Tidal", "YouTube Music"...
    search_pause: float = SEARCH_PAUSE
    # True if the service removes by song, not by position (Spotify): to remove one exact copy,
    # all of them are removed and the one that stays is added back (with a new "added" date)
    removes_all_copies: bool = False

    @abstractmethod
    def status(self) -> Status: ...

    @abstractmethod
    def connect(self, browser: Browser | None = None, cancel: threading.Event | None = None) -> None:
        """Signs in (it may open the browser and block until the user finishes).
        If `cancel` is set while waiting, it stops and raises `Cancelled`."""

    def collect(self, song: Song, queries: Iterable[str], fetch: Callable[[str], Iterable[Song]],
                found: dict[str, Song] | None = None, *, fatal: tuple[type[BaseException], ...] = (),
                recoverable: type[BaseException] = Exception) -> dict[str, Song]:
        """The search every service uses: tries the queries in order and stops as soon as there is a
        good match (SCORE_OK). `fetch(q)` runs one query and returns songs; they are gathered without
        repeats in `found` (which may already hold earlier results, such as the ISRC ones).
        If a query fails (`recoverable`) it is noted and the next one runs; `fatal` errors (expired
        session, the service asked to wait) stop everything: otherwise every song would come out "not found"."""
        found = {} if found is None else found
        tried = answered = 0
        for q in queries:
            tried += 1
            try:
                results = self.patient(fetch, q)
            except fatal:
                raise
            except recoverable as e:
                log.warning("%s: search %r failed: %s", self.name, q, e)
                continue
            answered += 1
            for c in results:
                if c.id:
                    found.setdefault(c.id, c)
            time.sleep(self.search_pause)
            if self.good_enough(song, found):
                break
        self._count_search_failure(tried > 0 and answered == 0)
        return found

    def patient(self, call: Callable, *args):
        """Makes a request; if the service asks to slow down (very common when liking thousands of songs or
        searching a lot), it waits and retries a few times instead of failing the song."""
        for wait in RATE_LIMIT_WAITS:
            try:
                return call(*args)
            except Exception as e:
                asked = rate_limit_pause(e)
                if asked is None:
                    raise
                self.pause(min(asked or wait, MAX_RATE_LIMIT_WAIT))
        return call(*args)

    # ---- waits: always announced (the person sees a countdown) and can be cancelled

    _listener: Callable[[dict], None] | None = None
    _cancel: threading.Event | None = None

    def listen(self, emit: Callable[[dict], None] | None, cancel: threading.Event | None = None) -> None:
        """While a task runs: where to announce waits and what to watch to stop them. (None, None) at the end."""
        self._listener, self._cancel = emit, cancel

    def adding(self, done: int, total: int) -> None:
        """How many songs have been added so far: thousands of liked songs in YouTube Music (one at a time)
        take many minutes, and without this the screen looked frozen."""
        if self._listener is not None:
            self._listener({"type": "adding", "service": self.name, "done": done, "total": total})

    def pause(self, seconds: float, why: str = "rate") -> None:
        """Waits as long as needed, saying why: "rate" (the service asked for a break) or "retry"
        (it answered badly and is retried). Checks Cancel every half second."""
        log.warning("%s: %s, waiting %s s", self.name, why, round(seconds, 1))
        if self._listener is not None:
            self._listener({"type": "waiting", "service": self.name, "why": why, "seconds": round(seconds)})
        left = float(seconds)
        while left > 0:
            if self._cancel is not None and self._cancel.is_set():
                raise Cancelled()
            step = min(0.5, left)
            time.sleep(step)
            left -= step
        if self._listener is not None:
            self._listener({"type": "waited", "service": self.name})

    def _count_search_failure(self, failed: bool) -> None:
        """A search that fails now and then is skipped. But if several songs in a row fail in all their
        queries, the service isn't answering (expired session, no internet): going on would mark every
        song "not found". It stops before changing anything (a transfer only adds at the end) and
        forgets the session in memory, so Connections checks it again."""
        self._failed_in_a_row = getattr(self, "_failed_in_a_row", 0) + 1 if failed else 0
        if self._failed_in_a_row >= FAILURES_IN_A_ROW:
            self._failed_in_a_row = 0
            self.forget_session()
            raise ConnectorError(tr("err.search_failing", service=self.name))

    def reset_search_failures(self) -> None:
        """When a transfer starts: what failed in an earlier one doesn't count."""
        self._failed_in_a_row = 0

    def forget_session(self) -> None:
        """Forgets the session held in memory (not the saved one): next time it is checked again."""

    @staticmethod
    def good_enough(song: Song, found: dict[str, Song]) -> bool:
        return bool(found) and best_match(song, list(found.values()))[1] >= SCORE_OK

    def login_steps(self, browser: Browser | None) -> list[str]:
        """What the person does while connecting, one action per step: keys of the "ui" section of the
        dictionary (they can use {browser}). The UI numbers them under the service while it waits."""
        return []

    @abstractmethod
    def disconnect(self) -> None:
        """Deletes the saved session."""

    @abstractmethod
    def list_playlists(self) -> list[PlaylistInfo]:
        """The user's playlists that songs can be added to."""

    @abstractmethod
    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        """(name, songs in order)."""

    @abstractmethod
    def search(self, song: Song) -> list[Song]:
        """Candidates for `song` in this service (not chosen: core.matching does that)."""

    @abstractmethod
    def create(self, name: str, description: str, public: bool = False) -> str:
        """Creates a playlist and returns its id."""

    @abstractmethod
    def add(self, playlist_id: str, songs: list[Song]) -> None: ...

    @abstractmethod
    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        """Removes those occurrences. Requires `confirmed=True`: only after the user confirmed in the UI."""

    @staticmethod
    @abstractmethod
    def parse_id(value: str) -> str:
        """Playlist ID from a URL or from the ID itself."""

    @staticmethod
    @abstractmethod
    def url(playlist_id: str) -> str: ...


def require_confirmation(confirmed: bool) -> None:
    if confirmed is not True:
        raise RemovalNotConfirmedError(tr("err.removal_not_confirmed"))
