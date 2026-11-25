"""The check for each step of a refactor: nothing changes all of a sudden, neither what the app does nor how it looks.

    py tools/check_step.py --base base.json     (once, before touching anything: the starting style snapshot)
    py tools/check_step.py base.json            (after each step)

Runs, in order and stopping at the first failure:
  1. pyflakes (unused code, misspelled names)
  2. every test
  3. the comparison with your real reports, if the step touched app/core/matching.py
  4. the style snapshot of every screen, which has to show 0 differences from the starting one
If it says OK, the step can go in its own commit; if not, only that step is undone.
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
        ok, out = run("starting style snapshot", *snapshot, args[1])
        print(out.splitlines()[-1] if out else "")
        return 0 if ok else 1
    if len(args) != 1:
        sys.exit(__doc__)
    base = args[0]
    steps = [("pyflakes", [PY, "-m", "pyflakes", "app", "tests", "tools"]),
             ("tests", [PY, "-m", "pytest", "-q"])]
    if changed("app/core/matching.py"):
        steps.append(("real reports", [PY, str(ROOT / "tools" / "check_reports.py")]))
    for title, cmd in steps:
        ok, out = run(title, *cmd)
        last = out.splitlines()[-1] if out else ""
        if not ok or (title == "real reports" and " 0 change category" not in last):
            print(f"FAILED: {title}\n{out[-3000:]}")
            return 1
        print(f"  {last or 'no problems'}")
    after = Path(tempfile.mkdtemp()) / "after.json"
    ok, out = run("style snapshot", *snapshot, str(after))
    if not ok:
        print(f"FAILED: style snapshot\n{out[-3000:]}")
        return 1
    ok, out = run("comparison with the starting snapshot", *snapshot, "--diff", base, str(after))
    first = out.splitlines()[0] if out else ""
    print(f"  {first}")
    if first != "0 differences":
        print(f"FAILED: how the app looks changed\n{out}")
        return 1
    print("OK: the step changed neither what the app does nor how it looks.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
