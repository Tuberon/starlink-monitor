"""Вендорний starlink_grpc.py: походження (PROVENANCE) і безпечне оновлення.

Скрипт scripts/fetch_starlink_grpc.sh раніше качав файл ПРЯМО поверх робочого:
обірване з'єднання лишало обрізаний файл -> SyntaxError при імпорті -> монітор
не стартував. Тепер: тимчасовий файл, перевірки, резервна копія, атомарна
заміна. Тести - проти ЛОКАЛЬНОГО HTTP-сервера (мережі немає).
"""
import functools
import hashlib
import http.server
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "app" / "vendor"
SCRIPT = ROOT / "scripts" / "fetch_starlink_grpc.sh"
pytestmark = pytest.mark.skipif(shutil.which("curl") is None or shutil.which("bash") is None, reason="потрібні bash і curl")

C1, C2, PINNED = "a" * 40, "b" * 40, "f" * 40


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _provenance(path=VENDOR / "PROVENANCE"):
    return dict(line.split("=", 1) for line in Path(path).read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#"))


# ---- запис походження ----

def test_provenance_matches_the_vendored_file_byte_for_byte():
    """Правка vendored файлу без оновлення запису (або навпаки) - падає."""
    info = _provenance()
    assert info["SHA256"] == _sha(VENDOR / "starlink_grpc.py")
    assert re.fullmatch(r"[0-9a-f]{40}", info["UPSTREAM_COMMIT"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", info["UPSTREAM_COMMIT_DATE"])
    assert info["UPSTREAM_REPO"].startswith("https://github.com/") and "Unlicense" in info["LICENSE"]


# ---- скрипт оновлення ----

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("upstream")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield directory, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()      # закрити слухаючий сокет (shutdown() лише зупиняє цикл) - ResourceWarning під -W error


class Fetcher:
    def __init__(self, server, tmp_path):
        self.dir, self.port = server
        self.tmp = tmp_path
        self.project = tmp_path / "project"
        (self.project / "app" / "vendor").mkdir(parents=True)
        for name in ("starlink_grpc.py", "PROVENANCE"):
            shutil.copy(VENDOR / name, self.project / "app" / "vendor" / name)
        self.real = (VENDOR / "starlink_grpc.py").read_text(encoding="utf-8")
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir()

    vendor = property(lambda self: self.project / "app" / "vendor")

    def publish(self, commit, content):
        (self.dir / commit).mkdir(exist_ok=True)
        (self.dir / commit / "starlink_grpc.py").write_text(content, encoding="utf-8")

    def feed(self, commit, date="2026-09-29T10:00:00Z", title="feat: щось нове"):
        (self.dir / "feed.atom").write_text(
            f"<feed><entry><id>tag:github.com,2008:Grit::Commit/{commit}</id>"
            f"<updated>{date}</updated><title>\n  {title}\n</title></entry></feed>", encoding="utf-8")

    def run(self, *args):
        env = {**os.environ, "STARLINK_PROJECT_DIR": str(self.project), "STARLINK_GRPC_RAW_BASE": f"http://127.0.0.1:{self.port}",
               "STARLINK_GRPC_FEED_URL": f"http://127.0.0.1:{self.port}/feed.atom", "STARLINK_FETCH_SKIP_ETH0": "1",
               "STARLINK_DISH_ADDR": "127.0.0.1:1", "TMPDIR": str(self.tmp)}
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    @property
    def working_sha(self):
        return _sha(self.vendor / "starlink_grpc.py")

    def leftovers(self):
        return [p.name for p in self.vendor.iterdir() if p.name.startswith(".")] + \
               [p.name for p in self.tmp.glob("starlink-grpc-fetch.*")]


@pytest.fixture
def fetcher(server, tmp_path):
    return Fetcher(server, tmp_path)


def test_successful_update_replaces_file_keeps_backup_and_records_provenance(fetcher):
    new = fetcher.real + "\n# нова версія\n"
    fetcher.publish(C1, new)
    fetcher.feed(C1)
    before = fetcher.working_sha
    result = fetcher.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert fetcher.working_sha == hashlib.sha256(new.encode()).hexdigest()
    assert _sha(fetcher.vendor / "starlink_grpc.py.prev") == before                # можна відкотитись
    info = _provenance(fetcher.vendor / "PROVENANCE")
    assert info["UPSTREAM_COMMIT"] == C1 and info["UPSTREAM_COMMIT_DATE"] == "2026-09-29"
    assert info["UPSTREAM_COMMIT_SUBJECT"] == "feat: щось нове" and info["SHA256"] == fetcher.working_sha
    assert fetcher.leftovers() == []


def test_already_up_to_date_changes_nothing(fetcher):
    fetcher.publish(C1, fetcher.real)
    fetcher.feed(C1)
    provenance_before = (fetcher.vendor / "PROVENANCE").read_text(encoding="utf-8")
    result = fetcher.run()
    assert result.returncode == 0 and "Вже актуально" in result.stdout
    assert not (fetcher.vendor / "starlink_grpc.py.prev").exists()
    assert (fetcher.vendor / "PROVENANCE").read_text(encoding="utf-8") == provenance_before


@pytest.mark.parametrize("label,content,reason", [
    ("обірване завантаження", None, "SyntaxError"),                       # половина файлу
    ("замалий файл", "x = 1\n", "замалий"),
    ("немає get_status", "class ChannelContext:\n    pass\n" + "# pad\n" * 5000, "get_status"),
    ("немає ChannelContext", "def get_status(context=None):\n    pass\n" + "# pad\n" * 5000, "ChannelContext"),
])
def test_bad_download_is_rejected_and_working_file_untouched(fetcher, label, content, reason):
    """Головне: робочий файл лишається як був, тимчасових залишків немає."""
    if content is None:
        content = (fetcher.real + "\n# x\n")[: len(fetcher.real) // 2]
    fetcher.publish(C2, content)
    fetcher.feed(C2)
    before = fetcher.working_sha
    result = fetcher.run()
    assert result.returncode != 0, label
    assert reason in result.stdout + result.stderr and "НЕ змінено" in result.stdout
    assert fetcher.working_sha == before and not (fetcher.vendor / "starlink_grpc.py.prev").exists()
    assert fetcher.leftovers() == []


def test_missing_revision_on_server_keeps_working_file(fetcher):
    fetcher.feed("e" * 40)                                                # ревізії, якої немає (HTTP 404)
    before = fetcher.working_sha
    result = fetcher.run()
    assert result.returncode != 0 and fetcher.working_sha == before and fetcher.leftovers() == []


def test_pinned_commit_ignores_the_feed(fetcher):
    fetcher.publish(PINNED, fetcher.real + "\n# pinned\n")
    fetcher.feed(C1)                                                      # стрічка радить інший коміт
    result = fetcher.run(f"--commit={PINNED}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _provenance(fetcher.vendor / "PROVENANCE")["UPSTREAM_COMMIT"] == PINNED


def test_unavailable_feed_falls_back_to_main_and_records_unknown(fetcher):
    fetcher.publish("main", fetcher.real + "\n# з main\n")               # стрічки немає
    result = fetcher.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert _provenance(fetcher.vendor / "PROVENANCE")["UPSTREAM_COMMIT"] == "unknown"


def test_invalid_commit_argument_is_rejected_before_any_download(fetcher):
    before = fetcher.working_sha
    result = fetcher.run("--commit=не-sha")
    assert result.returncode == 2 and "40 hex" in result.stdout
    assert fetcher.working_sha == before
