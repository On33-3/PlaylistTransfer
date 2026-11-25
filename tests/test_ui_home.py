"""Home: what the app explains and when it asks to connect."""
from __future__ import annotations

from tests.ui_helpers import make_api, open_page, shot, with_spotify


def test_first_run_everything_empty(browser, tmp_path):
    api, *_ = make_api(tmp_path, connected=False)
    page, errors = open_page(browser, api)
    # Not connected, the main button is "Go to Connections" (in the button row, not a separate box
    # above the title, which looked overlapped); "Transfer music" becomes secondary: a single main one
    assert page.inner_text("#home-connect") == "Ir a Conexiones"
    assert page.locator("#inicio .btn.primary:visible").count() == 1
    assert "ghost" in page.get_attribute("#home-transfer", "class")
    assert page.evaluate("document.querySelector('main').scrollHeight <= document.querySelector('main').clientHeight")
    page.click("#home-connect")
    assert page.is_visible("#conexiones")
    page.click("nav [data-go=inicio]")
    assert "sin conectar" in page.inner_text("#chips")
    shot(page, "1-inicio-primer-uso")
    page.click("nav [data-go=transferir]")
    assert "conectá Tidal y YouTube Music" in page.inner_text("#pick-connect-text")
    page.click("nav [data-go=historial]")
    assert "Todavía no hiciste transferencias" in page.inner_text("#hist")
    page.click("nav [data-go=conexiones]")
    page.wait_for_selector("#browser-list input")
    assert page.is_checked("#browser-list input[value='C:\\\\vivaldi.exe']")
    shot(page, "2-conexiones-primer-uso")
    assert errors == []


def test_home_only_asks_to_connect_when_fewer_than_two(browser, tmp_path):
    """Someone who uses YouTube Music and Spotify doesn't see "Tidal missing" forever."""
    api, tidal, yt = make_api(tmp_path)
    tidal.connected = False
    sp, _ = with_spotify(api, tmp_path)
    sp.set_client_id("0123456789abcdef0123456789abcdef")
    sp.connect()
    page, errors = open_page(browser, api)
    page.wait_for_selector("#chips .chip.ok >> text=Spotify")
    assert page.is_hidden("#home-connect") and "primary" in page.get_attribute("#home-transfer", "class")
    yt.connected = False
    page.reload(); page.wait_for_selector("#chips .chip.ok >> text=Spotify")
    page.wait_for_selector("#home-connect:not([hidden])")
    assert page.locator("#inicio .btn.primary:visible").count() == 1
    assert errors == []


def test_home_explains_the_app_and_the_route_is_chosen_in_transfer(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    with_spotify(api, tmp_path)
    page, errors = open_page(browser, api)
    # Home: a text about the app (with the registry's services), no dropdowns
    assert page.inner_text("#home-lede").startswith("PlaylistTransfer pasa tu música entre Tidal, YouTube Music y Spotify")
    assert page.locator("#inicio select").count() == 0
    assert page.locator("#inicio .features li").count() == 4
    assert page.inner_text("#inicio .features li:first-child").startswith("Conexiones. El primer paso: en Conexiones")
    assert page.eval_on_selector_all("nav button", "bs => bs.map(b => b.dataset.go)") == [
        "inicio", "conexiones", "transferir", "repetidos", "historial", "ajustes"]
    shot(page, "inicio-texto")
    page.click("#inicio .actions >> text=Transferir música")
    # Transfer, step 1: from where and to where
    page.wait_for_selector("#src-list >> text=Favoritas")
    assert page.eval_on_selector_all("#to option", "os => os.map(o => o.textContent)") == ["Tidal", "YouTube Music", "Spotify"]
    assert page.get_attribute("#swap", "title") == "Dar vuelta: de YouTube Music a Tidal"
    page.click("#swap")
    assert (page.input_value("#from"), page.input_value("#to")) == ("ytmusic", "tidal")
    assert page.inner_text("#src-title") == "Origen en YouTube Music"          # the lists reload
    page.select_option("#to", "spotify")
    assert page.get_attribute("#swap", "title") == "Dar vuelta: de Spotify a YouTube Music"
    page.select_option("#from", "spotify")                             # the same: the other takes the previous one
    assert (page.input_value("#from"), page.input_value("#to")) == ("spotify", "ytmusic")
    assert round(page.locator("#swap").bounding_box()["height"]) == 36
    assert round(page.locator("#from").bounding_box()["height"]) == 36
    assert "Spotify" in page.inner_text("#src-title")
    shot(page, "transferir-ruta")
    # The open list uses the app's style (not Windows') and is picked with the mouse
    assert page.evaluate("getComputedStyle(document.querySelector('#to')).appearance") == "base-select"
    page.click("#to")
    page.wait_for_timeout(250)
    shot(page, "transferir-ruta-lista")
    page.locator("#to option[value=tidal]").click()
    assert (page.input_value("#from"), page.input_value("#to")) == ("spotify", "tidal")
    assert errors == []
