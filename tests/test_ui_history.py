"""History: filters, informative mosaics, detail and download."""
from __future__ import annotations

from tests.ui_helpers import make_api, open_page, shot, transfer, english_api


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
    assert "Guardé 2 operaciones" in page.inner_text("#export-note")
    assert api._asked[-1].endswith(".txt")
    text = (tmp_path / "exportado" / api._asked[-1]).read_text(encoding="utf-8")
    assert text.startswith("# PlaylistTransfer — historial") and "- Quitado (repetido):" in text
    shot(page, "8-historial-exportado")
    page.click("nav [data-go=inicio]")
    assert page.locator("#home-history").count() == 0                # the history lives only in History
    shot(page, "9-inicio-con-historial")
    assert errors == []


def test_history_legend_only_when_there_is_history(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=historial]")
    assert page.is_hidden("#hist-legend")
    assert "Todavía no hiciste transferencias" in page.inner_text("#hist")
    transfer(page)
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist-legend:not([hidden])")
    legend = page.inner_text("#hist-legend")
    for word in ("Agregado", "Puede ser otra versión", "No encontrado", "Ya estaba", "Quitado (repetido)"):
        assert word in legend
    assert errors == []


def test_each_small_tile_says_which_song_it_is(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist .mosaic i")
    titles = [i.get_attribute("title") for i in page.query_selector_all("#hist .mosaic i")]
    assert "Hallelujah — Jeff Buckley: No encontrado" in titles
    assert "Mr. Blue Sky — Electric Light Orchestra: Ya estaba" in titles
    label = page.get_attribute("#hist .mosaic", "aria-label")
    assert label == "1 agregado, 1 ya estaba, 1 sin agregar (puede ser otra versión), 1 no encontrado"
    shot(page, "11-historial-leyenda")
    assert errors == []


def test_small_tiles_in_english(browser, tmp_path):
    api, *_ = english_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist .mosaic i")
    titles = [i.get_attribute("title") for i in page.query_selector_all("#hist .mosaic i")]
    assert "Hallelujah — Jeff Buckley: Not found" in titles
    assert "May be another version" in page.inner_text("#hist-legend")
    assert errors == []


def test_history_summary_says_what_was_not_added(browser, tmp_path):
    api, *_ = english_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    assert "1 added, 1 already there, 1 not added (may be another version), 1 not found" in page.inner_text("#hist")
    assert errors == []
