"""Lo ya buscado en una transferencia que no terminó: el servicio pidió una pausa de horas, se canceló, se
cortó internet. La próxima vez que se transfiere la misma playlist al mismo servicio, sigue desde ahí en
vez de buscar todo de nuevo (con miles de temas, volver a empezar chocaba otra vez con el mismo límite).

Un archivo por recorrido (servicio de origen, playlist, servicio de destino) en
%LOCALAPPDATA%\\PlaylistTransfer\\busquedas\\. Se borra cuando la transferencia termina bien; si no, vence
a los KEEP_DAYS días (el catálogo del servicio cambia). Solo guarda la mejor coincidencia de cada tema y
su puntaje: si cambian los umbrales de Ajustes, se vuelve a clasificar con los nuevos.
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
SAVE_EVERY = 20      # temas buscados entre una escritura y la siguiente (si la app se cierra, se pierden pocos)


def _song(d: dict | None) -> Song | None:
    if not d:
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
        except (OSError, ValueError, KeyError, TypeError) as e:     # un archivo roto no impide transferir
            log.warning("No pude leer lo buscado antes (%s): %s", self.path.name, e)
            return {}

    def get(self, song: Song) -> tuple[Song | None, float, list[Song]] | None:
        """(mejor coincidencia, puntaje, alternativas) si ya se buscó; None si no."""
        d = self._songs.get(self.song_key(song))
        if d is None:
            return None
        return _song(d.get("match")), float(d.get("score") or 0), [a for a in map(_song, d.get("alts") or []) if a]

    def put(self, song: Song, match: Song | None, score: float, alts: list[Song]) -> None:
        self._songs[self.song_key(song)] = {"match": _plain(match), "score": round(score, 3),
                                            "alts": [_plain(a) for a in alts]}
        self._unsaved += 1
        if self._unsaved >= SAVE_EVERY:
            self.save()

    def done(self, songs: list[Song]) -> int:
        """Cuántos de estos temas ya están buscados."""
        return sum(1 for s in songs if self.song_key(s) in self._songs)

    def save(self) -> None:
        if not self._songs:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"saved": datetime.now().isoformat(timespec="seconds"), "songs": self._songs},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)          # nunca queda un archivo a medio escribir
            self._unsaved = 0
        except OSError as e:
            log.warning("No pude guardar lo buscado: %s", e)

    def clear(self) -> None:
        self._songs, self._unsaved = {}, 0
        try:
            self.path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("No pude borrar lo buscado: %s", e)
