"""La API que usa la ventana, con servicios falsos y una carpeta de datos vacía."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.api import Api
from app.auth.browsers import Browser
from app.jobs import Jobs
from app.storage import prefs
from app.storage.history import History
from tests.fakes import FakeService, S

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
    opened = []
    api = Api({"tidal": tidal, "ytmusic": yt}, History(tmp_path / "historial.sqlite"), tmp_path / "preferencias.json",
              tmp_path, list_browsers=lambda: [VIVALDI, CHROME], open_url=lambda url, b: opened.append((url, b)))
    api.opened = opened
    return api, tidal, yt


def wait(api, job_id, timeout=10):
    return api._jobs.wait(job_id, timeout)


# ---------------------------------------------------------------- primer uso

def test_first_run_state(app):
    api, tidal, yt = app
    assert api.history() == []
    tidal.connected = yt.connected = False
    assert [(s["key"], s["status"]) for s in api.status()] == [
        ("tidal", "disconnected"), ("ytmusic", "disconnected"), ("spotify", "soon")]
    b = api.browsers()
    assert b["chosen"] is None and b["suggested"] == str(VIVALDI.exe)


def test_connect_uses_the_suggested_browser_never_chrome(app):
    api, tidal, yt = app
    tidal.connected = False
    r = wait(api, api.connect("tidal")["job"])
    assert r["state"] == "done" and tidal.calls == [("connect", "Vivaldi")]
    assert prefs.load(api._prefs_path)["browser"]["name"] == "Vivaldi"
    assert any(e["type"] == "login" and e["browser"] == "Vivaldi" for e in r["events"])


def test_choose_browser(app):
    api, *_ = app
    assert api.choose_browser(str(CHROME.exe))["name"] == "Google Chrome"   # elegido a mano, sí
    assert api.browsers()["chosen"] == str(CHROME.exe)
    assert "error" in api.choose_browser(r"C:\no-existe.exe")


def test_disconnected_service_gives_a_readable_error(app):
    api, tidal, yt = app
    yt.connected = False
    assert api.playlists("ytmusic") == {"error": "YouTube Music no está conectado."}
    assert "error" in api.playlists("spotify")


# ---------------------------------------------------------------- transferir

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
    assert "y2" not in [s.id for s in yt.songs("dst")]                     # Cohen no se agregó solo
    hallelujah = next(e for e in r["events"] if e["type"] == "track" and e["n"] == 2)
    assert hallelujah["alternatives"][0]["artist"] == "Leonard Cohen"
    a = wait(api, api.add_alternative(r["result"]["transfer"], 2, 0)["job"])
    assert a["state"] == "done" and [s.id for s in yt.songs("dst")] == ["y1", "y2"]
    assert len(api.history()) == 2


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


# ---------------------------------------------------------------- quitar repetidos

def scan(api):
    r = wait(api, api.scan_duplicates("ytmusic", "dups")["job"])
    assert r["state"] == "done", r["error"]
    return r["result"]


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
    assert [s.id for s in yt.songs("dups")] == ["v1", "v2", "v4"]       # la en vivo se quedó
    [op] = api.history()
    assert op["kind"] == "dedupe" and op["removed"] == 1
    [t] = api.history_detail(op["id"])
    assert (t["title"], t["status"], t["match_title"]) == ("Virtual Insanity (Official Video)", "quitado", "Virtual Insanity")


def test_remove_refuses_if_the_playlist_changed(app):
    api, tidal, yt = app
    res = scan(api)
    yt.add("dups", [S("Otra", "Alguien", id="nuevo")])                   # alguien la cambió desde otro lado
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


# ---------------------------------------------------------------- varios

def test_open_url_only_for_music_services(app):
    api, *_ = app
    assert api.open_url("https://music.youtube.com/playlist?list=x") == {"ok": True}
    assert "error" in api.open_url("file:///C:/Windows/System32/calc.exe")
    assert "error" in api.open_url("https://evil.example.com")
    assert len(api.opened) == 1


def test_unexpected_errors_become_messages(app):
    api, tidal, yt = app
    def boom(_):
        raise RuntimeError("se cortó internet")
    yt.search = boom
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert r["state"] == "error" and "se cortó internet" in r["error"]


def test_api_exposes_no_public_state(app):
    """pywebview publica a la página todo atributo público: solo tienen que verse métodos."""
    api, *_ = app
    public = [n for n in vars(api) if not n.startswith("_") and n != "opened"]
    assert public == []


def test_jobs_poll_unknown():
    assert Jobs().poll("nada")["state"] == "error"
