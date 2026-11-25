# PyInstaller: folder mode (onedir), no UPX and with version info.
# Single-file mode and UPX make antivirus tools flag more false positives (see SPEC).
# Don't use it directly: packaging/build.py calls it, after generating version_info.txt.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
# Modules PyInstaller drags in that the app doesn't use (checked against the real imports). Do NOT add
# cffi or pycparser: clr_loader uses them, and without them the window won't open (there is a test). Don't remove
# pywebview's win-arm64 and win-x86 DLLs either: it looks for them at startup even in a 64-bit .exe (seen).
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
    optimize=2,                 # no docstrings or asserts: the library code takes less space
    # tidalapi is LGPL-3.0: it ships as loose .py files (not inside the executable) so that
    # anyone can replace it with another version, as that license requires.
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
