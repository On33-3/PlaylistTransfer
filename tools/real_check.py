"""The pre-release check, with your real accounts and without changing anything in them.

    py tools/real_check.py                    (every connected service)
    py tools/real_check.py --service ytmusic  (the duplicates check only in that one)
    py tools/real_check.py --no-transfer      (without the transfer dry run, which runs searches)

Walks through the list that used to be done by hand, with the real interface (Edge, the same engine as
the window) and your sessions:
  0. the real window opens and closes by itself (PT_SMOKE)
  1. Home: it says everything runs locally, on your PC
  2. Transfer: a dry run with your smallest playlist; the route says service · playlist
  3. Remove duplicates, before checking: the three cards and «All playlists»
  4. Check all: the progress, Cancel halfway and then the whole check
  5. The result: other versions unticked, «Stays» when unticked, the bar always visible,
     «Untick all», and the screen aligned in the smallest window too
  6. The confirmation opens with what it would say, and is cancelled
  7. Removing for real: creates «PlaylistTransfer · test (deletes itself)» with three of your songs
     repeated on purpose (A, B, C, A, B), the screen finds the two duplicates, they're removed through
     the confirmation, A, B, C is left and the history records it. Then it deletes the playlist, even if
     something failed (and on start, it deletes one left by an interrupted run)
  8. Liked songs for real: likes two of your songs that weren't liked, like a transfer to liked songs
     does, checks they end up on top, unlikes them and checks your list is the same as before (if
     something fails halfway, it still unlikes them)
  9. Settings: «Privacy and data» reads correctly (it never touches «Erase everything»)

Outside that test playlist it never writes to your accounts: before the page opens, adding, creating,
removing, disconnecting, erasing everything and real transfers are blocked (`guard`), and at the end it
checks nobody tried. The three songs are copied; your playlists are only read.
This check's history, saved searches and preferences go to a temporary folder: your history doesn't
change. The screenshots contain your real data: they stay in that folder, never in the repo.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SMALL = 25              # songs: the transfer dry run uses a playlist of up to this size
SCAN_TIMEOUT = 20 * 60  # seconds per service to check every playlist
WRITES = ("add", "remove", "create")
BLOCKED_API = ("remove_many", "remove_duplicates", "wipe_data", "disconnect", "connect", "set_client_id",
               "add_doubtful", "add_alternative")


# ---------------------------------------------------------------- nothing gets written

def guard(api, sandbox: set[str] | None = None, liked: set[str] | None = None) -> list[str]:
    """Blocks everything that changes something in the accounts or the app. Returns the list of attempts
    (it has to stay empty): each one also fails, so it never reaches the service.
    The only exceptions:
      - removing from a test playlist (`sandbox`, the ids this script created): the UI can really
        remove there and nowhere else;
      - liking and unliking the test songs (`liked`, ids of songs that weren't liked)."""
    from app.connectors.base import LIKED
    attempts: list[str] = []
    sandbox = sandbox if sandbox is not None else set()
    liked = liked if liked is not None else set()

    def only_test_songs(args) -> bool:
        if len(args) < 2 or args[0] != LIKED or not args[1]:
            return False
        ids = [getattr(x, "id", None) or getattr(getattr(x, "song", None), "id", None) for x in args[1]]
        return all(i in liked for i in ids)

    def block_connector(c, name):
        real = getattr(c, name)

        def blocked(*args, **kwargs):
            if name == "remove" and args and args[0] in sandbox:
                return real(*args, **kwargs)
            if name in ("add", "remove") and only_test_songs(args):
                return real(*args, **kwargs)
            attempts.append(f"{c.key}.{name}")
            raise RuntimeError(f"real check: {c.key}.{name} is blocked")
        setattr(c, name, blocked)

    for c in api._connectors.values():
        for name in WRITES:
            block_connector(c, name)

    def block_api(name):
        real = getattr(api, name)

        def blocked(*args, **kwargs):
            # Removing only goes through once a test playlist exists: the connector still only allows removing from it
            if name in ("remove_many", "remove_duplicates") and sandbox:
                return real(*args, **kwargs)
            attempts.append(f"api.{name}")
            return {"error": f"real check: {name} is blocked"}
        setattr(api, name, blocked)

    for name in BLOCKED_API:
        block_api(name)

    real_transfer = api.start_transfer

    def only_dry(source, target, source_id, targets, dry_run=False):
        if dry_run is not True:
            attempts.append("api.start_transfer (for real)")
            return {"error": "real check: only the dry run is allowed"}
        return real_transfer(source, target, source_id, targets, True)
    api.start_transfer = only_dry
    return attempts


# ---------------------------------------------------------------- the test playlist

SANDBOX_NAME = "PlaylistTransfer · test (deletes itself)"
# Its name until 2026-11-25: one left over by an interrupted run is deleted too
SANDBOX_NAMES = (SANDBOX_NAME, "PlaylistTransfer · prueba (se borra sola)")
SANDBOX_TEXT = "Created by the PlaylistTransfer automated check; it deletes it when it finishes."
SETTLE = 30             # seconds: a service can take a while to show what was just created or deleted


def add_with_repeats(c, pid: str, songs) -> None:
    """Adds even if they're already there: the app never does (Tidal and YouTube Music skip them), the check does."""
    ids = [s.id for s in songs]
    if c.key == "tidal":
        c._editable(pid).add(ids, allow_duplicates=True)
    elif c.key == "ytmusic":
        c._ready().add_playlist_items(pid, ids, duplicates=True)
    else:                                       # Spotify accepts duplicates
        type(c).add(c, pid, songs)


def delete_playlist(c, pid: str) -> None:
    """Deletes the test playlist. This only exists here: the app can't delete playlists."""
    if c.key == "tidal":
        c._editable(pid).delete()
    elif c.key == "ytmusic":
        c._ready().delete_playlist(pid)
    else:                                       # on Spotify, deleting is unfollowing it (like its app does)
        c._call("DELETE", f"/playlists/{pid}/followers")


def leftovers(c) -> list[str]:
    """Test playlists left over from an interrupted run."""
    return [p.id for p in c.list_playlists() if p.name in SANDBOX_NAMES]


def three_songs(c):
    """Three different songs from your smallest playlist with at least three (only read)."""
    from app.connectors.base import LIKED
    from app.core.matching import same_song
    lists = sorted((p for p in c.list_playlists() if p.id != LIKED and p.name not in SANDBOX_NAMES and (p.count or 0) >= 3),
                   key=lambda p: p.count)
    for p in lists:
        picked = []
        for s in c.read(p.id)[1]:
            usable = s.id and (c.key != "spotify" or s.id.startswith("spotify:track:"))
            if usable and not any(same_song(s, x) for x in picked):
                picked.append(s)
            if len(picked) == 3:
                return p.name, picked
    return None, []


def two_unliked(c, liked_ids: set[str]):
    """Two different songs from your playlists that you haven't liked (only read).
    On YouTube Music, songs with an album: a liked video doesn't always show in liked songs."""
    from app.connectors.base import LIKED
    from app.core.matching import same_song
    lists = sorted((p for p in c.list_playlists() if p.id != LIKED and p.name not in SANDBOX_NAMES and (p.count or 0) >= 2),
                   key=lambda p: p.count)
    picked = []
    for p in lists:
        for s in c.read(p.id)[1]:
            usable = (s.id and s.id not in liked_ids and (c.key != "spotify" or s.id.startswith("spotify:track:"))
                      and (c.key != "ytmusic" or s.album))
            if usable and not any(same_song(s, x) for x in picked):
                picked.append(s)
            if len(picked) == 2:
                return picked
    return picked


def settle(check, what: str):
    """Waits for the service to show the change (YouTube Music can take a few seconds)."""
    until = time.monotonic() + SETTLE
    while not check():
        assert time.monotonic() < until, f"after {SETTLE} s, {what}"
        time.sleep(2)


def real_api(work: Path):
    """The API with your sessions, but with the history, searches and preferences in `work`."""
    from app.api import Api
    from app.connectors import registry
    from app.storage import paths
    from app.storage.history import History
    work.mkdir(parents=True, exist_ok=True)
    if paths.prefs_file().exists():
        shutil.copy(paths.prefs_file(), work / "preferencias.json")
    return Api(registry.build_connectors(), History(work / "historial.json"), work / "preferencias.json", work)


# ---------------------------------------------------------------- the walk-through

class Run:
    def __init__(self, out: Path):
        self.out, self.results, self.page = out, [], None     # page: for the screenshot of a failing step

    def step(self, name, fn, *args):
        print(f"· {name}…", flush=True)
        started = time.monotonic()
        try:
            detail = fn(*args)
            state = "SKIPPED" if isinstance(detail, Skip) else "OK"
        except AssertionError as e:
            state, detail = "FAILED", str(e) or "an expectation was not met"
        except Exception as e:                    # a timeout, a selector that doesn't show up…
            state, detail = "FAILED", f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
        took = time.monotonic() - started
        if state == "FAILED" and self.page is not None:      # what was on screen when it failed
            try:
                self.shot(self.page, "FAILED", full=True)
            except Exception:
                pass
        self.results.append((name, state, str(detail or ""), took))
        print(f"  {state} ({took:.0f} s){': ' + str(detail) if detail else ''}", flush=True)
        return state == "OK"

    def shot(self, page, name, full=False):
        page.screenshot(path=str(self.out / f"{len(self.results) + 1:02d}-{name}.png"), full_page=full)


class Skip(str):
    """A step that doesn't apply (for example, no connected service)."""


def smoke() -> str:
    env = dict(os.environ, PT_SMOKE="1")
    r = subprocess.run([sys.executable, "-m", "app"], cwd=ROOT, env=env, timeout=90, capture_output=True)
    assert r.returncode == 0, f"the window exited with code {r.returncode}: see app.log"
    return ""


def home(run, page, design):
    page.click("nav [data-go=inicio]")
    text = page.inner_text("#home-lede")
    tail = page.evaluate("t('home.lede', { services: '\\u0000' }).split('\\u0000').pop()")
    assert text.strip() and text.endswith(tail.strip()), f"the Home text is not the expected one: «{text}»"
    assert design(page, "inicio") == [], design(page, "inicio")
    run.shot(page, "home")
    return ""


def transfer(run, page, design, connected, names):
    if len(connected) < 2:
        return Skip("two connected services are needed")
    src, dst = connected[0], connected[1]
    page.click("nav [data-go=transferir]")
    page.select_option("#from", src)
    page.select_option("#to", dst)
    page.wait_for_selector("#src-list input[name=src]", timeout=60000)
    picks = page.evaluate("""(small) => [...document.querySelectorAll('#src-list input[name=src]')]
        .map(i => ({ value: i.value, n: Number(i.dataset.count), name: i.closest('li').querySelector('.name').textContent }))
        .filter(p => p.n > 0 && p.n <= small).sort((a, b) => a.n - b.n)""", SMALL)
    if not picks:
        return Skip(f"no {names[src]} playlist has between 1 and {SMALL} songs")
    p = picks[0]
    page.check(f"#src-list input[name=src][value='{p['value']}']")
    page.check("#dst-new")
    page.click("#dry")
    page.wait_for_selector("#done.show", timeout=10 * 60 * 1000)
    route, done = page.inner_text("#run-route"), page.inner_text("#done")
    assert names[src] in route and names[dst] in route and p["name"] in route, f"the route does not say from where to where: «{route}»"
    from_line = page.evaluate("([name, service]) => t('transfer.from_line', { name, service })", [p["name"], names[src]])
    assert from_line in done, f"the result does not say where from: «{done[:200]}»"
    assert page.is_hidden("#open-dest"), "a dry run offers to open the destination"
    assert design(page, "transferir") == [], design(page, "transferir")
    run.shot(page, "transfer-dry-run", full=True)
    return f"«{p['name']}» ({p['n']} songs) from {names[src]} to {names[dst]}: {page.inner_text('#done-title')}"


def to_dedupe(page, key) -> int:
    """Opens Remove duplicates on that service and waits for its playlists. Returns how many can be checked
    («All» only shows if there is more than one with 2 or more songs)."""
    page.click("nav [data-go=repetidos]")
    page.click(f"#dup-svc [data-svc={key}]")
    # While loading, the only option is «Loading…», with no value
    page.wait_for_function("document.querySelector('#dup-playlist option')"
                           " && !document.querySelector('#dup-playlist option:not([value])')", timeout=60000)
    return page.locator("#dup-playlist option[value]:not([value=''])").count() - page.locator("#dup-playlist option[value=__all__]").count()


def before_check(run, page, design, key):
    n = to_dedupe(page, key)
    if n == 0:
        return Skip("no playlist with 2 or more songs")
    want = "__all__" if n > 1 else page.locator("#dup-playlist option").first.get_attribute("value")
    assert page.input_value("#dup-playlist") == want, "the list does not start at «All playlists»"
    assert page.is_visible("#dup-how") and page.locator("#dup-how > div").count() == 3, "the three cards are not visible"
    assert design(page, "repetidos") == [], design(page, "repetidos")
    run.shot(page, f"duplicates-before-{key}")
    return "" if n > 1 else "only one playlist to check: no need for «All»"


def cancel_midway(run, page, key):
    to_dedupe(page, key)
    page.click("#dup-scan")
    page.wait_for_selector("#dup-progress:not([hidden])")
    page.wait_for_timeout(1500)
    run.shot(page, f"duplicates-checking-{key}")
    page.click("#dup-cancel")
    page.wait_for_function("document.querySelector('#dup-progress').hidden", timeout=120000)
    said = page.evaluate("t('dedupe.cancelled')")
    assert said in page.inner_text("#repetidos"), "cancelling does not say nothing was removed"
    return said


def scan_all(run, page, key):
    if to_dedupe(page, key) == 0:
        return Skip("no playlist with 2 or more songs")
    page.click("#dup-scan")
    page.wait_for_selector("#dup-progress:not([hidden])")
    page.wait_for_function("document.querySelector('#dup-progress').hidden", timeout=SCAN_TIMEOUT * 1000)
    page.wait_for_timeout(300)
    run.shot(page, f"duplicates-result-{key}", full=True)
    cards = page.locator(".dup-group").count()
    failed = page.inner_text("#dup-failed") if page.is_visible("#dup-failed") else ""
    return f"{page.inner_text('#dup-summary')} ({cards} with duplicates{'; ' + failed if failed else ''})"


def result_checks(run, page, design):
    if not page.locator(".dup-group").count():
        return Skip("no duplicates: nothing to untick")
    rows = page.evaluate("""() => [...document.querySelectorAll('.dups li')].map(li => ({
        checked: li.querySelector('input').checked, version: !!li.querySelector('.tag.version'), why: !!li.querySelector('.why') }))""")
    wrong = [r for r in rows if (r["version"] or r["why"]) and r["checked"]]
    assert not wrong, f"{len(wrong)} other versions come ticked"
    original = page.evaluate("[...document.querySelectorAll('.dups input')].map(i => i.checked)")
    try:
        # Unticking a row: it says «Stays» instead of «Removed»
        # (by position: «the first ticked one» would be another row after the click)
        k = page.evaluate("[...document.querySelectorAll('.dups input')].findIndex(i => i.checked)")
        if k >= 0:
            row = page.locator(".dups li").nth(k)
            row.locator("input").click()
            assert row.locator(".tag.off").is_visible() and not row.locator(".tag.on").is_visible(), "unticking does not switch to «Stays»"
        # «Untick all» and «Tick all» on the first card
        group, toggle = page.locator(".dup-group").first, page.locator(".dup-group .dup-toggle").first
        untick, tick = page.evaluate("[t('dedupe.untick_all'), t('dedupe.tick_all')]")
        if toggle.inner_text() == untick:
            toggle.click()
            assert group.locator("input:checked").count() == 0 and toggle.inner_text() == tick, "«Untick all» did not untick"
        toggle.click()
        assert group.locator("input:not(:checked)").count() == 0 and toggle.inner_text() == untick, "«Tick all» did not tick"
        # The decision bar, always visible while scrolling down
        for where in (0.5, 1):
            page.evaluate("(f) => { const m = document.querySelector('main'); m.scrollTop = m.scrollHeight * f; window.scrollTo(0, document.body.scrollHeight * f); }", where)
            page.wait_for_timeout(150)
            r = page.evaluate("(() => { const b = document.querySelector('#dup-actions').getBoundingClientRect(); return [b.top, b.bottom, innerHeight]; })()")
            assert 0 <= r[0] and r[1] <= r[2] + 1, f"the bottom bar is not visible when scrolling down ({r})"
        assert design(page, "repetidos") == [], design(page, "repetidos")
    finally:
        page.evaluate("""(orig) => [...document.querySelectorAll('.dups input')].forEach((i, k) => {
            if (i.checked !== orig[k]) i.click(); })""", original)
    return f"{len(rows)} rows"


def smallest_window(run, page, design):
    from tests.test_ui_dedupe import ALIGN
    page.set_viewport_size({"width": 860, "height": 620})
    try:
        page.wait_for_timeout(300)
        bad = page.evaluate(ALIGN) + design(page, "repetidos")
        run.shot(page, "duplicates-smallest-window")
        assert bad == [], bad
    finally:
        page.set_viewport_size({"width": 1120, "height": 780})
    return ""


def confirm_dialog(run, page, attempts):
    if not page.locator(".dup-group").count():
        return Skip("no duplicates: nothing to confirm")
    if not page.locator(".dups input:checked").count():
        page.locator(".dups input").first.click()
    page.click("#remove")
    page.wait_for_selector("#confirm[open]")
    said = page.inner_text("#c-title")
    run.shot(page, "duplicates-confirmation")
    page.click("#confirm-no")
    page.wait_for_selector("#confirm:not([open])", state="attached")
    assert not attempts, f"something tried to write: {attempts}"
    return f"«{said}», cancelled"


def sandbox_remove(run, page, api, c, sandbox, created):
    """A new playlist with A, B, C, A, B: the real screen finds the two duplicates, they're really
    removed (through the confirmation) and A, B, C is left."""
    old = leftovers(c)
    for pid in old:                              # from an interrupted run
        delete_playlist(c, pid)
    source, songs = three_songs(c)
    if not songs:
        return Skip("no playlist with three different songs to copy")
    a, b, x = songs
    pid = type(c).create(c, SANDBOX_NAME, SANDBOX_TEXT, False)
    created.append(pid)
    sandbox.add(pid)
    add_with_repeats(c, pid, [a, b, x, a, b])
    settle(lambda: len(c.read(pid)[1]) == 5, "the test playlist does not have the 5 songs")

    def listed():
        page.evaluate("(k) => forgetPlaylists(k)", c.key)
        to_dedupe(page, c.key)
        return page.locator(f"#dup-playlist option[value='{pid}']").count() > 0
    settle(listed, "the test playlist does not show in Remove duplicates")
    page.select_option("#dup-playlist", pid)
    page.click("#dup-scan")
    page.wait_for_selector(".dup-group", timeout=120000)
    rows = page.evaluate("""() => [...document.querySelectorAll('.dups li')].map(li => ({
        checked: li.querySelector('input').checked, title: li.querySelector('.side.remove .t').textContent }))""")
    assert [r["title"] for r in rows] == [a.title, b.title] and all(r["checked"] for r in rows), \
        f"expected «{a.title}» and «{b.title}» ticked, and found {rows}"
    run.shot(page, f"test-duplicates-{c.key}")
    page.click("#remove")
    page.wait_for_selector("#confirm[open]")
    page.click("#confirm-yes")
    try:
        page.wait_for_function("!state.busy && !document.querySelector('.dup-group')", timeout=180000)
    except Exception:                              # what the screen said while it waited
        said = page.evaluate("""() => ['#dup-summary', '#toast', '#dup-progress-text', '#dup-decide-text']
            .map(s => document.querySelector(s)).filter(e => e && e.offsetParent !== null)
            .map(e => e.textContent.trim()).filter(Boolean).join(' | ')""")
        raise AssertionError(f"removing did not finish in 3 minutes (busy: {page.evaluate('state.busy')}); "
                             f"the screen said: «{said}»") from None
    run.shot(page, f"test-removed-{c.key}")
    settle(lambda: [s.id for s in c.read(pid)[1]] == [a.id, b.id, x.id], "the test playlist did not end up as A, B, C")
    h = api.history(limit=1)[0]
    assert h["kind"] == "dedupe" and h["removed"] == 2 and h["source_name"] == SANDBOX_NAME, f"the history did not record it: {h}"
    again = f"; first deleted {len(old)} left over by another run" if old else ""
    return f"copied 3 from «{source}», added A, B, C, A, B and A, B, C was left{again}"


def liked_add_remove(c, liked):
    """Liked songs for real: likes two songs that weren't liked, the way a transfer does it
    (`add` to LIKED), checks they end up on top, unlikes them (`remove`) and checks your list
    is as it was. If something fails halfway, it still unlikes them."""
    from app.connectors.base import LIKED
    from app.core.models import Duplicate

    def liked_ids():
        return [s.id for s in c.read(LIKED)[1]]

    before = liked_ids()
    songs = two_unliked(c, set(before))
    if len(songs) < 2:
        return Skip("could not find two of your songs that are not liked")
    liked.update(s.id for s in songs)
    p, q = songs
    # YouTube Music likes one at a time: the order is exact. Tidal and Spotify like in batches, and inside a
    # batch the service decides the order (measured on 2026-11-25): both ending up on top is enough
    exact = c.key == "ytmusic"
    try:
        c.add(LIKED, [p, q])                       # what a transfer to liked songs does
        settle(lambda: liked_ids()[:2] == [p.id, q.id] if exact else set(liked_ids()[:2]) == {p.id, q.id},
               f"«{p.title}» and «{q.title}» did not end up on top of liked songs{', in that order' if exact else ''}")
    finally:
        try:
            c.remove(LIKED, [Duplicate(0, s, s) for s in (p, q)], confirmed=True)
            settle(lambda: not {p.id, q.id} & set(liked_ids()), "the test songs are still in liked songs")
        except Exception as e:
            raise AssertionError(f"could not unlike «{p.title}» and «{q.title}»: unlike them by hand ({e})") from e
        finally:
            liked.difference_update({p.id, q.id})
    assert liked_ids() == before, "liked songs did not stay as they were"
    return (f"liked «{p.title}» and «{q.title}», they ended up on top{' in order' if exact else ''}, unliked them "
            f"and your list stayed the same ({len(before)} {'song' if len(before) == 1 else 'songs'})")


def sandbox_delete(c, sandbox, created):
    if not created:
        return Skip("none was created")
    for pid in created:
        delete_playlist(c, pid)
    settle(lambda: not any(p.id in created for p in c.list_playlists()),
           f"«{SANDBOX_NAME}» still shows in {c.name}: delete it by hand")
    sandbox.difference_update(created)
    return ""


def settings(run, page, design):
    page.click("nav [data-go=ajustes]")
    text = page.inner_text("[data-i18n='settings.data_text']")
    assert text == page.evaluate("t('settings.data_text')"), f"Privacy and data says something else: «{text}»"
    assert page.is_visible("#wipe"), "«Erase everything» is not visible"
    assert design(page, "ajustes") == [], design(page, "ajustes")
    run.shot(page, "settings")
    return ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--service", help="check duplicates only in this service (tidal, ytmusic, spotify)")
    ap.add_argument("--no-transfer", action="store_true", help="without the transfer dry run")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    from tests.ui_helpers import check, open_page

    out = Path(tempfile.gettempdir()) / "PlaylistTransfer-check" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    run = Run(out)
    run.step("The real window opens and closes", smoke)

    api = real_api(out / "datos")
    sandbox: set[str] = set()
    liked: set[str] = set()
    attempts = guard(api, sandbox, liked)
    status = {s["key"]: s["status"] for s in api.status()}
    connected = [k for k, st in status.items() if st == "connected"]
    names = {s["key"]: s["name"] for s in api.services()}
    print(f"  connected: {', '.join(names[k] for k in connected) or 'none'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page, errors = open_page(browser, api)
        run.page = page
        run.step("Home", home, run, page, check)
        if not args.no_transfer:
            run.step("Transfer: dry run", transfer, run, page, check, connected, names)
        keys = [k for k in connected if not args.service or k == args.service]
        for i, key in enumerate(keys):
            n = names[key]
            run.step(f"Duplicates in {n}: before checking", before_check, run, page, check, key)
            if i == 0:
                run.step(f"Duplicates in {n}: cancel halfway", cancel_midway, run, page, key)
            if run.step(f"Duplicates in {n}: check all", scan_all, run, page, key):
                run.step(f"Duplicates in {n}: the result", result_checks, run, page, check)
                run.step(f"Duplicates in {n}: smallest window", smallest_window, run, page, check)
                run.step(f"Duplicates in {n}: the confirmation", confirm_dialog, run, page, attempts)
            # For real, in a playlist the check creates and deletes
            c, created = api._connectors[key], []
            try:
                run.step(f"Duplicates in {n}: remove for real in a test playlist", sandbox_remove,
                         run, page, api, c, sandbox, created)
            finally:
                run.step(f"Duplicates in {n}: delete the test playlist", sandbox_delete, c, sandbox, created)
            run.step(f"Liked songs in {n}: like and unlike two songs", liked_add_remove, c, liked)
        if not keys:
            run.results.append(("Remove duplicates", "SKIPPED", "no connected service", 0))
        run.step("Settings: privacy", settings, run, page, check)
        browser.close()

    run.results.append(("Nothing was written outside the test playlists", "FAILED" if attempts else "OK", ", ".join(attempts), 0))
    run.results.append(("No JavaScript errors", "FAILED" if errors else "OK", "; ".join(errors[:3]), 0))

    print("\n" + "\n".join(f"{state:9} {name}{'  — ' + detail if detail and state != 'OK' else ''}"
                           for name, state, detail, _ in run.results))
    print(f"\nScreenshots (with your data, outside the repo): {out}")
    bad = [r for r in run.results if r[1] == "FAILED"]
    print("OK: ready to publish" if not bad else f"{len(bad)} FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
