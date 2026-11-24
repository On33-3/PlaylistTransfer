"""Privacidad: todo queda en esta PC y se puede borrar entero desde Ajustes (pedido del usuario, 2026-11-24:
"que el usuario, cuando termine de usarla, pueda dejar todo limpio y quedarse tranquilo")."""
from __future__ import annotations

import re
from pathlib import Path

from tests.ui_helpers import check, make_api, open_page, shot

ROOT = Path(__file__).resolve().parent.parent


def fill(data: Path) -> None:
    """Lo que la app guarda de verdad en %LOCALAPPDATA%\\PlaylistTransfer."""
    for name in ("browser.json", "tidal_session.json", "spotify_session.json", "spotify_app.json",
                 "preferencias.json", "app.log"):
        (data / name).write_text("{}", encoding="utf-8")
    (data / "busquedas").mkdir()
    (data / "busquedas" / "abc.json").write_text("{}", encoding="utf-8")
    (data / "browser-profiles" / "Vivaldi" / "Default").mkdir(parents=True)
    (data / "browser-profiles" / "Vivaldi" / "Default" / "Cookies").write_text("x", encoding="utf-8")


def test_erasing_needs_the_dialog_and_leaves_nothing(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    api._jobs.wait(api.start_transfer("tidal", "ytmusic", "src", [{"name": "x", "id": "dst"}])["job"])
    fill(tmp_path)
    assert (tmp_path / "historial.sqlite").exists()
    assert "error" in api.wipe_data() and "error" in api.wipe_data("yes")     # sin el diálogo, nada
    assert (tmp_path / "browser.json").exists()
    r = api.wipe_data(True)
    assert r == {"ok": True, "left": []}
    assert list(tmp_path.iterdir()) == []                                    # como la primera vez
    assert not tidal.connected and not yt.connected                         # también lo que estaba en memoria
    assert api.history() == []


def test_what_is_in_use_is_reported_not_hidden(tmp_path):
    """La carpeta de la ventana está en uso mientras la app sigue abierta: se dice, no se da por borrada."""
    api, *_ = make_api(tmp_path)
    fill(tmp_path)
    locked = tmp_path / "webview" / "EBWebView" / "lock"
    locked.parent.mkdir(parents=True)
    with open(locked, "w") as f:                         # Windows no deja borrar un archivo abierto
        f.write("x")
        r = api.wipe_data(True)
    assert r["left"] == ["webview"] and not (tmp_path / "browser.json").exists()
    assert api._wiped                                    # al cerrar la app se vuelve a intentar


def test_cannot_erase_while_something_is_running(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    tidal.login_hangs = True
    api._jobs.start("connect", lambda emit, cancel: tidal.connect(None, cancel))
    assert "error" in api.wipe_data(True)
    api._jobs.cancel(api._jobs.busy().id)


def test_the_app_only_talks_to_the_music_services():
    """Lo que dice Ajustes ("se conecta únicamente con Tidal, YouTube Music y Spotify") tiene que ser cierto:
    ninguna otra dirección en el código de la app (los links de licencias a GitHub solo se muestran)."""
    allowed = {"127.0.0.1", "tidal.com", "listen.tidal.com", "music.youtube.com", "accounts.google.com",
               "open.spotify.com", "www.spotify.com", "developer.spotify.com", "accounts.spotify.com",
               "api.spotify.com", "github.com"}
    hosts = set()
    for f in (ROOT / "app").rglob("*"):
        if f.suffix in {".py", ".js", ".html"}:
            hosts |= set(re.findall(r"https?://([a-zA-Z0-9.-]+)", f.read_text(encoding="utf-8")))
    assert hosts <= allowed, hosts - allowed
    settings = (ROOT / "app" / "api" / "settings.py").read_text(encoding="utf-8")
    assert all("github.com" not in line or '"url"' in line for line in settings.splitlines())


def test_settings_explain_privacy_and_erase_everything(browser, tmp_path):
    api, tidal, yt = make_api(tmp_path)
    fill(tmp_path)
    page, errors = open_page(browser, api)
    page.click("nav [data-go=ajustes]")
    assert "solo en esta PC" in page.inner_text("#ajustes") and "código es abierto" in page.inner_text("#ajustes")
    page.click("#wipe")
    assert page.is_visible("#wipe-confirm") and "Tus playlists no se tocan" in page.inner_text("#wipe-confirm")
    shot(page, "ajustes-borrar-todo")
    page.click("#wipe-no")                                       # cancelar: no pasa nada
    assert (tmp_path / "browser.json").exists()
    page.click("#wipe")
    page.click("#wipe-yes")
    page.wait_for_selector("text=se borraron todos los datos")
    assert not (tmp_path / "browser.json").exists()
    page.wait_for_function("!document.querySelector('#chips .chip.ok')")          # los servicios quedan desconectados
    page.click("nav [data-go=ajustes]")
    shot(page, "ajustes-privacidad")
    assert check(page, "ajustes") == [] and errors == []
