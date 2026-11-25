import pytest

from app.core.matching import (DOUBTFUL, NOT_FOUND, OK, SCORE_MIN, SCORE_OK, best_match,
                               classify, clean,
                               find_duplicates, norm, queries_for, same_song, score, title_keys)
from app.core.models import Song
from tests.conftest import COPIES, KEPT, NEGATIVE_PAIRS, OTHERS, ROOT


# ---------------------------------------------------------------- duplicates from the video

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
        assert video_playlist.index(d.kept) < d.index    # the first one always stays


def test_video_kept_songs_are_never_marked(video_playlist):
    marked = {id(d.song) for d in find_duplicates(video_playlist)}
    originals = video_playlist[:len(KEPT) + len(OTHERS)]     # everything that isn't a copy
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


# ---------------------------------------------------------------- real negatives

@pytest.mark.parametrize("a,b", NEGATIVE_PAIRS, ids=lambda s: s.title if isinstance(s, Song) else "")
def test_lookalikes_stay_apart(a, b):
    """Before, "Sunshine" vs "Sunkissed" with similar lengths gave OK (0.79) and Good Kid's "From The Start"
    gave doubtful for Laufey's: they would have been added. Now they end up not found."""
    assert not same_song(a, b) and not same_song(b, a)
    b_same_length = Song(b.title, b.artists, duration=a.duration)
    assert classify(score(a, b_same_length)) == NOT_FOUND and classify(score(b_same_length, a)) == NOT_FOUND
    cand_a, cand_b = Song(a.title, a.artists, duration=a.duration, id="A"), Song(b.title, b.artists, duration=b.duration, id="B")
    assert best_match(a, [cand_b, cand_a])[0].id == "A" and best_match(b, [cand_a, cand_b])[0].id == "B"


def test_negatives_in_one_playlist_have_no_duplicates():
    songs = [s for pair in NEGATIVE_PAIRS for s in pair]
    assert find_duplicates(songs) == []


# ---------------------------------------------------------------- the artist as a requirement

def test_same_title_by_another_artist_is_not_found():
    src = Song("Mr. Blue Sky", ["Electric Light Orchestra"], duration=303)
    cover = Song("Mr. Blue Sky", ["Cover Band Hits"], duration=304)
    assert classify(score(src, cover)) == NOT_FOUND


def test_collaborations_match_on_any_artist():
    src = Song("Sunshine", ["Khai Dreams", "Atwood"], duration=167)
    assert classify(score(src, Song("Sunshine", ["khai dreams"], duration=167))) == OK
    assert classify(score(src, Song("Sunshine", ["Atwood"], duration=167))) == OK


def test_unknown_artist_is_at_most_doubtful():
    """A video with no artist and no artist in the title: it can't be claimed to be the same."""
    src = Song("Take On Me", ["a-ha"], duration=226)
    assert classify(score(src, Song("Take On Me", [], duration=226))) == DOUBTFUL


def test_best_match_prefers_the_original_over_a_cover():
    src = Song("Take On Me", ["a-ha"], duration=226)
    cover = Song("Take On Me", ["Some Tribute Band"], duration=226, id="cover")
    original = Song("Take On Me (2015 Remaster)", ["a-ha"], duration=229, id="orig")
    assert best_match(src, [cover, original])[0].id == "orig"


# ---------------------------------------------------------------- matches from the real reports

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
    assert norm("夜に駆ける") == "夜に駆ける"     # keeps non-Latin alphabets
    assert title_keys(Song("Jamiroquai - Virtual Insanity [HD]")) == {"jamiroquai virtual insanity",
                                                                       "jamiroquai", "virtual insanity"}
    assert queries_for(Song("Mr. Blue Sky (Remastered)", ["Electric Light Orchestra"])) == [
        "Electric Light Orchestra Mr. Blue Sky", "Mr. Blue Sky"]


# ---------------------------------------------------------------- same as the console script

def test_same_duplicates_as_legacy_script_on_the_real_playlist(video_playlist):
    """In the real playlist (no covers, parts or intros) the duplicates are the same as with the
    console script (since removed: what it decided is kept in tests/data/legacy_parity.json).
    The score changed on purpose: it is checked with tools/check_reports.py."""
    import json
    old = json.loads((ROOT / "tests" / "data" / "legacy_parity.json").read_text(encoding="utf-8"))
    songs = video_playlist + [s for pair in NEGATIVE_PAIRS for s in pair]
    assert [s.title for s in songs] == old["titles"]                   # the same test playlist
    for i, a in enumerate(songs):
        for j, b in enumerate(songs):
            assert same_song(a, b) == (old["same_song"][i][j] == "1"), (a.title, b.title)
            assert classify(score(a, b)) in (old["classify"][i][j], NOT_FOUND), (a.title, b.title)  # only stricter
    assert [d.index for d in find_duplicates(songs)] == old["duplicates"]


# ---------------------------------------------------------------- SongIndex: the same as walking the list

def _library(seed, size):
    """A made-up, hard library: almost equal titles, versions, no artist, repeated ids and ISRCs."""
    import random
    from app.core.models import Song
    r = random.Random(seed)
    words = ["love", "night", "fire", "city", "dream", "blue", "river", "amor", "noche", "x", "intro"]
    extras = ["", " (Live)", " (Remastered 2011)", " - Remaster", " Pt. 2", " II", " (Official Video)", " [Mono]", " feat. Ana"]
    out = []
    for _ in range(size):
        title = " ".join(r.choices(words, k=r.randint(1, 4)))
        if r.random() < .4:                                # a typo: the 0.92 similarity case
            i = r.randrange(len(title))
            title = title[:i] + r.choice("aeiouz") + title[i + 1:]
        if r.random() < .15:
            title = f"Artista {r.randint(1, 4)} - {title}"
        artists = r.choice([[], [f"Artista {r.randint(1, 4)}"], [f"Artista {r.randint(1, 4)} & Otro"]])
        out.append(Song(title + r.choice(extras), artists, duration=r.choice([None, 200, 203, 240]),
                        isrc=r.choice([None, None, "usabc1", "USABC2"]), id=r.choice([None, f"i{r.randint(1, 60)}"])))
    return out


@pytest.mark.parametrize("seed", range(3))
def test_the_index_finds_exactly_what_walking_the_list_finds(seed):
    """The index only skips impossible comparisons: the first result is always the same object."""
    from app.core.matching import SongIndex, same_song
    songs = _library(seed, 200)
    index = SongIndex(songs[:100])
    for s in songs[100:]:
        walked = next((k for k in songs[:100] if same_song(s, k)), None)
        assert index.first_match(s) is walked, s


def test_a_big_library_does_not_compare_every_song_with_every_other(monkeypatch):
    """What made 5000 songs take 11 minutes: it is counted (not timed, so a slow PC doesn't fail it)."""
    from app.core import matching
    from app.core.models import Song
    real, calls = matching.same_song, []
    monkeypatch.setattr(matching, "same_song", lambda a, b: calls.append(1) or real(a, b))
    import random
    r = random.Random(1)
    title = lambda: "".join(r.choices("abcdefghijklmnopqrstuvwxyz ", k=24))     # titles that aren't alike
    songs = [Song(title(), [f"Artista {i}"], id=f"s{i}") for i in range(2000)]
    assert matching.find_duplicates(songs) == []
    assert len(calls) < len(songs)                         # it used to be almost 2 million
