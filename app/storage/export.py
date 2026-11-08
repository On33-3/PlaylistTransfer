"""El historial en un archivo de texto liviano, para que lo lea una persona o una IA.

Texto plano (.txt, UTF-8) con títulos y listas al estilo Markdown: se abre con doble clic en el
Bloc de notas, pesa unos pocos KB y una IA lo entiende sin preparación. Siempre a pedido del
usuario (Historial › Descargar historial), nunca solo. Sale en el idioma de la app.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.i18n import t as tr
from app.storage.history import History, Operation, TrackRecord


@dataclass
class ReportOperation:
    op: Operation
    tracks: list[TrackRecord]


def from_history(history: History, kind: str | None = None) -> list[ReportOperation]:
    """Las operaciones del historial, la más vieja primero."""
    return [ReportOperation(op, history.tracks(op.id)) for op in reversed(history.list()) if kind in (None, op.kind)]


def _when(iso: str) -> str:
    """"2026-11-08T01:00:59" → "2026-11-08 01:00"."""
    return (iso or "").replace("T", " ")[:16]


def _counts(op: Operation) -> str:
    if op.kind == "dedupe":
        return tr("report.removed", n=op.removed)
    parts = [tr("report.added", n=op.added)]
    for key, n in (("report.already", op.already), ("report.skipped", op.skipped), ("report.not_found", op.not_found)):
        if n:
            parts.append(tr(key, n=n))
    return ", ".join(parts)


def _track_line(t: TrackRecord, kind: str) -> str:
    line = f"- {tr(f'report.status.{t.status}')}: {t.title} — {t.artist or '?'}"
    if t.match_title and t.status != "no_encontrado":
        other = f"{t.match_title} — {t.match_artist or '?'}"
        if kind == "dedupe":
            line += f" · {tr('report.kept', song=other)}"
        elif (t.match_title, t.match_artist) != (t.title, t.artist):
            line += f" → {other}"
    return line


def render(operations: list[ReportOperation], service_names: dict[str, str]) -> str:
    name = lambda key: service_names.get(key, key or "")   # noqa: E731
    lines = [f"# {tr('report.title')}",
             tr("report.downloaded", date=f"{datetime.now():%Y-%m-%d %H:%M}", n=len(operations)),
             "", tr("report.how_to_read"), ""]
    for item in operations:
        op = item.op
        if op.kind == "dedupe":
            title = tr("report.op_dedupe", name=op.source_name, service=name(op.source))
        else:
            title = tr("report.op_transfer", src=op.source_name, dst=op.target_name or "?",
                       src_service=name(op.source), dst_service=name(op.target))
        lines += [f"## {_when(op.created_at)} · {title}", _counts(op), ""]
        lines += [_track_line(t, op.kind) for t in item.tracks]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(path: Path, operations: Iterable[ReportOperation], service_names: dict[str, str]) -> int:
    """Escribe el archivo (en un .tmp que se renombra al final). Devuelve cuántas operaciones."""
    ops = list(operations)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(render(ops, service_names), encoding="utf-8")
    tmp.replace(path)
    return len(ops)
