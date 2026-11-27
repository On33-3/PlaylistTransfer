"""Matching between services and duplicate detection.

Based on `legacy/transfer_playlist.py`, the original console script (tested with real accounts,
since removed). What changed on purpose, with a test scenario for each rule (`tests/test_scenarios.py`):

- Artist and title are requirements, not just weights: a song with the same name by another artist,
  a cover or a karaoke version is not "the same" even if the title and the length match.
- A song can have several artists, and each service writes them differently ("Queen & David
  Bowie" as one text, or two artists, or "feat." in the title): one matching artist is enough.
- Parts and sequels are different songs (Pt. 1 ≠ Pt. 2, "The Unforgiven" ≠ "The Unforgiven II",
  "(Reprise)"), and generic titles ("Intro", "Interlude") only match on the same album.
- Another version of the same song (live, remix, acoustic, sped up...) is not OK when
  transferring (it stays doubtful), and among duplicates it is marked as "another version".
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from difflib import SequenceMatcher
from functools import lru_cache

from app.core.models import OTHER_VERSION, SAME, Duplicate, Song

SCORE_OK = 0.75      # from here on it is added without doubt
SCORE_MIN = 0.60     # between MIN and OK it is added, but marked as doubtful
ARTIST_MIN = 0.60    # minimum artist similarity: below it, it's another artist
TITLE_MIN = 0.75     # minimum title similarity: below it, it's another song
SAME_TITLE = 0.92    # title similarity from which two tracks can be the same song (duplicates)
NO_MATCH_CAP = 0.50  # cap when it's another song (it ends up as not found)
VERSION_CAP = 0.70   # cap when it's another version of the same song (it ends up as doubtful)

OK, DOUBTFUL, NOT_FOUND = "ok", "dudoso", "no_encontrado"


# ---------------------------------------------------------------- normalization

_NOISE_PAREN = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|with|remaster(ed)?|official|video|audio|lyrics?|visualizer|hd|4k)\b[^\)\]]*[\)\]]",
    re.I,
)
_NOISE_DASH = re.compile(r"\s+-\s+[^-]*\b(remaster(ed)?|official|video|audio|lyrics?)\b.*$", re.I)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?)\s+.*$", re.I)


# The text functions are pure and get called a huge number of times with the same titles and artists
# (each candidate is compared with each song): the result for every text already seen is cached.
CACHE = 1 << 16


@lru_cache(maxsize=CACHE)
def clean(text: str) -> str:
    """Removes typical title noise: (Official Video), - Remastered 2011, feat. X."""
    text = _NOISE_PAREN.sub(" ", text or "")
    text = _NOISE_DASH.sub(" ", text)
    text = _FEAT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=CACHE)
def plain(text: str) -> str:
    """Lowercase, no accents or punctuation, & → and. Nothing else removed."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=CACHE)
def norm(text: str) -> str:
    """Like `plain`, but first removes title noise (keeps non-Latin alphabets)."""
    return plain(clean(text))


def _contains_words(short: str, long: str) -> bool:
    return bool(short) and f" {short} " in f" {long} "


@lru_cache(maxsize=CACHE)
def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


# ---------------------------------------------------------------- artists

_ARTIST_SEP = re.compile(r"\s*(?:,|&|\+|/|;|\bx\b|\band\b|\by\b|\bfeat\.?|\bft\.?|\bfeaturing\b|"
                         r"\bwith\b|\bcon\b|\bvs\.?)\s*", re.I)
_CHANNEL_SUFFIX = re.compile(r"(?:\s*-?\s*\b(?:topic|official|oficial)\b|vevo)$")


@lru_cache(maxsize=CACHE)
def _artist_key(name: str) -> str:
    """A comparable artist name: without the "VEVO", " - Topic", "Official" of YouTube channels."""
    key = plain(name)
    for _ in range(2):
        key = _CHANNEL_SUFFIX.sub("", key).strip()
    return key


def artist_names(song: Song) -> list[tuple[str, frozenset[str]]]:
    """[(full name, parts)] per artist. "Queen & David Bowie" → parts {queen, david bowie}."""
    return list(_artist_names(tuple(song.artists)))


@lru_cache(maxsize=CACHE)
def _artist_names(artists: tuple[str, ...]) -> tuple[tuple[str, frozenset[str]], ...]:
    out = []
    for name in artists:
        whole = _artist_key(name)
        if not whole:
            continue
        parts = {_artist_key(p) for p in _ARTIST_SEP.split(name or "")}
        out.append((whole, frozenset(p for p in parts if len(p) >= 3) - {whole}))
    return tuple(out)


def _all_artist_tokens(*songs: Song) -> set[str]:
    return {t for s in songs for whole, parts in artist_names(s) for n in {whole, *parts} for t in n.split()}


def _artist_in_title(artist_song: Song, title_song: Song) -> bool:
    """"Artist - Song" style videos: one's artist appears as words in the other's title.
    What comes with "feat." doesn't count: in "Cheap Thrills (feat. Sean Paul)" uploaded by someone else,
    Sean Paul doesn't say who sings it. A remixer in brackets does ("Red Lips [Skrillex Remix]")."""
    title = _title_without_feat(title_song.title or "")
    return any(_contains_words(n, title) for whole, parts in artist_names(artist_song)
               for n in {whole, *parts} if len(n) >= 3)


@lru_cache(maxsize=CACHE)
def _title_without_feat(title: str) -> str:
    return plain(_FEAT.sub(" ", _FEAT_PAREN.sub(" ", title)))


_FEAT_PAREN = re.compile(r"[\(\[][^\)\]]*\b(?:feat|ft|featuring|with)\b[^\)\]]*[\)\]]", re.I)


def artist_sim(src: Song, cand: Song) -> float:
    """0 to 1: the best similarity between any artist of one and any of the other (a song can
    have several, and each service lists them differently)."""
    best = 0.0
    for wa, pa in artist_names(src):
        for wb, pb in artist_names(cand):
            if wa == wb:
                return 1.0
            s = _ratio(wa, wb)
            if _contains_words(wa, wb) or _contains_words(wb, wa) or wa in pb or wb in pa or pa & pb:
                s = max(s, 0.9)
            best = max(best, s)
    if _artist_in_title(src, cand):
        best = max(best, 0.9)
    return best


def _same_artist(a: Song, b: Song) -> bool:
    if artist_names(a) and artist_names(b) and artist_sim(a, b) >= 0.85:
        return True
    if _artist_in_title(a, b) or _artist_in_title(b, a):
        return True
    if not artist_names(a) or not artist_names(b):      # no artist: the length has to be almost the same
        return bool(a.duration and b.duration and abs(a.duration - b.duration) <= 5)
    return False


# ---------------------------------------------------------------- titles

_ANY_BRACKETS = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_PROD = re.compile(r"\s+(prod\.?|produced by)\s+.*$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")

# Words that can be extra in a video title without it being another song
_TITLE_NOISE = {"official", "oficial", "video", "videoclip", "clip", "audio", "lyric", "lyrics", "letra",
                "mv", "m", "v", "hd", "hq", "4k", "visualizer", "music", "musica", "topic", "vevo", "ft",
                "feat", "prod", "explicit", "clean", "radio", "edit", "full", "version", "remastered",
                "remaster"}


def title_keys(song: Song) -> set[str]:
    """Base title, without what changes between versions: (Official Video), [Remastered], (acoustic),
    feat. X, prod. X, '- Remastered 2011'. On YouTube it also tries without a leading 'Artist - '."""
    return set(_title_keys(song.title or ""))


@lru_cache(maxsize=CACHE)
def _title_keys(t: str) -> frozenset[str]:
    return _keys_of(t, *_DASH.split(t, maxsplit=1))


def _keys_of(*variants: str) -> frozenset[str]:
    keys = set()
    for v in variants:
        # Without what is in parentheses (almost always extra: "(Remastered)", "(Live)") and also with what
        # is inside, without the brackets: sometimes it is part of the title and another service writes it
        # without them, "(I Can't Get No) Satisfaction" = "I Can't Get No Satisfaction". Known noise ("Official
        # Video") is already removed by norm(); whether it's another version (live, remix) is up to version_tags()
        for w in (_ANY_BRACKETS.sub(" ", v), v):
            w = _PROD.sub(" ", _FEAT.sub(" ", w))
            # Parts ("Pt. 2", "II") are compared separately, in pieces(): they don't count here
            k = re.sub(r"\s+", " ", _TRAILING_ROMAN.sub("", _PART.sub(" ", norm(w)))).strip()
            if k:
                keys.add(k)
    return frozenset(keys)


def title_sim(src: Song, cand: Song) -> float:
    """0 to 1. One title containing the other only counts if what is left over is noise or the artist
    ("BTS 'Dynamite' Official MV" yes; "One Tree Hill" is not "One")."""
    return _title_match(src, cand)[0]


def _title_match(src: Song, cand: Song) -> tuple[float, float]:
    """(similarity, similar but not enough to be sure). The second is when the letters are alike but
    there is a real extra word ("Gettin' Over" / "Gettin' Over You") or what they don't share isn't alike
    ("God Mission - Español Version" / "Alone (Español Version)"): it may be another song, so it goes to
    review instead of being added on its own."""
    ka, kb = title_keys(src), title_keys(cand)
    if ka & kb:
        return 1.0, 0.0
    allowed = _TITLE_NOISE | _all_artist_tokens(src, cand)
    best = unsure = 0.0
    for x in ka:
        for y in kb:
            r = _ratio(x, y)
            short, long = sorted((x, y), key=len)
            if _contains_words(short, long):
                rest = f" {long} ".replace(f" {short} ", " ", 1).split()
                if all(w in allowed or _is_version_word(w) for w in rest):
                    best = max(best, 0.95)
                else:
                    unsure = max(unsure, r)
            elif _rest_alike(x, y):
                best = max(best, r)
            else:
                unsure = max(unsure, r)
    return best, unsure


@lru_cache(maxsize=CACHE)
def _rest_alike(x: str, y: str) -> bool:
    """What two titles don't share is alike (a typo: "frend" / "friend"). If not, what they share is
    not enough: "God Mission - Español Version" ≠ "Alone (Español Version)". Short leftovers ("you" / "u")
    are not checked."""
    wx, wy = x.split(), y.split()
    rx, ry = " ".join(w for w in wx if w not in wy), " ".join(w for w in wy if w not in wx)
    return len(rx) < 4 or len(ry) < 4 or rx == x or _ratio(rx, ry) >= 0.5


@lru_cache(maxsize=CACHE)
def _is_version_word(word: str) -> bool:
    """"remix", "live", "acoustic"...: whether it's another version is up to version_tags(), not the title."""
    return any(rx.search(word) for rx in _VERSION_RX.values())


# ---------------------------------------------------------------- versions, covers, parts

_VERSION_TAGS = {
    "en vivo": r"live|en vivo|ao vivo|en directo|unplugged|the first take",
    "acústica": r"acoustic|acustic[oa]|stripped",
    "remix": r"remix|rmx|rework|bootleg|club mix|extended mix|dub mix|vip mix",
    "acelerada": r"sped up|speed up|nightcore",
    "lenta": r"slowed|reverb",
    "instrumental": r"instrumental",
    "demo": r"demo",
    "orquestal": r"orchestral|symphonic|piano version",
    "regrabada": r"\w+ s version",          # (Taylor's Version)
    "8d": r"8d",
}
_VERSION_RX = {tag: re.compile(rf"\b(?:{p})\b") for tag, p in _VERSION_TAGS.items()}
_COVER = re.compile(r"\b(?:cover|covers|karaoke|tribute|in the style of|originally performed|made famous|"
                    r"made popular|backing (?:version|track)|emulation)\b")
_COVER_ARTIST = re.compile(r"\b(?:karaoke|tribute|cover band|covers)\b")
_PART = re.compile(r"\b(?:part|pt|parte|vol|volume|chapter|capitulo)\s*(\d+|[ivx]+)\b")
_TRAILING_ROMAN = re.compile(r"\b(ii|iii|iv|vi|vii|viii|ix)$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_GENERIC = {"intro", "outro", "interlude", "interludio", "skit", "prelude", "preludio", "intermission",
            "overture", "untitled", "bonus track", "hidden track", "introduccion"}


def _title_without_artists(song: Song, *others: Song) -> str:
    """The title without the artists' names (the band "Live" doesn't make it a live version)."""
    text = f" {plain(song.title)} "
    for whole, parts in artist_names(song) + [n for o in others for n in artist_names(o)]:
        for n in sorted({whole, *parts}, key=len, reverse=True):
            text = text.replace(f" {n} ", " ")
    return text.strip()


def version_tags(song: Song, *others: Song) -> set[str]:
    """{'en vivo', 'remix', ...}: what makes it another recording of the same song."""
    text = _title_without_artists(song, *others)
    tags = {tag for tag, rx in _VERSION_RX.items() if rx.search(text)}
    if _someone_elses_edit(song, *others):
        tags.add("remix")
    return tags


# "(Coyote Paul Schranz Edit)", "- Clash U Edit": someone else's version. "(Radio Edit)", "(Edit)" are not,
# and neither is one by an artist of the song: "It's A Killa (Shermanology Edit)" by FISHER and Shermanology
_EDIT = re.compile(r"(?:[\(\[]|\s[-–—]\s)([^\(\)\[\]]*?)\s*\bedit\s*(?:[\)\]]|$)", re.I)
_PLAIN_EDITS = {"radio", "single", "album", "clean", "explicit", "dirty", "short", "original", "video",
                "censored", "main", "lp", "ep", "mono", "stereo", "the", "a", "an", "new", "version"}


def _someone_elses_edit(song: Song, *others: Song) -> bool:
    names = {n for s in (song, *others) for whole, parts in artist_names(s) for n in {whole, *parts}}
    for m in _EDIT.finditer(song.title or ""):
        name = f" {plain(m.group(1))} "
        for n in sorted(names, key=len, reverse=True):
            name = name.replace(f" {n} ", " ")
        if any(w not in _PLAIN_EDITS for w in name.split()):
            return True
    return False


def is_cover(song: Song, *others: Song) -> bool:
    """Cover, karaoke or tribute: someone else sings it even if the title names the original artist."""
    if _COVER.search(_title_without_artists(song, *others)):
        return True
    return any(_COVER_ARTIST.search(plain(a)) for a in song.artists)


def pieces(song: Song) -> frozenset[str]:
    """Parts and sequels: {'parte 2'}, {'reprise'}. If they don't match, they are different songs."""
    text = plain(song.title)
    out = {f"parte {_ROMAN.get(n, n)}" for n in _PART.findall(text)}
    base = plain(_ANY_BRACKETS.sub(" ", _DASH.split(song.title or "")[-1]))
    m = _TRAILING_ROMAN.search(base)
    if m and not _PART.search(base):
        out.add(f"parte {_ROMAN[m.group(1)]}")
    if re.search(r"\breprise\b", text):
        out.add("reprise")
    return frozenset(out)


_DIGITS = re.compile(r"\d+")
_MOVEMENT = re.compile(r"[:\-–—]\s*([IVX]{1,4})\.\s")     # "Spring: I. Allegro", "- II. Largo"


def numbers(song: Song) -> frozenset[int]:
    """The numbers in the title that identify the work ("No. 1", "Op. 9", "Track 2"), without years
    (1900-2099) or what is in parentheses or after "- Remastered" (that is another version)."""
    base = plain(clean(_ANY_BRACKETS.sub(" ", song.title or "")))
    return frozenset(n for n in map(int, _DIGITS.findall(base)) if not 1900 <= n <= 2099)


def movements(song: Song) -> frozenset[str]:
    """Movements of a classical work: "I.", "II."... after ":" or "-"."""
    return frozenset(_MOVEMENT.findall(song.title or ""))


def is_generic(song: Song) -> bool:
    return bool(title_keys(song) & _GENERIC)


def _same_context(a: Song, b: Song) -> bool:
    """For generic titles: the same album (if both have one) or, if not, almost the same length."""
    if a.album and b.album:
        ka, kb = norm(_ANY_BRACKETS.sub(" ", a.album)), norm(_ANY_BRACKETS.sub(" ", b.album))
        return ka == kb or _ratio(ka, kb) >= 0.9
    return bool(a.duration and b.duration and abs(a.duration - b.duration) <= 3)


def _different_song(a: Song, b: Song) -> bool:
    """Rules that tell songs apart even when title and artist are alike."""
    if is_cover(a, b) != is_cover(b, a):
        return True
    if pieces(a) != pieces(b):
        return True
    # Classical music and numbered pieces: "Gymnopédie No. 1" ≠ "No. 3", "I. Allegro" ≠ "II. Largo".
    # Only if both have numbers (or movements): "Live at Wembley '86" is still the same song.
    na, nb = numbers(a), numbers(b)
    if na and nb and na != nb:
        return True
    ma, mb = movements(a), movements(b)
    if ma and mb and ma != mb:
        return True
    if (is_generic(a) or is_generic(b)) and not _same_context(a, b):
        return True
    return False


# ---------------------------------------------------------------- matching between services

def score(src: Song, cand: Song) -> float:
    """0 to 1. Weighs title, artist and length. The same ISRC = 1.

    Caps: if it's another song (another artist, another title, a cover, another part, an intro from
    another album), NO_MATCH_CAP → not found. If it's another version (live, remix...), VERSION_CAP → doubtful."""
    if src.isrc and cand.isrc and src.isrc.upper() == cand.isrc.upper():
        return 1.0

    t, unsure = _title_match(src, cand)
    a = artist_sim(src, cand)

    if src.duration and cand.duration:
        diff = abs(src.duration - cand.duration)
        d = 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / 27)
    else:
        d = 0.5

    total = round(0.5 * t + 0.35 * a + 0.15 * d, 3)
    other_artist = bool(artist_names(src) and artist_names(cand)) and a < ARTIST_MIN
    if t < TITLE_MIN <= unsure and not other_artist and not _different_song(src, cand):
        return min(round(0.5 * unsure + 0.35 * a + 0.15 * d, 3), VERSION_CAP)   # to review
    if t < TITLE_MIN or other_artist or _different_song(src, cand):
        return min(total, NO_MATCH_CAP)
    if version_tags(src, cand) != version_tags(cand, src):
        return min(total, VERSION_CAP)
    return total


def best_match(src: Song, candidates: list[Song]) -> tuple[Song | None, float]:
    best, best_score = None, 0.0
    for c in candidates:
        s = score(src, c)
        if s > best_score:
            best, best_score = c, s
    return best, best_score


def alternatives(src: Song, candidates: list[Song], limit: int = 3) -> list[Song]:
    """The same song sung by other artists (not home-made covers or karaoke), so the UI can offer it
    when the searched artist's version isn't there. They are never added on their own.

    Stricter than when comparing: it is offered by someone who isn't the searched artist, so the
    candidate's whole title has to be the searched one. With its pieces, "Bad Bunny - Otro Tema" passed
    for "Bad Bunny", and "Intro - Sore Mix" for "Tema - Remix" (seen in a real transfer)."""
    def closeness(c: Song) -> float:
        return abs(src.duration - c.duration) if src.duration and c.duration else float("inf")

    out: dict[tuple, Song] = {}
    for c in sorted(candidates, key=closeness):          # closest length first
        if (_is_the_named_title(src, c) and artist_sim(src, c) < ARTIST_MIN and not is_cover(c, src)
                and not any(_LYRICS_CHANNEL.search(plain(a)) for a in c.artists)
                and pieces(c) == pieces(src) and version_tags(c, src) <= version_tags(src, c)):
            out.setdefault((norm(c.title), tuple(sorted(plain(a) for a in c.artists))), c)
    return list(out.values())[:limit]


_LYRICS_CHANNEL = re.compile(r"\b(?:lyrics?|letra|letras)\b")


def _is_the_named_title(src: Song, cand: Song) -> bool:
    """Is the candidate's whole title the searched one? (For the searched one, parts do count: "Sade - Smooth Operator".)"""
    whole = _keys_of(cand.title or "")
    return any(x == y or _ratio(x, y) >= SAME_TITLE for x in title_keys(src) for y in whole)


def classify(sc: float, ok: float = SCORE_OK, minimum: float = SCORE_MIN) -> str:
    """'ok', 'dudoso' or 'no_encontrado' from the score."""
    if sc >= ok:
        return OK
    if sc >= minimum:
        return DOUBTFUL
    return NOT_FOUND


def queries_for(src: Song) -> list[str]:
    title = clean(src.title)
    qs = [f"{src.artist} {title}".strip(), title]
    return list(dict.fromkeys(q for q in qs if q))   # no repeats, in order


# ---------------------------------------------------------------- same song, another version

def same_song(a: Song, b: Song) -> bool:
    """Is it the same song, even if it's another version (another video, cover art, remaster, live)?
    It isn't if another artist sings it, if it's a cover or karaoke, another part, or an "Intro" from another album."""
    if a.id and b.id and a.id == b.id:
        return True
    if a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper():
        return True
    if _different_song(a, b) or not _same_artist(a, b):
        return False
    ka, kb = title_keys(a), title_keys(b)
    return bool(ka & kb) or any(_ratio(x, y) >= SAME_TITLE for x in ka for y in kb)


class SongIndex:
    """Answers "is this song already here?" without comparing against the whole list (with 5000 songs that took 11 minutes).

    It is exact: the same result as going through the list with `same_song`. To be the same song, two tracks
    have to share an id, an ISRC or an almost equal title (`SAME_TITLE`), so only pairs that cannot get there
    are skipped: `SAME_TITLE` limits how many characters can be left unpaired between the two titles, and that
    limits how much their length and the letters they use can differ. `same_song` is applied to the rest,
    in list order: the first one wins, as always."""

    def __init__(self, songs: Iterable[Song] = ()):
        self._songs: list[Song] = []
        self._ids: dict[str, list[int]] = {}
        self._isrcs: dict[str, list[int]] = {}
        self._keys: dict[str, list[int]] = {}
        self._by_length: dict[int, dict[str, int]] = {}      # length → {base title: letters it uses}
        for s in songs:
            self.add(s)

    def add(self, song: Song) -> None:
        at = len(self._songs)
        self._songs.append(song)
        if song.id:
            self._ids.setdefault(song.id, []).append(at)
        if song.isrc:
            self._isrcs.setdefault(song.isrc.upper(), []).append(at)
        for key in _title_keys(song.title or ""):
            if key not in self._keys:
                self._by_length.setdefault(len(key), {})[key] = _letters(key)
            self._keys.setdefault(key, []).append(at)

    def _similar_keys(self, key: str) -> Iterator[str]:
        """The stored base titles that can reach `SAME_TITLE` with `key`, and do."""
        letters, size = _letters(key), len(key)
        for other_size, bucket in self._by_length.items():
            total = size + other_size
            # The extra length and the letters only one of them has stay unpaired: ratio = 1 - unpaired / total
            unmatched = int((1 - SAME_TITLE) * total + 1e-9)
            if abs(size - other_size) > unmatched:
                continue
            for other, other_letters in bucket.items():
                if (letters ^ other_letters).bit_count() <= unmatched and (other == key or _ratio(key, other) >= SAME_TITLE):
                    yield other

    def first_match(self, song: Song) -> Song | None:
        """The first song in the list that is the same as `song`, or None."""
        spots = set(self._ids.get(song.id or "", ())) | set(self._isrcs.get((song.isrc or "").upper(), ()))
        for key in _title_keys(song.title or ""):
            for other in self._similar_keys(key):
                spots.update(self._keys[other])
        return next((self._songs[i] for i in sorted(spots) if same_song(song, self._songs[i])), None)


def _letters(key: str) -> int:
    """Which letters a text uses, as bits (two letters can share a bit: it only brings closer, never apart)."""
    bits = 0
    for c in set(key):
        bits |= 1 << (ord(c) % 61)
    return bits


OTHER_LENGTH = 30   # seconds: two copies that differ by more than this are different recordings


def _other_recording(a: Song, b: Song) -> bool:
    """Very different lengths: another recording even if the title doesn't say so (Majestic's remix of
    "Rasputin" is 3:07 and Boney M.'s original 4:27). A video with an intro differs by a few seconds
    (Virtual Insanity: 17 s). The same id or ISRC is always the same recording."""
    if (a.id and a.id == b.id) or (a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper()):
        return False
    return bool(a.duration and b.duration and abs(a.duration - b.duration) > OTHER_LENGTH)


def _only_alike_titles(a: Song, b: Song) -> bool:
    """The titles are only alike, not the same ("Black Night" / "Black Knight"): a typo or another song,
    the letters can't tell. Found by tools/load_check.py on 2026-11-27."""
    if (a.id and a.id == b.id) or (a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper()):
        return False
    return not title_keys(a) & title_keys(b)


def find_duplicates(songs: list[Song]) -> list[Duplicate]:
    """Repeated copies in a playlist. The first one always stays. Each copy says whether it is
    the same recording (SAME) or another version (OTHER_VERSION: live, remix...), so the UI
    leaves the latter unticked."""
    kept = SongIndex()
    dups: list[Duplicate] = []
    for i, s in enumerate(songs):
        twin = kept.first_match(s)
        if twin:
            kind = SAME if (version_tags(s, twin) == version_tags(twin, s) and not _other_recording(s, twin)
                            and not _only_alike_titles(s, twin)) else OTHER_VERSION
            dups.append(Duplicate(i, s, twin, kind))
        else:
            kept.add(s)
    return dups
