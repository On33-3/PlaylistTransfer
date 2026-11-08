"""Un servicio de música en memoria que cumple la interfaz Connector, para probar sin cuentas."""
from __future__ import annotations

import itertools
from dataclasses import replace

from app.connectors.base import (Connector, ConnectorError, NotConnectedError, Status,
                                 require_confirmation)
from app.core.matching import norm
from app.core.models import Cancelled, Duplicate, PlaylistInfo, Song


class FakeService(Connector):
    def __init__(self, key: str, name: str, catalog: list[Song] | None = None, connected: bool = True):
        self.key, self.name = key, name
        self.catalog = catalog or []            # lo que devuelve la búsqueda
        self.playlists: dict[str, tuple[str, list[Song]]] = {}
        self.connected = connected
        self.calls: list[tuple] = []            # registro de lo que modificó algo
        self._ids = itertools.count(1)
        self._set_ids = itertools.count(1)
        self.search_pause = 0
        self.login_hangs = False                # True: el login espera hasta que se cancele

    # ---- armado de datos para los tests
    def add_playlist(self, pid: str, name: str, songs: list[Song]) -> None:
        self.playlists[pid] = (name, [replace(s, set_id=s.set_id or f"set{next(self._set_ids)}") for s in songs])

    def songs(self, pid: str) -> list[Song]:
        return self.playlists[pid][1]

    # ---- Connector
    def status(self) -> Status:
        return Status.CONNECTED if self.connected else Status.DISCONNECTED

    def connect(self, browser=None, cancel=None) -> None:
        self.calls.append(("connect", browser.name if browser else None))
        while self.login_hangs and not (cancel and cancel.wait(0.05)):
            pass
        if self.login_hangs:
            raise Cancelled()
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def _ready(self):
        if not self.connected:
            raise NotConnectedError(f"{self.name} no está conectado.")

    def list_playlists(self) -> list[PlaylistInfo]:
        self._ready()
        return [PlaylistInfo(pid, name, len(songs)) for pid, (name, songs) in self.playlists.items()]

    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        self._ready()
        if playlist_id not in self.playlists:
            raise ConnectorError(f"No existe la playlist {playlist_id}.")
        name, songs = self.playlists[playlist_id]
        return name, list(songs)

    def search(self, song: Song) -> list[Song]:
        self._ready()
        words = set(norm(song.title).split())
        return [c for c in self.catalog if words & set(norm(c.title).split())]

    def create(self, name: str, description: str, public: bool = False) -> str:
        self._ready()
        pid = f"new{next(self._ids)}"
        self.playlists[pid] = (name, [])
        self.calls.append(("create", name))
        return pid

    def add(self, playlist_id: str, songs: list[Song]) -> None:
        self._ready()
        self.calls.append(("add", playlist_id, [s.id for s in songs]))
        self.playlists[playlist_id][1].extend(replace(s, set_id=f"set{next(self._set_ids)}") for s in songs)

    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        require_confirmation(confirmed)
        self._ready()
        self.calls.append(("remove", playlist_id, sorted(d.index for d in items)))
        songs = self.playlists[playlist_id][1]
        for i in sorted({d.index for d in items}, reverse=True):
            del songs[i]

    @staticmethod
    def parse_id(value: str) -> str:
        return value

    def url(self, playlist_id: str) -> str:
        return f"https://music.youtube.com/playlist?list={playlist_id}"


def S(title, *artists, dur=200, id=None, album=None) -> Song:
    return Song(title, list(artists), album=album, duration=dur, id=id or f"{norm(title)}|{','.join(artists)}")
