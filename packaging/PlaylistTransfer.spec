# PyInstaller: modo carpeta (onedir), sin UPX y con datos de versión.
# El modo de un solo archivo y UPX hacen que los antivirus marquen más falsos positivos (ver SPEC).
# No usar directamente: lo llama packaging/build.py, que primero genera version_info.txt.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
# Módulos que PyInstaller arrastra y la app no usa (revisado contra las importaciones reales). NO agregar
# cffi ni pycparser: los usa clr_loader, sin eso la ventana no abre (hay test). Tampoco sacar las DLL de
# win-arm64 y win-x86 de pywebview: las busca al arrancar aunque el .exe sea de 64 bits (comprobado).
UNUSED = ["tkinter", "pytest", "playwright", "PyInstaller", "setuptools", "pkg_resources", "pip", "wheel", "packaging",
          "jinja2", "markupsafe", "pydoc", "pydoc_data", "_pyrepl", "pdb", "doctest", "unittest", "test"]
PKG = ROOT / "packaging"

a = Analysis(
    [str(PKG / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "app" / "ui"), "app/ui"), (str(ROOT / "app" / "locales"), "app/locales")]
          + collect_data_files("ytmusicapi"),
    hiddenimports=["app.connectors.tidal", "app.connectors.ytmusic"],
    excludes=UNUSED,
    optimize=2,                 # sin docstrings ni asserts: el código de la biblioteca ocupa menos
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
    icon=str(ROOT / "app" / "ui" / "icon.ico"),
    version=str(PKG / "version_info.txt"),
    console=False,
    upx=False,
    debug=False,
    strip=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="PlaylistTransfer")
