"""Load check: big libraries through the real engine, API and UI, with fake services (no account is touched:
the real services are never load-tested, see SEARCH_PAUSE). Prints how long each step takes and the peak
memory, and fails if a step goes over its budget, which is generous on purpose: it catches a slowdown
of several times (like the 11 minutes that 5000 songs once took), not a slower PC.

    py tools/load_check.py            # times, against the budgets (a few minutes)
    py tools/load_check.py --memory   # peak memory of each step (slower; times not judged)
"""
from __future__ import annotations

import itertools
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.matching import find_duplicates, norm  # noqa: E402
from tests.fakes import FakeService, S, build_api  # noqa: E402

SONGS = 5000              # a big playlist (a user moved ~1600; liked songs of 2000 were reported)
DEDUPE = 10000            # the biggest playlist YouTube Music allows is 5000; liked songs can be more
OPERATIONS = 200          # history: a heavy user after a long time
UI_SONGS = 2000

ADJ = "red blue golden silent electric broken wild lonely sweet bitter frozen burning hidden lost velvet crystal " \
      "paper neon midnight summer winter northern southern secret simple endless fading rising falling hollow " \
      "quiet gentle restless crimson silver distant tender savage brave".split()
NOUN = "river heart city night road dream fire ocean garden mirror shadow letter window echo storm highway " \
       "morning island train mountain desert harbor forest signal planet engine circus castle bridge station " \
       "lantern compass meadow canyon thunder anchor feather horizon".split()


def library(n: int, offset: int = 0) -> list:
    """`n` different songs: two-word titles, a few versions per title, 300 artists."""
    titles = (f"{a} {b}".title() for a, b in itertools.product(ADJ, NOUN))
    out = []
    for i, (title, version) in enumerate(itertools.product(titles, ["", " Part II", " Reprise", " Again", " Interlude", " Revisited", " Demo", " Outro"])):
        if i < offset:
            continue
        if len(out) == n:
            break
        k = i
        out.append(S(title + version, f"Artist {k % 300:03d}", dur=150 + k % 200, id=f"s{k}", album=f"Album {k % 500}"))
    assert len(out) == n, "not enough distinct titles"
    return out


class IndexedService(FakeService):
    """A fake whose search answers like a real one: up to 10 results, found through an index (the fakes in
    tests/ look at the whole catalog, which would measure the fake instead of the app)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._index: dict[str, list] = {}
        for s in self.catalog:
            self._index.setdefault(norm(s.title), []).append(s)

    def search(self, song):
        return self._index.get(norm(song.title), [])[:10]


class Budget:
    """Times each step. With --memory it also measures the peak of Python memory (tracemalloc), which makes
    everything several times slower: then the times are not compared with the budgets."""

    def __init__(self, memory: bool):
        self.failed, self.memory = [], memory

    def step(self, name: str, seconds_max: float, fn):
        if self.memory:
            tracemalloc.start()
        t = time.perf_counter()
        detail = fn()
        took = time.perf_counter() - t
        peak = ""
        if self.memory:
            peak = f"  peak {tracemalloc.get_traced_memory()[1] / 2**20:6.1f} MB"
            tracemalloc.stop()
        ok = self.memory or took <= seconds_max
        if not ok:
            self.failed.append(name)
        print(f"{'OK' if ok else 'SLOW':<6}{took:7.2f} s  (max {seconds_max:g} s){peak}  {name}"
              + (f"  - {detail}" if detail else ""), flush=True)


def wait(api, job_id: str, timeout: float = 600) -> dict:
    r = api._jobs.wait(job_id, timeout)
    if r["state"] != "done":
        raise AssertionError(f"the job ended as {r['state']}: {r.get('error')}")
    return r


def main() -> int:
    b = Budget(memory="--memory" in sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="pt-load-"))
    songs = library(SONGS)

    # ---- engine and API (the real ones; only the services are fake)
    src = FakeService("tidal", "Tidal")
    src.add_playlist("big", "Big playlist", songs)
    dst = IndexedService("ytmusic", "YouTube Music", catalog=[S(s.title, *s.artists, dur=s.duration + 2, id=f"y{s.id}")
                                                              for s in songs])
    dst.add_playlist("half", "Half there", [S(s.title, *s.artists, dur=s.duration, id=f"y{s.id}") for s in songs[::2]])
    for svc in (src, dst):
        svc.search_pause = 0
    api = build_api(tmp, {"tidal": src, "ytmusic": dst})

    def transfer_new():
        r = wait(api, api.start_transfer("tidal", "ytmusic", "big", [{"name": "New", "id": None}])["job"])
        t, repeated = r["result"]["targets"][0], r["result"]["summary"]["repeated"]
        assert t["added"] + repeated == SONGS, (t, repeated)
        # Made-up titles one letter apart by similar artists ("Secret River Reprise" / "Sweet River Reprise")
        # count as the same song: see "Titles one letter apart" in tests/test_scenarios.py
        return f"{t['added']} added to a new playlist, {repeated} taken as repeated (titles one letter apart)"
    b.step(f"Transfer {SONGS} songs to a new playlist", 120, transfer_new)

    def transfer_half():
        r = wait(api, api.start_transfer("tidal", "ytmusic", "big", [{"name": "Half there", "id": "half"}])["job"])
        t = r["result"]["targets"][0]
        assert t["added"] + t["already"] + t["versions"] + r["result"]["summary"]["repeated"] >= SONGS - 60, t
        return f"{t['added']} added, {t['already'] + t['versions']} already there"
    b.step(f"Transfer {SONGS} songs into a playlist that has half of them", 120, transfer_half)

    def transfer_resumed():
        # The same transfer again: everything was searched already (saved progress is deleted when it ends
        # well, so this searches again; what it measures is that a second big run is as fast as the first)
        r = wait(api, api.start_transfer("tidal", "ytmusic", "big", [{"name": "Half there", "id": "half"}],
                                         dry_run=True)["job"])
        return f"dry run, {r['result']['summary']['ok']} found"
    b.step(f"Dry run of {SONGS} songs against a playlist of {SONGS}", 120, transfer_resumed)

    # ---- duplicates
    dup_songs = library(DEDUPE // 10 * 9) + library(DEDUPE // 10)          # the first 10% appear twice
    b.step(f"Find duplicates among {DEDUPE} songs", 30,
           lambda: f"{len(find_duplicates(dup_songs))} found")

    big = FakeService("ytmusic", "YouTube Music")
    big.add_playlist("huge", "Huge", dup_songs)
    for k in range(40):
        big.add_playlist(f"p{k}", f"Playlist {k}", library(250, offset=k * 250) * 2)
    big.search_pause = 0
    api2 = build_api(tmp / "two", {"ytmusic": big})

    def scan_all():
        r = wait(api2, api2.scan_duplicates("ytmusic", "__all__")["job"])
        found = sum(len(g["duplicates"]) for g in r["result"]["groups"])
        return f"41 playlists, {found} duplicates"
    b.step("Check every playlist for duplicates (41 playlists, 30000 songs)", 60, scan_all)

    def remove_all():
        r = wait(api2, api2.scan_duplicates("ytmusic", "__all__")["job"])
        choices = [{"scan": g["scan"], "selected": [d["i"] for d in g["duplicates"]]} for g in r["result"]["groups"]]
        r = wait(api2, api2.remove_many(choices, confirmed=True)["job"])
        left = sum(len(g["duplicates"]) for g in r["result"]["groups"])
        assert left == 0, f"{left} duplicates left"
        return f"{r['result']['removed']} removed, the check after shows none"
    b.step("Remove every duplicate found, in 41 playlists", 60, remove_all)

    # ---- history
    def fill_history():
        from app.storage.history import Operation, TrackRecord
        h = api._history
        for k in range(OPERATIONS):
            h.record(Operation(kind="transfer", source="tidal", source_name=f"Playlist {k}", target="ytmusic",
                               target_name=f"Copy {k}", added=SONGS),
                     [TrackRecord(n, "ok", s.title, s.artists[0], s.title, s.artists[0], 0.97)
                      for n, s in enumerate(songs, 1)])
        return f"{OPERATIONS} operations of {SONGS} songs"
    b.step(f"Record {OPERATIONS} operations of {SONGS} songs in the history", 120, fill_history)
    size = (tmp / "historial.sqlite").stat().st_size / 2**20
    print(f"      history file: {size:.0f} MB for {OPERATIONS * SONGS} songs")
    b.step("Open History (every operation, with its squares)", 5, lambda: f"{len(api.history())} rows")
    b.step("Open the detail of one operation", 2, lambda: f"{len(api.history_detail(api.history(limit=1)[0]['id']))} songs")
    b.step("Download the history as text", 60, lambda: str(api.export_history().get("error") or "written"))

    # ---- the window, with big data (headless Edge, like the UI tests)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP  the window: Playwright is not installed (requirements-dev.txt)")
    else:
        from tests.ui_helpers import BRIDGE, UI
        ui_src = FakeService("tidal", "Tidal")
        ui_src.add_playlist("big", "Big playlist", songs[:UI_SONGS])
        ui_dst = IndexedService("ytmusic", "YouTube Music", catalog=[S(s.title, *s.artists, dur=s.duration, id=f"y{s.id}")
                                                                     for s in songs[:UI_SONGS]])
        ui_dst.add_playlist("dst", "Target", [])
        ui_dst.add_playlist("dups", "Many duplicates", library(1000) * 2)
        for svc in (ui_src, ui_dst):
            svc.search_pause = 0
        ui_api = build_api(tmp / "ui", {"tidal": ui_src, "ytmusic": ui_dst})
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1120, "height": 780})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.expose_function("__pt_call", lambda m, a: json.dumps(getattr(ui_api, m)(*json.loads(a))))
            page.add_init_script(BRIDGE)
            page.goto(UI)
            page.wait_for_selector("#chips .chip:not(.checking)")

            def ui_transfer():
                page.click("nav [data-go=transferir]")
                page.check("input[name=src][value=big]")
                page.check("input[name=dst][value=dst]")
                page.click("#go")
                page.wait_for_selector("#done.show", timeout=300000)
                return f"{page.locator('#mosaic i').count()} squares, {page.locator('#tracks li').count()} rows"
            b.step(f"Window: transfer {UI_SONGS} songs, live", 120, ui_transfer)

            def ui_dedupe():
                page.click("nav [data-go=repetidos]")
                page.click("#dup-svc [data-svc=ytmusic]")
                page.wait_for_selector("#dup-playlist option[value=dups]", state="attached")
                page.select_option("#dup-playlist", "dups")
                page.click("#dup-scan")
                page.wait_for_selector(".dup-group", timeout=120000)
                page.click("#remove")
                page.wait_for_selector("#confirm[open]")
                page.keyboard.press("Escape")
                return f"{page.locator('.dups li').count()} duplicates on screen"
            b.step("Window: 1000 duplicates on screen, and the confirmation", 30, ui_dedupe)

            for k in range(OPERATIONS):
                from app.storage.history import Operation
                ui_api._history.record(Operation(kind="dedupe", source="ytmusic", source_name=f"List {k}", removed=3), [])

            def ui_history():
                page.click("nav [data-go=historial]")
                page.wait_for_function(f"document.querySelectorAll('#hist li[data-op]').length > {OPERATIONS}")
                page.locator("#hist li[data-op] .row").first.click()
                loading = page.evaluate("t('common.loading')")
                page.wait_for_function("(l) => { const d = document.querySelector('#hist li[data-op] .detail');"
                                       " return d && !d.textContent.includes(l); }", arg=loading, timeout=30000)
                return f"{page.locator('#hist li[data-op]').count()} rows; the first one opened"
            b.step(f"Window: History with {OPERATIONS + 1} operations", 15, ui_history)
            browser.close()
            if errors:
                print(f"JavaScript errors: {errors}")
                b.failed.append("JavaScript errors")

    print()
    if b.failed:
        print(f"{len(b.failed)} over budget or failed: {', '.join(b.failed)}")
        return 1
    print("OK: every step within its budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
