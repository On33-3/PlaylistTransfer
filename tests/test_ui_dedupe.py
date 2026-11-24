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


# ---------------------------------------------------------------- todas las playlists (diseño aprobado el 2026-11-24)

# Lo que se ve raro aunque cumpla las normas de tamaños: medido en la pantalla, en píxeles
ALIGN = """() => {
  const vis = el => el && el.getBoundingClientRect().width > 0, out = [];
  const r = el => el.getBoundingClientRect();
  // El número de cada paso, centrado en su círculo
  document.querySelectorAll('#dup-how .dup-how-n').forEach(n => {
    if (!vis(n)) return;
    const range = document.createRange(); range.selectNodeContents(n);
    const t = range.getBoundingClientRect(), c = r(n);
    const dx = (t.left + t.width / 2) - (c.left + c.width / 2), dy = (t.top + t.height / 2) - (c.top + c.height / 2);
    if (Math.abs(dx) > 1 || Math.abs(dy) > 1.5) out.push(`paso ${n.textContent}: número descentrado (${dx.toFixed(1)}, ${dy.toFixed(1)})`);
  });
  // Las tres tarjetas: misma altura y alineadas arriba
  const cards = [...document.querySelectorAll('#dup-how > div')].filter(vis).map(r);
  if (cards.length && new Set(cards.map(c => Math.round(c.top))).size > 1) out.push('tarjetas de pasos desalineadas arriba');
  if (cards.length && new Set(cards.map(c => Math.round(c.height))).size > 1) out.push('tarjetas de pasos de distinto alto');
  // Las pastillas de estado, centradas en su renglón
  document.querySelectorAll('#dup-pl li').forEach(li => {
    const p = li.querySelector('.dup-pill'); if (!vis(p)) return;
    const d = (r(p).top + r(p).height / 2) - (r(li).top + r(li).height / 2);
    if (Math.abs(d) > 1) out.push(`pastilla corrida ${d.toFixed(1)}px en «${li.textContent.trim().slice(0, 20)}»`);
  });
  // Todo arranca en el mismo borde izquierdo
  const left = Math.round(r(document.querySelector('#repetidos h1')).left);
  ['#dup-controls', '#dup-how', '#dup-progress', '#dup-summary', '.dup-group', '#dup-clean', '#dup-actions'].forEach(sel => {
    const el = document.querySelector(sel);
    if (vis(el) && Math.abs(Math.round(r(el).left) - left) > 1) out.push(`${sel} no arranca en el borde (${r(el).left} vs ${left})`);
  });
  // Nada se sale de la pantalla a lo ancho
  const main = document.querySelector('main');
  if (main.scrollWidth > main.clientWidth + 1) out.push(`la pantalla se sale a lo ancho (${main.scrollWidth} > ${main.clientWidth})`);
  return out;
}"""

SIZES = {"normal": (1120, 780), "minima": (860, 620)}


def all_api(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    yt.add_playlist("aaa", "Primera", [S("Dos", "X", id="d1"), S("Dos", "X", id="d2"), S("Tres", "X", id="t3")])
    for i in range(8):
        yt.add_playlist(f"ok{i}", f"Limpia número {i}", [S(f"A{i}", "X", id=f"a{i}"), S(f"B{i}", "X", id=f"b{i}")])
    return api, tidal, yt


def open_all(page):
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=aaa]", state="attached")


@pytest.mark.parametrize("size", SIZES)
def test_before_checking_the_three_steps_are_shown_and_aligned(browser, tmp_path, size):
    api, *_ = all_api(tmp_path)
    page, errors = open_page(browser, api)
    page.set_viewport_size(dict(zip(("width", "height"), SIZES[size])))
    open_all(page)
    assert page.input_value("#dup-playlist") == "__all__"                     # de entrada, todas
    assert page.inner_text("#dup-playlist option >> nth=0") == "Todas las playlists (10)"
    assert page.is_visible("#dup-how") and page.inner_text("#dup-scan") == "Revisar"
    shot(page, f"repetidos-antes-{size}")
    assert page.evaluate(ALIGN) == [] and check(page, "repetidos") == [] and errors == []


@pytest.mark.parametrize("size", SIZES)
def test_checking_all_shows_progress_then_one_card_per_playlist(browser, tmp_path, size):
    api, tidal, yt = all_api(tmp_path)
    page, errors = open_page(browser, api)
    page.set_viewport_size(dict(zip(("width", "height"), SIZES[size])))
    open_all(page)
    # A mitad de camino: se ve cuál va y cómo dio cada una (se frena la respuesta para mirarla)
    page.evaluate("""() => { const real = onScanEvent; window.mid = null;
        onScanEvent = (p, e) => { real(p, e); if (e.type === 'scan_result' && e.k === 3 && !mid)
          mid = { text: document.querySelector('#dup-progress-text').textContent,
                  pills: [...document.querySelectorAll('#dup-pl .dup-pill')].map(x => x.textContent) }; }; }""")
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group")
    mid = page.evaluate("mid")
    assert mid["text"] == "Revisando 4 de 10 playlists" and "en espera" in mid["pills"] and "sin repetidos" in mid["pills"]
    # La barra a mitad de camino, para la captura y la alineación
    page.evaluate("""() => { window.saved = state.scan; clearDups(); document.querySelector('#dup-progress').hidden = false; document.querySelector('#dup-how').hidden = true;
        const p = {}; onScanEvent(p, { type: 'scan_list', names: ['Primera', 'Clásicos', 'Limpia número 0', 'Una playlist con un nombre larguísimo que no entra en un renglón'] });
        onScanEvent(p, { type: 'scan_start', k: 0, name: 'Primera' }); onScanEvent(p, { type: 'scan_result', k: 0, found: 1 });
        onScanEvent(p, { type: 'scan_start', k: 1, name: 'Clásicos' }); }""")
    shot(page, f"repetidos-revisando-{size}")
    assert page.evaluate(ALIGN) == []
    page.evaluate("document.querySelector('#dup-progress').hidden = true; state.scan = saved; showDupResults()")
    summary = page.inner_text("#dup-summary")
    assert summary.startswith("Encontré 3 repetidos en 2 de 10 playlists.")
    assert page.locator(".dup-group header").all_inner_texts() == ["Clásicos\nse quita 1 de 2\nDestildar todos",
                                                                   "Primera\nse quita 1 de 1\nDestildar todos"]
    assert page.inner_text("#dup-clean") == "Sin repetidos: Limpia número 0, Limpia número 1, Limpia número 2, Limpia número 3 y 4 más."
    assert "tildado se quita" in page.inner_text("#dup-hint")                # qué significa tildar, antes de la lista
    assert page.inner_text("#remove") == "Quitar 2 temas" and page.inner_text("#dup-scan") == "Revisar de nuevo"
    assert page.inner_text("#dup-decide-text") == "Se van a quitar 2 temas de 2 playlists. Queda la versión de la derecha en cada caso."
    shot(page, f"repetidos-resultado-{size}")
    assert page.evaluate(ALIGN) == [] and check(page, "repetidos") == []
    page.click("#remove")
    assert page.inner_text("#c-title") == "¿Quitar 2 temas de 2 playlists?"
    assert page.locator("#c-list li").all_inner_texts() == ["Clásicos: 1 tema", "Primera: 1 tema"]
    page.click("#confirm-yes")
    page.wait_for_selector("text=Quitaste 2 temas de 2 playlists.")
    page.wait_for_function("!state.busy")
    assert [s.id for s in yt.songs("aaa")] == ["d1", "t3"] and len(yt.songs("dups")) == 3
    # Lo que queda: la "Otra versión" de Clásicos (sin tildar), y Primera pasó a las que están bien
    assert page.locator(".dup-group header").all_inner_texts() == ["Clásicos\nse quitan 0 de 1\nTildar todos"]
    assert page.inner_text("#dup-decide-text") == "No hay nada tildado: no se va a quitar nada." and page.is_disabled("#remove")
    assert page.inner_text("#dup-clean").startswith("Sin repetidos: Primera,")
    assert errors == []


def test_when_every_playlist_is_fine_it_says_which_ones_were_checked(browser, tmp_path):
    """Comprobado con datos reales (7 playlists de Tidal sin repetidos): solo decía "Ninguna tiene repetidos"."""
    api, tidal, yt = make_api(tmp_path)
    yt.playlists.pop("dups")
    for i in range(3):
        yt.add_playlist(f"ok{i}", f"Limpia {i}", [S(f"A{i}", "X", id=f"a{i}"), S(f"B{i}", "X", id=f"b{i}")])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=ok0]", state="attached")
    page.click("#dup-scan")
    page.wait_for_selector("#dup-clean:not([hidden])")
    assert page.inner_text("#dup-summary") == "Ninguna de las 3 playlists tiene repetidos."
    assert page.inner_text("#dup-clean") == "Revisé Limpia 0, Limpia 1, Limpia 2."
    assert page.locator(".dup-group").count() == 0 and page.is_hidden("#dup-actions") and errors == []



def test_each_row_says_what_will_happen_and_the_decision_is_always_in_sight(browser, tmp_path):
    """Pedido del usuario (2026-11-24): no dejar nada a la intuición. Tildada: se quita (tachada) → queda.
    Sin tildar: se conserva. La barra con la decisión se ve aunque la lista sea larga."""
    api, tidal, yt = make_api(tmp_path)
    many = []
    for i in range(12):
        many += [S(f"Tema {i}", "X", dur=200, id=f"m{i}"), S(f"Tema {i}", "X", dur=201, id=f"r{i}")]
    many += [S("Rasputin", "Majestic", "Boney M.", dur=187, id="ras1"), S("Rasputin", "Boney M.", dur=352, id="ras2")]
    yt.add_playlist("larga", "Larga", many)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=larga]", state="attached")
    page.select_option("#dup-playlist", "larga")
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group")
    row = page.locator(".dups li >> nth=0")
    shown = "el => [...el.querySelectorAll('.remove .tag')].filter(t => t.getBoundingClientRect().width).map(t => t.textContent)"
    assert row.evaluate(shown) == ["Se quita"] and row.locator(".arrow").is_visible()
    assert row.locator(".remove .t").evaluate("el => getComputedStyle(el).textDecorationLine") == "line-through"
    row.locator("input").uncheck()
    assert row.evaluate(shown) == ["Se conserva"] and not row.locator(".arrow").is_visible()
    assert "Se van a quitar 11 temas" in page.inner_text("#dup-decide-text")
    # La otra grabación viene sin tildar y dice por qué
    ras = page.locator(".dups li", has_text="Rasputin")
    assert not ras.locator("input").is_checked() and ras.locator(".why").inner_text() == "Dura 2:45 más: es otra grabación."
    # Con la lista larga, la barra queda pegada abajo de la ventana mientras se baja
    page.evaluate("document.querySelector('main').scrollTop = 400")
    page.wait_for_timeout(100)
    bar = page.evaluate("(() => { const r = document.querySelector('#dup-actions').getBoundingClientRect(); return [r.bottom, document.querySelector('main').getBoundingClientRect().bottom]; })()")
    assert abs(bar[0] - bar[1]) <= 1, bar
    shot(page, "repetidos-barra-fija")
    # "Destildar todos" y vuelta
    page.click(".dup-toggle")
    assert page.inner_text("#dup-decide-text") == "No hay nada tildado: no se va a quitar nada."
    page.click(".dup-toggle")
    assert "Se van a quitar 13 temas" in page.inner_text("#dup-decide-text")
    # El aviso negro no tapa la barra de decisión
    page.add_style_tag(content=".toast { transition: none !important; }")
    page.evaluate("toast('Quitaste 3 temas de «Larga».')")
    t, b = page.evaluate("[document.querySelector('#toast').getBoundingClientRect().bottom, document.querySelector('#dup-actions').getBoundingClientRect().top]")
    assert t <= b, (t, b)
    assert page.evaluate(ALIGN) == [] and check(page, "repetidos") == [] and errors == []


def test_a_long_check_can_be_cancelled(browser, tmp_path):
    """Revisar muchas playlists largas tarda: se puede cortar, y dice que no se quitó nada."""
    import threading
    api, tidal, yt = all_api(tmp_path)
    real, gate = yt.read, threading.Event()

    def slow(pid):
        gate.wait(5)                                   # la primera lectura espera a que se toque Cancelar
        return real(pid)
    yt.read = slow
    page, errors = open_page(browser, api)
    open_all(page)
    page.click("#dup-scan")
    page.wait_for_selector("#dup-cancel:visible")
    page.click("#dup-cancel")
    assert page.inner_text("#dup-cancel") == "Cancelando…"
    gate.set()
    page.wait_for_selector("text=Revisión cancelada: no se quitó nada.")
    assert yt.calls == [] and errors == []


def test_if_removing_stops_halfway_the_screen_says_so_and_drops_the_old_list(browser, tmp_path):
    api, tidal, yt = all_api(tmp_path)
    page, errors = open_page(browser, api)
    open_all(page)
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group + .dup-group")
    yt.add("aaa", [S("Nueva", "Y", id="n1")])          # "Primera" cambió desde otro lado
    page.click("#remove")
    page.click("#confirm-yes")
    page.wait_for_selector("#dup-summary .error")
    msg = page.inner_text("#dup-summary")
    assert "Clásicos" in msg and "«Primera» cambió" in msg                   # de cuál se quitó y cuál revisar
    assert page.locator(".dup-group").count() == 0 and page.is_hidden("#dup-actions")
    assert len(yt.songs("dups")) == 3 and len(yt.songs("aaa")) == 4 and errors == []
