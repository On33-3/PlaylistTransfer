"""La UI con datos extremos: que el diseño no se rompa por exceso.

Muchas playlists, nombres larguísimos (con y sin espacios), japonés y emojis, transferencias de
cientos de temas, mucho historial, y la ventana en su tamaño mínimo (860×620) y en el normal.
"""
from __future__ import annotations

import pytest

from tests.fakes import S
from tests.ui_helpers import make_api, open_page, shot

LONG = "Mi playlist con un nombre larguísimo que no entra de ninguna manera en una sola línea de la lista"
NOSPACE = "Playlist_sin_espacios_" + "x" * 70
JAPANESE = "夜に駆ける・群青・アイドル — 最高のアニメソング集 2026"
EMOJI = "🎧🔥 Para entrenar 💪🏽 (versión extendida) 🎶"

SIZES = {"minima": (860, 620), "normal": (1120, 780)}


def stress_api(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    names = [LONG, NOSPACE, JAPANESE, EMOJI] + [f"Playlist {i:03d}" for i in range(296)]
    for i, name in enumerate(names):
        tidal.add_playlist(f"t{i}", name, [S(f"Tema {i}-{k}", "Artista", id=f"t{i}-{k}") for k in range(3)])
        yt.add_playlist(f"y{i}", name, [])
    long_songs = [S(f"Una canción con un título muy, muy largo que sigue y sigue número {k} (Remastered 2024) [Deluxe]",
                    "Un artista con nombre largo, Otro artista invitado, Y un tercero", dur=200 + k, id=f"L{k}")
                  for k in range(120)]
    long_songs += [S("Supercalifragilisticexpialidocious" * 3, "SinEspacios" * 6, id="NS")]
    tidal.add_playlist("big", "Grande", long_songs)
    yt.catalog += [S(s.title, *s.artists, dur=s.duration, id="y" + s.id) for s in long_songs[::2]]
    yt.add_playlist("dupsbig", EMOJI, [s for s in long_songs[:60] for _ in range(2)])
    return api, tidal, yt


def overflow(page):
    """Elementos visibles que se salen de su caja por los costados (o la página entera)."""
    return page.evaluate("""() => {
      const out = [];
      const main = document.querySelector('main');
      if (main.scrollWidth > main.clientWidth + 1) out.push('main: ' + main.scrollWidth + ' > ' + main.clientWidth);
      if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push('página con scroll horizontal');
      for (const el of document.querySelectorAll('main *, .topbar *, nav *')) {
        const r = el.getBoundingClientRect();
        if (!r.width || getComputedStyle(el).visibility === 'hidden') continue;
        const box = el.closest('.picker ul, .window');
        if (r.right > window.innerWidth + 1) out.push((el.id || el.className || el.tagName) + ' se sale ' + Math.round(r.right - window.innerWidth) + 'px');
      }
      return [...new Set(out)].slice(0, 15);
    }""")


def test_extreme_data_does_not_break_the_layout(browser, tmp_path):
    """Un solo recorrido; cada pantalla se mide en los dos tamaños de ventana (y no el recorrido dos veces)."""
    api, tidal, yt = stress_api(tmp_path)
    page, errors = open_page(browser, api)
    problems = {}

    def measure(screen, picture):
        for name, (width, height) in SIZES.items():
            page.set_viewport_size({"width": width, "height": height})
            problems[f"{screen} {name}"] = overflow(page)
            shot(page, f"stress-{name}-{picture}")

    page.click("nav [data-go=transferir]")
    page.wait_for_selector("input[name=src][value=t0]")
    measure("transferir-elegir", "1-elegir")
    heights = page.evaluate("[...document.querySelectorAll('.picker ul')].map(u => Math.round(u.getBoundingClientRect().height))")

    page.check("input[name=src][value=big]")
    page.check("input[name=dst][value=y0]")
    page.click("#go")
    page.wait_for_selector("#done.show", timeout=60000)
    measure("transferir-resultado", "2-resultado")

    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dupsbig]", state="attached")
    page.select_option("#dup-playlist", "dupsbig")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    measure("repetidos", "3-repetidos")

    for i in range(3):                                               # varias operaciones en el historial
        page.click("nav [data-go=transferir]")
        page.click("#again") if page.is_visible("#again") else None
        page.wait_for_selector(f"input[name=src][value=t{i}]")
        page.check(f"input[name=src][value=t{i}]")
        page.check("#dst-new")
        page.click("#go")
        page.wait_for_selector("#done.show")
    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row >> nth=0")
    measure("historial", "4-historial")

    page.click("nav [data-go=inicio]")
    page.wait_for_selector("#inicio .features")
    measure("inicio", "5-inicio")

    page.click("nav [data-go=conexiones]")
    page.wait_for_selector("#svc-list .svc")
    measure("conexiones", "6-conexiones")

    page.click("nav [data-go=ajustes]")
    page.wait_for_selector("#th-ok")
    measure("ajustes", "6-ajustes")

    assert errors == []
    assert {k: v for k, v in problems.items() if v} == {}
    assert len(set(heights)) == 1, f"las listas de origen y destino tienen alturas distintas: {heights}"


@pytest.mark.parametrize("size", SIZES)
def test_the_toast_never_covers_the_end_of_a_screen(browser, tmp_path, size):
    """El aviso negro sale abajo al centro. Bajando del todo, lo último de cada pantalla (en Conexiones,
    el último navegador) queda por encima del aviso: nunca tapado, ni en la ventana mínima."""
    from tests.ui_helpers import BRAVE, VIVALDI, with_spotify
    api, tidal, yt = make_api(tmp_path, connected=False)
    with_spotify(api, tmp_path)
    api._list_browsers = lambda: [VIVALDI, BRAVE] * 2               # una lista larga de navegadores
    page, errors = open_page(browser, api)
    page.set_viewport_size(dict(zip(("width", "height"), SIZES[size])))
    page.add_style_tag(content=".toast { transition: none !important; }")
    covered = {}
    for screen in ("inicio", "conexiones", "transferir", "repetidos", "historial", "ajustes"):
        page.click(f"nav [data-go={screen}]")
        page.wait_for_timeout(150)
        page.evaluate("toast('Las ventanas para iniciar sesión se abren en Vivaldi.')")
        overlap = page.evaluate("""() => {
            const main = document.querySelector("main"); main.scrollTop = main.scrollHeight;
            const top = document.querySelector("#toast").getBoundingClientRect().top;
            const els = [...document.querySelectorAll(".screen.active *")].filter(e => {
                const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden";
            });
            return Math.max(0, ...els.map(e => e.getBoundingClientRect().bottom)) - top;
        }""")
        if overlap > 0:
            covered[screen] = round(overlap)
    assert covered == {}
    assert errors == []
