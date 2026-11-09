"""El historial: listar, detalle y descarga."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.api.base import ApiBase
from app.connectors import registry
from app.i18n import t as tr
from app.storage.export import ReportOperation, from_history, write_report


class HistoryApi(ApiBase):

    def history(self, limit: int | None = None, kind: str | None = None) -> list[dict]:
        ops = [op for op in self._history.list() if kind in (None, "", op.kind)][:limit or None]
        tiles = self._history.tiles([op.id for op in ops], limit=60)
        out = []
        for op in ops:
            d = op.__dict__.copy()
            d["source_label"] = registry.name(op.source)
            d["target_label"] = registry.name(op.target)
            # El mosaico de cada fila: un cuadrado por tema (hasta 60) con su estado, y qué tema es
            # (para mostrarlo al pasar el mouse); "tiles_total" permite mostrar "+N" si hay más.
            info = tiles.get(op.id, {"statuses": [], "songs": [], "total": 0})
            d["tiles"], d["tile_songs"], d["tiles_total"] = info["statuses"], info["songs"], info["total"]
            out.append(d)
        return out

    def export_history(self, kind: str | None = None) -> dict:
        """Todo el historial (o solo transferencias / solo repetidos) en un .txt liviano, a pedido."""
        ops = from_history(self._history, kind=kind or None)
        if not ops:
            return {"error": tr("err.history_empty")}
        base = {"transfer": tr("file.history_transfers"), "dedupe": tr("file.history_duplicates")}.get(kind or "", tr("file.history"))
        return self._save_report(f"{base}-{datetime.now():%Y-%m-%d}.txt", ops)

    def _save_report(self, default_name: str, ops: list[ReportOperation]) -> dict:
        """Abre "Guardar como" y escribe el .txt. {"cancelled": True} si el usuario cerró el diálogo."""
        if self._save_dialog is None:
            return {"error": tr("err.no_save_dialog")}
        chosen = self._save_dialog(default_name)
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        if not chosen:
            return {"cancelled": True}
        path = Path(chosen)
        if path.suffix.lower() != ".txt":
            path = path.with_name(path.name + ".txt")
        try:
            count = write_report(path, ops, registry.names())
        except OSError as e:
            return {"error": tr("err.save_failed", detail=e.strerror or e)}
        self._last_export = path
        return {"path": str(path), "name": path.name, "operations": count}

    def open_export(self) -> dict:
        """Abre el último historial descargado (con el Bloc de notas u otro editor). Solo ese archivo."""
        if self._last_export is None or not self._last_export.exists():
            return {"error": tr("err.export_first")}
        self._open_file(self._last_export)
        return {"ok": True}

    def history_detail(self, op_id: int) -> list[dict]:
        return [t.__dict__ for t in self._history.tracks(int(op_id))]
