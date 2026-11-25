"""The code itself: no unused imports or undefined names (pyflakes), in the app, the tests and the tools."""
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
