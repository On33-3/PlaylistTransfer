"""Reportes CSV: formato de Excel según la región de Windows, acentos y fórmulas."""
from __future__ import annotations

import csv

import pytest

from app.storage.export import HEADER, ReportOperation, from_history, safe_cell, write_csv
from app.storage.history import History, Operation, TrackRecord

NAMES = {"tidal": "Tidal", "ytmusic": "YouTube Music"}


@pytest.fixture
def history(tmp_path):
    h = History(tmp_path / "historial.sqlite")
    h.record(Operation(kind="transfer", source="tidal", source_name="Clásicos", target="ytmusic",
                       target_name="Clásicos", added=1, already=1, created_at="2026-11-08T01:00:59"),
             [TrackRecord(1, "ok", "Corazón Espinado", "Santana, Maná", "Corazón Espinado", "Santana", 0.97),
              TrackRecord(2, "ya_estaba", "Mr. Blue Sky", "ELO", "Mr. Blue Sky - 2012 Remaster", "ELO", 0.9),
              TrackRecord(3, "no_encontrado", "Hallelujah", "Jeff Buckley", None, None, 0.5)])
    h.record(Operation(kind="dedupe", source="ytmusic", source_name="Old songs", removed=1,
                       created_at="2026-11-08T01:02:23"),
             [TrackRecord(1, "quitado", "Virtual Insanity (Official Video)", "Jamiroquai", "Virtual Insanity", "Jamiroquai")])
    return h


def read(path, sep):
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "sin BOM, Excel muestra mal los acentos"
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=sep))


def test_english_windows_uses_comma_and_point(history, tmp_path):
    out = tmp_path / "h.csv"
    assert write_csv(out, from_history(history), NAMES, sep=",", decimal=".") == 4
    rows = read(out, ",")
    assert rows[0] == HEADER
    assert rows[1] == ["2026-11-08 · 01:00", "Transferencia", "Tidal", "Clásicos", "YouTube Music", "Clásicos",
                       "1", "Encontrado", "Corazón Espinado", "Santana, Maná", "Corazón Espinado", "Santana", "0.97"]
    assert rows[2][7] == "Ya estaba" and rows[3][7] == "No encontrado" and rows[3][10] == ""
    assert rows[4][:2] == ["2026-11-08 · 01:02", "Quitar repetidos"] and rows[4][7] == "Quitado"
    assert all(len(r) == len(HEADER) for r in rows)


def test_spanish_windows_uses_semicolon_and_comma(history, tmp_path):
    out = tmp_path / "h.csv"
    write_csv(out, from_history(history), NAMES, sep=";", decimal=",")
    rows = read(out, ";")
    assert rows[1][-1] == "0,97" and rows[1][9] == "Santana, Maná"      # la coma del artista no corta columnas
    assert all(len(r) == len(HEADER) for r in rows)


def test_oldest_first_and_filter_by_kind(history, tmp_path):
    assert [o.kind for o in from_history(history)] == ["transfer", "dedupe"]
    assert [o.kind for o in from_history(history, kind="dedupe")] == ["dedupe"]


@pytest.mark.parametrize("title", ["=HYPERLINK(\"http://x\")", "+1 (Remix)", "-Intro-", "@Home", "\tTab"])
def test_titles_that_look_like_formulas_are_neutralized(title):
    assert safe_cell(title) == "'" + title


def test_normal_titles_are_untouched():
    assert safe_cell("Take On Me") == "Take On Me" and safe_cell(None) == "" and safe_cell(3) == "3"


def test_formula_titles_end_up_as_text_in_the_file(tmp_path):
    op = ReportOperation("2026-01-01T00:00:00", "dry_run", "tidal", "=cmd|' /C calc'!A0", "ytmusic", "x",
                         [TrackRecord(1, "ok", "=1+1", "@artista", "-x", "+y", 1.0)])
    out = tmp_path / "f.csv"
    write_csv(out, [op], NAMES, sep=",", decimal=".")
    row = read(out, ",")[1]
    assert row[1] == "Prueba (sin cambios)"
    assert [row[3], row[8], row[9], row[10], row[11]] == ["'=cmd|' /C calc'!A0", "'=1+1", "'@artista", "'-x", "'+y"]


def test_an_error_does_not_leave_a_half_written_file(history, tmp_path, monkeypatch):
    out = tmp_path / "h.csv"
    out.write_text("versión anterior", encoding="utf-8")

    def broken():
        yield ReportOperation("2026-01-01T00:00:00", "transfer", "tidal", "a", "ytmusic", "b",
                              [TrackRecord(1, "ok", "x", "y")])
        raise RuntimeError("se cortó")
    with pytest.raises(RuntimeError):
        write_csv(out, broken(), NAMES, sep=",", decimal=".")
    assert out.read_text(encoding="utf-8") == "versión anterior"
