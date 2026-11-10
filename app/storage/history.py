"""Historial de transferencias y limpiezas, en SQLite, dentro de la carpeta de datos del usuario.

Primer uso: el archivo no existe y el historial está vacío. Leerlo no lo crea; recién la primera
transferencia (o limpieza) real lo crea y lo alimenta. Nunca viene nada con la app ni con el repo.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    kind          TEXT NOT NULL,              -- 'transfer' | 'dedupe'
    source        TEXT NOT NULL,              -- servicio de origen ('tidal', 'ytmusic'...)
    source_name   TEXT NOT NULL,              -- nombre de la playlist de origen
    target        TEXT,                       -- servicio de destino (NULL en una limpieza)
    target_name   TEXT,
    target_url    TEXT,
    added         INTEGER NOT NULL DEFAULT 0,
    already       INTEGER NOT NULL DEFAULT 0, -- ya estaban (incluye otra versión)
    doubtful      INTEGER NOT NULL DEFAULT 0,
    not_found     INTEGER NOT NULL DEFAULT 0,
    removed       INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0  -- "?" que no se agregaron (el usuario eligió no agregarlos solos)
);
CREATE TABLE IF NOT EXISTS operation_tracks (
    operation_id  INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
    n             INTEGER NOT NULL,
    status        TEXT NOT NULL,              -- 'ok' | 'dudoso' | 'dudoso_omitido' | 'no_encontrado' | 'ya_estaba' | 'quitado'
    title         TEXT NOT NULL,
    artist        TEXT NOT NULL,              -- el artista queda siempre como referencia
    match_title   TEXT,
    match_artist  TEXT,
    score         REAL,
    PRIMARY KEY (operation_id, n)
);
"""


@dataclass
class TrackRecord:
    n: int
    status: str
    title: str
    artist: str
    match_title: str | None = None
    match_artist: str | None = None
    score: float | None = None


@dataclass
class Operation:
    kind: str
    source: str
    source_name: str
    target: str | None = None
    target_name: str | None = None
    target_url: str | None = None
    added: int = 0
    already: int = 0
    doubtful: int = 0
    not_found: int = 0
    removed: int = 0
    skipped: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    id: int | None = None


class History:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self, create: bool) -> sqlite3.Connection | None:
        if not self.path.exists():
            if not create:
                return None
            self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            con.executescript(SCHEMA)
            self._migrate(con)
        except sqlite3.DatabaseError:
            con.close()
            self._set_aside()
            return self._connect(create)
        return con

    @staticmethod
    def _migrate(con: sqlite3.Connection) -> None:
        """Historiales de versiones anteriores: agrega las columnas nuevas sin tocar lo que hay."""
        cols = {r["name"] for r in con.execute("PRAGMA table_info(operations)")}
        if "skipped" not in cols:
            con.execute("ALTER TABLE operations ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0")
            con.commit()

    def _set_aside(self) -> None:
        """Archivo dañado: se aparta (nunca se borra) y se empieza un historial nuevo."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path.rename(self.path.with_name(f"{self.path.stem}.dañado-{stamp}{self.path.suffix}"))

    def is_empty(self) -> bool:
        return not self.list(limit=1)

    def list(self, limit: int | None = None) -> list[Operation]:
        """Lo más reciente primero. [] si todavía no se hizo nada."""
        con = self._connect(create=False)
        if con is None:
            return []
        with con:
            rows = con.execute("SELECT * FROM operations ORDER BY created_at DESC, id DESC"
                               + (" LIMIT ?" if limit else ""), (limit,) if limit else ()).fetchall()
        con.close()
        return [Operation(**dict(r)) for r in rows]

    def tiles(self, operation_ids: list[int], limit: int = 60) -> dict[int, dict]:
        """Para los mosaicos: {operación: {"statuses", "songs" [(tema, artista)], "total"}}, con los
        primeros `limit` temas de cada una (en orden) y el total, para mostrar "+N"."""
        con = self._connect(create=False)
        if con is None or not operation_ids:
            return {}
        marks = ", ".join("?" for _ in operation_ids)
        with con:
            rows = con.execute(f"SELECT operation_id, status, title, artist FROM operation_tracks "
                               f"WHERE operation_id IN ({marks}) ORDER BY operation_id, n", list(operation_ids)).fetchall()
        con.close()
        out: dict[int, dict] = {}
        for r in rows:
            d = out.setdefault(r["operation_id"], {"statuses": [], "songs": [], "total": 0})
            d["total"] += 1
            if len(d["statuses"]) < limit:
                d["statuses"].append(r["status"])
                d["songs"].append((r["title"], r["artist"]))
        return out

    def tracks(self, operation_id: int) -> list[TrackRecord]:
        con = self._connect(create=False)
        if con is None:
            return []
        with con:
            rows = con.execute("SELECT n, status, title, artist, match_title, match_artist, score "
                               "FROM operation_tracks WHERE operation_id = ? ORDER BY n",
                               (operation_id,)).fetchall()
        con.close()
        return [TrackRecord(**dict(r)) for r in rows]

    def record(self, op: Operation, tracks: list[TrackRecord]) -> int:
        """Guarda una operación real (las pruebas sin cambios no se registran). Devuelve su id."""
        data = {k: v for k, v in asdict(op).items() if k != "id"}
        con = self._connect(create=True)
        with con:
            cur = con.execute(f"INSERT INTO operations ({', '.join(data)}) "
                              f"VALUES ({', '.join('?' for _ in data)})", list(data.values()))
            op.id = cur.lastrowid
            con.executemany("INSERT INTO operation_tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            [(op.id, t.n, t.status, t.title, t.artist, t.match_title, t.match_artist, t.score)
                             for t in tracks])
        con.close()
        return op.id

    def amend(self, operation_id: int, tracks: list[TrackRecord], **deltas: int) -> None:
        """Lo que la persona decide después sobre una transferencia cambia esa misma operación:
        el estado de esos temas y las cantidades (`added=1, not_found=-1`)."""
        allowed = {"added", "already", "doubtful", "not_found", "skipped"}
        if not deltas.keys() <= allowed:
            raise ValueError(deltas)
        con = self._connect(create=True)
        with con:
            for k, v in deltas.items():
                con.execute(f"UPDATE operations SET {k} = MAX(0, {k} + ?) WHERE id = ?", (v, operation_id))
            con.executemany("UPDATE operation_tracks SET status = ?, match_title = ?, match_artist = ?, score = ? "
                            "WHERE operation_id = ? AND n = ?",
                            [(t.status, t.match_title, t.match_artist, t.score, operation_id, t.n) for t in tracks])
        con.close()
