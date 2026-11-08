"""Capturas para el README (docs/img/), con los servicios falsos de los tests: nunca datos reales.

Uso (desde la raíz del repo, con playwright instalado):  py tools/readme_screenshots.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.test_ui import make_api, open_page  # noqa: E402

OUT = ROOT / "docs" / "img"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        api, tidal, yt = make_api(Path(tmp))
        page, errors = open_page(browser, api)
        page.add_style_tag(content="#toast { display: none !important; }")   # los avisos no van en las capturas

        page.click("nav [data-go=transferir]")
        page.check("input[name=src][value=src]")
        page.check("input[name=dst][value=dst]")
        page.click("#go")
        page.wait_for_selector("#done.show")
        page.screenshot(path=str(OUT / "transferir.png"))

        page.click("nav [data-go=repetidos]")
        page.click("#dup-svc [data-svc=ytmusic]")
        page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
        page.select_option("#dup-playlist", "dups")
        page.click("#dup-scan")
        page.wait_for_selector("#dups li")
        page.screenshot(path=str(OUT / "repetidos.png"))
        page.click("#remove")
        page.click("#confirm-yes")
        page.wait_for_function("document.querySelector('#toast').textContent.startsWith('Quitaste 1 tema')")

        page.click("nav [data-go=historial]")
        page.wait_for_selector("#hist li[data-op]")
        page.click("#hist button.row >> nth=1")
        page.wait_for_selector("#hist li.open .detail .badge")
        page.screenshot(path=str(OUT / "historial.png"))

        page.click("nav [data-go=inicio]")
        page.wait_for_selector("#home-history .mosaic")
        page.screenshot(path=str(OUT / "inicio.png"))
        browser.close()
        if errors:
            raise SystemExit(f"Errores en la página: {errors}")
    print("Capturas en", OUT)


if __name__ == "__main__":
    main()
