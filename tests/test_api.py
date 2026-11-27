"""The API the window uses, with fake services and an empty data folder."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.auth.browsers import Browser
from app.jobs import Jobs
from app.storage import prefs
from app.storage.history import History
from app.connectors.base import ConnectorError, NotConnectedError
from tests.fakes import FakeService, S, build_api

VIVALDI = Browser("Vivaldi", "vivaldi", Path(r"C:\vivaldi.exe"), default=True)
CHROME = Browser("Google Chrome", "chrome", Path(r"C:\chrome.exe"))


@pytest.fixture
def app(tmp_path):
    tidal = FakeService("tidal", "Tidal")
    tidal.add_playlist("src", "Favoritas", [S("Take On Me", "a-ha", dur=225, id="t1"),
                                            S("Hallelujah", "Jeff Buckley", dur=414, id="t2")])
    yt = FakeService("ytmusic", "YouTube Music", catalog=[S("Take On Me", "a-ha", dur=226, id="y1"),
                                                          S("Hallelujah", "Leonard Cohen", dur=279, id="y2")])
    yt.add_playlist("dst", "Favoritas", [])
    yt.add_playlist("dups", "Clásicos", [
        S("Virtual Insanity", "Jamiroquai", dur=228, id="v1"),
        S("Take On Me", "a-ha", dur=226, id="v2"),
        S("Virtual Insanity (Official Video)", "Jamiroquai", dur=245, id="v3"),
        S("Virtual Insanity (Live)", "Jamiroquai", dur=300, id="v4"),
    ])
    api = build_api(tmp_path, {"tidal": tidal, "ytmusic": yt}, browsers=[VIVALDI, CHROME])
    return api, tidal, yt


def wait(api, job_id, timeout=10):
    return api._jobs.wait(job_id, timeout)


# ---------------------------------------------------------------- first run

def test_first_run_state(app):
    api, tidal, yt = app
    assert api.history() == []
    tidal.connected = yt.connected = False
    assert [(s["key"], s["status"]) for s in api.status()] == [
        ("tidal", "disconnected"), ("ytmusic", "disconnected"), ("spotify", "soon")]
    b = api.browsers()
    assert b["chosen"] is None and b["suggested"] == str(VIVALDI.exe)


def test_connect_uses_the_suggested_browser(app):
    api, tidal, yt = app
    tidal.connected = False
    r = wait(api, api.connect("tidal")["job"])
    assert r["state"] == "done" and tidal.calls == [("connect", "Vivaldi")]
    assert prefs.load(api._prefs_path)["browser"]["name"] == "Vivaldi"
    assert any(e["type"] == "login" and e["browser"] == "Vivaldi" for e in r["events"])


def test_connect_can_be_cancelled(app):
    api, tidal, yt = app
    tidal.connected, tidal.login_hangs = False, True
    job = api.connect("tidal")["job"]
    api.cancel(job)
    r = wait(api, job)
    assert r["state"] == "cancelled" and not tidal.connected
    assert "job" in api.connect("ytmusic")             # nothing is left stuck


def test_choose_browser(app):
    api, *_ = app
    assert api.choose_browser(str(CHROME.exe))["name"] == "Google Chrome"
    assert api.browsers()["chosen"] == str(CHROME.exe)
    assert "error" in api.choose_browser(r"C:\no-existe.exe")


def test_an_uninstalled_browser_is_not_used(app):
    """If the chosen browser was uninstalled, it goes back to the default instead of failing to connect."""
    api, tidal, yt = app
    api.choose_browser(str(CHROME.exe))
    api._list_browsers = lambda: [VIVALDI]                  # Chrome was uninstalled
    assert api.browsers()["chosen"] is None and api.browsers()["suggested"] == str(VIVALDI.exe)
    tidal.connected = False
    r = wait(api, api.connect("tidal")["job"])
    assert r["state"] == "done" and tidal.calls == [("connect", "Vivaldi")]
    assert prefs.load(api._prefs_path)["browser"]["name"] == "Vivaldi"


def test_disconnected_service_gives_a_readable_error(app):
    api, tidal, yt = app
    yt.connected = False
    assert api.playlists("ytmusic") == {"error": "YouTube Music no está conectado."}
    assert "error" in api.playlists("spotify")


# ---------------------------------------------------------------- transferring

def test_transfer_end_to_end_and_history(app):
    api, tidal, yt = app
    assert api.playlists("tidal") == [{"id": "src", "name": "Favoritas", "count": 2}]
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "Favoritas"}], False)["job"])
    assert r["state"] == "done", r["error"]
    assert r["result"]["targets"][0]["added"] == 1 and r["result"]["summary"]["not_found"] == 1
    assert [s.id for s in yt.songs("dst")] == ["y1"]
    [op] = api.history()
    assert (op["kind"], op["source_label"], op["target_label"], op["added"], op["not_found"]) == \
        ("transfer", "Tidal", "YouTube Music", 1, 1)
    detail = api.history_detail(op["id"])
    assert [(t["title"], t["artist"], t["status"]) for t in detail] == [
        ("Take On Me", "a-ha", "ok"), ("Hallelujah", "Jeff Buckley", "no_encontrado")]


def test_dry_run_is_not_recorded(app):
    api, tidal, yt = app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": None, "name": "Nueva"}], True)["job"])
    assert r["state"] == "done" and r["result"]["dry_run"]
    assert api.history() == [] and yt.calls == []


def test_add_the_version_of_another_artist_only_when_asked(app):
    api, tidal, yt = app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "Favoritas"}], False)["job"])
    assert "y2" not in [s.id for s in yt.songs("dst")]                     # Cohen wasn't added on its own
    hallelujah = next(e for e in r["events"] if e["type"] == "track" and e["n"] == 2)
    assert hallelujah["alternatives"][0]["artist"] == "Leonard Cohen"
    a = wait(api, api.add_alternative(r["result"]["transfer"], 2, 0)["job"])
    assert a["state"] == "done" and [s.id for s in yt.songs("dst")] == ["y1", "y2"]
    assert "error" in api.add_alternative(r["result"]["transfer"], 2, 0)    # a song is added only once
    assert a["result"]["summary"]["not_found"] == 0 and a["result"]["targets"][0]["added"] == 2
    ops = api.history()                                                     # it's the same transfer: a single row
    assert len(ops) == 1 and (ops[0]["added"], ops[0]["not_found"]) == (2, 0)
    assert "ok" in ops[0]["tiles"] and "no_encontrado" not in ops[0]["tiles"]


def test_alternatives_are_not_offered_after_a_dry_run(app):
    api, *_ = app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert "error" in api.add_alternative(r["result"]["transfer"], 2, 0)


def test_transfer_input_checks(app):
    api, *_ = app
    assert "error" in api.start_transfer("tidal", "tidal", "src", [{"id": "dst"}])
    assert "error" in api.start_transfer("tidal", "ytmusic", "src", [])


def test_only_one_job_at_a_time(app):
    api, tidal, yt = app
    gate = threading.Event()
    original = yt.search
    yt.search = lambda s: (gate.wait(5), original(s))[1]
    first = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)
    assert "job" in first
    second = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)
    assert "error" in second and "Esperá" in second["error"]
    gate.set()
    assert wait(api, first["job"])["state"] == "done"


def test_cancel_a_transfer(app):
    api, tidal, yt = app
    original = yt.search
    yt.search = lambda s: (time.sleep(0.2), original(s))[1]
    job = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"]
    api.cancel(job)
    assert wait(api, job)["state"] == "cancelled"
    assert yt.calls == [] and api.history() == []


# ---------------------------------------------------------------- removing duplicates

def scan(api):
    r = wait(api, api.scan_duplicates("ytmusic", "dups")["job"])
    assert r["state"] == "done", r["error"]
    return r["result"]["groups"][0]


def test_scan_marks_other_versions(app):
    api, *_ = app
    res = scan(api)
    assert [(d["index"], d["kind"]) for d in res["duplicates"]] == [(2, "misma"), (3, "otra_version")]
    assert res["name"] == "Clásicos" and res["total"] == 4


def test_remove_needs_confirmation(app):
    api, tidal, yt = app
    res = scan(api)
    for confirmed in (False, None, "true", 1):
        assert "error" in api.remove_duplicates(res["scan"], [0], confirmed)
    assert yt.calls == [] and len(yt.songs("dups")) == 4


def test_remove_only_the_selected_ones_and_record_it(app):
    api, tidal, yt = app
    res = scan(api)
    r = wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert r["state"] == "done" and r["result"]["removed"] == 1
    assert [s.id for s in yt.songs("dups")] == ["v1", "v2", "v4"]       # the live one stayed
    [op] = api.history()
    assert op["kind"] == "dedupe" and op["removed"] == 1
    [t] = api.history_detail(op["id"])
    assert (t["title"], t["status"], t["match_title"]) == ("Virtual Insanity (Official Video)", "quitado", "Virtual Insanity")


def test_removing_says_where_it_is_and_when_it_checks_again(app):
    """After confirming, the screen has to say what is happening: which playlist, how many are done, and
    the check of how it ended up (the user saw nothing happening behind, 2026-11-27)."""
    api, tidal, yt = app
    res = scan(api)
    job = api.remove_duplicates(res["scan"], [0, 1], True)["job"]
    assert wait(api, job)["state"] == "done"
    kinds = [(e["type"], e.get("name"), e.get("done"), e.get("total")) for e in api.poll(job, 0)["events"]]
    assert kinds == [("removing", "Clásicos", None, None),
                     ("removing_progress", None, 1, 2), ("removing_progress", None, 2, 2),
                     ("rechecking", "Clásicos", None, None)]


@pytest.mark.parametrize("stale_reads, waits", [(1, 1), (99, 3)])
def test_right_after_removing_an_old_answer_is_not_shown_as_the_result(app, monkeypatch, stale_reads, waits):
    """Seen with YouTube Music (2026-11-27, real_check): the duplicates were gone, but the next read still
    answered with the old list and the screen showed them again. While it reads as before, it waits (saying
    so) and reads again; a few times at most, and then it shows what the service says."""
    api, tidal, yt = app
    res = scan(api)
    before = list(yt.songs("dups"))
    real_read, reads = yt.read, iter(range(10**6))

    def read(pid):
        name, songs = real_read(pid)
        removed = any(c[0] == "remove" for c in yt.calls)
        return (name, before) if removed and next(reads) < stale_reads else (name, songs)
    monkeypatch.setattr(yt, "read", read)
    pauses = []
    monkeypatch.setattr(yt, "pause", lambda seconds, why="rate": pauses.append(why))
    r = wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert r["state"] == "done" and r["result"]["removed"] == 1
    assert pauses == ["update"] * waits
    [group] = r["result"]["groups"]
    assert group["total"] == (3 if stale_reads == 1 else 4)    # the service never caught up: what it says


def test_remove_refuses_if_the_playlist_changed(app):
    api, tidal, yt = app
    res = scan(api)
    yt.add("dups", [S("Otra", "Alguien", id="nuevo")])                   # someone changed it from elsewhere
    yt.calls.clear()
    r = wait(api, api.remove_duplicates(res["scan"], [0, 1], True)["job"])
    assert r["state"] == "error" and "cambió" in r["error"] and "no se quitó nada" in r["error"]
    assert yt.calls == [] and api.history() == []


def test_a_scan_can_only_be_used_once(app):
    api, *_ = app
    res = scan(api)
    wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert "error" in api.remove_duplicates(res["scan"], [0], True)


def test_bad_selection(app):
    api, *_ = app
    res = scan(api)
    assert "error" in api.remove_duplicates(res["scan"], [99], True)
    assert "error" in api.remove_duplicates(res["scan"], [], True)
    assert "error" in api.remove_duplicates("scan-inventado", [0], True)


# ---------------------------------------------------------------- miscellaneous

def test_open_url_only_for_music_services(app):
    api, *_ = app
    assert api.open_url("https://music.youtube.com/playlist?list=x") == {"ok": True}
    assert "error" in api.open_url("file:///C:/Windows/System32/calc.exe")
    assert "error" in api.open_url("https://evil.example.com")
    assert "error" in api.open_url("https://tidal.com.evil.example/")         # the prefix includes the slash
    assert len(api._open_url_calls) == 1


def test_services_come_from_the_registry(app):
    """A single place defines the services: the API and the window take them from there."""
    from app.connectors import registry
    api, *_ = app
    assert api.services() == [{"key": "tidal", "name": "Tidal", "available": True},
                              {"key": "ytmusic", "name": "YouTube Music", "available": True},
                              {"key": "spotify", "name": "Spotify", "available": False}]
    assert [s["key"] for s in api.status()] == [s.key for s in registry.SERVICES]
    assert registry.name("ytmusic") == "YouTube Music" and registry.name(None) is None
    assert all(s.links and all(p.startswith("https://") and p.endswith("/") for p in s.links)
               for s in registry.SERVICES)


def test_ui_has_no_service_list_of_its_own():
    from tests.test_i18n import ui_scripts
    js = ui_scripts()
    assert "YouTube Music" not in js and '"tidal"' not in js and '"ytmusic"' not in js


def test_unexpected_errors_become_messages(app):
    api, tidal, yt = app
    def boom(_):
        raise RuntimeError("se cortó internet")
    yt.search = boom
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert r["state"] == "error" and "se cortó internet" in r["error"]


def test_without_internet_the_message_says_so(app):
    """A connection error from `requests` (used by the Tidal and YouTube Music libraries) doesn't come out as
    "something went wrong (ConnectionError: HTTPSConnectionPool…)", neither in a task nor in a direct call."""
    import requests
    api, tidal, yt = app

    def offline(*_):
        raise requests.ConnectionError("HTTPSConnectionPool(host='music.youtube.com'): Max retries exceeded")
    yt.read = offline
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert r["state"] == "error" and r["error"].startswith("No hay conexión") and "HTTPS" not in r["error"]
    yt.list_playlists = offline
    assert api.playlists("ytmusic")["error"].startswith("No hay conexión")


def test_api_exposes_no_public_state(app):
    """pywebview publishes every public attribute to the page: only methods should be visible."""
    api, *_ = app
    public = [n for n in vars(api) if not n.startswith("_") and n != "opened"]
    assert public == []


def test_jobs_poll_unknown():
    assert Jobs().poll("nada")["state"] == "error"


# ---------------------------------------------------------------- history as text, and thresholds

@pytest.fixture
def saving(app, tmp_path):
    api, tidal, yt = app
    chosen = {"path": str(tmp_path / "salida" / "historial.txt"), "asked": []}
    def dialog(name):
        chosen["asked"].append(name)
        return chosen["path"]
    api._save_dialog = dialog
    api._open_file = lambda p: chosen.setdefault("opened", p)
    return api, yt, chosen


def test_download_history_on_request(saving):
    api, yt, chosen = saving
    assert "error" in api.export_history()                                  # empty: nothing to download
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    r = api.export_history()
    assert r["operations"] == 1 and r["name"] == "historial.txt" and chosen["asked"][0].startswith("historial-")
    assert chosen["asked"][0].endswith(".txt")
    text = Path(r["path"]).read_text(encoding="utf-8")
    assert "- Agregado: Take On Me — a-ha" in text and "- No encontrado: Hallelujah — Jeff Buckley" in text
    assert api.open_export() == {"ok": True} and Path(chosen["opened"]) == Path(r["path"])


def test_cancelling_save_as_writes_nothing(saving):
    api, yt, chosen = saving
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    chosen["path"] = None
    assert api.export_history() == {"cancelled": True}
    assert "error" in api.open_export()


def test_txt_extension_is_added(saving):
    api, yt, chosen = saving
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    chosen["path"] = chosen["path"].replace(".txt", "")
    assert api.export_history()["name"] == "historial.txt"


def test_there_is_no_per_transfer_report_anymore(app):
    """The per-transfer report was removed: what happened stays in the History."""
    api, *_ = app
    assert not hasattr(api, "export_transfer")


def test_history_tiles_and_filter(app):
    api, tidal, yt = app
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    res = wait(api, api.scan_duplicates("ytmusic", "dups")["job"])["result"]["groups"][0]
    wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert [o["kind"] for o in api.history()] == ["dedupe", "transfer"]
    assert [o["kind"] for o in api.history(None, "transfer")] == ["transfer"]
    assert api.history(None, "transfer")[0]["tiles"] == ["ok", "no_encontrado"]
    assert api.history(1)[0]["tiles"] == ["quitado"]


def test_thresholds_are_validated_and_used(app):
    api, tidal, yt = app
    s = api.settings()
    assert (s["score_ok"], s["score_min"]) == (0.75, 0.6) and s["limits"]["ok_min"] == 0.71
    for ok, mn in [(0.70, 0.6), (1.0, 0.6), (0.8, 0.5), (0.8, 0.78), ("a", 0.6), (None, None)]:
        assert "error" in api.save_thresholds(ok, mn), (ok, mn)
    assert api.save_thresholds("0.9", "0.8") == {"ok": True, "score_ok": 0.9, "score_min": 0.8}
    assert api.settings()["score_ok"] == 0.9

    # A candidate scoring between 0.80 and 0.90: with the factory thresholds it's OK, with 0.90 it's doubtful
    yt.catalog = [S("Take On Me", "a-ha", dur=262, id="lejos")]           # same song, 37 s apart
    def status():
        r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
        return next((e["status"], e["score"]) for e in r["events"] if e["type"] == "track" and e["n"] == 1)
    api.save_thresholds(0.75, 0.6)
    st, sc = status()
    assert 0.8 <= sc < 0.9 and st == "ok"
    api.save_thresholds(0.9, 0.8)
    assert status() == ("dudoso_omitido", sc)                              # "?": left to decide at the end


def test_thresholds_edited_by_hand_out_of_range_fall_back(app):
    api, *_ = app
    api._prefs_path.write_text('{"score_ok": 0.3, "score_min": 0.1}', encoding="utf-8")
    s = api.settings()
    assert (s["score_ok"], s["score_min"]) == (0.75, 0.6)


def test_licenses_listed(app):
    api, *_ = app
    names = {l["name"]: l["license"] for l in api.info()["licenses"]}
    assert names == {"tidalapi": "LGPL-3.0", "ytmusicapi": "MIT", "pywebview": "BSD-3-Clause"}


def test_history_tiles_say_which_song_each_one_is(app):
    api, tidal, yt = app
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    [op] = api.history()
    assert [list(x) for x in op["tile_songs"]] == [["Take On Me", "a-ha"], ["Hallelujah", "Jeff Buckley"]]
    assert op["tiles_total"] == 2


# ---------------------------------------------------------------- "+ Add anyway" for the "?"

@pytest.fixture
def doubtful_app(app):
    api, tidal, yt = app
    tidal.add_playlist("live", "En vivo", [S("Bohemian Rhapsody", "Queen", dur=354, id="q1"),
                                           S("Take On Me", "a-ha", dur=225, id="q2")])
    yt.catalog.append(S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="ylive"))
    return api, tidal, yt


def test_skipped_doubtful_can_be_added_later_and_only_once(doubtful_app):
    api, tidal, yt = doubtful_app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "live", [{"id": "dst", "name": "x"}], False)["job"])
    assert r["result"]["summary"]["skipped"] == 1
    assert [s.id for s in yt.songs("dst")] == ["y1"]                        # the "?" wasn't added on its own
    key = r["result"]["transfer"]
    a = wait(api, api.add_doubtful(key, [1])["job"])
    assert a["state"] == "done" and a["result"]["added_ns"] == [1]
    assert [s.id for s in yt.songs("dst")] == ["y1", "ylive"]
    assert "error" in api.add_doubtful(key, [1])                            # it isn't offered again
    ops = api.history()
    assert len(ops) == 1 and (ops[0]["added"], ops[0]["doubtful"], ops[0]["skipped"]) == (2, 1, 0)
    assert a["result"]["summary"]["skipped"] == 0 and a["result"]["targets"][0]["added"] == 2


def test_add_later_does_not_duplicate_what_is_already_there(doubtful_app):
    api, tidal, yt = doubtful_app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "live", [{"id": "dst", "name": "x"}], False)["job"])
    yt.add("dst", [S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="ylive")])   # someone added it
    yt.calls.clear()
    a = wait(api, api.add_doubtful(r["result"]["transfer"], [1])["job"])
    assert a["result"]["added_ns"] == [] and a["result"]["already"] == 1 and yt.calls == []


def test_add_later_is_not_possible_after_a_preview(doubtful_app):
    api, *_ = doubtful_app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "live", [{"id": "dst", "name": "x"}], True)["job"])
    assert "error" in api.add_doubtful(r["result"]["transfer"], [1])


def test_old_history_gets_the_new_column(tmp_path):
    import sqlite3
    db = tmp_path / "viejo.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE operations (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
        kind TEXT NOT NULL, source TEXT NOT NULL, source_name TEXT NOT NULL, target TEXT, target_name TEXT,
        target_url TEXT, added INTEGER NOT NULL DEFAULT 0, already INTEGER NOT NULL DEFAULT 0,
        doubtful INTEGER NOT NULL DEFAULT 0, not_found INTEGER NOT NULL DEFAULT 0, removed INTEGER NOT NULL DEFAULT 0);
        INSERT INTO operations (created_at, kind, source, source_name, added) VALUES ('2026-11-08T01:00:00', 'transfer', 'tidal', 'Anime', 21);""")
    con.commit(); con.close()
    [op] = History(db).list()
    assert (op.source_name, op.added, op.skipped) == ("Anime", 21, 0)


# ---------------------------------------------------------------- stability

def test_when_the_second_destination_fails_the_first_stays_in_history(app):
    """The record of what did get moved used to be lost."""
    from app.connectors.base import ConnectorError
    api, tidal, yt = app
    yt.add_playlist("dst2", "Otra", [])
    real = yt.add

    def add(pid, songs):
        if pid == "dst2":
            raise ConnectorError("YouTube Music rechazó el pedido.")
        real(pid, songs)
    yt.add = add
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst"}, {"id": "dst2"}])["job"])
    assert r["state"] == "error" and "No se pudo completar «Otra»" in r["error"] and "rechazó" in r["error"]
    assert [op["target_name"] for op in api.history()] == ["Favoritas"]


def test_a_history_that_cannot_be_written_does_not_fail_the_transfer(app):
    import sqlite3
    api, tidal, yt = app

    def broken(*_):
        raise sqlite3.OperationalError("database is locked")
    api._history.record = broken
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst"}])["job"])
    assert r["state"] == "done"


def test_services_are_asked_at_the_same_time(app):
    """Each query waits for the other: if they ran one at a time, the wait would time out."""
    import threading
    from app.connectors.base import Status
    api, tidal, yt = app
    together = threading.Barrier(2, timeout=5)

    def status():
        together.wait()
        return Status.CONNECTED
    tidal.status = yt.status = status
    assert {s["key"]: s["status"] for s in api.status() if s["key"] != "spotify"} == {"tidal": "connected", "ytmusic": "connected"}


def test_old_results_are_forgotten_and_ids_never_repeat(app):
    from app import api as api_module
    api, tidal, yt = app
    keys = [wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst"}], True)["job"])["result"]["transfer"]
            for _ in range(api_module.KEEP_RESULTS + 2)]
    assert len(set(keys)) == len(keys) and list(api._transfers) == keys[-api_module.KEEP_RESULTS:]


def test_finished_jobs_are_forgotten():
    from app import jobs
    j = Jobs()
    ids = []
    for _ in range(jobs.KEEP_FINISHED + 5):
        ids.append(j.start("scan", lambda emit, cancel: 1))
        j.wait(ids[-1])
    assert len(j._jobs) <= jobs.KEEP_FINISHED + 1 and j.poll(ids[-1])["state"] == "done"


# ---------------------------------------------------------------- duplicates in every playlist

def more_playlists(yt):
    yt.add_playlist("otra", "Otra lista", [S("Dos", "X", id="d1"), S("Dos", "X", id="d2"), S("Tres", "X", id="t1")])
    yt.add_playlist("limpia", "Sin nada", [S("Uno", "X", id="u1"), S("Cuatro", "X", id="c4")])
    yt.add_playlist("rota", "Rota", [S("Uno", "X", id="u9"), S("Cinco", "X", id="c5")])
    real = yt.read

    def read(pid):
        if pid == "rota":
            raise ConnectorError("No se pudo leer esa playlist.")
        return real(pid)
    yt.read = read


def test_scan_all_playlists_one_by_one(app):
    """One that can't be read doesn't stop the others; those with fewer than 2 songs aren't checked."""
    api, tidal, yt = app
    more_playlists(yt)
    r = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])
    assert r["state"] == "done", r["error"]
    res = r["result"]
    assert [g["name"] for g in res["groups"]] == ["Clásicos", "Otra lista"]
    assert res["clean"] == ["Sin nada"] and [f["name"] for f in res["failed"]] == ["Rota"]
    assert res["checked"] == 4                                    # "Para correr" has 1 song: it doesn't count
    kinds = [e["type"] for e in r["events"]]
    assert kinds[0] == "scan_list" and kinds.count("scan_start") == 4 and kinds.count("scan_result") == 4


def test_an_unexpected_error_in_one_playlist_does_not_lose_the_whole_check(app):
    """A tester checked every YouTube Music playlist: ytmusicapi failed with a KeyError on a mix saved to the
    library (it can't be read like a playlist), and the whole check stopped with that raw text, losing the
    190 duplicates already found in liked songs. Now that playlist is listed as not read and the rest is kept."""
    api, tidal, yt = app
    more_playlists(yt)
    yt.add_playlist("mix", "Mi Supermix", [S("Uno", "X", id="m1"), S("Dos", "Y", id="m2")])
    real = yt.read

    def read(pid):
        if pid == "mix":
            raise KeyError("Unable to find 'contents' using path ['contents', 'twoColumnBrowseResultsRenderer'] "
                           "on {'responseContext': {'serviceTrackingParams': [...]}} " + "x" * 2000)
        return real(pid)
    yt.read = read
    r = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])
    assert r["state"] == "done", r.get("error")
    res = r["result"]
    assert [g["name"] for g in res["groups"]] == ["Clásicos", "Otra lista"]          # what was found is kept
    assert [f["name"] for f in res["failed"]] == ["Rota", "Mi Supermix"]
    assert all(len(f["error"]) < 200 and "responseContext" not in f["error"] for f in res["failed"])


def test_with_the_session_gone_the_check_of_every_playlist_still_stops(app):
    """Not being connected isn't one playlist's problem: going on would fail on every one."""
    api, tidal, yt = app
    more_playlists(yt)

    def read(pid):
        raise NotConnectedError("YouTube Music no está conectado.")
    yt.read = read
    r = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])
    assert r["state"] == "error" and "no está conectado" in r["error"]


def test_remove_from_several_playlists_at_once(app):
    api, tidal, yt = app
    more_playlists(yt)
    res = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])["result"]
    choices = [{"scan": g["scan"], "selected": [0]} for g in res["groups"]]
    assert "error" in api.remove_many(choices, False)                         # without the dialog, nothing
    r = wait(api, api.remove_many(choices, True)["job"])
    assert r["state"] == "done" and r["result"]["removed"] == 2 and r["result"]["names"] == ["Clásicos", "Otra lista"]
    assert [s.id for s in yt.songs("dups")] == ["v1", "v2", "v4"] and [s.id for s in yt.songs("otra")] == ["d1", "t1"]
    after = {g["name"]: len(g["duplicates"]) for g in r["result"]["groups"]}  # they were checked again
    assert after == {"Clásicos": 1, "Otra lista": 0}
    assert [o["source_name"] for o in api.history()] == ["Otra lista", "Clásicos"]   # one row per playlist


def test_several_playlists_stop_at_one_that_changed_and_say_what_was_done(app):
    api, tidal, yt = app
    more_playlists(yt)
    res = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])["result"]
    yt.add("otra", [S("Nueva", "Y", id="n1")])                                # it changed from elsewhere
    r = wait(api, api.remove_many([{"scan": g["scan"], "selected": [0]} for g in res["groups"]], True)["job"])
    assert r["state"] == "error" and "Clásicos" in r["error"] and "«Otra lista» cambió" in r["error"]
    assert len(yt.songs("dups")) == 3 and len(yt.songs("otra")) == 4         # the second one wasn't touched


def test_a_service_slow_to_answer_its_status_is_written_in_the_log(app, monkeypatch, caplog):
    """At startup a service was slow and nothing recorded which one: now the log says it."""
    import logging
    from app.api import services
    api, tidal, yt = app
    monkeypatch.setattr(services, "SLOW_STATUS", -1)
    with caplog.at_level(logging.WARNING, logger="app.api.services"):
        api.status()
    assert any("YouTube Music took" in r.getMessage() for r in caplog.records)



def test_an_unexpected_error_shows_a_short_detail_and_the_log_keeps_it_whole(caplog):
    """The tester's screen was filled with a whole YouTube answer: the screen gets the start, the log all of it."""
    import logging
    from app.jobs import DETAIL_MAX
    raw = "Unable to find 'contents' " + "x" * 3000 + " END"

    def work(emit, cancel):
        raise KeyError(raw)
    jobs = Jobs()
    with caplog.at_level(logging.ERROR, logger="app.jobs"):
        done = jobs.wait(jobs.start("scan", work))
    assert done["state"] == "error" and "KeyError" in done["error"]
    assert len(done["error"]) < DETAIL_MAX + 80 and "END" not in done["error"]
    assert "END" in caplog.text                                   # the log keeps it whole, for diagnosis
