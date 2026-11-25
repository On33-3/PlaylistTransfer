"""A made-up but believable music library for the README screenshots: well-known songs, invented playlists,
the three services connected. Never anyone's real data.

The catalogs are built so a transfer shows every status the app has: found, "?" (only a live or acoustic
version exists), not found (only another artist sings it, or it isn't there at all), already in the
destination, and repeated in the source. YouTube Music has playlists with exact copies, another
version and clean ones, for Remove duplicates.
"""
from __future__ import annotations

from pathlib import Path

from tests.fakes import FakeService, S, build_api
from tests.ui_helpers import BRAVE, VIVALDI

# Playlist names in each language (the songs keep their real titles)
NAMES = {
    "en": {"road": "Road Trip", "sunday": "Sunday Morning", "workout": "Workout", "nineties": "90s Classics",
           "chill": "Chill Evenings"},
    "es": {"road": "Viaje en auto", "sunday": "Domingo tranquilo", "workout": "Para entrenar",
           "nineties": "Clásicos de los 90", "chill": "Tardes tranquilas"},
}

# (title, artist, length in seconds)
ROAD_TRIP = [
    ("Hotel California", "Eagles", 391), ("Go Your Own Way", "Fleetwood Mac", 223), ("Take It Easy", "Eagles", 211),
    ("Born to Run", "Bruce Springsteen", 270), ("Life Is a Highway", "Tom Cochrane", 266),
    ("Mr. Blue Sky", "Electric Light Orchestra", 303), ("Don't Stop Believin'", "Journey", 250),
    ("Sweet Home Alabama", "Lynyrd Skynyrd", 283), ("Take On Me", "a-ha", 225), ("Livin' on a Prayer", "Bon Jovi", 249),
    ("Bohemian Rhapsody", "Queen", 354), ("Here Comes the Sun", "The Beatles", 185), ("Dreams", "Fleetwood Mac", 257),
    ("Under Pressure", "Queen", 248), ("Wonderwall", "Oasis", 258), ("Mr. Brightside", "The Killers", 222),
    ("Born to Be Wild", "Steppenwolf", 210), ("Highway to Hell", "AC/DC", 208), ("Africa", "Toto", 295),
    ("Free Fallin'", "Tom Petty", 256), ("Hallelujah", "Jeff Buckley", 414),
    ("Take Me Home, Country Roads", "John Denver", 193), ("Layla", "Derek & The Dominos", 424),
    ("Go Your Own Way", "Fleetwood Mac", 223), ("September", "Earth, Wind & Fire", 215), ("Dancing Queen", "ABBA", 231),
    ("I Want to Break Free", "Queen", 259), ("Everybody Wants to Rule the World", "Tears for Fears", 251),
    ("Sweet Child O' Mine", "Guns N' Roses", 356), ("Running on Empty", "Jackson Browne", 296),
    ("Can't Stop", "Red Hot Chili Peppers", 269), ("The Chain", "Fleetwood Mac", 270), ("Fast Car", "Tracy Chapman", 296),
]
# On Spotify these only exist in another version (→ "?"), by another artist (→ not found, offered as an
# alternative), or not at all
OTHER_VERSION = {"Hotel California": ("Hotel California - Live on MTV, 1994", 427),
                 "Layla": ("Layla - Live", 465),
                 "Sweet Child O' Mine": ("Sweet Child O' Mine - Acoustic", 301)}
OTHER_ARTIST = {"Hallelujah": ("Leonard Cohen", 279)}
MISSING = {"Running on Empty"}
ALREADY = ["Dreams", "Wonderwall", "Dancing Queen", "Fast Car"]     # already in the Spotify playlist

SUNDAY = [("Here Comes the Sun", "The Beatles", 185), ("Banana Pancakes", "Jack Johnson", 191),
          ("Put Your Records On", "Corinne Bailey Rae", 215), ("Sunday Morning", "Maroon 5", 245),
          ("Lovely Day", "Bill Withers", 254), ("Yellow", "Coldplay", 269), ("Riptide", "Vance Joy", 204),
          ("Better Together", "Jack Johnson", 207), ("Budapest", "George Ezra", 200), ("Fast Car", "Tracy Chapman", 296)]
WORKOUT = [("Eye of the Tiger", "Survivor", 245), ("Lose Yourself", "Eminem", 326), ("Stronger", "Kanye West", 312),
           ("Till I Collapse", "Eminem", 298), ("Can't Hold Us", "Macklemore & Ryan Lewis", 258),
           ("Thunderstruck", "AC/DC", 292), ("Power", "Kanye West", 292), ("Remember the Name", "Fort Minor", 230)]
NINETIES = [("Smells Like Teen Spirit", "Nirvana", 301), ("Wonderwall", "Oasis", 258), ("Zombie", "The Cranberries", 306),
            ("Losing My Religion", "R.E.M.", 268), ("Creep", "Radiohead", 239), ("Bitter Sweet Symphony", "The Verve", 358),
            ("Black Hole Sun", "Soundgarden", 318), ("Torn", "Natalie Imbruglia", 245)]


def song(title, artist, dur, svc, n, album=None):
    return S(title, artist, dur=dur, id=f"{svc}{n}", album=album)


def build(tmp_path: Path, lang: str = "en"):
    """The API with the demo library. Returns (api, tidal, ytmusic, spotify)."""
    names = NAMES[lang]
    tidal = FakeService("tidal", "Tidal")
    yt = FakeService("ytmusic", "YouTube Music")
    spotify = FakeService("spotify", "Spotify")

    # Tidal: the source playlists
    tidal.add_playlist("road", names["road"], [song(t, a, d, "t", i) for i, (t, a, d) in enumerate(ROAD_TRIP)])
    tidal.add_playlist("sunday", names["sunday"], [song(t, a, d, "ts", i) for i, (t, a, d) in enumerate(SUNDAY)])
    tidal.add_playlist("workout", names["workout"], [song(t, a, d, "tw", i) for i, (t, a, d) in enumerate(WORKOUT)])
    tidal.add_playlist("nineties", names["nineties"], [song(t, a, d, "tn", i) for i, (t, a, d) in enumerate(NINETIES)])

    # Spotify: its catalog, with the gaps above, and a Road Trip playlist that already has a few songs
    seen, catalog = set(), []
    for i, (t, a, d) in enumerate(ROAD_TRIP + SUNDAY + WORKOUT + NINETIES):
        if (t, a) in seen or t in MISSING:
            continue
        seen.add((t, a))
        if t in OTHER_VERSION:
            title, dur = OTHER_VERSION[t]
            catalog.append(song(title, a, dur, "s", i))
        elif t in OTHER_ARTIST:
            artist, dur = OTHER_ARTIST[t]
            catalog.append(song(t, artist, dur, "s", i))
        else:
            catalog.append(song(t, a, d, "s", i))
    spotify.catalog = catalog
    by_title = {s.title: s for s in catalog}
    spotify.add_playlist("road", names["road"], [by_title[t] for t in ALREADY])
    spotify.add_playlist("workout", names["workout"], [])

    # YouTube Music: its catalog (for the Sunday transfer) and playlists to clean up
    yt.catalog = [song(t, a, d, "y", i, album=a) for i, (t, a, d) in enumerate(SUNDAY)]
    yt.add_playlist("road", names["road"], [
        S("Mr. Brightside", "The Killers", dur=222, id="yb1", album="Hot Fuss"),
        S("Take On Me", "a-ha", dur=225, id="yb2", album="Hunting High and Low"),
        S("Bohemian Rhapsody", "Queen", dur=354, id="yb3", album="A Night at the Opera"),
        S("Africa", "Toto", dur=295, id="yb4", album="Toto IV"),
        S("Mr. Brightside (Official Music Video)", "The Killers", dur=228, id="yb5"),
        S("Take On Me (Official Video)", "a-ha", dur=244, id="yb6"),
        S("Bohemian Rhapsody (Live Aid 1985)", "Queen", dur=356, id="yb7", album="Live Aid"),
        S("Dreams", "Fleetwood Mac", dur=257, id="yb8", album="Rumours")])
    yt.add_playlist("chill", names["chill"], [
        S("Banana Pancakes", "Jack Johnson", dur=191, id="yc1", album="In Between Dreams"),
        S("Riptide", "Vance Joy", dur=204, id="yc2", album="Dream Your Life Away"),
        S("Lovely Day", "Bill Withers", dur=254, id="yc3", album="Menagerie"),
        S("Banana Pancakes (Audio)", "Jack Johnson", dur=192, id="yc4")])
    yt.add_playlist("workout", names["workout"], [song(t, a, d, "yw", i) for i, (t, a, d) in enumerate(WORKOUT)])
    yt.add_playlist("nineties", names["nineties"], [song(t, a, d, "yn", i) for i, (t, a, d) in enumerate(NINETIES)])

    api = build_api(tmp_path, {"tidal": tidal, "ytmusic": yt, "spotify": spotify}, browsers=[VIVALDI, BRAVE])
    api.set_language(lang)
    return api, tidal, yt, spotify
