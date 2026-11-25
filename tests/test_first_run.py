"""First run: whoever downloads the repo or the app starts from zero, with nobody's data.

Each test simulates a new PC by pointing %LOCALAPPDATA% at an empty folder.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from app.connectors.base import Status
from app.connectors.tidal import TidalConnector
from app.connectors.ytmusic import YTMusicConnector
from app.core.matching import SCORE_MIN, SCORE_OK
from app.storage import paths, prefs
from app.storage.history import History, Operation, TrackRecord
from tests.conftest import ROOT


@pytest.fixture
def new_pc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "PlaylistTransfer"


def sample_transfer() -> tuple[Operation, list[TrackRecord]]:
    op = Operation(kind="transfer", source="tidal", source_name="Favoritas", target="ytmusic",
                   target_name="Favoritas", target_url="https://music.youtube.com/playlist?list=PLx",
                   added=2, already=1, not_found=1)
    tracks = [TrackRecord(1, "ok", "Luna de papel", "Los Faroles", "Luna de papel", "Los Faroles", 1.0),
              TrackRecord(2, "ok", "Neon Bay", "Coral Static", "Neon Bay", "Coral Static", 0.97),
              TrackRecord(3, "ya_estaba", "Marea", "Bruma", "Marea (sped up)", "Bruma", 0.9),
              TrackRecord(4, "no_encontrado", "From The Start", "Orchid Lane")]
    return op, tracks


# ---------------------------------------------------------------- everything empty

def test_history_starts_empty_and_reading_does_not_create_it(new_pc):
    h = History(paths.history_file())
    assert h.list() == [] and h.is_empty() and h.tracks(1) == []
    assert not paths.history_file().exists()


def test_preferences_start_with_defaults(new_pc):
    p = prefs.load(paths.prefs_file())
    assert p == {"browser": None, "score_ok": SCORE_OK, "score_min": SCORE_MIN, "language": None}
    assert not paths.prefs_file().exists()


def test_services_start_disconnected(new_pc):
    assert TidalConnector(paths.tidal_session_file()).status() is Status.DISCONNECTED
    assert YTMusicConnector(paths.yt_browser_file(), paths.browser_profiles_dir()).status() is Status.DISCONNECTED
    from app.connectors.spotify import SpotifyConnector
    assert SpotifyConnector(paths.spotify_session_file(), paths.spotify_app_file()).status() is Status.SETUP


def test_the_app_starts_with_spotify_waiting_for_setup(new_pc):
    """Without a Client ID nobody is asked: Spotify stays "not set up" and the rest works."""
    from app.api import Api
    api = Api.create()
    assert {s["key"]: s["status"] for s in api.status()}["spotify"] == "setup"
    sp = next(s for s in api.services() if s["key"] == "spotify")
    assert sp["available"] and "http://127.0.0.1:8973/callback" in [st.get("copy") for sec in sp["setup"]["sections"] for st in sec["steps"]]
    assert sp["setup"]["client_id"] is None
    assert not paths.spotify_app_file().exists() and not paths.spotify_session_file().exists()


def test_nothing_is_read_from_the_repo_folder(new_pc):
    """Even if the repo has ignored files of yours (browser.json, reports...), the app doesn't use them."""
    for f in (paths.tidal_session_file(), paths.yt_browser_file(), paths.prefs_file(),
              paths.history_file(), paths.browser_profiles_dir(), paths.spotify_session_file(),
              paths.spotify_app_file()):
        assert new_pc in f.parents and ROOT not in f.parents


# ---------------------------------------------------------------- the first transfer fills it

def test_first_transfer_creates_and_feeds_the_history(new_pc):
    op, tracks = sample_transfer()
    op_id = History(paths.history_file()).record(op, tracks)
    assert paths.history_file().exists()

    again = History(paths.history_file())          # as if the app were reopened
    [saved] = again.list()
    assert saved.id == op_id and saved.source_name == "Favoritas" and saved.added == 2
    detail = again.tracks(op_id)
    assert [(t.title, t.artist, t.status) for t in detail] == [(t.title, t.artist, t.status) for t in tracks]
    assert all(t.artist for t in detail)              # the artist always stays as a reference


def test_history_is_newest_first(new_pc):
    h = History(paths.history_file())
    first, t = sample_transfer()
    first.created_at = "2026-03-03T18:10:00"
    second = Operation(kind="dedupe", source="ytmusic", source_name="Clásicos", removed=8,
                       created_at="2026-03-04T10:02:00")
    h.record(first, t)
    h.record(second, [])
    assert [o.kind for o in h.list()] == ["dedupe", "transfer"]
    assert [o.kind for o in h.list(limit=1)] == ["dedupe"]


def test_preferences_are_saved_after_choosing_a_browser(new_pc):
    p = prefs.load(paths.prefs_file())
    p["browser"] = {"name": "Vivaldi", "kind": "vivaldi", "exe": r"C:\vivaldi.exe"}
    prefs.save(paths.prefs_file(), p)
    assert prefs.load(paths.prefs_file())["browser"]["name"] == "Vivaldi"


# ---------------------------------------------------------------- damaged files

def test_broken_preferences_fall_back_to_defaults_without_overwriting(new_pc):
    paths.prefs_file().write_text("{roto", encoding="utf-8")
    assert prefs.load(paths.prefs_file())["browser"] is None
    assert paths.prefs_file().read_text(encoding="utf-8") == "{roto"


def test_broken_history_is_set_aside_not_deleted(new_pc):
    paths.history_file().write_bytes(b"esto no es una base de datos" * 100)
    h = History(paths.history_file())
    op, tracks = sample_transfer()
    h.record(op, tracks)
    assert len(h.list()) == 1
    assert len(list(new_pc.glob("historial.dañado-*.sqlite"))) == 1


# ---------------------------------------------------------------- the repo carries nobody's data

PERSONAL = ("browser.json", "oauth.json", "tidal_session", "preferencias.json", "reporte_",
            ".yt-login", "browser-profiles", ".sqlite", ".db", ".env")


@pytest.mark.skipif(shutil.which("git") is None, reason="git no está instalado")
def test_repo_tracks_no_personal_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        pytest.skip("no es un repo de git")
    leaked = [f for f in out.stdout.splitlines() if any(p in f for p in PERSONAL)]
    assert leaked == []


def test_history_tiles_have_the_song_and_cap_with_total(new_pc):
    h = History(paths.history_file())
    op, tracks = sample_transfer()
    op_id = h.record(op, tracks)
    info = h.tiles([op_id], limit=3)[op_id]
    assert info["statuses"] == ["ok", "ok", "ya_estaba"] and info["total"] == 4
    assert info["songs"][0] == ("Luna de papel", "Los Faroles")
    assert h.tiles([]) == {} and History(new_pc / "no-existe.sqlite").tiles([1]) == {}
