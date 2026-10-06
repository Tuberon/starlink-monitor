"""
Відправка сповіщень через Telegram Bot API (прямий HTTP, без важкої
бібліотеки python-telegram-bot). Налаштування - в БД (settings),
керуються з веб-інтерфейсу без перезапуску сервісу.
"""
import logging
import os
import socket
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from app import config, db, i18n
from app.log_redact import redact

logger = logging.getLogger("telegram_notify")

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# DNS-сервери для ручного резолвінгу через eth0.
_DNS_TIMEOUT_SEC = 5  # на кожен резервний DNS-сервер при запиті через eth0
_FALLBACK_DNS_SERVERS = ["8.8.8.8", "1.1.1.1"]
_ETH0_IFACE = b"eth0"


def _bind_to_eth0(sock: socket.socket) -> bool:
    """Форсує вихідний інтерфейс сокета на eth0 через SO_BINDTODEVICE. На відміну від прив'язки до IP
    джерела (source_address), яку Linux ігнорує — інтерфейс обирається за таблицею маршрутизації за
    адресою ПРИЗНАЧЕННЯ, — це єдиний надійний спосіб форсувати інтерфейс із застосунку. Потребує
    CAP_NET_RAW (systemd AmbientCapabilities на starlink-monitor.service) або root; без привілею
    повертає False (не кидає виняток), щоб виклик продовжився без ефекту прив'язки.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, _ETH0_IFACE)
        return True
    except (PermissionError, OSError) as e:
        logger.warning("SO_BINDTODEVICE(eth0) не вдався (потрібен CAP_NET_RAW): %s", e)
        return False


def _get_eth0_ip() -> Optional[str]:
    """IP-адреса eth0 (USB-Ethernet), якщо інтерфейс підключений."""
    try:
        import fcntl
        import struct
        # with: сокет закривається завжди (раніше - лише збиранням сміття
        # при виході з функції, тобто залежно від деталі реалізації CPython)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            return socket.inet_ntoa(fcntl.ioctl(
                s.fileno(), 0x8915, struct.pack("256s", b"eth0"[:15])
            )[20:24])
    except OSError:
        return None


def _resolve_via_eth0(hostname: str) -> Optional[str]:
    """Резолвить hostname у IP явним DNS-запитом (UDP, A-запис) через
    сокет, форсований на eth0 через SO_BINDTODEVICE - не системний
    резолвер (/etc/resolv.conf) і не dnspython's власний сокет (той
    прив'язується лише до IP через параметр source=, що недостатньо -
    див. _bind_to_eth0). Повертає None при будь-якій помилці."""
    try:
        import dns.message
        import dns.query
        import dns.rdatatype
    except ImportError:
        logger.warning("Пакет dnspython не встановлено - ручний DNS через eth0 недоступний")
        return None

    query = dns.message.make_query(hostname, dns.rdatatype.A)
    for dns_server in _FALLBACK_DNS_SERVERS:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _bind_to_eth0(sock)
            sock.settimeout(_DNS_TIMEOUT_SEC)
            response = dns.query.udp(query, dns_server, timeout=_DNS_TIMEOUT_SEC, sock=sock)
            for rrset in response.answer:
                for item in rrset:
                    if item.rdtype == dns.rdatatype.A:
                        return str(item.address)
        except Exception as e:
            logger.warning("Ручний DNS-запит через eth0 до %s не вдався для %s: %s", dns_server, hostname, e)
        finally:
            if sock:
                sock.close()
    return None


class _Eth0BoundAdapter(HTTPAdapter):
    """HTTPAdapter, що форсує вихідні TCP-з'єднання через фізичний
    інтерфейс eth0 (SO_BINDTODEVICE, через urllib3's socket_options -
    застосовується до кожного нового сокета пулу з'єднань автоматично,
    без потреби перевизначати низькорівневий connect())."""
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        import urllib3.connection
        kwargs["socket_options"] = urllib3.connection.HTTPConnection.default_socket_options + [
            (socket.SOL_SOCKET, socket.SO_BINDTODEVICE, _ETH0_IFACE),
        ]
        super().init_poolmanager(*args, **kwargs)


def _request_via_eth0(method: str, url: str, resolved_ip: Optional[str] = None, **kwargs: Any) -> requests.Response:
    """HTTP-запит через eth0 (SO_BINDTODEVICE). Якщо resolved_ip заданий (системний DNS теж недоступний) —
    запит іде напряму на IP з оригінальним hostname у заголовку Host, а TLS перевіряється без
    hostname-matching (`verify=False`: SNI і matching прив'язані до hostname з URL). Свідомий компроміс:
    доставити сповіщення про втрату зв'язку важливіше за суворий matching у вузькому фолбек-вікні (лише
    коли недоступний і системний DNS).
    """
    # Сесія закривається у finally: тіло відповіді вже прочитане
    # (stream=False), тож закриття пулу з'єднань після request() безпечне;
    # раніше нова сесія створювалась на КОЖНЕ резервне надсилання й
    # закривалась лише збиранням сміття.
    session = requests.Session()
    try:
        adapter = _Eth0BoundAdapter()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return _send_via_bound_session(session, method, url, resolved_ip, **kwargs)
    finally:
        session.close()


def _send_via_bound_session(
    session: requests.Session, method: str, url: str, resolved_ip: Optional[str], **kwargs: Any,
) -> requests.Response:
    if not resolved_ip:
        return session.request(method, url, **kwargs)

    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    original_host = parts.hostname
    new_netloc = resolved_ip if not parts.port else f"{resolved_ip}:{parts.port}"
    ip_url = urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))

    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("Host", original_host)
    # TLS-сертифікат перевіряється за іменем хоста з'єднання (тут resolved_ip), а URL з IP дає SNI=IP,
    # що не пройде перевірку сертифіката api.telegram.org; тому підстановка IP у URL безпечна лише з
    # verify=False. Свідомий компроміс: сповіщення про збій зв'язку важливіше за суворий
    # hostname-matching у вузькому фолбек-вікні.
    return session.request(method, ip_url, headers=headers, verify=False, **kwargs)


def _request_with_eth0_fallback(
    method: str, url: str, *, session: Optional[requests.Session] = None, **kwargs: Any,
) -> requests.Response:
    """HTTP-запит у три кроки: звичайний (дефолтний маршрут — зазвичай wlan0/WiFi Starlink); при мережевій
    помилці — через eth0 (SO_BINDTODEVICE); якщо не резолвиться й системний DNS (типово, коли
    супутниковий канал недоступний) — явний DNS-запит через eth0 (_resolve_via_eth0) і запит напряму на
    IP. Без цього сповіщення мовчали б саме тоді, коли вони найпотрібніші.
    """
    # session - лише для основного шляху й лише з одного потоку (цикл
    # getUpdates бота): постійне з'єднання без TLS-рукостискання на
    # кожен запит. Резервний шлях через eth0 - завжди окремий.
    try:
        if session is not None:
            return session.request(method, url, **kwargs)
        return requests.request(method, url, **kwargs)  # noqa: S113 - timeout приходить у **kwargs (передають усі виклики)
    except requests.RequestException as e:
        eth0_ip = _get_eth0_ip()
        if not eth0_ip:
            raise
        logger.info("Дефолтний маршрут недоступний (%s), пробую через eth0", redact(str(e)))

        try:
            return _request_via_eth0(method, url, **kwargs)
        except requests.RequestException as e2:
            hostname = urlparse(url).hostname
            resolved_ip = _resolve_via_eth0(hostname) if hostname else None
            if not resolved_ip:
                raise e2
            logger.info("Системний DNS теж недоступний, резолвлено %s -> %s через eth0", hostname, resolved_ip)
            return _request_via_eth0(method, url, resolved_ip=resolved_ip, **kwargs)


def get_telegram_config() -> tuple[str, list[str], bool]:
    """Повертає (token, chat_id, enabled) з БД. chat_id може містити
    кілька id через кому (сповіщення кільком отримувачам)."""
    token = db.get_setting("telegram_bot_token", "") or ""
    chat_ids_raw = db.get_setting("telegram_chat_ids", "") or ""
    enabled = db.get_setting("telegram_enabled", "0") == "1"
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    return token, chat_ids, enabled


def set_telegram_config(
    token: Optional[str] = None,
    chat_ids: Optional[list[str]] = None,
    enabled: Optional[bool] = None,
) -> None:
    if token is not None:
        db.set_setting("telegram_bot_token", token.strip())
    if chat_ids is not None:
        db.set_setting("telegram_chat_ids", ",".join(str(c).strip() for c in chat_ids if str(c).strip()))
    if enabled is not None:
        db.set_setting("telegram_enabled", "1" if enabled else "0")


def _send_to_all_chats(chat_ids: list[str], method_label: str, make_request: Callable[[str], requests.Response]) -> tuple[bool, str]:
    """Спільна retry/error-handling логіка для send_message()/
    send_document() - раніше продубльована в обох майже ідентично.
    `make_request(chat_id)` виконує РЕАЛЬНИЙ HTTP-запит (json-body для
    sendMessage, multipart-файл для sendDocument) - єдина відмінність
    між викликачами."""
    errors = []
    any_ok = False
    token = get_telegram_config()[0]      # для точного очищення секрету з текстів помилок
    for chat_id in chat_ids:
        last_network_error: Optional[str] = None
        # +1 - перша спроба не рахується "повтором". TELEGRAM_SEND_
        # RETRIES=0 дав би рівно 1 спробу без повторів (стара
        # поведінка); дефолт 1 - одна додаткова спроба при мережевій
        # помилці.
        for attempt in range(config.TELEGRAM_SEND_RETRIES + 1):
            if attempt > 0:
                time.sleep(config.TELEGRAM_SEND_RETRY_DELAY_SEC)
                logger.info("Telegram %s повторна спроба %d для %s", method_label, attempt, chat_id)
            try:
                resp = make_request(chat_id)
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    any_ok = True
                else:
                    err_desc = data.get("description", f"HTTP {resp.status_code}")
                    errors.append(f"{chat_id}: {err_desc}")
                    logger.warning("Telegram %s помилка для %s: %s", method_label, chat_id, err_desc)
                break  # HTTP-рівня відповідь отримана (успіх чи ні) - повтор не допоможе, не пробуємо знову
            except requests.RequestException as e:
                # текст винятку requests містить URL із токеном - очищаємо і для
                # логу, і для повідомлення, яке дашборд показує в браузері
                last_network_error = redact(str(e), token)
                logger.warning("Telegram %s мережева помилка для %s (спроба %d): %s",
                               method_label, chat_id, attempt + 1, last_network_error)
        else:
            # Цикл for завершився БЕЗ break - усі спроби (включно з
            # повторними) дали мережеву помилку, жодної HTTP-відповіді.
            errors.append(f"{chat_id}: {last_network_error}")

    if any_ok and not errors:
        return True, i18n.t("api_sent")
    if any_ok and errors:
        return True, i18n.t("api_sent_partially", errors="; ".join(errors))
    return False, "; ".join(errors) if errors else i18n.t("api_unknown_error")


# Код стану конфігурації -> ключ перекладу повідомлення про нього.
_CONFIG_PROBLEM_KEYS = {"disabled": "tg_cfg_disabled", "no_token": "tg_cfg_no_token", "no_chat_ids": "tg_cfg_no_chat_ids"}


def _problem_code(token: Optional[str], chat_ids: Optional[list[str]], enabled: bool) -> Optional[str]:
    if not enabled:
        return "disabled"
    if not token:
        return "no_token"
    if not chat_ids:
        return "no_chat_ids"
    return None


def config_problem() -> Optional[str]:
    """Чому Telegram зараз не може надсилати - КОД ("disabled"/"no_token"/
    "no_chat_ids"), None якщо налаштовано. Для рішень у коді (напр.
    monitor не логує очікувану ситуацію "Telegram вимкнено"): раніше там
    порівнювались ТЕКСТИ повідомлень, і будь-який переклад їх би зламав."""
    token, chat_ids, enabled = get_telegram_config()
    return _problem_code(token, chat_ids, enabled)


def _validate_telegram_config() -> tuple[Optional[str], Optional[list[str]], Optional[str]]:
    """Спільна перевірка увімкнено/token/chat_ids для send_message()/
    send_document() - раніше продубльована в обох. Повертає (token,
    chat_ids, error) - error непустий рядок, якщо конфігурація
    невалідна (token/chat_ids тоді None, ігноруються викликачем)."""
    token, chat_ids, enabled = get_telegram_config()
    code = _problem_code(token, chat_ids, enabled)
    if code is not None:
        return None, None, i18n.t(_CONFIG_PROBLEM_KEYS[code])
    return token, chat_ids, None


def send_message(text: str) -> tuple[bool, str]:
    """Надсилає text усім налаштованим chat_id. Ніколи не кидає виняток
    назовні - повертає (успіх, повідомлення). Якщо Telegram вимкнено
    або не налаштовано - тихо повертає (False, причина), не заважаючи
    основному циклу моніторингу."""
    token, chat_ids, error = _validate_telegram_config()
    if error:
        return False, error
    assert token is not None and chat_ids is not None  # для mypy - error=None гарантує обидва

    url = API_BASE.format(token=token, method="sendMessage")
    return _send_to_all_chats(chat_ids, "sendMessage", lambda chat_id: _request_with_eth0_fallback(
        "post", url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=config.TELEGRAM_NOTIFY_TIMEOUT_SEC,
    ))


def send_document(file_path: str, caption: str = "") -> tuple[bool, str]:
    """Надсилає файл (напр. backup JSON) усім налаштованим chat_id
    через sendDocument (multipart/form-data, не JSON - на відміну
    від sendMessage). Той самий retry/eth0-fallback підхід, що
    send_message() (через спільний _send_to_all_chats()) - файл
    відкривається ЗАНОВО для кожної спроби й кожного chat_id (file
    handle споживається один раз при завантаженні, повторне
    використання того самого відкритого файла для другого запиту
    дало б порожнє тіло)."""
    token, chat_ids, error = _validate_telegram_config()
    if error:
        return False, error
    assert token is not None and chat_ids is not None
    if not os.path.isfile(file_path):
        return False, i18n.t("api_file_not_found", path=file_path)

    filename = os.path.basename(file_path)
    url = API_BASE.format(token=token, method="sendDocument")

    def _make_request(chat_id: str) -> requests.Response:
        with open(file_path, "rb") as f:
            return _request_with_eth0_fallback(
                "post", url,
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (filename, f)},
                timeout=config.TELEGRAM_NOTIFY_TIMEOUT_SEC,
            )

    return _send_to_all_chats(chat_ids, "sendDocument", _make_request)


def test_connection() -> tuple[bool, str]:
    """Перевіряє валідність bot token через getMe, незалежно від chat_id."""
    token, _, _ = get_telegram_config()
    if not token:
        return False, i18n.t("tg_cfg_no_token")
    try:
        resp = _request_with_eth0_fallback(
            "get",
            API_BASE.format(token=token, method="getMe"),
            timeout=config.TELEGRAM_NOTIFY_TIMEOUT_SEC,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            bot_name = data.get("result", {}).get("username", "?")
            return True, i18n.t("api_bot_ok", name=bot_name)
        return False, data.get("description", f"HTTP {resp.status_code}")
    except requests.RequestException as e:
        return False, redact(str(e), token)
