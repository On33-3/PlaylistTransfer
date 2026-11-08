"""La UI real (app/ui) en Edge, conectada a la API real con servicios falsos.

Usa Playwright con el Edge instalado (el mismo motor que WebView2). Si no está, se saltea.
Con PT_SCREENSHOTS=<carpeta> guarda capturas de cada pantalla.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.api import Api
from app.auth.browsers import Browser
from app.storage.history import History
from tests.conftest import ROOT
from tests.fakes import FakeService, S

sync_api = pytest.importorskip("playwright.sync_api")

UI = (ROOT / "app" / "ui" / "index.html").as_uri()
SHOTS = os.environ.get("PT_SCREENSHOTS")

BRIDGE = """
window.pywebview = { api: new Proxy({}, { get: (_, m) => (...args) =>
  window.__pt_call(String(m), JSON.stringify(args)).then(r => JSON.parse(r)) }) };
"""


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as e:
            pytest.skip(f"No pude abrir Edge: {e}")
        yield b
        b.close()


def make_api(tmp_path, connected=True):
    tidal = FakeService("tidal", "Tidal", connected=connected)
    tidal.add_playlist("src", "Favoritas", [S("Take On Me", "a-ha", dur=225, id="t1"),
                                            S("Mr. Blue Sky", "Electric Light Orchestra", dur=303, id="t2"),
                                            S("Hallelujah", "Jeff Buckley", dur=414, id="t3"),
                                            S("Bohemian Rhapsody", "Queen", dur=354, id="t4")])
    yt = FakeService("ytmusic", "YouTube Music", connected=connected, catalog=[
        S("Take On Me (Official Video)", "a-ha", dur=244, id="y1"),
        S("Mr. Blue Sky", "Electric Light Orchestra", dur=304, id="y2"),
        S("Hallelujah", "Leonard Cohen", dur=279, id="y3"),
        S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="y4")])
    yt.add_playlist("dst", "Para correr", [S("Mr. Blue Sky - 2012 Remaster", "Electric Light Orchestra", dur=304, id="y2old")])
    yt.add_playlist("dups", "Clásicos", [
        S("Virtual Insanity", "Jamiroquai", dur=228, id="v1", album="Travelling Without Moving"),
        S("Take On Me", "a-ha", dur=226, id="v2"),
        S("Virtual Insanity (Official Video)", "Jamiroquai", dur=245, id="v3"),
        S("Virtual Insanity (Live)", "Jamiroquai", dur=300, id="v4", album="Live at Verona")])
    vivaldi = Browser("Vivaldi", "vivaldi", Path(r"C:\vivaldi.exe"), default=True)
    api = Api({"tidal": tidal, "ytmusic": yt}, History(tmp_path / "historial.sqlite"), tmp_path / "prefs.json",
              tmp_path, list_browsers=lambda: [vivaldi, Browser("Brave", "brave", Path(r"C:\brave.exe"))],
              open_url=lambda u, b: None)
    api.asked = []                                     # nombres que propuso el diálogo "Guardar como"
    api._save_dialog = lambda name: (api.asked.append(name), str(tmp_path / "exportado" / name))[1]
    api._open_file = lambda p: None
    return api, tidal, yt


def open_page(browser, api):
    page = browser.new_page(viewport={"width": 1120, "height": 780})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.expose_function("__pt_call", lambda m, a: json.dumps(getattr(api, m)(*json.loads(a))))
    page.add_init_script(BRIDGE)
    page.goto(UI)
    page.wait_for_selector("#chips .chip:not(.checking)")
    return page, errors


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def test_first_run_everything_empty(browser, tmp_path):
    api, *_ = make_api(tmp_path, connected=False)
    page, errors = open_page(browser, api)
    assert "Todavía no hiciste transferencias" in page.inner_text("#home-history")
    assert page.is_visible("#home-connect")
    assert "sin conectar" in page.inner_text("#chips")
    shot(page, "1-inicio-primer-uso")
    page.click("nav [data-go=transferir]")
    assert "conectá Tidal y YouTube Music" in page.inner_text("#pick-connect-text")
    page.click("nav [data-go=historial]")
    assert "Todavía no hiciste transferencias" in page.inner_text("#hist")
    page.click("nav [data-go=ajustes]")
    page.wait_for_selector("#browser-list input")
    assert page.is_checked("#browser-list input[value='C:\\\\vivaldi.exe']")
    shot(page, "2-ajustes-primer-uso")
    assert errors == []


def test_transfer_live_then_add_another_artist(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    assert page.inner_text("#go") == "Transferir 4 temas"
    shot(page, "3-transferir-elegir")
    page.click("#go")
    page.wait_for_selector("#done.show")
    assert page.inner_text("#count") == "4"
    assert [page.inner_text(f"#n-{k}") for k in ("ok", "doubt", "miss", "dup")] == ["1", "1", "1", "1"]
    done = page.inner_text("#done")
    assert "«Para correr»" in done and "agregaste 2 temas; 1 ya estaba en otra versión" in done
    tracks = page.inner_text("#tracks")
    assert "No está la de Jeff Buckley" in tracks and "Ya estaba como «Mr. Blue Sky - 2012 Remaster»" in tracks
    shot(page, "4-transferir-resultado")
    page.click("[data-alt='3:0']")
    page.wait_for_selector("text=Agregada la de Leonard Cohen")
    assert [s.id for s in yt.songs("dst")] == ["y2old", "y1", "y4", "y3"]
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row >> nth=1")
    page.wait_for_selector("#hist li.open .detail .badge")
    assert "Hallelujah" in page.inner_text("#hist")
    shot(page, "5-historial")
    assert errors == []


def test_dry_run_from_the_window_changes_nothing(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("#dst-new")
    assert page.input_value("#dst-new-name") == "Favoritas"
    page.click("#dry")
    page.wait_for_selector("#done.show")
    assert "no se modificó nada" in page.inner_text("#done")
    assert page.is_hidden("#open-dest") and yt.calls == []
    assert page.is_disabled("[data-alt='3:0']")
    assert errors == []


def test_remove_duplicates_needs_the_dialog(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    boxes = page.query_selector_all("#dups input")
    assert [b.is_checked() for b in boxes] == [True, False]             # la en vivo, sin tildar
    assert "Otra versión" in page.inner_text("#dups li >> nth=1")
    assert page.inner_text("#remove") == "Quitar 1 tema"
    shot(page, "6-repetidos")
    page.click("#remove")
    assert page.is_visible("#confirm")
    shot(page, "7-repetidos-confirmar")
    page.click("#confirm-no")                                            # cancelar: no pasa nada
    assert yt.calls == [] and len(yt.songs("dups")) == 4
    page.click("#remove")
    page.click("#confirm-yes")
    page.wait_for_selector("text=Quitaste 1 tema de «Clásicos».")
    assert [s.id for s in yt.songs("dups")] == ["v1", "v2", "v4"]
    assert errors == []


def test_connect_from_settings(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path, connected=False)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=ajustes]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("text=Tidal conectado.")
    assert tidal.calls == [("connect", "Vivaldi")]
    page.wait_for_selector("#chips .chip.ok")
    assert "Tidal ✓" in page.inner_text("#chips")
    assert errors == []


# ---------------------------------------------------------------- F3

def transfer(page, report=False, dry=False):
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    if report:
        page.check("#want-report")
    page.click("#dry" if dry else "#go")
    page.wait_for_selector("#done.show")


def test_report_is_only_saved_when_ticked(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src]")
    assert not page.is_checked("#want-report")                       # destildado por defecto
    transfer(page)
    page.wait_for_timeout(300)
    assert api.asked == [] and page.is_hidden("#open-report")
    page.click("#again")
    transfer(page, report=True, dry=True)
    page.wait_for_selector("#open-report:not([hidden])")
    assert len(api.asked) == 1 and api.asked[0].startswith("prueba-Favoritas-")
    assert (tmp_path / "exportado" / api.asked[0]).exists()
    assert errors == []


def test_history_filter_mosaic_and_export(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    page.click("#remove"); page.click("#confirm-yes")
    page.wait_for_selector("text=Quitaste 1 tema")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    assert page.locator("#hist li[data-op]").count() == 2
    assert page.locator("#hist li[data-op] >> nth=1").locator(".mosaic i").count() == 4
    page.click("#hist-filter [data-kind=dedupe]")
    page.wait_for_function("document.querySelectorAll('#hist li[data-op]').length === 1")
    assert "Repetidos en Clásicos" in page.inner_text("#hist")
    page.click("#hist-filter [data-kind='']")
    page.wait_for_function("document.querySelectorAll('#hist li[data-op]').length === 2")
    page.click("#export")
    page.wait_for_selector("#open-export:not([hidden])")
    assert "Guardé 5 filas" in page.inner_text("#export-note")
    assert (tmp_path / "exportado" / api.asked[-1]).exists()
    shot(page, "8-historial-exportado")
    page.click("nav [data-go=inicio]")
    assert page.locator("#home-history .mosaic i").count() == 5
    shot(page, "9-inicio-con-historial")
    assert errors == []


def test_thresholds_in_settings(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=ajustes]")
    page.wait_for_function("document.querySelector('#th-ok').value === '0.75'")
    page.fill("#th-ok", "0.70")
    page.click("#th-save")
    page.wait_for_selector("#th-error:not([hidden])")
    assert "versiones en vivo o remix" in page.inner_text("#th-error")
    page.fill("#th-ok", "0.85"); page.fill("#th-min", "0.65")
    page.click("#th-save")
    page.wait_for_selector("text=Guardado: sin dudar desde 0.85")
    assert api.settings()["score_ok"] == 0.85
    shot(page, "10-ajustes-umbrales")
    page.click("#th-reset")
    page.wait_for_function("document.querySelector('#th-ok').value === '0.75'")
    assert api.settings()["score_ok"] == 0.75
    assert "tidalapi (LGPL-3.0)" in page.inner_text("#licenses")
    assert errors == []
