"""Checks that a change only touched comments and docstrings: the code is exactly the same.

    py tools/check_comments_only.py            (the working tree against the last commit)
    py tools/check_comments_only.py HEAD~3     (against another commit)

For each changed file:
  - Python (also PyInstaller's .spec): the syntax tree without docstrings has to be identical;
  - JavaScript, CSS, HTML and YAML: the text without comments, with whitespace collapsed, has to be identical.
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


REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}     # a "/" after these starts a regular expression


def js_parts(text: str):
    """Splits JavaScript into ("code" | "comment", text, line). Strings, template literals and regular
    expressions are code, so a "//" in a URL or a quote in /[&<>"']/ is not misread."""
    i, n, start, last = 0, len(text), 0, ""

    def skip_quoted(j, quote):
        """From an opening quote to just after its closing one. Inside a template literal, each ${...}
        is code again, with its own strings and nested template literals."""
        j += 1
        while j < n and text[j] != quote:
            if text[j] == "\\":
                j += 2
            elif quote == "`" and text.startswith("${", j):
                j, depth = j + 2, 1
                while j < n and depth:
                    if text[j] in "'\"`":
                        j = skip_quoted(j, text[j])
                        continue
                    depth += {"{": 1, "}": -1}.get(text[j], 0)
                    j += 1
            else:
                j += 1
        return j + 1

    while i < n:
        c = text[i]
        if c in "'\"`":
            i, last = skip_quoted(i, c), c
        elif text.startswith("//", i) or text.startswith("/*", i):
            if start < i:
                yield "code", text[start:i], text.count("\n", 0, start) + 1
            end = text.find("\n", i) if text[i + 1] == "/" else text.find("*/", i + 2) + 2
            end = n if end < (0 if text[i + 1] == "/" else 2) else end
            yield "comment", text[i:end], text.count("\n", 0, i) + 1
            i = start = end
        elif c == "/" and last in REGEX_AFTER:              # a regular expression: up to its closing /
            j, in_class = i + 1, False
            while j < n and (text[j] != "/" or in_class) and text[j] != "\n":
                in_class = (in_class or text[j] == "[") and text[j] != "]"
                j += 2 if text[j] == "\\" else 1
            i, last = j + 1, "/"
        else:
            if not c.isspace():
                last = c
            i += 1
    if start < n:
        yield "code", text[start:], text.count("\n", 0, start) + 1


def js_code(text: str) -> str:
    """JavaScript without // and /* */ comments."""
    return collapse("".join(part for kind, part, _ in js_parts(text) if kind == "code"))


def css_code(text: str) -> str:
    return collapse(re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def html_code(text: str) -> str:
    return collapse(re.sub(r"<!--.*?-->", "", text, flags=re.S))


def yaml_code(text: str) -> str:
    """YAML without # comments: a # starts one at the start of a line or after a space, outside quotes
    (so "${NAME#v}" in a value stays)."""
    lines = []
    for line in text.splitlines():
        quote, cut = None, len(line)
        for i, c in enumerate(line):
            if quote:
                quote = None if c == quote else quote
            elif c in "'\"":
                quote = c
            elif c == "#" and (i == 0 or line[i - 1].isspace()):
                cut = i
                break
        lines.append(line[:cut])
    return collapse("\n".join(lines))


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


CODE = {".py": python_code, ".spec": python_code, ".js": js_code, ".css": css_code, ".html": html_code,
        ".yml": yaml_code, ".yaml": yaml_code}


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
            problems.append(f"{rel}: not a file this check understands ({', '.join(sorted(CODE))})")
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
