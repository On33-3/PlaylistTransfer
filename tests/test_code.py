"""El código en sí: sin imports que sobran ni nombres sin definir (pyflakes), en la app, los tests y las herramientas."""
from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import ROOT


def test_pyflakes_finds_nothing():
    pytest.importorskip("pyflakes")
    r = subprocess.run([sys.executable, "-m", "pyflakes", "app", "tests", "tools", "packaging"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.stdout.strip() == "", r.stdout
