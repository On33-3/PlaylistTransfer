"""Negative and edge cases (review of 2026-11-24): actions at the wrong time, broken data, odd names.
Each one is something a person can do by accident, or that a service can return."""
from __future__ import annotations

import json
import threading

from app.core.models import Song
from app.storage.progress import SearchProgress
from tests.fakes import S
from tests.ui_helpers import check, make_api, open_page


def slow_reads(yt):
    """Reads wait until the test releases them: to act while the app is checking."""
    real, gate = yt.read, threading.Event()

    def read(pid):
        gate.wait(5)
        return real(pid)
    yt.read = read
    return gate


def to_dedupe(page, value="dups"):
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector(f"#dup-playlist option[value={value}]", state="attached")


# ---------------------------------------------------------------- at the wrong time

def test_service_and_playlist_cannot_change_while_checking(browser, tmp_path):
    """Switching service halfway made one's result show up under the other's name."""
    api, tidal, yt = make_api(tmp_path)
    gate = slow_reads(yt)
    page, errors = open_page(browser, api)
    to_dedupe(page)
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dup-progress:not([hidden])")
    assert page.is_disabled("#dup-svc [data-svc=tidal]") and page.is_disabled("#dup-playlist")
    gate.set()
    page.wait_for_selector(".dup-group")
    assert page.is_enabled("#dup-svc [data-svc=tidal]") and page.is_enabled("#dup-playlist") and errors == []


def test_after_erasing_everything_no_old_result_is_left_on_screen(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    to_dedupe(page)
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group")
    page.click("nav [data-go=ajustes]")
    page.click("#wipe")
    page.click("#wipe-yes")
    page.wait_for_selector("text=se borraron")
    page.click("nav [data-go=repetidos]")
    page.wait_for_timeout(300)
    assert page.locator(".dup-group").count() == 0 and page.is_hidden("#dup-actions")
    assert errors == []


# ---------------------------------------------------------------- malformed requests

def test_removing_with_a_malformed_request_answers_an_error_and_never_crashes(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    res = api._jobs.wait(api.scan_duplicates("ytmusic", "dups")["job"])["result"]["groups"][0]
    for bad in (None, "x", 7, [None], ["x"], [{"scan": None}], [{"scan": res["scan"], "selected": "0"}],
                [{"scan": res["scan"], "selected": [None]}], [{"scan": res["scan"], "selected": [-1]}]):
        r = api.remove_many(bad, True)
        assert "error" in r, bad
    assert yt.calls == [] and len(yt.songs("dups")) == 4


def test_the_same_playlist_twice_in_one_request_is_removed_once(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    res = api._jobs.wait(api.scan_duplicates("ytmusic", "dups")["job"])["result"]["groups"][0]
    r = api._jobs.wait(api.remove_many([{"scan": res["scan"], "selected": [0]}, {"scan": res["scan"], "selected": [0]}], True)["job"])
    assert r["state"] == "done" and r["result"]["removed"] == 1 and len(yt.songs("dups")) == 3
    assert len(api.history()) == 1


def test_a_check_cannot_be_used_twice(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    res = api._jobs.wait(api.scan_duplicates("ytmusic", "dups")["job"])["result"]["groups"][0]
    api._jobs.wait(api.remove_many([{"scan": res["scan"], "selected": [0]}], True)["job"])
    assert "error" in api.remove_many([{"scan": res["scan"], "selected": [0]}], True)
    assert len(yt.songs("dups")) == 3


# ---------------------------------------------------------------- broken files

def test_a_half_broken_progress_file_does_not_stop_a_transfer(tmp_path):
    p = SearchProgress(tmp_path, "tidal", "src", "ytmusic")
    good, bad = Song("Uno", ["A"], id="t1"), Song("Dos", ["A"], id="t2")
    p.put(good, Song("Uno", ["A"], id="y1"), 0.9, [])
    p.save()
    data = json.loads(p.path.read_text(encoding="utf-8"))
    data["songs"]["t2"] = {"match": "roto", "score": "x", "alts": [None, 3]}
    p.path.write_text(json.dumps(data), encoding="utf-8")
    again = SearchProgress(tmp_path, "tidal", "src", "ytmusic")
    assert again.get(good)[0].id == "y1"
    assert again.get(bad) is None                    # the broken entry is searched again, breaking nothing


# ---------------------------------------------------------------- odd names

def test_a_playlist_named_like_html_is_shown_as_text(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    evil = '<img src=x onerror="window.hacked=1">'
    yt.add_playlist("evil", evil, [S("Uno", "X", id="e1"), S("Uno", "X", id="e2")])
    page, errors = open_page(browser, api)
    to_dedupe(page, "evil")
    page.click("#dup-scan")                                       # all playlists
    page.wait_for_selector(".dup-group")
    page.click("#remove")
    assert page.evaluate("window.hacked") is None
    assert evil in page.inner_text("#dups") and evil in page.inner_text("#confirm")
    assert errors == []


def test_a_very_long_playlist_name_fits_in_the_smallest_window(browser, tmp_path):
    from tests.test_ui_dedupe import ALIGN
    api, tidal, yt = make_api(tmp_path)
    long = "Una playlist con un nombre larguísimo que alguien escribió sin pensar en la pantalla " * 2
    yt.add_playlist("larga", long, [S("Uno", "X", id="l1"), S("Uno", "X", id="l2")])
    page, errors = open_page(browser, api)
    page.set_viewport_size({"width": 860, "height": 620})
    to_dedupe(page, "larga")
    page.select_option("#dup-playlist", "larga")
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group")
    assert page.evaluate(ALIGN) == [] and check(page, "repetidos") == [] and errors == []


def test_a_long_playlist_name_stays_in_one_line_in_the_closed_list(browser, tmp_path):
    """Seen on 2026-11-24 with tools/real_check.py's playlist: «… (se borra sola) (5 temas)»
    wrapped onto two lines inside the closed list. Now it ends in «…» and reads in full on hover."""
    api, tidal, yt = make_api(tmp_path)
    name = "PlaylistTransfer · prueba (se borra sola)"
    yt.add_playlist("larga", name, [S("Uno", "X", id="l1"), S("Uno", "X", id="l2")])
    page, errors = open_page(browser, api)
    to_dedupe(page, "larga")
    page.select_option("#dup-playlist", "larga")
    shown = page.evaluate("""() => { const s = document.querySelector('#dup-playlist'), b = s.querySelector(':scope > button');
        const line = parseFloat(getComputedStyle(b).fontSize) * 1.6;
        return { text: b.textContent, cut: b.scrollWidth > b.clientWidth, oneLine: b.getBoundingClientRect().height < line,
                 title: s.title }; }""")
    assert shown["text"] == shown["title"] == f"{name} (2 temas)"
    assert shown["cut"] and shown["oneLine"], shown
    assert check(page, "repetidos") == [] and errors == []


# ---------------------------------------------------------------- other screens

def test_disconnecting_a_service_drops_its_duplicates_result(browser, tmp_path):
    """With the result on screen, disconnecting the service left a button that could no longer remove anything."""
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    to_dedupe(page)
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group")
    api.disconnect("ytmusic")
    page.evaluate("refreshStatus().then(() => serviceChanged('ytmusic'))")
    page.wait_for_function("!document.querySelector('.dup-group')")
    assert page.is_hidden("#dup-actions") and errors == []


def test_a_new_playlist_without_a_name_gets_the_default_name(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    r = api._jobs.wait(api.start_transfer("tidal", "ytmusic", "src", [{"name": "   ", "id": None}])["job"])
    assert r["state"] == "done" and r["result"]["targets"][0]["name"] == "Nueva playlist"


def test_cancelling_twice_is_harmless(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    gate = slow_reads(tidal)
    job = api.start_transfer("tidal", "ytmusic", "src", [{"name": "x", "id": "dst"}])["job"]
    api.cancel(job)
    api.cancel(job)
    gate.set()
    assert api._jobs.wait(job)["state"] == "cancelled" and yt.calls == []
    assert api.cancel("job-inventado") == {"ok": True}


def test_after_erasing_everything_the_history_is_empty_and_works(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    api._jobs.wait(api.start_transfer("tidal", "ytmusic", "src", [{"name": "x", "id": "dst"}])["job"])
    assert api.history()
    api.wipe_data(True)
    assert api.history() == [] and api.history_detail(1) == []
    tidal.connected = yt.connected = True                       # connects again and transfers
    api._jobs.wait(api.start_transfer("tidal", "ytmusic", "src", [{"name": "x", "id": "dst"}])["job"])
    assert len(api.history()) == 1


def test_a_transfer_from_an_empty_playlist_says_so(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    tidal.add_playlist("vacia", "Vacía", [])
    r = api._jobs.wait(api.start_transfer("tidal", "ytmusic", "vacia", [{"name": "x", "id": "dst"}])["job"])
    assert r["state"] == "error" and "Vacía" in r["error"] and yt.calls == []
