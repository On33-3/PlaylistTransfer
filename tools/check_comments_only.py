"""Checks that a change only touched comments and docstrings: the code is exactly the same.

    py tools/check_comments_only.py            (the working tree against the last commit)
    py tools/check_comments_only.py HEAD~3     (against another commit)

For each changed file:
  - Python: the syntax tree without docstrings has to be identical (comments are not in it);
  - JavaScript, CSS and HTML: the text without comments, with whitespace collapsed, has to be identical.
Any other changed file counts as a code change. Exit code 0 only if nothing but comments changed.
Used when translating the code comments, so a slip in the code cannot hide among hundreds of edits.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def python_code(text: str) -> str:
    """The syntax tree without docstrings (comments never reach it)."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def js_code(text: str) -> str:
    """JavaScript without // and /* */ comments. Strings and template literals are kept as they are,
    so a "//" inside a URL is not taken for a comment."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return collapse("".join(out))


def css_code(text: str) -> str:
    return collapse(re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def html_code(text: str) -> str:
    return collapse(re.sub(r"<!--.*?-->", "", text, flags=re.S))


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


CODE = {".py": python_code, ".js": js_code, ".css": css_code, ".html": html_code}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    changed = [f for f in git("diff", "--name-only", base).splitlines() if f]
    problems = []
    for rel in changed:
        path = ROOT / rel
        code = CODE.get(path.suffix)
        if code is None:
            problems.append(f"{rel}: not a file this check understands (only .py, .js, .css, .html)")
            continue
        try:
            before = git("show", f"{base}:{rel}")
        except subprocess.CalledProcessError:
            problems.append(f"{rel}: new file")
            continue
        if not path.exists():
            problems.append(f"{rel}: deleted")
        elif code(before) != code(path.read_text(encoding="utf-8")):
            problems.append(f"{rel}: the code changed, not only comments")
    print(f"{len(changed)} changed files")
    for p in problems:
        print("  " + p)
    print("OK: only comments and docstrings changed" if not problems else f"{len(problems)} with code changes")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
