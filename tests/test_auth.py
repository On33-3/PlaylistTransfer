"""Navegadores, cliente CDP (contra un socket local) y cookies de Firefox."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import struct
from pathlib import Path

from app.auth.browsers import Browser, exe_from_command, kind_of, sort_browsers, suggested_browser
from app.auth.cdp_login import CDP, cookies_for_domain, encode_frame, profile_dir_for
from app.auth import firefox
from app.storage import paths
from tests.conftest import ROOT


# ---------------------------------------------------------------- navegadores

def test_exe_from_command():
    assert exe_from_command(r'"C:\Program Files\Vivaldi\vivaldi.exe" --single-argument %1') == \
        Path(r"C:\Program Files\Vivaldi\vivaldi.exe")
    assert exe_from_command(r"C:\Edge\msedge.exe %1") == Path(r"C:\Edge\msedge.exe")
    assert kind_of(r"C:\Program Files\BraveSoftware\brave.exe") == "brave"


def test_the_suggestion_is_each_persons_default_browser():
    """Se propone el predeterminado de Windows de cada persona, sea cual sea (ninguno queda afuera)."""
    chrome = Browser("Google Chrome", "chrome", Path(r"C:\chrome.exe"))
    vivaldi = Browser("Vivaldi", "vivaldi", Path(r"C:\vivaldi.exe"))
    brave = Browser("Brave", "brave", Path(r"C:\brave.exe"))
    edge = Browser("Microsoft Edge", "msedge", Path(r"C:\msedge.exe"))
    ordered = sort_browsers([brave, vivaldi, chrome], Path(r"c:\VIVALDI.exe"))
    assert [b.name for b in ordered] == ["Vivaldi", "Brave", "Google Chrome"]
    assert suggested_browser(ordered) is vivaldi
    assert suggested_browser(sort_browsers([edge, chrome, brave], Path(r"C:\chrome.exe"))) is chrome
    assert suggested_browser([chrome]) is chrome
    # Predeterminado desconocido (uno que la app no reconoce): el primero instalado, nunca nada
    assert suggested_browser(sort_browsers([edge, brave], Path(r"C:\raro.exe"))) is brave
    assert suggested_browser([]) is None


def test_profile_dir_per_browser(tmp_path):
    assert profile_dir_for(tmp_path, "Google Chrome") == tmp_path / "googlechrome"


# ---------------------------------------------------------------- CDP

def _server_frame(obj: dict, opcode: int = 0x1) -> bytes:
    return encode_frame(json.dumps(obj).encode(), opcode)          # el servidor no enmascara


def _read_client_frame(sock: socket.socket) -> tuple[int, bytes]:
    b1, b2 = sock.recv(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", sock.recv(2))[0]
    assert b2 & 0x80, "el cliente tiene que enmascarar"
    mask = sock.recv(4)
    data = b""
    while len(data) < n:
        data += sock.recv(n - len(data))
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def test_cdp_get_cookies_answers_ping_and_ignores_events():
    client, server = socket.socketpair()
    try:
        server.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
        server.sendall(encode_frame(b"hola", 0x9))                                   # ping
        server.sendall(_server_frame({"method": "Target.targetCreated", "params": {}}))  # evento suelto
        big = [{"name": f"c{i}", "value": "x" * 50, "domain": ".youtube.com"} for i in range(30)]
        server.sendall(_server_frame({"id": 1, "result": {"cookies": big}}))     # > 125 bytes
        cdp = CDP("ws://127.0.0.1:9222/devtools/browser/abc", sock=client)
        result = cdp.send("Storage.getCookies")
        assert len(result["cookies"]) == 30

        handshake = b""
        while b"\r\n\r\n" not in handshake:
            handshake += server.recv(1)
        assert handshake.startswith(b"GET /devtools/browser/abc HTTP/1.1")
        op, data = _read_client_frame(server)
        assert op == 0x1 and json.loads(data) == {"id": 1, "method": "Storage.getCookies", "params": {}}
        op, data = _read_client_frame(server)
        assert (op, data) == (0xA, b"hola")                                          # pong
    finally:
        client.close()
        server.close()


def test_cookies_for_domain_prefers_root_domain():
    cookies = [{"name": "SAPISID", "value": "sub", "domain": "music.youtube.com"},
               {"name": "SAPISID", "value": "root", "domain": ".youtube.com"},
               {"name": "SID", "value": "g", "domain": ".google.com"}]
    assert cookies_for_domain(cookies, "youtube.com") == {"SAPISID": "root"}


# ---------------------------------------------------------------- Firefox

def test_firefox_reads_profile_cookies(tmp_path):
    db = tmp_path / "cookies.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE moz_cookies (name, value, host, originAttributes, expiry)")
    con.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?,?)", [
        ("SAPISID", "container", ".youtube.com", "^userContextId=1", 1),
        ("SAPISID", "normal", ".youtube.com", "", 2),
        ("NID", "x", ".google.com", "", 1),
    ])
    con.commit()
    con.close()
    assert firefox.read_cookies("youtube.com", {"SAPISID"}, dbs=[db]) == {"SAPISID": "normal"}
    assert firefox.read_cookies("youtube.com", {"__Secure-3PAPISID"}, dbs=[db]) == {}


# ---------------------------------------------------------------- datos y script de consola

def test_data_dir_is_outside_the_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "PlaylistTransfer"
    assert paths.yt_browser_file().parent == tmp_path / "PlaylistTransfer"
    assert ROOT not in paths.tidal_session_file().parents


def test_no_shebang_in_python_files():
    """El launcher `py` respeta el shebang y abre otro Python sin las librerías (ver CLAUDE.md)."""
    for f in [*(ROOT / "app").rglob("*.py"), *(ROOT / "tools").glob("*.py"), *(ROOT / "packaging").glob("*.py")]:
        assert not f.read_text(encoding="utf-8").startswith("#!"), f
