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


def _github_slug(title):
    """Якір заголовка як у GitHub: нижній регістр, символи крім літер/цифр/пробілу/дефіса/_ видаляються
    (разом з емодзі й U+FE0F), пробіли -> дефіси; ведучий пробіл після емодзі дає ведучий дефіс."""
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


@pytest.mark.parametrize("rel", ["README.md", "README.en.md"])
def test_readme_table_of_contents_matches_the_headings(rel):
    """Зміст містить КОЖЕН розділ `##` у тому самому порядку, і кожне посилання веде на існуючий заголовок.
    Раніше новий розділ міг з'явитись без запису в змісті (або лишитись із застарілим якорем)."""
    text = _read(rel)
    toc = re.search(r"\*\*(?:Зміст|Contents)\*\*:(.*?)\n\s*\n", text, re.S)
    assert toc, "не знайдено рядка змісту"
    links = [link.replace("\ufe0f", "") for link in re.findall(r"\]\(#([^)]+)\)", toc.group(1))]
    headings = [_github_slug(h) for h in re.findall(r"(?m)^## (.+)$", re.sub(r"```.*?```", "", text, flags=re.S))]
    assert links == headings, f"зміст і заголовки розходяться: {sorted(set(links) ^ set(headings))}"


# ---- README: твердження звірені з кодом ----

_README_FILES = ["README.md", "README.en.md"]
# Системні юніти, які README згадує, але яких немає в проєкті (явний список: загальне виключення для *.timer пропустило б
# і помилку на кшталт `healthcheck.timer` замість starlink-monitor-healthcheck.timer).
_SYSTEM_UNITS = {"fstrim.timer"}


def _readme_text_and_spans(rel):
    text = re.sub(r"```.*?```", "", _read(rel), flags=re.S)
    spans = {re.sub(r"\s+", " ", m).strip() for m in re.findall(r"`([^`]+?)`", text, re.S)}
    return text, spans


@pytest.mark.parametrize("rel", _README_FILES)
def test_readme_references_existing_files_variables_and_tests(rel):
    """Файли, змінні `STARLINK_*` і тести, згадані в README, існують. Раніше `healthcheck.timer` жив у тексті,
    хоча справжня назва - `starlink-monitor-healthcheck.timer`."""
    text, spans = _readme_text_and_spans(rel)
    everything = {p.name for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts}
    everything |= {str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if ".git" not in p.parts}
    missing = []
    for span in spans:
        first = (span.split() or [span])[0].rstrip(",.;:)")
        looks_like_file = re.search(r"\.(py|sh|md|txt|toml|ini|json|service|timer|html|js|css)$", first) or first.startswith(
            ("app/", "docs/", "scripts/", "tests/", "static/", "templates/", "systemd/"))
        if not looks_like_file or first.startswith(("/", "~", ".../")) or "*" in first or "<" in first or first in _SYSTEM_UNITS:
            continue
        if first not in everything and first.rstrip("/") not in everything:
            missing.append(first)
    assert missing == [], f"{rel}: немає в проєкті: {sorted(missing)}"
    config_src = _read("app/config.py")
    known = set(re.findall(r'"(STARLINK_[A-Z0-9_]+)"', config_src))
    assert set(re.findall(r"STARLINK_[A-Z0-9_]+", text)) <= known, sorted(set(re.findall(r"STARLINK_[A-Z0-9_]+", text)) - known)
    tests_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("*.py"))
    assert [s for s in spans if re.fullmatch(r"test_\w+", s) and f"def {s}" not in tests_src] == []


@pytest.mark.parametrize("rel", _README_FILES)
def test_readme_telegram_commands_match_the_bot(rel):
    code = set(re.findall(r'"(/[a-z]+)"', _read("app/telegram_bot.py")))
    text, _ = _readme_text_and_spans(rel)
    documented = set(re.findall(r"(?<![\w/])(/(?:status|checkupdates|reboot|id|help|start))\b", text))
    assert documented == code, f"{rel}: лише в README {sorted(documented - code)}, лише в коді {sorted(code - documented)}"


def _security_section(rel):
    text = _read(rel)
    match = re.search(r"## 🔒 [^\n]+\n(.*?)(?=\n## )", text, re.S)
    assert match, f"{rel}: немає розділу про безпеку"
    return match.group(1)


def test_readme_security_claims_match_the_install_script_units_and_config():
    """Розділ "Безпека" стверджує: sudo - рівно чотири команди; від root працюють лише healthcheck і WAN-failover;
    env 600, каталог даних 700; інтерфейс слухає 0.0.0.0. Кожне - проти коду."""
    install = _read("scripts/install.sh")
    assert len(re.findall(r"NOPASSWD: ", install.split("cat > /etc/sudoers.d/starlink-monitor", 1)[1].split("\nEOF", 1)[0])) == 4
    root_units = {p.name for p in (ROOT / "systemd").glob("*.service") if not re.search(r"^User=", p.read_text(encoding="utf-8"), re.M)}
    assert root_units == {"starlink-monitor-healthcheck.service", "starlink-wan-failover.service"}
    assert "chmod 600 /etc/starlink-monitor/env" in install and "chmod 700 /var/lib/starlink-monitor" in install
    assert re.search(r'"STARLINK_WEBUI_HOST",\s*"0\.0\.0\.0"', _read("app/config.py"))
    uk, en = _security_section("README.md"), _security_section("README.en.md")
    for word in ("чотири", "healthcheck", "WAN-failover", "0.0.0.0", "600", "700", "403", "DNS-rebinding"):
        assert word in uk, ("README.md", word)
    for word in ("four", "healthcheck", "WAN-failover", "0.0.0.0", "600", "700", "403", "DNS rebinding"):
        assert word in en, ("README.en.md", word)


def _readme_sections(rel):
    return {s.split("\n", 1)[0]: s for s in re.split(r"(?m)^(?=## )", _read(rel)) if s.startswith("## ")}


def _structure(section):
    without_code = re.sub(r"```.*?```", "", section, flags=re.S)
    return {
        "блоків коду": len(re.findall(r"```.*?```", section, re.S)),
        "рядків таблиць": len(re.findall(r"(?m)^\|", without_code)),
        "пунктів списків": len(re.findall(r"(?m)^\s*(?:- |\d+\. )", without_code)),
    }


def test_readme_languages_have_the_same_structure_in_every_section():
    """Блоки коду, рядки таблиць і пункти списків збігаються по розділах. Перепаковка абзацу з двома суміжними
    пунктами списку склеювала їх у один (маркер наступного пункту лишався в кінці речення) - числа в паритет-тесті
    це не показували."""
    uk, en = list(_readme_sections("README.md").values()), list(_readme_sections("README.en.md").values())
    assert len(uk) == len(en)
    different = [(a.split("\n", 1)[0][:40], _structure(a), _structure(b)) for a, b in zip(uk, en, strict=True) if _structure(a) != _structure(b)]
    assert different == [], different


def test_readme_has_no_list_marker_glued_to_the_end_of_a_sentence():
    """" - **Наступний пункт**" усередині рядка - ознака склеєних пунктів списку."""
    for rel in _README_FILES:
        glued = re.findall(r"[^\n]+[.!?)`] - \*\*[^\n]*", re.sub(r"```.*?```", "", _read(rel), flags=re.S))
        assert glued == [], (rel, glued[:2])


# ---- архітектура/індекс/план ↔ код ----

def test_architecture_module_table_lists_every_app_module_and_nothing_else():
    """Таблиця модулів в architecture.md застаріла відносно index.md: не було рядків для i18n, log_redact і services."""
    modules = {p.name for p in (ROOT / "app").glob("*.py") if p.name != "__init__.py"}
    rows = set(re.findall(r"(?m)^\| `(\w+\.py)` \|", _read("docs/architecture.md")))
    rows -= {"starlink_grpc.py"}                  # vendored, згадується окремо під таблицею
    assert rows == modules, f"лише в коді: {sorted(modules - rows)}; лише в таблиці: {sorted(rows - modules)}"


def _doc_telegram_commands():
    """Команди бота, перелічені в рядку таблиці architecture.md, рядку index.md і пункті plan.md."""
    arch = re.search(r"(?m)^\| `telegram_bot.py` \|(.*)\|$", _read("docs/architecture.md")).group(1)
    index = re.search(r"(?m)^\| `telegram_bot.py` \|(.*)\|$", _read("docs/index.md")).group(1)
    plan = re.search(r"- \[x\] Telegram: вихідні сповіщення.*?(?=\n- \[x\])", _read("docs/plan.md"), re.S).group(0)
    return {"architecture.md": arch, "index.md": index, "plan.md": plan}


@pytest.mark.parametrize("doc", ["architecture.md", "index.md", "plan.md"])
def test_documented_telegram_commands_match_the_bot(doc):
    code = set(re.findall(r'"(/[a-z]+)"', _read("app/telegram_bot.py")))
    mentioned = set(re.findall(r"`(/[a-z]+)`", _doc_telegram_commands()[doc]))        # за формою, не за списком відомих: вигадана команда теж ловиться
    assert {c for c in code if c != "/start"} <= mentioned <= code, f"{doc}: у документі {sorted(mentioned)}, у боті {sorted(code)}"


@pytest.mark.parametrize("rel", _README_FILES)
def test_readme_project_tree_lists_every_top_level_entry(rel):
    """Дерево "Структура проєкту" збігається з реальним коренем (без прихованих файлів і самих README). Раніше в ньому
    не було LICENSE, mypy.ini, pytest.ini, ruff.toml, а docs/plan.md не згадувався."""
    text = _read(rel)
    tree = re.search(r"```\nstarlink-monitor/\n(.*?)```", text, re.S).group(1)
    listed = {m.group(1) for m in re.finditer(r"(?m)^[├└]── (\S+?)/?\s", tree)}
    real = {p.name for p in ROOT.iterdir() if not p.name.startswith(".") and not p.name.startswith("README") and p.name != "__pycache__"}
    assert listed == real, f"{rel}: лише в корені {sorted(real - listed)}, лише в дереві {sorted(listed - real)}"
    docs_line = next(line for line in tree.splitlines() if "├── docs/" in line)
    for doc in (p.name for p in (ROOT / "docs").glob("*.md")):
        assert doc in docs_line, (rel, doc)
