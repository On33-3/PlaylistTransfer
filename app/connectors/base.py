"""Interfaz que cumple cada servicio de música. El resto de la app solo habla con esto."""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from enum import Enum

from app.auth.browsers import Browser
from app.core.matching import SCORE_OK, best_match
from app.core.models import Cancelled, Duplicate, PlaylistInfo, Song
from app.i18n import t as tr

log = logging.getLogger(__name__)

BATCH = 50           # temas por pedido al agregar o quitar
# Las canciones que te gustan ("Me gusta", Liked Songs, favoritos): no son una playlist en ningún servicio,
# pero la app las muestra como una más, primera en la lista, con este id (cada conector lo traduce)
LIKED = "__liked__"


def liked_info(count: int | None) -> PlaylistInfo:
    return PlaylistInfo(LIKED, tr("playlist.liked"), count)
SEARCH_PAUSE = 0.3   # segundos entre búsquedas, para no saturar las APIs
RATE_LIMIT_WAITS = (2, 5, 15)   # segundos a esperar si el servicio pide frenar; después, se da por fallado
MAX_RATE_LIMIT_WAIT = 60        # aunque el servicio pida más
FAILURES_IN_A_ROW = 3   # rondas de búsqueda seguidas sin ninguna consulta que conteste: el servicio no responde


def rate_limit_pause(e: BaseException) -> float | None:
    """Si el error es "pediste demasiado, esperá": cuántos segundos pide (0 si no lo dice). Si no, None.
    tidalapi lanza TooManyRequests con `retry_after`; requests, un HTTPError 429; ytmusicapi lo dice en el texto."""
    response = getattr(e, "response", None)
    if type(e).__name__ == "TooManyRequests" or getattr(response, "status_code", None) == 429 or "HTTP 429" in str(e):
        for value in (getattr(e, "retry_after", None), getattr(response, "headers", {}).get("Retry-After") if response is not None else None):
            try:
                if value is not None and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):       # Retry-After también puede ser una fecha
                pass
        return 0
    return None


def human_wait(seconds: float) -> str:
    """"45 s", "12 min", "3 h 20 min": cuánto pidió esperar un servicio."""
    s = max(1, round(seconds))
    if s < 60:
        return tr("time.seconds", n=s)
    m = -(-s // 60)
    if m < 60:
        return tr("time.minutes", n=m)
    h, m = divmod(m, 60)
    return tr("time.hours_minutes", h=h, m=m) if m else tr("time.hours", h=h)


def clock_after(seconds: float, now=None) -> str:
    """La hora a la que termina la espera ("18:40"); con fecha si es otro día ("11/11 18:40")."""
    from datetime import datetime, timedelta
    now = now or datetime.now()
    end = now + timedelta(seconds=seconds)
    return f"{end:%H:%M}" if end.date() == now.date() else f"{end:%d/%m %H:%M}"


class Status(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"            # hay una sesión guardada, pero el servicio ya no la acepta
    DISCONNECTED = "disconnected"
    SETUP = "setup"                # falta configurar algo antes de conectar (Spotify: el Client ID)


class ConnectorError(Exception):
    """Error que se le puede mostrar al usuario tal cual."""


class NotConnectedError(ConnectorError):
    pass


class NoNetwork(ConnectorError):
    """No se pudo hablar con el servicio (sin internet, o no contesta)."""


def is_network_error(e: BaseException) -> bool:
    """Los errores de conexión de `requests` (lo usan tidalapi, ytmusicapi y Spotify): sin internet o sin respuesta."""
    try:
        import requests
    except ImportError:
        return False
    return isinstance(e, (requests.ConnectionError, requests.Timeout))


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

    def collect(self, song: Song, queries: Iterable[str], fetch: Callable[[str], Iterable[Song]],
                found: dict[str, Song] | None = None, *, fatal: tuple[type[BaseException], ...] = (),
                recoverable: type[BaseException] = Exception) -> dict[str, Song]:
        """La búsqueda de todos los servicios: prueba las consultas en orden y corta apenas hay una
        coincidencia buena (SCORE_OK). `fetch(q)` hace una consulta y devuelve canciones; se juntan sin
        repetir en `found` (que puede venir con resultados de antes, como los de ISRC).
        Si una consulta falla (`recoverable`) se anota y se sigue con la próxima; los errores de `fatal`
        (sesión vencida, el servicio pidió esperar) cortan todo: si no, cada tema saldría "no encontrado"."""
        found = {} if found is None else found
        tried = answered = 0
        for q in queries:
            tried += 1
            try:
                results = self.patient(fetch, q)
            except fatal:
                raise
            except recoverable as e:
                log.warning("%s: falló la búsqueda %r: %s", self.name, q, e)
                continue
            answered += 1
            for c in results:
                if c.id:
                    found.setdefault(c.id, c)
            time.sleep(self.search_pause)
            if self.good_enough(song, found):
                break
        self._count_search_failure(tried > 0 and answered == 0)
        return found

    def patient(self, call: Callable, *args):
        """Hace un pedido; si el servicio pide frenar (muy común al marcar miles de "Me gusta" o buscar
        mucho), espera y reintenta unas veces en vez de dar el tema por fallado."""
        for wait in RATE_LIMIT_WAITS:
            try:
                return call(*args)
            except Exception as e:
                asked = rate_limit_pause(e)
                if asked is None:
                    raise
                self.pause(min(asked or wait, MAX_RATE_LIMIT_WAIT))
        return call(*args)

    # ---- esperas: siempre se avisan (la persona ve una cuenta regresiva) y se pueden cancelar

    _listener: Callable[[dict], None] | None = None
    _cancel: threading.Event | None = None

    def listen(self, emit: Callable[[dict], None] | None, cancel: threading.Event | None = None) -> None:
        """Mientras dura una tarea: a dónde avisar las esperas y qué mirar para cortarlas. (None, None) al final."""
        self._listener, self._cancel = emit, cancel

    def pause(self, seconds: float, why: str = "rate") -> None:
        """Espera lo que hace falta avisando por qué: "rate" (el servicio pidió una pausa) o "retry"
        (contestó mal y se reintenta). Mira Cancelar cada medio segundo."""
        log.warning("%s: %s, espero %s s", self.name, why, round(seconds, 1))
        if self._listener is not None:
            self._listener({"type": "waiting", "service": self.name, "why": why, "seconds": round(seconds)})
        left = float(seconds)
        while left > 0:
            if self._cancel is not None and self._cancel.is_set():
                raise Cancelled()
            step = min(0.5, left)
            time.sleep(step)
            left -= step
        if self._listener is not None:
            self._listener({"type": "waited", "service": self.name})

    def _count_search_failure(self, failed: bool) -> None:
        """Una búsqueda que falla de vez en cuando se saltea. Pero si varios temas seguidos fallan en
        todas sus consultas, el servicio no está contestando (sesión vencida, sin internet): seguir
        marcaría cada tema "no encontrado". Se corta antes de cambiar nada (la transferencia agrega
        recién al final) y se olvida la sesión en memoria, así Conexiones vuelve a comprobarla."""
        self._failed_in_a_row = getattr(self, "_failed_in_a_row", 0) + 1 if failed else 0
        if self._failed_in_a_row >= FAILURES_IN_A_ROW:
            self._failed_in_a_row = 0
            self.forget_session()
            raise ConnectorError(tr("err.search_failing", service=self.name))

    def reset_search_failures(self) -> None:
        """Al empezar una transferencia: lo que falló en una anterior no cuenta."""
        self._failed_in_a_row = 0

    def forget_session(self) -> None:
        """Olvida la sesión que tiene en memoria (no la guardada): la próxima vez se vuelve a comprobar."""

    @staticmethod
    def good_enough(song: Song, found: dict[str, Song]) -> bool:
        return bool(found) and best_match(song, list(found.values()))[1] >= SCORE_OK

    def login_steps(self, browser: Browser | None) -> list[str]:
        """Lo que la persona hace mientras se conecta, una acción por paso: claves de la sección "ui" del
        diccionario (pueden usar {browser}). La UI las numera debajo del servicio mientras espera."""
        return []

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
