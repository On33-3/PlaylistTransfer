"""La API que usa la ventana, con servicios falsos y una carpeta de datos vacía."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.auth.browsers import Browser
from app.jobs import Jobs
from app.storage import prefs
from app.storage.history import History
from app.connectors.base import ConnectorError
from tests.fakes import FakeService, S, build_api

VIVALDI = Browser("Vivaldi", "vivaldi", Path(r"C:\vivaldi.exe"), default=True)
CHROME = Browser("Google Chrome", "chrome", Path(r"C:\chrome.exe"))


@pytest.fixture
def app(tmp_path):
    tidal = FakeService("tidal", "Tidal")
    tidal.add_playlist("src", "Favoritas", [S("Take On Me", "a-ha", dur=225, id="t1"),
                                            S("Hallelujah", "Jeff Buckley", dur=414, id="t2")])
    yt = FakeService("ytmusic", "YouTube Music", catalog=[S("Take On Me", "a-ha", dur=226, id="y1"),
                                                          S("Hallelujah", "Leonard Cohen", dur=279, id="y2")])
    yt.add_playlist("dst", "Favoritas", [])
    yt.add_playlist("dups", "Clásicos", [
        S("Virtual Insanity", "Jamiroquai", dur=228, id="v1"),
        S("Take On Me", "a-ha", dur=226, id="v2"),
        S("Virtual Insanity (Official Video)", "Jamiroquai", dur=245, id="v3"),
        S("Virtual Insanity (Live)", "Jamiroquai", dur=300, id="v4"),
    ])
    api = build_api(tmp_path, {"tidal": tidal, "ytmusic": yt}, browsers=[VIVALDI, CHROME])
    return api, tidal, yt


def wait(api, job_id, timeout=10):
    return api._jobs.wait(job_id, timeout)


# ---------------------------------------------------------------- primer uso

def test_first_run_state(app):
    api, tidal, yt = app
    assert api.history() == []
    tidal.connected = yt.connected = False
    assert [(s["key"], s["status"]) for s in api.status()] == [
        ("tidal", "disconnected"), ("ytmusic", "disconnected"), ("spotify", "soon")]
    b = api.browsers()
    assert b["chosen"] is None and b["suggested"] == str(VIVALDI.exe)


def test_connect_uses_the_suggested_browser(app):
    api, tidal, yt = app
    tidal.connected = False
    r = wait(api, api.connect("tidal")["job"])
    assert r["state"] == "done" and tidal.calls == [("connect", "Vivaldi")]
    assert prefs.load(api._prefs_path)["browser"]["name"] == "Vivaldi"
    assert any(e["type"] == "login" and e["browser"] == "Vivaldi" for e in r["events"])


def test_connect_can_be_cancelled(app):
    api, tidal, yt = app
    tidal.connected, tidal.login_hangs = False, True
    job = api.connect("tidal")["job"]
    api.cancel(job)
    r = wait(api, job)
    assert r["state"] == "cancelled" and not tidal.connected
    assert "job" in api.connect("ytmusic")             # no queda nada trabado


def test_choose_browser(app):
    api, *_ = app
    assert api.choose_browser(str(CHROME.exe))["name"] == "Google Chrome"
    assert api.browsers()["chosen"] == str(CHROME.exe)
    assert "error" in api.choose_browser(r"C:\no-existe.exe")


def test_an_uninstalled_browser_is_not_used(app):
    """Si el navegador elegido se desinstaló, se vuelve al predeterminado en vez de fallar al conectar."""
    api, tidal, yt = app
    api.choose_browser(str(CHROME.exe))
    api._list_browsers = lambda: [VIVALDI]                  # desinstalaron Chrome
    assert api.browsers()["chosen"] is None and api.browsers()["suggested"] == str(VIVALDI.exe)
    tidal.connected = False
    r = wait(api, api.connect("tidal")["job"])
    assert r["state"] == "done" and tidal.calls == [("connect", "Vivaldi")]
    assert prefs.load(api._prefs_path)["browser"]["name"] == "Vivaldi"


def test_disconnected_service_gives_a_readable_error(app):
    api, tidal, yt = app
    yt.connected = False
    assert api.playlists("ytmusic") == {"error": "YouTube Music no está conectado."}
    assert "error" in api.playlists("spotify")


# ---------------------------------------------------------------- transferir

def test_transfer_end_to_end_and_history(app):
    api, tidal, yt = app
    assert api.playlists("tidal") == [{"id": "src", "name": "Favoritas", "count": 2}]
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "Favoritas"}], False)["job"])
    assert r["state"] == "done", r["error"]
    assert r["result"]["targets"][0]["added"] == 1 and r["result"]["summary"]["not_found"] == 1
    assert [s.id for s in yt.songs("dst")] == ["y1"]
    [op] = api.history()
    assert (op["kind"], op["source_label"], op["target_label"], op["added"], op["not_found"]) == \
        ("transfer", "Tidal", "YouTube Music", 1, 1)
    detail = api.history_detail(op["id"])
    assert [(t["title"], t["artist"], t["status"]) for t in detail] == [
        ("Take On Me", "a-ha", "ok"), ("Hallelujah", "Jeff Buckley", "no_encontrado")]


def test_dry_run_is_not_recorded(app):
    api, tidal, yt = app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": None, "name": "Nueva"}], True)["job"])
    assert r["state"] == "done" and r["result"]["dry_run"]
    assert api.history() == [] and yt.calls == []


def test_add_the_version_of_another_artist_only_when_asked(app):
    api, tidal, yt = app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "Favoritas"}], False)["job"])
    assert "y2" not in [s.id for s in yt.songs("dst")]                     # Cohen no se agregó solo
    hallelujah = next(e for e in r["events"] if e["type"] == "track" and e["n"] == 2)
    assert hallelujah["alternatives"][0]["artist"] == "Leonard Cohen"
    a = wait(api, api.add_alternative(r["result"]["transfer"], 2, 0)["job"])
    assert a["state"] == "done" and [s.id for s in yt.songs("dst")] == ["y1", "y2"]
    assert "error" in api.add_alternative(r["result"]["transfer"], 2, 0)    # un tema se agrega una sola vez
    assert a["result"]["summary"]["not_found"] == 0 and a["result"]["targets"][0]["added"] == 2
    ops = api.history()                                                     # es la misma transferencia: una sola fila
    assert len(ops) == 1 and (ops[0]["added"], ops[0]["not_found"]) == (2, 0)
    assert "ok" in ops[0]["tiles"] and "no_encontrado" not in ops[0]["tiles"]


def test_alternatives_are_not_offered_after_a_dry_run(app):
    api, *_ = app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert "error" in api.add_alternative(r["result"]["transfer"], 2, 0)


def test_transfer_input_checks(app):
    api, *_ = app
    assert "error" in api.start_transfer("tidal", "tidal", "src", [{"id": "dst"}])
    assert "error" in api.start_transfer("tidal", "ytmusic", "src", [])


def test_only_one_job_at_a_time(app):
    api, tidal, yt = app
    gate = threading.Event()
    original = yt.search
    yt.search = lambda s: (gate.wait(5), original(s))[1]
    first = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)
    assert "job" in first
    second = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)
    assert "error" in second and "Esperá" in second["error"]
    gate.set()
    assert wait(api, first["job"])["state"] == "done"


def test_cancel_a_transfer(app):
    api, tidal, yt = app
    original = yt.search
    yt.search = lambda s: (time.sleep(0.2), original(s))[1]
    job = api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"]
    api.cancel(job)
    assert wait(api, job)["state"] == "cancelled"
    assert yt.calls == [] and api.history() == []


# ---------------------------------------------------------------- quitar repetidos

def scan(api):
    r = wait(api, api.scan_duplicates("ytmusic", "dups")["job"])
    assert r["state"] == "done", r["error"]
    return r["result"]["groups"][0]


def test_scan_marks_other_versions(app):
    api, *_ = app
    res = scan(api)
    assert [(d["index"], d["kind"]) for d in res["duplicates"]] == [(2, "misma"), (3, "otra_version")]
    assert res["name"] == "Clásicos" and res["total"] == 4


def test_remove_needs_confirmation(app):
    api, tidal, yt = app
    res = scan(api)
    for confirmed in (False, None, "true", 1):
        assert "error" in api.remove_duplicates(res["scan"], [0], confirmed)
    assert yt.calls == [] and len(yt.songs("dups")) == 4


def test_remove_only_the_selected_ones_and_record_it(app):
    api, tidal, yt = app
    res = scan(api)
    r = wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert r["state"] == "done" and r["result"]["removed"] == 1
    assert [s.id for s in yt.songs("dups")] == ["v1", "v2", "v4"]       # la en vivo se quedó
    [op] = api.history()
    assert op["kind"] == "dedupe" and op["removed"] == 1
    [t] = api.history_detail(op["id"])
    assert (t["title"], t["status"], t["match_title"]) == ("Virtual Insanity (Official Video)", "quitado", "Virtual Insanity")


def test_remove_refuses_if_the_playlist_changed(app):
    api, tidal, yt = app
    res = scan(api)
    yt.add("dups", [S("Otra", "Alguien", id="nuevo")])                   # alguien la cambió desde otro lado
    yt.calls.clear()
    r = wait(api, api.remove_duplicates(res["scan"], [0, 1], True)["job"])
    assert r["state"] == "error" and "cambió" in r["error"] and "no se quitó nada" in r["error"]
    assert yt.calls == [] and api.history() == []


def test_a_scan_can_only_be_used_once(app):
    api, *_ = app
    res = scan(api)
    wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert "error" in api.remove_duplicates(res["scan"], [0], True)


def test_bad_selection(app):
    api, *_ = app
    res = scan(api)
    assert "error" in api.remove_duplicates(res["scan"], [99], True)
    assert "error" in api.remove_duplicates(res["scan"], [], True)
    assert "error" in api.remove_duplicates("scan-inventado", [0], True)


# ---------------------------------------------------------------- varios

def test_open_url_only_for_music_services(app):
    api, *_ = app
    assert api.open_url("https://music.youtube.com/playlist?list=x") == {"ok": True}
    assert "error" in api.open_url("file:///C:/Windows/System32/calc.exe")
    assert "error" in api.open_url("https://evil.example.com")
    assert "error" in api.open_url("https://tidal.com.evil.example/")         # el prefijo incluye la barra
    assert len(api._open_url_calls) == 1


def test_services_come_from_the_registry(app):
    """Un solo lugar define los servicios: la API y la ventana los toman de ahí."""
    from app.connectors import registry
    api, *_ = app
    assert api.services() == [{"key": "tidal", "name": "Tidal", "available": True},
                              {"key": "ytmusic", "name": "YouTube Music", "available": True},
                              {"key": "spotify", "name": "Spotify", "available": False}]
    assert [s["key"] for s in api.status()] == [s.key for s in registry.SERVICES]
    assert registry.name("ytmusic") == "YouTube Music" and registry.name(None) is None
    assert all(s.links and all(p.startswith("https://") and p.endswith("/") for p in s.links)
               for s in registry.SERVICES)


def test_ui_has_no_service_list_of_its_own():
    from tests.test_i18n import ui_scripts
    js = ui_scripts()
    assert "YouTube Music" not in js and '"tidal"' not in js and '"ytmusic"' not in js


def test_unexpected_errors_become_messages(app):
    api, tidal, yt = app
    def boom(_):
        raise RuntimeError("se cortó internet")
    yt.search = boom
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert r["state"] == "error" and "se cortó internet" in r["error"]


def test_without_internet_the_message_says_so(app):
    """Un error de conexión de `requests` (lo usan las librerías de Tidal y YouTube Music) no sale como
    "algo salió mal (ConnectionError: HTTPSConnectionPool…)", ni en una tarea ni en un llamado directo."""
    import requests
    api, tidal, yt = app

    def offline(*_):
        raise requests.ConnectionError("HTTPSConnectionPool(host='music.youtube.com'): Max retries exceeded")
    yt.read = offline
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
    assert r["state"] == "error" and r["error"].startswith("No hay conexión") and "HTTPS" not in r["error"]
    yt.list_playlists = offline
    assert api.playlists("ytmusic")["error"].startswith("No hay conexión")


def test_api_exposes_no_public_state(app):
    """pywebview publica a la página todo atributo público: solo tienen que verse métodos."""
    api, *_ = app
    public = [n for n in vars(api) if not n.startswith("_") and n != "opened"]
    assert public == []


def test_jobs_poll_unknown():
    assert Jobs().poll("nada")["state"] == "error"


# ---------------------------------------------------------------- historial en texto y umbrales

@pytest.fixture
def saving(app, tmp_path):
    api, tidal, yt = app
    chosen = {"path": str(tmp_path / "salida" / "historial.txt"), "asked": []}
    def dialog(name):
        chosen["asked"].append(name)
        return chosen["path"]
    api._save_dialog = dialog
    api._open_file = lambda p: chosen.setdefault("opened", p)
    return api, yt, chosen


def test_download_history_on_request(saving):
    api, yt, chosen = saving
    assert "error" in api.export_history()                                  # vacío: nada para descargar
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    r = api.export_history()
    assert r["operations"] == 1 and r["name"] == "historial.txt" and chosen["asked"][0].startswith("historial-")
    assert chosen["asked"][0].endswith(".txt")
    text = Path(r["path"]).read_text(encoding="utf-8")
    assert "- Agregado: Take On Me — a-ha" in text and "- No encontrado: Hallelujah — Jeff Buckley" in text
    assert api.open_export() == {"ok": True} and Path(chosen["opened"]) == Path(r["path"])


def test_cancelling_save_as_writes_nothing(saving):
    api, yt, chosen = saving
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    chosen["path"] = None
    assert api.export_history() == {"cancelled": True}
    assert "error" in api.open_export()


def test_txt_extension_is_added(saving):
    api, yt, chosen = saving
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    chosen["path"] = chosen["path"].replace(".txt", "")
    assert api.export_history()["name"] == "historial.txt"


def test_there_is_no_per_transfer_report_anymore(app):
    """Se quitó el reporte de cada transferencia: lo que pasó queda en el Historial."""
    api, *_ = app
    assert not hasattr(api, "export_transfer")


def test_history_tiles_and_filter(app):
    api, tidal, yt = app
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    res = wait(api, api.scan_duplicates("ytmusic", "dups")["job"])["result"]["groups"][0]
    wait(api, api.remove_duplicates(res["scan"], [0], True)["job"])
    assert [o["kind"] for o in api.history()] == ["dedupe", "transfer"]
    assert [o["kind"] for o in api.history(None, "transfer")] == ["transfer"]
    assert api.history(None, "transfer")[0]["tiles"] == ["ok", "no_encontrado"]
    assert api.history(1)[0]["tiles"] == ["quitado"]


def test_thresholds_are_validated_and_used(app):
    api, tidal, yt = app
    s = api.settings()
    assert (s["score_ok"], s["score_min"]) == (0.75, 0.6) and s["limits"]["ok_min"] == 0.71
    for ok, mn in [(0.70, 0.6), (1.0, 0.6), (0.8, 0.5), (0.8, 0.78), ("a", 0.6), (None, None)]:
        assert "error" in api.save_thresholds(ok, mn), (ok, mn)
    assert api.save_thresholds("0.9", "0.8") == {"ok": True, "score_ok": 0.9, "score_min": 0.8}
    assert api.settings()["score_ok"] == 0.9

    # Un candidato con puntaje entre 0.80 y 0.90: con los umbrales de fábrica es OK, con 0.90 queda dudoso
    yt.catalog = [S("Take On Me", "a-ha", dur=262, id="lejos")]           # misma canción, 37 s de diferencia
    def status():
        r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], True)["job"])
        return next((e["status"], e["score"]) for e in r["events"] if e["type"] == "track" and e["n"] == 1)
    api.save_thresholds(0.75, 0.6)
    st, sc = status()
    assert 0.8 <= sc < 0.9 and st == "ok"
    api.save_thresholds(0.9, 0.8)
    assert status() == ("dudoso_omitido", sc)                              # "?": queda para decidir al final


def test_thresholds_edited_by_hand_out_of_range_fall_back(app):
    api, *_ = app
    api._prefs_path.write_text('{"score_ok": 0.3, "score_min": 0.1}', encoding="utf-8")
    s = api.settings()
    assert (s["score_ok"], s["score_min"]) == (0.75, 0.6)


def test_licenses_listed(app):
    api, *_ = app
    names = {l["name"]: l["license"] for l in api.info()["licenses"]}
    assert names == {"tidalapi": "LGPL-3.0", "ytmusicapi": "MIT", "pywebview": "BSD-3-Clause"}


def test_history_tiles_say_which_song_each_one_is(app):
    api, tidal, yt = app
    wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst", "name": "x"}], False)["job"])
    [op] = api.history()
    assert [list(x) for x in op["tile_songs"]] == [["Take On Me", "a-ha"], ["Hallelujah", "Jeff Buckley"]]
    assert op["tiles_total"] == 2


# ---------------------------------------------------------------- "+ Agregar igual" para los "?"

@pytest.fixture
def doubtful_app(app):
    api, tidal, yt = app
    tidal.add_playlist("live", "En vivo", [S("Bohemian Rhapsody", "Queen", dur=354, id="q1"),
                                           S("Take On Me", "a-ha", dur=225, id="q2")])
    yt.catalog.append(S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="ylive"))
    return api, tidal, yt


def test_skipped_doubtful_can_be_added_later_and_only_once(doubtful_app):
    api, tidal, yt = doubtful_app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "live", [{"id": "dst", "name": "x"}], False)["job"])
    assert r["result"]["summary"]["skipped"] == 1
    assert [s.id for s in yt.songs("dst")] == ["y1"]                        # el "?" no se agregó solo
    key = r["result"]["transfer"]
    a = wait(api, api.add_doubtful(key, [1])["job"])
    assert a["state"] == "done" and a["result"]["added_ns"] == [1]
    assert [s.id for s in yt.songs("dst")] == ["y1", "ylive"]
    assert "error" in api.add_doubtful(key, [1])                            # ya no se ofrece de nuevo
    ops = api.history()
    assert len(ops) == 1 and (ops[0]["added"], ops[0]["doubtful"], ops[0]["skipped"]) == (2, 1, 0)
    assert a["result"]["summary"]["skipped"] == 0 and a["result"]["targets"][0]["added"] == 2


def test_add_later_does_not_duplicate_what_is_already_there(doubtful_app):
    api, tidal, yt = doubtful_app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "live", [{"id": "dst", "name": "x"}], False)["job"])
    yt.add("dst", [S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="ylive")])   # alguien lo agregó
    yt.calls.clear()
    a = wait(api, api.add_doubtful(r["result"]["transfer"], [1])["job"])
    assert a["result"]["added_ns"] == [] and a["result"]["already"] == 1 and yt.calls == []


def test_add_later_is_not_possible_after_a_preview(doubtful_app):
    api, *_ = doubtful_app
    r = wait(api, api.start_transfer("tidal", "ytmusic", "live", [{"id": "dst", "name": "x"}], True)["job"])
    assert "error" in api.add_doubtful(r["result"]["transfer"], [1])


def test_old_history_gets_the_new_column(tmp_path):
    import sqlite3
    db = tmp_path / "viejo.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE operations (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
        kind TEXT NOT NULL, source TEXT NOT NULL, source_name TEXT NOT NULL, target TEXT, target_name TEXT,
        target_url TEXT, added INTEGER NOT NULL DEFAULT 0, already INTEGER NOT NULL DEFAULT 0,
        doubtful INTEGER NOT NULL DEFAULT 0, not_found INTEGER NOT NULL DEFAULT 0, removed INTEGER NOT NULL DEFAULT 0);
        INSERT INTO operations (created_at, kind, source, source_name, added) VALUES ('2026-11-08T01:00:00', 'transfer', 'tidal', 'Anime', 21);""")
    con.commit(); con.close()
    [op] = History(db).list()
    assert (op.source_name, op.added, op.skipped) == ("Anime", 21, 0)


# ---------------------------------------------------------------- estabilidad

def test_when_the_second_destination_fails_the_first_stays_in_history(app):
    """Antes se perdía el registro de lo que sí se había pasado."""
    from app.connectors.base import ConnectorError
    api, tidal, yt = app
    yt.add_playlist("dst2", "Otra", [])
    real = yt.add

    def add(pid, songs):
        if pid == "dst2":
            raise ConnectorError("YouTube Music rechazó el pedido.")
        real(pid, songs)
    yt.add = add
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst"}, {"id": "dst2"}])["job"])
    assert r["state"] == "error" and "No se pudo completar «Otra»" in r["error"] and "rechazó" in r["error"]
    assert [op["target_name"] for op in api.history()] == ["Favoritas"]


def test_a_history_that_cannot_be_written_does_not_fail_the_transfer(app):
    import sqlite3
    api, tidal, yt = app

    def broken(*_):
        raise sqlite3.OperationalError("database is locked")
    api._history.record = broken
    r = wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst"}])["job"])
    assert r["state"] == "done"


def test_services_are_asked_at_the_same_time(app):
    """Cada consulta espera a la otra: si fueran de a una, se vencería la espera."""
    import threading
    from app.connectors.base import Status
    api, tidal, yt = app
    together = threading.Barrier(2, timeout=5)

    def status():
        together.wait()
        return Status.CONNECTED
    tidal.status = yt.status = status
    assert {s["key"]: s["status"] for s in api.status() if s["key"] != "spotify"} == {"tidal": "connected", "ytmusic": "connected"}


def test_old_results_are_forgotten_and_ids_never_repeat(app):
    from app import api as api_module
    api, tidal, yt = app
    keys = [wait(api, api.start_transfer("tidal", "ytmusic", "src", [{"id": "dst"}], True)["job"])["result"]["transfer"]
            for _ in range(api_module.KEEP_RESULTS + 2)]
    assert len(set(keys)) == len(keys) and list(api._transfers) == keys[-api_module.KEEP_RESULTS:]


def test_finished_jobs_are_forgotten():
    from app import jobs
    j = Jobs()
    ids = []
    for _ in range(jobs.KEEP_FINISHED + 5):
        ids.append(j.start("scan", lambda emit, cancel: 1))
        j.wait(ids[-1])
    assert len(j._jobs) <= jobs.KEEP_FINISHED + 1 and j.poll(ids[-1])["state"] == "done"


# ---------------------------------------------------------------- repetidos en todas las playlists

def more_playlists(yt):
    yt.add_playlist("otra", "Otra lista", [S("Dos", "X", id="d1"), S("Dos", "X", id="d2"), S("Tres", "X", id="t1")])
    yt.add_playlist("limpia", "Sin nada", [S("Uno", "X", id="u1"), S("Cuatro", "X", id="c4")])
    yt.add_playlist("rota", "Rota", [S("Uno", "X", id="u9"), S("Cinco", "X", id="c5")])
    real = yt.read

    def read(pid):
        if pid == "rota":
            raise ConnectorError("No se pudo leer esa playlist.")
        return real(pid)
    yt.read = read


def test_scan_all_playlists_one_by_one(app):
    """Una que no se puede leer no frena las demás; las de menos de 2 temas no se revisan."""
    api, tidal, yt = app
    more_playlists(yt)
    r = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])
    assert r["state"] == "done", r["error"]
    res = r["result"]
    assert [g["name"] for g in res["groups"]] == ["Clásicos", "Otra lista"]
    assert res["clean"] == ["Sin nada"] and [f["name"] for f in res["failed"]] == ["Rota"]
    assert res["checked"] == 4                                    # "Para correr" tiene 1 tema: no cuenta
    kinds = [e["type"] for e in r["events"]]
    assert kinds[0] == "scan_list" and kinds.count("scan_start") == 4 and kinds.count("scan_result") == 4


def test_remove_from_several_playlists_at_once(app):
    api, tidal, yt = app
    more_playlists(yt)
    res = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])["result"]
    choices = [{"scan": g["scan"], "selected": [0]} for g in res["groups"]]
    assert "error" in api.remove_many(choices, False)                         # sin el diálogo, nada
    r = wait(api, api.remove_many(choices, True)["job"])
    assert r["state"] == "done" and r["result"]["removed"] == 2 and r["result"]["names"] == ["Clásicos", "Otra lista"]
    assert [s.id for s in yt.songs("dups")] == ["v1", "v2", "v4"] and [s.id for s in yt.songs("otra")] == ["d1", "t1"]
    after = {g["name"]: len(g["duplicates"]) for g in r["result"]["groups"]}  # se revisaron de nuevo
    assert after == {"Clásicos": 1, "Otra lista": 0}
    assert [o["source_name"] for o in api.history()] == ["Otra lista", "Clásicos"]   # una fila por playlist


def test_several_playlists_stop_at_one_that_changed_and_say_what_was_done(app):
    api, tidal, yt = app
    more_playlists(yt)
    res = wait(api, api.scan_duplicates("ytmusic", "__all__")["job"])["result"]
    yt.add("otra", [S("Nueva", "Y", id="n1")])                                # cambió desde otro lado
    r = wait(api, api.remove_many([{"scan": g["scan"], "selected": [0]} for g in res["groups"]], True)["job"])
    assert r["state"] == "error" and "Clásicos" in r["error"] and "«Otra lista» cambió" in r["error"]
    assert len(yt.songs("dups")) == 3 and len(yt.songs("otra")) == 4         # la segunda no se tocó


def test_a_service_slow_to_answer_its_status_is_written_in_the_log(app, monkeypatch, caplog):
    """Al arrancar, un servicio tardó y no quedaba registrado cuál: ahora el log lo dice."""
    import logging
    from app.api import services
    api, tidal, yt = app
    monkeypatch.setattr(services, "SLOW_STATUS", -1)
    with caplog.at_level(logging.WARNING, logger="app.api.services"):
        api.status()
    assert any("YouTube Music tardó" in r.getMessage() for r in caplog.records)

