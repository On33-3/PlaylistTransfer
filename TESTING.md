# How PlaylistTransfer is tested

PlaylistTransfer changes people's playlists. A wrong match adds a song nobody asked for; a wrong duplicate removes a song someone wanted. So the testing starts from one rule, written at the top of the matching scenarios:

> When in doubt, the result has to be the safe one. When transferring, "not found" or "doubtful" is better than adding another song as OK. When cleaning, missing a duplicate is better than removing a song that wasn't one.

**At a glance** (v0.3.13)

- **493 automated tests**, about 75 seconds on a laptop, run on every push in a clean Windows machine on GitHub Actions.
- The interface is tested **in the same engine the window uses** (Chromium, through Playwright and Edge), and design rules are measured on every screen.
- Before each release, **the real interface runs against real accounts**, with every write blocked except inside a throwaway playlist the check creates and deletes.
- Each release is built and opened in a clean Windows without Python before it is published, with a SHA-256 checksum.

## Risks first

| Risk | How it's covered |
|---|---|
| Removing a song the user wanted | Removing requires `confirmed=True` all the way down to each connector, set only by the confirmation dialog. The playlist is read again right before removing; if it changed, nothing is removed. Other versions (live, remix, a different length) come unticked and marked; a different length also says by how much. |
| Adding the wrong song | 139 matching scenarios. A doubtful match is never added on its own: the user decides at the end. Matching changes are compared against the author's real transfer reports (kept locally, never in the repo). |
| Leaking personal data | A test fails if the app's code contains any address other than the three music services. Another fails if the repo tracks a session or preference file, and the build stops if one would end up in the package. |
| A playlist left half-changed | A transfer stops before writing anything if the searches start failing. Once writing starts, Cancel is hidden and a service's pause can't cut it. A transfer that stops while searching resumes from where it stopped. |
| The `.exe` not opening on someone's PC | CI builds it and opens it in a clean Windows with Python removed from `PATH`, and fails if the interface doesn't load within 2 minutes. |
| The layout breaking with real data | Extreme data (long names, thousands of songs, many playlists) in the smallest window size, with the design rules measured on top. |

## The layers

Counts as of v0.3.13.

| Layer | What it proves | Where | Tests |
|---|---|---|---|
| Matching scenarios | Same recording, other performer, other song by the same artist, live/remix/karaoke/cover, parts and sequels, featured artists. Each rule has its scenario. | `tests/test_scenarios.py`, `tests/test_matching.py` | 169 |
| Engine and API | Transfers, resuming, duplicates across all playlists, history, first run, erase everything, malformed requests, actions at the wrong time. Uses fake services. | `test_api`, `test_transfer`, `test_resume`, `test_edge_cases`, `test_first_run`, `test_privacy`, `test_export` | 112 |
| Connectors | Tidal and YouTube Music connectors, and the Spotify connector against a fake Spotify Web API: pagination, `429 Retry-After`, token refresh, the local OAuth (PKCE) server. | `test_connectors`, `test_spotify`, `test_auth` | 101 |
| Interface, end to end | Every screen driven in headless Edge with Playwright, talking to the real API over the same bridge the window uses. | `tests/test_ui_*.py` | 65 |
| Languages | Spanish and English have the same keys; English screens show no Spanish words. | `test_i18n` | 21 |
| Packaging and startup | Icon, version info, licenses of what is bundled, package size limit, a startup failure is shown and logged, the window never reuses old copies of the UI. | `test_packaging`, `test_main` | 21 |
| Tooling and static checks | The real-account check can't write outside its test playlist; pyflakes finds nothing. | `test_real_check`, `test_code` | 4 |

### Design rules, measured

`check()` in `tests/ui_helpers.py` runs on every screen in the interface tests:

- buttons are 42, 36 or 28 px tall, and fields 42 or 36;
- icon-only buttons are square, with the icon centred to half a pixel;
- no button text wraps or gets cut, and no dropdown shows its selected item on two lines.

Other screen-level checks:

- the duplicates screen has its own alignment check (cards of equal height, one left edge, nothing overflowing), at the normal and the smallest window size;
- a main action never looks like something already selected;
- the notification toast never covers the end of a screen.

## Real accounts: `tools/real_check.py`

The automated tests use fake services, so they can't notice when a real service behaves differently. Before each release, `py tools/real_check.py` drives the real interface with the author's own sessions:

1. The real window opens and closes.
2. Home, and a dry-run transfer of the smallest playlist (it searches but changes nothing).
3. In each connected service:
   - the duplicates screen before checking;
   - cancelling a check halfway, then checking all playlists;
   - the result: ticking and unticking, "untick all", and the decision bar staying visible while scrolling;
   - the smallest window size;
   - the confirmation dialog, which is opened and cancelled.
4. **Removing for real**, in each service:
   - three of the user's songs are copied into a new playlist as A, B, C, A, B;
   - the screen has to find exactly the two duplicates;
   - they are removed through the confirmation dialog;
   - the playlist has to end up as A, B, C, and the history has to record it.

   The playlist is then deleted, always (in a `finally`), and a leftover from an interrupted run is deleted first.
5. Settings, and no JavaScript errors anywhere.

**Safety.** Before the page opens, `guard()` replaces every write the API and the connectors can do: add, create, remove, disconnect, erase everything, and a real transfer. Only removing from the throwaway playlist goes through, and the run fails if anything else was attempted. A test checks the guard with the fake services.

**Where its data goes.** The check's history and saved searches go to a temporary folder. Its screenshots contain real data, so they stay outside the repo.

It doesn't run in CI on purpose: that would mean uploading personal sessions.

## Refactoring without surprises: `tools/check_step.py`

A refactor is done in small steps. Before the first one, `py tools/check_step.py --base base.json` takes a snapshot of the computed styles of every element in 32 interface states. After each step, `py tools/check_step.py base.json` runs, stopping at the first failure:

1. pyflakes;
2. every test;
3. the matching check against real reports, if the step touched `matching.py`;
4. a new style snapshot, which must show **0 differences**.

Each step that passes gets its own commit; one that fails is undone. A refactor never changes a test, and a behaviour fix never travels in the same commit as a refactor.

## Bugs the automation caught

| Bug | Caught by | Test that now guards it |
|---|---|---|
| Spotify's playlist list kept saying 0 songs for a just-created playlist that already had 5, so it was missing from Remove duplicates | `real_check` (removing for real) | `test_a_new_playlist_listed_with_no_songs_gets_its_real_count`, which fails on the old code |
| A long playlist name wrapped onto two lines inside the closed dropdown | `real_check` screenshot review | `test_a_long_playlist_name_stays_in_one_line_in_the_closed_list`, plus the dropdown rule in `check()`, which fails on the old code |
| The Spotify login test failed now and then on CI: it read the reply page before the fake browser thread had finished | CI | The test waits for that thread; 30 runs in a row passed |
| Switching service in the middle of a duplicates check showed one service's result under another's name | Negative-case review | `test_service_and_playlist_cannot_change_while_checking` |
| A malformed removal request crashed the API, and an index of `-1` picked the last duplicate | Negative-case review | `test_removing_with_a_malformed_request_answers_an_error_and_never_crashes` |
| A half-broken saved-search file stopped a transfer | Negative-case review | `test_a_half_broken_progress_file_does_not_stop_a_transfer` |
| Other cases from the same review: results left on screen after erasing everything or after disconnecting a service, and the same check sent twice in one request | Negative-case review | `tests/test_edge_cases.py` |
| Startup failed now and then: the page waited for every service before saying it was ready | Repeated startup runs | The page is ready first; a slow service is written to the log |

One bug the tests could not have caught: after an update, the window (WebView2) reused a cached copy of the old page with the new scripts. The tests open the page as a file, without that cache. The user found it. Now the cache is cleared before the window opens, and `test_the_window_never_reuses_old_copies_of_the_ui` guards it.

## Known limits

- The real-account check is run by hand before each release, not on a schedule.
- Adding to Spotify's "Liked songs" has not been checked with a real account yet.
- Tidal and YouTube Music work through unofficial libraries. A change on their side can break them; `real_check` detects it, but nothing can prevent it.
- The `.exe` is not signed yet, and the code comments are still in Spanish (translating them is planned).

## How AI was used

The app was built with an AI coding assistant (Claude Code), directed by the author. What kept the quality under the author's control:

- **Written rules** in `CLAUDE.md` (in Spanish), applied in every session. For example:
  - never remove songs without the user's confirmation in the interface;
  - never commit sessions or personal files;
  - nothing counts as working until it has been run and its output shown.
- **Proof, not promises.** A bug fix comes with a test, and recent ones were also run against the old code to show the test catches the bug. Interface work is finished only after looking at its screenshots.
- **Small, separate steps:**
  - one change per commit;
  - refactors checked with `check_step` and never mixed with fixes;
  - a negative-case review before handing a version to new users.
- **Human decisions.** The author decides the scope, what happens with personal data, and when something is published. Releases and anything public need their explicit OK.

## Running it

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q                  # every test (the interface ones need Playwright and Edge)
py tools/real_check.py           # before a release, with your own accounts
py tools/check_step.py base.json # after each refactor step
py tools/check_reports.py        # after changing the matching, against your own reports
```
