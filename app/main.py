"""Starts the window. Usage: `py -m app` (or double-click "Abrir PlaylistTransfer.bat")."""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from app.api import Api
from app.storage import paths

TITLE = "PlaylistTransfer"


def ui_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))   # PyInstaller (F4)
    return base / "app" / "ui"


def own_taskbar_icon() -> None:
    """Windows groups the window with the process that opened it: with `py -m app`, the taskbar showed
    Python's icon. With an id of its own, the app shows up as itself (with its icon)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PlaylistTransfer.App")
    except (AttributeError, OSError):    # not Windows
        pass


def setup_logging() -> Path:
    """app.log in the data folder, with a cap: up to 1 MB and one previous copy (app.log.1)."""
    from logging.handlers import RotatingFileHandler
    log_file = paths.data_dir() / "app.log"
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return log_file


def show_startup_error(e: BaseException) -> None:
    """If the app can't open the window, the .exe (which has no console) would close without a word:
    the error is logged and a Windows message box says where the details are."""
    from app.i18n import t as tr
    logging.getLogger(__name__).exception("No se pudo abrir la app")
    msg = tr("err.startup_failed", detail=f"{type(e).__name__}: {e}", log=paths.data_dir() / "app.log")
    if os.environ.get("PT_SMOKE") == "1":      # in the automated test nobody will click "OK"
        print(msg, file=sys.stderr, flush=True)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, TITLE, 0x10)    # 0x10: error icon
    except (AttributeError, OSError):    # not Windows
        print(msg, file=sys.stderr)


# What the app only imports when connecting or going online: the startup test imports it on purpose,
# so a module missing from the package is caught when building it, not when someone clicks Connect
LOADED_LATER = ("tidalapi", "ytmusicapi", "requests", "ssl", "sqlite3")


# What the window (WebView2) keeps from the pages it loads. pywebview serves the UI with a local server
# (always http://127.0.0.1:42001) without saying not to cache: the window used old copies of some
# files and new ones of others. It happened after an update (2026-11-24): an index.html from 11/8 with the new
# JavaScript, and Remove duplicates didn't show its steps and hung while checking.
PAGE_CACHES = ("Cache", "Code Cache")


def forget_page_cache(webview_dir: Path) -> None:
    """Before opening the window: make sure it always loads this version's files. These are only copies of the
    app's UI (there are no sessions there: sign-in happens in the person's browser)."""
    import shutil
    for name in PAGE_CACHES:
        shutil.rmtree(Path(webview_dir) / "EBWebView" / "Default" / name, ignore_errors=True)


def missing_modules() -> list[str]:
    import importlib
    missing = []
    for name in LOADED_LATER:
        try:
            importlib.import_module(name)
        except Exception:
            logging.getLogger(__name__).exception("Falta %s en el paquete", name)
            missing.append(name)
    return missing


def main() -> None:
    setup_logging()
    try:
        run()
    except Exception as e:
        show_startup_error(e)
        sys.exit(1)


def run() -> None:
    import webview

    own_taskbar_icon()
    api = Api.create()
    from app import i18n
    logging.getLogger(__name__).info("Arranca PlaylistTransfer; idioma: %s", i18n.get_language())
    window = webview.create_window(TITLE, url=str(ui_dir() / "index.html"), js_api=api,
                                   width=1120, height=780, min_size=(860, 620), background_color="#F3F1FA")

    def save_dialog(default_name: str):
        documents = Path.home() / "Documents"
        return window.create_file_dialog(webview.FileDialog.SAVE, save_filename=default_name,
                                         directory=str(documents if documents.is_dir() else Path.home()),
                                         file_types=(i18n.t("file.txt_type"),))

    api._save_dialog = save_dialog

    smoke = os.environ.get("PT_SMOKE") == "1"   # automated test: opens, waits for the UI and closes
    ready = threading.Event()
    api._on_ready = ready.set                    # the UI calls api.ui_ready() when it finishes loading

    failed = {"modules": []}

    def on_start():
        if not smoke:
            return
        failed["modules"] = missing_modules()
        ok = ready.wait(30)
        print("UI lista" if ok else "La UI no avisó que estaba lista", flush=True)
        logging.getLogger(__name__).info("Prueba de arranque: %s", "UI lista" if ok else "la UI no cargó")
        window.destroy()

    forget_page_cache(paths.data_dir() / "webview")
    webview.start(on_start, debug=os.environ.get("PT_DEBUG") == "1", private_mode=False,
                  storage_path=str(paths.data_dir() / "webview"), icon=str(ui_dir() / "icon.ico"))
    if getattr(api, "_wiped", False):          # Settings › Erase everything: what was in use, now that it closed
        from app.storage.wipe import wipe
        logging.shutdown()
        wipe(paths.data_dir())
    if smoke:   # the .exe has no console: the result comes out as the exit code
        sys.exit(0 if ready.is_set() and not failed["modules"] else 1)


if __name__ == "__main__":
    main()
