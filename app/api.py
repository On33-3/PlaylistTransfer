"""Puente JS ⇄ Python: los métodos que llama la UI (`window.pywebview.api.<método>`).

Todo devuelve datos simples (dict/list/str). Los errores vuelven como {"error": "mensaje"} para
mostrarlos tal cual. Lo que tarda (logins, transferencias, buscar o quitar repetidos) corre en
segundo plano: devuelve {"job": id} y la UI lee el avance con `poll(id, desde)`.

pywebview expone los atributos públicos de este objeto: por eso todo el estado va con "_".
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from app import __version__, i18n
from app.auth import browsers as browsers_mod
from app.auth.browsers import Browser
from app.connectors.base import Connector, ConnectorError, Status
from app.core.matching import DOUBTFUL, find_duplicates
from app.core.models import Duplicate
from app.core.transfer import (ALREADY, DOUBTFUL_SKIPPED, Target, TransferResult, plan_target,
                                run_transfer, song_dict, summary)
from app.jobs import BusyError, Jobs
from app.storage import paths, prefs
from app.storage.export import ReportOperation, from_history, write_report
from app.storage.history import History, Operation, TrackRecord
from app.i18n import t as tr

log = logging.getLogger(__name__)

SERVICES = [("tidal", "Tidal"), ("ytmusic", "YouTube Music"), ("spotify", "Spotify")]

# Lo que se muestra en Ajustes › Acerca de (verificar al actualizar requirements.txt)
LICENSES = [
    {"name": "tidalapi", "license": "LGPL-3.0", "url": "https://github.com/tamland/python-tidal"},
    {"name": "ytmusicapi", "license": "MIT", "url": "https://github.com/sigma67/ytmusicapi"},
    {"name": "pywebview", "license": "BSD-3-Clause", "url": "https://github.com/r0x0r/pywebview"},
]


def _artist(s) -> str:
    return ", ".join(s.artists) if s is not None else ""



class Api:
    def __init__(self, connectors: dict[str, Connector], history: History, prefs_path: Path, data_dir: Path,
                 list_browsers: Callable[[], list[Browser]] = browsers_mod.installed_browsers,
                 open_url: Callable[[str, Browser | None], None] = browsers_mod.open_url,
                 save_dialog: Callable[[str], str | None] | None = None,
                 open_file: Callable[[Path], None] | None = None,
                 system_language: Callable[[], str] = i18n.system_language):
        self._save_dialog = save_dialog          # main.py pone el diálogo "Guardar como" de la ventana
        self._open_file = open_file or (lambda p: os.startfile(p))  # type: ignore[attr-defined]
        self._last_export: Path | None = None
        self._connectors = connectors
        self._history = history
        self._prefs_path = Path(prefs_path)
        self._data_dir = Path(data_dir)
        self._list_browsers = list_browsers
        self._open_url = open_url
        self._jobs = Jobs()
        self._transfers: dict[str, TransferResult] = {}
        self._scans: dict[str, dict] = {}
        i18n.set_language(self._resolve_language(system_language))

    def _resolve_language(self, system_language: Callable[[], str]) -> str:
        """El elegido en Ajustes. Si nunca se eligió: castellano para quien ya usaba la app (antes
        solo existía en castellano) y el idioma de Windows para una instalación nueva."""
        chosen = self._prefs().get("language")
        if chosen in i18n.LANGUAGES:
            return chosen
        if self._prefs_path.exists():
            return "es"
        lang = system_language()
        return lang if lang in i18n.LANGUAGES else "en"

    @classmethod
    def create(cls) -> "Api":
        from app.connectors.tidal import TidalConnector
        from app.connectors.ytmusic import YTMusicConnector
        return cls({"tidal": TidalConnector(paths.tidal_session_file()),
                    "ytmusic": YTMusicConnector(paths.yt_browser_file(), paths.browser_profiles_dir())},
                   History(paths.history_file()), paths.prefs_file(), paths.data_dir())

    # ---------------------------------------------------------------- utilidades

    def _connector(self, key: str) -> Connector:
        c = self._connectors.get(key)
        if c is None:
            raise ConnectorError(tr("err.service_unavailable"))
        return c

    def _start(self, kind: str, fn) -> dict:
        try:
            return {"job": self._jobs.start(kind, fn)}
        except BusyError as e:
            return {"error": str(e)}

    def _prefs(self) -> dict:
        return prefs.load(self._prefs_path)

    def _save_prefs(self, p: dict) -> None:
        """Guarda siempre el idioma en uso: si no, una instalación nueva en inglés volvería a
        castellano la próxima vez (el archivo ya existiría, sin idioma)."""
        p["language"] = p.get("language") or i18n.get_language()
        prefs.save(self._prefs_path, p)

    def _browser(self) -> Browser | None:
        b = self._prefs().get("browser")
        if b and b.get("exe"):
            return Browser(b.get("name", ""), b.get("kind", ""), Path(b["exe"]))
        return None

    @staticmethod
    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ConnectorError as e:
            return {"error": str(e)}
        except Exception as e:   # nunca dejar a la UI sin respuesta
            log.exception("Error en la API")
            return {"error": tr("err.unexpected", detail=f"{type(e).__name__}: {e}")}

    # ---------------------------------------------------------------- app

    def info(self) -> dict:
        return {"version": __version__, "data_dir": str(self._data_dir), "licenses": LICENSES,
                "language": i18n.get_language()}

    # ---------------------------------------------------------------- idioma

    def ui_strings(self) -> dict:
        """Los textos de la ventana en el idioma actual (la UI los pide al arrancar)."""
        return {"language": i18n.get_language(), "strings": i18n.ui_strings()}

    def languages(self) -> dict:
        return {"current": i18n.get_language(), "available": i18n.language_names()}

    def set_language(self, code: str) -> dict:
        if code not in i18n.LANGUAGES:
            return {"error": tr("err.bad_language")}
        if self._jobs.busy():
            return {"error": tr("err.busy", kind=tr(f"job.{self._jobs.busy().kind}"))}
        p = self._prefs()
        p["language"] = code
        self._save_prefs(p)
        i18n.set_language(code)
        return {"ok": True, "language": code}

    # ---------------------------------------------------------------- ajustes

    def settings(self) -> dict:
        p = self._prefs()
        return {"score_ok": p["score_ok"], "score_min": p["score_min"],
                "defaults": {"score_ok": prefs.DEFAULTS["score_ok"], "score_min": prefs.DEFAULTS["score_min"]},
                "limits": prefs.threshold_limits()}

    def save_thresholds(self, score_ok, score_min) -> dict:
        try:
            ok, mn = prefs.validate_thresholds(score_ok, score_min)
        except ValueError as e:
            return {"error": str(e)}
        p = self._prefs()
        p["score_ok"], p["score_min"] = ok, mn
        self._save_prefs(p)
        return {"ok": True, "score_ok": ok, "score_min": mn}

    def ui_ready(self) -> dict:
        """La UI avisa que cargó (lo usa la prueba automática de arranque)."""
        cb = getattr(self, "_on_ready", None)
        if cb:
            cb()
        return {"ok": True}

    def open_data_dir(self) -> dict:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self._data_dir)  # type: ignore[attr-defined]  (solo Windows)
        return {"ok": True}

    def open_url(self, url: str) -> dict:
        if not str(url).startswith(("https://tidal.com/", "https://music.youtube.com/", "https://listen.tidal.com/")):
            return {"error": tr("err.only_music_links")}
        self._open_url(url, self._browser())
        return {"ok": True}

    # ---------------------------------------------------------------- servicios y navegador

    def status(self) -> list[dict]:
        out = []
        for key, name in SERVICES:
            c = self._connectors.get(key)
            if c is None:
                out.append({"key": key, "name": name, "status": "soon"})
                continue
            try:
                st = c.status().value
            except Exception as e:
                log.warning("No pude ver el estado de %s: %s", name, e)
                st = Status.EXPIRED.value
            out.append({"key": key, "name": name, "status": st})
        return out

    def browsers(self) -> dict:
        found = self._list_browsers()
        chosen = self._browser()
        suggested = browsers_mod.suggested_browser(found)
        return {"browsers": [{"name": b.name, "kind": b.kind, "exe": str(b.exe), "default": b.default} for b in found],
                "chosen": str(chosen.exe) if chosen else None,
                "suggested": str(suggested.exe) if suggested else None}

    def choose_browser(self, exe: str) -> dict:
        b = next((b for b in self._list_browsers() if str(b.exe).lower() == str(exe).lower()), None)
        if b is None:
            return {"error": tr("err.browser_not_found")}
        p = self._prefs()
        p["browser"] = {"name": b.name, "kind": b.kind, "exe": str(b.exe)}
        self._save_prefs(p)
        return {"ok": True, "name": b.name}

    def connect(self, key: str) -> dict:
        def work(emit, cancel):
            c = self._connector(key)
            browser = self._browser()
            if browser is None:
                suggested = browsers_mod.suggested_browser(self._list_browsers())
                if suggested is None:
                    raise ConnectorError(tr("err.choose_browser_first"))
                self.choose_browser(str(suggested.exe))
                browser = suggested
            emit({"type": "login", "service": c.name, "browser": browser.name})
            c.connect(browser)
            return {"status": c.status().value}
        return self._start("login", work)

    def disconnect(self, key: str) -> dict:
        return self._safe(lambda: (self._connector(key).disconnect(), {"ok": True})[1])

    def playlists(self, key: str) -> list[dict] | dict:
        def work():
            return [{"id": p.id, "name": p.name, "count": p.count} for p in self._connector(key).list_playlists()]
        return self._safe(work)

    # ---------------------------------------------------------------- tareas

    def poll(self, job_id: str, since: int = 0) -> dict:
        return self._jobs.poll(job_id, int(since or 0))

    def cancel(self, job_id: str) -> dict:
        self._jobs.cancel(job_id)
        return {"ok": True}

    # ---------------------------------------------------------------- transferir

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
            result = run_transfer(src, dst, source_id, tgts, dry_run=bool(dry_run), emit=emit, cancel=cancel,
                                  score_ok=p["score_ok"], score_min=p["score_min"])
            if not result.dry_run:
                self._record_transfer(result, src, dst)
            key = f"transfer-{len(self._transfers) + 1}"
            self._transfers[key] = result      # para "agregar la versión de otro artista"
            return {"transfer": key, "summary": summary(result), "source_name": result.source_name,
                    "dry_run": result.dry_run,
                    "targets": [{"name": t.target.name, "id": t.target.id, "url": t.url, "added": len(t.added),
                                 "already": len(t.already), "versions": len(t.versions), "created": t.created}
                                for t in result.targets]}

        return self._start("transfer", work)

    def add_alternative(self, transfer: str, n: int, k: int) -> dict:
        """El usuario eligió agregar la versión de otro artista (tema `n`, alternativa `k`)."""
        result = self._transfers.get(transfer)
        if result is None or result.dry_run:
            return {"error": tr("err.transfer_gone")}
        track = next((t for t in result.tracks if t.n == int(n)), None)
        if track is None or not (0 <= int(k) < len(track.alternatives)):
            return {"error": tr("err.version_not_found")}
        alt = track.alternatives[int(k)]

        def work(emit, cancel):
            dst = self._connector(result.target_service)
            done = []
            for t in result.targets:
                if t.target.id is None:      # playlist nueva que no se creó (no había nada para agregar)
                    t.target.id = dst.create(t.target.name, tr("playlist.description", service=dict(SERVICES).get(result.source_service, result.source_service), date=f"{datetime.now():%Y-%m-%d}"))
                    t.url = dst.url(t.target.id)
                dst.add(t.target.id, [alt])
                done.append(t.target.name)
                self._history.record(
                    Operation(kind="transfer", source=result.source_service, source_name=result.source_name,
                              target=result.target_service, target_name=t.target.name, target_url=t.url, added=1),
                    [TrackRecord(1, "ok", track.source.title, _artist(track.source), alt.title, _artist(alt))])
            return {"added": song_dict(alt), "targets": done}
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
            service = dict(SERVICES).get(result.source_service, result.source_service)
            added_total = already_total = 0
            added_ns: set[int] = set()
            for t in result.targets:
                current: list = []
                if t.target.id is not None:
                    _, current = dst.read(t.target.id)
                to_add, already, versions = plan_target([x.match for x in tracks], current)
                if to_add:
                    if t.target.id is None:      # playlist nueva que no se creó (no había nada para agregar)
                        t.target.id = dst.create(t.target.name, tr("playlist.description", service=service,
                                                                    date=f"{datetime.now():%Y-%m-%d}"))
                        t.url = dst.url(t.target.id)
                    dst.add(t.target.id, to_add)
                present = {id(m) for m in to_add}
                records = [TrackRecord(i, DOUBTFUL if id(x.match) in present else ALREADY, x.source.title,
                                       _artist(x.source), x.match.title, _artist(x.match), round(x.score, 3))
                           for i, x in enumerate(tracks, 1)]
                self._history.record(
                    Operation(kind="transfer", source=result.source_service, source_name=result.source_name,
                              target=result.target_service, target_name=t.target.name, target_url=t.url,
                              added=len(to_add), already=len(already) + len(versions), doubtful=len(to_add)),
                    records)
                added_total += len(to_add)
                added_ns |= {x.n for x in tracks if id(x.match) in present}
                already_total += len(already) + len(versions)
            for x in tracks:              # ya no se ofrecen de nuevo
                x.status = DOUBTFUL
            return {"ns": [x.n for x in tracks], "added_ns": sorted(added_ns), "added": added_total,
                    "already": already_total,
                    "url": next((t.url for t in result.targets if t.url), None)}
        return self._start("add_doubtful", work)

    @staticmethod
    def _transfer_operations(result: TransferResult) -> list[tuple[Operation, list[TrackRecord]]]:
        """Una operación por playlist de destino, con el estado de cada tema respecto de esa playlist.
        La usan el historial y el reporte, así los dos dicen exactamente lo mismo."""
        s = summary(result)
        out = []
        for t in result.targets:
            already_ids = {id(m) for m in t.already} | {id(m) for m, _, _ in t.versions}
            tracks = []
            for track in result.tracks:
                status = ALREADY if track.match is not None and id(track.match) in already_ids else track.status
                tracks.append(TrackRecord(track.n, status, track.source.title, _artist(track.source),
                                          track.match.title if track.match else None, _artist(track.match) or None,
                                          round(track.score, 3)))
            op = Operation(kind="transfer", source=result.source_service, source_name=result.source_name,
                           target=result.target_service, target_name=t.target.name, target_url=t.url,
                           added=len(t.added), already=len(t.already) + len(t.versions),
                           doubtful=s["doubtful"], not_found=s["not_found"], skipped=s["skipped"])
            out.append((op, tracks))
        return out

    def _record_transfer(self, result: TransferResult, src: Connector, dst: Connector) -> None:
        for op, tracks in self._transfer_operations(result):
            self._history.record(op, tracks)

    # ---------------------------------------------------------------- quitar repetidos

    def scan_duplicates(self, key: str, playlist_id: str) -> dict:
        def work(emit, cancel):
            c = self._connector(key)
            emit({"type": "reading", "service": c.name})
            name, songs = c.read(playlist_id)
            dups = find_duplicates(songs)
            scan_id = f"scan-{len(self._scans) + 1}"
            self._scans[scan_id] = {"key": key, "playlist_id": playlist_id, "name": name, "songs": songs, "dups": dups}
            return {"scan": scan_id, "name": name, "total": len(songs),
                    "duplicates": [{"i": i, "index": d.index, "kind": d.kind, "song": song_dict(d.song),
                                    "kept": song_dict(d.kept)} for i, d in enumerate(dups)]}
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

    # ---------------------------------------------------------------- historial

    def history(self, limit: int | None = None, kind: str | None = None) -> list[dict]:
        names = dict(SERVICES)
        ops = [op for op in self._history.list() if kind in (None, "", op.kind)][:limit or None]
        tiles = self._history.tiles([op.id for op in ops], limit=60)
        out = []
        for op in ops:
            d = op.__dict__.copy()
            d["source_label"] = names.get(op.source, op.source)
            d["target_label"] = names.get(op.target, op.target) if op.target else None
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
            count = write_report(path, ops, dict(SERVICES))
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
