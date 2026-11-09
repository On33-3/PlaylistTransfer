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
from tests.fakes import FakeSpotify, Resp, track

CID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def api():
    return FakeSpotify()


def make(tmp_path, api, client_id=CID, logged=True, **kw):
    c = SpotifyConnector(tmp_path / "spotify_session.json", tmp_path / "spotify_app.json", http=api, **kw)
    c.search_pause = 0
    if client_id:
        c.set_client_id(client_id)
    if logged:
        c._store({"access_token": "tok0", "refresh_token": "ref", "expires_in": 3600, "scope": sp.SCOPES}, first=True)
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
    info = c.setup_info()
    assert info["client_id"] == CID
    steps = [st for sec in info["sections"] for st in sec["steps"]]
    from app.connectors import registry
    links = [st["link"] for st in steps if "link" in st]
    assert links == [sp.PREMIUM, sp.CREATE_APP, sp.DASHBOARD]
    assert all(registry.is_allowed_link(u) for u in links)                 # la app los deja abrir
    assert [st.get("copy") for st in steps if st.get("copy")] == ["http://127.0.0.1:8973/callback"]
    assert sum(1 for st in steps if st.get("input")) == 1                  # un solo lugar para pegar el Client ID


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
        # exactamente la que el usuario registró en su app (con el puerto): si no, Spotify la rechaza
        assert q["redirect_uri"] == sp.REDIRECT_URI == "http://127.0.0.1:8973/callback"
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
    assert len(names) == 62 and "De otro" not in names and "Colaborativa" in names
    assert names[0] == "Canciones que te gustan"


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


# ---------------------------------------------------------------- revisión de bugs

def test_account_without_api_access_does_not_look_expired(tmp_path, api):
    """Login bien, pero Spotify no deja usar la API (sin Premium o fuera de la lista): no se guarda
    la sesión y queda "sin conectar" (no "venció"), con el motivo explicado."""
    def browser_opens(url, browser):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        threading.Thread(target=lambda: urllib.request.urlopen(f"{q['redirect_uri']}?code=C&state={q['state']}").read()).start()
    c = make(tmp_path, api, logged=False, open_browser=browser_opens)
    api.forced = [Resp(403, {"error": {"status": 403, "message": "Active premium subscription required for the owner of the app."}})]
    with pytest.raises(ConnectorError, match="Premium"):
        c.connect(None)
    assert not c.session_file.exists() and c.status() is Status.DISCONNECTED


def test_rate_limit_during_search_stops_instead_of_saying_not_found(tmp_path, api, monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    c = make(tmp_path, api)
    c._me = "yo"
    api.forced = [Resp(429, {}, {"Retry-After": "600"})]
    with pytest.raises(sp.RateLimited):
        c.search(Song("Take On Me", ["a-ha"], duration=226))


# ---------------------------------------------------------------- sin internet

def test_without_internet_the_search_stops_with_a_clear_message(tmp_path, api):
    """Sin red, `requests` lanza ConnectionError: no es "no encontrado" ni "algo salió mal", es "no hay
    conexión", y corta la búsqueda en la primera consulta."""
    import requests
    c = make(tmp_path, api)
    c._me = "me"

    class Offline:
        calls = 0

        def request(self, *a, **kw):
            Offline.calls += 1
            raise requests.ConnectionError("Failed to resolve 'api.spotify.com'")
    c._http = Offline()
    with pytest.raises(sp.NoNetwork, match="No hay conexión con Spotify"):
        c.search(Song("Take On Me", ["a-ha"]))
    assert Offline.calls == 1


def test_the_redirect_uri_has_a_fixed_port(tmp_path, api):
    """El panel de Spotify rechaza "http://127.0.0.1/callback" (sin puerto) como "not secure" y no deja
    guardar: la dirección que se registra lleva puerto, y la app espera siempre en ese."""
    assert urlparse(sp.REDIRECT_URI).port == sp.CALLBACK_PORT
    steps = [st for sec in make(tmp_path, api).setup_info()["sections"] for st in sec["steps"]]
    assert [st["copy"] for st in steps if "copy" in st] == [sp.REDIRECT_URI]


def test_if_the_port_is_taken_the_message_says_so(tmp_path, api):
    """Otro programa escucha en el puerto: la app no lo abre y explica qué pasa (no "algo salió mal")."""
    import socket
    other = socket.socket()
    other.bind(("127.0.0.1", sp.CALLBACK_PORT))
    other.listen()
    try:
        c = make(tmp_path, api, logged=False, open_browser=lambda *a: pytest.fail("no tenía que abrir nada"))
        with pytest.raises(ConnectorError, match="puerto 8973"):
            c.connect(None)
    finally:
        other.close()


# ---------------------------------------------------------------- canciones que te gustan

def test_liked_songs_read_add_and_remove(tmp_path, api):
    """Se leen con /me/tracks; desde 2026 se guardan y quitan con /me/library (URIs, hasta 40 por pedido).
    Al guardar, el primero de la lista queda arriba."""
    tracks = [track(i, f"Tema {i}", "Artista") for i in range(45)]
    api.add_tracks(*tracks)
    api.liked = [tracks[0]["uri"]]
    c = make(tmp_path, api)
    assert c.list_playlists()[0].name == "Canciones que te gustan" and c.list_playlists()[0].count == 1
    c.add(sp.LIKED, [song_of(t) for t in tracks[1:]])
    assert api.liked == [t["uri"] for t in tracks[1:]] + [tracks[0]["uri"]]
    assert all(len(p["uris"].split(",")) <= 40 for m, path, p, _, _ in api.calls if path == "/me/library")
    name, songs = c.read(sp.LIKED)
    assert name == "Canciones que te gustan" and songs[0].id == tracks[1]["uri"] and len(songs) == 45
    c.remove(sp.LIKED, [Duplicate(1, song_of(tracks[2]), song_of(tracks[1]), "exact")], confirmed=True)
    assert tracks[2]["uri"] not in api.liked and len(api.liked) == 44
    assert c.url(sp.LIKED) == "https://open.spotify.com/collection/tracks"


def test_an_old_session_without_library_permission_still_lists_playlists(tmp_path, api):
    """Una sesión de antes de pedir permiso para "Me gusta": las playlists se ven igual (reconectar lo suma)."""
    api.add_playlist("p".ljust(22, "0"), "Mía", [])
    api.library_scope = False
    assert [p.name for p in make(tmp_path, api).list_playlists()] == ["Mía"]


def test_login_asks_for_the_liked_songs_permission():
    assert "user-library-read" in sp.SCOPES.split() and "user-library-modify" in sp.SCOPES.split()


def test_transfer_into_liked_songs_end_to_end(tmp_path, api):
    """De punta a punta: de una playlist de Tidal a las canciones que te gustan de Spotify. Lo que ya
    te gustaba no se repite, y el orden es el del origen."""
    from app.core.transfer import Target, run_transfer
    from tests.fakes import FakeService, S
    a, b, cc = track(1, "Take On Me", "a-ha"), track(2, "Mr. Blue Sky", "Electric Light Orchestra"), track(3, "Hallelujah", "Jeff Buckley")
    api.add_tracks(a, b, cc)
    api.liked = [b["uri"]]
    src = FakeService("tidal", "Tidal")
    src.add_playlist("p", "Favoritas", [S("Take On Me", "a-ha", id="t1"), S("Mr. Blue Sky", "Electric Light Orchestra", id="t2"),
                                        S("Hallelujah", "Jeff Buckley", id="t3")])
    res = run_transfer(src, make(tmp_path, api), "p", [Target("?", sp.LIKED)])
    t = res.targets[0]
    assert t.target.name == "Canciones que te gustan" and not t.created
    assert [s.id for s in t.added] == [a["uri"], cc["uri"]] and [s.id for s in t.already] == [b["uri"]]
    assert api.liked == [a["uri"], cc["uri"], b["uri"]]


def test_a_session_from_before_liked_songs_asks_to_reconnect(tmp_path, api):
    """Una sesión de una versión anterior no tiene permiso para "Me gusta": se veía conectada, pero la lista
    no aparecía hasta desconectar y conectar. Ahora dice que hay que reconectar."""
    c = make(tmp_path, api, logged=False)
    c._store({"access_token": "tok0", "refresh_token": "ref", "expires_in": 3600}, first=True)       # como las viejas
    assert make(tmp_path, api, logged=False).status() is Status.EXPIRED
    c._store({"access_token": "tok1", "refresh_token": "ref", "expires_in": 3600, "scope": "user-library-read"}, first=True)
    assert make(tmp_path, api, logged=False).status() is Status.EXPIRED     # falta el de guardar


def test_refreshing_the_token_keeps_the_permissions_it_had(tmp_path, api):
    """Al renovar, Spotify puede no repetir los permisos: no por eso hay que volver a pedirlos."""
    c = make(tmp_path, api)
    c._store({"access_token": "tok2", "expires_in": 3600})
    assert c.status() is Status.CONNECTED
