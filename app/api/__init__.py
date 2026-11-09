"""Puente JS ⇄ Python: los métodos que llama la UI (`window.pywebview.api.<método>`).

Todo devuelve datos simples (dict/list/str). Los errores vuelven como {"error": "mensaje"} para
mostrarlos tal cual. Lo que tarda (logins, transferencias, buscar o quitar repetidos) corre en
segundo plano: devuelve {"job": id} y la UI lee el avance con `poll(id, desde)`.

pywebview expone los atributos públicos de este objeto: por eso todo el estado va con "_".
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
    """Un solo objeto para la página, armado por tema: cada archivo de este paquete es una parte."""
