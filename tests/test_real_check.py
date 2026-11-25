"""The real-account check (tools/real_check.py) never writes: this proves it with the fake services."""
from __future__ import annotations

from tests.fakes import S
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
                raise AssertionError(f"{c.key}.{name} is not blocked")
    assert yt.calls == [] and tidal.calls == [] and len(yt.songs("dups")) == 4
    assert {"api.remove_many", "api.wipe_data", "api.disconnect", "api.start_transfer (for real)"} <= set(attempts)
    assert all(hasattr(api, n) for n in BLOCKED_API)          # if one gets renamed, this test says so


def test_removing_is_only_possible_in_the_test_playlist(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    yt.add_playlist("other", "Mía", [S("Uno", "X", id="o1"), S("Uno", "X", id="o2")])
    sandbox: set[str] = set()
    attempts = guard(api, sandbox)
    scans = {g["name"]: g["scan"] for g in api._jobs.wait(api.scan_duplicates("ytmusic", "__all__")["job"])["result"]["groups"]}
    assert "error" in api.remove_many([{"scan": scans["Clásicos"], "selected": [0]}], True)   # without a test playlist
    sandbox.add("dups")
    r = api._jobs.wait(api.remove_many([{"scan": scans["Clásicos"], "selected": [0]}], True)["job"])
    assert r["state"] == "done" and len(yt.songs("dups")) == 3
    r = api._jobs.wait(api.remove_many([{"scan": scans["Mía"], "selected": [0]}], True)["job"])
    assert r["state"] == "error" and len(yt.songs("other")) == 2 and "ytmusic.remove" in attempts


def test_only_the_test_songs_can_be_liked_and_unliked(tmp_path):
    from app.connectors.base import LIKED
    from app.core.models import Duplicate
    api, tidal, yt = make_api(tmp_path)
    yt.add_playlist(LIKED, "Me gusta", [S("Mía", "X", id="mine")])
    test, other = S("Prueba", "X", id="t1"), S("Otra", "X", id="o1")
    liked: set[str] = {"t1"}
    attempts = guard(api, set(), liked)
    yt.add(LIKED, [test])                                                   # the test one, yes
    for bad in ([other], [test, other]):                                    # any other, never
        try:
            yt.add(LIKED, bad)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"liked {bad}")
    try:
        yt.remove(LIKED, [Duplicate(0, S("Mía", "X", id="mine"), test)], confirmed=True)   # your liked songs, never
    except RuntimeError:
        pass
    yt.remove(LIKED, [Duplicate(1, test, test)], confirmed=True)
    assert [s.id for s in yt.songs(LIKED)] == ["mine"] and attempts == ["ytmusic.add", "ytmusic.add", "ytmusic.remove"]


def test_a_dry_run_still_works_and_changes_nothing(tmp_path):
    api, tidal, yt = make_api(tmp_path)
    attempts = guard(api)
    r = api._jobs.wait(api.start_transfer("tidal", "ytmusic", "src", [{"name": "Nueva", "id": None}], True)["job"])
    assert r["state"] == "done" and r["result"]["dry_run"] and yt.calls == [] and attempts == []
