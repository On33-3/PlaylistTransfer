"""Screenshots for the READMEs (docs/img/<language>/), with the tests' fake services: never real data.
README.md (English) uses docs/img/en/ and README.es.md, docs/img/es/.

Usage (from the repo root, with playwright installed):  py tools/readme_screenshots.py
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
# how the "you removed a song" toast starts in each language (to wait for it to finish)
REMOVED = {"es": "Quitaste 1 tema", "en": "Removed 1 song"}


def capture(browser, lang: str, tmp: Path) -> None:
    out = OUT / lang
    out.mkdir(parents=True, exist_ok=True)
    api, tidal, yt = (english_api if lang == "en" else make_api)(tmp)
    # Spotify not connected: it looks like a new install, not "coming soon"
    api._connectors["spotify"] = FakeService("spotify", "Spotify", connected=False)
    page, errors = open_page(browser, api)
    page.add_style_tag(content="#toast { display: none !important; }")   # toasts don't go in the screenshots

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
