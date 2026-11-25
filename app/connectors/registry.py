"""The music services the app knows, in one place.

From here come the connectors the app builds, the names the window shows (the UI asks for them with
`Api.services()`) and the links that can be opened. To add a service: its connector in
`app/connectors/` and one line in `SERVICES`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.connectors.base import Connector
from app.storage import paths


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    links: tuple[str, ...]                    # prefixes of the links that can be opened
    make: Callable[[], Connector] | None      # None: not available yet ("coming soon")


def _tidal() -> Connector:
    from app.connectors.tidal import TidalConnector
    return TidalConnector(paths.tidal_session_file())


def _ytmusic() -> Connector:
    from app.connectors.ytmusic import YTMusicConnector
    return YTMusicConnector(paths.yt_browser_file(), paths.browser_profiles_dir())


def _spotify() -> Connector:
    from app.connectors.spotify import SpotifyConnector
    return SpotifyConnector(paths.spotify_session_file(), paths.spotify_app_file())


SERVICES: tuple[Service, ...] = (
    Service("tidal", "Tidal", ("https://tidal.com/", "https://listen.tidal.com/"), _tidal),
    Service("ytmusic", "YouTube Music", ("https://music.youtube.com/",), _ytmusic),
    Service("spotify", "Spotify", ("https://open.spotify.com/", "https://developer.spotify.com/",
                                     "https://www.spotify.com/"), _spotify),
)


def names() -> dict[str, str]:
    """{"tidal": "Tidal", ...} in the order they are shown."""
    return {s.key: s.name for s in SERVICES}


def name(key: str | None) -> str | None:
    return names().get(key, key) if key else None


def build_connectors() -> dict[str, Connector]:
    """One connector for each available service."""
    return {s.key: s.make() for s in SERVICES if s.make is not None}


def is_allowed_link(url: str) -> bool:
    """Only links to the music services are opened (never just any address)."""
    return any(str(url).startswith(prefix) for s in SERVICES for prefix in s.links)
