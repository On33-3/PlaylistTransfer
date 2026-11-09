"""Login OAuth con PKCE para apps de escritorio: un servidor mínimo en 127.0.0.1 recibe el código.

Spotify (y otros) redirigen al navegador a `http://127.0.0.1:<puerto>/callback?code=…&state=…`.
Escuchamos solo en 127.0.0.1 (nunca en la red), en un puerto libre, y una sola respuesta.
Sin librerías externas.
"""
from __future__ import annotations

import base64
import hashlib
import html
import http.server
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from app.core.models import Cancelled
from app.i18n import t as tr

CALLBACK_PATH = "/callback"


def make_verifier() -> str:
    """El "code_verifier" de PKCE: 64 caracteres al azar (el estándar pide entre 43 y 128)."""
    return secrets.token_urlsafe(48)[:64]


def challenge(verifier: str) -> str:
    """El "code_challenge" S256: SHA-256 del verificador en base64url, sin "="."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class LoginFailed(Exception):
    """El servicio devolvió un error en la redirección (por ejemplo, el usuario tocó "Cancelar")."""


class _Server(http.server.HTTPServer):
    # En Windows, SO_REUSEADDR deja que dos programas escuchen el mismo puerto: con un puerto fijo,
    # la respuesta de Spotify podría llegarle a otro. Sin eso, si está ocupado, falla al abrirlo.
    allow_reuse_address = False


class CallbackServer:
    """Espera una sola redirección a /callback. Uso:

        with CallbackServer() as cb:
            abrir(f"...&redirect_uri={cb.redirect_uri}")
            code = cb.wait(state, cancel=evento, timeout=600)
    """

    def __init__(self, port: int = 0):
        self._result: dict[str, str] | None = None
        self._got = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                          # noqa: N802 (nombre de http.server)
                url = urlparse(self.path)
                if url.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                outer._result = {k: v[0] for k, v in parse_qs(url.query).items()}
                ok = "code" in outer._result
                body = (f"<!doctype html><meta charset=utf-8><title>PlaylistTransfer</title>"
                        f"<body style='font:16px system-ui;margin:3em;text-align:center'>"
                        f"<p>{html.escape(tr('msg.login_back_ok' if ok else 'msg.login_back_error'))}</p>").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer._got.set()

            def log_message(self, *args):              # sin imprimir en la consola
                pass

        self._server = _Server(("127.0.0.1", port), Handler)
        self._server.timeout = 0.5
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._serve, name="oauth-callback", daemon=True)
        self._stop = threading.Event()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{CALLBACK_PATH}"

    def _serve(self) -> None:
        while not self._stop.is_set() and not self._got.is_set():
            self._server.handle_request()

    def __enter__(self) -> "CallbackServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(2)
        self._server.server_close()

    def wait(self, state: str, cancel: threading.Event | None = None, timeout: float = 600) -> str:
        """El código de autorización. Comprueba `state` (que la respuesta sea la nuestra)."""
        deadline = time.monotonic() + timeout
        while not self._got.wait(0.25):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise LoginFailed(tr("err.login_timeout"))
        r = self._result or {}
        if r.get("state") != state:
            raise LoginFailed(tr("err.login_state_mismatch"))
        if "code" not in r:
            raise LoginFailed(tr("err.login_denied", detail=r.get("error", "?")))
        return r["code"]
