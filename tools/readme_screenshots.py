"""Screenshots for the READMEs (docs/img/<language>/), with a made-up demo library (tools/demo_library.py):
never real data. README.md (English) uses docs/img/en/ and README.es.md, docs/img/es/.
They are taken at twice the resolution, so they stay sharp on high-density screens and on LinkedIn.

Usage (from the repo root, with playwright installed):  py tools/readme_screenshots.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.ui_helpers import BRIDGE, UI  # noqa: E402
from tools.demo_library import build  # noqa: E402

OUT = ROOT / "docs" / "img"
WINDOW = {"width": 1120, "height": 880}      # a bit taller than the default window: whole cards, nothing cut in half
SCALE = 2


def open_demo(browser, api):
    page = browser.new_context(viewport=WINDOW, device_scale_factor=SCALE).new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.expose_function("__pt_call", lambda m, a: json.dumps(getattr(api, m)(*json.loads(a))))
    page.add_init_script(BRIDGE)
    page.goto(UI)
    page.wait_for_selector("#chips .chip:not(.checking)")
    page.add_style_tag(content="#toast { display: none !important; }")   # toasts don't go in the screenshots
    return page, errors


def wait_for_history(api, n: int) -> None:
    until = time.monotonic() + 30
    while len(api.history()) < n:
        if time.monotonic() > until:
            raise SystemExit("The history never got the operation")
        time.sleep(0.1)


def capture(browser, lang: str, tmp: Path) -> None:
    out = OUT / lang
    out.mkdir(parents=True, exist_ok=True)
    api, tidal, yt, spotify = build(tmp, lang)
    # Something already in the history: a playlist moved earlier
    api._jobs.wait(api.start_transfer("tidal", "ytmusic", "sunday", [{"name": tidal.playlists["sunday"][0], "id": None}])["job"])
    page, errors = open_demo(browser, api)

    # Remove duplicates: every YouTube Music playlist, with the result and the decision on screen
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=__all__]", state="attached")
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group")
    page.wait_for_function("document.querySelector('#dup-progress').hidden")
    page.screenshot(path=str(out / "repetidos.png"))
    page.click("#remove")
    page.click("#confirm-yes")
    wait_for_history(api, 2)

    # Transfer: Tidal to Spotify, into a playlist that already has some of the songs
    page.click("nav [data-go=transferir]")
    page.select_option("#from", "tidal")
    page.select_option("#to", "spotify")
    page.wait_for_selector("input[name=src][value=road]")
    page.check("input[name=src][value=road]")
    page.check("input[name=dst][value=road]")
    page.click("#go")
    page.wait_for_selector("#done.show")
    page.screenshot(path=str(out / "transferir.png"))

    page.click("nav [data-go=historial]")
    page.wait_for_selector("#hist li[data-op]")
    page.click("#hist button.row >> nth=1")                       # the clean-up: a short detail, all three rows visible
    page.wait_for_selector("#hist li.open .detail .badge")
    page.screenshot(path=str(out / "historial.png"))

    page.click("nav [data-go=inicio]")
    page.wait_for_selector("#inicio .features")
    page.screenshot(path=str(out / "inicio.png"))
    page.context.close()
    if errors:
        raise SystemExit(f"Errors on the page ({lang}): {errors}")


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        for lang in ("en", "es"):
            with tempfile.TemporaryDirectory() as tmp:
                capture(browser, lang, Path(tmp))
        browser.close()
    print("Screenshots in", OUT)


if __name__ == "__main__":
    main()
