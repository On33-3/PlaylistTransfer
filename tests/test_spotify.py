"""Spotify contra una API falsa en memoria que se comporta como la real (2026): `/items`, búsqueda de
hasta 10, quitar por URI saca todas las copias, 429 con Retry-After, refresh token que vence."""
from __future__ import annotations

import threading
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from app.auth import loopback
from app.connectors import spotify as sp
from app.connectors.base import ConnectorError, NotOwnerError, RemovalNotConfirmedError, Status
from app.connectors.spotify import SpotifyConnector
from app.core.matching import find_duplicates
from app.core.models import Cancelled, Duplicate, Song

CID = "0123456789abcdef0123456789abcdef"


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

    def add_tracks(self, *tracks):
        for t in tracks:
            self.catalog[t["uri"]] = t

    def add_playlist(self, pid, name, uris, owner="yo", collaborative=False):
        self.playlists[pid] = {"name": name, "owner": owner, "collaborative": collaborative, "items": list(uris)}

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
            return Resp(200, {"access_token": f"tok{self.tokens}", "refresh_token": "ref", "expires_in": 3600})
        if self.forced:
            return self.forced.pop(0)
        assert headers["Authorization"].startswith("Bearer tok")
        if (method, path) == ("GET", "/me"):
            return Resp(200, {"id": self.me})
        if (method, path) == ("GET", "/me/playlists"):
            items = [{"id": k, "name": v["name"], "owner": {"id": v["owner"]}, "collaborative": v["collaborative"],
                      "items": {"total": len(v["items"])}} for k, v in self.playlists.items()]
            return self._page(items, params, url)
        if (method, path) == ("POST", "/me/playlists"):
            pid = f"{len(self.playlists) + 1:0>22}"
            self.add_playlist(pid, json["name"], [])
            return Resp(201, {"id": pid})
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


@pytest.fixture
def api():
    return FakeSpotify()


def make(tmp_path, api, client_id=CID, logged=True, **kw):
    c = SpotifyConnector(tmp_path / "spotify_session.json", tmp_path / "spotify_app.json", http=api, **kw)
    c.search_pause = 0
    if client_id:
        c.set_client_id(client_id)
    if logged:
        c._store({"access_token": "tok0", "refresh_token": "ref", "expires_in": 3600}, first=True)
    return c


def song_of(t):
    return SpotifyConnector.to_song(t)


# ---------------------------------------------------------------- configuración y sesión

def test_needs_a_client_id_first(tmp_path, api):
    c = make(tmp_path, api, client_id=None, logged=False)
    assert c.status() is Status.SETUP
    with pytest.raises(ConnectorError, match="Client ID"):
        c.set_client_id("no-es-un-id")
    with pytest.raises(ConnectorError, match="configurá Spotify"):
        c.connect(None)
    assert c.set_client_id("  " + CID.upper() + " ") == CID       # se limpia: espacios y mayúsculas
    assert c.status() is Status.DISCONNECTED
    assert c.setup_info() == {"dashboard": sp.DASHBOARD, "redirect_uri": "http://127.0.0.1/callback", "client_id": CID}


def test_changing_the_client_id_drops_the_old_session(tmp_path, api):
    c = make(tmp_path, api)
    assert c.status() is Status.CONNECTED
    c.set_client_id(CID)                                            # el mismo: no cambia nada
    assert c.session_file.exists()
    c.set_client_id("f" * 32)
    assert not c.session_file.exists() and c.status() is Status.DISCONNECTED


def test_disconnect_keeps_the_client_id(tmp_path, api):
    c = make(tmp_path, api)
    c.disconnect()
    assert c.status() is Status.DISCONNECTED and c.client_id == CID


def test_login_with_pkce_through_the_local_server(tmp_path, api):
    """De punta a punta: el servidor real en 127.0.0.1 recibe el código que manda el "navegador"."""
    seen = {}

    def browser_opens(url, browser):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(q)
        assert url.startswith(sp.AUTHORIZE) and q["client_id"] == CID and q["code_challenge_method"] == "S256"
        assert urlparse(q["redirect_uri"]).hostname == "127.0.0.1"          # nunca "localhost"
        back = f"{q['redirect_uri']}?code=CODE123&state={q['state']}"
        threading.Thread(target=lambda: seen.setdefault("page", urllib.request.urlopen(back).read().decode())).start()

    c = make(tmp_path, api, logged=False, open_browser=browser_opens)
    c.connect(None)
    exchange = next(d for m, p, _, _, d in api.calls if d and d.get("grant_type") == "authorization_code")
    assert exchange["code"] == "CODE123" and exchange["redirect_uri"] == seen["redirect_uri"]
    assert loopback.challenge(exchange["code_verifier"]) == seen["code_challenge"]          # PKCE
    assert c.status() is Status.CONNECTED and c.session_file.exists()
    assert "PlaylistTransfer" in seen["page"]


def test_login_rejects_an_answer_with_another_state(tmp_path, api):
    def browser_opens(url, browser):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        threading.Thread(target=lambda: urllib.request.urlopen(f"{q['redirect_uri']}?code=X&state=otro").read()).start()
    c = make(tmp_path, api, logged=False, open_browser=browser_opens)
    with pytest.raises(ConnectorError, match="no era la esperada"):
        c.connect(None)
    assert not c.session_file.exists()


def test_login_when_the_user_says_no(tmp_path, api):
    def browser_opens(url, browser):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        threading.Thread(target=lambda: urllib.request.urlopen(f"{q['redirect_uri']}?error=access_denied&state={q['state']}").read()).start()
    c = make(tmp_path, api, logged=False, open_browser=browser_opens)
    with pytest.raises(ConnectorError, match="access_denied"):
        c.connect(None)


def test_login_can_be_cancelled(tmp_path, api):
    cancel = threading.Event()
    c = make(tmp_path, api, logged=False, open_browser=lambda url, b: threading.Timer(0.2, cancel.set).start())
    with pytest.raises(Cancelled):
        c.connect(None, cancel=cancel)
    assert not c.session_file.exists()


def test_local_server_only_listens_on_loopback():
    with loopback.CallbackServer() as cb:
        assert cb._server.server_address[0] == "127.0.0.1" and cb.port > 0
        assert cb.redirect_uri == f"http://127.0.0.1:{cb.port}/callback"


def test_expired_token_is_refreshed(tmp_path, api):
    c = make(tmp_path, api)
    c._token["expires_at"] = 0
    assert c.status() is Status.CONNECTED
    assert any(d and d.get("grant_type") == "refresh_token" for *_, d in api.calls)


def test_refresh_token_that_expired_after_six_months(tmp_path, api):
    c = make(tmp_path, api)
    c._token["expires_at"] = 0
    api.refresh_ok = False
    assert c.status() is Status.EXPIRED


def test_unauthorized_retries_once_with_a_new_token(tmp_path, api):
    c = make(tmp_path, api)
    api.forced = [Resp(401, {"error": {"status": 401, "message": "expired"}})]
    assert c.status() is Status.CONNECTED and api.tokens == 1


def test_forbidden_explains_premium_and_user_list(tmp_path, api):
    c = make(tmp_path, api)
    api.forced = [Resp(403, {"error": {"status": 403, "message": "Forbidden"}})]
    assert c.status() is Status.EXPIRED
    api.forced = [Resp(403, {})]
    c._me = "yo"
    with pytest.raises(ConnectorError, match="Premium"):
        c.read("x" * 22)


def test_waits_when_spotify_asks(tmp_path, api, monkeypatch):
    slept = []
    monkeypatch.setattr(sp.time, "sleep", slept.append)
    c = make(tmp_path, api)
    api.forced = [Resp(429, {}, {"Retry-After": "2"})]
    assert c.status() is Status.CONNECTED and slept == [2]
    c._me = None
    api.forced = [Resp(429, {}, {"Retry-After": "3600"})]           # una hora: se avisa, no se espera
    with pytest.raises(ConnectorError, match="3600"):
        c._call("GET", "/me")


# ---------------------------------------------------------------- leer y buscar

def test_lists_only_own_and_collaborative_playlists_across_pages(tmp_path, api):
    for i in range(60):
        api.add_playlist(f"p{i:0>21}", f"Mía {i}", [])
    api.add_playlist("ajena".ljust(22, "0"), "De otro", [], owner="otro")
    api.add_playlist("colab".ljust(22, "0"), "Colaborativa", [], owner="otro", collaborative=True)
    names = [p.name for p in make(tmp_path, api).list_playlists()]
    assert len(names) == 61 and "De otro" not in names and "Colaborativa" in names


def test_reads_every_song_with_isrc_and_skips_podcasts(tmp_path, api):
    tracks = [track(i, f"Tema {i}", "Artista", isrc=f"US{i:0>10}", dur=181) for i in range(120)]
    api.add_tracks(*tracks)
    episode = {"type": "episode", "uri": "spotify:episode:1", "name": "Podcast"}
    api.catalog[episode["uri"]] = episode
    api.add_playlist("p" * 22, "Larga", [t["uri"] for t in tracks] + [episode["uri"], "spotify:track:borrado"])
    name, songs = make(tmp_path, api).read("p" * 22)
    assert name == "Larga" and len(songs) == 120
    assert songs[0] == Song("Tema 0", ["Artista"], "Album", 181, "US0000000000", tracks[0]["uri"])
    assert sum(1 for c in api.calls if c[1].endswith("/items")) == 3        # de a 50


def test_search_by_isrc_first(tmp_path, api):
    exact = track(1, "Take On Me", "a-ha", isrc="NOA0000001", dur=226)
    api.add_tracks(exact, track(2, "Take On Me (Live)", "a-ha", dur=300))
    found = make(tmp_path, api).search(Song("Take On Me", ["a-ha"], duration=226, isrc="NOA0000001"))
    assert [s.id for s in found] == [exact["uri"]]
    assert [c[2]["q"] for c in api.calls if c[1] == "/search"] == ["isrc:NOA0000001"]   # alcanzó con una


def test_search_by_text_without_isrc(tmp_path, api):
    api.add_tracks(track(3, "Virtual Insanity", "Jamiroquai", dur=228))
    found = make(tmp_path, api).search(Song("Virtual Insanity", ["Jamiroquai"], duration=228))
    assert [s.title for s in found] == ["Virtual Insanity"]
    assert all(int(c[2]["limit"]) == 10 for c in api.calls if c[1] == "/search")


def test_parse_id_and_url():
    pid = "37i9dQZF1DXcBWIGoYBM5M"
    for value in (pid, f"https://open.spotify.com/playlist/{pid}?si=abc", f"spotify:playlist:{pid}"):
        assert SpotifyConnector.parse_id(value) == pid
    assert SpotifyConnector.url(pid) == f"https://open.spotify.com/playlist/{pid}"
    with pytest.raises(ConnectorError):
        SpotifyConnector.parse_id("https://open.spotify.com/album/corto")


# ---------------------------------------------------------------- escribir

def test_create_and_add_in_batches_of_100(tmp_path, api):
    c = make(tmp_path, api)
    pid = c.create("Nueva", "Desde Tidal")
    songs = [Song(f"T{i}", ["A"], id=f"spotify:track:{i:0>22}") for i in range(250)]
    c.add(pid, songs + [Song("Local", ["A"], id="spotify:local:x"), Song("Sin id", ["A"])])
    assert len(api.uris(pid)) == 250
    assert [len(x[3]["uris"]) for x in api.calls if x[0] == "POST" and x[1].endswith("/items")] == [100, 100, 50]


def test_adding_to_someone_elses_playlist(tmp_path, api):
    api.add_playlist("o" * 22, "De otro", [], owner="otro")
    with pytest.raises(NotOwnerError):
        make(tmp_path, api).add("o" * 22, [Song("T", ["A"], id="spotify:track:" + "1" * 22)])


# ---------------------------------------------------------------- quitar repetidos (la parte delicada)

def dedupe_setup(api, order):
    """`order` usa letras: la misma letra es la misma URI."""
    t = {k: track(i, f"Tema {k}", "Artista", dur=200 + i) for i, k in enumerate(sorted(set(order)))}
    api.add_tracks(*t.values())
    api.add_playlist("d" * 22, "Repetidos", [t[k]["uri"] for k in order])
    return t


def remove_at(c, positions):
    _, songs = c.read("d" * 22)
    c.remove("d" * 22, [Duplicate(i, songs[i], songs[0]) for i in positions], confirmed=True)


def letters(api, t):
    back = {v["uri"]: k for k, v in t.items()}
    return "".join(back[u] for u in api.uris("d" * 22))


def test_removing_an_exact_copy_keeps_the_original_in_place(tmp_path, api):
    t = dedupe_setup(api, "ABACD")
    c = make(tmp_path, api)
    remove_at(c, [2])                                    # la segunda A
    assert letters(api, t) == "ABCD"
    posted = [x[3] for x in api.calls if x[0] == "POST" and x[1].endswith("/items")]
    assert posted == [{"uris": [t["A"]["uri"]], "position": 0}]


def test_three_copies_and_several_songs_at_once(tmp_path, api):
    t = dedupe_setup(api, "AXBAYABZB")
    remove_at(make(tmp_path, api), [3, 5, 8])            # dos A repetidas y la B del final
    assert letters(api, t) == "AXBYBZ"                   # la otra B (posición 6) queda en su lugar


def test_removing_every_copy_of_a_different_recording_needs_no_readd(tmp_path, api):
    t = dedupe_setup(api, "ABC")
    remove_at(make(tmp_path, api), [2])                  # C es otra grabación de A (otra URI)
    assert letters(api, t) == "AB"
    assert not [x for x in api.calls if x[0] == "POST" and x[1].endswith("/items")]


def test_with_the_real_duplicate_finder(tmp_path, api):
    t = dedupe_setup(api, "ABA")
    c = make(tmp_path, api)
    _, songs = c.read("d" * 22)
    dups = find_duplicates(songs)
    assert [(d.index, d.kind) for d in dups] == [(2, "misma")]
    c.remove("d" * 22, dups, confirmed=True)
    assert letters(api, t) == "AB"


def test_remove_needs_confirmation_and_an_unchanged_playlist(tmp_path, api):
    t = dedupe_setup(api, "ABA")
    c = make(tmp_path, api)
    _, songs = c.read("d" * 22)
    with pytest.raises(RemovalNotConfirmedError):
        c.remove("d" * 22, [Duplicate(2, songs[2], songs[0])])
    api.uris("d" * 22).insert(0, t["B"]["uri"])            # alguien la cambió mientras tanto
    with pytest.raises(ConnectorError, match="cambió"):
        c.remove("d" * 22, [Duplicate(2, songs[2], songs[0])], confirmed=True)
    assert letters(api, t) == "BABA"                       # no se tocó nada


def test_if_spotify_loses_a_song_it_is_added_back_and_reported(tmp_path, api):
    t = dedupe_setup(api, "ABA")
    c = make(tmp_path, api)
    _, songs = c.read("d" * 22)
    api.ignore_adds = True                                 # Spotify no guardó la que se volvió a agregar
    with pytest.raises(ConnectorError, match="Tema A"):
        c.remove("d" * 22, [Duplicate(2, songs[2], songs[0])], confirmed=True)
    retries = [x[3] for x in api.calls if x[0] == "POST" and x[1].endswith("/items") and "position" not in x[3]]
    assert retries == [{"uris": [t["A"]["uri"]]}]


# ---------------------------------------------------------------- con el motor

def test_transfer_from_tidal_to_spotify_matches_by_isrc(tmp_path, api):
    from app.core.transfer import Target, run_transfer, summary
    from tests.fakes import FakeService
    tidal = FakeService("tidal", "Tidal")
    tidal.add_playlist("src", "Favoritas", [Song("Take On Me", ["a-ha"], duration=226, isrc="NOA0000001", id="t1"),
                                            Song("Nada Igual", ["Nadie"], duration=100, id="t2")])
    api.add_tracks(track(1, "Take On Me - 2017 Remaster", "a-ha", isrc="NOA0000001", dur=226))
    c = make(tmp_path, api)
    result = run_transfer(tidal, c, "src", [Target("Favoritas", None)])
    s = summary(result)
    assert (s["ok"], s["not_found"]) == (1, 1)
    pid = result.targets[0].target.id
    assert api.uris(pid) == [f"spotify:track:{1:0>22}"]
