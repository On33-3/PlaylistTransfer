"""Coincidencias entre servicios y detección de repetidos.

Extraído de `legacy/transfer_playlist.py` (probado con cuentas reales). Única diferencia a
propósito: `score` exige artista y título (ver su docstring). `tests/test_matching.py` verifica
que, fuera de eso, ambas versiones den lo mismo.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.core.models import Duplicate, Song

SCORE_OK = 0.75      # desde acá se agrega sin dudas
SCORE_MIN = 0.60     # entre MIN y OK se agrega, pero queda marcado como dudoso
ARTIST_MIN = 0.60    # parecido mínimo de artista: por debajo, es otro artista
TITLE_MIN = 0.75     # parecido mínimo de título: por debajo, es otro tema
NO_MATCH_CAP = 0.50  # tope cuando no se cumple alguno de los dos (queda como no encontrado)

OK, DOUBTFUL, NOT_FOUND = "ok", "dudoso", "no_encontrado"


# ---------------------------------------------------------------- normalización

_NOISE_PAREN = re.compile(
    r"[\(\[][^\)\]]*\b(feat|ft|with|remaster(ed)?|official|video|audio|lyrics?|visualizer|hd|4k)\b[^\)\]]*[\)\]]",
    re.I,
)
_NOISE_DASH = re.compile(r"\s+-\s+[^-]*\b(remaster(ed)?|official|video|audio|lyrics?)\b.*$", re.I)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?)\s+.*$", re.I)


def clean(text: str) -> str:
    """Saca ruido típico de títulos: (Official Video), - Remastered 2011, feat. X."""
    text = _NOISE_PAREN.sub(" ", text or "")
    text = _NOISE_DASH.sub(" ", text)
    text = _FEAT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm(text: str) -> str:
    """Minúsculas, sin acentos, sin puntuación (respeta alfabetos no latinos)."""
    text = unicodedata.normalize("NFKD", clean(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        ratio = max(ratio, 0.9)
    return ratio


# ---------------------------------------------------------------- coincidencias entre servicios

def artist_sim(src: Song, cand: Song) -> float:
    """0 a 1: el mejor parecido entre los artistas de uno y otro."""
    a = 0.0
    for sa in src.artists or [""]:
        for ca in cand.artists or [""]:
            a = max(a, sim(sa, ca))
    # En YouTube a veces el artista viene en el título del video: "Artista - Tema"
    if src.artist and norm(src.artist) in norm(cand.title):
        a = max(a, 0.9)
    return a


def score(src: Song, cand: Song) -> float:
    """0 a 1. Pondera título, artista y duración. ISRC idéntico = 1.

    El artista y el título son requisitos, no solo pesos: si el artista es otro (cuando los dos
    lo tienen) o el título no se parece lo suficiente, el puntaje queda en NO_MATCH_CAP como
    máximo, o sea "no encontrado". Así un cover o un tema homónimo de otro artista
    ("From The Start" de Laufey ≠ de Good Kid), u otro tema del mismo artista
    ("Sunshine" ≠ "Sunkissed"), no se agregan aunque la duración coincida."""
    if src.isrc and cand.isrc and src.isrc.upper() == cand.isrc.upper():
        return 1.0

    t = sim(src.title, cand.title)
    a = artist_sim(src, cand)

    if src.duration and cand.duration:
        diff = abs(src.duration - cand.duration)
        d = 1.0 if diff <= 3 else max(0.0, 1 - (diff - 3) / 27)
    else:
        d = 0.5

    total = round(0.5 * t + 0.35 * a + 0.15 * d, 3)
    other_artist = bool(src.artists and cand.artists) and a < ARTIST_MIN
    if t < TITLE_MIN or other_artist:
        return min(total, NO_MATCH_CAP)
    return total


def best_match(src: Song, candidates: list[Song]) -> tuple[Song | None, float]:
    best, best_score = None, 0.0
    for c in candidates:
        s = score(src, c)
        if s > best_score:
            best, best_score = c, s
    return best, best_score


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

_ANY_BRACKETS = re.compile(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]")
_PROD = re.compile(r"\s+(prod\.?|produced by)\s+.*$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")


def title_keys(song: Song) -> set[str]:
    """Título base, sin lo que cambia entre versiones: (Official Video), [Remastered], (acoustic),
    feat. X, prod. X, '- Remastered 2011'. En YouTube también prueba sin el 'Artista - ' del principio."""
    t = song.title or ""
    variants = [t] + _DASH.split(t, maxsplit=1)
    keys = set()
    for v in variants:
        v = _PROD.sub(" ", _FEAT.sub(" ", _ANY_BRACKETS.sub(" ", v)))
        k = norm(v)
        if k:
            keys.add(k)
    return keys


def _same_artist(a: Song, b: Song) -> bool:
    aa = {norm(x) for x in a.artists if norm(x)}
    bb = {norm(x) for x in b.artists if norm(x)}
    if aa and bb and any(x == y or SequenceMatcher(None, x, y).ratio() >= 0.85 for x in aa for y in bb):
        return True
    ta, tb = norm(a.title), norm(b.title)          # videos tipo "Artista - Tema"
    if any(x in tb for x in aa) or any(y in ta for y in bb):
        return True
    if not aa or not bb:                           # sin artista: que la duración sea casi igual
        return bool(a.duration and b.duration and abs(a.duration - b.duration) <= 5)
    return False


def same_song(a: Song, b: Song) -> bool:
    """¿Es la misma canción aunque sea otra versión (otro video, portada, remaster, acústica)?"""
    if a.id and b.id and a.id == b.id:
        return True
    if not _same_artist(a, b):
        return False
    ka, kb = title_keys(a), title_keys(b)
    return bool(ka & kb) or any(SequenceMatcher(None, x, y).ratio() >= 0.92 for x in ka for y in kb)


def find_duplicates(songs: list[Song]) -> list[Duplicate]:
    """Copias repetidas de una playlist. Queda siempre la que está primero."""
    kept: list[Song] = []
    dups: list[Duplicate] = []
    for i, s in enumerate(songs):
        twin = next((k for k in kept if same_song(s, k)), None)
        if twin:
            dups.append(Duplicate(i, s, twin))
        else:
            kept.append(s)
    return dups
