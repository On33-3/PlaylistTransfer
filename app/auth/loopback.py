"""OAuth sign-in with PKCE for desktop apps: a minimal server on 127.0.0.1 receives the code.

Spotify (and others) redirect the browser to `http://127.0.0.1:<port>/callback?code=…&state=…`.
We listen only on 127.0.0.1 (never on the network), on a free port, for a single answer.
No external libraries.
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
    """The PKCE "code_verifier": 64 random characters (the standard asks for 43 to 128)."""
    return secrets.token_urlsafe(48)[:64]


def challenge(verifier: str) -> str:
    """The S256 "code_challenge": SHA-256 of the verifier in base64url, without "="."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class LoginFailed(Exception):
    """The service returned an error in the redirect (for example, the user clicked "Cancel")."""


class _Server(http.server.HTTPServer):
    # On Windows, SO_REUSEADDR lets two programs listen on the same port: with a fixed port,
    # Spotify's answer could reach another one. Without it, if the port is taken, opening it fails.
    allow_reuse_address = False


class CallbackServer:
    """Waits for a single redirect to /callback. Usage:

        with CallbackServer() as cb:
            open_browser(f"...&redirect_uri={cb.redirect_uri}")
            code = cb.wait(state, cancel=event, timeout=600)
    """

    def __init__(self, port: int = 0):
        self._result: dict[str, str] | None = None
        self._got = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                          # noqa: N802 (http.server's name)
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

            def log_message(self, *args):              # without printing to the console
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
        """The authorization code. Checks `state` (that the answer is ours)."""
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
