"""Arma el .exe y el zip para publicar. Lo mismo en tu compu y en GitHub Actions.

Uso (desde la raíz del repo):  py packaging/build.py
Resultado: dist/PlaylistTransfer-<versión>-windows.zip y su .sha256

Pasos: ícono → datos de versión → PyInstaller (carpeta, sin UPX) → licencias de terceros →
LICENSE y LÉEME → zip → SHA-256. Nada de la carpeta de datos del usuario entra al paquete.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402

# Lo que va dentro del paquete (y sus dependencias): sus licencias se copian a THIRD-PARTY-NOTICES.txt
BUNDLED = ["tidalapi", "ytmusicapi", "pywebview"]
PERSONAL = re.compile(r"(browser\.json|oauth\.json|tidal_session|preferencias\.json|reporte_|historial|"
                      r"\.sqlite$|\.yt-login|browser-profiles|app\.log|\.env)", re.I)

README_USER = """PlaylistTransfer {version}
======================

Pasa playlists entre Tidal y YouTube Music, y quita temas repetidos.

Cómo usarlo
-----------
1. Descomprimí esta carpeta donde quieras (por ejemplo, en Documentos).
2. Abrí PlaylistTransfer.exe.
3. En Ajustes, conectá Tidal y YouTube Music.

No hace falta instalar Python. Necesita Windows 10 u 11 (usa WebView2, que ya viene en Windows).

Tus datos
---------
Tus sesiones, preferencias e historial se guardan solo en tu compu, en:
    %LOCALAPPDATA%\\PlaylistTransfer
Para borrar todo, cerrá la app y borrá esa carpeta.

Licencias
---------
PlaylistTransfer es software libre (licencia MIT, ver LICENSE.txt).
Usa librerías de terceros con sus propias licencias: ver THIRD-PARTY-NOTICES.txt.
tidalapi (LGPL-3.0) está en _internal\\tidalapi como archivos .py: podés reemplazarla por otra versión.

No está afiliado a Tidal, Google ni Spotify.
"""


def run(cmd: list[str]) -> None:
    print("›", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def version_info() -> None:
    nums = [int(x) for x in re.findall(r"\d+", __version__)[:3]] + [0, 0, 0]
    t = tuple(nums[:3] + [0])
    (PKG / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040a04b0', [
      StringStruct('CompanyName', 'PlaylistTransfer (open source)'),
      StringStruct('FileDescription', 'PlaylistTransfer: pasar playlists entre servicios de música'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'PlaylistTransfer'),
      StringStruct('LegalCopyright', 'Licencia MIT'),
      StringStruct('OriginalFilename', 'PlaylistTransfer.exe'),
      StringStruct('ProductName', 'PlaylistTransfer'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x040a, 1200])])
  ]
)
""", encoding="utf-8")


def dependencies(names: list[str]) -> list[md.Distribution]:
    """Los paquetes nombrados y todas sus dependencias (las que están instaladas)."""
    seen: dict[str, md.Distribution] = {}
    todo = list(names)
    while todo:
        name = re.split(r"[\s;<>=!~\[(]", todo.pop(), maxsplit=1)[0].lower().replace("_", "-")
        if not name or name in seen:
            continue
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        seen[name] = dist
        for req in dist.requires or []:
            if "extra ==" not in req:
                todo.append(req)
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def third_party_notices(out: Path) -> None:
    parts = ["Licencias de terceros incluidas en PlaylistTransfer\n" + "=" * 52 + "\n"]
    for d in dependencies(BUNDLED):
        meta = d.metadata
        lic = meta.get("License-Expression") or meta.get("License") or ""
        if not lic or len(lic) > 80:
            lic = next((c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")), lic[:80])
        texts = [f for f in (d.files or []) if re.search(r"(LICEN[CS]E|COPYING|NOTICE)", f.name, re.I)]
        parts.append(f"\n{'-' * 72}\n{meta['Name']} {d.version}  —  {lic or 'ver el proyecto'}\n"
                     f"{meta.get('Home-page') or next(iter(meta.get_all('Project-URL') or []), '')}\n{'-' * 72}\n")
        for f in texts:
            try:
                parts.append(Path(d.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip() + "\n")
            except OSError:
                pass
    out.write_text("\n".join(parts), encoding="utf-8")


def check_no_personal_data(folder: Path) -> None:
    leaked = [p for p in folder.rglob("*") if PERSONAL.search(p.name)]
    if leaked:
        raise SystemExit(f"Hay archivos personales en el paquete, no se publica: {leaked[:5]}")


def main() -> Path:
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    run([sys.executable, str(PKG / "make_icon.py")])
    version_info()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(DIST),
         "--workpath", str(BUILD), str(PKG / "PlaylistTransfer.spec")])

    app_dir = DIST / "PlaylistTransfer"
    shutil.copy(ROOT / "LICENSE", app_dir / "LICENSE.txt")
    third_party_notices(app_dir / "THIRD-PARTY-NOTICES.txt")
    (app_dir / "LÉEME.txt").write_text(README_USER.format(version=__version__), encoding="utf-8-sig")
    if not (app_dir / "_internal" / "tidalapi" / "session.py").exists():
        raise SystemExit("tidalapi no quedó como archivos .py sueltos (LGPL): revisá el .spec")
    check_no_personal_data(app_dir)

    zip_path = DIST / f"PlaylistTransfer-{__version__}-windows.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app_dir.rglob("*")):
            z.write(f, f.relative_to(DIST))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (DIST / (zip_path.name + ".sha256")).write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
    size = zip_path.stat().st_size / 1e6
    print(f"\nListo: {zip_path.name} ({size:.1f} MB)\nSHA-256: {digest}")
    return zip_path


if __name__ == "__main__":
    main()
