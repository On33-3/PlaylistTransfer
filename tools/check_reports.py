"""Compara el matching nuevo con el del script de consola sobre tus reportes reales.

Uso (desde la raíz del repo):  py tools/check_reports.py [reporte_*.csv ...]

Los reportes (reporte_*.csv) los genera legacy/transfer_playlist.py, son tuyos y no se suben al
repo. Correlo cada vez que cambies app/core/matching.py: ninguna coincidencia real debería
empeorar de categoría sin que lo sepas.
"""
import csv
import glob
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.matching import classify, score  # noqa: E402
from app.core.models import Song  # noqa: E402


def load_legacy():
    spec = importlib.util.spec_from_file_location("legacy_transfer", ROOT / "legacy" / "transfer_playlist.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def song(label: str, duration: str) -> Song:
    artist, _, title = label.partition(" - ")
    return Song(title, [artist], duration=int(duration) if duration else None)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    files = sys.argv[1:] or sorted(glob.glob(str(ROOT / "reporte_*.csv")))
    if not files:
        print("No hay reportes (reporte_*.csv). Hacé una transferencia con el script de consola primero.")
        return 0
    legacy = load_legacy()
    total = changed = 0
    for f in files:
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("destino", "-") == "-":
                    continue
                total += 1
                a, b = song(r["origen"], r["origen_duracion"]), song(r["destino"], r["destino_duracion"])
                old = legacy.score(legacy.Song(a.title, a.artists, duration=a.duration),
                                   legacy.Song(b.title, b.artists, duration=b.duration))
                new = score(a, b)
                if classify(old) != classify(new):
                    changed += 1
                    print(f"CAMBIA  {r['origen']}  →  {r['destino']}:  {old:.2f} {classify(old)}  →  "
                          f"{new:.2f} {classify(new)}")
    print(f"{total} coincidencias reales revisadas, {changed} cambian de categoría.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
