"""El control de cada paso de un refactor: que nada cambie de golpe, ni lo que hace la app ni cómo se ve.

    py tools/check_step.py --base base.json     (una vez, antes de tocar nada: la foto de estilos de partida)
    py tools/check_step.py base.json            (después de cada paso)

Corre, en orden y cortando en el primero que falla:
  1. pyflakes (código sin usar, nombres mal escritos)
  2. todos los tests
  3. la comparación con tus reportes reales, si el paso tocó app/core/matching.py
  4. la foto de estilos de cada pantalla, que tiene que dar 0 diferencias con la de partida
Si dice OK, el paso se puede guardar en su propio commit; si no, se deshace ese paso solo.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(title: str, *cmd: str) -> tuple[bool, str]:
    print(f"· {title}…", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out


def changed(path: str) -> bool:
    r = subprocess.run(["git", "status", "--porcelain", "--", path], cwd=ROOT, capture_output=True, text=True)
    return bool(r.stdout.strip())


def main(args: list[str]) -> int:
    snapshot = [PY, str(ROOT / "tools" / "ui_style_snapshot.py")]
    if len(args) == 2 and args[0] == "--base":
        ok, out = run("foto de estilos de partida", *snapshot, args[1])
        print(out.splitlines()[-1] if out else "")
        return 0 if ok else 1
    if len(args) != 1:
        sys.exit(__doc__)
    base = args[0]
    steps = [("pyflakes", [PY, "-m", "pyflakes", "app", "tests", "tools"]),
             ("tests", [PY, "-m", "pytest", "-q"])]
    if changed("app/core/matching.py"):
        steps.append(("reportes reales", [PY, str(ROOT / "tools" / "check_reports.py")]))
    for title, cmd in steps:
        ok, out = run(title, *cmd)
        last = out.splitlines()[-1] if out else ""
        if not ok or (title == "reportes reales" and " 0 cambian" not in last):
            print(f"FALLÓ: {title}\n{out[-3000:]}")
            return 1
        print(f"  {last or 'sin problemas'}")
    after = Path(tempfile.mkdtemp()) / "despues.json"
    ok, out = run("foto de estilos", *snapshot, str(after))
    if not ok:
        print(f"FALLÓ: foto de estilos\n{out[-3000:]}")
        return 1
    ok, out = run("comparación con la foto de partida", *snapshot, "--diff", base, str(after))
    first = out.splitlines()[0] if out else ""
    print(f"  {first}")
    if first != "0 diferencias":
        print(f"FALLÓ: cambió cómo se ve la app\n{out}")
        return 1
    print("OK: el paso no cambió lo que hace la app ni cómo se ve.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
