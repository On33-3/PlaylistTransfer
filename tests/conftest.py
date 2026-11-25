"""The user's real data (from the reports and the "Remove duplicates" video) and utilities."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.models import Song

ROOT = Path(__file__).resolve().parent.parent


def mmss(text: str) -> int:
    m, s = text.split(":")
    return int(m) * 60 + int(s)


def yt(title: str, artist: str, dur: str, album: str | None, vid: str, set_id: str) -> Song:
    return Song(title=title, artists=[a.strip() for a in artist.split(",")] if artist else [],
                album=album, duration=mmss(dur), id=vid, set_id=set_id)


# The video's YouTube Music playlist: first the version that stays, then the copies.
# (title, artist, length, album) — copies taken from prototype/index.html
KEPT = [
    ("Virtual Insanity", "Jamiroquai", "3:48", "Pop Music: The Modern Era"),
    ("Stuck In The Middle With You", "Stealers Wheel", "3:29", "Stealers Wheel"),
    ("Come and Get Your Love", "Redbone", "3:33", "Rock Hits der 70er"),
    ("Mr. Blue Sky", "Electric Light Orchestra", "5:04", "Out of the Blue"),
    ("Take On Me", "a-ha", "3:46", "Hunting High and Low"),
    ("(I Can't Get No) Satisfaction (Mono)", "The Rolling Stones", "3:43", "Singles 1965-1967"),
]
COPIES = [   # (title, artist, length, album, title of the one that stays)
    ("Virtual Insanity", "Jamiroquai", "4:24", "Travelling Without Moving", "Virtual Insanity"),
    ("Stuck In The Middle With You", "Stealers Wheel", "3:24", "Reservoir Dogs (soundtrack)",
     "Stuck In The Middle With You"),
    ("Come and Get Your Love", "Redbone", "5:01", "Wovoka", "Come and Get Your Love"),
    ("Virtual Insanity", "Jamiroquai", "5:41", "Travelling Without Moving", "Virtual Insanity"),
    ("Mr. Blue Sky", "Electric Light Orchestra", "5:03", "All Over The World", "Mr. Blue Sky"),
    ("Take On Me", "a-ha", "3:49", "Scandi Pop Hits 2", "Take On Me"),
    ("(I Can't Get No) Satisfaction (Mono)", "The Rolling Stones", "3:44", "The Rolling Stones In Mono",
     "(I Can't Get No) Satisfaction (Mono)"),
    ("Virtual Insanity (Official Video)", "Jamiroquai", "4:05", None, "Virtual Insanity"),
]

# Songs from the same playlists that are NOT duplicates, including the look-alike pairs.
OTHERS = [
    ("Spirit In The Sky", "Norman Greenbaum", "4:02", "Spirit In The Sky"),
    ("Marcy (Deluxe Edition)", "Norman Greenbaum", "3:17", "Spirit In The Sky (Deluxe Edition)"),
    ("From The Start", "Laufey", "2:50", "Bewitched"),
    ("From the Start", "Good Kid", "2:30", "Good Kid 2"),
    ("Sunshine", "Khai Dreams, Atwood", "2:47", "Sunshine"),
    ("Sunkissed", "Khai Dreams", "2:06", "Sunkissed"),
    ("September", "Earth, Wind & Fire", "3:35", "The Best of Earth, Wind & Fire"),
    ("Dancing Queen", "ABBA", "3:51", "Arrival"),
]

# Negative pairs: they look alike, but they are different songs
NEGATIVE_PAIRS = [
    (Song("Spirit In The Sky", ["Norman Greenbaum"], duration=242),
     Song("Marcy (Deluxe Edition)", ["Norman Greenbaum"], duration=197)),
    (Song("From The Start", ["Laufey"], duration=170),
     Song("From the Start", ["Good Kid"], duration=150)),
    (Song("Sunshine", ["Khai Dreams", "Atwood"], duration=167),
     Song("Sunkissed", ["Khai Dreams"], duration=126)),
]


@pytest.fixture
def video_playlist() -> list[Song]:
    """Like the real playlist: the originals mixed with other songs and the copies at the end."""
    songs: list[Song] = []
    n = 0

    def add(title, artist, dur, album):
        nonlocal n
        n += 1
        songs.append(yt(title, artist, dur, album, f"vid{n:02d}", f"set{n:02d}"))

    others = iter(OTHERS)
    for k in KEPT:
        add(*k)
        add(*next(others))
    for o in others:
        add(*o)
    for title, artist, dur, album, _ in COPIES:
        add(title, artist, dur, album)
    return songs


@pytest.fixture(autouse=True)
def spanish_by_default():
    """The tests check the Spanish texts. The language is global: it's restored after each test."""
    from app import i18n
    i18n.set_language("es")
    yield
    i18n.set_language("es")


@pytest.fixture(scope="module")
def browser():
    """Headless Edge (the same engine as WebView2), for the UI tests. If it isn't there, they're skipped."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as e:
            pytest.skip(f"No pude abrir Edge: {e}")
        yield b
        b.close()
