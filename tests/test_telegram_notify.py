"""
Тести для app/telegram_notify.py - перевірка, що config.TELEGRAM_
NOTIFY_TIMEOUT_SEC (раніше hardcoded module-level константа, винесено
в config.py для консистентності з рештою "усе через env" паттерну
проєкту) реально впливає на HTTP-запит, не лише існує як параметр.
"""
from unittest.mock import MagicMock, patch

import pytest

from app import config, db, telegram_notify


def test_send_message_uses_configured_timeout(db_path):
    """Реальна регресійна перевірка: після винесення REQUEST_TIMEOUT
    з hardcoded module-level константи в config.TELEGRAM_NOTIFY_
    TIMEOUT_SEC - значення має РЕАЛЬНО дійти до requests.request(),
    не лише існувати в config.py непов'язано."""
    config.TELEGRAM_NOTIFY_TIMEOUT_SEC = 42
    try:
        db.set_setting("telegram_enabled", "1")
        db.set_setting("telegram_bot_token", "FAKE_TOKEN")
        db.set_setting("telegram_chat_ids", "123")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("requests.request", return_value=mock_response) as mock_req:
            telegram_notify.send_message("тест")
            assert mock_req.call_args.kwargs.get("timeout") == 42
    finally:
        config.TELEGRAM_NOTIFY_TIMEOUT_SEC = 10


# ---- Retry для мережевих помилок (не для HTTP-рівня відповідей) ----

def _setup_telegram(chat_id="123"):
    db.set_setting("telegram_enabled", "1")
    db.set_setting("telegram_bot_token", "FAKE_TOKEN")
    db.set_setting("telegram_chat_ids", chat_id)


def test_network_error_then_success_retries_and_succeeds(db_path):
    """Реальна мета retry: тимчасова мережева помилка (timeout,
    розрив з'єднання) не має губити сповіщення назавжди, якщо
    повторна спроба вдається."""
    _setup_telegram()
    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            import requests
            raise requests.exceptions.Timeout("timeout")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    with patch("requests.request", side_effect=fake_request), patch("time.sleep") as mock_sleep:
        ok, msg = telegram_notify.send_message("тест")

    assert ok is True
    assert call_count[0] == 2
    mock_sleep.assert_called_once()


def test_http_level_error_does_not_retry(db_path):
    """HTTP-рівня відповідь (напр. 'chat not found') - повтор нічого
    не змінить, НЕ має витрачати час на затримку/повторну спробу."""
    _setup_telegram()
    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"ok": False, "description": "chat not found"}
        return resp

    with patch("requests.request", side_effect=fake_request), patch("time.sleep") as mock_sleep:
        ok, msg = telegram_notify.send_message("тест")

    assert ok is False
    assert call_count[0] == 1
    mock_sleep.assert_not_called()


def test_all_retries_exhausted_reports_network_error(db_path):
    """Якщо ВСІ спроби (включно з повторними) дають мережеву помилку -
    результат МАЄ це чесно відобразити, не мовчати."""
    _setup_telegram()
    import requests

    with patch("requests.request", side_effect=requests.exceptions.Timeout("timeout")), \
         patch("time.sleep"):
        ok, msg = telegram_notify.send_message("тест")

    assert ok is False
    assert "timeout" in msg.lower() or "123" in msg


def test_retries_respect_configured_count(db_path):
    """config.TELEGRAM_SEND_RETRIES реально визначає кількість спроб,
    не лише існує як параметр."""
    _setup_telegram()
    config.TELEGRAM_SEND_RETRIES = 3
    import requests
    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        raise requests.exceptions.Timeout("timeout")

    try:
        with patch("requests.request", side_effect=fake_request), patch("time.sleep"):
            telegram_notify.send_message("тест")
        assert call_count[0] == 4, "1 початкова спроба + 3 повтори = 4 виклики"
    finally:
        config.TELEGRAM_SEND_RETRIES = 1


# ---- send_document() ----

def test_send_document_success(db_path, tmp_path):
    _setup_telegram()
    test_file = tmp_path / "backup-1000.json"
    test_file.write_text('{"test": true}')

    from unittest.mock import MagicMock
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    with patch("app.telegram_notify._request_with_eth0_fallback", side_effect=fake_request):
        ok, msg = telegram_notify.send_document(str(test_file), caption="Тестовий backup")

    assert ok is True
    assert "sendDocument" in captured["url"]
    assert captured["data"]["caption"] == "Тестовий backup"
    assert "document" in captured["files"]


def test_send_document_missing_file_returns_false(db_path):
    _setup_telegram()
    ok, msg = telegram_notify.send_document("/nonexistent/backup.json")
    assert ok is False
    assert "не знайдено" in msg


def test_send_document_disabled_does_not_attempt_request(db_path, tmp_path):
    db.set_setting("telegram_enabled", "0")
    test_file = tmp_path / "backup.json"
    test_file.write_text("{}")
    with patch("app.telegram_notify._request_with_eth0_fallback") as mock_req:
        ok, msg = telegram_notify.send_document(str(test_file))
    assert ok is False
    mock_req.assert_not_called()


def test_send_document_reopens_file_for_each_chat_id(db_path, tmp_path):
    """Реальна мета: file handle споживається один раз при
    завантаженні - файл МАЄ відкриватись ЗАНОВО для кожного chat_id,
    інакше другий отримувач отримав би порожній документ."""
    _setup_telegram(chat_id="111,222")
    test_file = tmp_path / "backup.json"
    test_file.write_text('{"data": "test"}')

    from unittest.mock import MagicMock
    file_sizes_seen = []

    def fake_request(method, url, **kwargs):
        f = kwargs["files"]["document"][1]
        file_sizes_seen.append(len(f.read()))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    with patch("app.telegram_notify._request_with_eth0_fallback", side_effect=fake_request):
        telegram_notify.send_document(str(test_file))

    assert file_sizes_seen == [len('{"data": "test"}')] * 2, "обидва chat_id мали отримати ПОВНИЙ файл, не порожній"


# ---- _bind_to_eth0() ----

def test_bind_to_eth0_success():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with patch.object(socket.socket, "setsockopt"):
            assert telegram_notify._bind_to_eth0(sock) is True
    finally:
        sock.close()


def test_bind_to_eth0_permission_error_returns_false():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with patch.object(socket.socket, "setsockopt", side_effect=PermissionError("немає CAP_NET_RAW")):
            assert telegram_notify._bind_to_eth0(sock) is False
    finally:
        sock.close()


def test_bind_to_eth0_os_error_returns_false():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with patch.object(socket.socket, "setsockopt", side_effect=OSError("eth0 не існує")):
            assert telegram_notify._bind_to_eth0(sock) is False
    finally:
        sock.close()


# ---- _get_eth0_ip() ----

def test_get_eth0_ip_success():
    import socket
    fake_response = b"\x00" * 20 + socket.inet_aton("192.168.7.1") + b"\x00" * 8
    with patch("fcntl.ioctl", return_value=fake_response):
        ip = telegram_notify._get_eth0_ip()
    assert ip == "192.168.7.1"


def test_get_eth0_ip_not_connected_returns_none():
    with patch("fcntl.ioctl", side_effect=OSError("eth0 не підключений")):
        assert telegram_notify._get_eth0_ip() is None


# ---- _resolve_via_eth0() ----

def test_resolve_via_eth0_success():
    import dns.rdatatype

    fake_rrset = MagicMock()
    fake_item = MagicMock(rdtype=dns.rdatatype.A, address="149.154.167.220")
    fake_rrset.__iter__ = lambda self: iter([fake_item])
    fake_response = MagicMock(answer=[fake_rrset])

    with patch("app.telegram_notify._bind_to_eth0"), \
         patch("dns.query.udp", return_value=fake_response):
        ip = telegram_notify._resolve_via_eth0("api.telegram.org")
    assert ip == "149.154.167.220"


def test_resolve_via_eth0_all_servers_fail_returns_none():
    with patch("app.telegram_notify._bind_to_eth0"), \
         patch("dns.query.udp", side_effect=OSError("timeout")):
        assert telegram_notify._resolve_via_eth0("api.telegram.org") is None


def test_resolve_via_eth0_dnspython_not_installed_returns_none():
    with patch.dict("sys.modules", {"dns.message": None, "dns.query": None, "dns.rdatatype": None}):
        assert telegram_notify._resolve_via_eth0("api.telegram.org") is None


# ---- _request_via_eth0() ----

def test_request_via_eth0_without_resolved_ip_uses_original_url():
    fake_session = MagicMock()
    with patch("requests.Session", return_value=fake_session):
        telegram_notify._request_via_eth0("get", "https://api.telegram.org/test")
    fake_session.request.assert_called_once_with("get", "https://api.telegram.org/test")


def test_request_via_eth0_with_resolved_ip_substitutes_url_and_sets_host_header():
    fake_session = MagicMock()
    with patch("requests.Session", return_value=fake_session):
        telegram_notify._request_via_eth0(
            "post", "https://api.telegram.org/bot123/sendMessage", resolved_ip="1.2.3.4"
        )
    args, kwargs = fake_session.request.call_args
    assert "1.2.3.4" in args[1]
    assert kwargs["headers"]["Host"] == "api.telegram.org"
    assert kwargs["verify"] is False


# ---- test_connection() ----

def test_connection_success(db_path):
    _setup_telegram()
    with patch("app.telegram_notify._request_with_eth0_fallback") as mock_req:
        mock_req.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"username": "testbot"}})
        ok, msg = telegram_notify.test_connection()
    assert ok is True
    assert "testbot" in msg


def test_connection_invalid_token(db_path):
    _setup_telegram()
    with patch("app.telegram_notify._request_with_eth0_fallback") as mock_req:
        mock_req.return_value = MagicMock(status_code=401, json=lambda: {"ok": False, "description": "Unauthorized"})
        ok, msg = telegram_notify.test_connection()
    assert ok is False
    assert "Unauthorized" in msg


def test_connection_no_token_configured(db_path):
    db.set_setting("telegram_bot_token", "")
    ok, msg = telegram_notify.test_connection()
    assert ok is False
    assert "token" in msg.lower()


def test_connection_network_error(db_path):
    _setup_telegram()
    import requests
    with patch("app.telegram_notify._request_with_eth0_fallback", side_effect=requests.exceptions.ConnectionError("немає мережі")):
        ok, msg = telegram_notify.test_connection()
    assert ok is False


# ---- _request_with_eth0_fallback() - оркестрація всіх 3 рівнів fallback ----

def test_fallback_level1_normal_request_succeeds():
    """Рівень 1: дефолтний маршрут (звичайний WiFi Starlink) працює -
    eth0-логіка взагалі не викликається."""
    fake_response = MagicMock()
    with patch("requests.request", return_value=fake_response) as mock_req, \
         patch("app.telegram_notify._get_eth0_ip") as mock_eth0:
        result = telegram_notify._request_with_eth0_fallback("get", "https://api.telegram.org/test")
    assert result is fake_response
    mock_eth0.assert_not_called()


def test_fallback_level2_eth0_succeeds_when_default_route_fails():
    """Рівень 2: дефолтний маршрут провалюється мережевою помилкою,
    eth0 (SO_BINDTODEVICE, системний DNS ще працює) успішний."""
    import requests
    fake_response = MagicMock()
    with patch("requests.request", side_effect=requests.exceptions.ConnectionError("wlan0 недоступний")), \
         patch("app.telegram_notify._get_eth0_ip", return_value="192.168.7.1"), \
         patch("app.telegram_notify._request_via_eth0", return_value=fake_response) as mock_eth0_req:
        result = telegram_notify._request_with_eth0_fallback("get", "https://api.telegram.org/test")
    assert result is fake_response
    mock_eth0_req.assert_called_once_with("get", "https://api.telegram.org/test")


def test_fallback_level3_manual_dns_succeeds_when_eth0_dns_also_fails():
    """Рівень 3: і дефолтний маршрут, і eth0 з системним DNS
    провалюються (типова картина, коли Starlink повністю недоступний,
    включно з DNS-резолюцією) - явний DNS-запит через eth0, потім
    HTTP напряму на резолвлений IP."""
    import requests
    fake_response = MagicMock()
    with patch("requests.request", side_effect=requests.exceptions.ConnectionError("wlan0 недоступний")), \
         patch("app.telegram_notify._get_eth0_ip", return_value="192.168.7.1"), \
         patch("app.telegram_notify._request_via_eth0", side_effect=[
             requests.exceptions.ConnectionError("системний DNS теж недоступний"),
             fake_response,
         ]) as mock_eth0_req, \
         patch("app.telegram_notify._resolve_via_eth0", return_value="149.154.167.220"):
        result = telegram_notify._request_with_eth0_fallback("get", "https://api.telegram.org/test")
    assert result is fake_response
    assert mock_eth0_req.call_count == 2
    # Другий виклик - напряму на резолвлений IP
    assert mock_eth0_req.call_args[1]["resolved_ip"] == "149.154.167.220"


def test_fallback_no_eth0_available_raises_original_error():
    """Реальний edge case: USB-Ethernet взагалі не підключений (немає
    eth0-fallback інфраструктури) - оригінальна мережева помилка МАЄ
    поширитись, не проковтуватись мовчки."""
    import requests
    original_error = requests.exceptions.ConnectionError("wlan0 недоступний")
    with patch("requests.request", side_effect=original_error), \
         patch("app.telegram_notify._get_eth0_ip", return_value=None):
        with pytest.raises(requests.exceptions.ConnectionError):
            telegram_notify._request_with_eth0_fallback("get", "https://api.telegram.org/test")


def test_fallback_all_levels_fail_raises_last_error():
    """Реальний edge case: eth0 підключений, але і eth0-запит, і
    ручна DNS-резолюція через eth0 провалюються (напр. USB-Ethernet
    підключений, але сам не має інтернету) - МАЄ поширити останню
    (найглибшу) помилку, не оригінальну з рівня 1."""
    import requests
    with patch("requests.request", side_effect=requests.exceptions.ConnectionError("wlan0 недоступний")), \
         patch("app.telegram_notify._get_eth0_ip", return_value="192.168.7.1"), \
         patch("app.telegram_notify._request_via_eth0", side_effect=requests.exceptions.ConnectionError("eth0 теж без інтернету")), \
         patch("app.telegram_notify._resolve_via_eth0", return_value=None):
        with pytest.raises(requests.exceptions.ConnectionError, match="eth0 теж без інтернету"):
            telegram_notify._request_with_eth0_fallback("get", "https://api.telegram.org/test")
