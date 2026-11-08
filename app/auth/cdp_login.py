"""Login con el navegador elegido, sin Playwright.

El navegador se lanza como un proceso común, con `--remote-debugging-port` y un perfil propio,
abierto directamente en la página de login. Las cookies se leen con `Storage.getCookies` por el
protocolo de DevTools. No se manipulan pestañas: Vivaldi no deja que un programa las cree.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from app.i18n import t as tr


class LoginError(Exception):
    """El inicio de sesión no se pudo completar (el mensaje se puede mostrar al usuario)."""


class CDP:
    """Cliente mínimo del protocolo de depuración de Chrome (DevTools), sin dependencias.
    Solo hace falta para pedirle las cookies al navegador y cerrarlo."""

    def __init__(self, ws_url: str, timeout: float = 15, sock: socket.socket | None = None):
        u = urlparse(ws_url)
        self.sock = sock or socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError(tr("err.browser_cut"))
            resp += chunk
        status = resp.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status:
            raise ConnectionError(tr("err.browser_refused", status=status))
        self.buf = resp.split(b"\r\n\r\n", 1)[1]
        self.msg_id = 0

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError(tr("err.browser_closed"))
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, data: bytes, opcode: int = 0x1) -> None:
        self.sock.sendall(encode_frame(data, opcode, mask=os.urandom(4)))

    def _recv_message(self) -> str:
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError(tr("err.browser_closed"))
            if opcode == 0x9:                      # ping -> pong
                self._send_frame(payload, 0xA)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b1 & 0x80:
                    return message.decode("utf-8", errors="replace")

    def send(self, method: str, params: dict | None = None) -> dict:
        self.msg_id += 1
        self._send_frame(json.dumps({"id": self.msg_id, "method": method, "params": params or {}}).encode())
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") == self.msg_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", msg["error"]))
                return msg.get("result", {})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def encode_frame(data: bytes, opcode: int = 0x1, mask: bytes | None = None) -> bytes:
    """Frame de WebSocket (FIN=1). Los clientes siempre enmascaran (RFC 6455)."""
    n = len(data)
    mask_bit = 0x80 if mask else 0
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(mask_bit | n)
    elif n < 65536:
        header += bytes([mask_bit | 126]) + struct.pack(">H", n)
    else:
        header += bytes([mask_bit | 127]) + struct.pack(">Q", n)
    if not mask:
        return bytes(header) + data
    return bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def devtools_info(port: int) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost: sin proxy
    with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
        return json.loads(r.read().decode())


def profile_dir_for(profiles_dir: Path, browser_name: str) -> Path:
    """Un perfil aparte por navegador: Chrome y sus derivados no dejan automatizar el perfil normal."""
    return profiles_dir / (re.sub(r"\W", "", browser_name.lower()) or "browser")


def cookies_for_domain(cookies: list[dict], domain: str) -> dict[str, str]:
    """{nombre: valor} de las cookies de `domain` y sus subdominios; las del dominio raíz ganan."""
    own = [c for c in cookies if c.get("domain", "").lstrip(".").endswith(domain)]
    own.sort(key=lambda c: c.get("domain") != f".{domain}")
    jar: dict[str, str] = {}
    for c in own:
        jar.setdefault(c["name"], c["value"])
    return jar


def login_and_read_cookies(exe: Path, browser_name: str, profiles_dir: Path, login_url: str,
                           domain: str, wanted: set[str], timeout: float = 600,
                           on_status: Callable[[str], None] | None = None) -> tuple[dict[str, str], str]:
    """Abre el navegador en `login_url` y espera hasta que aparezca alguna cookie de `wanted` en
    `domain`. Devuelve (cookies de ese dominio, user-agent). Cierra el navegador al terminar."""
    say = on_status or (lambda _msg: None)
    port = free_port()
    profile = profile_dir_for(profiles_dir, browser_name)
    profile.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen([str(exe), f"--remote-debugging-port={port}",
                                 f"--user-data-dir={profile.resolve()}", "--no-first-run",
                                 "--no-default-browser-check", "--new-window", login_url])
    except OSError as e:
        raise LoginError(tr("err.browser_open_failed", name=browser_name, detail=e)) from e

    cdp, info, last_error = None, {}, None
    deadline = time.time() + 40
    while time.time() < deadline and cdp is None:
        try:
            info = devtools_info(port)
            cdp = CDP(info["webSocketDebuggerUrl"])
        except Exception as e:
            last_error = e
            time.sleep(0.5)
    if cdp is None:
        try:
            proc.kill()
        except OSError:
            pass
        raise LoginError(tr("err.browser_no_answer", name=browser_name, detail=last_error))

    say(tr("msg.browser_opened", name=browser_name))

    def jar() -> dict[str, str]:
        return cookies_for_domain(cdp.send("Storage.getCookies").get("cookies", []), domain)

    try:
        deadline = time.time() + timeout
        cookies = jar()
        while not (wanted & cookies.keys()):
            if time.time() > deadline:
                raise LoginError(tr("err.login_timeout"))
            time.sleep(1.5)
            cookies = jar()
        time.sleep(3)              # deja terminar la redirección
        cookies = jar()
        user_agent = cdp.send("Browser.getVersion").get("userAgent") or info.get("User-Agent", "")
    except LoginError:
        raise
    except Exception as e:         # por ejemplo, si se cerró la ventana antes de terminar
        raise LoginError(tr("err.login_interrupted", detail=e)) from e
    finally:
        try:
            cdp.send("Browser.close")   # cierre prolijo: el perfil guarda la sesión para la próxima
        except Exception:
            pass
        cdp.close()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    return cookies, user_agent
