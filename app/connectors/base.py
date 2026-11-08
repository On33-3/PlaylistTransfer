"""Interfaz que cumple cada servicio de música. El resto de la app solo habla con esto."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from enum import Enum

from app.auth.browsers import Browser
from app.core.models import Duplicate, PlaylistInfo, Song
from app.i18n import t as tr

BATCH = 50           # temas por pedido al agregar o quitar
SEARCH_PAUSE = 0.3   # segundos entre búsquedas, para no saturar las APIs


class Status(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"            # hay una sesión guardada, pero el servicio ya no la acepta
    DISCONNECTED = "disconnected"
    SETUP = "setup"                # falta configurar algo antes de conectar (Spotify: el Client ID)


class ConnectorError(Exception):
    """Error que se le puede mostrar al usuario tal cual."""


class NotConnectedError(ConnectorError):
    pass


class NotOwnerError(ConnectorError):
    """La playlist no es del usuario: no se le pueden agregar ni quitar temas."""


class RemovalNotConfirmedError(ConnectorError):
    """Se intentó quitar temas sin la confirmación explícita del usuario."""


class Connector(ABC):
    key: str          # "tidal", "ytmusic"...
    name: str         # "Tidal", "YouTube Music"...
    search_pause: float = SEARCH_PAUSE
    # True si el servicio quita por canción y no por posición (Spotify): para quitar una copia exacta
    # se quitan todas y se vuelve a agregar la que queda (con fecha de "agregado" nueva)
    removes_all_copies: bool = False

    @abstractmethod
    def status(self) -> Status: ...

    @abstractmethod
    def connect(self, browser: Browser | None = None, cancel: threading.Event | None = None) -> None:
        """Inicia sesión (puede abrir el navegador y bloquear hasta que el usuario termine).
        Si `cancel` se activa mientras espera, corta y lanza `Cancelled`."""

    @abstractmethod
    def disconnect(self) -> None:
        """Borra la sesión guardada."""

    @abstractmethod
    def list_playlists(self) -> list[PlaylistInfo]:
        """Playlists del usuario a las que se les pueden agregar temas."""

    @abstractmethod
    def read(self, playlist_id: str) -> tuple[str, list[Song]]:
        """(nombre, temas en orden)."""

    @abstractmethod
    def search(self, song: Song) -> list[Song]:
        """Candidatos para `song` en este servicio (sin elegir: eso lo hace core.matching)."""

    @abstractmethod
    def create(self, name: str, description: str, public: bool = False) -> str:
        """Crea una playlist y devuelve su id."""

    @abstractmethod
    def add(self, playlist_id: str, songs: list[Song]) -> None: ...

    @abstractmethod
    def remove(self, playlist_id: str, items: list[Duplicate], *, confirmed: bool = False) -> None:
        """Quita esas apariciones. Exige `confirmed=True`: solo después de que el usuario confirmó en la UI."""

    @staticmethod
    @abstractmethod
    def parse_id(value: str) -> str:
        """ID de playlist a partir de una URL o del ID mismo."""

    @staticmethod
    @abstractmethod
    def url(playlist_id: str) -> str: ...


def require_confirmation(confirmed: bool) -> None:
    if confirmed is not True:
        raise RemovalNotConfirmedError(tr("err.removal_not_confirmed"))
