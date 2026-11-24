"""La prueba antes de publicar, con tus cuentas reales y sin cambiar nada en ellas.

    py tools/real_check.py                    (todos los servicios conectados)
    py tools/real_check.py --service ytmusic  (la revisión de repetidos solo en ese)
    py tools/real_check.py --no-transfer      (sin la prueba de transferencia, que hace búsquedas)

Recorre la lista que antes se hacía a mano, con la interfaz real (Edge, el mismo motor que la ventana)
y tus sesiones:
  0. la ventana de verdad abre y cierra sola (PT_SMOKE)
  1. Inicio: dice que todo funciona de forma local, en tu PC
  2. Transferir: una prueba sin cambios con tu playlist más chica; la ruta dice servicio · playlist
  3. Quitar repetidos, antes de revisar: las tres tarjetas y «Todas las playlists»
  4. Revisar todas: el avance, Cancelar a mitad y después la revisión entera
  5. El resultado: otras versiones sin tildar, «Se conserva» al destildar, la barra siempre a la vista,
     «Destildar todos», y la pantalla alineada también en la ventana mínima
  6. La confirmación se abre con lo que diría, y se cancela
  7. Ajustes: «Privacidad y datos» se lee (nunca toca «Borrar todo»)

Nunca escribe en tus cuentas: antes de abrir la página, agregar, crear, quitar, desconectar, borrar todo
y transferir de verdad quedan bloqueados (`guard`), y al final se comprueba que nadie lo intentó.
El historial, las búsquedas guardadas y las preferencias de esta prueba van a una carpeta temporal: tu
historial no cambia. Las capturas tienen tus datos reales: quedan en esa carpeta, nunca en el repo.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SMALL = 25              # temas: la prueba de transferencia usa una playlist de hasta este tamaño
SCAN_TIMEOUT = 20 * 60  # segundos por servicio para revisar todas las playlists
WRITES = ("add", "remove", "create")
BLOCKED_API = ("remove_many", "remove_duplicates", "wipe_data", "disconnect", "connect", "set_client_id",
               "add_doubtful", "add_alternative")


# ---------------------------------------------------------------- nada se escribe

def guard(api) -> list[str]:
    """Bloquea todo lo que cambia algo en las cuentas o en la app. Devuelve la lista de intentos
    (tiene que quedar vacía): cada uno también falla, así que nunca llega al servicio."""
    attempts: list[str] = []

    def block_connector(c, name):
        def blocked(*args, **kwargs):
            attempts.append(f"{c.key}.{name}")
            raise RuntimeError(f"prueba real: {c.key}.{name} está bloqueado")
        setattr(c, name, blocked)

    for c in api._connectors.values():
        for name in WRITES:
            block_connector(c, name)

    def block_api(name):
        def blocked(*args, **kwargs):
            attempts.append(f"api.{name}")
            return {"error": f"prueba real: {name} está bloqueado"}
        setattr(api, name, blocked)

    for name in BLOCKED_API:
        block_api(name)

    real_transfer = api.start_transfer

    def only_dry(source, target, source_id, targets, dry_run=False):
        if dry_run is not True:
            attempts.append("api.start_transfer (de verdad)")
            return {"error": "prueba real: solo se permite la prueba sin cambios"}
        return real_transfer(source, target, source_id, targets, True)
    api.start_transfer = only_dry
    return attempts


def real_api(work: Path):
    """La API con tus sesiones, pero con el historial, las búsquedas y las preferencias en `work`."""
    from app.api import Api
    from app.connectors import registry
    from app.storage import paths
    from app.storage.history import History
    work.mkdir(parents=True, exist_ok=True)
    if paths.prefs_file().exists():
        shutil.copy(paths.prefs_file(), work / "preferencias.json")
    return Api(registry.build_connectors(), History(work / "historial.json"), work / "preferencias.json", work)


# ---------------------------------------------------------------- el recorrido

class Run:
    def __init__(self, out: Path):
        self.out, self.results = out, []

    def step(self, name, fn, *args):
        print(f"· {name}…", flush=True)
        started = time.monotonic()
        try:
            detail = fn(*args)
            state = "SALTEADO" if isinstance(detail, Skip) else "OK"
        except AssertionError as e:
            state, detail = "FALLA", str(e) or "no se cumplió lo esperado"
        except Exception as e:                    # un timeout, un selector que no aparece…
            state, detail = "FALLA", f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
        took = time.monotonic() - started
        self.results.append((name, state, str(detail or ""), took))
        print(f"  {state} ({took:.0f} s){': ' + str(detail) if detail else ''}", flush=True)
        return state == "OK"

    def shot(self, page, name, full=False):
        page.screenshot(path=str(self.out / f"{len(self.results) + 1:02d}-{name}.png"), full_page=full)


class Skip(str):
    """Un paso que no aplica (por ejemplo, ningún servicio conectado)."""


def smoke() -> str:
    env = dict(os.environ, PT_SMOKE="1")
    r = subprocess.run([sys.executable, "-m", "app"], cwd=ROOT, env=env, timeout=90, capture_output=True)
    assert r.returncode == 0, f"la ventana salió con el código {r.returncode}: mirá app.log"
    return ""


def home(run, page, design):
    page.click("nav [data-go=inicio]")
    text = page.inner_text("#home-lede")
    tail = page.evaluate("t('home.lede', { services: '\\u0000' }).split('\\u0000').pop()")
    assert text.strip() and text.endswith(tail.strip()), f"el texto de Inicio no es el esperado: «{text}»"
    assert design(page, "inicio") == [], design(page, "inicio")
    run.shot(page, "inicio")
    return ""


def transfer(run, page, design, connected, names):
    if len(connected) < 2:
        return Skip("hace falta tener dos servicios conectados")
    src, dst = connected[0], connected[1]
    page.click("nav [data-go=transferir]")
    page.select_option("#from", src)
    page.select_option("#to", dst)
    page.wait_for_selector("#src-list input[name=src]", timeout=60000)
    picks = page.evaluate("""(small) => [...document.querySelectorAll('#src-list input[name=src]')]
        .map(i => ({ value: i.value, n: Number(i.dataset.count), name: i.closest('li').querySelector('.name').textContent }))
        .filter(p => p.n > 0 && p.n <= small).sort((a, b) => a.n - b.n)""", SMALL)
    if not picks:
        return Skip(f"ninguna playlist de {names[src]} tiene entre 1 y {SMALL} temas")
    p = picks[0]
    page.check(f"#src-list input[name=src][value='{p['value']}']")
    page.check("#dst-new")
    page.click("#dry")
    page.wait_for_selector("#done.show", timeout=10 * 60 * 1000)
    route, done = page.inner_text("#run-route"), page.inner_text("#done")
    assert names[src] in route and names[dst] in route and p["name"] in route, f"la ruta no dice de dónde a dónde: «{route}»"
    from_line = page.evaluate("([name, service]) => t('transfer.from_line', { name, service })", [p["name"], names[src]])
    assert from_line in done, f"el resultado no dice desde dónde: «{done[:200]}»"
    assert page.is_hidden("#open-dest"), "una prueba sin cambios ofrece abrir el destino"
    assert design(page, "transferir") == [], design(page, "transferir")
    run.shot(page, "transferir-prueba", full=True)
    return f"«{p['name']}» ({p['n']} temas) de {names[src]} a {names[dst]}: {page.inner_text('#done-title')}"


def to_dedupe(page, key) -> int:
    """Abre Quitar repetidos en ese servicio y espera sus playlists. Devuelve cuántas se pueden revisar
    («Todas» aparece solo si hay más de una con 2 temas o más)."""
    page.click("nav [data-go=repetidos]")
    page.click(f"#dup-svc [data-svc={key}]")
    # Mientras carga, la única opción es «Cargando…», sin value
    page.wait_for_function("document.querySelector('#dup-playlist option')"
                           " && !document.querySelector('#dup-playlist option:not([value])')", timeout=60000)
    return page.locator("#dup-playlist option[value]:not([value=''])").count() - page.locator("#dup-playlist option[value=__all__]").count()


def before_check(run, page, design, key):
    n = to_dedupe(page, key)
    if n == 0:
        return Skip("ninguna playlist con 2 temas o más")
    want = "__all__" if n > 1 else page.locator("#dup-playlist option").first.get_attribute("value")
    assert page.input_value("#dup-playlist") == want, "la lista no arranca en «Todas las playlists»"
    assert page.is_visible("#dup-how") and page.locator("#dup-how > div").count() == 3, "no se ven las tres tarjetas"
    assert design(page, "repetidos") == [], design(page, "repetidos")
    run.shot(page, f"repetidos-antes-{key}")
    return "" if n > 1 else "una sola playlist para revisar: no hace falta «Todas»"


def cancel_midway(run, page, key):
    to_dedupe(page, key)
    page.click("#dup-scan")
    page.wait_for_selector("#dup-progress:not([hidden])")
    page.wait_for_timeout(1500)
    run.shot(page, f"repetidos-revisando-{key}")
    page.click("#dup-cancel")
    page.wait_for_function("document.querySelector('#dup-progress').hidden", timeout=120000)
    said = page.evaluate("t('dedupe.cancelled')")
    assert said in page.inner_text("#repetidos"), "al cancelar no dice que no se quitó nada"
    return said


def scan_all(run, page, key):
    if to_dedupe(page, key) == 0:
        return Skip("ninguna playlist con 2 temas o más")
    page.click("#dup-scan")
    page.wait_for_selector("#dup-progress:not([hidden])")
    page.wait_for_function("document.querySelector('#dup-progress').hidden", timeout=SCAN_TIMEOUT * 1000)
    page.wait_for_timeout(300)
    run.shot(page, f"repetidos-resultado-{key}", full=True)
    cards = page.locator(".dup-group").count()
    failed = page.inner_text("#dup-failed") if page.is_visible("#dup-failed") else ""
    return f"{page.inner_text('#dup-summary')} ({cards} con repetidos{'; ' + failed if failed else ''})"


def result_checks(run, page, design):
    if not page.locator(".dup-group").count():
        return Skip("no hay repetidos: nada para destildar")
    rows = page.evaluate("""() => [...document.querySelectorAll('.dups li')].map(li => ({
        checked: li.querySelector('input').checked, version: !!li.querySelector('.tag.version'), why: !!li.querySelector('.why') }))""")
    wrong = [r for r in rows if (r["version"] or r["why"]) and r["checked"]]
    assert not wrong, f"{len(wrong)} otras versiones vienen tildadas"
    original = page.evaluate("[...document.querySelectorAll('.dups input')].map(i => i.checked)")
    try:
        # Destildar una fila: dice «Se conserva» en vez de «Se quita»
        # (por posición: «la primera tildada» sería otra fila después del clic)
        k = page.evaluate("[...document.querySelectorAll('.dups input')].findIndex(i => i.checked)")
        if k >= 0:
            row = page.locator(".dups li").nth(k)
            row.locator("input").click()
            assert row.locator(".tag.off").is_visible() and not row.locator(".tag.on").is_visible(), "al destildar no cambia a «Se conserva»"
        # «Destildar todos» y «Tildar todos» en la primera tarjeta
        group, toggle = page.locator(".dup-group").first, page.locator(".dup-group .dup-toggle").first
        untick, tick = page.evaluate("[t('dedupe.untick_all'), t('dedupe.tick_all')]")
        if toggle.inner_text() == untick:
            toggle.click()
            assert group.locator("input:checked").count() == 0 and toggle.inner_text() == tick, "«Destildar todos» no destildó"
        toggle.click()
        assert group.locator("input:not(:checked)").count() == 0 and toggle.inner_text() == untick, "«Tildar todos» no tildó"
        # La barra de decisión, siempre a la vista mientras se baja
        for where in (0.5, 1):
            page.evaluate("(f) => { const m = document.querySelector('main'); m.scrollTop = m.scrollHeight * f; window.scrollTo(0, document.body.scrollHeight * f); }", where)
            page.wait_for_timeout(150)
            r = page.evaluate("(() => { const b = document.querySelector('#dup-actions').getBoundingClientRect(); return [b.top, b.bottom, innerHeight]; })()")
            assert 0 <= r[0] and r[1] <= r[2] + 1, f"la barra de abajo no se ve al bajar ({r})"
        assert design(page, "repetidos") == [], design(page, "repetidos")
    finally:
        page.evaluate("""(orig) => [...document.querySelectorAll('.dups input')].forEach((i, k) => {
            if (i.checked !== orig[k]) i.click(); })""", original)
    return f"{len(rows)} filas"


def smallest_window(run, page, design):
    from tests.test_ui_dedupe import ALIGN
    page.set_viewport_size({"width": 860, "height": 620})
    try:
        page.wait_for_timeout(300)
        bad = page.evaluate(ALIGN) + design(page, "repetidos")
        run.shot(page, "repetidos-ventana-minima")
        assert bad == [], bad
    finally:
        page.set_viewport_size({"width": 1120, "height": 780})
    return ""


def confirm_dialog(run, page, attempts):
    if not page.locator(".dup-group").count():
        return Skip("no hay repetidos: nada para confirmar")
    if not page.locator(".dups input:checked").count():
        page.locator(".dups input").first.click()
    page.click("#remove")
    page.wait_for_selector("#confirm[open]")
    said = page.inner_text("#c-title")
    run.shot(page, "repetidos-confirmacion")
    page.click("#confirm-no")
    page.wait_for_selector("#confirm:not([open])", state="attached")
    assert not attempts, f"se intentó escribir: {attempts}"
    return f"«{said}», cancelado"


def settings(run, page, design):
    page.click("nav [data-go=ajustes]")
    text = page.inner_text("[data-i18n='settings.data_text']")
    assert text == page.evaluate("t('settings.data_text')"), f"Privacidad y datos dice otra cosa: «{text}»"
    assert page.is_visible("#wipe"), "no se ve «Borrar todo»"
    assert design(page, "ajustes") == [], design(page, "ajustes")
    run.shot(page, "ajustes")
    return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--service", help="revisar repetidos solo en este servicio (tidal, ytmusic, spotify)")
    ap.add_argument("--no-transfer", action="store_true", help="sin la prueba de transferencia")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    from tests.ui_helpers import check, open_page

    out = Path(tempfile.gettempdir()) / "PlaylistTransfer-prueba" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    run = Run(out)
    run.step("La ventana de verdad abre y cierra", smoke)

    api = real_api(out / "datos")
    attempts = guard(api)
    status = {s["key"]: s["status"] for s in api.status()}
    connected = [k for k, st in status.items() if st == "connected"]
    names = {s["key"]: s["name"] for s in api.services()}
    print(f"  conectados: {', '.join(names[k] for k in connected) or 'ninguno'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page, errors = open_page(browser, api)
        run.step("Inicio", home, run, page, check)
        if not args.no_transfer:
            run.step("Transferir: prueba sin cambios", transfer, run, page, check, connected, names)
        keys = [k for k in connected if not args.service or k == args.service]
        for i, key in enumerate(keys):
            n = names[key]
            run.step(f"Repetidos en {n}: antes de revisar", before_check, run, page, check, key)
            if i == 0:
                run.step(f"Repetidos en {n}: cancelar a mitad", cancel_midway, run, page, key)
            if run.step(f"Repetidos en {n}: revisar todas", scan_all, run, page, key):
                run.step(f"Repetidos en {n}: el resultado", result_checks, run, page, check)
                run.step(f"Repetidos en {n}: ventana mínima", smallest_window, run, page, check)
                run.step(f"Repetidos en {n}: la confirmación", confirm_dialog, run, page, attempts)
        if not keys:
            run.results.append(("Quitar repetidos", "SALTEADO", "ningún servicio conectado", 0))
        run.step("Ajustes: privacidad", settings, run, page, check)
        browser.close()

    run.results.append(("Nada se escribió en tus cuentas", "FALLA" if attempts else "OK", ", ".join(attempts), 0))
    run.results.append(("Sin errores de JavaScript", "FALLA" if errors else "OK", "; ".join(errors[:3]), 0))

    print("\n" + "\n".join(f"{state:9} {name}{'  — ' + detail if detail and state != 'OK' else ''}"
                           for name, state, detail, _ in run.results))
    print(f"\nCapturas (con tus datos, fuera del repo): {out}")
    bad = [r for r in run.results if r[1] == "FALLA"]
    print("OK: lista para publicar" if not bad else f"{len(bad)} con FALLA")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
