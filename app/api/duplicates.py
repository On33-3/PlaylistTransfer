"""Quitar repetidos: buscar y, con confirmación, quitar."""
from __future__ import annotations

from app.api.base import ApiBase, _artist, _remember
from app.connectors.base import ConnectorError
from app.core.matching import find_duplicates
from app.core.models import Duplicate
from app.core.transfer import song_dict
from app.i18n import t as tr
from app.storage.history import Operation, TrackRecord


class DuplicatesApi(ApiBase):

    def scan_duplicates(self, key: str, playlist_id: str) -> dict:
        def work(emit, cancel):
            c = self._connector(key)
            emit({"type": "reading", "service": c.name})
            name, songs = c.read(playlist_id)
            dups = find_duplicates(songs)
            scan_id = f"scan-{next(self._ids)}"
            _remember(self._scans, scan_id, {"key": key, "playlist_id": playlist_id, "name": name, "songs": songs, "dups": dups})
            return {"scan": scan_id, "name": name, "total": len(songs),
                    "duplicates": [{"i": i, "index": d.index, "kind": d.kind, "song": song_dict(d.song),
                                    "kept": song_dict(d.kept),
                                    # Spotify: la que queda se quita y se vuelve a agregar (se avisa antes)
                                    "readd": c.removes_all_copies and d.song.id is not None and d.song.id == d.kept.id}
                                   for i, d in enumerate(dups)]}
        return self._start("scan", work)

    def remove_duplicates(self, scan_id: str, selected: list[int], confirmed: bool = False) -> dict:
        """Quita los repetidos elegidos. Solo con `confirmed=True`, que la UI manda después del
        diálogo de confirmación. Antes de quitar, vuelve a leer la playlist: si cambió, no toca nada."""
        if confirmed is not True:
            return {"error": tr("err.need_confirmation")}
        scan = self._scans.get(scan_id)
        if scan is None:
            return {"error": tr("err.scan_again")}
        try:
            chosen: list[Duplicate] = [scan["dups"][int(i)] for i in dict.fromkeys(selected)]
        except (IndexError, ValueError, TypeError):
            return {"error": tr("err.bad_selection")}
        if not chosen:
            return {"error": tr("err.nothing_selected")}

        def work(emit, cancel):
            c = self._connector(scan["key"])
            name, now = c.read(scan["playlist_id"])
            before = scan["songs"]
            unchanged = len(now) == len(before) and all(
                (a.id, a.set_id) == (b.id, b.set_id) for a, b in zip(now, before))
            if not unchanged:
                raise ConnectorError(tr("err.playlist_changed"))
            c.remove(scan["playlist_id"], chosen, confirmed=True)
            self._scans.pop(scan_id, None)
            self._history.record(
                Operation(kind="dedupe", source=c.key, source_name=name, removed=len(chosen)),
                [TrackRecord(n, "quitado", d.song.title, _artist(d.song), d.kept.title, _artist(d.kept))
                 for n, d in enumerate(chosen, 1)])
            return {"removed": len(chosen), "name": name}
        return self._start("remove", work)
