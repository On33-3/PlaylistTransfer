"""tools/check_comments_only.py: a comment or docstring change passes; any code change, however small, is caught."""
from __future__ import annotations

from tools.check_comments_only import css_code, html_code, js_code, python_code

PY = '''"""Módulo."""
def f(x):
    """Suma uno."""
    return x + 1  # el siguiente
'''


def test_python_comments_and_docstrings_do_not_count():
    translated = '''"""Module."""
def f(x):
    """Adds one."""
    return x + 1  # the next one
'''
    assert python_code(PY) == python_code(translated)


def test_python_code_changes_are_caught():
    assert python_code(PY) != python_code(PY.replace("x + 1", "x + 2"))
    assert python_code(PY) != python_code(PY.replace("def f(x)", "def f(y)").replace("x + 1", "y + 1"))
    assert python_code(PY) == python_code(PY.replace('    """Suma uno."""\n', ""))   # a docstring is not code


def test_python_a_string_that_is_not_a_docstring_counts():
    a, b = 'x = 1\ny = "uno"\n', 'x = 1\ny = "one"\n'
    assert python_code(a) != python_code(b)


def test_js_comments_do_not_count_but_strings_do():
    a = 'const u = "http://127.0.0.1"; // la dirección\n/* bloque */ f(u);'
    assert js_code(a) == js_code('const u = "http://127.0.0.1"; // the address\n/* block */ f(u);')
    assert js_code(a) != js_code(a.replace("127.0.0.1", "127.0.0.2"))
    assert js_code("t(`hola ${x}`); // c") != js_code("t(`chau ${x}`); // c")


def test_js_a_quote_inside_a_regular_expression_is_not_a_string():
    """It used to read /[&<>"']/ as the start of a string and lose track of everything after it."""
    a = """const esc = s => s.replace(/[&<>"']/g, c => c); // escapa\nlet x = 1; // uno\n"""
    b = """const esc = s => s.replace(/[&<>"']/g, c => c); // escapes\nlet x = 1; // one\n"""
    assert js_code(a) == js_code(b)
    assert js_code(a) != js_code(a.replace("x = 1", "x = 2"))
    assert js_code("t = a / b; // half") == js_code("t = a / b; // mitad")


def test_js_nested_template_literals_are_read_whole():
    """A template literal inside ${...} of another one used to end the outer one too early."""
    a = 'h = `<b>${xs.map(x => `<i>${x}</i>`).join("")}</b>`; // lista\nok = 1; // sí\n'
    b = 'h = `<b>${xs.map(x => `<i>${x}</i>`).join("")}</b>`; // list\nok = 1; // yes\n'
    assert js_code(a) == js_code(b)
    assert js_code(a) != js_code(a.replace("<i>", "<u>"))


def test_css_and_html_comments_do_not_count():
    assert css_code("/* botón */ .btn { height: 42px; }") == css_code("/* button */\n.btn {\n  height: 42px;\n}")
    assert css_code(".btn { height: 42px; }") != css_code(".btn { height: 36px; }")
    assert html_code("<!-- menú --><nav></nav>") == html_code("<!-- menu -->\n<nav></nav>")
