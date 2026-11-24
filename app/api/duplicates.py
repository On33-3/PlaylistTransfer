"""Quitar repetidos: buscar (en una playlist o en todas) y, con confirmación, quitar."""
from __future__ import annotations

import itertools
import time

from app.api.base import ApiBase, _artist, _remember
from app.connectors.base import Connector, ConnectorError, NoNetwork, NotConnectedError
from app.core.matching import OTHER_LENGTH, find_duplicates
from app.core.models import Cancelled, Duplicate
from app.core.transfer import song_dict
from app.i18n import t as tr
from app.storage.history import Operation, TrackRecord

ALL = "__all__"      # la UI pide revisar todas las playlists del servicio

# Una revisión de varias playlists se recuerda entera (como una sola), con un número por playlist
_parts = itertools.count(1)


def _longer(d: Duplicate) -> int | None:
    a, b = d.song.duration, d.kept.duration
    return a - b if d.kind != "misma" and a and b and abs(a - b) > OTHER_LENGTH else None


class DuplicatesApi(ApiBase):

    def scan_duplicates(self, key: str, playlist_id: str) -> dict:
        """Busca repetidos dentro de una playlist o, con ALL, dentro de cada una (de a una, con la pausa de
        siempre entre playlists: no se apura a los servicios). Avisa qué playlist está revisando."""
        def work(emit, cancel):
            c = self._connector(key)
            if playlist_id == ALL:
                # Con menos de 2 temas no puede haber repetidos (las de cantidad desconocida se revisan)
                todo = [(p.id, p.name) for p in c.list_playlists() if p.count is None or p.count >= 2]
            else:
                todo = [(playlist_id, None)]
            emit({"type": "scan_list", "names": [n for _, n in todo]})
            batch, groups, clean, failed, checked_songs = {}, [], [], [], 0
            for k, (pid, name) in enumerate(todo):
                if cancel.is_set():
                    raise Cancelled()
                if k:
                    time.sleep(c.search_pause)
                emit({"type": "scan_start", "k": k, "name": name})
                try:
                    group = self._scan_one(c, key, pid, batch)
                except (NotConnectedError, NoNetwork):
                    raise                                   # sin sesión o sin internet: no sirve seguir
                except ConnectorError as e:                 # una playlist que no se puede leer no frena las demás
                    if playlist_id != ALL:
                        raise
                    failed.append({"name": name, "error": str(e)})
                    emit({"type": "scan_result", "k": k, "error": str(e)})
                    continue
                checked_songs += group["total"]
                emit({"type": "scan_result", "k": k, "found": len(group["duplicates"]), "name": group["name"]})
                (groups if group["duplicates"] else clean).append(group if group["duplicates"] else group["name"])
            batch_id = f"scan-{next(self._ids)}"
            _remember(self._scans, batch_id, batch)
            return {"batch": batch_id, "checked": len(todo), "songs_checked": checked_songs, "groups": groups,
                    "clean": clean, "failed": failed}
        return self._start("scan", work)

    def _scan_one(self, c: Connector, key: str, playlist_id: str, batch: dict) -> dict:
        name, songs = c.read(playlist_id)
        dups = find_duplicates(songs)
        scan_id = f"part-{next(_parts)}"
        if dups:                                        # solo se recuerda lo que se puede quitar
            batch[scan_id] = {"key": key, "playlist_id": playlist_id, "name": name, "songs": songs, "dups": dups}
        return {"scan": scan_id, "playlist_id": playlist_id, "name": name, "total": len(songs),
                "duplicates": [{"i": i, "index": d.index, "kind": d.kind, "song": song_dict(d.song),
                                "kept": song_dict(d.kept),
                                # Por qué viene sin tildar: dura muy distinto (segundos de más o de menos)
                                "longer": _longer(d),
                                # Spotify: la que queda se quita y se vuelve a agregar (se avisa antes)
                                "readd": c.removes_all_copies and d.song.id is not None and d.song.id == d.kept.id}
                               for i, d in enumerate(dups)]}

    def _find_scan(self, scan_id: str) -> tuple[dict, dict] | None:
        for batch in reversed(list(self._scans.values())):
            if scan_id in batch:
                return batch, batch[scan_id]
        return None

    def remove_duplicates(self, scan_id: str, selected: list[int], confirmed: bool = False) -> dict:
        """Quita los repetidos elegidos de una playlist (ver remove_many)."""
        return self.remove_many([{"scan": scan_id, "selected": selected}], confirmed)

    def remove_many(self, choices: list[dict], confirmed: bool = False) -> dict:
        """Quita los repetidos elegidos, en una o varias playlists. Solo con `confirmed=True`, que la UI manda
        después del diálogo de confirmación. Antes de quitar de cada una, vuelve a leerla: si cambió, no toca
        nada de esa. Después vuelve a revisar las playlists que cambiaron, así la pantalla dice lo que quedó."""
        if confirmed is not True:
            return {"error": tr("err.need_confirmation")}
        plan = []
        for choice in choices or []:
            found = self._find_scan(str((choice or {}).get("scan")))
            if found is None:
                return {"error": tr("err.scan_again")}
            batch, scan = found
            try:
                chosen: list[Duplicate] = [scan["dups"][int(i)] for i in dict.fromkeys(choice.get("selected") or [])]
            except (IndexError, ValueError, TypeError):
                return {"error": tr("err.bad_selection")}
            if chosen:
                plan.append((choice["scan"], batch, scan, chosen))
        if not plan:
            return {"error": tr("err.nothing_selected")}

        def work(emit, cancel):
            removed, names, groups = 0, [], []
            for k, (scan_id, batch, scan, chosen) in enumerate(plan):
                c = self._connector(scan["key"])
                emit({"type": "removing", "k": k, "of": len(plan), "name": scan["name"]})
                name, now = c.read(scan["playlist_id"])
                before = scan["songs"]
                unchanged = len(now) == len(before) and all(
                    (a.id, a.set_id) == (b.id, b.set_id) for a, b in zip(now, before))
                if not unchanged:
                    if removed:            # lo de las anteriores ya se hizo: se dice qué sí y qué no
                        raise ConnectorError(tr("err.partial_dedupe", done=", ".join(names), name=name))
                    raise ConnectorError(tr("err.playlist_changed"))
                c.remove(scan["playlist_id"], chosen, confirmed=True)
                batch.pop(scan_id, None)
                self._history.record(
                    Operation(kind="dedupe", source=c.key, source_name=name, removed=len(chosen)),
                    [TrackRecord(n, "quitado", d.song.title, _artist(d.song), d.kept.title, _artist(d.kept))
                     for n, d in enumerate(chosen, 1)])
                removed += len(chosen)
                names.append(name)
                groups.append(self._scan_one(c, scan["key"], scan["playlist_id"], batch))   # cómo quedó
            return {"removed": removed, "name": names[0], "names": names, "groups": groups}
        return self._start("remove", work)
