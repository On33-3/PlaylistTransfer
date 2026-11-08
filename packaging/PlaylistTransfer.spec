# PyInstaller: modo carpeta (onedir), sin UPX y con datos de versión.
# El modo de un solo archivo y UPX hacen que los antivirus marquen más falsos positivos (ver SPEC).
# No usar directamente: lo llama packaging/build.py, que primero genera version_info.txt.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
PKG = ROOT / "packaging"

a = Analysis(
    [str(PKG / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "app" / "ui"), "app/ui")] + collect_data_files("ytmusicapi"),
    hiddenimports=["app.connectors.tidal", "app.connectors.ytmusic"],
    excludes=["tkinter", "pytest", "playwright", "PyInstaller"],
    # tidalapi es LGPL-3.0: va como archivos .py sueltos (no dentro del ejecutable) para que
    # cualquiera pueda reemplazarlo por otra versión, como pide esa licencia.
    module_collection_mode={"tidalapi": "py"},
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PlaylistTransfer",
    icon=str(PKG / "icon.ico"),
    version=str(PKG / "version_info.txt"),
    console=False,
    upx=False,
    debug=False,
    strip=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="PlaylistTransfer")
