"""What the UI tests share: the test app, opening the page in Edge, screenshots and the design
rules (sizes, centered icons, no cut-off text), which are checked on every screen.

Uses Playwright with the installed Edge (the same engine as WebView2). If it isn't there, the tests are skipped.
With PT_SCREENSHOTS=<folder> it saves screenshots of each screen.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.auth.browsers import Browser
from tests.conftest import ROOT
from tests.fakes import FakeService, S, build_api

# ---------------------------------------------------------------- constants

UI = (ROOT / "app" / "ui" / "index.html").as_uri()
SHOTS = os.environ.get("PT_SCREENSHOTS")

# The page talks to Python as in pywebview: every API call goes through __pt_call
BRIDGE = """
window.pywebview = { api: new Proxy({}, { get: (_, m) => (...args) =>
  window.__pt_call(String(m), JSON.stringify(args)).then(r => JSON.parse(r)) }) };
"""

# Spanish words that shouldn't show with the app in English
SPANISH = re.compile(r"\b(Inicio|Transferir|Quitar|Historial|Ajustes|temas?|Todavía|conectá|Elegí|Listo|"
                     r"Buscar|repetidos?|Encontrado|Revisar|ya estaba|Coincidencias|Navegador|Idioma|"
                     r"Datos|Acerca|predeterminado|Cancelar|agregaste|Probar|Origen|Destino|sin conectar)\b")

VIVALDI = Browser("Vivaldi", "vivaldi", Path(r"C:\vivaldi.exe"), default=True)
BRAVE = Browser("Brave", "brave", Path(r"C:\brave.exe"))


# ---------------------------------------------------------------- design rules

BUTTONS = {28, 36, 42}
SEGMENT = 34
FIELDS = {36, 42}

MEASURE = """(screen) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const root = document.querySelector('#' + screen), out = [];
  root.querySelectorAll('button, input[type=text], input[type=search], input[type=number], select').forEach(el => {
    if (!vis(el) || el.matches('.hist button.row, select > button')) return;   // filas del historial; lo elegido de una lista
    out.push({what: (el.id || el.className || el.tagName) + ' «' + (el.textContent || '').trim().slice(0, 20) + '»',
              h: Math.round(el.getBoundingClientRect().height), tag: el.tagName, seg: !!el.closest('.seg'),
              field: el.matches('input, select')});
  });
  return out;
}"""


# Rule for icon buttons (the ones with no text, like ⇅): they use .icon-btn, they're square
# and the icon is centered, measured on screen. A text character as an icon looks off-center.
ICONS = """(screen) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = [];
  // Ninguna lista desplegable con lo elegido partido en dos renglones: o lo dibuja su <button> en un
  // renglón (con «…» si no entra), o tiene que entrar en el alto de la lista
  document.querySelectorAll('#' + screen + ' select').forEach(s => {
    if (!vis(s)) return;
    const b = s.querySelector(':scope > button');
    if (b ? getComputedStyle(b).whiteSpace !== 'nowrap' : s.scrollHeight > s.clientHeight + 1)
      out.push((s.id || s.className) + ': lista desplegable con el texto partido «' + (s.selectedOptions[0]?.textContent || '').slice(0, 20) + '»');
  });
  document.querySelectorAll('#' + screen + ' button').forEach(b => {
    if (!vis(b) || b.matches('select > button')) return;     // ya se miró con su lista
    // Ningún botón con el texto partido en dos renglones o cortado (pasa si algo al lado lo aplasta)
    if (b.textContent.trim() && !b.matches('.hist button.row') &&
        (b.scrollWidth > b.clientWidth + 1 || b.scrollHeight > b.clientHeight + 1 ||
         Array.from(b.getClientRects()).length > 1 || b.getBoundingClientRect().height > 44))
      out.push((b.id || b.className) + ': texto partido o cortado «' + b.textContent.trim().slice(0, 20) + '»');
    if (b.textContent.trim() && !b.querySelector('svg')) return;      // botón con texto: lo que sigue no aplica
    const name = b.id || b.className;
    if (!b.matches('.icon-btn')) { out.push(name + ': botón sin texto que no usa .icon-btn'); return; }
    if (b.textContent.trim()) { out.push(name + ': el ícono tiene que ser un SVG, no texto'); return; }
    const r = b.getBoundingClientRect(), i = b.querySelector('svg').getBoundingClientRect();
    if (Math.abs(r.width - r.height) > 0.5) out.push(name + `: no es cuadrado (${r.width}x${r.height})`);
    const dx = (i.left + i.width / 2) - (r.left + r.width / 2), dy = (i.top + i.height / 2) - (r.top + r.height / 2);
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) out.push(name + `: ícono descentrado (${dx.toFixed(1)}, ${dy.toFixed(1)})`);
  });
  return out;
}"""


def check(page, screen):
    bad = list(page.evaluate(ICONS, screen))
    for el in page.evaluate(MEASURE, screen):
        allowed = {SEGMENT} if el["seg"] else (FIELDS if el["field"] else BUTTONS)
        if el["h"] not in allowed:
            bad.append(f"{screen}: {el['what']} mide {el['h']}px (permitido: {sorted(allowed)})")
    return bad


# ---------------------------------------------------------------- the test app

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
    api = build_api(tmp_path, {"tidal": tidal, "ytmusic": yt}, browsers=[VIVALDI, BRAVE])
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


def with_spotify(api, tmp_path):
    """Adds Spotify with its fake API (tests/fakes.py). The "browser" approves the sign-in by itself."""
    import threading
    import urllib.request
    from urllib.parse import parse_qs, urlparse

    from app.connectors.spotify import SpotifyConnector
    from tests.fakes import FakeSpotify, track

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


def transfer(page, dry=False):
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    page.click("#dry" if dry else "#go")
    page.wait_for_selector("#done.show")


def english_api(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    api.set_language("en")
    for svc in (tidal, yt):                                   # English playlist names for the test
        svc.playlists = {k: ({"Favoritas": "Favorites", "Para correr": "Running", "Clásicos": "Classics"}.get(n, n), s)
                         for k, (n, s) in svc.playlists.items()}
    return api, tidal, yt


def visible_spanish(page):
    return sorted(set(m.group(0) for m in SPANISH.finditer(page.inner_text("body"))))


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
