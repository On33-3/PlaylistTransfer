"""Empaquetado: lo que se verificó a mano en la F4, para que un cambio no lo rompa sin avisar."""
from __future__ import annotations

import importlib.util
import re
import struct

import pytest

from app import __version__
from tests.conftest import ROOT


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_icon_is_a_valid_ico_with_all_sizes():
    ico = load("make_icon").ico()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind) == (0, 1) and count == 7
    sizes = [ico[6 + 16 * i] or 256 for i in range(count)]
    assert sizes == [16, 24, 32, 48, 64, 128, 256]
    first_offset = struct.unpack("<I", ico[6 + 12:6 + 16])[0]
    assert ico[first_offset:first_offset + 8] == b"\x89PNG\r\n\x1a\n"


def test_the_window_and_the_exe_use_the_same_icon():
    """El ícono va con la UI: lo usan la ventana (también con `py -m app`) y el .exe, y está al día."""
    ico = ROOT / "app" / "ui" / "icon.ico"
    assert ico.read_bytes() == load("make_icon").ico()
    assert 'icon=str(ui_dir() / "icon.ico")' in (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'icon=str(ROOT / "app" / "ui" / "icon.ico")' in (ROOT / "packaging" / "PlaylistTransfer.spec").read_text(encoding="utf-8")


def test_version_info_matches_the_app(tmp_path, monkeypatch):
    build = load("build")
    monkeypatch.setattr(build, "PKG", tmp_path)
    build.version_info()
    text = (tmp_path / "version_info.txt").read_text(encoding="utf-8")
    assert f"'ProductVersion', '{__version__}'" in text and "PlaylistTransfer.exe" in text


def test_third_party_licenses_cover_what_is_bundled():
    names = {d.metadata["Name"].lower() for d in load("build").dependencies(["tidalapi", "ytmusicapi", "pywebview"])}
    assert {"tidalapi", "ytmusicapi", "pywebview", "requests"} <= names


def test_personal_files_stop_the_build(tmp_path):
    build = load("build")
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "browser.json").write_text("{}")
    with pytest.raises(SystemExit):
        build.check_no_personal_data(tmp_path)
    (tmp_path / "_internal" / "browser.json").unlink()
    (tmp_path / "PlaylistTransfer.exe").write_bytes(b"MZ")
    build.check_no_personal_data(tmp_path)          # sin datos personales: sigue


def test_spec_keeps_tidalapi_replaceable_and_no_upx():
    spec = (ROOT / "packaging" / "PlaylistTransfer.spec").read_text(encoding="utf-8")
    assert 'module_collection_mode={"tidalapi": "py"}' in spec        # LGPL-3.0
    assert "upx=False" in spec and "console=False" in spec and "COLLECT(" in spec   # carpeta, no un solo archivo


def test_workflow_only_uses_github_actions_and_checks_the_version():
    wf = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*([\w./-]+)@", wf)
    assert uses and all(u.startswith("actions/") for u in uses), uses
    assert "does not match version" in wf and "PT_SMOKE" in wf
    assert re.search(r"release:\n\s+if: startsWith\(github.ref, 'refs/tags/v'\)", wf)
    assert "permissions:\n  contents: read" in wf                     # por defecto, solo lectura


def test_build_requirements_are_pinned():
    for f in ("requirements.txt", "requirements-build.txt"):
        for line in (ROOT / f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-r")):
                assert "==" in line, f"{f}: {line} sin versión fija"


@pytest.mark.parametrize("readme, lang", [("README.md", "en"), ("README.es.md", "es")])
def test_readme_screenshots_exist_in_its_language(readme, lang):
    """README.md (inglés, lo que ve GitHub) y README.es.md: cada uno con sus capturas
    (tools/readme_screenshots.py) y un enlace al otro."""
    text = (ROOT / readme).read_text(encoding="utf-8")
    imgs = re.findall(r"\((docs/img/[\w/-]+\.png)\)", text)
    assert imgs
    for img in imgs:
        assert img.startswith(f"docs/img/{lang}/") and (ROOT / img).exists(), img
    other = "README.es.md" if readme == "README.md" else "README.md"
    assert f"]({other})" in text


def test_checksum_file_works_with_sha256sum(tmp_path):
    f = tmp_path / "PlaylistTransfer-9.9.9-windows.zip"
    f.write_bytes(b"contenido")
    digest = load("build").write_checksum(f)
    raw = (tmp_path / (f.name + ".sha256")).read_bytes()
    assert raw == f"{digest}  {f.name}\n".encode() and b"\r" not in raw


def test_files_in_the_zip_root_have_ascii_names():
    build = (ROOT / "packaging" / "build.py").read_text(encoding="utf-8")
    roots = re.findall(r'app_dir / "([^"]+)"', build)
    assert roots and all(name.isascii() for name in roots), roots


@pytest.mark.parametrize("name", ["README_USER", "README_USER_EN"])
def test_user_readmes_have_clean_windows_paths(name):
    text = getattr(load("build"), name).format(version="9.9.9")
    assert "\t" not in text and "\\\\" not in text                    # ni tabs (por un "\t" suelto) ni barras dobles
    assert r"%LOCALAPPDATA%\PlaylistTransfer" in text and r"_internal\tidalapi" in text
