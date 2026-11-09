"""Coincidencias entre servicios y detección de repetidos.

Base extraída de `legacy/transfer_playlist.py` (probada con cuentas reales). Lo que cambió a
propósito, con un escenario de prueba por cada regla (`tests/test_scenarios.py`):

- El artista y el título son requisitos, no solo pesos: un tema homónimo de otro artista, un
  cover o un karaoke no es "el mismo" aunque el título y la duración coincidan.
- Una canción puede tener varios artistas, y cada servicio los escribe distinto ("Queen & David
  Bowie" en un solo texto, o dos artistas, o "feat." en el título): alcanza con que coincida uno.
- Partes y secuelas son temas distintos (Pt. 1 ≠ Pt. 2, "The Unforgiven" ≠ "The Unforgiven II",
  "(Reprise)"), y los títulos genéricos ("Intro", "Interlude") solo coinciden en el mismo álbum.
- Otra versión de la misma canción (en vivo, remix, acústica, sped up...) no se da por OK al
  transferir (queda dudosa), y en los repetidos se marca como "otra versión".
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from difflib import SequenceMatcher
from functools import lru_cache

from app.core.models import OTHER_VERSION, SAME, Duplicate, Song

SCORE_OK = 0.75      # desde acá se agrega sin dudas
SCORE_MIN = 0.60     # entre MIN y OK se agrega, pero queda marcado como dudoso
ARTIST_MIN = 0.60    # parecido mínimo de artista: por debajo, es otro artista
TITLE_MIN = 0.75     # parecido mínimo de título: por debajo, es otro tema
SAME_TITLE = 0.92    # parecido de título desde el que dos temas pueden ser la misma canción (repetidos)
NO_MATCH_CAP = 0.50  # tope cuando es otra canción (queda como no encontrado)
VERSION_CAP = 0.70   # tope cuando es otra versión de la misma canción (queda como dudoso)

OK, DOUBTFUL, NOT_FOUND = "ok", "dudoso", "no_encontrado"


# ---------------------------------------------------------------- normalización

_NOISE_PAREN = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|with|remaster(ed)?|official|video|audio|lyrics?|visualizer|hd|4k)\b[^\)\]]*[\)\]]",
    re.I,
)
_NOISE_DASH = re.compile(r"\s+-\s+[^-]*\b(remaster(ed)?|official|video|audio|lyrics?)\b.*$", re.I)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?)\s+.*$", re.I)


# Las funciones de texto son puras y se llaman muchísimas veces con los mismos títulos y artistas
# (cada candidato se compara con cada tema): se guarda el resultado de cada texto ya visto.
CACHE = 1 << 16


@lru_cache(maxsize=CACHE)
def clean(text: str) -> str:
    """Saca ruido típico de títulos: (Official Video), - Remastered 2011, feat. X."""
    text = _NOISE_PAREN.sub(" ", text or "")
    text = _NOISE_DASH.sub(" ", text)
    text = _FEAT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=CACHE)
def plain(text: str) -> str:
    """Minúsculas, sin acentos ni puntuación, & → and. Sin sacar nada más."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=CACHE)
def norm(text: str) -> str:
    """Como `plain`, pero antes saca el ruido de los títulos (respeta alfabetos no latinos)."""
    return plain(clean(text))


def _contains_words(short: str, long: str) -> bool:
    return bool(short) and f" {short} " in f" {long} "


@lru_cache(maxsize=CACHE)
def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


# ---------------------------------------------------------------- artistas

_ARTIST_SEP = re.compile(r"\s*(?:,|&|\+|/|;|\bx\b|\band\b|\by\b|\bfeat\.?|\bft\.?|\bfeaturing\b|"
                         r"\bwith\b|\bcon\b|\bvs\.?)\s*", re.I)
_CHANNEL_SUFFIX = re.compile(r"(?:\s*-?\s*\b(?:topic|official|oficial)\b|vevo)$")


@lru_cache(maxsize=CACHE)
def _artist_key(name: str) -> str:
    """Nombre de artista comparable: sin "VEVO", " - Topic", "Official" de los canales de YouTube."""
    key = plain(name)
    for _ in range(2):
        key = _CHANNEL_SUFFIX.sub("", key).strip()
    return key


def artist_names(song: Song) -> list[tuple[str, frozenset[str]]]:
    """[(nombre completo, partes)] por artista. "Queen & David Bowie" → partes {queen, david bowie}."""
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
    """Videos tipo "Artista - Tema": el artista de uno aparece como palabras en el título del otro."""
    title = plain(title_song.title)
    return any(_contains_words(n, title) for whole, parts in artist_names(artist_song)
               for n in {whole, *parts} if len(n) >= 3)


def artist_sim(src: Song, cand: Song) -> float:
    """0 a 1: el mejor parecido entre algún artista de uno y alguno del otro (una canción puede
    tener varios, y cada servicio los lista distinto)."""
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
    if not artist_names(a) or not artist_names(b):      # sin artista: que la duración sea casi igual
        return bool(a.duration and b.duration and abs(a.duration - b.duration) <= 5)
    return False


# ---------------------------------------------------------------- títulos

_ANY_BRACKETS = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_PROD = re.compile(r"\s+(prod\.?|produced by)\s+.*$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")

# Palabras que pueden sobrar en el título de un video sin que sea otro tema
_TITLE_NOISE = {"official", "oficial", "video", "videoclip", "clip", "audio", "lyric", "lyrics", "letra",
                "mv", "m", "v", "hd", "hq", "4k", "visualizer", "music", "musica", "topic", "vevo", "ft",
                "feat", "prod", "explicit", "clean", "radio", "edit", "full", "version", "remastered",
                "remaster"}


def title_keys(song: Song) -> set[str]:
    """Título base, sin lo que cambia entre versiones: (Official Video), [Remastered], (acoustic),
    feat. X, prod. X, '- Remastered 2011'. En YouTube también prueba sin el 'Artista - ' del principio."""
    return set(_title_keys(song.title or ""))


@lru_cache(maxsize=CACHE)
def _title_keys(t: str) -> frozenset[str]:
    variants = [t] + _DASH.split(t, maxsplit=1)
    keys = set()
    for v in variants:
        # Sin lo que va entre paréntesis (casi siempre sobra: "(Remastered)", "(Live)") y también con lo de
        # adentro, sin los signos: a veces es parte del título y otro servicio lo escribe sin paréntesis,
        # "(I Can't Get No) Satisfaction" = "I Can't Get No Satisfaction". El ruido conocido ("Official
        # Video") ya lo saca norm(); que sea otra versión (en vivo, remix) lo deciden version_tags()
        for w in (_ANY_BRACKETS.sub(" ", v), v):
            w = _PROD.sub(" ", _FEAT.sub(" ", w))
            # Las partes ("Pt. 2", "II") se comparan aparte, en pieces(): acá no cuentan
            k = re.sub(r"\s+", " ", _TRAILING_ROMAN.sub("", _PART.sub(" ", norm(w)))).strip()
            if k:
                keys.add(k)
    return frozenset(keys)


def title_sim(src: Song, cand: Song) -> float:
    """0 a 1. Que un título contenga al otro solo cuenta si lo que sobra es ruido o el artista
    ("BTS 'Dynamite' Official MV" sí; "One Tree Hill" no es "One")."""
    ka, kb = title_keys(src), title_keys(cand)
    if ka & kb:
        return 1.0
    allowed = _TITLE_NOISE | _all_artist_tokens(src, cand)
    best = 0.0
    for x in ka:
        for y in kb:
            best = max(best, _ratio(x, y))
            short, long = sorted((x, y), key=len)
            if _contains_words(short, long):
                rest = f" {long} ".replace(f" {short} ", " ", 1).split()
                if all(w in allowed for w in rest):
                    best = max(best, 0.95)
    return best


# ---------------------------------------------------------------- versiones, covers, partes

_VERSION_TAGS = {
    "en vivo": r"live|en vivo|ao vivo|en directo|unplugged",
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
_COVER = re.compile(r"\b(?:cover|covers|karaoke|tribute|in the style of|originally performed|made famous)\b")
_COVER_ARTIST = re.compile(r"\b(?:karaoke|tribute|cover band|covers)\b")
_PART = re.compile(r"\b(?:part|pt|parte|vol|volume|chapter|capitulo)\s*(\d+|[ivx]+)\b")
_TRAILING_ROMAN = re.compile(r"\b(ii|iii|iv|vi|vii|viii|ix)$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_GENERIC = {"intro", "outro", "interlude", "interludio", "skit", "prelude", "preludio", "intermission",
            "overture", "untitled", "bonus track", "hidden track", "introduccion"}


def _title_without_artists(song: Song, *others: Song) -> str:
    """Título sin los nombres de los artistas (la banda "Live" no hace que sea una versión en vivo)."""
    text = f" {plain(song.title)} "
    for whole, parts in artist_names(song) + [n for o in others for n in artist_names(o)]:
        for n in sorted({whole, *parts}, key=len, reverse=True):
            text = text.replace(f" {n} ", " ")
    return text.strip()


def version_tags(song: Song, *others: Song) -> set[str]:
    """{'en vivo', 'remix', ...}: lo que hace que sea otra grabación de la misma canción."""
    text = _title_without_artists(song, *others)
    return {tag for tag, rx in _VERSION_RX.items() if rx.search(text)}


def is_cover(song: Song, *others: Song) -> bool:
    """Cover, karaoke o tributo: la canta otra persona aunque el título nombre al artista original."""
    if _COVER.search(_title_without_artists(song, *others)):
        return True
    return any(_COVER_ARTIST.search(plain(a)) for a in song.artists)


def pieces(song: Song) -> frozenset[str]:
    """Partes y secuelas: {'parte 2'}, {'reprise'}. Si no coinciden, son temas distintos."""
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
    """Los números del título que identifican la obra ("No. 1", "Op. 9", "Track 2"), sin años
    (1900-2099) ni lo que va entre paréntesis o después de "- Remastered" (eso es otra versión)."""
    base = plain(clean(_ANY_BRACKETS.sub(" ", song.title or "")))
    return frozenset(n for n in map(int, _DIGITS.findall(base)) if not 1900 <= n <= 2099)


def movements(song: Song) -> frozenset[str]:
    """Movimientos de una obra clásica: "I.", "II."... después de ":" o "-"."""
    return frozenset(_MOVEMENT.findall(song.title or ""))


def is_generic(song: Song) -> bool:
    return bool(title_keys(song) & _GENERIC)


def _same_context(a: Song, b: Song) -> bool:
    """Para títulos genéricos: mismo álbum (si los dos lo tienen) o, si no, duración casi igual."""
    if a.album and b.album:
        ka, kb = norm(_ANY_BRACKETS.sub(" ", a.album)), norm(_ANY_BRACKETS.sub(" ", b.album))
        return ka == kb or _ratio(ka, kb) >= 0.9
    return bool(a.duration and b.duration and abs(a.duration - b.duration) <= 3)


def _different_song(a: Song, b: Song) -> bool:
    """Reglas que separan canciones aunque título y artista se parezcan."""
    if is_cover(a, b) != is_cover(b, a):
        return True
    if pieces(a) != pieces(b):
        return True
    # Música clásica y temas numerados: "Gymnopédie No. 1" ≠ "No. 3", "I. Allegro" ≠ "II. Largo".
    # Solo si los dos tienen números (o movimientos): "Live at Wembley '86" sigue siendo el mismo tema.
    na, nb = numbers(a), numbers(b)
    if na and nb and na != nb:
        return True
    ma, mb = movements(a), movements(b)
    if ma and mb and ma != mb:
        return True
    if (is_generic(a) or is_generic(b)) and not _same_context(a, b):
        return True
    return False


# ---------------------------------------------------------------- coincidencias entre servicios

def score(src: Song, cand: Song) -> float:
    """0 a 1. Pondera título, artista y duración. ISRC idéntico = 1.

    Topes: si es otra canción (otro artista, otro título, cover, otra parte, intro de otro álbum),
    NO_MATCH_CAP → no encontrado. Si es otra versión (en vivo, remix...), VERSION_CAP → dudoso."""
    if src.isrc and cand.isrc and src.isrc.upper() == cand.isrc.upper():
        return 1.0

    t = title_sim(src, cand)
    a = artist_sim(src, cand)

    if src.duration and cand.duration:
        diff = abs(src.duration - cand.duration)
        d = 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / 27)
    else:
        d = 0.5

    total = round(0.5 * t + 0.35 * a + 0.15 * d, 3)
    other_artist = bool(artist_names(src) and artist_names(cand)) and a < ARTIST_MIN
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
    """La misma canción cantada por otros artistas (no covers caseros ni karaoke), para que la UI
    la ofrezca cuando no está la versión del artista buscado. Nunca se agregan solas."""
    out = [c for c in candidates
           if title_sim(src, c) >= TITLE_MIN and artist_sim(src, c) < ARTIST_MIN
           and not is_cover(c, src) and pieces(c) == pieces(src) and not version_tags(c, src)]
    return out[:limit]


def classify(sc: float, ok: float = SCORE_OK, minimum: float = SCORE_MIN) -> str:
    """'ok', 'dudoso' o 'no_encontrado' según el puntaje."""
    if sc >= ok:
        return OK
    if sc >= minimum:
        return DOUBTFUL
    return NOT_FOUND


def queries_for(src: Song) -> list[str]:
    title = clean(src.title)
    qs = [f"{src.artist} {title}".strip(), title]
    return list(dict.fromkeys(q for q in qs if q))   # sin repetidos, en orden


# ---------------------------------------------------------------- misma canción, otra versión

def same_song(a: Song, b: Song) -> bool:
    """¿Es la misma canción aunque sea otra versión (otro video, portada, remaster, en vivo)?
    No lo es si la canta otro artista, si es un cover o karaoke, otra parte, o un "Intro" de otro álbum."""
    if a.id and b.id and a.id == b.id:
        return True
    if a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper():
        return True
    if _different_song(a, b) or not _same_artist(a, b):
        return False
    ka, kb = title_keys(a), title_keys(b)
    return bool(ka & kb) or any(_ratio(x, y) >= SAME_TITLE for x in ka for y in kb)


class SongIndex:
    """Busca "¿ya está esta canción?" sin comparar contra toda la lista (con 5000 temas eran 11 minutos).

    Es exacto: da lo mismo que recorrer la lista con `same_song`. Para ser la misma canción hay que compartir
    id, ISRC o un título casi igual (`SAME_TITLE`), así que solo se descartan los pares que no pueden
    llegar: `SAME_TITLE` limita cuántos caracteres pueden quedar sin pareja entre los dos títulos, y eso
    limita cuánto pueden diferir su largo y las letras que usan. A los que quedan se les aplica `same_song`,
    en el orden de la lista: gana el primero, como siempre."""

    def __init__(self, songs: Iterable[Song] = ()):
        self._songs: list[Song] = []
        self._ids: dict[str, list[int]] = {}
        self._isrcs: dict[str, list[int]] = {}
        self._keys: dict[str, list[int]] = {}
        self._by_length: dict[int, dict[str, int]] = {}      # largo → {título base: letras que usa}
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
        """Los títulos base guardados que pueden llegar a `SAME_TITLE` con `key`, y lo llegan."""
        letters, size = _letters(key), len(key)
        for other_size, bucket in self._by_length.items():
            total = size + other_size
            # Lo que sobra de largo y las letras que tiene uno solo se quedan sin pareja: ratio = 1 - sin pareja / total
            unmatched = int((1 - SAME_TITLE) * total + 1e-9)
            if abs(size - other_size) > unmatched:
                continue
            for other, other_letters in bucket.items():
                if (letters ^ other_letters).bit_count() <= unmatched and (other == key or _ratio(key, other) >= SAME_TITLE):
                    yield other

    def first_match(self, song: Song) -> Song | None:
        """La primera canción de la lista que es la misma que `song`, o None."""
        spots = set(self._ids.get(song.id or "", ())) | set(self._isrcs.get((song.isrc or "").upper(), ()))
        for key in _title_keys(song.title or ""):
            for other in self._similar_keys(key):
                spots.update(self._keys[other])
        return next((self._songs[i] for i in sorted(spots) if same_song(song, self._songs[i])), None)


def _letters(key: str) -> int:
    """Qué letras usa un texto, como bits (dos letras pueden compartir bit: solo acerca, nunca aleja)."""
    bits = 0
    for c in set(key):
        bits |= 1 << (ord(c) % 61)
    return bits


def find_duplicates(songs: list[Song]) -> list[Duplicate]:
    """Copias repetidas de una playlist. Queda siempre la que está primero. Cada copia dice si es
    la misma grabación (SAME) u otra versión (OTHER_VERSION: en vivo, remix...), para que la UI
    deje sin tildar estas últimas."""
    kept = SongIndex()
    dups: list[Duplicate] = []
    for i, s in enumerate(songs):
        twin = kept.first_match(s)
        if twin:
            kind = SAME if version_tags(s, twin) == version_tags(twin, s) else OTHER_VERSION
            dups.append(Duplicate(i, s, twin, kind))
        else:
            kept.add(s)
    return dups
