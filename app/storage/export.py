"""Reportes CSV para abrir con doble clic en Excel. Siempre a pedido del usuario, nunca solos.

Excel abre un CSV con el separador de listas de Windows: "," en inglés, ";" en castellano
(Argentina, España...). Si no coincide, todo queda en una sola columna. Por eso se usa el
formato de la compu que exporta. UTF-8 con BOM para que los acentos se vean bien.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.i18n import t as tr
from app.storage.history import History, TrackRecord

# Las columnas y los estados salen en el idioma de la app (app/locales, claves "csv.*")
HEADER_KEYS = ["date", "operation", "source_service", "source_playlist", "target_service", "target_playlist",
               "n", "status", "title", "artist", "match_title", "match_artist", "score"]


def header() -> list[str]:
    return [tr(f"csv.{k}") for k in HEADER_KEYS]


def status_label(status: str) -> str:
    return tr(f"csv.status.{status}")


def kind_label(kind: str) -> str:
    return tr(f"csv.kind.{kind}")

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class ReportOperation:
    """Lo mínimo para escribir una operación: sale del historial o de una transferencia recién hecha."""
    created_at: str
    kind: str
    source: str
    source_name: str
    target: str | None
    target_name: str | None
    tracks: list[TrackRecord]


def windows_number_format() -> tuple[str, str]:
    """(separador de listas, separador decimal) de la configuración regional de Windows."""
    sep, dec = ",", "."
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as k:
            sep = winreg.QueryValueEx(k, "sList")[0] or sep
            dec = winreg.QueryValueEx(k, "sDecimal")[0] or dec
    except (ImportError, OSError):
        pass
    if len(sep) != 1 or sep == dec:   # configuración rara: ";" es lo más seguro
        sep = ";"
    return sep, dec


def excel_date(iso: str) -> str:
    """"2026-11-08T01:00:59" → "2026-11-08 · 01:00".

    Con el "·" Excel lo deja como texto, tal cual. Si lo convierte a fecha: con la fecha y la hora
    muestra "########" (no entra en el ancho de la columna) y "08/11/2026" lo lee como 11 de agosto
    en un Windows en inglés. Probado con Excel 16. Empieza por el año, así se ordena bien."""
    day, _, time = (iso or "").partition("T")
    return f"{day} · {time[:5]}" if time else day


def safe_cell(value) -> str:
    """Un título que empieza con "=" o "+" Excel lo tomaría como fórmula: se le antepone un apóstrofo."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def from_history(history: History, kind: str | None = None) -> list[ReportOperation]:
    """Las operaciones del historial, la más vieja primero."""
    return [ReportOperation(op.created_at, op.kind, op.source, op.source_name, op.target, op.target_name,
                            history.tracks(op.id))
            for op in reversed(history.list()) if kind in (None, op.kind)]


def write_csv(path: Path, operations: Iterable[ReportOperation], service_names: dict[str, str],
              sep: str | None = None, decimal: str | None = None) -> int:
    """Una fila por tema. Devuelve cuántas filas escribió. Escribe en un .tmp y lo renombra al final."""
    if sep is None or decimal is None:
        sep, decimal = windows_number_format()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    rows = 0
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=sep, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header())
        for op in operations:
            date = excel_date(op.created_at)
            for t in op.tracks:
                score = "" if t.score is None else f"{t.score:.2f}".replace(".", decimal)
                w.writerow([date, kind_label(op.kind),
                            service_names.get(op.source, op.source), safe_cell(op.source_name),
                            service_names.get(op.target, op.target or ""), safe_cell(op.target_name),
                            t.n, status_label(t.status), safe_cell(t.title), safe_cell(t.artist),
                            safe_cell(t.match_title), safe_cell(t.match_artist), score])
                rows += 1
    tmp.replace(path)
    return rows
