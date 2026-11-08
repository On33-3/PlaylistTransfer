"""Conectores con clientes falsos: sin red ni cuentas reales."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.connectors.base import (BATCH, ConnectorError, NotOwnerError, RemovalNotConfirmedError, Status)
from app.connectors.tidal import TidalConnector, login_link
from app.connectors.ytmusic import (YTMusicConnector, browser_headers, parse_count, sapisid_hash)
from app.core.matching import find_duplicates
from app.core.models import Duplicate, Song


# ================================================================ Tidal

def track(id, name, artist, duration=200, version=None, isrc=None, album="Album"):
    return NS(id=id, name=name, artists=[NS(name=artist)], album=NS(name=album),
              duration=duration, version=version, isrc=isrc)


class FakeTidalPlaylist:
    def __init__(self, name, tracks, owned=True):
        self.name, self._tracks, self.added, self.removed = name, tracks, [], []
        if owned:
            self.add = lambda ids: self.added.append(list(ids))
            self.remove_by_index = lambda i: self.removed.append(i)

    def tracks(self, limit, offset):
        return self._tracks[offset:offset + limit]


class FakeTidalSession:
    def __init__(self, playlists=None, search_results=None):
        self.playlists = playlists or {}
        self.search_results = search_results or {}
        self.queries = []
        self.user = NS(playlists=self._user_playlists,
                       create_playlist=lambda name, desc: NS(id="new-uuid"))

    def check_login(self):
        return True

    def _user_playlists(self, limit=50, offset=0):
        items = [NS(id=k, name=v.name, num_tracks=len(v._tracks)) for k, v in self.playlists.items()]
        return items[offset:offset + limit]

    def playlist(self, pid):
        return self.playlists[pid]

    def search(self, q, models=None, limit=10):
        self.queries.append(q)
        return {"tracks": self.search_results.get(q, [])}


def tidal(session) -> TidalConnector:
    c = TidalConnector("unused.json", session=session)
    c.search_pause = 0
    return c


def test_tidal_read_paginates_and_appends_version():
    tracks = [track(i, f"Song {i}", "X") for i in range(250)]
    tracks[3] = track(3, "Virtual Insanity", "Jamiroquai", version="Remastered 2013")
    s = FakeTidalSession({"p1": FakeTidalPlaylist("Old songs", tracks)})
    name, songs = tidal(s).read("p1")
    assert name == "Old songs" and len(songs) == 250
    assert songs[3].title == "Virtual Insanity (Remastered 2013)"
    assert songs[3].id == "3" and songs[3].artists == ["Jamiroquai"]


def test_tidal_search_stops_when_first_query_is_good():
    hit = track(9, "Mr. Blue Sky", "Electric Light Orchestra", duration=304)
    s = FakeTidalSession(search_results={"Electric Light Orchestra Mr. Blue Sky": [hit]})
    found = tidal(s).search(Song("Mr. Blue Sky", ["Electric Light Orchestra"], duration=303))
    assert [f.id for f in found] == ["9"]
    assert s.queries == ["Electric Light Orchestra Mr. Blue Sky"]   # no hizo la segunda búsqueda


def test_tidal_add_in_batches():
    pl = FakeTidalPlaylist("Dest", [])
    c = tidal(FakeTidalSession({"p": pl}))
    c.add("p", [Song(f"S{i}", id=str(i)) for i in range(BATCH + 7)])
    assert [len(b) for b in pl.added] == [BATCH, 7]


def test_tidal_remove_needs_confirmation_and_goes_backwards(video_playlist):
    pl = FakeTidalPlaylist("Dest", [])
    c = tidal(FakeTidalSession({"p": pl}))
    dups = find_duplicates(video_playlist)
    with pytest.raises(RemovalNotConfirmedError):
        c.remove("p", dups)
    with pytest.raises(RemovalNotConfirmedError):
        c.remove("p", dups, confirmed="sí")     # solo vale True
    assert pl.removed == []
    c.remove("p", dups, confirmed=True)
    assert pl.removed == sorted((d.index for d in dups), reverse=True)


def test_tidal_not_owner():
    c = tidal(FakeTidalSession({"p": FakeTidalPlaylist("De otro", [], owned=False)}))
    with pytest.raises(NotOwnerError):
        c.add("p", [Song("S", id="1")])


def test_tidal_list_and_create():
    s = FakeTidalSession({"a": FakeTidalPlaylist("A", [track(1, "x", "y")])})
    c = tidal(s)
    assert [(p.id, p.name, p.count) for p in c.list_playlists()] == [("a", "A", 1)]
    assert c.create("Nueva", "desc") == "new-uuid"


def test_tidal_ids_and_urls():
    uid = "1ba86b4b-0000-47a7-9e14-10b775252379"
    assert TidalConnector.parse_id(f"https://tidal.com/browse/playlist/{uid}?u") == uid
    assert TidalConnector.url(uid) == f"https://tidal.com/playlist/{uid}"
    with pytest.raises(ConnectorError):
        TidalConnector.parse_id("https://tidal.com/album/123")


def test_tidal_login_link():
    assert login_link("Visit link.tidal.com/ABCDE to log in, the code will expire in 300 seconds.") \
        == "https://link.tidal.com/ABCDE"
    assert login_link("Visit https://link.tidal.com/XYZ12.") == "https://link.tidal.com/XYZ12"
    assert login_link("sin link") is None


class FakeTidalLogin:
    """La parte de tidalapi que usa el login por link: aprueba después de `pending` consultas."""
    def __init__(self, pending=2):
        self.pending, self.asked = pending, 0
        self.interval = 0          # la falsa contesta enseguida: sin intervalo, no parece vencida
        self.token_type, self.access_token, self.refresh_token, self.expiry_time = "Bearer", "a", "r", None

    def get_link_login(self):
        return NS(verification_uri_complete="link.tidal.com/ABCDE", expires_in=300, interval=self.interval)

    def process_link_login(self, link, until_expiry=True):
        assert until_expiry is False          # una consulta por vez: así se puede cancelar
        self.asked += 1
        if self.asked <= self.pending:
            raise TimeoutError("todavía no")
        return True

    def check_login(self):
        return self.asked > self.pending


def test_tidal_login_opens_the_link_and_waits_for_approval(tmp_path, monkeypatch):
    import app.connectors.tidal as tidal_mod
    opened = []
    monkeypatch.setattr(tidal_mod, "open_url", lambda url, browser: opened.append(url))
    fake = FakeTidalLogin(pending=2)
    c = TidalConnector(tmp_path / "tidal_session.json")
    c._s = fake
    c.connect(None)
    assert opened == ["https://link.tidal.com/ABCDE"] and fake.asked == 3
    assert json.loads((tmp_path / "tidal_session.json").read_text(encoding="utf-8"))["access_token"] == "a"


def test_tidal_login_can_be_cancelled(tmp_path, monkeypatch):
    import threading

    import app.connectors.tidal as tidal_mod
    from app.core.models import Cancelled
    monkeypatch.setattr(tidal_mod, "open_url", lambda url, browser: None)
    fake = FakeTidalLogin(pending=10 ** 6)
    cancel = threading.Event()
    original = fake.process_link_login
    def ask(link, until_expiry=True):
        if fake.asked == 3:
            cancel.set()                      # el usuario tocó "Cancelar" mientras esperaba
        return original(link, until_expiry)
    fake.process_link_login = ask
    c = TidalConnector(tmp_path / "tidal_session.json")
    c._s = fake
    with pytest.raises(Cancelled):
        c.connect(None, cancel=cancel)
    assert fake.asked == 4 and not (tmp_path / "tidal_session.json").exists()


def test_tidal_login_with_an_expired_link_stops_instead_of_hammering(tmp_path, monkeypatch):
    """Con el link vencido, tidalapi contesta enseguida: no hay que seguir consultando sin pausa."""
    import app.connectors.tidal as tidal_mod
    monkeypatch.setattr(tidal_mod, "open_url", lambda url, browser: None)
    fake = FakeTidalLogin(pending=10 ** 6)
    fake.interval = 2                         # Tidal pide esperar 2 s, pero contesta al instante: venció
    c = TidalConnector(tmp_path / "tidal_session.json")
    c._s = fake
    with pytest.raises(ConnectorError, match="tiempo"):
        c.connect(None)
    assert fake.asked == 1


def test_firefox_login_can_be_cancelled(monkeypatch):
    import threading
    import time

    from app.auth import firefox
    from app.core.models import Cancelled
    monkeypatch.setattr(firefox, "read_cookies", lambda domain, wanted: {})
    monkeypatch.setattr(firefox.webbrowser, "open", lambda url: True)
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    start = time.monotonic()
    with pytest.raises(Cancelled):
        firefox.login_and_read_cookies("https://example.com", "youtube.com", {"SID"}, cancel=cancel)
    assert time.monotonic() - start < 2       # no espera los 3 segundos enteros



def test_firefox_login_opens_in_firefox_not_the_default_browser(monkeypatch):
    """Si eligió Firefox pero su predeterminado es otro, el login se abre en Firefox (ahí se leen las cookies)."""
    from app.auth import firefox
    calls, seen = [], iter([{}, {"SID": "x"}])
    monkeypatch.setattr(firefox, "read_cookies", lambda domain, wanted: next(seen))
    monkeypatch.setattr(firefox.webbrowser, "open", lambda url: calls.append(("default", url)))
    monkeypatch.setattr(firefox.subprocess, "Popen", lambda args: calls.append(("exe", *args)))
    monkeypatch.setattr(threading.Event, "wait", lambda self, t=None: False)
    jar, _ = firefox.login_and_read_cookies("https://music.youtube.com", "youtube.com", {"SID"},
                                            exe=Path(r"C:irefox.exe"))
    assert jar == {"SID": "x"} and calls == [("exe", r"C:irefox.exe", "https://music.youtube.com")]

def test_tidal_status_without_network(tmp_path):
    f = tmp_path / "tidal_session.json"
    assert TidalConnector(f).status() is Status.DISCONNECTED
    f.write_text("{roto", encoding="utf-8")
    assert TidalConnector(f).status() is Status.EXPIRED
    c = TidalConnector(f)
    c.disconnect()
    assert not f.exists()


# ================================================================ YouTube Music

class FakeYT:
    def __init__(self, tracks=None, add_status="STATUS_SUCCEEDED", created="PLnew"):
        self.tracks, self.add_status, self.created = tracks or [], add_status, created
        self.added, self.removed, self.searches = [], [], []

    def get_library_playlists(self, limit=25):
        return [{"playlistId": "LM", "title": "Me gusta", "count": "500 canciones"},
                {"playlistId": "SE", "title": "Episodios"},
                {"playlistId": "PL1", "title": "Old songs", "count": "1.234 canciones"},
                {"playlistId": "PL2", "title": None, "count": None}]

    def get_playlist(self, pid, limit=None):
        return {"title": "Old songs", "tracks": self.tracks}

    def search(self, q, filter=None, limit=10):
        self.searches.append((filter, q))
        if filter == "videos":
            return [{"videoId": "vidX", "title": "Redbone - Come and Get Your Love (Official Video)",
                     "artists": [{"name": "Redbone"}], "duration_seconds": 300}]
        return []

    def create_playlist(self, name, desc, privacy_status="PRIVATE"):
        self.privacy = privacy_status
        return self.created

    def add_playlist_items(self, pid, ids, duplicates=False):
        self.added.append(list(ids))
        return {"status": self.add_status}

    def remove_playlist_items(self, pid, items):
        self.removed.append(list(items))


def ytc(client) -> YTMusicConnector:
    c = YTMusicConnector("unused.json", "profiles", client=client)
    c.search_pause = 0
    return c


def test_yt_list_skips_special_playlists_and_parses_counts():
    pls = ytc(FakeYT()).list_playlists()
    assert [(p.id, p.name, p.count) for p in pls] == [("PL1", "Old songs", 1234), ("PL2", "(sin nombre)", None)]


def test_yt_read_keeps_set_video_id():
    tracks = [{"title": "Take On Me", "artists": [{"name": "a-ha"}], "album": {"name": "Hunting High and Low"},
               "duration_seconds": 226, "videoId": "v1", "setVideoId": "s1"},
              {"title": None, "videoId": "gone"}]       # tema no disponible: se ignora
    name, songs = ytc(FakeYT(tracks)).read("PL1")
    assert name == "Old songs"
    assert songs == [Song("Take On Me", ["a-ha"], "Hunting High and Low", 226, None, "v1", "s1")]


def test_yt_search_falls_back_to_videos():
    fake = FakeYT()
    found = ytc(fake).search(Song("Come and Get Your Love", ["Redbone"], duration=301))
    assert [f.id for f in found] == ["vidX"]
    assert [f for f, _ in fake.searches] == ["songs", "songs", "videos"]


def test_yt_remove_uses_each_appearance(video_playlist):
    fake = FakeYT()
    dups = find_duplicates(video_playlist)
    with pytest.raises(RemovalNotConfirmedError):
        ytc(fake).remove("PL1", dups)
    assert fake.removed == []
    ytc(fake).remove("PL1", dups, confirmed=True)
    sent = [item for batch in fake.removed for item in batch]
    assert sent == [{"videoId": d.song.id, "setVideoId": d.song.set_id} for d in dups]
    kept_ids = {d.kept.set_id for d in dups}
    assert not kept_ids & {i["setVideoId"] for i in sent}       # nunca la que queda


def test_yt_remove_refuses_without_set_video_id():
    fake = FakeYT()
    dup = Duplicate(3, Song("X", id="v"), Song("X", id="v0", set_id="s0"))
    with pytest.raises(ConnectorError):
        ytc(fake).remove("PL1", [dup], confirmed=True)
    assert fake.removed == []


def test_yt_add_batches_and_reports_failure():
    fake = FakeYT()
    ytc(fake).add("PL1", [Song(f"S{i}", id=f"v{i}") for i in range(BATCH + 1)])
    assert [len(b) for b in fake.added] == [BATCH, 1]
    with pytest.raises(NotOwnerError):
        ytc(FakeYT(add_status="STATUS_FAILED")).add("PL1", [Song("S", id="v")])


def test_yt_create():
    fake = FakeYT()
    assert ytc(fake).create("Nueva", "desc") == "PLnew" and fake.privacy == "PRIVATE"
    ytc(fake).create("Nueva", "desc", public=True)
    assert fake.privacy == "PUBLIC"
    with pytest.raises(ConnectorError):
        ytc(FakeYT(created={"error": "x"})).create("Nueva", "desc")


def test_yt_ids_and_urls():
    assert YTMusicConnector.parse_id("https://music.youtube.com/playlist?list=PLabc&si=x") == "PLabc"
    assert YTMusicConnector.parse_id("  PLabc ") == "PLabc"
    assert YTMusicConnector.url("PLabc") == "https://music.youtube.com/playlist?list=PLabc"
    with pytest.raises(ConnectorError):
        YTMusicConnector.parse_id("https://music.youtube.com/watch?v=abc")


def test_yt_status_and_disconnect(tmp_path):
    f = tmp_path / "browser.json"
    assert YTMusicConnector(f, tmp_path).status() is Status.DISCONNECTED
    assert YTMusicConnector(f, tmp_path, client=FakeYT()).status() is Status.CONNECTED
    f.write_text("{}", encoding="utf-8")
    c = YTMusicConnector(f, tmp_path, client=FakeYT())
    c.disconnect()
    assert not f.exists() and c.status() is Status.DISCONNECTED


def test_parse_count():
    assert parse_count("1.234 canciones") == 1234
    assert parse_count("25 songs") == 25
    assert parse_count(7) == 7
    assert parse_count(None) is None and parse_count("") is None and parse_count("muchas") is None


# ---------------------------------------------------------------- sesión de navegador (SAPISIDHASH)

def test_sapisid_hash_is_deterministic():
    # sha1("1700000000 abc https://music.youtube.com")
    import hashlib
    expected = hashlib.sha1(b"1700000000 abc https://music.youtube.com").hexdigest()
    assert sapisid_hash("abc", ts=1700000000) == f"SAPISIDHASH 1700000000_{expected}"


def test_browser_headers_add_authorization_and_3papisid():
    raw = browser_headers({"SAPISID": "abc", "SID": "s"}, "UA/1.0", ts=1700000000)
    lines = dict(line.split(": ", 1) for line in raw.splitlines())
    assert lines["authorization"] == sapisid_hash("abc", ts=1700000000)
    assert "__Secure-3PAPISID=abc" in lines["cookie"] and "SID=s" in lines["cookie"]
    assert lines["user-agent"] == "UA/1.0" and lines["x-goog-authuser"] == "0"


def test_browser_headers_without_session_cookie():
    from app.auth.cdp_login import LoginError
    with pytest.raises(LoginError):
        browser_headers({"PREF": "x"}, "UA")


def test_saved_session_is_recognized_as_browser_auth(tmp_path):
    """El problema de "oauth_credentials not provided": ytmusicapi tiene que tomar el archivo como
    sesión de navegador. Sin red: solo se arma el archivo y se abre."""
    ytmusicapi = pytest.importorskip("ytmusicapi")
    f = tmp_path / "browser.json"
    c = YTMusicConnector(f, tmp_path)
    c.save_session({"__Secure-3PAPISID": "fake", "SAPISID": "fake", "SID": "x"}, "UA/1.0")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["authorization"].startswith("SAPISIDHASH ")
    from ytmusicapi.auth.types import AuthType
    assert ytmusicapi.YTMusic(str(f)).auth_type == AuthType.BROWSER


# ---------------------------------------------------------------- la búsqueda común (Connector.collect)

def test_collect_stops_at_the_first_good_match_and_skips_failed_queries():
    from tests.fakes import FakeService, S
    svc = FakeService("x", "X")
    wanted = S("Take On Me", "a-ha", dur=225)
    asked = []

    def fetch(q):
        asked.append(q)
        if q == "falla":
            raise RuntimeError("se cortó")
        return {"nada": [S("Otra", "Otro", id="o1")], "bien": [S("Take On Me", "a-ha", dur=226, id="t1")]}[q]
    found = svc.collect(wanted, ["falla", "nada", "bien", "de más"], fetch)
    assert asked == ["falla", "nada", "bien"]                           # "de más" ya no hizo falta
    assert sorted(found) == ["o1", "t1"]


def test_collect_fatal_errors_stop_everything():
    """Si la sesión venció o el servicio pidió esperar, se corta: si no, cada tema saldría "no encontrado"."""
    from app.connectors.base import NotConnectedError
    from tests.fakes import FakeService, S
    svc = FakeService("x", "X")

    def fetch(q):
        raise NotConnectedError("venció")
    with pytest.raises(NotConnectedError):
        svc.collect(S("A", "B"), ["a", "b"], fetch, fatal=(NotConnectedError,))
