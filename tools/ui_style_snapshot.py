"""Foto del estilo calculado de cada elemento, en cada estado de la app (cada pantalla y
lo que se abre en ella, en la ventana normal y en la mínima), para comprobar que un refactor del CSS o
del JavaScript no cambia nada. A diferencia de las capturas, no depende de la hora ni del mouse.

    py tools/ui_style_snapshot.py antes.json        (antes de tocar nada)
    py tools/ui_style_snapshot.py despues.json
    py tools/ui_style_snapshot.py --diff antes.json despues.json   → "0 diferencias"
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DUMP = """() => {
  const key = el => { const parts = []; for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const i = e.parentElement ? [...e.parentElement.children].indexOf(e) : 0;
      parts.unshift(`${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}[${i}]`); } return parts.join('>'); };
  const out = {};
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 || r.height > 0; };
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el)) continue;
    for (const pseudo of [null, '::before', '::after', '::checkmark', '::picker-icon']) {
      const cs = getComputedStyle(el, pseudo);
      if (pseudo && (cs.content === 'none' || cs.content === 'normal') && pseudo !== '::checkmark' && pseudo !== '::picker-icon') continue;
      const d = {};
      for (let i = 0; i < cs.length; i++) { const p = cs[i]; d[p] = cs.getPropertyValue(p); }
      const r = el.getBoundingClientRect();
      if (!pseudo) d['@box'] = [r.x, r.y, r.width, r.height].map(v => Math.round(v * 10) / 10).join(',');
      out[key(el) + (pseudo || '')] = d;
    }
  }
  return out;
}"""


def states(browser):
    from app.connectors.tidal import TidalConnector
    from app.connectors.ytmusic import YTMusicConnector
    from tests.ui_helpers import make_api, open_page, with_spotify

    def fresh(**kw):
        tmp = Path(tempfile.mkdtemp())
        return tmp, make_api(tmp, **kw)

    # 1. primer uso (Inicio, Conexiones con pasos reales y el panel de Spotify, conectando)
    tmp, (api, tidal, yt) = fresh(connected=False)
    with_spotify(api, tmp)
    tidal.login_steps = lambda b: TidalConnector.login_steps(None, b)
    yt.login_steps = lambda b: YTMusicConnector.login_steps(None, b)
    tidal.login_hangs = True
    page, errors = open_page(browser, api)
    yield "inicio-primer-uso", page
    page.click("nav [data-go=conexiones]"); page.wait_for_selector(".svc-steps")
    yield "conexiones", page
    page.click("[data-setup=spotify]"); page.wait_for_selector("#setup-spotify:not([hidden])")
    yield "conexiones-spotify", page
    page.click("[data-connect=tidal]"); page.wait_for_selector("#cancel-login")
    yield "conexiones-conectando", page
    page.click("#cancel-login"); page.wait_for_timeout(300)
    page.click("nav [data-go=ajustes]"); page.wait_for_function("document.querySelector('#th-ok').value !== ''")
    yield "ajustes", page
    page.click("#wipe"); page.wait_for_selector("#wipe-confirm[open]"); page.wait_for_timeout(300)
    yield "ajustes-borrar-todo", page
    page.click("#wipe-no")
    page.close()

    # 2. Transferir y resultado con "?" y alternativas
    tmp, (api, tidal, yt) = fresh()
    from tests.fakes import S
    yt.add_playlist("otra", "Otra lista", [S("Rasputin", "Majestic", "Boney M.", dur=187, id="r1"),
                                           S("Rasputin", "Boney M.", dur=352, id="r2"),
                                           S("Take On Me", "a-ha", dur=225, id="t1"), S("Take On Me", "a-ha", dur=226, id="t2")])
    page, errors = open_page(browser, api)
    page.click("nav [data-go=transferir]"); page.wait_for_selector("input[name=src]")
    page.check("input[name=src][value=src]"); page.check("#dst-new")
    yield "transferir-elegir", page
    page.uncheck("#dst-new"); page.check("input[name=dst][value=dst]")
    page.click("#go"); page.wait_for_selector("#done.show"); page.wait_for_timeout(400)
    yield "transferir-resultado", page
    page.click("nav [data-go=repetidos]"); page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    yield "repetidos-antes", page
    # El avance, armado con eventos fijos (no depende de cuánto tarda la revisión)
    page.evaluate("""() => { document.querySelector('#dup-how').hidden = true; document.querySelector('#dup-progress').hidden = false;
        const p = {}; onScanEvent(p, { type: 'scan_list', names: ['Clásicos', 'Otra lista', 'Para correr'] });
        onScanEvent(p, { type: 'scan_start', k: 0, name: 'Clásicos' }); onScanEvent(p, { type: 'scan_result', k: 0, found: 2 });
        onScanEvent(p, { type: 'scan_start', k: 1, name: 'Otra lista' }); }""")
    yield "repetidos-revisando", page
    page.evaluate("clearDups()")
    page.click("#dup-scan"); page.wait_for_selector(".dup-group + .dup-group")
    yield "repetidos-todas", page
    page.locator(".dups li input >> nth=0").uncheck()
    page.evaluate("document.querySelector('main').scrollTop = 0")
    yield "repetidos-destildado", page
    page.select_option("#dup-playlist", "dups"); page.click("#dup-scan"); page.wait_for_selector("#dups li")
    yield "repetidos", page
    page.click("#remove"); page.wait_for_selector("#confirm[open]"); page.wait_for_timeout(300)
    yield "repetidos-confirmar", page
    page.click("#confirm-no")
    page.click("nav [data-go=historial]"); page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row >> nth=0"); page.wait_for_timeout(400)
    yield "historial", page
    page.click("nav [data-go=inicio]"); page.wait_for_timeout(200)
    yield "inicio-con-datos", page
    page.close()


def dump(out_path):
    from playwright.sync_api import sync_playwright
    result = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="msedge", headless=True)
        for size in ((1120, 780), (860, 620)):
            for name, page in states(b):
                page.set_viewport_size({"width": size[0], "height": size[1]})
                page.mouse.move(0, 0)
                page.wait_for_timeout(250)
                result[f"{name}@{size[0]}"] = page.evaluate(DUMP)
                page.set_viewport_size({"width": 1120, "height": 780})
        b.close()
    Path(out_path).write_text(json.dumps(result), encoding="utf-8")
    print(f"{len(result)} estados, {sum(len(v) for v in result.values())} elementos")


def diff(a_path, b_path):
    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (a_path, b_path))
    problems = []
    for state in sorted(set(a) | set(b)):
        x, y = a.get(state, {}), b.get(state, {})
        for el in sorted(set(x) | set(y)):
            if el not in x or el not in y:
                problems.append(f"{state}: {'falta' if el not in y else 'sobra'} {el[-90:]}")
                continue
            for prop in sorted(set(x[el]) | set(y[el])):
                if x[el].get(prop) != y[el].get(prop):
                    problems.append(f"{state}: {el[-70:]} {prop}: {x[el].get(prop)!r} -> {y[el].get(prop)!r}")
    print(f"{len(problems)} diferencias")
    for p in problems[:60]:
        print("  ", p)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "--diff":
        diff(args[1], args[2])
    elif len(args) == 1 and not args[0].startswith("-"):     # "--help" no es un nombre de archivo (pasó: 100 MB)
        dump(args[0])
    else:
        sys.exit(__doc__)
