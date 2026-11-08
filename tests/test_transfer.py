"""Motor de transferencia con servicios falsos: lo mismo que hacía el script, con eventos."""
from __future__ import annotations

import threading

import pytest

from app.core.transfer import ALREADY, Cancelled, Target, run_transfer, summary
from tests.fakes import FakeService, S


@pytest.fixture
def services():
    tidal = FakeService("tidal", "Tidal")
    tidal.add_playlist("src", "Favoritas", [
        S("Take On Me", "a-ha", dur=225, id="t1"),
        S("Mr. Blue Sky", "Electric Light Orchestra", dur=303, id="t2"),
        S("Hallelujah", "Jeff Buckley", dur=414, id="t3"),
        S("Bohemian Rhapsody", "Queen", dur=354, id="t4"),
        S("Take On Me", "a-ha", dur=225, id="t1b"),          # repetida en el origen
    ])
    yt = FakeService("ytmusic", "YouTube Music", catalog=[
        S("Take On Me (Official Video)", "a-ha", dur=244, id="y-tom"),
        S("Mr. Blue Sky", "Electric Light Orchestra", dur=304, id="y-blue"),
        S("Hallelujah", "Leonard Cohen", dur=279, id="y-cohen"),
        S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="y-boh-live"),
    ])
    yt.add_playlist("dst", "Favoritas", [S("Mr. Blue Sky - 2012 Remaster", "Electric Light Orchestra", dur=304, id="y-blue-old")])
    return tidal, yt


def collect():
    events = []
    return events, events.append


def test_events_follow_each_song_live(services):
    tidal, yt = services
    events, emit = collect()
    run_transfer(tidal, yt, "src", [Target("Favoritas", "dst")], dry_run=True, emit=emit)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "source" and events[0]["total"] == 5
    assert kinds.count("searching") == 5 and kinds.count("track") == 5
    searching = [e["n"] for e in events if e["type"] == "searching"]
    assert searching == [1, 2, 3, 4, 5]
    assert kinds[-1] == "target"


def test_statuses_and_alternatives(services):
    tidal, yt = services
    events, emit = collect()
    res = run_transfer(tidal, yt, "src", [Target("Favoritas", "dst")], dry_run=True, emit=emit)
    by_n = {t.n: t for t in res.tracks}
    assert by_n[1].status == "ok" and by_n[1].match.id == "y-tom"
    assert by_n[3].status == "no_encontrado" and by_n[3].match is None          # Cohen ≠ Buckley
    assert [a.id for a in by_n[3].alternatives] == ["y-cohen"]                  # pero se ofrece
    assert by_n[4].status == "dudoso"                                            # solo hay en vivo
    track3 = next(e for e in events if e["type"] == "track" and e["n"] == 3)
    assert track3["alternatives"][0]["artist"] == "Leonard Cohen"
    assert summary(res) == {"ok": 3, "doubtful": 1, "not_found": 1, "total": 5}


def test_dry_run_changes_nothing(services):
    tidal, yt = services
    before = {k: (n, list(s)) for k, (n, s) in yt.playlists.items()}
    res = run_transfer(tidal, yt, "src", [Target("Favoritas", "dst"), Target("Nueva")], dry_run=True)
    assert yt.calls == [] and yt.playlists == before
    new = res.targets[1]
    assert new.target.id is None and not new.created and len(new.added) == 3


def test_real_transfer_adds_only_what_is_missing(services):
    tidal, yt = services
    events, emit = collect()
    res = run_transfer(tidal, yt, "src", [Target("?", "dst")], emit=emit)
    t = res.targets[0]
    assert t.target.name == "Favoritas"                                          # nombre real del destino
    assert [s.id for s in t.added] == ["y-tom", "y-boh-live"]                    # ok y dudoso; Cohen no
    assert [(m.id, w.id, why) for m, w, why in t.versions] == [("y-blue", "y-blue-old", "ya estaba")]
    assert yt.calls == [("add", "dst", ["y-tom", "y-boh-live"])]
    already = [e for e in events if e["type"] == "already"]
    assert [(e["n"], e["as"]["title"]) for e in already] == [(2, "Mr. Blue Sky - 2012 Remaster")]


def test_new_playlist_is_created_only_if_there_is_something_to_add(services):
    tidal, yt = services
    res = run_transfer(tidal, yt, "src", [Target("De Tidal")])
    assert res.targets[0].created and ("create", "De Tidal") in yt.calls
    assert res.targets[0].url.endswith(res.targets[0].target.id)

    empty = FakeService("tidal", "Tidal")
    empty.add_playlist("x", "Nada", [S("Hallelujah", "Jeff Buckley", dur=414)])
    yt.calls.clear()
    res = run_transfer(empty, yt, "x", [Target("Vacía")])
    assert not res.targets[0].created and yt.calls == []


def test_duplicates_in_the_source_are_added_once(services):
    tidal, yt = services
    res = run_transfer(tidal, yt, "src", [Target("Nueva")])
    ids = [s.id for s in res.targets[0].added]
    assert ids.count("y-tom") == 1


def test_several_targets(services):
    tidal, yt = services
    yt.add_playlist("dst2", "Otra", [])
    res = run_transfer(tidal, yt, "src", [Target("a", "dst"), Target("b", "dst2")])
    assert [len(t.added) for t in res.targets] == [2, 3]


def test_cancel_during_search_changes_nothing(services):
    tidal, yt = services
    cancel = threading.Event()

    def emit(e):
        if e["type"] == "track" and e["n"] == 2:
            cancel.set()
    with pytest.raises(Cancelled):
        run_transfer(tidal, yt, "src", [Target("Nueva")], emit=emit, cancel=cancel)
    assert yt.calls == []


def test_thresholds_from_preferences(services):
    tidal, yt = services
    res = run_transfer(tidal, yt, "src", [Target("x", "dst")], dry_run=True, score_ok=0.99, score_min=0.0)
    assert {t.status for t in res.tracks} <= {"ok", "dudoso", "no_encontrado"}
    assert sum(t.status == "dudoso" for t in res.tracks) >= 2


def test_needs_a_target(services):
    tidal, yt = services
    with pytest.raises(ValueError):
        run_transfer(tidal, yt, "src", [])


def test_already_constant():
    assert ALREADY == "ya_estaba"
