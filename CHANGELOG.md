# Changelog

What changed in each version. Up to v0.3.14 the app was published privately; v0.4.0 is the first public release, under the GPL-3.0.

## Unreleased

- **Remove duplicates: titles one letter apart are never ticked.** "Black Night" and "Black Knight" by the same artist could be taken as the same song and ticked to remove; a typo and another song look the same, so now they're shown as a possible duplicate, unticked, and you decide.
- **Removing duplicates says what's happening.** After you confirm, the bar at the bottom shows which playlist it's removing from, how many are done and how long is left, any pause a service asks for, and when it checks the playlist again. Unliking hundreds of songs in YouTube Music takes minutes, and before nothing on screen moved.
- **History opens lighter** with a long history: it only reads the songs it shows.

## v0.4.1 (2026-11-27)

- **Opens after unzipping with Windows Explorer.** Downloaded with a browser and unzipped with Explorer, the app could fail to open at all; now it opens, and every build checks it.
- **Remove duplicates in every playlist no longer stops halfway.** A YouTube mix saved to your library (YouTube's automatic mixes can't be read like a playlist) stopped the whole check and lost what had been found; now it's listed as not read and the rest is kept. Unexpected errors show a short message.
- **YouTube Music: song counts for the lists YouTube makes.** A list like Replay showed "unknown count" although YouTube says how many songs it has; now the count is shown.
- **After removing duplicates, the result is the real one.** Right after removing, YouTube Music could still answer with the old list for a moment, and the screen showed the removed duplicates again. Now the app notices, says it's looking again, and shows the playlist as it ended up.
- **Transparency:** the README explains each Windows warning (SmartScreen and Smart App Control), why the app isn't signed yet and how to check the download, plus a list of known limitations.

## v0.4.0 (2026-11-25)

- **First public release.** PlaylistTransfer is free software under the GNU GPL v3 or later, © 2026 Emmanuel Gaitan: you can use, study, share and change it, and whoever distributes it, changed or not, has to keep the same license, the source code and the copyright notice.
- The app is the same as v0.3.14; Settings › About shows the copyright and the license.
- Earlier versions were released privately; what changed in each one is below.

## v0.3.14 (2026-11-25)

- **Open source:** Settings › Privacy and data now says so: anyone can review the code here.
- **Connections:** a connected Spotify no longer offers *See the steps*.
- **Remove duplicates:** the first step says to pick the service and the playlist (or all of them).
- **YouTube Music:** updated to ytmusicapi 1.12.4 (36 of 36 real songs matched the same video as before).
- **How it's tested:** [TESTING.md](https://github.com/On33-3/PlaylistTransfer/blob/main/TESTING.md) explains the risks, the 506 automated tests, the measured design rules and the pre-release check with real accounts (which now also likes and unlikes songs for real).
- **For contributors:** code comments, logs and tool output are in English, and the README screenshots come from an invented demo library.

## v0.3.13 (2026-11-24)

- **Remove duplicates in all your playlists at once:** pick one playlist or all of them. The screen shows the steps before you start, the progress playlist by playlist (you can cancel), and one card per playlist with what is removed and what stays. A bar at the bottom always says what will happen before you confirm.
- **Clearer decisions:** each row says *To remove → Stays*; untick it and it says *Stays*. Other versions (live, remix, a different length) come unticked and say why.
- **Transfer says from where to where:** service · playlist on both sides, during the transfer and in the result.
- **Privacy:** everything stays on your PC, and the app only talks to Tidal, YouTube Music and Spotify. **Settings › Erase everything** leaves the app as it was the first time (your playlists are not touched).
- **Fixes:**
  - a new Spotify playlist could show 0 songs and be missing from Remove duplicates;
  - a long playlist name no longer wraps inside the list;
  - the window could show an old copy of the screens after an update;
  - you can no longer change the service while a check is running;
  - nothing from before stays on screen after erasing everything;
  - a damaged saved search no longer stops a transfer;
  - faster start.

## v0.3.12 (2026-11-24)

- **Pick up where it stopped:** if a transfer stops halfway (a service asks for a long break, you cancel, or the connection drops), what was already searched is kept. Transfer the same playlist again and it continues from there instead of starting over, and the screen tells you so.
- **You always see what's happening:** when a service asks for a break, the screen shows a countdown ("Spotify asked for a break: continuing in 2:40") and you can still cancel. If the break is longer than 5 minutes, it tells you from what time you can continue.
- **Progress while adding:** adding thousands of liked songs (YouTube Music marks them one by one) now shows "Adding to …: 400 of 1600" with the time left, instead of a still screen.
- **Spotify is searched more slowly**, and slows down further after each break it asks for, so large transfers are less likely to be stopped.
- **Safer cancel:** once songs start being added, Cancel is hidden, so a playlist is never left half-filled.

## v0.3.11 (2026-11-10)

- **No more needless reconnecting to YouTube Music:** if YouTube sends an unreadable answer while the app checks your saved sign-in, the app tries again instead of saying the session expired. A session that really expired is still reported as before.

## v0.3.10 (2026-11-10)

- **Screens stay in sync:** a new playlist from a transfer, a song added later or a service you just connected shows up on the screen you're looking at, without losing what you picked. After removing duplicates the app no longer checks a different playlist.
- **Alternatives that make sense:** "Same name, other artists" only offers songs whose whole title matches, skips lyric channels and repeats, and shows the artist on each button.
- **Results and history add up:** adding a song later updates the counters and tiles, and the history keeps one row per transfer.
- **Fewer wrong matches:** karaoke and 8-bit versions, uploads that only name the artist in "(feat. …)", someone else's edit, and titles with an extra word ("Gettin' Over" vs "Gettin' Over You") are no longer added on their own: they're left out or go to review.
- **YouTube Music hiccups:** an unreadable answer while reading a long playlist is retried, and if it keeps failing you get a clear message.

## v0.3.9 (2026-11-09)

- **YouTube Music sign-in opens a single tab:** Vivaldi used to reopen the tabs of the previous sign-in, adding one more each time.

## v0.3.8 (2026-11-09)

- **Liked songs show their real state:** YouTube Music likes no longer look empty (an unknown count now says so), and lists are fetched again when you come back to choose, so new likes appear without reconnecting.
- **Spotify:** a session from an earlier version now asks you to reconnect, which is needed to read Liked Songs.
- **Remaining time:** a long transfer shows about how much searching is left.

## v0.3.7 (2026-11-09)

- **Sign-in no longer gets stuck:** "Open again" and "Cancel" used to disappear if you changed screen or browser while a sign-in was waiting, leaving every Connect button greyed out for several minutes. They now stay available.

## v0.3.6 (2026-11-09)

- **Big libraries are fast now:** comparing 5000 songs went from about 11 minutes to under 20 seconds, and finding duplicates from over 3 minutes to about 7 seconds. Results are the same.
- **More reliable transfers:** if Tidal or YouTube Music ask to slow down, the app waits and retries instead of losing the song. If a later destination fails, what already went to the earlier ones stays in History.
- **Faster start:** services are checked at the same time when the app opens.
- **Smaller download:** 14.5 MB instead of 17 MB.

## v0.3.5 (2026-11-09)

- **Clearer buttons:** main actions are now deep purple with white text, so they never look like something already selected (light lavender still marks what's chosen: the active service, filter or step).
- **Home:** while services are missing, "Go to Connections" is the main button of the row, instead of a box on top of the title.

## v0.3.4 (2026-11-09)

From the first user's feedback:

- **"To review" first:** songs waiting for your decision (a "?" you can add anyway, or another artist's version) now sit right under the result, in playlist order.
- **Repeated songs get their own square:** a song that appears twice in the source playlist shows as "×2 Repeated in the source" (it used to show as "✓ Added" or "≈ Already there"). It's still added only once.
- **Better matching with parentheses:** "(I Can't Get No) Satisfaction" now matches "The Rolling Stones - I Can't Get No Satisfaction".

## v0.3.3 (2026-11-09)

- **Spotify setup fixed:** Spotify's dashboard rejects the old address without a port ("This redirect URI is not secure"). The address is now `http://127.0.0.1:8973/callback`; if you already started, add this one in your Spotify app.
- **Liked songs** (Tidal favorites, YouTube Music likes, Spotify Liked Songs) show up first in every list: transfer from them, into them, or remove duplicates there.
- **Closed the sign-in window?** YouTube Music notices it right away; for Tidal and Spotify an **Open again** button restarts the sign-in instead of a greyed-out Connect.
- Empty playlists are no longer offered as a source, and an empty source no longer looks like a successful transfer.
- The black notice at the bottom no longer covers the end of a screen.

## v0.3.2 (2026-11-09)

- **A service that stops answering no longer ruins a transfer.** If your Tidal or YouTube Music session expires or the internet drops mid-transfer, the app stops with a clear message **before changing any playlist**, instead of marking every song "not found". Connections then shows the service's real state.
- **No internet?** You get a plain "can't reach the service" message instead of a technical error.
- If the app can't start, a Windows dialog tells you so and where the log is.
- The log file is capped at 1 MB.
- Small fixes: untitled YouTube Music playlists follow the app language; the status chips refresh after a failed transfer.

## v0.3.1 (2026-11-08)

- **Connections screen** (second in the menu): each service with its numbered steps, visible **before** touching anything, and the browser used to sign in. Home guides you there.
- **Faster transfers:** finding matches is about 18 times faster.
- Internal tidy-up of the code and the tests, so services can be added without touching the interface.

## v0.3.0 (2026-11-08)

- **Spotify** (optional, still in testing): move playlists to and from Spotify and remove duplicates. Spotify requires Premium and each person creates their own app; **Settings › Spotify › Set up** walks you through it.
- **Guided steps to connect** Tidal, YouTube Music and Spotify: before connecting, what will happen; while connecting, what to do, numbered.
- **Home** explains what the app does; the source and the destination are picked in **Transfer**.
- Dropdown lists in the app's style and **its own icon** in the window and the taskbar.
- Fixes: the suggested browser is always each person's Windows default; with Firefox, the YouTube Music sign-in opens in Firefox; if the chosen browser was uninstalled, another one is suggested; Home no longer asks for a service you don't use.

## v0.2.0 (2026-11-08)

- **English and Spanish**, selectable in Settings.
- **Colored squares that tell you something:** each song of a transfer is a square with a legend.
- **Harmonized screens** with a single system of sizes.
- **Simpler:** the "?" (maybe another version) are decided at the end, on the result; the history downloads as a light text file.
- A stress test: the design holds with extreme data; classical music is not mixed up.

## v0.1.0 (2026-11-08)

- First release: move playlists between **Tidal and YouTube Music** and **remove duplicates**, from a desktop window (pywebview).
- Matching that handles several artists, covers, parts and versions, with a catalog of scenarios.
- **History** of every transfer and clean-up, with CSV reports on request; match thresholds in Settings.
- A Windows package (`.exe` in a folder + zip) built and published by GitHub Actions.
