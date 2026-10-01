"""Узгодженість документації з кодом (охоронні перевірки).

Знайдено проходом "узгодження проєкт - документація": 39 код-спанів, розірваних
переносом рядка посеред ідентифікатора (Markdown рендерить їх з пробілом:
`STARLINK_DISPLAY_SHUTDOWN_ MESSAGE_DELAY_SEC`), застарілі посилання на
перенесені/перейменовані функції, 9 ендпоінтів без жодної згадки. Ці тести не
дають повторити.
"""
import ast
import glob
import os
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DOCS = ["README.md", "README.en.md", *sorted(str(Path(p).relative_to(ROOT)) for p in (ROOT / "docs").glob("*.md"))]
CODE_DOCS = ["README.md", "README.en.md", "docs/architecture.md", "docs/index.md"]   # поточний стан, не історія рішень
SPAN = re.compile(r"(?<!`)`(?!`)([^`]+?)(?<!`)`(?!`)")
_FILE_SUFFIXES = {"py", "js", "sh", "md", "txt", "toml", "ini", "json", "html", "css", "service", "timer", "yml", "db", "local", "sha256"}


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _paragraphs_outside_fences(text):
    for i, segment in enumerate(re.split(r"(```.*?```)", text, flags=re.S)):
        if i % 2 == 0:
            yield from re.split(r"\n[ \t]*\n", segment)


@pytest.mark.parametrize("rel", DOCS)
def test_no_code_span_is_broken_inside_an_identifier(rel):
    """`muted_reboot_⏎count` рендериться як `muted_reboot_ count`. Переносити
    можна лише на межі слів; ідентифікатор лишати цілим."""
    broken = []
    for paragraph in _paragraphs_outside_fences(_read(rel)):
        if paragraph.count("`") % 2:
            continue
        for match in SPAN.finditer(paragraph):
            parts = re.split(r"[ \t]*\n[ \t]*", match.group(1))
            for left, right in zip(parts, parts[1:], strict=False):   # пари сусідніх шматків: довжини різні навмисно
                if left and right and (left[-1] in "_.-/(" or right[0] in ".)_"):
                    broken.append(f"{left[-25:]}⏎{right[:25]}")
    assert broken == [], f"{rel}: розірвані ідентифікатори: {broken[:5]}"


def _app_symbols():
    defined = {}
    for path in glob.glob(str(ROOT / "app" / "*.py")):
        module = os.path.basename(path)[:-3]
        if module == "__init__":
            continue
        names = set()
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                names.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        defined[module] = names
    return defined


@pytest.mark.parametrize("rel", CODE_DOCS)
def test_module_function_references_exist_in_code(rel):
    """`services.check_updates_now()`, `pi_power.shutdown_from_button()`: якщо
    функцію перенесли чи перейменували, документація не лишається з привидом.
    Перевіряється лише форма `модуль_app.ім'я` (стороння/стандартна бібліотека
    і файлові назви - поза перевіркою)."""
    defined = _app_symbols()
    stale = []
    flat = re.sub(r"\s*\n\s*", " ", _read(rel))
    for span in (m.group(1).strip() for m in SPAN.finditer(flat)):
        match = re.match(r"^(\w+)\.(_?\w+)(?:\(.*\))?$", span)
        if not match or match.group(1) not in defined or match.group(2) in _FILE_SUFFIXES:
            continue
        module, name = match.groups()
        # саме в ЦЬОМУ модулі: перенесену функцію в іншому не зараховуємо. Обмеження: у множину
        # входять і локальні змінні модуля, тож ім'я, що збігається з ними (`display.image`), не ловиться
        if name not in defined[module]:
            stale.append(span)
    assert stale == [], f"{rel}: посилання на неіснуючі символи: {stale}"


def test_readme_languages_have_the_same_sections_and_numbers():
    uk, en = _read("README.md"), _read("README.en.md")
    numbers = lambda t: Counter(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", re.sub(r"```.*?```", "", t, flags=re.S)))
    assert len(re.findall(r"(?m)^## ", uk)) == len(re.findall(r"(?m)^## ", en))
    assert dict(numbers(uk) - numbers(en)) == {} and dict(numbers(en) - numbers(uk)) == {}


def test_docs_index_mentions_every_project_file():
    index = _read("docs/index.md")
    files = {os.path.basename(p) for d in ("app", "static", "templates", "tests", "scripts", "systemd")
             for p in glob.glob(str(ROOT / d / "*")) if os.path.isfile(p)
             and not p.endswith((".pyc", "__init__.py", ".png", ".ico"))}
    files |= {"constraints.txt", "PROVENANCE", "ruff.toml", "mypy.ini", "pytest.ini", "requirements.txt", "requirements-dev.txt"}
    def mentioned(name):
        stem, ext = os.path.splitext(name)
        patterns = [rf"`(?:[\w./-]*/)?{re.escape(name)}`"]                      # `file.py`, `vendor/PROVENANCE`
        if ext:
            patterns.append(rf"`{re.escape(stem)}\.\w+`/`\.{re.escape(ext[1:])}`")   # `x.service`/`.timer`
        return any(re.search(pattern, index) for pattern in patterns)

    # точний збіг ЗА ІМЕНЕМ у зворотних лапках (підрядок у назві іншого файлу не рахується)
    missing = sorted(f for f in files if not mentioned(f))
    assert missing == [], f"файли без згадки в docs/index.md: {missing}"


def _actual_test_counts():
    """Реальна кількість тестів (--collect-only: тести НЕ виконуються, тож
    рекурсії немає) і файлів у tests/."""
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", "-p", "no:cacheprovider", str(ROOT / "tests")],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    collected = re.search(r"(\d+) tests? collected", result.stdout)
    assert collected, result.stdout[-300:]
    return int(collected.group(1)), len(list((ROOT / "tests").glob("*.py")))


def test_readme_test_and_file_counts_match_reality():
    """Числа в обох README (проза й рядок дерева каталогів, який паритет-тест
    не бачить, бо він у блоці коду) збігаються з фактичними. Раніше вони
    застарівали після кожного додавання тестів."""
    tests, files = _actual_test_counts()
    expectations = {
        "README.md": [(r"(\d+) тест\w* \(`pytest-randomly`", tests), (r"(\d+)\s+файл\w*\s+у `tests/`", files),
                      (r"# (\d+) тест\w* \(\d+ файл\w*\)", tests), (r"# \d+ тест\w* \((\d+) файл\w*\)", files)],
        "README.en.md": [(r"(\d+) tests \(`pytest-randomly`", tests), (r"(\d+)\s+files in `tests/`", files),
                         (r"# (\d+) tests \(\d+ files\)", tests), (r"# \d+ tests \((\d+) files\)", files)],
    }
    wrong = []
    for rel, rules in expectations.items():
        text = _read(rel)
        for pattern, actual in rules:
            match = re.search(pattern, text)
            if not match:
                wrong.append(f"{rel}: не знайдено за шаблоном {pattern}")
            elif int(match.group(1)) != actual:
                wrong.append(f"{rel}: каже {match.group(1)}, фактично {actual}")
    assert wrong == [], "оновіть лічильники в README: " + "; ".join(wrong)
