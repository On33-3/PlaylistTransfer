"""Arranca la ventana. Uso: `py -m app` (o doble clic en "PlaylistTransfer.bat")."""
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
    """Windows agrupa la ventana con el proceso que la abrió: con `py -m app`, la barra de tareas mostraba
    el ícono de Python. Con un id propio, la app aparece como ella misma (con su ícono)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PlaylistTransfer.App")
    except (AttributeError, OSError):    # no es Windows
        pass


def setup_logging() -> Path:
    """app.log en la carpeta de datos, con tope: hasta 1 MB y una copia anterior (app.log.1)."""
    from logging.handlers import RotatingFileHandler
    log_file = paths.data_dir() / "app.log"
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return log_file


def show_startup_error(e: BaseException) -> None:
    """Si la app no llega a abrir la ventana, el .exe (que no tiene consola) se cerraría sin decir nada:
    se registra el error y se avisa con un cuadro de Windows que dice dónde quedó el detalle."""
    from app.i18n import t as tr
    logging.getLogger(__name__).exception("No se pudo abrir la app")
    msg = tr("err.startup_failed", detail=f"{type(e).__name__}: {e}", log=paths.data_dir() / "app.log")
    if os.environ.get("PT_SMOKE") == "1":      # en la prueba automática nadie va a tocar "Aceptar"
        print(msg, file=sys.stderr, flush=True)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, TITLE, 0x10)    # 0x10: ícono de error
    except (AttributeError, OSError):    # no es Windows
        print(msg, file=sys.stderr)


# Lo que la app recién importa al conectar o al usar internet: la prueba de arranque lo importa a propósito,
# así un módulo que falte en el paquete se detecta al armarlo y no cuando alguien toca Conectar
LOADED_LATER = ("tidalapi", "ytmusicapi", "requests", "ssl", "sqlite3")


# Lo que la ventana (WebView2) guarda de las páginas que carga. pywebview sirve la UI con un servidor local
# (siempre http://127.0.0.1:42001) sin decir que no se guarde: la ventana usaba copias viejas de algunos
# archivos y nuevas de otros. Pasó al actualizar (2026-11-24): un index.html del 8/11 con el JavaScript
# nuevo, y Quitar repetidos no mostraba sus pasos y se colgaba al revisar.
PAGE_CACHES = ("Cache", "Code Cache")


def forget_page_cache(webview_dir: Path) -> None:
    """Antes de abrir la ventana: que cargue siempre los archivos de esta versión. Solo son copias de la UI
    de la app (no hay sesiones ahí: el login pasa en el navegador de la persona)."""
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

    smoke = os.environ.get("PT_SMOKE") == "1"   # prueba automática: abre, espera la UI y cierra
    ready = threading.Event()
    api._on_ready = ready.set                    # la UI llama a api.ui_ready() al terminar de cargar

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
    if getattr(api, "_wiped", False):          # Ajustes › Borrar todo: lo que estaba en uso, ahora que se cerró
        from app.storage.wipe import wipe
        logging.shutdown()
        wipe(paths.data_dir())
    if smoke:   # el .exe no tiene consola: el resultado sale como código de salida
        sys.exit(0 if ready.is_set() and not failed["modules"] else 1)


if __name__ == "__main__":
    main()
