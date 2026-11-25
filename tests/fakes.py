"""Un servicio de música en memoria que cumple la interfaz Connector, para probar sin cuentas."""
from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.connectors import spotify as sp
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



def build_api(tmp_path, connectors: dict, browsers=(), system_language: str = "es", prefs: Path | None = None):
    """La API real con servicios falsos y todo lo de afuera (navegadores, abrir links, guardar archivos)
    reemplazado. Lo que haga la app queda anotado: api._open_url_calls (links), api._asked (nombres que
    propuso el diálogo "Guardar como")."""
    from app.api import Api
    from app.storage.history import History
    api = Api(connectors, History(tmp_path / "historial.sqlite"), prefs or tmp_path / "prefs.json", tmp_path,
              list_browsers=lambda: list(browsers), open_url=lambda url, b: api._open_url_calls.append(url),
              system_language=lambda: system_language)
    api._open_url_calls = []
    api._asked = []
    api._save_dialog = lambda name: (api._asked.append(name), str(tmp_path / "exportado" / name))[1]
    api._open_file = lambda p: None
    return api

# ---------------------------------------------------------------- la API de Spotify, en memoria

def track(n, name, artist, isrc=None, dur=200, album="Album"):
    return {"type": "track", "uri": f"spotify:track:{n:0>22}", "name": name, "artists": [{"name": artist}],
            "album": {"name": album}, "duration_ms": dur * 1000, "external_ids": {"isrc": isrc} if isrc else {},
            "is_local": False}


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("sin cuerpo")
        return self._body


class FakeSpotify:
    """Lo justo de la Web API para probar el conector, con las reglas de 2026."""

    def __init__(self):
        self.me = "yo"
        self.catalog: dict[str, dict] = {}
        self.playlists: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.forced: list[Resp] = []          # respuestas que se devuelven antes que las normales
        self.refresh_ok = True
        self.ignore_adds = False              # simula que Spotify no guardó lo agregado
        self.tokens = 0
        self.liked: list[str] = []            # canciones que te gustan, la más reciente primero
        self.library_scope = True             # False: sesión de antes de pedir permiso para "Me gusta"

    def add_tracks(self, *tracks):
        for t in tracks:
            self.catalog[t["uri"]] = t

    def add_playlist(self, pid, name, uris, owner="yo", collaborative=False, listed=None):
        """`listed`: la cantidad que dice /me/playlists, si no es la real (pasa con una recién creada)."""
        self.playlists[pid] = {"name": name, "owner": owner, "collaborative": collaborative, "items": list(uris),
                               "listed": listed}

    def uris(self, pid):
        return self.playlists[pid]["items"]

    def _page(self, items, params, url):
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 20))
        assert limit <= 50
        page = items[offset:offset + limit]
        nxt = f"{url.split('?')[0]}?offset={offset + limit}&limit={limit}" if offset + limit < len(items) else None
        return Resp(200, {"items": page, "next": nxt, "total": len(items)})

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):
        u = urlparse(url)
        params = {**{k: v[0] for k, v in parse_qs(u.query).items()}, **(params or {})}
        path = u.path.replace("/v1", "", 1)
        self.calls.append((method, path, params, json, data))
        if url == sp.TOKEN:
            if data["grant_type"] == "refresh_token" and not self.refresh_ok:
                return Resp(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"})
            self.tokens += 1
            return Resp(200, {"access_token": f"tok{self.tokens}", "refresh_token": "ref", "expires_in": 3600, "scope": sp.SCOPES})
        if self.forced:
            return self.forced.pop(0)
        assert headers["Authorization"].startswith("Bearer tok")
        if (method, path) == ("GET", "/me"):
            return Resp(200, {"id": self.me})
        if (method, path) == ("GET", "/me/playlists"):
            items = [{"id": k, "name": v["name"], "owner": {"id": v["owner"]}, "collaborative": v["collaborative"],
                      "items": {"total": len(v["items"]) if v["listed"] is None else v["listed"]}}
                     for k, v in self.playlists.items()]
            return self._page(items, params, url)
        if (method, path) == ("POST", "/me/playlists"):
            pid = f"{len(self.playlists) + 1:0>22}"
            self.add_playlist(pid, json["name"], [])
            return Resp(201, {"id": pid})
        # Canciones que te gustan: se leen con /me/tracks (lo último guardado primero, como "track") y desde
        # 2026 se guardan y quitan con /me/library (URIs separadas por coma en la dirección, hasta 40)
        if path in ("/me/tracks", "/me/library") and not self.library_scope:
            return Resp(403, {"error": {"status": 403, "message": "Insufficient client scope"}})
        if (method, path) == ("GET", "/me/tracks"):
            return self._page([{"track": self.catalog[x]} for x in self.liked], params, url)
        if path == "/me/library":
            uris = params["uris"].split(",")
            assert len(uris) <= 40 and json is None
            if method == "PUT":
                # Medido con una cuenta real (2026-11-25, tools/real_check.py): el pedido entra arriba, pero
                # el orden dentro del pedido lo decide Spotify, no el orden en que se mandan. Acá: por URI, al
                # revés (a propósito distinto del de la lista, para que ningún test dependa de él)
                self.liked[0:0] = sorted((x for x in dict.fromkeys(uris) if x not in self.liked), reverse=True)
            else:
                self.liked = [x for x in self.liked if x not in uris]
            return Resp(200, None)
        if path == "/search":
            assert int(params["limit"]) <= 10
            q = params["q"]
            if q.startswith("isrc:"):
                hits = [t for t in self.catalog.values() if t["external_ids"].get("isrc") == q[5:]]
            else:
                words = q.lower().replace('"', " ").split()
                hits = [t for t in self.catalog.values() if any(w in t["name"].lower() for w in words)]
            return Resp(200, {"tracks": {"items": hits[:int(params["limit"])]}})
        parts = path.strip("/").split("/")
        pl = self.playlists.get(parts[1]) if len(parts) > 1 else None
        if pl is None:
            return Resp(404, {"error": {"status": 404, "message": "Not found"}})
        mine = pl["owner"] == self.me or pl["collaborative"]
        if len(parts) == 2 and method == "GET":
            return Resp(200, {"name": pl["name"]})
        if parts[2:] == ["items"]:
            if not mine:
                return Resp(403, {"error": {"status": 403, "message": "Forbidden"}})
            if method == "GET":
                return self._page([{"item": self.catalog[x]} if x in self.catalog else {"item": None}
                                   for x in pl["items"]], params, url)
            if method == "POST":
                assert len(json["uris"]) <= 100
                if not self.ignore_adds:
                    pos = json.get("position", len(pl["items"]))
                    pl["items"][pos:pos] = json["uris"]
                return Resp(201, {"snapshot_id": "s"})
            if method == "DELETE":
                assert "positions" not in str(json)              # ya no existe: siempre por URI
                gone = {x["uri"] for x in json["items"]}
                pl["items"] = [x for x in pl["items"] if x not in gone]     # todas las copias
                return Resp(200, {"snapshot_id": "s"})
        raise AssertionError(f"pedido inesperado: {method} {path}")
