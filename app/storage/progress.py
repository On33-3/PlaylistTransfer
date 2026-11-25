"""What was already searched in a transfer that didn't finish: the service asked for a pause of hours,
it was cancelled, the internet dropped. The next time the same playlist goes to the same service, it
continues from there instead of searching everything again (with thousands of songs, starting over hit
the same limit again).

One file per route (source service, playlist, destination service) in
%LOCALAPPDATA%\\PlaylistTransfer\\busquedas\\. It is deleted when the transfer ends well; otherwise it
expires after KEEP_DAYS days (the service's catalog changes). It only keeps each song's best match and
its score: if the thresholds in Settings change, it is classified again with the new ones.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.core.models import Song

log = logging.getLogger(__name__)

KEEP_DAYS = 7
SAVE_EVERY = 20      # songs searched between one write and the next (if the app closes, few are lost)


def _song(d) -> Song | None:
    if not isinstance(d, dict) or not isinstance(d.get("title"), str):
        return None
    fields = {f.name for f in dataclasses.fields(Song)}
    return Song(**{k: v for k, v in d.items() if k in fields})


def _plain(s: Song | None) -> dict | None:
    return dataclasses.asdict(s) if s is not None else None


class SearchProgress:
    def __init__(self, folder: Path, source: str, source_id: str, dest: str):
        key = hashlib.sha1(f"{source}|{source_id}|{dest}".encode("utf-8")).hexdigest()[:16]
        self.path = Path(folder) / f"{key}.json"
        self._songs: dict[str, dict] = self._load()
        self._unsaved = 0

    @staticmethod
    def song_key(s: Song) -> str:
        return s.id or f"{s.title}|{'|'.join(s.artists)}|{s.duration}"

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if datetime.now() - datetime.fromisoformat(data["saved"]) > timedelta(days=KEEP_DAYS):
                return {}
            return dict(data["songs"])
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, KeyError, TypeError) as e:     # a broken file doesn't prevent transferring
            log.warning("No pude leer lo buscado antes (%s): %s", self.path.name, e)
            return {}

    def get(self, song: Song) -> tuple[Song | None, float, list[Song]] | None:
        """(best match, score, alternatives) if already searched; None if not."""
        d = self._songs.get(self.song_key(song))
        try:
            match, score, alts = _song(d["match"]) if d["match"] else None, float(d["score"]), d.get("alts") or []
            if d["match"] and match is None or not isinstance(alts, list):
                raise ValueError("coincidencia rota")
        except (TypeError, ValueError, KeyError):         # nothing saved, or a broken entry: it is searched again
            return None
        return match, score, [a for a in map(_song, alts) if a]

    def put(self, song: Song, match: Song | None, score: float, alts: list[Song]) -> None:
        self._songs[self.song_key(song)] = {"match": _plain(match), "score": round(score, 3),
                                            "alts": [_plain(a) for a in alts]}
        self._unsaved += 1
        if self._unsaved >= SAVE_EVERY:
            self.save()

    def done(self, songs: list[Song]) -> int:
        """How many of these songs have already been searched."""
        return sum(1 for s in songs if self.song_key(s) in self._songs)

    def save(self) -> None:
        if not self._songs:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"saved": datetime.now().isoformat(timespec="seconds"), "songs": self._songs},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)          # a half-written file is never left behind
            self._unsaved = 0
        except OSError as e:
            log.warning("No pude guardar lo buscado: %s", e)

    def clear(self) -> None:
        self._songs, self._unsaved = {}, 0
        try:
            self.path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("No pude borrar lo buscado: %s", e)
