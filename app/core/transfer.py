"""Orquesta una transferencia: leer el origen → buscar cada tema → ver qué ya está → agregar.

Misma lógica que `run()` del script de consola, pero sin imprimir: cada paso sale como un
evento (`emit`) para que la UI lo muestre en vivo. Nada se modifica hasta que terminaron todas
las búsquedas, así que cancelar durante la búsqueda nunca deja una playlist a medias.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.connectors.base import Connector, ConnectorError
from app.core.matching import (DOUBTFUL, NOT_FOUND, OK, SCORE_MIN, SCORE_OK, alternatives, best_match,
                               classify, same_song)
from app.core.models import Song
from app.core.models import Cancelled  # noqa: F401  (se importa también desde acá)
from app.i18n import t as tr

ALREADY = "ya_estaba"   # estado de un tema que ya estaba en el destino
# "?" (puede ser otra versión): no se agregan solos. Quedan con su coincidencia para que el
# usuario decida al final ("+ Agregar igual" / "Agregar los N…").
DOUBTFUL_SKIPPED = "dudoso_omitido"
ADDABLE = (OK, DOUBTFUL)
# Por qué no se agrega una versión (códigos internos, no se muestran): otra del mismo tema ya
# estaba en el destino, o el origen la tenía repetida
ALREADY_THERE, REPEATED_IN_SOURCE = "already_there", "repeated_in_source"

Emit = Callable[[dict], None]


@dataclass
class Target:
    name: str
    id: str | None = None      # None = playlist nueva (se crea solo si hay algo para agregar)


@dataclass
class TrackResult:
    n: int
    source: Song
    match: Song | None
    score: float
    status: str                                  # ok / dudoso / dudoso_omitido / no_encontrado
    alternatives: list[Song] = field(default_factory=list)


@dataclass
class TargetResult:
    target: Target
    added: list[Song] = field(default_factory=list)
    already: list[Song] = field(default_factory=list)            # el mismo tema ya estaba
    versions: list[tuple[Song, Song, str]] = field(default_factory=list)   # (match, el que estaba, por qué)
    created: bool = False
    url: str | None = None


@dataclass
class TransferResult:
    source_service: str
    source_name: str
    target_service: str
    dry_run: bool
    tracks: list[TrackResult]
    targets: list[TargetResult]


def song_dict(s: Song | None) -> dict | None:
    if s is None:
        return None
    return {"title": s.title, "artist": ", ".join(s.artists), "album": s.album, "duration": s.duration, "id": s.id}


def match_all(songs: list[Song], dest: Connector, emit: Emit, cancel: threading.Event | None = None,
              score_ok: float = SCORE_OK, score_min: float = SCORE_MIN,
              include_doubtful: bool = False) -> list[TrackResult]:
    results = []
    dest.reset_search_failures()
    for n, src in enumerate(songs, 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        emit({"type": "searching", "n": n, "song": song_dict(src)})
        candidates = dest.search(src)
        match, sc = best_match(src, candidates)
        status = classify(sc, score_ok, score_min) if match else NOT_FOUND
        if status == DOUBTFUL and not include_doubtful:
            status = DOUBTFUL_SKIPPED
        alts = alternatives(src, candidates) if status == NOT_FOUND else []
        r = TrackResult(n, src, match if status != NOT_FOUND else None, sc, status, alts)
        results.append(r)
        emit({"type": "track", "n": n, "status": status, "score": round(sc, 2), "song": song_dict(src),
              "match": song_dict(match) if status != NOT_FOUND else None,
              "alternatives": [song_dict(a) for a in alts]})
    return results


def plan_target(found: list[Song], current: list[Song]) -> tuple[list[Song], list[Song], list[tuple[Song, Song, str]]]:
    """(para agregar, ya estaban, otra versión ya estaba). Igual que el script de consola."""
    to_add: list[Song] = []
    already: list[Song] = []
    versions: list[tuple[Song, Song, str]] = []
    for m in found:
        twin = next((c for c in current if same_song(m, c)), None)
        if twin is None:
            twin = next((a for a in to_add if same_song(m, a)), None)
            if twin is not None:
                versions.append((m, twin, REPEATED_IN_SOURCE))
                continue
        elif twin.id == m.id:
            already.append(m)
            continue
        else:
            versions.append((m, twin, ALREADY_THERE))
            continue
        to_add.append(m)
    return to_add, already, versions


def ensure_playlist(dest: Connector, target: Target, source_service: str, public: bool = False) -> bool:
    """Una playlist nueva se crea recién cuando hay algo para agregar. True si la creó."""
    if target.id is not None:
        return False
    desc = tr("playlist.description", service=source_service, date=f"{datetime.now():%Y-%m-%d}")
    target.id = dest.create(target.name, desc, public)
    return True


def run_transfer(source: Connector, dest: Connector, source_id: str, targets: list[Target], *,
                 dry_run: bool = False, emit: Emit = lambda e: None, cancel: threading.Event | None = None,
                 score_ok: float = SCORE_OK, score_min: float = SCORE_MIN, public: bool = False,
                 include_doubtful: bool = False) -> TransferResult:
    if not targets:
        raise ValueError(tr("err.choose_target"))
    src_name, songs = source.read(source_id)
    if not songs:      # nada para pasar: no es "transferido" (antes daba un resultado positivo vacío)
        raise ConnectorError(tr("err.source_empty", name=src_name))
    emit({"type": "source", "name": src_name, "total": len(songs), "service": source.name})

    tracks = match_all(songs, dest, emit, cancel, score_ok, score_min, include_doubtful)

    found: list[Song] = []                       # sin repetir el mismo id; los "?" omitidos no
    for t in tracks:
        if t.status in ADDABLE and t.match and t.match.id and all(t.match.id != f.id for f in found):
            found.append(t.match)

    if cancel is not None and cancel.is_set():   # última oportunidad antes de modificar algo
        raise Cancelled()

    results = []
    for target in targets:
        current: list[Song] = []
        if target.id is not None:
            target.name, current = dest.read(target.id)
        to_add, already, versions = plan_target(found, current)
        res = TargetResult(target, to_add, already, versions)
        if not dry_run and to_add:
            res.created = ensure_playlist(dest, target, source.name, public)
            dest.add(target.id, to_add)
        if target.id is not None:
            res.url = dest.url(target.id)
        results.append(res)
        present = {id(m) for m in already} | {id(m) for m, _, _ in versions}
        for t in tracks:
            if t.match is not None and id(t.match) in present:
                twin = next((w for m, w, _ in versions if m is t.match), None)
                emit({"type": "already", "n": t.n, "target": target.name,
                      "as": song_dict(twin) if twin is not None else None})
        emit({"type": "target", "name": target.name, "url": res.url, "created": res.created, "dry_run": dry_run,
              "added": len(to_add), "already": len(already), "versions": len(versions)})

    return TransferResult(source.key, src_name, dest.key, dry_run, tracks, results)


def summary(result: TransferResult) -> dict:
    """Cantidades para la UI y el historial."""
    by = {OK: 0, DOUBTFUL: 0, DOUBTFUL_SKIPPED: 0, NOT_FOUND: 0}
    for t in result.tracks:
        by[t.status] += 1
    return {"ok": by[OK], "doubtful": by[DOUBTFUL], "skipped": by[DOUBTFUL_SKIPPED],
            "not_found": by[NOT_FOUND], "total": len(result.tracks)}
