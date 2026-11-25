"""Languages: both dictionaries complete, no loose text in the UI, and switching language."""
from __future__ import annotations

import json
import re

import pytest

from app import i18n
from app.storage.export import ReportOperation, render
from app.storage.history import Operation, TrackRecord
from tests.conftest import ROOT
from tests.fakes import FakeService, S, build_api

LOCALES = ROOT / "app" / "locales"
UI = ROOT / "app" / "ui"


def ui_scripts() -> str:
    """All of the UI's JavaScript (app/ui/js/*.js), together."""
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted((UI / "js").glob("*.js")))


def load(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- dictionaries

@pytest.mark.parametrize("section", ["ui", "py"])
def test_both_languages_have_exactly_the_same_keys(section):
    es, en = set(load("es")[section]), set(load("en")[section])
    assert es - en == set(), f"faltan en inglés: {sorted(es - en)}"
    assert en - es == set(), f"faltan en castellano: {sorted(en - es)}"


@pytest.mark.parametrize("section", ["ui", "py"])
def test_placeholders_match_between_languages(section):
    es, en = load("es")[section], load("en")[section]
    for key in es:
        assert set(re.findall(r"\{(\w+)\}", es[key])) == set(re.findall(r"\{(\w+)\}", en[key])), key


def test_no_empty_texts_and_english_has_no_spanish_marks():
    for lang in i18n.LANGUAGES:
        for section in ("ui", "py"):
            for key, text in load(lang)[section].items():
                assert text.strip(), f"{lang}.{key} vacío"
    for section in ("ui", "py"):
        for key, text in load("en")[section].items():
            assert not re.search(r"[¿¡ñáéíóú«»]", text, re.I), f"en.{key} parece castellano: {text}"


def test_every_key_used_in_the_ui_exists():
    used = set()
    for src in (ui_scripts(), (UI / "index.html").read_text(encoding="utf-8")):
        src = re.sub(r"/\*.*?\*/|<!--.*?-->", "", src, flags=re.S)        # without comments
        used |= set(re.findall(r'data-i18n(?:-aria)?="([\w.]+)"', src))
        used |= set(re.findall(r'\bt\("([\w.]+)"', src))
    ui = load("es")["ui"]
    missing = [k for k in used if k not in ui and f"{k}_one" not in ui]
    assert missing == [], missing


def test_every_key_used_in_python_exists():
    used = set()
    for f in (ROOT / "app").rglob("*.py"):
        used |= set(re.findall(r'\btr\(f?"([\w.]+)"', f.read_text(encoding="utf-8")))
    py = load("es")["py"]
    used = {k for k in used if "{" not in k}
    missing = [k for k in used if k not in py and f"{k}_one" not in py]
    assert missing == [], missing
    # keys built with f-strings: job.<type>, csv.status.<status>, csv.kind.<type>, csv.<column>
    for kind in ("transfer", "login", "scan", "remove", "alternative"):
        assert f"job.{kind}" in py
    for status in ("ok", "dudoso", "dudoso_omitido", "no_encontrado", "ya_estaba", "quitado"):
        assert f"report.status.{status}" in py


def test_services_that_need_setup_say_so():
    """What needs setting up first (Spotify) has its settings.hint.<service>_setup line; the rest
    show their steps (login_steps), which the test below requires for every service in the registry."""
    from app.connectors import registry
    for lang in ("es", "en"):
        ui = load(lang)["ui"]
        for s in registry.SERVICES:
            if getattr(s.make(), "needs_setup", False):
                assert f"settings.hint.{s.key}_setup" in ui, (lang, s.key)


def test_setup_steps_name_their_buttons_exactly(tmp_path):
    """The setup steps (Spotify) are "like a test case": every text exists in both languages,
    and if a step mentions a button, it names it with {button}, which comes from the same key as the
    button. That way the text never says "open the form" next to a button called something else."""
    from app.connectors.spotify import SpotifyConnector
    info = SpotifyConnector(tmp_path / "s.json", tmp_path / "a.json").setup_info()
    for lang in ("es", "en"):
        ui = load(lang)["ui"]
        for sec in info["sections"]:
            assert sec["title"] in ui
            for st in sec["steps"]:
                for field in ("text", "button", "copy_key", "note"):
                    if field in st:
                        assert st[field] in ui, (lang, st[field])
                assert ("{button}" in ui[st["text"]]) == ("button" in st), (lang, st["text"])


def test_every_login_step_exists_in_both_languages(tmp_path):
    """The steps shown while connecting (each connector gives them, also depending on the browser)."""
    from pathlib import Path as P
    from app.auth.browsers import Browser
    from app.connectors import registry
    browsers = [Browser("Vivaldi", "vivaldi", P(r"C:\v.exe")), Browser("Firefox", "firefox", P(r"C:\f.exe"))]
    for s in registry.SERVICES:
        for b in browsers:
            steps = s.make().login_steps(b)
            assert steps, (s.key, b.kind)
            for lang in ("es", "en"):
                assert all(k in load(lang)["ui"] for k in steps), (lang, s.key, b.kind)


def test_no_hardcoded_spanish_left_in_the_ui_scripts():
    """Outside comments, the UI's JavaScript has no Spanish text: everything goes through t()."""
    src = ui_scripts()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(^|\s)//.*$", "", src, flags=re.M)
    strings = re.findall(r'"([^"\n]*)"|`([^`]*)`', src)
    words = re.compile(r"\b(tema|temas|playlist de|conect|quitar|historial|Listo|Algo salió|ya estaba|"
                       r"agregaste|Cancel(a|ó)|transferencia|navegador|repetido)\b", re.I)
    texts = [a or b for a, b in strings]
    # internal ids (screens like "historial", statuses like "ya_estaba") aren't visible text
    texts = [x for x in texts if not re.fullmatch(r"[a-z_#.-]+", x)]
    offenders = [x for x in texts if words.search(x) and "t(" not in x]
    assert offenders == []


def test_index_html_texts_all_have_keys():
    """Every visible HTML text has its data-i18n (the Spanish left there is only a fallback)."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    body = html.split("<body>", 1)[1]
    body = re.sub(r"<!--.*?-->|<script.*?</script>", "", body, flags=re.S)
    orphans = []
    for m in re.finditer(r"<(\w+)([^>]*)>([^<]+)<", body):
        tag, attrs, text = m.groups()
        text = text.strip()
        if not text or re.fullmatch(r"[\d\s·✓?✗≈…−⇅]+", text) or text == "PlaylistTransfer":
            continue
        if "data-i18n=" not in attrs:
            orphans.append(text)
    assert orphans == []


# ---------------------------------------------------------------- python

def test_t_switches_language_and_handles_plurals():
    i18n.set_language("en")
    assert i18n.t("err.not_connected", service="Tidal") == "Tidal is not connected."
    i18n.set_language("es")
    assert i18n.t("err.not_connected", service="Tidal") == "Tidal no está conectado."
    with pytest.raises(ValueError):
        i18n.set_language("fr")


def test_history_file_follows_the_language():
    op = ReportOperation(Operation(kind="transfer", source="tidal", source_name="A", target="ytmusic", target_name="B",
                                   added=1, created_at="2026-11-08T01:00:00"), [TrackRecord(1, "ok", "x", "y")])
    assert "- Agregado: x — y" in render([op], {"tidal": "Tidal", "ytmusic": "YouTube Music"})
    i18n.set_language("en")
    assert "- Added: x — y" in render([op], {"tidal": "Tidal", "ytmusic": "YouTube Music"})


def test_connector_errors_follow_the_language():
    svc = FakeService("tidal", "Tidal", connected=False)
    from app.connectors.tidal import TidalConnector
    i18n.set_language("en")
    with pytest.raises(Exception, match="Couldn't find a Tidal playlist ID"):
        TidalConnector.parse_id("https://tidal.com/album/1")
    assert svc  # (the fake uses its own messages; the real one, the dictionary's)


# ---------------------------------------------------------------- api: which language it uses and how it changes

def make(tmp_path, system="en", prefs_text=None):
    p = tmp_path / "prefs.json"
    if prefs_text is not None:
        p.write_text(prefs_text, encoding="utf-8")
    tidal = FakeService("tidal", "Tidal")
    tidal.add_playlist("src", "Favoritas", [S("Take On Me", "a-ha", id="t1")])
    yt = FakeService("ytmusic", "YouTube Music", catalog=[S("Take On Me", "a-ha", id="y1")])
    yt.add_playlist("dst", "Mix", [])
    return build_api(tmp_path, {"tidal": tidal, "ytmusic": yt}, system_language=system, prefs=p)


def test_new_install_follows_windows(tmp_path):
    assert make(tmp_path / "a", system="en").languages()["current"] == "en"
    assert make(tmp_path / "b", system="es").languages()["current"] == "es"
    assert make(tmp_path / "c", system="de").languages()["current"] == "en"     # another language: English


def test_existing_users_keep_spanish(tmp_path):
    """Someone who already used the app (which used to exist only in Spanish) doesn't see the language change on its own."""
    api = make(tmp_path, system="en", prefs_text='{"browser": null, "score_ok": 0.8, "score_min": 0.6}')
    assert api.languages()["current"] == "es"


def test_choice_is_saved_and_wins(tmp_path):
    api = make(tmp_path, system="es")
    assert api.set_language("en") == {"ok": True, "language": "en"}
    assert api.ui_strings()["strings"]["nav.home"] == "Home"
    again = make(tmp_path, system="es")                        # reopen the app
    assert again.languages()["current"] == "en"
    assert "error" in again.set_language("fr")


def test_a_new_english_install_stays_english_after_saving_prefs(tmp_path):
    api = make(tmp_path, system="en")
    api.save_thresholds(0.8, 0.6)                              # creates the preferences file
    assert make(tmp_path, system="en").languages()["current"] == "en"


def test_errors_and_reports_come_in_the_chosen_language(tmp_path):
    api = make(tmp_path, system="en")
    assert api.start_transfer("tidal", "tidal", "src", [{"id": "dst"}]) == {
        "error": "The source and destination must be different services."}
    api.set_language("es")
    assert api.start_transfer("tidal", "tidal", "src", [{"id": "dst"}]) == {
        "error": "El origen y el destino tienen que ser servicios distintos."}


def test_language_cannot_change_during_a_task(tmp_path):
    import threading
    api = make(tmp_path)
    gate = threading.Event()
    yt = api._connectors["ytmusic"]
    original = yt.search
    yt.search = lambda s: (gate.wait(5), original(s))[1]
    job = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "Mix"}], True)["job"]
    assert "error" in api.set_language("es")
    gate.set()
    api._jobs.wait(job)
    assert api.set_language("es")["ok"]
