"""Compares the new matching with the console script's on your real reports.

Usage (from the repo root):  py tools/check_reports.py [reporte_*.csv ...]

The reports (reporte_*.csv) were made by the original console script (since removed); they are yours and
are never uploaded to the repo. Their "puntaje" column is what that script decided: it's compared against that.
Run it every time you change app/core/matching.py: no real match should drop a category
without you knowing.
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
        print("No reports (reporte_*.csv) in the repo root: nothing to compare against.")
        return 0
    total = changed = 0
    for f in files:
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r.get("destino", "-") == "-":
                    continue
                total += 1
                a, b = song(r["origen"], r["origen_duracion"]), song(r["destino"], r["destino_duracion"])
                old = float(r["puntaje"])           # what the console script decided
                new = score(a, b)
                if classify(old) != classify(new):
                    changed += 1
                    print(f"CHANGES  {r['origen']}  →  {r['destino']}:  {old:.2f} {classify(old)}  →  "
                          f"{new:.2f} {classify(new)}")
    print(f"{total} real matches checked, {changed} change category.")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
