"""The README screenshots come from tools/demo_library.py: it has to keep showing every status the app has,
so a change can't quietly leave the screenshots with less to show."""
from __future__ import annotations

from tools.demo_library import build


def test_the_demo_transfer_shows_every_status(tmp_path):
    api, tidal, yt, spotify = build(tmp_path, "en")
    r = api._jobs.wait(api.start_transfer("tidal", "spotify", "road", [{"name": "x", "id": "road"}])["job"])
    s = r["result"]["summary"]
    assert r["state"] == "done" and s["total"] == 33
    assert s["ok"] > 20 and s["skipped"] == 3 and s["not_found"] == 2 and s["repeated"] == 1
    assert r["result"]["targets"][0]["already"] == 4


def test_the_demo_duplicates_have_copies_another_version_and_clean_playlists(tmp_path):
    api, tidal, yt, spotify = build(tmp_path, "es")
    res = api._jobs.wait(api.scan_duplicates("ytmusic", "__all__")["job"])["result"]
    kinds = [d["kind"] for g in res["groups"] for d in g["duplicates"]]
    assert len(res["groups"]) == 2 and kinds.count("misma") == 3 and kinds.count("otra_version") == 1
    assert res["clean"] == ["Para entrenar", "Clásicos de los 90"]
