# Security

## Reporting a vulnerability

Please don't describe it in a public issue. Use GitHub's private reporting instead: the repository's **Security** tab → **Report a vulnerability**. Only the author sees it.

If that option isn't there, open an issue that only says you have something to report privately, with no details, and you'll be contacted.

Only the [latest release](../../releases/latest) gets fixes.

## What the app keeps, and where

Everything stays on your computer, in `%LOCALAPPDATA%\PlaylistTransfer\`. The app has no servers of its own and only talks to the music services.

| File or folder | What it is |
|---|---|
| `tidal_session.json` | Your Tidal session |
| `browser.json` | Your YouTube Music session (the cookies it needs, read from the sign-in window) |
| `spotify_app.json`, `spotify_session.json` | The Client ID of the Spotify app you created, and your Spotify session |
| `browser-profiles\` | Separate browser profiles used only to sign in to YouTube Music |
| `historial.sqlite`, `busquedas\`, `preferencias.json` | History, searches saved to resume a transfer, and settings |
| `app.log` | What the app did, to find out what went wrong; it has a size limit |

**These files are not encrypted.** They're protected the way the rest of your Windows user folder is: anyone who can sign in to your Windows account, or a program running as you, can read them. That is a known limitation.

To remove everything: **Settings › Erase everything**. It leaves the app as it was the first time. Disconnecting a service in **Connections** removes its session only.

## How signing in works

- **YouTube Music:** the app opens your browser with a profile of its own and a debugging port that only listens on `127.0.0.1`, on a random port, while you sign in. It reads that profile's YouTube cookies and closes the browser. Your normal browser profile is never touched.
- **Tidal:** the official device sign-in. You confirm a code on Tidal's site.
- **Spotify:** OAuth with PKCE, using the app you created in Spotify's dashboard. A small server listens on `127.0.0.1:8973` only while you sign in, and it checks the `state` it gets back.

## Checking a download

The `.exe` isn't digitally signed yet. The README's [Windows warnings](README.md#windows-warnings) explain how to check that a download is the one built by GitHub Actions from this code.
