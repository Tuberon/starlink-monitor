"""Структурні гарантії залежностей між модулями app/.

Раніше два цикли трималися на лінивих імпортах: monitor <-> telegram_bot
(webapp і бот імпортували весь monitor заради трьох функцій) і db <-> i18n
(check_integrity повертав перекладений текст). Обидва прибрано; ці тести не
дають повернути.
"""
import ast
import glob
import os
from pathlib import Path

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _modules():
    return {os.path.basename(f)[:-3]: f for f in glob.glob(os.path.join(APP, "*.py"))
            if not f.endswith("__init__.py")}


def _import_graph():
    """Ребра модуль -> модуль app/, ВКЛЮЧНО з лінивими імпортами всередині функцій."""
    mods = _modules()
    graph = {m: set() for m in mods}
    for m, path in mods.items():
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            targets = []
            if isinstance(node, ast.ImportFrom) and node.module == "app":
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                targets = [node.module.split(".")[1]]
            elif isinstance(node, ast.Import):
                targets = [a.name.split(".")[1] for a in node.names if a.name.startswith("app.")]
            graph[m] |= {t for t in targets if t in mods and t != m}
    return graph


def _find_cycles(graph):
    cycles = set()

    def walk(node, path):
        for nxt in graph[node]:
            if nxt in path:
                cycles.add(tuple(sorted(path[path.index(nxt):])))
            elif len(path) < 8:
                walk(nxt, path + [nxt])
    for start in graph:
        walk(start, [start])
    return sorted(cycles)


def test_no_import_cycles_even_through_lazy_imports():
    assert _find_cycles(_import_graph()) == []


def test_entry_points_are_not_libraries():
    """Точка входу сервісу (модуль із logging.basicConfig) - не бібліотека:
    жоден інший модуль її не імпортує (раніше webapp і telegram_bot тягли
    monitor заради 3 функцій, тепер вони в services)."""
    def calls_basicconfig(path):
        # справжній ВИКЛИК на верхньому рівні (AST), а не згадка в докстрінгу/коментарі
        return any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "basicConfig"
                   for n in ast.parse(Path(path).read_text(encoding="utf-8")).body)

    mods = _modules()
    entry = {m for m, path in mods.items() if calls_basicconfig(path)}
    assert entry == {"display", "monitor", "shutdown_button", "webapp"}
    graph = _import_graph()
    importers = {m: sorted(graph[m] & entry) for m in graph if graph[m] & entry}
    assert importers == {}, importers


def test_data_layer_does_not_depend_on_presentation():
    """db - шар даних: не залежить від i18n, labels, Telegram чи веб-шару."""
    forbidden = {"i18n", "labels", "telegram_bot", "telegram_notify", "webapp", "monitor", "services", "display"}
    assert _import_graph()["db"] & forbidden == set()
