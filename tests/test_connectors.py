"""Connectors with fake clients: no network and no real accounts."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.connectors.base import (BATCH, LIKED, ConnectorError, NotOwnerError, RemovalNotConfirmedError, Status)
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


class FakeTidalFavorites:
    """Tidal's favorites: newest first; each add request goes in at the top."""

    def __init__(self, tracks=()):
        self._tracks, self.calls = list(tracks), []

    def get_tracks_count(self):
        return len(self._tracks)

    def tracks(self, limit=50, offset=0, order=None, order_direction=None):
        self.calls.append(("tracks", getattr(order, "value", order), getattr(order_direction, "value", order_direction)))
        return self._tracks[offset:offset + limit]

    def add_track(self, ids):
        # Measured with a real account (2026-11-25, tools/real_check.py): the request goes in at the top, but the order
        # inside the request is Tidal's, not the order they're sent in. Here: by id
        self.calls.append(("add", list(ids)))
        self._tracks[0:0] = [track(i, f"Tema {i}", "Artista") for i in sorted(ids, key=str, reverse=True)]
        return True

    def remove_track(self, tid):
        self.calls.append(("remove", tid))
        self._tracks = [t for t in self._tracks if str(t.id) != str(tid)]
        return True


class FakeTidalSession:
    def __init__(self, playlists=None, search_results=None, favorites=None):
        self.playlists = playlists or {}
        self.search_results = search_results or {}
        self.queries = []
        self.favorites = favorites or FakeTidalFavorites()
        self.user = NS(playlists=self._user_playlists, favorites=self.favorites,
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
    assert s.queries == ["Electric Light Orchestra Mr. Blue Sky"]   # it didn't run the second search


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
        c.remove("p", dups, confirmed="sí")     # only True counts
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
    assert [(p.id, p.name, p.count) for p in c.list_playlists()] == [(LIKED, "Canciones que te gustan", 0), ("a", "A", 1)]
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
    """The part of tidalapi the link sign-in uses: it approves after `pending` checks."""
    def __init__(self, pending=2):
        self.pending, self.asked = pending, 0
        self.interval = 0          # the fake one answers at once: with no interval, it doesn't look expired
        self.token_type, self.access_token, self.refresh_token, self.expiry_time = "Bearer", "a", "r", None

    def get_link_login(self):
        return NS(verification_uri_complete="link.tidal.com/ABCDE", expires_in=300, interval=self.interval)

    def process_link_login(self, link, until_expiry=True):
        assert until_expiry is False          # one check at a time: that way it can be cancelled
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
            cancel.set()                      # the user clicked "Cancel" while waiting
        return original(link, until_expiry)
    fake.process_link_login = ask
    c = TidalConnector(tmp_path / "tidal_session.json")
    c._s = fake
    with pytest.raises(Cancelled):
        c.connect(None, cancel=cancel)
    assert fake.asked == 4 and not (tmp_path / "tidal_session.json").exists()


def test_tidal_login_with_an_expired_link_stops_instead_of_hammering(tmp_path, monkeypatch):
    """With the link expired, tidalapi answers at once: it must not keep asking without a pause."""
    import app.connectors.tidal as tidal_mod
    monkeypatch.setattr(tidal_mod, "open_url", lambda url, browser: None)
    fake = FakeTidalLogin(pending=10 ** 6)
    fake.interval = 2                         # Tidal asks to wait 2 s, but answers instantly: it expired
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
    assert time.monotonic() - start < 2       # it doesn't wait the whole 3 seconds



def test_firefox_login_opens_in_firefox_not_the_default_browser(monkeypatch):
    """If Firefox was chosen but the default is another one, the sign-in opens in Firefox (that's where the cookies are read)."""
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
        return [{"playlistId": "LM", "title": "Me gusta"},      # like the real one: it's automatic, with no count
                {"playlistId": "SE", "title": "Episodios"},
                {"playlistId": "PL1", "title": "Old songs", "count": "1.234 canciones"},
                {"playlistId": "PL2", "title": None, "count": None}]

    def get_playlist(self, pid, limit=None):
        self.read_ids = getattr(self, "read_ids", []) + [pid]
        if pid == "LM":
            return {"title": "Liked Music", "tracks": self.tracks, "trackCount": 500}
        return {"title": "Old songs", "tracks": self.tracks}

    def rate_song(self, vid, rating):
        self.rated = getattr(self, "rated", []) + [(vid, rating)]

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
    assert [(p.id, p.name, p.count) for p in pls] == [(LIKED, "Canciones que te gustan", 500), ("PL1", "Old songs", 1234),
                                                     ("PL2", "(sin nombre)", None)]


def test_yt_read_keeps_set_video_id():
    tracks = [{"title": "Take On Me", "artists": [{"name": "a-ha"}], "album": {"name": "Hunting High and Low"},
               "duration_seconds": 226, "videoId": "v1", "setVideoId": "s1"},
              {"title": None, "videoId": "gone"}]       # unavailable song: ignored
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
    assert not kept_ids & {i["setVideoId"] for i in sent}       # never the one that stays


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


# ---------------------------------------------------------------- browser session (SAPISIDHASH)

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
    """The "oauth_credentials not provided" problem: ytmusicapi has to take the file as a browser
    session. No network: the file is only built and opened."""
    ytmusicapi = pytest.importorskip("ytmusicapi")
    f = tmp_path / "browser.json"
    c = YTMusicConnector(f, tmp_path)
    c.save_session({"__Secure-3PAPISID": "fake", "SAPISID": "fake", "SID": "x"}, "UA/1.0")
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["authorization"].startswith("SAPISIDHASH ")
    from ytmusicapi.auth.types import AuthType
    assert ytmusicapi.YTMusic(str(f)).auth_type == AuthType.BROWSER


# ---------------------------------------------------------------- the shared search (Connector.collect)

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
    assert asked == ["falla", "nada", "bien"]                           # "extra" wasn't needed anymore
    assert sorted(found) == ["o1", "t1"]


def test_collect_fatal_errors_stop_everything():
    """If the session expired or the service asked to wait, it stops: otherwise every song would come out "not found"."""
    from app.connectors.base import NotConnectedError
    from tests.fakes import FakeService, S
    svc = FakeService("x", "X")

    def fetch(q):
        raise NotConnectedError("venció")
    with pytest.raises(NotConnectedError):
        svc.collect(S("A", "B"), ["a", "b"], fetch, fatal=(NotConnectedError,))


def test_collect_stops_when_the_service_stops_answering():
    """Tidal and YouTube Music don't report "expired session" with an error of their own: each search fails
    and is skipped. If several rounds in a row fail in all their queries, the service isn't answering: it
    stops (otherwise every song would come out "not found") and the session in memory is forgotten."""
    from app.connectors.base import FAILURES_IN_A_ROW
    from tests.fakes import FakeService, S
    svc = FakeService("x", "X")
    forgot = []
    svc.forget_session = lambda: forgot.append(True)

    def broken(q):
        raise RuntimeError("401")
    for _ in range(FAILURES_IN_A_ROW - 1):
        assert svc.collect(S("A", "B"), ["a", "b"], broken) == {}       # a few: they're skipped
    with pytest.raises(ConnectorError, match="dejó de responder"):
        svc.collect(S("A", "B"), ["a", "b"], broken)
    assert forgot == [True]


def test_collect_a_search_that_answers_resets_the_count():
    from app.connectors.base import FAILURES_IN_A_ROW
    from tests.fakes import FakeService, S
    svc = FakeService("x", "X")

    def broken(q):
        raise RuntimeError("se cortó")
    for _ in range(3):
        for _ in range(FAILURES_IN_A_ROW - 1):
            svc.collect(S("A", "B"), ["a"], broken)
        svc.collect(S("A", "B"), ["a"], lambda q: [])                   # it answered (even with no results)
    # one query failing and another answering in the same round doesn't count either
    for _ in range(FAILURES_IN_A_ROW + 1):
        svc.collect(S("A", "B"), ["a", "b"], lambda q: (_ for _ in ()).throw(RuntimeError()) if q == "a" else [])


def test_a_transfer_to_a_service_that_stopped_answering_changes_nothing(tmp_path):
    """End to end with the real YouTube Music connector: the session expired midway (ytmusicapi
    fails on every search). The transfer stops with a clear message, doesn't touch the destination and
    Connections checks the session again."""
    from app.core.transfer import run_transfer, Target
    from tests.fakes import FakeService, S
    src = FakeService("tidal", "Tidal")
    src.add_playlist("p", "Favoritas", [S(f"Tema {i}", "Artista", id=f"t{i}") for i in range(10)])

    class Expired(FakeYT):
        def search(self, q, filter=None, limit=10):
            self.searches.append((filter, q))
            raise Exception("Server returned HTTP 401: Unauthorized.")
    fake = Expired()
    yt = ytc(fake)
    with pytest.raises(ConnectorError, match="YouTube Music dejó de responder"):
        run_transfer(src, yt, "p", [Target("Mix", "PL1")])
    assert fake.added == [] and len(fake.searches) < 20                  # it stopped right away, without adding anything
    assert yt.yt is None                                                 # the session is checked again


def test_yt_untitled_playlist_follows_the_language():
    from app import i18n
    i18n.set_language("en")
    assert [p.name for p in ytc(FakeYT()).list_playlists()] == ["Liked songs", "Old songs", "(untitled)"]


# ---------------------------------------------------------------- liked songs

def test_tidal_liked_songs_read_add_and_remove():
    """Tidal's favorites as one more playlist: read newest to oldest; what is added ends up on top
    (inside one request, in the order Tidal decides); removing takes out that song and nothing else."""
    fav = FakeTidalFavorites([track(3, "Nuevo", "A"), track(2, "Medio", "A")])
    c = tidal(FakeTidalSession(favorites=fav))
    name, songs = c.read(LIKED)
    assert name == "Canciones que te gustan" and [s.id for s in songs] == ["3", "2"]
    assert fav.calls[0] == ("tracks", "DATE", "DESC")
    c.add(LIKED, [Song("Dos", ["A"], id="11"), Song("Uno", ["A"], id="10")])
    ids = [s.id for s in c.read(LIKED)[1]]
    assert set(ids[:2]) == {"10", "11"} and ids[2:] == ["3", "2"]
    c.remove(LIKED, [Duplicate(1, Song("Dos", ["A"], id="11"), Song("Uno", ["A"], id="10"), "exact")], confirmed=True)
    assert [s.id for s in c.read(LIKED)[1]] == ["10", "3", "2"]
    with pytest.raises(RemovalNotConfirmedError):
        c.remove(LIKED, [Duplicate(0, Song("Uno", ["A"], id="10"), Song("Uno", ["A"], id="3"), "exact")])
    assert c.url(LIKED) == "https://tidal.com/my-collection/tracks"


def test_tidal_liked_songs_keep_the_order_between_batches():
    """More songs than one request: the order between requests is the app's (the first of the list, in the top
    request); inside one request Tidal decides (measured with the real account on 2026-11-25)."""
    from app.connectors.base import BATCH
    fav = FakeTidalFavorites([track(999, "Viejo", "A")])
    c = tidal(FakeTidalSession(favorites=fav))
    ids = [str(i) for i in range(1, BATCH + 12)]
    c.add(LIKED, [Song(f"Tema {i}", ["A"], id=i) for i in ids])
    got = [s.id for s in c.read(LIKED)[1]]
    top = len(ids) - BATCH                         # the top batch: the list's first 11
    assert len([x for x in fav.calls if x[0] == "add"]) == 2 and got[-1] == "999"
    assert set(got[:top]) == set(ids[:top]) and set(got[top:-1]) == set(ids[top:])

def test_yt_liked_songs_are_the_lm_list_and_add_means_like():
    fake = FakeYT([{"title": "Take On Me", "artists": [{"name": "a-ha"}], "videoId": "v1"}])
    c = ytc(fake)
    name, songs = c.read(LIKED)
    assert name == "Canciones que te gustan" and fake.read_ids == ["LM"] and [s.id for s in songs] == ["v1"]
    c.add(LIKED, [Song("Uno", ["A"], id="a"), Song("Dos", ["A"], id="b")])
    assert fake.rated == [("b", "LIKE"), ("a", "LIKE")] and fake.added == []      # never "add to the list"
    c.remove(LIKED, [Duplicate(0, Song("Uno", ["A"], id="a"), Song("Uno", ["A"], id="v1"), "exact")], confirmed=True)
    assert fake.rated[-1] == ("a", "INDIFFERENT") and fake.removed == []
    assert c.url(LIKED) == "https://music.youtube.com/playlist?list=LM"


# ---------------------------------------------------------------- the service asks to slow down

class TooManyRequests(Exception):
    """Like tidalapi's."""
    retry_after = 7


@pytest.mark.parametrize("error, waited", [
    (TooManyRequests(), [7]),                                  # Tidal says how long
    (Exception("Server returned HTTP 429: Too Many Requests."), [2]),   # YouTube Music doesn't: the app's own is used
])
def test_a_rate_limit_waits_and_retries_instead_of_losing_the_song(monkeypatch, error, waited):
    from app.connectors import base
    from tests.fakes import FakeService, S
    slept = []
    monkeypatch.setattr(base.time, "sleep", slept.append)
    svc, asked = FakeService("x", "X"), []

    def fetch(q):
        asked.append(q)
        if len(asked) == 1:
            raise error
        return [S("A", "B", id="ok")]
    assert list(svc.collect(S("A", "B"), ["a"], fetch)) == ["ok"]
    assert asked == ["a", "a"] and sum(slept) == sum(waited)          # in pieces: it can be cancelled


def test_a_rate_limit_that_never_ends_gives_up():
    from app.connectors import base
    from tests.fakes import FakeService
    calls = []

    def always():
        calls.append(1)
        raise Exception("Server returned HTTP 429")
    real, base.time.sleep = base.time.sleep, lambda _: None
    try:
        with pytest.raises(Exception, match="429"):
            FakeService("x", "X").patient(always)
    finally:
        base.time.sleep = real
    assert len(calls) == len(base.RATE_LIMIT_WAITS) + 1


@pytest.mark.parametrize("answer, expected", [({"trackCount": None}, None), (RuntimeError("401"), None)])
def test_yt_liked_songs_without_a_count_are_still_offered(answer, expected):
    class NoCount(FakeYT):
        def get_playlist(self, pid, limit=None):
            if isinstance(answer, Exception):
                raise answer
            return answer
    assert ytc(NoCount()).list_playlists()[0].count is expected


class FlakyYT(FakeYT):
    """YouTube sometimes answers empty: ytmusicapi fails parsing it."""
    def __init__(self, bad):
        super().__init__([{"title": "Uno", "videoId": "v1", "artists": [{"name": "A"}]}])
        self.bad, self.reads = bad, 0

    def get_playlist(self, pid, limit=None):
        self.reads += 1
        if self.reads <= self.bad:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return super().get_playlist(pid, limit)


def test_yt_a_bad_answer_while_reading_a_long_list_is_retried(monkeypatch):
    monkeypatch.setattr("app.connectors.ytmusic.time.sleep", lambda s: None)
    client = FlakyYT(bad=2)
    assert [s.id for s in ytc(client).read("PL1")[1]] == ["v1"] and client.reads == 3


def test_a_service_that_keeps_answering_badly_gets_a_readable_message(monkeypatch):
    from app.jobs import Jobs
    monkeypatch.setattr("app.connectors.ytmusic.time.sleep", lambda s: None)
    jobs = Jobs()
    done = jobs.wait(jobs.start("scan", lambda emit, cancel: ytc(FlakyYT(bad=99)).read("PL1")))
    assert done["state"] == "error" and done["error"].startswith("El servicio contestó") and "char" not in done["error"]


def test_yt_a_bad_answer_when_checking_the_session_is_not_an_expired_session(tmp_path, monkeypatch):
    """A one-off unreadable answer when opening the app used to ask for reconnecting for no reason."""
    monkeypatch.setattr("app.connectors.ytmusic.time.sleep", lambda s: None)
    answers = iter([json.JSONDecodeError("Expecting value", "", 0), []])

    class OnceBad:
        def __init__(self, path): pass

        def get_library_playlists(self, limit=None):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
    monkeypatch.setattr("ytmusicapi.YTMusic", OnceBad)
    f = tmp_path / "browser.json"
    f.write_text("{}")
    assert YTMusicConnector(f, tmp_path).status() is Status.CONNECTED


def test_yt_a_rejected_session_is_still_expired(tmp_path, monkeypatch):
    class Rejected:
        def __init__(self, path): pass

        def get_library_playlists(self, limit=None):
            raise Exception("Server returned HTTP 401: Unauthorized.")
    monkeypatch.setattr("ytmusicapi.YTMusic", Rejected)
    f = tmp_path / "browser.json"
    f.write_text("{}")
    assert YTMusicConnector(f, tmp_path).status() is Status.EXPIRED


def test_yt_adding_many_liked_songs_says_how_many_went(monkeypatch):
    """YouTube Music likes go one at a time: 1600 of them were ~15 minutes without a word."""
    monkeypatch.setattr("app.connectors.ytmusic.time.sleep", lambda s: None)
    c, told = ytc(FakeYT()), []
    c.listen(told.append)
    c.add(LIKED, [Song(f"T{i}", ["A"], id=f"v{i}") for i in range(3)])
    assert [(e["done"], e["total"]) for e in told if e["type"] == "adding"] == [(1, 3), (2, 3), (3, 3)]


def test_yt_adding_to_a_playlist_says_it_per_batch():
    c, told = ytc(FakeYT()), []
    c.listen(told.append)
    c.add("PL1", [Song(f"T{i}", ["A"], id=f"v{i}") for i in range(BATCH + 5)])
    assert [(e["done"], e["total"]) for e in told if e["type"] == "adding"] == [(BATCH, BATCH + 5), (BATCH + 5, BATCH + 5)]


class MixYT(FakeYT):
    """A mix that YouTube makes (RD…), saved to the library: ytmusicapi can't read it as a playlist and fails
    with a KeyError whose text is a whole YouTube answer (seen with a tester on 2026-11-27)."""
    def get_playlist(self, pid, limit=None):
        if pid.startswith("RD") or pid == "PLbroken":
            raise KeyError("Unable to find 'contents' using path ['contents', 'twoColumnBrowseResultsRenderer'] "
                           "on {'responseContext': {...}}")
        return super().get_playlist(pid, limit)


def test_yt_a_mix_says_it_is_a_mix_instead_of_the_raw_answer():
    with pytest.raises(ConnectorError) as e:
        ytc(MixYT()).read("RDEMI0V0e34vA6znf4KLMCIbbQ")
    assert "mix" in str(e.value) and "responseContext" not in str(e.value)


def test_yt_a_playlist_that_cant_be_parsed_gets_a_readable_message():
    with pytest.raises(ConnectorError) as e:
        ytc(MixYT()).read("PLbroken")
    assert "YouTube Music" in str(e.value) and "responseContext" not in str(e.value)
