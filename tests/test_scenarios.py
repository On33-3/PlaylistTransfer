"""Catalog of matching scenarios.

Each case says what has to happen in the engine's two uses:
  - transferring: `classify(score(source, candidate))` → ok / doubtful / not found
  - removing duplicates: `same_song(a, b)` → True if one is a copy of the other

Golden rule: when in doubt, the result has to be the safe one. When transferring, "not found"
or "doubtful" is better than adding another song as OK. When cleaning, missing a duplicate is
better than removing a song that wasn't one.
"""
from __future__ import annotations

from typing import NamedTuple

import pytest

from app.core.matching import (DOUBTFUL, NOT_FOUND, OK, alternatives, artist_names, artist_sim, best_match, classify,
                               find_duplicates, numbers, same_song, score, title_sim)
from app.core.models import OTHER_VERSION, SAME, Song


def S(title, *artists, dur=None, album=None, isrc=None, id=None) -> Song:
    return Song(title, list(artists), album=album, duration=dur, isrc=isrc, id=id)


class Case(NamedTuple):
    id: str
    src: Song
    cand: Song
    transfer: str        # expected when transferring
    duplicate: bool      # expected when finding duplicates


def run(case: Case) -> None:
    got = classify(score(case.src, case.cand))
    assert got == case.transfer, f"transferir: {score(case.src, case.cand)} → {got}"
    assert same_song(case.src, case.cand) == case.duplicate, "repetidos (a, b)"
    assert same_song(case.cand, case.src) == case.duplicate, "repetidos (b, a)"


def ids(cases):
    return [c.id for c in cases]


# ================================================================ A. The same recording written differently

SAME_RECORDING = [
    Case("remaster entre paréntesis (Tidal agrega la versión)",
         S("Virtual Insanity (Remastered 2013)", "Jamiroquai", dur=341), S("Virtual Insanity", "Jamiroquai", dur=341), OK, True),
    Case("remaster con guion", S("Mr. Blue Sky", "Electric Light Orchestra", dur=303),
         S("Mr. Blue Sky - 2012 Remaster", "Electric Light Orchestra", dur=304), OK, True),
    Case("remaster entre corchetes", S("December, 1963 (Oh What a Night!)", "The Four Seasons", dur=212),
         S("December, 1963 (Oh, What a Night) [2007 Remaster]", "The Four Seasons", dur=212), OK, True),
    Case("video oficial", S("Take On Me", "a-ha", dur=226), S("Take On Me (Official Video)", "a-ha", dur=244), OK, True),
    Case("video con letra", S("Sweater Weather", "The Neighbourhood", dur=240),
         S("Sweater Weather [Lyric Video]", "The Neighbourhood", dur=241), OK, True),
    Case("visualizer", S("As It Was", "Harry Styles", dur=167), S("As It Was (Visualizer)", "Harry Styles", dur=167), OK, True),
    Case("radio edit", S("Midnight City", "M83", dur=243), S("Midnight City (Radio Edit)", "M83", dur=242), OK, True),
    Case("mono vs estéreo", S("(I Can't Get No) Satisfaction (Mono)", "The Rolling Stones", dur=224),
         S("(I Can't Get No) Satisfaction", "The Rolling Stones", dur=223), OK, True),
    Case("video 'Artista - Tema' subido por otro canal",
         S("Come and Get Your Love", "Redbone", dur=301),
         S("Redbone - Come and Get Your Love (Official Video)", "Oldies Channel", dur=300), OK, True),
    Case("video sin artista, con el artista en el título",
         S("Take On Me", "a-ha", dur=226), S("a-ha - Take On Me (Official Video)", dur=244), OK, True),
    Case("canal VEVO", S("Virtual Insanity", "Jamiroquai", dur=228), S("Virtual Insanity", "JamiroquaiVEVO", dur=229), OK, True),
    Case("canal '- Topic'", S("Virtual Insanity", "Jamiroquai", dur=228),
         S("Virtual Insanity", "Jamiroquai - Topic", dur=229), OK, True),
    Case("video de K-pop con comillas y MV",
         S("Dynamite", "BTS", dur=199), S("BTS (방탄소년단) 'Dynamite' Official MV", "HYBE LABELS", dur=223), OK, False),
    Case("mayúsculas", S("Take on Me", "a-ha", dur=225), S("Take On Me", "a-ha", dur=229), OK, True),
    Case("acentos", S("Corazón Espinado", "Santana", "Maná", dur=276), S("Corazon Espinado", "Santana", dur=275), OK, True),
    Case("& o and en el título", S("Rock & Roll", "Led Zeppelin", dur=220), S("Rock and Roll", "Led Zeppelin", dur=221), OK, True),
    Case("puntuación", S("Mr. Blue Sky", "Electric Light Orchestra", dur=303), S("Mr Blue Sky", "ELO", "Electric Light Orchestra", dur=303), OK, True),
    Case("apóstrofo que falta", S("Im Doing Fine", "MARINO", dur=180), S("I'm Doing Fine", "MARINO", dur=181), OK, True),
    Case("'The' en el artista", S("Let It Be", "The Beatles", dur=243), S("Let It Be", "Beatles", dur=243), OK, True),
    Case("artista con barra", S("Thunderstruck", "AC/DC", dur=292), S("Thunderstruck", "ACDC", dur=292), OK, True),
    Case("alfabeto no latino", S("夜に駆ける", "YOASOBI", dur=261), S("夜に駆ける", "YOASOBI", dur=261), OK, True),
    Case("duración con unos segundos de diferencia", S("Take On Me", "a-ha", dur=225), S("Take On Me", "a-ha", dur=228), OK, True),
    Case("sin duración en un servicio", S("Take On Me", "a-ha", dur=225), S("Take On Me", "a-ha"), OK, True),
    Case("mismo ISRC con el título en otro idioma", S("Gangnam Style", "PSY", isrc="KRA381200234"),
         S("강남스타일", "싸이", isrc="kra381200234"), OK, True),
    # Found by the first user (2026-11-08): in one service part of the title is in parentheses and in
    # the other it isn't (and has "Artist - " in front). Parentheses are almost always noise, but here they are the title
    Case("parte del título entre paréntesis en un servicio y sin paréntesis en el otro",
         S("(I Can't Get No) Satisfaction", "The Rolling Stones", dur=223),
         S("The Rolling Stones - I Can't Get No Satisfaction", "The Rolling Stones", dur=224), OK, True),
    Case("'Artista - (texto)' en un servicio y 'texto' sin paréntesis en el otro",
         S("(Don't Fear) The Reaper", "Blue Öyster Cult", dur=308),
         S("Blue Oyster Cult - Don't Fear The Reaper", "Blue Öyster Cult", dur=309), OK, True),
    Case("'(Artista) - Tema' con el artista entre paréntesis",
         S("Bohemian Rhapsody", "Queen", dur=354), S("(Queen) - Bohemian Rhapsody", "Queen", dur=355), OK, True),
]


@pytest.mark.parametrize("case", SAME_RECORDING, ids=ids(SAME_RECORDING))
def test_same_recording(case):
    run(case)


# ================================================================ B. Several artists on the same song

MANY_ARTISTS = [
    Case("dúo: en un servicio los dos, en el otro uno",
         S("Under Pressure", "Queen", "David Bowie", dur=248), S("Under Pressure", "David Bowie", dur=248), OK, True),
    Case("dúo en un solo texto con &",
         S("Under Pressure", "Queen", "David Bowie", dur=248), S("Under Pressure", "Queen & David Bowie", dur=248), OK, True),
    Case("dúo con x", S("Happier", "Marshmello", "Bastille", dur=214), S("Happier", "Marshmello x Bastille", dur=214), OK, True),
    Case("feat. dentro del artista",
         S("This Is What You Came For", "Calvin Harris", "Rihanna", dur=222),
         S("This Is What You Came For", "Calvin Harris feat. Rihanna", dur=222), OK, True),
    Case("feat. en el título y no en los artistas",
         S("Just the Two of Us (feat. Bill Withers)", "Grover Washington, Jr.", "Bill Withers", dur=438),
         S("Just the Two of Us", "Grover Washington, Jr.", dur=444), OK, True),
    Case("artistas en otro orden",
         S("Put Your Head On My Shoulder", "Paul Anka", "Olivia Newton-John", dur=287),
         S("Put Your Head On My Shoulder", "Olivia Newton-John", "Paul Anka", dur=287), OK, True),
    Case("solo figura el invitado",
         S("Just the Two of Us", "Grover Washington, Jr.", "Bill Withers", dur=438),
         S("Just the Two of Us", "Bill Withers", dur=438), OK, True),
    Case("coma dentro del nombre de la banda",
         S("September", "Earth, Wind & Fire", dur=215), S("September", "Earth Wind and Fire", dur=218), OK, True),
    Case("banda con & escrita con and",
         S("The Sound of Silence", "Simon & Garfunkel", dur=185), S("The Sound of Silence", "Simon and Garfunkel", dur=185), OK, True),
    Case("dúo con 'y' en español", S("Dueles", "Jesse & Joy", dur=244), S("Dueles", "Jesse y Joy", dur=244), OK, True),
    Case("tres artistas, coincide uno",
         S("Fly Away", "Khai Dreams", "Matt Jordan", "Atwood", dur=180), S("Fly Away", "Atwood", dur=180), OK, True),
]


@pytest.mark.parametrize("case", MANY_ARTISTS, ids=ids(MANY_ARTISTS))
def test_many_artists(case):
    run(case)


# ================================================================ C. The same song sung by another artist

OTHER_PERFORMER = [
    Case("versión de otro artista conocido", S("Hallelujah", "Jeff Buckley", dur=414),
         S("Hallelujah", "Leonard Cohen", dur=279), NOT_FOUND, False),
    Case("versión famosa vs la original", S("I Will Always Love You", "Whitney Houston", dur=271),
         S("I Will Always Love You", "Dolly Parton", dur=180), NOT_FOUND, False),
    Case("versión con duración parecida", S("Hurt", "Johnny Cash", dur=218), S("Hurt", "Nine Inch Nails", dur=220), NOT_FOUND, False),
    Case("misma duración exacta", S("Respect", "Aretha Franklin", dur=147), S("Respect", "Otis Redding", dur=147), NOT_FOUND, False),
    Case("cover casero que nombra al original", S("Hallelujah", "Jeff Buckley", dur=414),
         S("Hallelujah (Jeff Buckley Cover)", "Some Youtuber", dur=410), NOT_FOUND, False),
    Case("cover sin artista, con guiones", S("Hallelujah", "Jeff Buckley", dur=414),
         S("Some Youtuber - Hallelujah - Jeff Buckley cover", dur=412), NOT_FOUND, False),
    Case("karaoke 'in the style of'", S("Hallelujah", "Jeff Buckley", dur=414),
         S("Hallelujah (Karaoke Version) [In the Style of Jeff Buckley]", "Karaoke Hits", dur=414), NOT_FOUND, False),
    Case("banda tributo con el nombre del original", S("Bohemian Rhapsody", "Queen", dur=354),
         S("Bohemian Rhapsody", "Queen Tribute Band", dur=355), NOT_FOUND, False),
    Case("artista cuyo nombre contiene al otro", S("Go With the Flow", "Queen", dur=187),
         S("Go With the Flow", "Queens of the Stone Age", dur=187), NOT_FOUND, False),
    Case("homónimas sin relación", S("Hello", "Adele", dur=295), S("Hello", "Lionel Richie", dur=251), NOT_FOUND, False),
    Case("negativo real: Laufey ≠ Good Kid", S("From The Start", "Laufey", dur=170),
         S("From the Start", "Good Kid", dur=168), NOT_FOUND, False),
    Case("si el origen es un cover, se busca ese cover", S("Hallelujah (Jeff Buckley Cover)", "Some Youtuber", dur=410),
         S("Hallelujah (Jeff Buckley Cover)", "Some Youtuber", dur=411), OK, True),
    Case("banda llamada como una palabra de versión", S("Lightning Crashes", "Live", dur=325),
         S("Live - Lightning Crashes (Official Video)", "LiveVEVO", dur=330), OK, True),
]


@pytest.mark.parametrize("case", OTHER_PERFORMER, ids=ids(OTHER_PERFORMER))
def test_other_performer(case):
    run(case)


def test_other_performers_are_offered_as_alternatives_never_added():
    src = S("Hallelujah", "Jeff Buckley", dur=414)
    results = [S("Hallelujah", "Leonard Cohen", dur=279, id="cohen"),
               S("Hallelujah (Karaoke Version)", "Karaoke Hits", dur=414, id="karaoke"),
               S("Hallelujah (Jeff Buckley Cover)", "Some Youtuber", dur=410, id="youtuber"),
               S("Hallelujah", "Pentatonix", dur=268, id="ptx"),
               S("Hallelujah (Live)", "Leonard Cohen", dur=440, id="cohen-live"),
               S("Hallelujah, I Love Her So", "Ray Charles", dur=155, id="ray")]
    assert classify(best_match(src, results)[1]) == NOT_FOUND
    assert [a.id for a in alternatives(src, results)] == ["cohen", "ptx"]


def test_the_right_artist_wins_between_versions():
    src = S("Hurt", "Johnny Cash", dur=218)
    found, sc = best_match(src, [S("Hurt", "Nine Inch Nails", dur=218, id="nin"),
                                 S("Hurt", "Johnny Cash", dur=216, id="cash"),
                                 S("Hurt (Johnny Cash Cover)", "Guitar Guy", dur=218, id="cover")])
    assert found.id == "cash" and classify(sc) == OK


# ================================================================ D. Another song by the same artist

OTHER_SONG_SAME_ARTIST = [
    Case("negativo real: Sunshine ≠ Sunkissed", S("Sunshine", "Khai Dreams", "Atwood", dur=167),
         S("Sunkissed", "Khai Dreams", dur=165), NOT_FOUND, False),
    Case("negativo real: Spirit In The Sky ≠ Marcy", S("Spirit In The Sky", "Norman Greenbaum", dur=242),
         S("Marcy (Deluxe Edition)", "Norman Greenbaum", dur=240), NOT_FOUND, False),
    Case("un título contiene al otro", S("One", "U2", dur=276), S("One Tree Hill", "U2", dur=323), NOT_FOUND, False),
    Case("parte 1 vs parte 2", S("Another Brick in the Wall, Pt. 1", "Pink Floyd", dur=191),
         S("Another Brick in the Wall (Part 2)", "Pink Floyd", dur=239), NOT_FOUND, False),
    Case("parte en romanos vs números", S("Another Brick in the Wall, Pt. II", "Pink Floyd", dur=239),
         S("Another Brick in the Wall (Part 2)", "Pink Floyd", dur=239), OK, True),
    Case("secuela con número romano", S("The Unforgiven", "Metallica", dur=387),
         S("The Unforgiven II", "Metallica", dur=396), NOT_FOUND, False),
    Case("reprise", S("Sgt. Pepper's Lonely Hearts Club Band", "The Beatles", dur=122),
         S("Sgt. Pepper's Lonely Hearts Club Band (Reprise)", "The Beatles", dur=79), NOT_FOUND, False),
    Case("'Intro' de otro álbum", S("Intro", "The xx", album="xx", dur=128),
         S("Intro", "The xx", album="Coexist", dur=127), NOT_FOUND, False),
    Case("'Intro' del mismo álbum (edición deluxe)", S("Intro", "The xx", album="xx", dur=128),
         S("Intro", "The xx", album="xx (Deluxe Edition)", dur=128), OK, True),
    Case("'Interlude' sin álbum, misma duración", S("Interlude", "Khai Dreams", dur=64),
         S("Interlude", "Khai Dreams", dur=65), OK, True),
    Case("'Interlude' sin álbum, otra duración", S("Interlude", "Khai Dreams", dur=64),
         S("Interlude", "Khai Dreams", dur=95), NOT_FOUND, False),
]


@pytest.mark.parametrize("case", OTHER_SONG_SAME_ARTIST, ids=ids(OTHER_SONG_SAME_ARTIST))
def test_other_song_same_artist(case):
    run(case)


# ================================================================ E. Another version of the same song

OTHER_VERSION_CASES = [
    Case("en vivo", S("Bohemian Rhapsody", "Queen", dur=354), S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356), DOUBTFUL, True),
    Case("en vivo con guion", S("Bohemian Rhapsody", "Queen", dur=354),
         S("Bohemian Rhapsody - Live at Wembley '86", "Queen", dur=360), DOUBTFUL, True),
    Case("en vivo en español", S("Corazón Espinado", "Maná", dur=276), S("Corazón Espinado (En Vivo)", "Maná", dur=290), DOUBTFUL, True),
    Case("unplugged", S("Layla", "Eric Clapton", dur=424), S("Layla (Unplugged)", "Eric Clapton", dur=286), DOUBTFUL, True),
    Case("acústica", S("Somebody To Love", "Abhi The Nomad", dur=200),
         S("Somebody To Love (acoustic)", "Abhi The Nomad", dur=198), DOUBTFUL, True),
    Case("remix", S("Shape of You", "Ed Sheeran", dur=233),
         S("Shape of You (Major Lazer Remix)", "Ed Sheeran", "Major Lazer", dur=210), DOUBTFUL, True),
    Case("sped up", S("her", "JVKE", dur=150), S("her (sped up)", "JVKE", dur=120), DOUBTFUL, True),
    Case("slowed + reverb", S("her", "JVKE", dur=150), S("her (slowed + reverb)", "JVKE", dur=190), DOUBTFUL, True),
    Case("regrabación (Taylor's Version)", S("Love Story", "Taylor Swift", dur=235),
         S("Love Story (Taylor's Version)", "Taylor Swift", dur=235), DOUBTFUL, True),
    Case("instrumental", S("Sweater Weather", "The Neighbourhood", dur=240),
         S("Sweater Weather (Instrumental)", "The Neighbourhood", dur=240), DOUBTFUL, True),
    Case("demo", S("Yesterday", "The Beatles", dur=125), S("Yesterday (Take 1 / Demo)", "The Beatles", dur=150), DOUBTFUL, True),
    Case("las dos en vivo", S("Bohemian Rhapsody (Live)", "Queen", dur=356),
         S("Bohemian Rhapsody - Live", "Queen", dur=357), OK, True),
]


@pytest.mark.parametrize("case", OTHER_VERSION_CASES, ids=ids(OTHER_VERSION_CASES))
def test_other_version(case):
    run(case)


def test_original_wins_over_versions_when_both_exist():
    src = S("her", "JVKE", dur=150)
    found, sc = best_match(src, [S("her (sped up)", "JVKE", dur=120, id="sped"),
                                 S("her (Live)", "JVKE", dur=160, id="live"),
                                 S("her", "JVKE", dur=151, id="orig")])
    assert found.id == "orig" and classify(sc) == OK


# ================================================================ F. Removing duplicates in a playlist

def test_same_video_twice_is_a_duplicate():
    a = S("Take On Me", "a-ha", dur=226, id="v1")
    dups = find_duplicates([a, S("Take On Me", "a-ha", dur=226, id="v1")])
    assert len(dups) == 1 and dups[0].kind == SAME


def test_other_versions_are_marked_so_the_ui_leaves_them_unchecked():
    songs = [S("Bohemian Rhapsody", "Queen", dur=354, id="1"),
             S("Bohemian Rhapsody (Official Video)", "Queen", dur=359, id="2"),
             S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="3")]
    assert [(d.song.id, d.kind) for d in find_duplicates(songs)] == [("2", SAME), ("3", OTHER_VERSION)]


def test_versions_by_other_artists_are_kept():
    songs = [S("Hallelujah", "Jeff Buckley", dur=414), S("Hallelujah", "Leonard Cohen", dur=279),
             S("Hallelujah", "Pentatonix", dur=268), S("Hallelujah (Jeff Buckley Cover)", "Some Youtuber", dur=410),
             S("Hallelujah (Karaoke Version)", "Karaoke Hits", dur=414)]
    assert find_duplicates(songs) == []


def test_parts_sequels_and_intros_are_kept():
    songs = [S("Another Brick in the Wall, Pt. 1", "Pink Floyd", dur=191),
             S("Another Brick in the Wall, Pt. 2", "Pink Floyd", dur=239),
             S("Another Brick in the Wall, Pt. 3", "Pink Floyd", dur=78),
             S("The Unforgiven", "Metallica", dur=387), S("The Unforgiven II", "Metallica", dur=396),
             S("The Unforgiven III", "Metallica", dur=466),
             S("Intro", "The xx", album="xx", dur=128), S("Intro", "The xx", album="Coexist", dur=127),
             S("Sgt. Pepper's Lonely Hearts Club Band", "The Beatles", dur=122),
             S("Sgt. Pepper's Lonely Hearts Club Band (Reprise)", "The Beatles", dur=79)]
    assert find_duplicates(songs) == []


def test_duo_written_differently_is_a_duplicate():
    songs = [S("Under Pressure", "Queen", "David Bowie", dur=248), S("Under Pressure", "Queen & David Bowie", dur=248)]
    assert [d.index for d in find_duplicates(songs)] == [1]


def test_without_artist_only_the_duration_decides():
    a = S("Take On Me", dur=226)
    assert same_song(a, S("Take On Me", dur=229))
    assert not same_song(a, S("Take On Me", dur=260))


def test_the_first_one_stays_even_if_it_is_the_video():
    video = S("Virtual Insanity (Official Video)", "Jamiroquai", dur=245)
    album = S("Virtual Insanity", "Jamiroquai", dur=228)
    [dup] = find_duplicates([video, album])
    assert dup.kept is video and dup.song is album


# ================================================================ G. Known limits (they fail on the safe side)

def test_limit_artist_alias_is_not_detected_as_duplicate():
    """'P!nk' and 'Pink' are the same artist. When transferring it is found, but when cleaning it isn't
    detected as a duplicate: that's the safe side (nothing is removed)."""
    a, b = S("So What", "P!nk", dur=215), S("So What", "Pink", dur=215)
    assert classify(score(a, b)) == OK
    assert not same_song(a, b)


def test_limit_artist_in_another_alphabet_needs_isrc():
    """'米津玄師' is 'Kenshi Yonezu': without an ISRC there's no way to know. It stays not found."""
    a, b = S("Lemon", "米津玄師", dur=255), S("Lemon", "Kenshi Yonezu", dur=255)
    assert classify(score(a, b)) == NOT_FOUND
    assert classify(score(S("Lemon", "米津玄師", isrc="JPU901800001"),
                          S("Lemon", "Kenshi Yonezu", isrc="JPU901800001"))) == OK


def test_limit_translated_title_without_isrc():
    """A translated title ("La Bamba" / "The Bamba") without an ISRC stays not found."""
    assert classify(score(S("La Bamba", "Ritchie Valens", dur=124), S("The Bamba", "Ritchie Valens", dur=124))) == NOT_FOUND


def test_limit_live_word_in_both_titles():
    """If the title already has the word ("Live and Let Die"), the live version can't be told apart:
    it ends up OK instead of doubtful. Rare and low risk (it's the same song by the same artist)."""
    assert classify(score(S("Live and Let Die", "Wings", dur=192),
                          S("Live and Let Die (Live)", "Wings", dur=200))) == OK


# ================================================================ H. Classical music and numbered pieces
# (found with the stress test: "Gymnopédie No. 1" and "No. 3" were taken as duplicates)

CLASSICAL = [
    Case("otra obra con el mismo nombre y otro número", S("Gymnopédie No. 1", "Erik Satie", dur=200),
         S("Gymnopédie No. 3", "Erik Satie", dur=198), NOT_FOUND, False),
    Case("sonata con otro número", S("Piano Sonata No. 14 in C-Sharp Minor", "Ludwig van Beethoven", dur=900),
         S("Piano Sonata No. 8 in C Minor", "Ludwig van Beethoven", dur=905), NOT_FOUND, False),
    Case("mismo opus, otro número", S("Nocturne in E-Flat Major, Op. 9 No. 2", "Frédéric Chopin", dur=270),
         S("Nocturne in B-Flat Minor, Op. 9 No. 1", "Frédéric Chopin", dur=272), NOT_FOUND, False),
    Case("otro movimiento de la misma obra",
         S("The Four Seasons, Violin Concerto No. 1 in E Major, RV 269 Spring: I. Allegro", "Antonio Vivaldi", dur=200),
         S("The Four Seasons, Violin Concerto No. 1 in E Major, RV 269 Spring: II. Largo", "Antonio Vivaldi", dur=202),
         NOT_FOUND, False),
    Case("temas numerados", S("Track 1", "Alguien", dur=180), S("Track 2", "Alguien", dur=181), NOT_FOUND, False),
    Case("la misma obra remasterizada", S("Gymnopédie No. 1", "Erik Satie", dur=200),
         S("Gymnopédie No. 1 (Remastered 2015)", "Erik Satie", dur=201), OK, True),
    Case("la misma obra en vivo", S("Symphony No. 9 in D Minor, Op. 125", "Ludwig van Beethoven", dur=4000),
         S("Symphony No. 9 in D Minor, Op. 125 (Live 1985)", "Ludwig van Beethoven", dur=4010), DOUBTFUL, True),
    Case("un año en el título no cuenta como número", S("December, 1963 (Oh What a Night!)", "The Four Seasons", dur=212),
         S("December, 1963 (Oh, What a Night)", "The Four Seasons", dur=213), OK, True),
]


@pytest.mark.parametrize("case", CLASSICAL, ids=ids(CLASSICAL))
def test_classical_and_numbered(case):
    run(case)


def test_classical_playlist_keeps_every_piece():
    songs = [S(f"Gymnopédie No. {i}", "Erik Satie", dur=200) for i in (1, 2, 3)] + [
        S("Spring: I. Allegro", "Antonio Vivaldi", dur=200), S("Spring: II. Largo", "Antonio Vivaldi", dur=160),
        S("Spring: III. Allegro pastorale", "Antonio Vivaldi", dur=250)]
    assert find_duplicates(songs) == []


def test_limit_number_on_one_side_only():
    """"Song 2" vs "Song" (Blur): only one has a number, and telling them apart for that would break "Live at
    Wembley '86" vs the original. It used to be added on its own; now the extra "2" leaves it for review."""
    assert classify(score(S("Song 2", "Blur", dur=122), S("Song", "Blur", dur=122))) == DOUBTFUL
    assert classify(score(S("Bohemian Rhapsody", "Queen", dur=354),
                          S("Bohemian Rhapsody - Live at Wembley '86", "Queen", dur=360))) == DOUBTFUL


# ================================================================ The limits of each rule
# Found with bugs introduced on purpose (mutation testing): a small change in these numbers or
# conditions got through without any test noticing.

@pytest.mark.parametrize("seconds, same", [(5, True), (6, False)])
def test_without_artist_the_duration_decides_up_to_5_seconds(seconds, same):
    assert same_song(S("Tema Largo", dur=200), S("Tema Largo", "Alguien", dur=200 + seconds)) is same


@pytest.mark.parametrize("a, b, same", [
    (S("Intro", "A", album="Disco Uno", dur=60), S("Intro", "A", album="Disco Uno (Deluxe)", dur=90), True),
    (S("Intro", "A", album="Disco Uno", dur=60), S("Intro", "A", album="Otra Cosa", dur=60), False),
    (S("Intro", "A", album="Disco Uno", dur=60), S("Intro", "A", dur=63), True),       # an album is missing: the length decides
    (S("Intro", "A", album="Disco Uno", dur=60), S("Intro", "A", dur=64), False),
])
def test_a_generic_title_needs_the_same_album_or_length(a, b, same):
    assert same_song(a, b) is same


def test_a_movement_only_counts_if_both_titles_have_one():
    assert same_song(S("Spring: I. Allegro", "Vivaldi"), S("Spring Allegro", "Vivaldi"))
    assert not same_song(S("Spring: I. Allegro", "Vivaldi"), S("Spring: II. Allegro", "Vivaldi"))


@pytest.mark.parametrize("seconds, expected", [(3, 1.0), (4, 0.994), (17, 0.922), (60, 0.85)])
def test_the_length_weighs_little_and_gradually(seconds, expected):
    assert score(S("Tema", "Artista", dur=200), S("Tema", "Artista", dur=200 + seconds)) == expected


def test_a_missing_length_counts_half():
    assert score(S("Tema", "Artista"), S("Tema", "Artista", dur=200)) == 0.925


def test_noise_around_the_title_and_a_longer_artist_name_score_high_but_not_perfect():
    assert title_sim(S("Dynamite", "BTS"), S("BTS Dynamite Official MV", "BTS")) == 0.95
    assert artist_sim(S("X", "Queen"), S("X", "Queen Band")) == 0.9


@pytest.mark.parametrize("year, counts", [(1899, True), (1900, False), (2099, False), (2100, True)])
def test_a_year_in_the_title_is_not_a_track_number(year, counts):
    assert bool(numbers(S(f"Tema {year}", "A"))) is counts


def test_very_short_pieces_of_an_artist_name_do_not_count():
    assert dict(artist_names(S("X", "Al & Queen")))["al and queen"] == {"queen"}
    assert dict(artist_names(S("X", "Ana & Queen")))["ana and queen"] == {"ana", "queen"}


def test_at_most_three_other_artists_are_offered():
    src = S("Hallelujah", "Jeff Buckley")
    others = [S("Hallelujah", name, id=name) for name in ("Leonard Cohen", "Pentatonix", "Rufus Wainwright", "John Cale")]
    assert [a.id for a in alternatives(src, others)] == ["Leonard Cohen", "Pentatonix", "Rufus Wainwright"]


# Cases from a real transfer of 1684 songs: unrelated artists were being offered
@pytest.mark.parametrize("src, cand", [
    (S("Roncan RKT - REMIX", "Alan Gomez RMX"), S("INTRO MATADOR - SORE MIX", "BRIANMIX")),
    (S("Bad Bunny", "Pê"), S("Bad Bunny - Tití Me Preguntó", "Skyline")),
    (S("Trap Nation", "Bass Boosted HD"), S("Trap Nation - Best Of 2020", "Beats Gang")),
    (S("Shrek", "Maaguz"), S("Shrek", "Skyline Lyrics")),
])
def test_unrelated_songs_are_not_offered_as_alternatives(src, cand):
    assert alternatives(src, [cand]) == []


@pytest.mark.parametrize("src, cand", [
    (S("Sade - Smooth Operator (RICHIE ROZEX Remix)", "Stay See"), S("Smooth Operator", "RICHIE ROZEX")),   # chosen by the user
    (S("Sub Urban - Cradles (slowed Reverb)", "iji macu"), S("Cradles", "Sub Urban")),
    (S("Roncan RKT - REMIX", "Alan Gomez RMX"), S("Roncan RKT - REMIX", "BRIANMIX")),
])
def test_the_same_song_by_someone_else_is_still_offered(src, cand):
    assert alternatives(src, [cand]) == [cand]


def test_alternatives_do_not_repeat_and_the_closest_length_comes_first():
    src = S("Mar de Luces", "Palmar", dur=200)
    far, near = S("Mar de Luces", "Isabel Pantoja", dur=300, id="far"), S("Mar de Luces", "Romina", dur=205, id="near")
    twin = S("Mar de Luces", "Isabel Pantoja", dur=301, id="twin")
    assert [a.id for a in alternatives(src, [far, twin, near])] == ["near", "far"]


# Matches that were added as good in a real transfer of 1684 songs
@pytest.mark.parametrize("src, cand, expected", [
    # Karaoke, 8-bit or cover: the title names the original, but someone else sings it
    (S("Gentleman", "PSY"), S("Gentleman (made popular by Psy) [backing version]", "Party Tyme"), NOT_FOUND),
    (S("Mine All Day", "pewdiepie", "Party in Backyard"),
     S("Mine All Day (8-Bit PewDiePie Emulation)", "8-Bit Arcade"), NOT_FOUND),
    # "(feat. Sean Paul)" in the title doesn't say who uploaded it
    (S("Cheap Thrills (feat. Sean Paul)", "Sia", "Sean Paul"), S("Cheap Thrills (feat. Sean Paul)", "Vermillon League"),
     NOT_FOUND),
    # A real extra word, or what they don't share isn't alike: to review
    (S("Gettin' Over (feat. Chris Willis & Fergie)", "David Guetta", "Chris Willis", "Fergie"),
     S("Gettin' Over You (feat. Fergie & LMFAO)", "David Guetta", "Chris Willis"), DOUBTFUL),
    (S("God Mission - Español Version", "Teté Llosas & Melodyne MLD"),
     S("Alone (Español Version)", "Lucho Gonzalez", "Teté Llosas & Melodyne MLD"), DOUBTFUL),
    # Someone else's edit is another version; one by an artist of the song or the "Radio Edit" is not
    (S("Help Me Lose My Mind", "Disclosure", "London Grammar"),
     S("Disclosure - Help Me Lose My Mind (Coyote Paul Schranz Edit)", "Coyote Paul"), DOUBTFUL),
    (S("It's A Killa (Shermanology Edit)", "Fisher (OZ)"), S("It's A Killa", "FISHER", "Shermanology"), OK),
    (S("Intoxicated - Radio Edit", "Martin Solveig", "Good Times Ahead"),
     S("Intoxicated (Edit)", "Martin Solveig", "Good Times Ahead"), OK),
    # The remixer in brackets is an artist of the song ("GTA" is Good Times Ahead)
    (S("Red Lips (feat. Sam Bruno) - Skrillex Remix", "Good Times Ahead", "Sam Bruno", "Skrillex"),
     S("Red Lips [Skrillex Remix] (feat. Sam Bruno)", "GTA"), OK),
    # Typos and short leftovers are still the same song
    (S("The Way You Make Me Feel", "Michael Jackson"), S("The Way U Make Me Feel", "Michael Jackson"), OK),
])
def test_what_a_real_transfer_added_by_mistake(src, cand, expected):
    assert classify(score(src, cand)) == expected


# Duplicates from a real library (2026-11-24): different recordings were being ticked for removal
@pytest.mark.parametrize("first, again", [
    # Majestic's remix (3:07) stayed and Boney M.'s original (4:27 and 5:52) was removed
    (S("Rasputin", "Majestic", "Boney M.", dur=187, album="Rasputin"), S("Rasputin", "Boney M.", dur=267, album="The Magic Of Boney M.")),
    (S("Rasputin", "Majestic", "Boney M.", dur=187), S("Rasputin", "Boney M.", dur=352, album="Nightflight to Venus")),
    # THE FIRST TAKE is a single-take live recording, not the studio one
    (S("青のすみか - Where Our Blue Is", "Tatsuya Kitani", dur=197),
     S("青のすみか - From THE FIRST TAKE - Where Our Blue Is - From THE FIRST TAKE", "Tatsuya Kitani", dur=198)),
    # Titles one letter apart (found by tools/load_check.py, 2026-11-27): a typo or another song, the
    # letters can't tell. Found as a possible duplicate, but never ticked
    (S("Black Night", "Deep Purple", dur=208), S("Black Knight", "Deep Purple", dur=208)),
    (S("Sweet River Reprise", "Fleetwood Mac", dur=200), S("Secret River Reprise", "Fleetwood Mac", dur=200)),
    (S("Bohemian Rhapsody", "Queen", dur=354), S("Bohemian Rapsody", "Queen", dur=354)),
])
def test_a_different_recording_is_never_ticked_to_remove(first, again):
    [d] = find_duplicates([first, again])
    assert d.kind == OTHER_VERSION                  # the person decides: it comes unticked


def test_a_video_with_a_short_intro_is_still_the_same_recording():
    """Virtual Insanity (Official Video) is 17 s longer because of the intro: still the same one."""
    [d] = find_duplicates([S("Virtual Insanity", "Jamiroquai", dur=228), S("Virtual Insanity (Official Video)", "Jamiroquai", dur=245)])
    assert d.kind == SAME
