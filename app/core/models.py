"""Modelos que comparten el motor y los conectores."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


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


class Duplicate(NamedTuple):
    """Una copia repetida dentro de una playlist. `index` es su posición (desde 0)."""
    index: int
    song: Song
    kept: Song    # la que queda: siempre la que aparece primero
