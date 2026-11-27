# How PlaylistTransfer is tested

PlaylistTransfer changes people's playlists. A wrong match adds a song nobody asked for; a wrong duplicate removes a song someone wanted. So the testing starts from one rule, written at the top of the matching scenarios:

> When in doubt, the result has to be the safe one. When transferring, "not found" or "doubtful" is better than adding another song as OK. When cleaning, missing a duplicate is better than removing a song that wasn't one.

**At a glance** (November 2026)

- **529 automated tests**, about 75 seconds on a laptop, run on every push in a clean Windows machine on GitHub Actions.
- The interface is tested **in the same engine the window uses** (Chromium, through Playwright and Edge), and design rules are measured on every screen.
- Before each release, **the real interface runs against real accounts**, with every write blocked except inside a throwaway playlist the check creates and deletes.
- Each release is built and opened in a clean Windows without Python before it is published, with a SHA-256 checksum.
- A **load check** runs libraries of 5,000 to 30,000 songs and a history of a million through the real engine, API and window.

## Risks first

| Risk | How it's covered |
|---|---|
| Removing a song the user wanted | Removing requires `confirmed=True` all the way down to each connector, set only by the confirmation dialog. The playlist is read again right before removing; if it changed, nothing is removed. Other versions (live, remix, a different length) come unticked and marked; a different length also says by how much. |
| Adding the wrong song | 139 matching scenarios. A doubtful match is never added on its own: the user decides at the end. Matching changes are compared against the author's real transfer reports (kept locally, never in the repo). |
| Leaking personal data | A test fails if the app's code contains any address other than the three music services. Another fails if the repo tracks a session or preference file, and the build stops if one would end up in the package. |
| A playlist left half-changed | A transfer stops before writing anything if the searches start failing. Once writing starts, Cancel is hidden and a service's pause can't cut it. A transfer that stops while searching resumes from where it stopped. |
| The `.exe` not opening on someone's PC | CI builds it and opens it in a clean Windows with Python removed from `PATH`, and fails if the interface doesn't load within 2 minutes. |
| Text from a service running as code (the window can remove songs) | Every field a service returns (song, artist, album, playlist, error) carries HTML through every screen, and nothing may run. Checked with a negative control: with escaping turned off, the test fails. Every public API method is also called with odd values (wrong types, empty, huge) and must answer, never raise. |
| The layout breaking with real data | Extreme data (long names, thousands of songs, many playlists) in the smallest window size, with the design rules measured on top. |

## The layers

Counts as of November 2026.

| Layer | What it proves | Where | Tests |
|---|---|---|---|
| Matching scenarios | Same recording, other performer, other song by the same artist, live/remix/karaoke/cover, parts and sequels, featured artists. Each rule has its scenario. | `tests/test_scenarios.py`, `tests/test_matching.py` | 172 |
| Engine and API | Transfers, resuming, duplicates across all playlists, history, first run, erase everything, malformed requests, actions at the wrong time. Uses fake services. | `test_api`, `test_transfer`, `test_resume`, `test_edge_cases`, `test_first_run`, `test_privacy`, `test_export` | 120 |
| Connectors | Tidal and YouTube Music connectors, and the Spotify connector against a fake Spotify Web API: pagination, `429 Retry-After`, token refresh, the local OAuth (PKCE) server. | `test_connectors`, `test_spotify`, `test_auth` | 111 |
| Interface, end to end | Every screen driven in headless Edge with Playwright, talking to the real API over the same bridge the window uses. | `tests/test_ui_*.py` | 65 |
| Languages | Spanish and English have the same keys; English screens show no Spanish words. | `test_i18n` | 21 |
| Packaging and startup | Icon, version info, licenses of what is bundled, package size limit, a startup failure is shown and logged, the window never reuses old copies of the UI. | `test_packaging`, `test_main` | 25 |
| Tooling and static checks | The real-account check can't write outside its test playlist and test songs; the comments-only check catches any code change; the demo library behind the README screenshots keeps every status; pyflakes finds nothing. | `test_real_check`, `test_check_comments_only`, `test_demo_library`, `test_code` | 15 |

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
5. **Liked songs for real**, in each service:
   - two of the user's songs that weren't liked are liked the way a transfer does it;
   - they have to show up at the top;
   - they are unliked (always), and the liked list has to be exactly as it was.
6. Settings, and no JavaScript errors anywhere.

A step that fails leaves a screenshot of the screen at that moment.

**Safety.** Before the page opens, `guard()` replaces every write the API and the connectors can do: add, create, remove, disconnect, erase everything, and a real transfer. Only two things go through: removing from the throwaway playlist, and liking or unliking the two test songs. The run fails if anything else was attempted. A test checks the guard with the fake services.

**Where its data goes.** The check's history and saved searches go to a temporary folder. Its screenshots contain real data, so they stay outside the repo.

It doesn't run in CI on purpose: that would mean uploading personal sessions.

## Load: `tools/load_check.py`

Big libraries through the real engine, API and window, with fake services that answer like real ones (up to 10 results per search). The real services are never load-tested: the app asks them one thing at a time on purpose. Each step has a generous time budget, to catch a slowdown of several times (like the 11 minutes 5,000 songs once took), not a slower PC; `--memory` also measures the peak memory of each step.

Measured on 2026-11-27, on the author's laptop (the services' own pauses not included):

| Step | Time |
|---|---|
| Transfer 5,000 songs to a new playlist | 6 s |
| Transfer 5,000 songs into a playlist that has half of them | 8 s |
| Find duplicates among 10,000 songs | 19 s |
| Check 41 playlists (30,000 songs) and remove the 11,161 duplicates found | 22 s + 40 s |
| Open History with 200 operations of 5,000 songs (a 99 MB history) | 1.2 s, 5 MB |
| The window: a live transfer of 2,000 songs; 1,000 duplicates on screen; 201 history rows | 2.4 s; 0.7 s; 0.1 s |

It found two things on its first run, both fixed (see the table below).

## Refactoring without surprises: `tools/check_step.py`

A refactor is done in small steps. Before the first one, `py tools/check_step.py --base base.json` takes a snapshot of the computed styles of every element in 32 interface states. After each step, `py tools/check_step.py base.json` runs, stopping at the first failure:

1. pyflakes;
2. every test;
3. the matching check against real reports, if the step touched `matching.py`;
4. a new style snapshot, which must show **0 differences**.

Each step that passes gets its own commit; one that fails is undone. A refactor never changes a test, and a behaviour fix never travels in the same commit as a refactor.

When a change should only touch comments (translating them into English was one: about 1,250 lines in 76 files), `py tools/check_comments_only.py` proves the code is identical: Python is compared by syntax tree without docstrings, and JavaScript, CSS, HTML and YAML by their text without comments. It has its own tests, including the two cases where its JavaScript reader got confused (a quote inside a regular expression, and nested template literals); both were found during the translation itself.

## Bugs the automation caught

| Bug | Caught by | Test that now guards it |
|---|---|---|
| Spotify's playlist list kept saying 0 songs for a just-created playlist that already had 5, so it was missing from Remove duplicates | `real_check` (removing for real) | `test_a_new_playlist_listed_with_no_songs_gets_its_real_count`, which fails on the old code |
| The fakes were too optimistic: they kept the order of the songs inside one "like" request, so the tests "proved" an exact order in Liked songs. Tidal and Spotify decide that order themselves (sent in either order, the result was the same). Only the order between requests is the app's. | `real_check` (liked songs) | The fakes now order each request differently from the list on purpose, and the tests check what is really guaranteed. Exact order would mean one request per song; the author chose speed |
| A long playlist name wrapped onto two lines inside the closed dropdown | `real_check` screenshot review | `test_a_long_playlist_name_stays_in_one_line_in_the_closed_list`, plus the dropdown rule in `check()`, which fails on the old code |
| The Spotify login test failed now and then on CI: it read the reply page before the fake browser thread had finished | CI | The test waits for that thread; 30 runs in a row passed |
| Switching service in the middle of a duplicates check showed one service's result under another's name | Negative-case review | `test_service_and_playlist_cannot_change_while_checking` |
| A malformed removal request crashed the API, and an index of `-1` picked the last duplicate | Negative-case review | `test_removing_with_a_malformed_request_answers_an_error_and_never_crashes` |
| A half-broken saved-search file stopped a transfer | Negative-case review | `test_a_half_broken_progress_file_does_not_stop_a_transfer` |
| Other cases from the same review: results left on screen after erasing everything or after disconnecting a service, and the same check sent twice in one request | Negative-case review | `tests/test_edge_cases.py` |
| The 75 tests that use the browser were skipped on CI without anyone noticing: Playwright wasn't installed there, while this document said every test ran on every push | Reading the CI log while writing up the project | `requirements-dev.txt` installs Playwright, and on CI a missing browser now fails the run instead of skipping (`browser` fixture in `tests/conftest.py`) |
| A tester's YouTube Music library had a mix that YouTube makes (an `RD…` playlist): ytmusicapi can't read it like a playlist, and that unexpected error stopped the whole "every playlist" duplicates check, losing the 190 duplicates already found | A tester (screenshot), then reproduced with the same playlist id | `test_an_unexpected_error_in_one_playlist_does_not_lose_the_whole_check` and the connector tests for mixes |
| Unzipped with Windows Explorer, every file carries the mark of the web and .NET refused to load the window's assemblies: the app never opened | Investigating the same tester's report, by marking every file of the release | CI marks every file as downloaded and opens the `.exe`, with a control that must fail without `PlaylistTransfer.exe.config` |
| Right after removing duplicates, YouTube Music once answered with the old list, so the screen showed the removed duplicates again (intermittent: it passed the next two runs) | `real_check` (removing for real), on the day of a release, which waited for the fix | `test_right_after_removing_an_old_answer_is_not_shown_as_the_result`, which fails on the old code |
| A list that YouTube makes (like Replay) showed "unknown count": ytmusicapi only reads the count from one subtitle shape | A tester, then checked against the author's own library | `test_yt_a_playlist_without_count_takes_it_from_its_description` |
| Titles one letter apart by the same artist ("Black Night" / "Black Knight") counted as the same song, and Remove duplicates ticked one to remove. A typo and another song look alike, so now they come unticked and the person decides | `load_check` (made-up titles), then checked with real-looking pairs | Three cases in `test_a_different_recording_is_never_ticked_to_remove`; `check_reports`: 81 real matches, none changed |
| Opening History read every song of every operation to show 60 of each: 281 MB with a long history (now 5 MB) | `load_check --memory` | `test_history_tiles_have_the_song_and_cap_with_total` (same result) |
| 105 combinations of odd values made API methods raise instead of answering, and `Api.create` was published to the page | A test that calls every public method with odd values | `test_any_request_with_odd_values_gets_an_answer_never_an_exception` |
| The startup test crashed now and then on CI after "UI ready": closing the window from the test's thread raced with the window's own thread | CI | `test_the_smoke_test_ends_with_its_result_without_closing_the_window` |
| Startup failed now and then: the page waited for every service before saying it was ready | Repeated startup runs | The page is ready first; a slow service is written to the log |

One bug the tests could not have caught: after an update, the window (WebView2) reused a cached copy of the old page with the new scripts. The tests open the page as a file, without that cache. The user found it. Now the cache is cleared before the window opens, and `test_the_window_never_reuses_old_copies_of_the_ui` guards it.

## Known limits

- The real-account check is run by hand before each release, not on a schedule.
- Tidal and YouTube Music work through unofficial libraries. A change on their side can break them; `real_check` detects it, but nothing can prevent it.
- The `.exe` is not signed yet, so Windows' Smart App Control blocks it, and only a signature fixes that (see the README's *Windows warnings*). It can't be reproduced on the author's PC: it runs a Windows Insider build where Smart App Control didn't block the app.
- The real-account check uses the author's accounts, so it only covers what they have: a YouTube mix saved to the library was found by a tester, not by `real_check`.

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
py tools/load_check.py           # big libraries, against time budgets (--memory: peak memory)
py tools/check_step.py base.json # after each refactor step
py tools/check_reports.py        # after changing the matching, against your own reports
```
