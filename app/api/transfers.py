"""Transferir: empezar, agregar después lo que la persona decide y registrar en el historial."""
from __future__ import annotations

import logging
import sqlite3

from app.api.base import ApiBase, _artist, _remember
from app.connectors import registry
from app.connectors.base import Connector, ConnectorError
from app.core.matching import DOUBTFUL, NOT_FOUND, OK
from app.core.transfer import (ALREADY, ALREADY_THERE, DOUBTFUL_SKIPPED, REPEATED, REPEATED_IN_SOURCE, PartialTransfer,
                               Target, TargetResult, TransferResult, already_there, ensure_playlist, plan_target,
                               run_transfer, song_dict, summary)
from app.i18n import t as tr
from app.jobs import unexpected_message
from app.storage.history import Operation, TrackRecord
from app.storage.progress import SearchProgress

log = logging.getLogger(__name__)


class TransfersApi(ApiBase):

    def start_transfer(self, source: str, target: str, source_id: str, targets: list[dict], dry_run: bool = False) -> dict:
        """Los "?" (pueden ser otra versión) no se agregan solos: el usuario decide al final con
        `add_doubtful`."""
        if source == target:
            return {"error": tr("err.same_service")}
        if not targets:
            return {"error": tr("err.choose_target")}
        p = self._prefs()

        def work(emit, cancel):
            src, dst = self._connector(source), self._connector(target)
            tgts = [Target(str(x.get("name") or "").strip() or tr("playlist.default_name"), x.get("id") or None) for x in targets]
            try:
                result = run_transfer(src, dst, source_id, tgts, dry_run=bool(dry_run), emit=emit, cancel=cancel,
                                      score_ok=p["score_ok"], score_min=p["score_min"],
                                      progress=SearchProgress(self._data_dir / "busquedas", source, source_id, target))
            except PartialTransfer as partial:          # lo que ya se pasó queda en el historial
                if not partial.result.dry_run:
                    self._record_transfer(partial.result)
                cause = partial.__cause__
                detail = str(cause) if isinstance(cause, ConnectorError) else unexpected_message(cause)
                raise ConnectorError(tr("err.partial_transfer", name=partial.failed, detail=detail)) from cause
            if not result.dry_run:
                self._record_transfer(result)
            key = f"transfer-{next(self._ids)}"
            _remember(self._transfers, key, result)      # para "agregar la versión de otro artista"
            return {"transfer": key, "source_name": result.source_name, "dry_run": result.dry_run, **self._outcome(result)}

        return self._start("transfer", work)

    @staticmethod
    def _outcome(result: TransferResult) -> dict:
        """Las cantidades de la pantalla de resultado (cambian si después se agrega algo)."""
        return {"summary": summary(result),
                "targets": [{"name": t.target.name, "id": t.target.id, "url": t.url, "added": len(t.added),
                             "already": len(t.already), "versions": already_there(t) - len(t.already),
                             "created": t.created}
                            for t in result.targets]}

    def add_alternative(self, transfer: str, n: int, k: int) -> dict:
        """El usuario eligió agregar la versión de otro artista (tema `n`, alternativa `k`)."""
        result = self._transfers.get(transfer)
        if result is None or result.dry_run:
            return {"error": tr("err.transfer_gone")}
        track = next((t for t in result.tracks if t.n == int(n)), None)
        if track is None or not (0 <= int(k) < len(track.alternatives)):
            return {"error": tr("err.version_not_found")}
        if track.status != NOT_FOUND:        # ya se eligió una: un tema no se agrega dos veces
            return {"error": tr("err.version_not_found")}
        alt = track.alternatives[int(k)]

        def work(emit, cancel):
            dst = self._connector(result.target_service)
            done = []
            for t in result.targets:
                self._ensure_playlist(dst, t, result)
                dst.add(t.target.id, [alt])
                t.added.append(alt)
                done.append(t.target.name)
                self._amend_history(t, [TrackRecord(track.n, OK, track.source.title, _artist(track.source), alt.title, _artist(alt))],
                                    added=1, not_found=-1)
            track.status, track.match = OK, alt
            return {"added": song_dict(alt), "names": done, **self._outcome(result)}
        return self._start("alternative", work)

    def add_doubtful(self, transfer: str, ns: list[int]) -> dict:
        """El usuario eligió agregar igual temas "?" que no se habían agregado (uno o todos).
        Antes de agregar vuelve a leer cada destino: lo que ya está no se agrega dos veces."""
        result = self._transfers.get(transfer)
        if result is None or result.dry_run:
            return {"error": tr("err.transfer_gone")}
        wanted = {int(n) for n in ns}
        tracks = [t for t in result.tracks if t.n in wanted and t.status == DOUBTFUL_SKIPPED and t.match]
        if not tracks:
            return {"error": tr("err.version_not_found")}

        def work(emit, cancel):
            dst = self._connector(result.target_service)
            added_total = already_total = 0
            added_ns: set[int] = set()
            for t in result.targets:
                current: list = []
                if t.target.id is not None:
                    _, current = dst.read(t.target.id)
                to_add, already, versions = plan_target([x.match for x in tracks], current)
                if to_add:
                    self._ensure_playlist(dst, t, result)
                    dst.add(t.target.id, to_add)
                present = {id(m) for m in to_add}
                records = [TrackRecord(x.n, DOUBTFUL if id(x.match) in present else ALREADY, x.source.title,
                                       _artist(x.source), x.match.title, _artist(x.match), round(x.score, 3))
                           for x in tracks]
                t.added.extend(to_add)
                t.already.extend(already)
                t.versions.extend(versions)
                self._amend_history(t, records, added=len(to_add), doubtful=len(to_add),
                                    already=len(already) + len(versions), skipped=-len(tracks))
                added_total += len(to_add)
                added_ns |= {x.n for x in tracks if id(x.match) in present}
                already_total += len(already) + len(versions)
            for x in tracks:              # ya no se ofrecen de nuevo
                x.status = DOUBTFUL
            return {"ns": [x.n for x in tracks], "added_ns": sorted(added_ns), "added": added_total,
                    "already": already_total,
                    "url": next((t.url for t in result.targets if t.url), None), **self._outcome(result)}
        return self._start("add_doubtful", work)

    @staticmethod
    def _ensure_playlist(dst: Connector, t: TargetResult, result: TransferResult) -> None:
        """Al agregar después (un "?" u otro artista): si la playlist nueva no se había creado porque no
        había nada para agregar, se crea ahora."""
        if ensure_playlist(dst, t.target, registry.name(result.source_service)):
            t.created, t.url = True, dst.url(t.target.id)

    @staticmethod
    def _transfer_op(result: TransferResult, t: TargetResult, **counts) -> Operation:
        """La operación del historial para una playlist de destino de una transferencia."""
        return Operation(kind="transfer", source=result.source_service, source_name=result.source_name,
                         target=result.target_service, target_name=t.target.name, target_url=t.url, **counts)

    @classmethod
    def _transfer_operations(cls, result: TransferResult) -> list[tuple[Operation, list[TrackRecord]]]:
        """Una operación por playlist de destino, con el estado de cada tema respecto de esa playlist.
        La usan el historial y el reporte, así los dos dicen exactamente lo mismo."""
        s = summary(result)
        out = []
        for t in result.targets:
            already_ids = {id(m) for m in t.already} | {id(m) for m, _, why in t.versions if why == ALREADY_THERE}
            repeated_ids = {id(m) for m, _, why in t.versions if why == REPEATED_IN_SOURCE}
            tracks = []
            for track in result.tracks:
                status = track.status
                if track.match is not None and id(track.match) in already_ids:
                    status = ALREADY
                elif track.match is not None and id(track.match) in repeated_ids:
                    status = REPEATED
                tracks.append(TrackRecord(track.n, status, track.source.title, _artist(track.source),
                                          track.match.title if track.match else None, _artist(track.match) or None,
                                          round(track.score, 3)))
            op = cls._transfer_op(result, t, added=len(t.added), already=already_there(t),
                                  doubtful=s["doubtful"], not_found=s["not_found"], skipped=s["skipped"])
            out.append((op, tracks))
        return out

    def _record_transfer(self, result: TransferResult) -> None:
        """El historial es un registro: si no se puede escribir, la transferencia igual salió bien."""
        for t, (op, tracks) in zip(result.targets, self._transfer_operations(result)):
            try:
                t.history_id = self._history.record(op, tracks)
            except (sqlite3.Error, OSError):
                log.exception("No pude guardar en el historial")

    def _amend_history(self, t: TargetResult, tracks: list[TrackRecord], **deltas: int) -> None:
        """Lo agregado después es parte de la misma transferencia: corrige su fila, no suma otra."""
        if t.history_id is None:
            return
        try:
            self._history.amend(t.history_id, tracks, **deltas)
        except (sqlite3.Error, OSError):
            log.exception("No pude guardar en el historial")
