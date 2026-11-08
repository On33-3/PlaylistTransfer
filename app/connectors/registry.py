"""Los servicios de música que conoce la app, en un solo lugar.

De acá salen los conectores que arma la app, los nombres que muestra la ventana (la UI los pide con
`Api.services()`) y los links que se pueden abrir. Para sumar un servicio: su conector en
`app/connectors/` y una línea en `SERVICES`.
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
    links: tuple[str, ...]                    # prefijos de los links que se pueden abrir
    make: Callable[[], Connector] | None      # None: todavía no está disponible ("próximamente")


def _tidal() -> Connector:
    from app.connectors.tidal import TidalConnector
    return TidalConnector(paths.tidal_session_file())


def _ytmusic() -> Connector:
    from app.connectors.ytmusic import YTMusicConnector
    return YTMusicConnector(paths.yt_browser_file(), paths.browser_profiles_dir())


SERVICES: tuple[Service, ...] = (
    Service("tidal", "Tidal", ("https://tidal.com/", "https://listen.tidal.com/"), _tidal),
    Service("ytmusic", "YouTube Music", ("https://music.youtube.com/",), _ytmusic),
    Service("spotify", "Spotify", ("https://open.spotify.com/",), None),
)


def names() -> dict[str, str]:
    """{"tidal": "Tidal", ...} en el orden en que se muestran."""
    return {s.key: s.name for s in SERVICES}


def name(key: str | None) -> str | None:
    return names().get(key, key) if key else None


def build_connectors() -> dict[str, Connector]:
    """Un conector por cada servicio disponible."""
    return {s.key: s.make() for s in SERVICES if s.make is not None}


def is_allowed_link(url: str) -> bool:
    """Solo se abren links de los servicios de música (nunca una dirección cualquiera)."""
    return any(str(url).startswith(prefix) for s in SERVICES for prefix in s.links)
