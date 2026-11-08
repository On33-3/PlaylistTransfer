"""Quitar repetidos: elegir la playlist, revisar y confirmar."""
from __future__ import annotations

from tests.ui_helpers import check, make_api, open_page, shot, with_spotify


def test_dedupe_playlist_list_has_the_app_style_and_scrolls(browser, tmp_path):
    """La lista de playlists de Quitar repetidos usa el mismo estilo que De/a, y con muchas playlists
    tiene alto máximo y scroll en vez de tapar la pantalla."""
    api, tidal, yt = make_api(tmp_path)
    for i in range(40):
        yt.add_playlist(f"x{i}", f"Lista número {i} con un nombre bastante largo para ver cómo se corta", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=x39]", state="attached")
    assert page.evaluate("getComputedStyle(document.querySelector('#dup-playlist')).appearance") == "base-select"
    page.click("#dup-playlist")
    page.wait_for_timeout(250)
    box = page.evaluate("""() => { const o = document.querySelector('#dup-playlist option[value=x20]').parentElement;
        const r = document.querySelector('#dup-playlist option').getBoundingClientRect(); return innerHeight; }""")
    last = page.locator("#dup-playlist option[value=x39]").bounding_box()
    assert last is None or last["y"] > box                             # la lista no entra entera: hay scroll
    cut = page.evaluate("""() => [...document.querySelectorAll('#dup-playlist option')]
        .filter(o => o.scrollWidth > o.clientWidth + 1).map(o => o.textContent)""")
    assert cut == []                                                  # ningún nombre cortado: pasa a dos renglones
    shot(page, "repetidos-lista-abierta")
    page.locator("#dup-playlist option[value=dups]").click()
    assert page.input_value("#dup-playlist") == "dups"
    assert check(page, "repetidos") == []
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


def test_spotify_duplicates_warn_before_removing_every_copy(browser, tmp_path):
    from tests.fakes import track
    api, *_ = make_api(tmp_path)
    sp, fake = with_spotify(api, tmp_path)
    sp.set_client_id("0123456789abcdef0123456789abcdef")
    sp._store({"access_token": "tok0", "refresh_token": "ref", "expires_in": 3600}, first=True)
    a, b = track(7, "Virtual Insanity", "Jamiroquai", dur=228), track(8, "Take On Me", "a-ha", dur=226)
    fake.add_tracks(a, b)
    fake.add_playlist("d" * 22, "Repetidos en Spotify", [a["uri"], b["uri"], a["uri"]])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=spotify]")
    page.wait_for_selector(f"#dup-playlist option[value='{'d' * 22}']", state="attached")
    page.select_option("#dup-playlist", "d" * 22)
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    page.click("#remove")
    assert "Spotify no puede quitar una sola copia" in page.inner_text("#c-readd")
    shot(page, "repetidos-spotify-confirmar")
    page.click("#confirm-yes")
    page.wait_for_selector("text=Quitaste 1 tema de «Repetidos en Spotify».")
    assert fake.uris("d" * 22) == [a["uri"], b["uri"]]                # quedó la primera, en su lugar
    assert errors == []


def test_other_services_do_not_show_the_readd_warning(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    page.click("#remove")
    assert page.is_hidden("#c-readd")
    assert errors == []
