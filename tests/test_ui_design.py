"""Sistema de diseño: cada pantalla cumple las normas de tests/ui_helpers.py (tamaños de botones y
campos, botones de ícono centrados, ningún texto cortado)."""
from __future__ import annotations

from tests.ui_helpers import check, make_api, open_page

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
    page.click("nav [data-go=conexiones]")
    page.wait_for_selector("#svc-list .svc")
    bad += check(page, "conexiones")
    page.click("nav [data-go=ajustes]")
    page.wait_for_selector("#th-ok")
    bad += check(page, "ajustes")
    assert errors == []
    assert bad == []


# ---------------------------------------------------------------- números centrados en sus círculos

def _ink_offset(png: bytes, scale: int) -> tuple[float, float] | None:
    """(dx, dy) en píxeles CSS entre el centro de la "tinta" (texto oscuro) y el centro de la imagen."""
    import struct
    import zlib
    w, h = struct.unpack(">II", png[16:24])
    bpp = 4 if png[25] == 6 else 3
    idat, i = b"", 8
    while i < len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        if png[i + 4:i + 8] == b"IDAT":
            idat += png[i + 8:i + 8 + n]
        i += 12 + n
    raw, stride = zlib.decompress(idat), w * bpp
    prev, pos, xs, ys = bytearray(stride), 0, [], []
    for y in range(h):
        f, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride]); pos += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b, c = prev[x], (prev[x - bpp] if x >= bpp else 0)
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        for x in range(w):
            if sum(line[x * bpp:x * bpp + 3]) / 3 < 110:
                xs.append(x); ys.append(y)
        prev = line
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2 - (w - 1) / 2) / scale, ((min(ys) + max(ys)) / 2 - (h - 1) / 2) / scale


def test_numbers_and_symbols_are_centered_in_their_circles(browser, tmp_path):
    """Con Segoe UI un dígito queda ~1,5 px bajo del centro si no se corrige (lo vio el usuario)."""
    import json
    from tests.ui_helpers import BRIDGE, UI
    api, *_ = make_api(tmp_path)
    ctx = browser.new_context(viewport={"width": 1120, "height": 780}, device_scale_factor=4)
    page = ctx.new_page()
    page.expose_function("__pt_call", lambda m, a: json.dumps(getattr(api, m)(*json.loads(a))))
    page.add_init_script(BRIDGE)
    page.goto(UI)
    page.wait_for_selector("#chips .chip:not(.checking)")
    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    off = {}
    for sel in ["#steps li b", "#tracks .badge.ok", "#tracks .badge.doubt", "#tracks .badge.miss", "#tracks .badge.dup"]:
        for k, el in enumerate(page.query_selector_all(sel)[:3]):
            d = _ink_offset(el.screenshot(), 4)
            if d:
                off[f"{sel} {k + 1}"] = (round(d[0], 2), round(d[1], 2))
    ctx.close()
    assert len(off) >= 7
    bad = {k: v for k, v in off.items() if abs(v[0]) > 1 or abs(v[1]) > 1}
    assert bad == {}, bad
