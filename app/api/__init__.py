"""JS ⇄ Python bridge: the methods the UI calls (`window.pywebview.api.<method>`).

Everything returns plain data (dict/list/str). Errors come back as {"error": "message"} to be
shown as they are. What takes time (sign-ins, transfers, finding or removing duplicates) runs in
the background: it returns {"job": id} and the UI reads the progress with `poll(id, since)`.

pywebview exposes this object's public attributes: that's why all state starts with "_".
"""
from __future__ import annotations

from app.api.base import KEEP_RESULTS
from app.api.duplicates import DuplicatesApi
from app.api.history import HistoryApi
from app.api.services import ServicesApi
from app.api.settings import SettingsApi
from app.api.transfers import TransfersApi

__all__ = ["Api", "KEEP_RESULTS"]


class Api(SettingsApi, ServicesApi, TransfersApi, DuplicatesApi, HistoryApi):
    """One object for the page, built by topic: each file in this package is one part."""
