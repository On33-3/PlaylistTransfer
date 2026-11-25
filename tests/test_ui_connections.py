"""Connections: each service's steps, connecting, cancelling and setting up Spotify."""
from __future__ import annotations

import pytest

from tests.ui_helpers import check, make_api, open_page, shot, with_spotify


def test_connect_from_connections(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path, connected=False)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("text=Tidal conectado.")
    assert tidal.calls == [("connect", "Vivaldi")]
    page.wait_for_selector("#chips .chip.ok")
    assert "Tidal ✓" in page.inner_text("#chips")
    assert errors == []


def test_each_service_shows_its_steps_before_connecting(browser, tmp_path):
    """Not connected, each service shows its numbered steps before anything is clicked (step 1: click Connect);
    what needs setting up first (Spotify) points to «Set up». Once connected, the steps disappear."""
    api, tidal, yt = make_api(tmp_path, connected=False)
    with_spotify(api, tmp_path)
    from app.connectors.tidal import TidalConnector
    from app.connectors.ytmusic import YTMusicConnector
    tidal.login_steps = lambda b: TidalConnector.login_steps(None, b)
    yt.login_steps = lambda b: YTMusicConnector.login_steps(None, b)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.wait_for_selector(".svc-steps")
    first = page.eval_on_selector_all(".svc-steps li:first-child p", "ps => ps.map(p => p.textContent)")
    assert first == ["Tocá «Conectar»: se abre la página de Tidal en Vivaldi. Si no la ves, buscá Vivaldi en la barra de tareas. Si la cerrás sin querer, tocá «Abrir de nuevo».",
                     "Tocá «Conectar»: se abre una ventana nueva de Vivaldi, aparte de la tuya y sin tus sesiones. Es normal."]
    assert page.eval_on_selector_all(".svc-steps", "s => s.map(x => x.querySelectorAll('li').length)") == [4, 4]
    assert page.inner_text(".svc-hint") == "Necesita Spotify Premium y crear tu propia app en Spotify: tocá «Configurar» y seguí los pasos."
    assert check(page, "conexiones") == []
    shot(page, "ajustes-pasos-antes")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("text=Tidal conectado.")
    page.wait_for_function("document.querySelectorAll('.svc-steps').length === 1")     # YouTube Music's remain
    page.locator("input[name=br]").nth(1).check()                  # switch to Brave: the steps name it
    page.click("nav [data-go=inicio]"); page.click("nav [data-go=conexiones]")
    page.wait_for_selector(".svc-steps >> text=ventana nueva de Brave")
    assert errors == []


def test_spotify_setup_from_connections(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    sp, fake = with_spotify(api, tmp_path)
    page, errors = open_page(browser, api)
    shot(page, "inicio-spotify-sin-configurar")
    assert "Spotify sin configurar" in " ".join(page.inner_text("#chips").split())
    assert page.is_hidden("#home-connect")              # not asked for: Spotify is optional
    page.click("nav [data-go=conexiones]")
    page.click("[data-setup=spotify]")
    page.wait_for_selector("#setup-spotify:not([hidden])")
    panel = page.inner_text("#setup-spotify")
    assert "Premium" in panel and "http://127.0.0.1:8973/callback" in panel
    # the text names the button exactly as it's called
    assert "Tocá «Abrir el formulario para crear la app»" in panel
    assert page.is_visible("#setup-spotify button:text-is('Abrir el formulario para crear la app')")
    assert "No copies el «Client secret»" in panel
    numbers = page.eval_on_selector_all("#setup-spotify .step-n", "ns => ns.map(n => n.textContent)")
    assert numbers == ["1.1", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "3.1", "3.2", "3.3", "3.4"]
    assert check(page, "conexiones") == []                 # the panel follows the size system
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
    # Connected, «See the steps» makes no sense (the user saw it on 2026-11-24): it comes back on disconnect
    assert page.is_visible("[data-disconnect=spotify]") and page.locator("[data-setup=spotify]").count() == 0
    page.click("[data-disconnect=spotify]")
    page.wait_for_selector("[data-connect=spotify]")
    assert page.is_visible("[data-setup=spotify]") and page.inner_text("[data-setup=spotify]") == "Ver los pasos"
    assert sp.client_id == "0123456789abcdef0123456789abcdef"                # kept for connecting again
    page.click("[data-connect=spotify]")
    page.wait_for_selector("#chips .chip.ok >> text=Spotify")
    # Spotify can now be picked as a destination
    page.click("nav [data-go=transferir]")
    page.select_option("#to", "spotify")
    page.wait_for_selector("#dst-list >> text=Mi lista de Spotify")
    assert errors == []


def test_copy_redirect_uri(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    with_spotify(api, tmp_path)
    page, errors = open_page(browser, api)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click("nav [data-go=conexiones]")
    page.click("[data-setup=spotify]")
    # each value to paste has its Copy button, next to the step where it goes
    copies = page.eval_on_selector_all("#setup-spotify [data-copy]", "bs => bs.map(b => b.dataset.copy)")
    assert copies == ["PlaylistTransfer", "Pasar mis playlists entre servicios", "http://127.0.0.1:8973/callback"]
    page.click("#setup-spotify [data-copy='http://127.0.0.1:8973/callback']")
    page.wait_for_selector("text=Copiado.")
    assert page.evaluate("navigator.clipboard.readText()") == "http://127.0.0.1:8973/callback"
    redirect_step = page.locator("#setup-spotify li", has=page.locator("[data-copy='http://127.0.0.1:8973/callback']"))
    assert "Redirect URIs" in redirect_step.inner_text() and redirect_step.locator(".step-n").inner_text() == "2.6"
    # each step has its link, and each one opens the right page
    links = page.eval_on_selector_all("#setup-spotify [data-link]", "bs => bs.map(b => b.dataset.link)")
    assert links == ["https://www.spotify.com/premium/", "https://developer.spotify.com/dashboard/create",
                     "https://developer.spotify.com/dashboard"]
    for i, url in enumerate(links):
        page.locator("#setup-spotify [data-link]").nth(i).click()
        page.wait_for_timeout(300)                                 # the request to Python is asynchronous
        assert api._open_url_calls[-1] == url
    assert "not secure" in page.inner_text("#setup-spotify")
    page.set_viewport_size({"width": 860, "height": 620})          # smallest window: nothing overflows
    overflow = page.evaluate("""() => [...document.querySelectorAll('#setup-spotify *')].filter(el => {
        const r = el.getBoundingClientRect(), p = document.querySelector('#setup-spotify').getBoundingClientRect();
        return r.width && (r.right > p.right + 0.5 || r.left < p.left - 0.5); }).map(el => el.className || el.tagName)""")
    assert overflow == []
    assert page.evaluate("document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth")
    shot(page, "ajustes-spotify-minima")
    assert errors == []


def test_while_connecting_the_steps_show_up_numbered(browser, tmp_path):
    """While connecting, the steps (the real connector's) and the Cancel button show under the service."""
    from app.connectors.tidal import TidalConnector
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    tidal.login_steps = lambda b: TidalConnector.login_steps(None, b)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.wait_for_selector(".svc:has(#wait-tidal) .svc-steps li")    # already before anything is clicked
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#cancel-login")
    steps = ".svc:has(#wait-tidal) .svc-steps"
    assert page.eval_on_selector_all(steps + " .step-n", "ns => ns.map(n => n.textContent)") == ["1", "2", "3", "4"]
    text = page.inner_text(steps)
    assert "se abre la página de Tidal en Vivaldi" in text and "«Tidal ✓»" in text
    assert check(page, "conexiones") == []                 # with the steps open, the buttons don't get squashed
    page.set_viewport_size({"width": 860, "height": 620})
    assert check(page, "conexiones") == []
    page.set_viewport_size({"width": 1120, "height": 780})
    shot(page, "ajustes-conectando-pasos")
    page.click("#cancel-login")
    page.wait_for_selector("text=Cancelaste el inicio de sesión.")
    assert errors == []


def test_if_the_login_window_was_closed_open_again_restarts_it(browser, tmp_path):
    """A user connected, closed the browser window and was left with Connect greyed out: with Tidal and Spotify
    the app doesn't learn the window was closed and waits until the time runs out. While it waits, instead
    of Connect there is «Open again» (enabled), which stops that wait and opens the sign-in again."""
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#reopen-login")
    assert page.is_hidden("[data-connect=tidal]") and page.is_enabled("#reopen-login")
    assert page.inner_text("#reopen-login") == "Abrir de nuevo"
    assert check(page, "conexiones") == []
    page.set_viewport_size({"width": 860, "height": 620})
    assert check(page, "conexiones") == []
    shot(page, "conexiones-abrir-de-nuevo")
    page.click("#reopen-login")
    page.wait_for_function("document.querySelector('#reopen-login') && !document.querySelector('#reopen-login').disabled")
    assert [c for c in tidal.calls if c[0] == "connect"] == [("connect", "Vivaldi")] * 2    # it opened again
    assert "Cancelaste" not in page.inner_text("#toast")
    tidal.login_hangs = False                                   # this time the person approves
    page.wait_for_selector("text=Tidal conectado.")
    page.wait_for_selector("#chips .chip.ok")
    assert page.locator("#reopen-login").count() == 0 and page.locator("#cancel-login").count() == 0
    assert tidal.connected and "Tidal ✓" in page.inner_text("#chips")
    assert errors == []


def test_login_can_be_cancelled_from_connections(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#cancel-login")
    assert round(page.locator("#cancel-login").bounding_box()["height"]) == 36   # size system
    page.click("#cancel-login")
    page.wait_for_selector("text=Cancelaste el inicio de sesión.")
    assert page.locator("#cancel-login").count() == 0 and page.is_hidden("#wait-tidal")
    assert not tidal.connected and page.is_enabled("[data-connect=tidal]")
    assert errors == []


@pytest.mark.parametrize("redraw", ["otra pantalla", "otro navegador"])
def test_the_login_controls_survive_a_redraw(browser, tmp_path, redraw):
    """A user opened the sign-in, closed the window and was left with everything greyed out and no button: if the
    screen was redrawn while waiting (changing section or browser), «Open again» and «Cancel»
    disappeared and the wait went on for up to 10 minutes."""
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#reopen-login")
    if redraw == "otra pantalla":
        page.click("nav [data-go=ajustes]")
        page.click("nav [data-go=conexiones]")
    else:
        page.click("#browser-list input >> nth=1")
        page.wait_for_function("document.querySelector('#toast').textContent.includes('Brave')")
    page.wait_for_selector("#reopen-login")
    assert page.is_enabled("#reopen-login") and page.is_enabled("#cancel-login")
    assert page.is_hidden("[data-connect=tidal]")
    page.click("#cancel-login")
    page.wait_for_selector("text=Cancelaste el inicio de sesión.")
    page.wait_for_selector("[data-connect=tidal]:enabled")
    assert page.locator("#reopen-login").count() == 0 and page.locator("#cancel-login").count() == 0
    assert errors == []
