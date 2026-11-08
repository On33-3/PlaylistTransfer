from dataclasses import asdict

import pytest

from app.core.matching import (DOUBTFUL, NO_MATCH_CAP, NOT_FOUND, OK, SCORE_MIN, SCORE_OK, best_match,
                               classify, clean,
                               find_duplicates, norm, queries_for, same_song, score, title_keys)
from app.core.models import Song
from tests.conftest import COPIES, KEPT, NEGATIVE_PAIRS, OTHERS, load_legacy


# ---------------------------------------------------------------- repetidos del video

def test_video_finds_exactly_the_eight_copies(video_playlist):
    dups = find_duplicates(video_playlist)
    got = [(d.song.title, d.song.album, d.kept.title) for d in dups]
    expected = [(title, album, kept) for title, _, _, album, kept in COPIES]
    assert got == expected


def test_video_virtual_insanity_three_copies_keep_the_first(video_playlist):
    dups = [d for d in find_duplicates(video_playlist) if "Virtual Insanity" in d.song.title]
    assert len(dups) == 3
    first = next(s for s in video_playlist if s.title == "Virtual Insanity")
    assert all(d.kept is first for d in dups)
    assert first.album == "Pop Music: The Modern Era"


def test_video_duplicates_point_to_their_real_position(video_playlist):
    for d in find_duplicates(video_playlist):
        assert video_playlist[d.index] is d.song
        assert video_playlist.index(d.kept) < d.index    # queda siempre la que está primero


def test_video_kept_songs_are_never_marked(video_playlist):
    marked = {id(d.song) for d in find_duplicates(video_playlist)}
    originals = video_playlist[:len(KEPT) + len(OTHERS)]     # todo lo que no es copia
    assert not any(id(s) in marked for s in originals)


@pytest.mark.parametrize("title", [
    "Virtual Insanity (Official Video)",
    "Virtual Insanity [Remastered 2013]",
    "Virtual Insanity - Remastered 2013",
    "Jamiroquai - Virtual Insanity",
    "Virtual Insanity (feat. Nadie)",
])
def test_versions_of_the_same_song(title):
    base = Song("Virtual Insanity", ["Jamiroquai"], duration=228)
    assert same_song(base, Song(title, ["Jamiroquai"], duration=245))


def test_youtube_video_without_artist_field_but_artist_in_title():
    assert same_song(Song("Take On Me", ["a-ha"], duration=226),
                     Song("a-ha - Take On Me (Official Video)", [], duration=244))


# ---------------------------------------------------------------- negativos reales

@pytest.mark.parametrize("a,b", NEGATIVE_PAIRS, ids=lambda s: s.title if isinstance(s, Song) else "")
def test_negatives_are_not_the_same_song(a, b):
    assert not same_song(a, b)
    assert not same_song(b, a)


@pytest.mark.parametrize("a,b", NEGATIVE_PAIRS, ids=lambda s: s.title if isinstance(s, Song) else "")
def test_negatives_never_score_as_ok(a, b):
    assert score(a, b) < SCORE_OK
    assert score(b, a) < SCORE_OK


def test_negatives_in_one_playlist_have_no_duplicates():
    songs = [s for pair in NEGATIVE_PAIRS for s in pair]
    assert find_duplicates(songs) == []


@pytest.mark.parametrize("a,b", NEGATIVE_PAIRS, ids=lambda s: s.title if isinstance(s, Song) else "")
def test_best_match_prefers_the_right_one_between_lookalikes(a, b):
    cand_a = Song(a.title, a.artists, duration=a.duration, id="A")
    cand_b = Song(b.title, b.artists, duration=b.duration, id="B")
    assert best_match(a, [cand_b, cand_a])[0].id == "A"
    assert best_match(b, [cand_a, cand_b])[0].id == "B"


@pytest.mark.parametrize("a,b", NEGATIVE_PAIRS, ids=lambda s: s.title if isinstance(s, Song) else "")
def test_negatives_are_not_found_even_with_the_same_duration(a, b):
    """Antes, "Sunshine" vs "Sunkissed" con duraciones parecidas daba OK (0.79) y "From The Start"
    de Good Kid daba dudoso para el de Laufey: se habrían agregado. Ahora quedan como no encontrado."""
    b_same_length = Song(b.title, b.artists, duration=a.duration)
    assert classify(score(a, b_same_length)) == NOT_FOUND
    assert classify(score(b_same_length, a)) == NOT_FOUND


# ---------------------------------------------------------------- el artista como requisito

def test_same_title_by_another_artist_is_not_found():
    src = Song("Mr. Blue Sky", ["Electric Light Orchestra"], duration=303)
    cover = Song("Mr. Blue Sky", ["Cover Band Hits"], duration=304)
    assert classify(score(src, cover)) == NOT_FOUND


def test_artist_only_in_video_title_still_counts():
    src = Song("Come and Get Your Love", ["Redbone"], duration=301)
    video = Song("Redbone - Come and Get Your Love (Official Video)", ["Oldies Channel"], duration=300)
    assert classify(score(src, video)) == OK


def test_artist_channel_names_still_count():
    src = Song("Virtual Insanity", ["Jamiroquai"], duration=228)
    assert classify(score(src, Song("Virtual Insanity", ["JamiroquaiVEVO"], duration=229))) == OK
    assert classify(score(src, Song("Virtual Insanity", ["Jamiroquai - Topic"], duration=229))) == OK


def test_collaborations_match_on_any_artist():
    src = Song("Sunshine", ["Khai Dreams", "Atwood"], duration=167)
    assert classify(score(src, Song("Sunshine", ["khai dreams"], duration=167))) == OK
    assert classify(score(src, Song("Sunshine", ["Atwood"], duration=167))) == OK


def test_unknown_artist_is_at_most_doubtful():
    """Un video sin artista y sin el artista en el título: no se puede afirmar que sea el mismo."""
    src = Song("Take On Me", ["a-ha"], duration=226)
    assert classify(score(src, Song("Take On Me", [], duration=226))) == DOUBTFUL


def test_best_match_prefers_the_original_over_a_cover():
    src = Song("Take On Me", ["a-ha"], duration=226)
    cover = Song("Take On Me", ["Some Tribute Band"], duration=226, id="cover")
    original = Song("Take On Me (2015 Remaster)", ["a-ha"], duration=229, id="orig")
    assert best_match(src, [cover, original])[0].id == "orig"


# ---------------------------------------------------------------- coincidencias de los reportes reales

@pytest.mark.parametrize("src,cand", [
    (Song("Virtual Insanity (Remastered 2013)", ["Jamiroquai"], duration=341),
     Song("Virtual Insanity", ["Jamiroquai"], duration=341)),
    (Song("December, 1963 (Oh What a Night!)", ["The Four Seasons"], duration=212),
     Song("December, 1963 (Oh, What a Night) [2007 Remaster]", ["The Four Seasons"], duration=212)),
    (Song("Just the Two of Us (feat. Bill Withers)", ["Grover Washington, Jr.", "Bill Withers"], duration=438),
     Song("Just the Two of Us", ["Grover Washington, Jr."], duration=444)),
    (Song("Take on Me", ["a-ha"], duration=225), Song("Take On Me", ["a-ha"], duration=229)),
    (Song("Put Your Head On My Shoulder", ["Paul Anka", "Olivia Newton-John"], duration=287),
     Song("Put Your Head On My Shoulder", ["Paul Anka"], duration=163)),
])
def test_real_report_matches_are_ok(src, cand):
    assert classify(score(src, cand)) == OK


def test_isrc_wins():
    assert score(Song("A", ["X"], isrc="usabc123"), Song("Totally different", ["Y"], isrc="USABC123")) == 1.0


def test_classify_thresholds():
    assert classify(SCORE_OK) == OK
    assert classify(SCORE_MIN) == DOUBTFUL
    assert classify(SCORE_MIN - 0.001) == NOT_FOUND


def test_text_helpers():
    assert clean("Song (Official Video) - Remastered 2011") == "Song"
    assert clean("Song feat. Someone") == "Song"
    assert norm("Beyoncé & Jay-Z") == "beyonce and jay z"
    assert norm("夜に駆ける") == "夜に駆ける"     # respeta alfabetos no latinos
    assert title_keys(Song("Jamiroquai - Virtual Insanity [HD]")) == {"jamiroquai virtual insanity",
                                                                       "jamiroquai", "virtual insanity"}
    assert queries_for(Song("Mr. Blue Sky (Remastered)", ["Electric Light Orchestra"])) == [
        "Electric Light Orchestra Mr. Blue Sky", "Mr. Blue Sky"]


# ---------------------------------------------------------------- igual que el script de consola

def test_same_results_as_legacy_script(video_playlist):
    """Igual que el script, salvo el tope a propósito cuando el artista o el título no coinciden."""
    legacy = load_legacy()
    songs = video_playlist + [s for pair in NEGATIVE_PAIRS for s in pair]
    old = [legacy.Song(**asdict(s)) for s in songs]
    for i, a in enumerate(songs):
        for j, b in enumerate(songs):
            before = legacy.score(old[i], old[j])
            assert score(a, b) in (before, min(before, NO_MATCH_CAP)), (a.title, b.title)
            assert same_song(a, b) == legacy.same_song(old[i], old[j]), (a.title, b.title)
    assert [d.index for d in find_duplicates(songs)] == [i for i, _, _ in legacy.find_duplicates(old)]
