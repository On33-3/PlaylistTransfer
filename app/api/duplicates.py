"""Remove duplicates: find them (in one playlist or in all) and, with confirmation, remove them."""
from __future__ import annotations

import logging
import time

from app.api.base import ApiBase, _artist, _remember
from app.connectors.base import Connector, ConnectorError, NoNetwork, NotConnectedError
from app.core.matching import OTHER_LENGTH, find_duplicates
from app.core.models import Cancelled, Duplicate
from app.core.transfer import song_dict
from app.i18n import t as tr
from app.jobs import unexpected_message
from app.storage.history import Operation, TrackRecord

log = logging.getLogger(__name__)
ALL = "__all__"      # the UI asks to check every playlist of the service


def _longer(d: Duplicate) -> int | None:
    a, b = d.song.duration, d.kept.duration
    return a - b if d.kind != "misma" and a and b and abs(a - b) > OTHER_LENGTH else None


class DuplicatesApi(ApiBase):

    def scan_duplicates(self, key: str, playlist_id: str) -> dict:
        """Finds duplicates inside one playlist or, with ALL, inside each one (one at a time, with the usual
        pause between playlists: the services are not rushed). Says which playlist it is checking."""
        def work(emit, cancel):
            c = self._connector(key)
            if playlist_id == ALL:
                # With fewer than 2 songs there can't be duplicates (those with an unknown count are checked)
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
                except (NotConnectedError, NoNetwork, Cancelled):
                    raise                                   # no session or no internet: no point going on
                except Exception as e:                      # a playlist that can't be read doesn't stop the others
                    if playlist_id != ALL:
                        raise
                    # Any error, not only the known ones: an unexpected one in ytmusicapi (a mix saved to the
                    # library) stopped the whole check and lost what had been found
                    if isinstance(e, ConnectorError):
                        why = str(e)
                    else:
                        log.warning("Couldn't check %s: %s", name, unexpected_message(e))
                        why = tr("err.playlist_unreadable", service=c.name)
                    failed.append({"name": name, "error": why})
                    emit({"type": "scan_result", "k": k, "error": why})
                    continue
                checked_songs += group["total"]
                emit({"type": "scan_result", "k": k, "found": len(group["duplicates"]), "name": group["name"]})
                if group["duplicates"]:
                    groups.append(group)
                else:
                    clean.append(group["name"])
            batch_id = f"scan-{next(self._ids)}"
            _remember(self._scans, batch_id, batch)
            return {"batch": batch_id, "checked": len(todo), "songs_checked": checked_songs, "groups": groups,
                    "clean": clean, "failed": failed}
        return self._start("scan", work)

    def _scan_one(self, c: Connector, key: str, playlist_id: str, batch: dict) -> dict:
        name, songs = c.read(playlist_id)
        dups = find_duplicates(songs)
        # A check of several playlists is kept whole (one group), with one number per playlist
        scan_id = f"part-{next(self._ids)}"
        if dups:                                        # only what can be removed is kept
            batch[scan_id] = {"key": key, "playlist_id": playlist_id, "name": name, "songs": songs, "dups": dups}
        return {"scan": scan_id, "playlist_id": playlist_id, "name": name, "total": len(songs),
                "duplicates": [{"i": i, "index": d.index, "kind": d.kind, "song": song_dict(d.song),
                                "kept": song_dict(d.kept),
                                # Why it comes unticked: very different length (seconds more or less)
                                "longer": _longer(d),
                                # Spotify: the one that stays is removed and added back (announced beforehand)
                                "readd": c.removes_all_copies and d.song.id is not None and d.song.id == d.kept.id}
                               for i, d in enumerate(dups)]}

    def _find_scan(self, scan_id: str) -> tuple[dict, dict] | None:
        for batch in reversed(list(self._scans.values())):
            if scan_id in batch:
                return batch, batch[scan_id]
        return None

    def remove_duplicates(self, scan_id: str, selected: list[int], confirmed: bool = False) -> dict:
        """Removes the chosen duplicates from one playlist (see remove_many)."""
        return self.remove_many([{"scan": scan_id, "selected": selected}], confirmed)

    def remove_many(self, choices: list[dict], confirmed: bool = False) -> dict:
        """Removes the chosen duplicates, in one or several playlists. Only with `confirmed=True`, which the UI
        sends after the confirmation dialog. Before removing from each one, it reads it again: if it changed,
        nothing in it is touched. Afterwards it checks the changed playlists again, so the screen shows what's left."""
        if confirmed is not True:
            return {"error": tr("err.need_confirmation")}
        if not isinstance(choices, list) or not all(isinstance(c, dict) for c in choices):
            return {"error": tr("err.bad_selection")}
        wanted: dict[str, list[int]] = {}               # the same check twice counts once
        for choice in choices:
            selected = choice.get("selected")
            if not isinstance(selected, list) or not all(type(i) is int and i >= 0 for i in selected):
                return {"error": tr("err.bad_selection")}
            picks = wanted.setdefault(str(choice.get("scan")), [])
            picks += [i for i in selected if i not in picks]
        plan = []
        for scan_id, picks in wanted.items():
            found = self._find_scan(scan_id)
            if found is None:
                return {"error": tr("err.scan_again")}
            batch, scan = found
            if any(i >= len(scan["dups"]) for i in picks):
                return {"error": tr("err.bad_selection")}
            if picks:
                plan.append((scan_id, batch, scan, [scan["dups"][i] for i in picks]))
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
                    if removed:            # the earlier ones are already done: it says what was and what wasn't
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
                groups.append(self._scan_one(c, scan["key"], scan["playlist_id"], batch))   # how it ended up
            return {"removed": removed, "name": names[0], "names": names, "groups": groups}
        return self._start("remove", work)
