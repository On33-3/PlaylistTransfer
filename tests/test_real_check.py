"""La prueba con cuentas reales (tools/real_check.py) nunca escribe: lo comprueba con los servicios falsos."""
from __future__ import annotations

from tests.ui_helpers import make_api
from tools.real_check import BLOCKED_API, WRITES, guard


def test_nothing_that_writes_reaches_a_service(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    attempts = guard(api)
    scan = api._jobs.wait(api.scan_duplicates("ytmusic", "dups")["job"])["result"]["groups"][0]
    assert "error" in api.remove_many([{"scan": scan["scan"], "selected": [0]}], True)
    assert "error" in api.wipe_data(True) and "error" in api.disconnect("ytmusic")
    assert "error" in api.start_transfer("tidal", "ytmusic", "src", [{"name": "x", "id": "dst"}])
    for c in (tidal, yt):
        for name in WRITES:
            try:
                getattr(c, name)("dst", [])
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{c.key}.{name} no está bloqueado")
    assert yt.calls == [] and tidal.calls == [] and len(yt.songs("dups")) == 4
    assert {"api.remove_many", "api.wipe_data", "api.disconnect", "api.start_transfer (de verdad)"} <= set(attempts)
    assert all(hasattr(api, n) for n in BLOCKED_API)          # si se renombra uno, este test lo dice


def test_a_dry_run_still_works_and_changes_nothing(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    attempts = guard(api)
    r = api._jobs.wait(api.start_transfer("tidal", "ytmusic", "src", [{"name": "Nueva", "id": None}], True)["job"])
    assert r["state"] == "done" and r["result"]["dry_run"] and yt.calls == [] and attempts == []
