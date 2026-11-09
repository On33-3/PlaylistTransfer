"""Capturas para los README (docs/img/<idioma>/), con los servicios falsos de los tests: nunca datos reales.
README.md (inglés) usa docs/img/en/ y README.es.md, docs/img/es/.

Uso (desde la raíz del repo, con playwright instalado):  py tools/readme_screenshots.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.fakes import FakeService  # noqa: E402
from tests.ui_helpers import english_api, make_api, open_page  # noqa: E402

OUT = ROOT / "docs" / "img"
# cómo empieza el aviso de "quitaste un tema" en cada idioma (para esperar a que termine)
REMOVED = {"es": "Quitaste 1 tema", "en": "Removed 1 song"}


def capture(browser, lang: str, tmp: Path) -> None:
    out = OUT / lang
    out.mkdir(parents=True, exist_ok=True)
    api, tidal, yt = (english_api if lang == "en" else make_api)(tmp)
    # Spotify sin conectar: se ve como en una instalación nueva, no "próximamente"
    api._connectors["spotify"] = FakeService("spotify", "Spotify", connected=False)
    page, errors = open_page(browser, api)
    page.add_style_tag(content="#toast { display: none !important; }")   # los avisos no van en las capturas

    page.click("nav [data-go=transferir]")
    page.check("input[name=src][value=src]")
    page.check("input[name=dst][value=dst]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    page.screenshot(path=str(out / "transferir.png"))

    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    page.screenshot(path=str(out / "repetidos.png"))
    page.click("#remove")
    page.click("#confirm-yes")
    page.wait_for_function("t => document.querySelector('#toast').textContent.startsWith(t)", arg=REMOVED[lang])

    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row >> nth=1")
    page.wait_for_selector("#hist li.open .detail .badge")
    page.screenshot(path=str(out / "historial.png"))

    page.click("nav [data-go=inicio]")
    page.wait_for_selector("#inicio .features")
    page.screenshot(path=str(out / "inicio.png"))
    page.close()
    if errors:
        raise SystemExit(f"Errores en la página ({lang}): {errors}")


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        for lang in ("en", "es"):
            with tempfile.TemporaryDirectory() as tmp:
                capture(browser, lang, Path(tmp))
        browser.close()
    print("Capturas en", OUT)


if __name__ == "__main__":
    main()
