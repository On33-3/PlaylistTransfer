"""Tareas largas en segundo plano, con una cola de eventos que la UI lee periódicamente.

Corre una tarea por vez: las librerías de Tidal y YouTube Music no están pensadas para usarse
desde varios hilos a la vez, y la UI no deja empezar otra mientras hay una en curso.
"""
from __future__ import annotations

import itertools
import logging
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from app.connectors.base import ConnectorError, is_network_error
from app.auth.cdp_login import LoginError
from app.core.transfer import Cancelled
from app.i18n import t as tr

log = logging.getLogger(__name__)

RUNNING, DONE, ERROR, CANCELLED = "running", "done", "error", "cancelled"

# Errores pensados para mostrarse tal cual; cualquier otro se muestra con un mensaje genérico
USER_ERRORS = (ConnectorError, LoginError, ValueError)


@dataclass
class Job:
    id: str
    kind: str
    state: str = RUNNING
    events: list[dict] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


def unexpected_message(e: BaseException) -> str:
    """Lo que ve el usuario ante un error no previsto: sin internet se dice así; lo demás, con su detalle."""
    if is_network_error(e):
        return tr("err.no_network_generic")
    return tr("err.unexpected", detail=f"{type(e).__name__}: {e}")


class BusyError(Exception):
    pass


class Jobs:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def busy(self) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs.values() if j.state == RUNNING), None)

    def start(self, kind: str, fn: Callable[[Callable[[dict], None], threading.Event], Any]) -> str:
        """`fn(emit, cancel)` corre en otro hilo. Lo que devuelve queda como resultado."""
        with self._lock:
            running = next((j for j in self._jobs.values() if j.state == RUNNING), None)
            if running:
                raise BusyError(tr("err.busy", kind=tr(f"job.{running.kind}")))
            job = Job(f"{kind}-{next(self._ids)}", kind)
            self._jobs[job.id] = job

        def emit(event: dict) -> None:
            with job.lock:
                job.events.append(event)

        def run() -> None:
            try:
                result = fn(emit, job.cancel)
                with job.lock:
                    job.result, job.state = result, DONE
            except Cancelled:
                with job.lock:
                    job.state = CANCELLED
            except USER_ERRORS as e:
                with job.lock:
                    job.error, job.state = str(e), ERROR
            except Exception as e:      # error inesperado: se registra completo y se avisa corto
                log.error("Falló %s:\n%s", job.id, traceback.format_exc())
                with job.lock:
                    job.error = unexpected_message(e)
                    job.state = ERROR

        t = threading.Thread(target=run, name=job.id, daemon=True)
        self._threads[job.id] = t
        t.start()
        return job.id

    def poll(self, job_id: str, since: int = 0) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"state": ERROR, "error": tr("err.job_gone"), "events": [], "next": since}
        with job.lock:
            events = job.events[since:]
            return {"id": job.id, "kind": job.kind, "state": job.state, "events": events,
                    "next": since + len(events), "result": job.result if job.state == DONE else None,
                    "error": job.error}

    def cancel(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.cancel.set()

    def wait(self, job_id: str, timeout: float = 10) -> dict:
        """Para los tests: espera a que termine."""
        t = self._threads.get(job_id)
        if t is not None:
            t.join(timeout)
        return self.poll(job_id)
