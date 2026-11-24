"""Casos negativos y de borde (revisión del 2026-11-24): acciones a destiempo, datos rotos, nombres raros.
Cada uno es algo que una persona puede hacer sin querer, o que un servicio puede devolver."""
from __future__ import annotations

import json
import threading

from app.core.models import Song
from app.storage.progress import SearchProgress
from tests.fakes import S
from tests.ui_helpers import check, make_api, open_page


def slow_reads(yt):
    """Las lecturas esperan hasta que el test las suelta: para actuar mientras la app revisa."""
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


# ---------------------------------------------------------------- a destiempo

def test_service_and_playlist_cannot_change_while_checking(browser, tmp_path):
    """Si se cambiaba de servicio a mitad, el resultado de uno aparecía bajo el nombre del otro."""
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


# ---------------------------------------------------------------- pedidos mal formados

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


# ---------------------------------------------------------------- archivos rotos

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
    assert again.get(bad) is None                    # lo roto se busca de nuevo, sin romper nada


# ---------------------------------------------------------------- nombres raros

def test_a_playlist_named_like_html_is_shown_as_text(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    evil = '<img src=x onerror="window.hacked=1">'
    yt.add_playlist("evil", evil, [S("Uno", "X", id="e1"), S("Uno", "X", id="e2")])
    page, errors = open_page(browser, api)
    to_dedupe(page, "evil")
    page.click("#dup-scan")                                       # todas las playlists
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
