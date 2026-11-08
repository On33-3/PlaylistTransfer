"""Compara el matching nuevo con el del script de consola sobre tus reportes reales.

Uso (desde la raíz del repo):  py tools/check_reports.py [reporte_*.csv ...]

Los reportes (reporte_*.csv) los generó el script de consola original (ya borrado), son tuyos y no
se suben al repo. Su columna "puntaje" es lo que decidió ese script: se compara contra eso.
Correlo cada vez que cambies app/core/matching.py: ninguna coincidencia real debería empeorar de
categoría sin que lo sepas.
"""
import csv
import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.matching import classify, score  # noqa: E402
from app.core.models import Song  # noqa: E402


def song(label: str, duration: str) -> Song:
    artist, _, title = label.partition(" - ")
    return Song(title, [artist], duration=int(duration) if duration else None)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    files = sys.argv[1:] or sorted(glob.glob(str(ROOT / "reporte_*.csv")))
    if not files:
        print("No hay reportes (reporte_*.csv) en la raíz del repo: no hay con qué comparar.")
        return 0
    total = changed = 0
    for f in files:
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("destino", "-") == "-":
                    continue
                total += 1
                a, b = song(r["origen"], r["origen_duracion"]), song(r["destino"], r["destino_duracion"])
                old = float(r["puntaje"])           # lo que decidió el script de consola
                new = score(a, b)
                if classify(old) != classify(new):
                    changed += 1
                    print(f"CAMBIA  {r['origen']}  →  {r['destino']}:  {old:.2f} {classify(old)}  →  "
                          f"{new:.2f} {classify(new)}")
    print(f"{total} coincidencias reales revisadas, {changed} cambian de categoría.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
