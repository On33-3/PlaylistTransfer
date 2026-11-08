"""Sistema de diseño: todos los botones y campos usan los tamaños definidos (mejoras 3).

Botones: 42 (principal), 36 (secundario) y 28 (dentro de un tema). Los segmentos de un selector
miden 34 y el selector entero, 42. Campos: 36, o 42 si están en una fila de acciones. Los
selectores de la frase de Inicio ("Pasar de Tidal a…") son grandes a propósito.
"""
from __future__ import annotations

from tests.test_ui import browser, make_api, open_page  # noqa: F401  (fixture de Playwright)

BUTTONS = {28, 36, 42}
SEGMENT = 34
FIELDS = {36, 42}

MEASURE = """(screen) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const root = document.querySelector('#' + screen), out = [];
  root.querySelectorAll('button, input[type=text], input[type=search], input[type=number], select').forEach(el => {
    if (!vis(el) || el.matches('.hist button.row, .sentence select')) return;   // filas del historial y la frase de Inicio
    out.push({what: (el.id || el.className || el.tagName) + ' «' + (el.textContent || '').trim().slice(0, 20) + '»',
              h: Math.round(el.getBoundingClientRect().height), tag: el.tagName, seg: !!el.closest('.seg'),
              field: el.matches('input, select')});
  });
  return out;
}"""


def check(page, screen):
    bad = []
    for el in page.evaluate(MEASURE, screen):
        allowed = {SEGMENT} if el["seg"] else (FIELDS if el["field"] else BUTTONS)
        if el["h"] not in allowed:
            bad.append(f"{screen}: {el['what']} mide {el['h']}px (permitido: {sorted(allowed)})")
    return bad


def test_every_control_uses_the_design_sizes(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    for i in range(12):
        yt.add_playlist(f"x{i}", f"Lista {i}", [])
    page, errors = open_page(browser, api)
    bad = check(page, "inicio")
    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src]")
    page.check("input[name=src][value=src]")
    page.check("#dst-new")
    bad += check(page, "transferir")
    page.uncheck("#dst-new")
    page.check("input[name=dst][value=dst]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    bad += check(page, "transferir")
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    bad += check(page, "repetidos")
    row = page.evaluate("[...document.querySelectorAll('#dup-controls > *')].map(e => Math.round(e.getBoundingClientRect().height))")
    assert len(set(row)) == 1, f"la fila de Repetidos no está alineada: {row}"
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    bad += check(page, "historial")
    page.click("nav [data-go=ajustes]")
    page.wait_for_selector("#th-ok")
    bad += check(page, "ajustes")
    assert errors == []
    assert bad == []
