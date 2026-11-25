"""Runs a transfer: read the source → search for each song → see what is already there → add.

The same logic as `run()` in the original console script, but without printing: each step is an
event (`emit`) so the UI can show it live. Nothing is changed until every search has finished,
so cancelling while searching never leaves a playlist half-done.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.connectors.base import Connector, ConnectorError
from app.core.matching import (DOUBTFUL, NOT_FOUND, OK, SCORE_MIN, SCORE_OK, SongIndex, alternatives, best_match,
                               classify)
from app.core.models import Song
from app.core.models import Cancelled  # noqa: F401  (also imported from here)
from app.storage.progress import SearchProgress
from app.i18n import t as tr

ALREADY = "ya_estaba"   # status of a song that was already in the destination
# Repeated in the source playlist (the same song higher up, or another version): added only once
REPEATED = "repetido"
# "?" (may be another version): never added on their own. They keep their match so the user
# decides at the end ("+ Add anyway" / "Add the N…").
DOUBTFUL_SKIPPED = "dudoso_omitido"
ADDABLE = (OK, DOUBTFUL)
# Why a version is not added (internal codes, never shown): another one of the same song was
# already in the destination, or the source had it repeated
ALREADY_THERE, REPEATED_IN_SOURCE = "already_there", "repeated_in_source"

Emit = Callable[[dict], None]


class PartialTransfer(Exception):
    """A destination failed after others were already done: `result` has what did get done."""

    def __init__(self, result: "TransferResult", failed: str):
        super().__init__(failed)
        self.result, self.failed = result, failed


@dataclass
class Target:
    name: str
    id: str | None = None      # None = new playlist (created only if there is something to add)


@dataclass
class TrackResult:
    n: int
    source: Song
    match: Song | None
    score: float
    status: str                                  # ok / doubtful / doubtful skipped / not found
    alternatives: list[Song] = field(default_factory=list)


@dataclass
class TargetResult:
    target: Target
    added: list[Song] = field(default_factory=list)
    already: list[Song] = field(default_factory=list)            # the same song was already there
    versions: list[tuple[Song, Song, str]] = field(default_factory=list)   # (match, the one that was there, why)
    created: bool = False
    url: str | None = None
    history_id: int | None = None     # its operation in the history: what is added later goes there


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
              include_doubtful: bool = False, progress: SearchProgress | None = None) -> list[TrackResult]:
    """Searches for each song in the destination. With `progress`, what was already searched in a
    transfer that didn't finish is not searched again, and if this one stops (a long pause, cancel, no
    internet) what was searched is kept: both cases are announced."""
    results = []
    dest.reset_search_failures()
    if progress is not None and (before := progress.done(songs)):
        emit({"type": "resumed", "done": before, "total": len(songs)})
    try:
        for n, src in enumerate(songs, 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            hit = progress.get(src) if progress is not None else None
            emit({"type": "searching", "n": n, "song": song_dict(src), "cached": hit is not None})
            if hit is not None:
                match, sc, saved_alts = hit
            else:
                candidates = dest.search(src)
                match, sc = best_match(src, candidates)
                saved_alts = None
            status = classify(sc, score_ok, score_min) if match else NOT_FOUND
            if status == DOUBTFUL and not include_doubtful:
                status = DOUBTFUL_SKIPPED
            if saved_alts is None:
                alts = alternatives(src, candidates) if status == NOT_FOUND else []
                if progress is not None:
                    progress.put(src, match, sc, alts)
            else:
                alts = saved_alts if status == NOT_FOUND else []
            r = TrackResult(n, src, match if status != NOT_FOUND else None, sc, status, alts)
            results.append(r)
            emit({"type": "track", "n": n, "status": status, "score": round(sc, 2), "song": song_dict(src),
                  "match": song_dict(match) if status != NOT_FOUND else None,
                  "alternatives": [song_dict(a) for a in alts], "cached": hit is not None})
    except BaseException:
        if progress is not None and (kept := progress.done(songs)):
            progress.save()
            emit({"type": "saved", "done": kept, "total": len(songs)})
        raise
    return results


def plan_target(found: list[Song], current: list[Song]) -> tuple[list[Song], list[Song], list[tuple[Song, Song, str]]]:
    """(to add, already there, another version was already there). The same as the console script."""
    to_add: list[Song] = []
    already: list[Song] = []
    versions: list[tuple[Song, Song, str]] = []
    in_destination, queued = SongIndex(current), SongIndex()
    for m in found:
        twin = in_destination.first_match(m)
        if twin is None:
            twin = queued.first_match(m)
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
        queued.add(m)
    return to_add, already, versions


def ensure_playlist(dest: Connector, target: Target, source_service: str, public: bool = False) -> bool:
    """A new playlist is created only once there is something to add. True if it created it."""
    if target.id is not None:
        return False
    desc = tr("playlist.description", service=source_service, date=f"{datetime.now():%Y-%m-%d}")
    target.id = dest.create(target.name, desc, public)
    return True


def run_transfer(source: Connector, dest: Connector, source_id: str, targets: list[Target], *,
                 dry_run: bool = False, emit: Emit = lambda e: None, cancel: threading.Event | None = None,
                 score_ok: float = SCORE_OK, score_min: float = SCORE_MIN, public: bool = False,
                 include_doubtful: bool = False, progress: SearchProgress | None = None) -> TransferResult:
    if not targets:
        raise ValueError(tr("err.choose_target"))
    for c in (source, dest):            # the services' waits show on screen and can be cancelled
        c.listen(emit, cancel)
    try:
        result = _transfer(source, dest, source_id, targets, dry_run, emit, cancel, score_ok, score_min, public,
                           include_doubtful, progress)
    except BaseException:
        if progress is not None:        # also if adding failed: next time it isn't searched again
            progress.save()
        raise
    finally:
        for c in (source, dest):
            c.listen(None, None)
    if progress is not None:
        # A dry run keeps the searches for the real transfer; a finished real one doesn't need them
        if dry_run:
            progress.save()
        else:
            progress.clear()
    return result


def _transfer(source, dest, source_id, targets, dry_run, emit, cancel, score_ok, score_min, public,
              include_doubtful, progress) -> TransferResult:
    src_name, songs = source.read(source_id)
    if not songs:      # nothing to move: that is not "transferred" (it used to give an empty positive result)
        raise ConnectorError(tr("err.source_empty", name=src_name))
    emit({"type": "source", "name": src_name, "total": len(songs), "service": source.name})

    tracks = match_all(songs, dest, emit, cancel, score_ok, score_min, include_doubtful, progress)

    found: list[Song] = []                       # without repeating the same id; skipped "?" are left out
    first: dict[str, int] = {}                   # found id → the first source song that uses it
    for t in tracks:
        if t.status in ADDABLE and t.match and t.match.id:
            if t.match.id in first:              # the same song, again in the source: it goes only once
                t.status = REPEATED
                emit({"type": "repeated", "n": t.n, "of": first[t.match.id]})
            else:
                first[t.match.id] = t.n
                found.append(t.match)

    if cancel is not None and cancel.is_set():   # last chance before changing anything
        raise Cancelled()
    # From here on it writes: a service's wait is still announced, but Cancel no longer stops it (the
    # playlist would be left half-done and the screen would say "none was changed")
    dest.listen(emit, None)
    emit({"type": "writing"})

    results: list[TargetResult] = []
    for target in targets:
        try:
            results.append(_write_target(source, dest, target, tracks, found, dry_run, public, emit))
        except Exception as e:
            if results:
                raise PartialTransfer(TransferResult(source.key, src_name, dest.key, dry_run, tracks, results), target.name) from e
            raise
    return TransferResult(source.key, src_name, dest.key, dry_run, tracks, results)


def _write_target(source: Connector, dest: Connector, target: Target, tracks: list[TrackResult], found: list[Song],
                  dry_run: bool, public: bool, emit: Emit) -> TargetResult:
    """One destination: see what is already there and add what is missing."""
    current: list[Song] = []
    if target.id is not None:
        target.name, current = dest.read(target.id)
    to_add, already, versions = plan_target(found, current)
    res = TargetResult(target, to_add, already, versions)
    if not dry_run and to_add:
        res.created = ensure_playlist(dest, target, source.name, public)
        emit({"type": "adding_to", "name": target.name, "total": len(to_add)})
        dest.add(target.id, to_add)
    if target.id is not None:
        res.url = dest.url(target.id)
    present = {id(m) for m in already} | {id(m) for m, _, _ in versions}
    for t in tracks:
        if t.match is not None and id(t.match) in present:
            twin, why = next(((w, y) for m, w, y in versions if m is t.match), (None, ALREADY_THERE))
            # another version of the same song higher up in the source: repeated, not "already there"
            emit({"type": "repeated" if why == REPEATED_IN_SOURCE else "already", "n": t.n, "target": target.name,
                  "as": song_dict(twin) if twin is not None else None})
    emit({"type": "target", "name": target.name, "url": res.url, "created": res.created, "dry_run": dry_run,
          "added": len(to_add), "already": len(already), "versions": len(versions)})
    return res


def summary(result: TransferResult) -> dict:
    """Counts for the UI and the history."""
    by = {OK: 0, DOUBTFUL: 0, DOUBTFUL_SKIPPED: 0, NOT_FOUND: 0, REPEATED: 0}
    for t in result.tracks:
        by[t.status] += 1
    # Repeated in the source: the same song (REPEATED) and another version of it (in the first destination)
    versions = result.targets[0].versions if result.targets else []
    repeated = by[REPEATED] + sum(1 for _, _, why in versions if why == REPEATED_IN_SOURCE)
    return {"ok": by[OK], "doubtful": by[DOUBTFUL], "skipped": by[DOUBTFUL_SKIPPED],
            "not_found": by[NOT_FOUND], "repeated": repeated, "total": len(result.tracks)}


def already_there(t: TargetResult) -> int:
    """Those already in that destination (the same song or another version): source repeats don't count."""
    return len(t.already) + sum(1 for _, _, why in t.versions if why == ALREADY_THERE)
