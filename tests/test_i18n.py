"""Idiomas: los dos diccionarios completos, sin texto suelto en la UI, y el cambio de idioma."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import i18n
from app.api import Api
from app.storage.export import header
from app.storage.history import History
from tests.conftest import ROOT
from tests.fakes import FakeService, S

LOCALES = ROOT / "app" / "locales"
UI = ROOT / "app" / "ui"


def load(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- diccionarios

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
    for f in ("app.js", "index.html"):
        src = (UI / f).read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/|<!--.*?-->", "", src, flags=re.S)        # sin comentarios
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
    # claves armadas con f-strings: job.<tipo>, csv.status.<estado>, csv.kind.<tipo>, csv.<columna>
    for kind in ("transfer", "login", "scan", "remove", "alternative"):
        assert f"job.{kind}" in py
    for status in ("ok", "dudoso", "no_encontrado", "ya_estaba", "quitado"):
        assert f"csv.status.{status}" in py
    for kind in ("transfer", "dedupe", "dry_run"):
        assert f"csv.kind.{kind}" in py


def test_no_hardcoded_spanish_left_in_app_js():
    """Fuera de los comentarios, app.js no tiene texto en castellano: todo pasa por t()."""
    src = (UI / "app.js").read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(^|\s)//.*$", "", src, flags=re.M)
    strings = re.findall(r'"([^"\n]*)"|`([^`]*)`', src)
    words = re.compile(r"\b(tema|temas|playlist de|conect|quitar|historial|Listo|Algo salió|ya estaba|"
                       r"agregaste|Cancel(a|ó)|transferencia|navegador|repetido)\b", re.I)
    texts = [a or b for a, b in strings]
    # los ids internos (pantallas como "historial", estados como "ya_estaba") no son texto visible
    texts = [x for x in texts if not re.fullmatch(r"[a-z_#.-]+", x)]
    offenders = [x for x in texts if words.search(x) and "t(" not in x]
    assert offenders == []


def test_index_html_texts_all_have_keys():
    """Todo texto visible del HTML tiene su data-i18n (el castellano que queda es solo respaldo)."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    body = html.split("<body>", 1)[1]
    body = re.sub(r"<!--.*?-->|<script.*?</script>", "", body, flags=re.S)
    orphans = []
    for m in re.finditer(r"<(\w+)([^>]*)>([^<]+)<", body):
        tag, attrs, text = m.groups()
        text = text.strip()
        if not text or re.fullmatch(r"[\d\s·✓?✗≈…−]+", text) or text == "PlaylistTransfer":
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


def test_csv_header_follows_the_language():
    assert header()[0] == "Fecha"
    i18n.set_language("en")
    assert header()[:3] == ["Date", "Operation", "Source service"]


def test_connector_errors_follow_the_language():
    svc = FakeService("tidal", "Tidal", connected=False)
    from app.connectors.tidal import TidalConnector
    i18n.set_language("en")
    with pytest.raises(Exception, match="Couldn't find a Tidal playlist ID"):
        TidalConnector.parse_id("https://tidal.com/album/1")
    assert svc  # (el falso usa sus propios mensajes; el real, los del diccionario)


# ---------------------------------------------------------------- api: qué idioma usa y cómo se cambia

def make(tmp_path, system="en", prefs_text=None):
    p = tmp_path / "prefs.json"
    if prefs_text is not None:
        p.write_text(prefs_text, encoding="utf-8")
    tidal = FakeService("tidal", "Tidal")
    tidal.add_playlist("src", "Favoritas", [S("Take On Me", "a-ha", id="t1")])
    yt = FakeService("ytmusic", "YouTube Music", catalog=[S("Take On Me", "a-ha", id="y1")])
    yt.add_playlist("dst", "Mix", [])
    return Api({"tidal": tidal, "ytmusic": yt}, History(tmp_path / "h.sqlite"), p, tmp_path,
               list_browsers=lambda: [], open_url=lambda u, b: None, system_language=lambda: system)


def test_new_install_follows_windows(tmp_path):
    assert make(tmp_path / "a", system="en").languages()["current"] == "en"
    assert make(tmp_path / "b", system="es").languages()["current"] == "es"
    assert make(tmp_path / "c", system="de").languages()["current"] == "en"     # otro idioma: inglés


def test_existing_users_keep_spanish(tmp_path):
    """Quien ya usaba la app (que antes solo existía en castellano) no ve un cambio de idioma solo."""
    api = make(tmp_path, system="en", prefs_text='{"browser": null, "score_ok": 0.8, "score_min": 0.6}')
    assert api.languages()["current"] == "es"


def test_choice_is_saved_and_wins(tmp_path):
    api = make(tmp_path, system="es")
    assert api.set_language("en") == {"ok": True, "language": "en"}
    assert api.ui_strings()["strings"]["nav.home"] == "Home"
    again = make(tmp_path, system="es")                        # reabrir la app
    assert again.languages()["current"] == "en"
    assert "error" in again.set_language("fr")


def test_a_new_english_install_stays_english_after_saving_prefs(tmp_path):
    api = make(tmp_path, system="en")
    api.save_thresholds(0.8, 0.6)                              # crea el archivo de preferencias
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
