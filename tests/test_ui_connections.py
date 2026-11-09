"""Conexiones: pasos de cada servicio, conectar, cancelar y configurar Spotify."""
from __future__ import annotations

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
    """Sin conectar, cada servicio muestra sus pasos numerados antes de tocar nada (el 1: tocar Conectar);
    lo que se configura antes (Spotify) manda a «Configurar». Al conectar, los pasos desaparecen."""
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
    assert first == ["Tocá «Conectar»: se abre la página de Tidal en Vivaldi. Si no la ves, buscá Vivaldi en la barra de tareas.",
                     "Tocá «Conectar»: se abre una ventana nueva de Vivaldi, aparte de la tuya y sin tus sesiones. Es normal."]
    assert page.eval_on_selector_all(".svc-steps", "s => s.map(x => x.querySelectorAll('li').length)") == [4, 4]
    assert page.inner_text(".svc-hint") == "Necesita Spotify Premium y crear tu propia app en Spotify: tocá «Configurar» y seguí los pasos."
    assert check(page, "conexiones") == []
    shot(page, "ajustes-pasos-antes")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("text=Tidal conectado.")
    page.wait_for_function("document.querySelectorAll('.svc-steps').length === 1")     # quedan los de YouTube Music
    page.locator("input[name=br]").nth(1).check()                  # cambia a Brave: los pasos lo nombran
    page.click("nav [data-go=inicio]"); page.click("nav [data-go=conexiones]")
    page.wait_for_selector(".svc-steps >> text=ventana nueva de Brave")
    assert errors == []


def test_spotify_setup_from_connections(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    sp, fake = with_spotify(api, tmp_path)
    page, errors = open_page(browser, api)
    shot(page, "inicio-spotify-sin-configurar")
    assert "Spotify sin configurar" in " ".join(page.inner_text("#chips").split())
    assert page.is_hidden("#home-connect")              # no se reclama: Spotify es opcional
    page.click("nav [data-go=conexiones]")
    page.click("[data-setup=spotify]")
    page.wait_for_selector("#setup-spotify:not([hidden])")
    panel = page.inner_text("#setup-spotify")
    assert "Premium" in panel and "http://127.0.0.1:8973/callback" in panel
    # el texto nombra el botón exactamente como se llama
    assert "Tocá «Abrir el formulario para crear la app»" in panel
    assert page.is_visible("#setup-spotify button:text-is('Abrir el formulario para crear la app')")
    assert "No copies el «Client secret»" in panel
    numbers = page.eval_on_selector_all("#setup-spotify .step-n", "ns => ns.map(n => n.textContent)")
    assert numbers == ["1.1", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "3.1", "3.2", "3.3", "3.4"]
    assert check(page, "conexiones") == []                 # el panel respeta el sistema de tamaños
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
    # cada valor para pegar tiene su botón Copiar, al lado del paso donde va
    copies = page.eval_on_selector_all("#setup-spotify [data-copy]", "bs => bs.map(b => b.dataset.copy)")
    assert copies == ["PlaylistTransfer", "Pasar mis playlists entre servicios", "http://127.0.0.1:8973/callback"]
    page.click("#setup-spotify [data-copy='http://127.0.0.1:8973/callback']")
    page.wait_for_selector("text=Copiado.")
    assert page.evaluate("navigator.clipboard.readText()") == "http://127.0.0.1:8973/callback"
    redirect_step = page.locator("#setup-spotify li", has=page.locator("[data-copy='http://127.0.0.1:8973/callback']"))
    assert "Redirect URIs" in redirect_step.inner_text() and redirect_step.locator(".step-n").inner_text() == "2.6"
    # cada paso trae su link, y cada uno abre la página justa
    links = page.eval_on_selector_all("#setup-spotify [data-link]", "bs => bs.map(b => b.dataset.link)")
    assert links == ["https://www.spotify.com/premium/", "https://developer.spotify.com/dashboard/create",
                     "https://developer.spotify.com/dashboard"]
    for i, url in enumerate(links):
        page.locator("#setup-spotify [data-link]").nth(i).click()
        page.wait_for_timeout(300)                                 # el pedido a Python es asíncrono
        assert api._open_url_calls[-1] == url
    assert "not secure" in page.inner_text("#setup-spotify")
    page.set_viewport_size({"width": 860, "height": 620})          # ventana mínima: nada se sale
    overflow = page.evaluate("""() => [...document.querySelectorAll('#setup-spotify *')].filter(el => {
        const r = el.getBoundingClientRect(), p = document.querySelector('#setup-spotify').getBoundingClientRect();
        return r.width && (r.right > p.right + 0.5 || r.left < p.left - 0.5); }).map(el => el.className || el.tagName)""")
    assert overflow == []
    assert page.evaluate("document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth")
    shot(page, "ajustes-spotify-minima")
    assert errors == []


def test_while_connecting_the_steps_show_up_numbered(browser, tmp_path):
    """Mientras se conecta, debajo del servicio aparecen los pasos (los del conector real) y el botón Cancelar."""
    from app.connectors.tidal import TidalConnector
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    tidal.login_steps = lambda b: TidalConnector.login_steps(None, b)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.wait_for_selector(".svc:has(#wait-tidal) .svc-steps li")    # ya antes de tocar nada
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#cancel-login")
    steps = ".svc:has(#wait-tidal) .svc-steps"
    assert page.eval_on_selector_all(steps + " .step-n", "ns => ns.map(n => n.textContent)") == ["1", "2", "3", "4"]
    text = page.inner_text(steps)
    assert "se abre la página de Tidal en Vivaldi" in text and "«Tidal ✓»" in text
    assert check(page, "conexiones") == []                 # con los pasos abiertos, los botones no se aplastan
    page.set_viewport_size({"width": 860, "height": 620})
    assert check(page, "conexiones") == []
    page.set_viewport_size({"width": 1120, "height": 780})
    shot(page, "ajustes-conectando-pasos")
    page.click("#cancel-login")
    page.wait_for_selector("text=Cancelaste el inicio de sesión.")
    assert errors == []


def test_login_can_be_cancelled_from_connections(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path, connected=False)
    tidal.login_hangs = True
    page, errors = open_page(browser, api)
    page.click("nav [data-go=conexiones]")
    page.click("[data-connect=tidal]")
    page.wait_for_selector("#cancel-login")
    assert round(page.locator("#cancel-login").bounding_box()["height"]) == 36   # sistema de tamaños
    page.click("#cancel-login")
    page.wait_for_selector("text=Cancelaste el inicio de sesión.")
    assert page.locator("#cancel-login").count() == 0 and page.is_hidden("#wait-tidal")
    assert not tidal.connected and page.is_enabled("[data-connect=tidal]")
    assert errors == []
