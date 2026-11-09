"""Arranque: si algo falla antes de abrir la ventana, el .exe (sin consola) avisa en vez de cerrarse
callado, y el log tiene tope."""
from __future__ import annotations

import ctypes
import logging
from types import SimpleNamespace as NS

import pytest

from app import main


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "PlaylistTransfer"


def test_a_startup_failure_is_shown_and_logged(data_dir, monkeypatch):
    shown = []
    monkeypatch.setattr(ctypes, "windll", NS(user32=NS(MessageBoxW=lambda h, text, title, flags: shown.append((text, title)))),
                        raising=False)

    def broken():
        raise RuntimeError("falta WebView2")
    monkeypatch.setattr(main, "run", broken)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        with pytest.raises(SystemExit) as ex:
            main.main()
        assert ex.value.code == 1
        [(text, title)] = shown
        assert title == "PlaylistTransfer"
        assert "falta WebView2" in text and str(data_dir / "app.log") in text
        for h in root.handlers:
            h.flush()
        assert "falta WebView2" in (data_dir / "app.log").read_text(encoding="utf-8")
    finally:
        for h in root.handlers[:]:
            if h not in before:
                h.close()
                root.removeHandler(h)


def test_the_log_has_a_size_limit(data_dir):
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        main.setup_logging()
        new = [h for h in root.handlers if h not in before]
        assert len(new) == 1 and isinstance(new[0], RotatingFileHandler)
        assert new[0].maxBytes == 1_000_000 and new[0].backupCount == 1
    finally:
        for h in root.handlers[:]:
            if h not in before:
                h.close()
                root.removeHandler(h)


def test_the_smoke_test_notices_a_module_missing_from_the_package(monkeypatch):
    """La ventana abre aunque falte tidalapi: recién fallaría al tocar Conectar."""
    import importlib
    real = importlib.import_module

    def fake(name):
        if name == "tidalapi":
            raise ModuleNotFoundError(name)
        return real(name)
    monkeypatch.setattr(importlib, "import_module", fake)
    assert main.missing_modules() == ["tidalapi"]
