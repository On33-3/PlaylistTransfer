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


def setup_logging() -> Path:
    log_file = paths.data_dir() / "app.log"
    logging.basicConfig(filename=log_file, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


def main() -> None:
    import webview

    setup_logging()
    api = Api.create()
    window = webview.create_window(TITLE, url=str(ui_dir() / "index.html"), js_api=api,
                                   width=1120, height=780, min_size=(860, 620), background_color="#F3F1FA")

    def save_dialog(default_name: str):
        documents = Path.home() / "Documents"
        return window.create_file_dialog(webview.FileDialog.SAVE, save_filename=default_name,
                                         directory=str(documents if documents.is_dir() else Path.home()),
                                         file_types=("Archivo CSV para Excel (*.csv)",))

    api._save_dialog = save_dialog

    smoke = os.environ.get("PT_SMOKE") == "1"   # prueba automática: abre, espera la UI y cierra

    def on_start():
        if not smoke:
            return
        ready = threading.Event()
        api._on_ready = ready.set     # la UI llama a api.ui_ready() al terminar de cargar
        ok = ready.wait(30)
        print("UI lista" if ok else "La UI no avisó que estaba lista", flush=True)
        window.destroy()

    webview.start(on_start, debug=os.environ.get("PT_DEBUG") == "1", private_mode=False,
                  storage_path=str(paths.data_dir() / "webview"))


if __name__ == "__main__":
    main()
