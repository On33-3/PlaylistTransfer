"""La UI real (app/ui) en Edge, conectada a la API real con servicios falsos.

Usa Playwright con el Edge instalado (el mismo motor que WebView2). Si no está, se saltea.
Con PT_SCREENSHOTS=<carpeta> guarda capturas de cada pantalla.
"""
from __future__ import annotations

import json
import re
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
              open_url=lambda u, b: api._open_url_calls.append(u), system_language=lambda: "es")
    api._open_url_calls = []
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
    assert "«Para correr»" in done and "agregaste 1 tema; 1 ya estaba en otra versión" in done
    tracks = page.inner_text("#tracks")
    assert "No está la de Jeff Buckley" in tracks and "Ya estaba como «Mr. Blue Sky - 2012 Remaster»" in tracks
    shot(page, "4-transferir-resultado")
    page.click("[data-alt='3:0']")
    page.wait_for_selector("text=Agregada la de Leonard Cohen")
    assert [s.id for s in yt.songs("dst")] == ["y2old", "y1", "y3"]          # el en vivo (?) no se agregó solo
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


def with_spotify(api, tmp_path):
    """Suma Spotify con la API falsa de test_spotify. El "navegador" aprueba el login solo."""
    import threading
    import urllib.request
    from urllib.parse import parse_qs, urlparse

    from app.connectors.spotify import SpotifyConnector
    from tests.test_spotify import FakeSpotify, track

    def browser_opens(url, b):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        back = f"{q['redirect_uri']}?code=OK&state={q['state']}"
        threading.Thread(target=lambda: urllib.request.urlopen(back).read(), daemon=True).start()

    fake = FakeSpotify()
    fake.add_tracks(track(1, "Take On Me", "a-ha", dur=225))
    fake.add_playlist("p" * 22, "Mi lista de Spotify", [])
    c = SpotifyConnector(tmp_path / "spotify_session.json", tmp_path / "spotify_app.json", http=fake,
                         open_browser=browser_opens)
    c.search_pause = 0
    api._connectors["spotify"] = c
    return c, fake


def test_spotify_setup_from_settings(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    sp, fake = with_spotify(api, tmp_path)
    page, errors = open_page(browser, api)
    shot(page, "inicio-spotify-sin-configurar")
    assert "Spotify sin configurar" in " ".join(page.inner_text("#chips").split())
    assert page.is_hidden("#home-connect")              # no se reclama: Spotify es opcional
    page.click("nav [data-go=ajustes]")
    page.click("[data-setup=spotify]")
    page.wait_for_selector("#setup-spotify:not([hidden])")
    panel = page.inner_text("#setup-spotify")
    assert "Premium" in panel and "http://127.0.0.1/callback" in panel
    from tests.test_ui_design import check
    assert check(page, "ajustes") == []                 # el panel respeta el sistema de tamaños
    shot(page, "ajustes-spotify-configurar")
    page.fill("#client-spotify", "no-es-un-id")
    page.click("[data-save-client=spotify]")
    page.wait_for_selector("#client-error-spotify:not([hidden])")
    assert "32 letras y números" in page.inner_text("#client-error-spotify")
    page.fill("#client-spotify", "0123456789abcdef0123456789abcdef")
    page.press("#client-spotify", "Enter")
    page.wait_for_selector("text=Spotify conectado.")
    page.wait_for_selector("#chips .chip.ok >> text=Spotify")
    assert sp.client_id == "0123456789abcdef0123456789abcdef" and sp.status().value == "connected"
    assert page.is_visible("[data-disconnect=spotify]") and page.is_visible("[data-setup=spotify]")
    # Spotify ya se puede elegir de destino
    page.click("nav [data-go=inicio]")
    page.select_option("#to", "spotify")
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("#dst-list >> text=Mi lista de Spotify")
    assert errors == []


def test_copy_redirect_uri(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    with_spotify(api, tmp_path)
    page, errors = open_page(browser, api)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click("nav [data-go=ajustes]")
    page.click("[data-setup=spotify]")
    page.click("[data-copy=spotify]")
    page.wait_for_selector("text=Copiado.")
    assert page.evaluate("navigator.clipboard.readText()") == "http://127.0.0.1/callback"
    page.click("[data-dashboard=spotify]")
    page.wait_for_timeout(300)                                     # el pedido a Python es asíncrono
    assert api._open_url_calls[-1] == "https://developer.spotify.com/dashboard"
    page.set_viewport_size({"width": 860, "height": 620})          # ventana mínima: nada se sale
    overflow = page.evaluate("""() => [...document.querySelectorAll('#setup-spotify *')].filter(el => {
        const r = el.getBoundingClientRect(), p = document.querySelector('#setup-spotify').getBoundingClientRect();
        return r.width && (r.right > p.right + 0.5 || r.left < p.left - 0.5); }).map(el => el.className || el.tagName)""")
    assert overflow == []
    assert page.evaluate("document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth")
    shot(page, "ajustes-spotify-minima")
    assert errors == []


def test_login_can_be_cancelled_from_settings(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    page, errors = open_page(browser, api)
    page.click("nav [data-go=ajustes]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#cancel-login")
    assert round(page.locator("#cancel-login").bounding_box()["height"]) == 36   # sistema de tamaños
    page.click("#cancel-login")
    page.wait_for_selector("text=Cancelaste el inicio de sesión.")
    assert page.locator("#cancel-login").count() == 0 and page.is_hidden("#wait-tidal")
    assert not tidal.connected and page.is_enabled("[data-connect=tidal]")
    assert errors == []


# ---------------------------------------------------------------- F3

def transfer(page, dry=False):
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    page.click("#dry" if dry else "#go")
    page.wait_for_selector("#done.show")


def test_transfer_screen_stays_simple(browser, tmp_path):
    """Sin casillas extra: ni reporte por transferencia ni "agregar los que pueden ser otra versión"."""
    api, tidal, yt = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src]")
    assert page.locator("#pick input[type=checkbox]:not([name=dst]):not(#dst-new)").count() == 0
    transfer(page)
    page.wait_for_timeout(300)
    assert api.asked == []                                           # nunca se guarda un archivo solo
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
    assert "Guardé 2 operaciones" in page.inner_text("#export-note")
    assert api.asked[-1].endswith(".txt")
    text = (tmp_path / "exportado" / api.asked[-1]).read_text(encoding="utf-8")
    assert text.startswith("# PlaylistTransfer — historial") and "- Quitado (repetido):" in text
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


# ---------------------------------------------------------------- idiomas

SPANISH = re.compile(r"\b(Inicio|Transferir|Quitar|Historial|Ajustes|temas?|Todavía|conectá|Elegí|Listo|"
                     r"Buscar|repetidos?|Encontrado|Revisar|ya estaba|Coincidencias|Navegador|Idioma|"
                     r"Datos|Acerca|predeterminado|Cancelar|agregaste|Probar|Origen|Destino|sin conectar)\b")


def english_api(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    api.set_language("en")
    for svc in (tidal, yt):                                   # nombres de playlists en inglés para el test
        svc.playlists = {k: ({"Favoritas": "Favorites", "Para correr": "Running", "Clásicos": "Classics"}.get(n, n), s)
                         for k, (n, s) in svc.playlists.items()}
    return api, tidal, yt


def visible_spanish(page):
    return sorted(set(m.group(0) for m in SPANISH.finditer(page.inner_text("body"))))


def test_every_screen_in_english_has_no_spanish_left(browser, tmp_path):
    api, tidal, yt = english_api(tmp_path)
    page, errors = open_page(browser, api)
    assert page.get_attribute("html", "lang") == "en"
    assert page.inner_text("nav") .startswith("Home")
    assert visible_spanish(page) == []
    transfer(page)
    assert "added 1 song" in page.inner_text("#done") and visible_spanish(page) == []
    shot(page, "en-transfer")
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    assert "Found 2 duplicates" in page.inner_text("#dup-summary") and visible_spanish(page) == []
    page.click("#remove")
    assert page.inner_text("#c-title") == "Remove 1 song from “Classics”?"
    page.click("#confirm-yes")
    page.wait_for_selector("text=Removed 1 song")
    for screen in ("historial", "ajustes", "inicio"):
        page.click(f"nav [data-go={screen}]")
        page.wait_for_timeout(250)
        assert visible_spanish(page) == [], screen
        shot(page, f"en-{screen}")
    assert errors == []


def test_switch_language_from_settings(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    assert page.inner_text("nav [data-go=ajustes]") == "Ajustes"
    page.click("nav [data-go=ajustes]")
    page.wait_for_selector("#language-list input[value=en]")
    assert page.is_checked("#language-list input[value=es]")
    page.check("#language-list input[value=en]")
    page.wait_for_selector("nav [data-go=ajustes]:text-is('Settings')")
    assert page.is_visible("#ajustes")                          # vuelve a Ajustes después de recargar
    assert page.is_checked("#language-list input[value=en]")
    assert page.inner_text("#ajustes h1") == "Settings"
    page.check("#language-list input[value=es]")
    page.wait_for_selector("nav [data-go=ajustes]:text-is('Ajustes')")
    assert errors == []


# ---------------------------------------------------------------- mosaicos que informan

def test_home_and_history_legends_only_when_there_is_history(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    assert page.is_hidden("#home-legend")
    page.click("nav [data-go=historial]")
    assert page.is_hidden("#hist-legend")
    transfer(page)
    page.click("nav [data-go=inicio]")
    page.wait_for_selector("#home-legend:not([hidden])")
    legend = page.inner_text("#home-legend")
    for word in ("Agregado", "Puede ser otra versión", "No encontrado", "Ya estaba", "Quitado (repetido)"):
        assert word in legend
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist-legend:not([hidden])")
    assert errors == []


def test_each_small_tile_says_which_song_it_is(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("nav [data-go=inicio]")
    page.wait_for_selector("#home-history .mosaic i")
    titles = [i.get_attribute("title") for i in page.query_selector_all("#home-history .mosaic i")]
    assert "Hallelujah — Jeff Buckley: No encontrado" in titles
    assert "Mr. Blue Sky — Electric Light Orchestra: Ya estaba" in titles
    label = page.get_attribute("#home-history .mosaic", "aria-label")
    assert label == "1 agregado, 1 ya estaba, 1 sin agregar (puede ser otra versión), 1 no encontrado"
    shot(page, "11-inicio-leyenda")
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


def test_more_tiles_than_fit_show_plus_n(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    tidal.add_playlist("big", "Grande", [S(f"Tema {i}", "Alguien", id=f"b{i}") for i in range(65)])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=big]")
    page.check("input[name=dst][value=dst]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist .mosaic i")
    assert page.locator("#hist li[data-op] >> nth=0").locator(".mosaic i").count() == 60
    assert page.inner_text("#hist li[data-op] >> nth=0 >> .more") == "+5 más"
    assert errors == []


def test_clicking_a_big_tile_jumps_to_that_song(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    tile = page.locator("#mosaic i >> nth=2")                         # Hallelujah
    assert tile.get_attribute("title").startswith("Hallelujah — Jeff Buckley: No encontrado. Tocá")
    tile.click()
    row = page.locator("#tracks li", has_text="Hallelujah")
    assert "flash" in row.get_attribute("class")
    assert errors == []


# ---------------------------------------------------------------- listas con muchas playlists

def test_lists_same_height_search_and_new_on_top(browser, tmp_path):
    """El caso de la captura: pocas playlists de origen y muchas de destino."""
    api, tidal, yt = make_api(tmp_path)
    for i in range(14):
        yt.add_playlist(f"extra{i}", f"Lista {i:02d}", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=dst][value=extra13]")
    heights = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().height))")
    assert heights[0] == heights[1], heights
    tops = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().top))")
    assert tops[0] == tops[1], f"las cajas no quedan alineadas: {tops}"   # buscador en las dos, aunque una sea corta
    first = page.locator("#dst-list li >> nth=0")
    assert "Crear una playlist nueva" in first.inner_text()
    page.fill(".picker:nth-child(2) .filter", "lista 1")
    visible = page.evaluate("[...document.querySelectorAll('#dst-list li[data-filter]')].filter(li => !li.hidden).map(li => li.querySelector('.name').textContent)")
    assert visible == ["Lista 10", "Lista 11", "Lista 12", "Lista 13"]          # "Lista 01" no contiene "lista 1"
    page.fill(".picker:nth-child(2) .filter", "no existe")
    assert page.is_visible("text=Ninguna playlist coincide.")
    page.fill(".picker:nth-child(2) .filter", "")
    shot(page, "12-listas-iguales")
    assert errors == []


def test_filter_does_not_lose_the_selection(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    for i in range(12):
        yt.add_playlist(f"extra{i}", f"Lista {i:02d}", [])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=extra3]")
    page.fill(".picker:nth-child(2) .filter", "lista 07")
    page.check("input[name=dst][value=extra7]")
    page.fill(".picker:nth-child(2) .filter", "")
    assert page.is_checked("input[name=dst][value=extra3]") and page.is_checked("input[name=dst][value=extra7]")
    assert not page.is_disabled("#go")
    assert errors == []


# ---------------------------------------------------------------- textos según lo que pasa de verdad

def test_a_preview_says_would_be_added(browser, tmp_path):
    """En "Probar sin cambiar nada" no se agrega nada: la leyenda, las notas y los cuadrados lo dicen."""
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page, dry=True)
    legend = page.inner_text("#run .legend")
    assert "✓ Se agregaría" in legend and "? No se agregaría, puede ser otra versión" in legend
    assert "Agregado" not in legend
    assert "No se agregaría «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#tracks")
    assert "1 tema no se agregaría porque puede ser otra versión" in page.inner_text("#done")
    assert page.locator("#mosaic i >> nth=0").get_attribute("title").startswith("Take On Me — a-ha: Se agregaría.")
    page.click("#again")                                             # una transferencia real después
    transfer(page)
    assert "✓ Agregado" in page.inner_text("#run .legend")
    assert "No se agregó «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#tracks")
    assert "1 tema no se agregó porque puede ser otra versión" in page.inner_text("#done")
    assert errors == []


def test_history_summary_says_what_was_not_added(browser, tmp_path):
    api, *_ = english_api(tmp_path)
    page, errors = open_page(browser, api)
    transfer(page)
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    assert "1 added, 1 already there, 1 not added (may be another version), 1 not found" in page.inner_text("#hist")
    assert errors == []


# ---------------------------------------------------------------- "?" a criterio del usuario

def doubtful_setup(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    tidal.add_playlist("live", "En vivo", [S("Bohemian Rhapsody", "Queen", dur=354, id="q1"),
                                           S("Take On Me", "a-ha", dur=225, id="q2"),
                                           S("Layla", "Eric Clapton", dur=424, id="q3")])
    yt.catalog.append(S("Layla (Unplugged)", "Eric Clapton", dur=286, id="ylayla"))
    return api, tidal, yt


def run_live(page, dry=False):
    page.click("nav [data-go=transferir]")
    if page.is_visible("#again"):
        page.click("#again")
    page.wait_for_selector("input[name=src][value=live]")
    page.check("input[name=src][value=live]")
    page.check("input[name=dst][value=dst]")
    page.click("#dry" if dry else "#go")
    page.wait_for_selector("#done.show")


def test_doubtful_are_decided_at_the_end_one_by_one(browser, tmp_path):
    api, tidal, yt = doubtful_setup(tmp_path)
    page, errors = open_page(browser, api)
    run_live(page)
    assert "? No agregado, puede ser otra versión" in page.inner_text("#run .legend")
    assert "No se agregó «Bohemian Rhapsody (Live Aid 1985)» de Queen" in page.inner_text("#tracks")
    assert "2 temas no se agregaron porque pueden ser otra versión" in page.inner_text("#done")
    assert sorted(s.id for s in yt.songs("dst")) == ["y1", "y2old"]          # solo Take On Me
    assert page.inner_text("#add-all") == "Agregar los 2 que pueden ser otra versión"
    shot(page, "13-agregar-igual")
    page.click("#tracks button[data-add-doubtful='1']")                 # uno solo
    page.wait_for_selector("#tracks button[data-add-doubtful='1'].done")
    assert page.inner_text("#tracks button[data-add-doubtful='1']") == "✓ Agregado"
    assert "y4" in [s.id for s in yt.songs("dst")]
    assert page.inner_text("#add-all") == "Agregar el que puede ser otra versión"
    page.click("#add-all")                                              # el resto, de una
    page.wait_for_selector("#add-all[hidden]", state="attached")
    assert "ylayla" in [s.id for s in yt.songs("dst")]
    assert errors == []


def test_preview_has_no_add_buttons(browser, tmp_path):
    api, *_ = doubtful_setup(tmp_path)
    page, errors = open_page(browser, api)
    run_live(page, dry=True)
    assert "? No se agregaría, puede ser otra versión" in page.inner_text("#run .legend")
    assert page.locator("#tracks [data-add-doubtful]").count() == 0 and page.is_hidden("#add-all")
    assert errors == []
