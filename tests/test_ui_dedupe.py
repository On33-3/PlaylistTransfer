"""Quitar repetidos: elegir la playlist, revisar y confirmar."""
from __future__ import annotations

import pytest

from app.connectors import spotify
from tests.fakes import S
from tests.ui_helpers import check, make_api, open_page, shot, with_spotify


def test_dedupe_playlist_list_has_the_app_style_and_scrolls(browser, tmp_path):
    """La lista de playlists de Quitar repetidos usa el mismo estilo que De/a, y con muchas playlists
    tiene alto máximo y scroll en vez de tapar la pantalla."""
    api, tidal, yt = make_api(tmp_path)
    for i in range(40):
        yt.add_playlist(f"x{i}", f"Lista número {i} con un nombre bastante largo para ver cómo se corta",
                        [S("Tema", "Artista", id=f"a{i}"), S("Otro", "Artista", id=f"b{i}")])
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
    sp._store({"access_token": "tok0", "refresh_token": "ref", "expires_in": 3600, "scope": spotify.SCOPES}, first=True)
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


def test_playlists_that_cannot_have_duplicates_are_not_offered(browser, tmp_path):
    """Con menos de 2 temas no puede haber repetidos: no se ofrecen."""
    api, tidal, yt = make_api(tmp_path)
    yt.add_playlist("uno", "Un solo tema", [S("Tema", "Artista", id="u1")])
    yt.add_playlist("cero", "Vacía", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    values = page.eval_on_selector_all("#dup-playlist option", "os => os.map(o => o.value)")
    assert "uno" not in values and "cero" not in values and "dups" in values
    assert errors == []


def two_playlists(yt, keeps_enough=True):
    uno = [S("Uno", "X", id="u1"), S("Uno", "X", id="u2")] + ([S("Otra", "X", id="o1")] if keeps_enough else [])
    yt.add_playlist("aaa", "Primera", [S("Dos", "X", id="d1"), S("Dos", "X", id="d2")])
    yt.add_playlist("bbb", "Segunda", uno)


def open_dedupe(page):
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=bbb]", state="attached")


@pytest.mark.parametrize("keeps_enough", [True, False])
def test_after_removing_it_never_checks_another_playlist(browser, tmp_path, keeps_enough):
    """Al redibujar la lista se volvía a la primera: después de quitar se revisaba otra playlist.
    Si le queda un solo tema, sale de la lista y no se revisa ninguna."""
    api, tidal, yt = make_api(tmp_path)
    two_playlists(yt, keeps_enough)
    page, errors = open_page(browser, api)
    open_dedupe(page)
    page.select_option("#dup-playlist", "bbb")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    page.click("#remove")
    page.click("#confirm-yes")
    page.wait_for_selector("text=«Segunda»")
    page.wait_for_function("!state.busy")
    page.wait_for_timeout(500)
    assert (page.input_value("#dup-playlist") == "bbb") is keeps_enough
    assert page.locator("#dups li").count() == 0                       # "Primera" tiene repetidos: no se miró
    assert len(yt.songs("bbb")) == (2 if keeps_enough else 1) and len(yt.songs("aaa")) == 2
    assert errors == []


def test_a_new_playlist_shows_up_on_the_screen_being_watched(browser, tmp_path):
    """Una transferencia que termina (o un servicio que se conecta) actualiza lo que se está viendo."""
    api, tidal, yt = make_api(tmp_path)
    two_playlists(yt)
    page, errors = open_page(browser, api)
    open_dedupe(page)
    page.select_option("#dup-playlist", "bbb")
    yt.add_playlist("ccc", "Recién creada", [S("Tres", "X", id="t1"), S("Cuatro", "X", id="t2")])
    page.evaluate("serviceChanged('ytmusic')")
    page.wait_for_selector("#dup-playlist option[value=ccc]", state="attached")
    assert page.input_value("#dup-playlist") == "bbb"             # lo elegido no se pierde
    assert errors == []


def test_a_slow_answer_does_not_overwrite_the_service_chosen_later(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    two_playlists(yt)
    page, errors = open_page(browser, api)
    open_dedupe(page)
    page.evaluate("""() => { const real = api.playlists; api.playlists = key => key === 'tidal'
        ? new Promise(ok => setTimeout(() => real.call(api, key).then(ok), 400)) : real.call(api, key); }""")
    page.click("#dup-svc [data-svc=tidal]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_timeout(700)
    assert page.locator("#dup-playlist option[value=bbb]").count() == 1
    assert errors == []
