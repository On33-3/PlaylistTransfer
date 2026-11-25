"""Settings and language: thresholds, switching language and no Spanish in English."""
from __future__ import annotations

from tests.ui_helpers import make_api, open_page, shot, transfer, english_api, visible_spanish


def test_thresholds_in_settings(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=ajustes]")
    page.wait_for_function("document.querySelector('#th-ok').value === '0.75'")
    page.fill("#th-ok", "0.70")
    page.click("#th-save")
    page.wait_for_selector("#th-error:not([hidden])")
    assert "versiones en vivo o remix" in page.inner_text("#th-error")
    page.fill("#th-ok", "0.85"); page.fill("#th-min", "0.65")
    page.click("#th-save")
    page.wait_for_selector("text=Guardado: sin dudar desde 0.85")
    assert api.settings()["score_ok"] == 0.85
    shot(page, "10-ajustes-umbrales")
    page.click("#th-reset")
    page.wait_for_function("document.querySelector('#th-ok').value === '0.75'")
    assert api.settings()["score_ok"] == 0.75
    assert "tidalapi (LGPL-3.0)" in page.inner_text("#licenses")
    assert errors == []


def test_every_screen_in_english_has_no_spanish_left(browser, tmp_path):
    api, tidal, yt = english_api(tmp_path)
    page, errors = open_page(browser, api)
    assert page.get_attribute("html", "lang") == "en"
    assert page.inner_text("nav") .startswith("Home")
    assert visible_spanish(page) == []
    transfer(page)
    assert "added 1 song" in page.inner_text("#done") and visible_spanish(page) == []
    shot(page, "en-transfer")
    page.click("nav [data-go=repetidos]")
    page.click("#dup-svc [data-svc=ytmusic]")
    page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
    page.select_option("#dup-playlist", "dups")
    page.click("#dup-scan")
    page.wait_for_selector("#dups li")
    assert "Found 2 duplicates" in page.inner_text("#dup-summary") and visible_spanish(page) == []
    page.click("#remove")
    assert page.inner_text("#c-title") == "Remove 1 song from “Classics”?"
    page.click("#confirm-yes")
    page.wait_for_selector("text=Removed 1 song")
    for screen in ("historial", "conexiones", "ajustes", "inicio"):
        page.click(f"nav [data-go={screen}]")
        page.wait_for_timeout(250)
        assert visible_spanish(page) == [], screen
        shot(page, f"en-{screen}")
    assert errors == []


def test_switch_language_from_settings(browser, tmp_path):
    api, *_ = make_api(tmp_path)
    page, errors = open_page(browser, api)
    assert page.inner_text("nav [data-go=ajustes]") == "Ajustes"
    page.click("nav [data-go=ajustes]")
    page.wait_for_selector("#language-list input[value=en]")
    assert page.is_checked("#language-list input[value=es]")
    page.check("#language-list input[value=en]")
    page.wait_for_selector("nav [data-go=ajustes]:text-is('Settings')")
    # The page reloads: the menu changes before Settings finishes drawing (it is waited for, not assumed)
    page.wait_for_selector("#ajustes.active #language-list input[value=en]:checked")
    assert page.inner_text("#ajustes h1") == "Settings"
    page.check("#language-list input[value=es]")
    page.wait_for_selector("nav [data-go=ajustes]:text-is('Ajustes')")
    assert errors == []
