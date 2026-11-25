"""Long tasks in the background, with an event queue the UI reads periodically.

One task runs at a time: the Tidal and YouTube Music libraries aren't meant to be used from
several threads at once, and the UI doesn't allow starting another while one is running.
"""
from __future__ import annotations

import itertools
import json
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

KEEP_FINISHED = 20      # finished tasks that are kept (each one keeps its events)

# Errors meant to be shown as they are; any other one is shown with a generic message
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
    """What the user sees for an unexpected error: no internet is said as such; the rest, with its detail."""
    if is_network_error(e):
        return tr("err.no_network_generic")
    if isinstance(e, json.JSONDecodeError):          # the service answered something it shouldn't have
        return tr("err.bad_answer")
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
        """`fn(emit, cancel)` runs in another thread. What it returns becomes the result."""
        with self._lock:
            running = next((j for j in self._jobs.values() if j.state == RUNNING), None)
            if running:
                raise BusyError(tr("err.busy", kind=tr(f"job.{running.kind}")))
            for old in [j.id for j in self._jobs.values()][:-KEEP_FINISHED or None]:   # without growing forever
                del self._jobs[old]
                self._threads.pop(old, None)
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
            except Exception as e:
                # JSONDecodeError is a ValueError, but its text means nothing to anyone
                if isinstance(e, USER_ERRORS) and not isinstance(e, json.JSONDecodeError):
                    message = str(e)
                else:                   # unexpected error: logged in full and reported briefly
                    log.error("%s failed:\n%s", job.id, traceback.format_exc())
                    message = unexpected_message(e)
                with job.lock:
                    job.error, job.state = message, ERROR

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
        """For the tests: waits until it finishes."""
        t = self._threads.get(job_id)
        if t is not None:
            t.join(timeout)
        return self.poll(job_id)
