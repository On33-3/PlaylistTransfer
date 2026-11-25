"""Models shared by the engine and the connectors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class Cancelled(Exception):
    """The user cancelled a running task (a transfer, a sign-in...)."""


@dataclass
class Song:
    title: str
    artists: list[str] = field(default_factory=list)
    album: str | None = None
    duration: int | None = None   # seconds
    isrc: str | None = None
    id: str | None = None         # Tidal id or YouTube videoId
    set_id: str | None = None     # (YouTube Music) id of this occurrence in the playlist, needed to remove it

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


SAME = "misma"                  # the same recording (another video, a remaster, another cover)
OTHER_VERSION = "otra_version"  # the same song in another version (live, remix, sped up...)


class Duplicate(NamedTuple):
    """A repeated copy inside a playlist. `index` is its position (from 0)."""
    index: int
    song: Song
    kept: Song         # the one that stays: always the one that appears first
    kind: str = SAME   # OTHER_VERSION: the UI shows it unticked
