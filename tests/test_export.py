"""The history as light text: readable for a person and for an AI."""
from __future__ import annotations

import pytest

from app.storage.export import from_history, render, write_report
from app.storage.history import History, Operation, TrackRecord

NAMES = {"tidal": "Tidal", "ytmusic": "YouTube Music"}


@pytest.fixture
def history(tmp_path):
    h = History(tmp_path / "historial.sqlite")
    h.record(Operation(kind="transfer", source="tidal", source_name="Clásicos", target="ytmusic",
                       target_name="Clásicos", added=1, already=1, skipped=1, not_found=1,
                       created_at="2026-11-08T01:00:59"),
             [TrackRecord(1, "ok", "Corazón Espinado", "Santana, Maná", "Corazón Espinado", "Santana", 0.97),
              TrackRecord(2, "ya_estaba", "Mr. Blue Sky", "ELO", "Mr. Blue Sky - 2012 Remaster", "ELO", 0.9),
              TrackRecord(3, "dudoso_omitido", "Bohemian Rhapsody", "Queen", "Bohemian Rhapsody (Live Aid 1985)", "Queen", 0.7),
              TrackRecord(4, "no_encontrado", "Hallelujah", "Jeff Buckley", None, None, 0.5)])
    h.record(Operation(kind="dedupe", source="ytmusic", source_name="Old songs", removed=1,
                       created_at="2026-11-08T01:02:23"),
             [TrackRecord(1, "quitado", "Virtual Insanity (Official Video)", "Jamiroquai", "Virtual Insanity", "Jamiroquai")])
    return h


def test_the_file_reads_like_a_simple_document(history):
    text = render(from_history(history), NAMES)
    lines = text.splitlines()
    assert lines[0] == "# PlaylistTransfer — historial"
    assert lines[1].endswith("· 2 operaciones")
    assert "## 2026-11-08 01:00 · Clásicos → Clásicos (Tidal → YouTube Music)" in lines
    assert "1 agregado, 1 ya estaba, 1 sin agregar (puede ser otra versión), 1 no encontrado" in lines
    assert "- Agregado: Corazón Espinado — Santana, Maná → Corazón Espinado — Santana" in lines
    assert "- Ya estaba: Mr. Blue Sky — ELO → Mr. Blue Sky - 2012 Remaster — ELO" in lines
    assert "- No agregado, puede ser otra versión: Bohemian Rhapsody — Queen → Bohemian Rhapsody (Live Aid 1985) — Queen" in lines
    assert "- No encontrado: Hallelujah — Jeff Buckley" in lines
    assert "## 2026-11-08 01:02 · Repetidos en Old songs (YouTube Music)" in lines
    assert "- Quitado (repetido): Virtual Insanity (Official Video) — Jamiroquai · quedó: Virtual Insanity — Jamiroquai" in lines


def test_same_title_and_artist_does_not_repeat_the_match(tmp_path):
    h = History(tmp_path / "h.sqlite")
    h.record(Operation(kind="transfer", source="tidal", source_name="A", target="ytmusic", target_name="B", added=1),
             [TrackRecord(1, "ok", "Take On Me", "a-ha", "Take On Me", "a-ha", 1.0)])
    assert "- Agregado: Take On Me — a-ha\n" in render(from_history(h), NAMES)


def test_oldest_first_and_filter_by_kind(history):
    assert [o.op.kind for o in from_history(history)] == ["transfer", "dedupe"]
    assert [o.op.kind for o in from_history(history, kind="dedupe")] == ["dedupe"]


def test_file_is_light_utf8_text(history, tmp_path):
    out = tmp_path / "historial.txt"
    assert write_report(out, from_history(history), NAMES) == 2
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")                      # plain UTF-8, no BOM
    assert "Corazón" in raw.decode("utf-8") and len(raw) < 2000


def test_a_big_history_stays_small(tmp_path):
    h = History(tmp_path / "h.sqlite")
    for i in range(20):
        h.record(Operation(kind="transfer", source="tidal", source_name=f"P{i}", target="ytmusic", target_name="X", added=50),
                 [TrackRecord(k, "ok", f"Tema {k}", "Artista", f"Tema {k}", "Artista", 1.0) for k in range(50)])
    out = tmp_path / "h.txt"
    write_report(out, from_history(h), NAMES)
    assert out.stat().st_size < 40_000                                 # 1000 songs: about 30 KB


def test_an_error_does_not_leave_a_half_written_file(history, tmp_path, monkeypatch):
    out = tmp_path / "h.txt"
    out.write_text("versión anterior", encoding="utf-8")
    import app.storage.export as ex
    monkeypatch.setattr(ex, "render", lambda *a: (_ for _ in ()).throw(RuntimeError("se cortó")))
    with pytest.raises(RuntimeError):
        write_report(out, from_history(history), NAMES)
    assert out.read_text(encoding="utf-8") == "versión anterior"
