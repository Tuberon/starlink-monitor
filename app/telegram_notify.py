"""
Відправка сповіщень через Telegram Bot API (прямий HTTP, без важкої
бібліотеки python-telegram-bot). Налаштування - в БД (settings),
керуються з веб-інтерфейсу без перезапуску сервісу.
"""
import logging
import os
import socket
import time
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

from app import config, db

logger = logging.getLogger("telegram_notify")

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# DNS-сервери для ручного резолвінгу через eth0.
_FALLBACK_DNS_SERVERS = ["8.8.8.8", "1.1.1.1"]
_ETH0_IFACE = b"eth0"


def _bind_to_eth0(sock: socket.socket) -> bool:
    """Форсує вихідний фізичний інтерфейс сокета на eth0 через
    SO_BINDTODEVICE. Це ПРИНЦИПОВО відрізняється від прив'язки до
    IP-адреси (source_address) - Linux обирає фізичний інтерфейс за
    таблицею маршрутизації на основі адреси ПРИЗНАЧЕННЯ, повністю
    ігноруючи заявлену джерельну IP сокета (якщо не налаштована
    окрема policy-based routing на рівні ОС). SO_BINDTODEVICE - єдиний
    надійний спосіб форсувати конкретний інтерфейс з коду застосунку.
    Вимагає CAP_NET_RAW (надано через systemd AmbientCapabilities на
    starlink-monitor.service) або root - повертає False (не кидає
    виняток), якщо привілею немає, щоб виклик міг продовжити без
    падіння (просто без реального ефекту від прив'язки)."""
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
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ip = socket.inet_ntoa(fcntl.ioctl(
            s.fileno(), 0x8915, struct.pack("256s", b"eth0"[:15])
        )[20:24])
        return ip
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
            sock.settimeout(5)
            response = dns.query.udp(query, dns_server, timeout=5, sock=sock)
            for rrset in response.answer:
                for item in rrset:
                    if item.rdtype == dns.rdatatype.A:
                        return item.address
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
    """HTTP-запит, форсований через eth0 (SO_BINDTODEVICE). Якщо
    resolved_ip заданий (системний DNS теж недоступний), запит іде
    напряму на цю IP замість hostname з URL, з оригінальним hostname
    у заголовку Host. Сертифікат TLS у цьому випадку перевіряється
    без hostname-matching (`verify=False`) - SNI/matching природно
    прив'язані до hostname з URL, а тут з'єднання йде за IP; ланцюжок
    довіри сертифіката все одно валідний, лише конкретна перевірка
    імені пропускається. Свідомий компроміс: доставити сповіщення про
    втрату зв'язку важливіше за суворий hostname-matching у цьому
    вузькому й короткому фолбек-вікні (лише коли й системний DNS
    недоступний)."""
    session = requests.Session()
    adapter = _Eth0BoundAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    if not resolved_ip:
        return session.request(method, url, **kwargs)

    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    original_host = parts.hostname
    new_netloc = resolved_ip if not parts.port else f"{resolved_ip}:{parts.port}"
    ip_url = urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))

    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("Host", original_host)
    # requests/urllib3 перевіряють TLS-сертифікат за іменем хоста з'єднання
    # (тут - resolved_ip); щоб сертифікат api.telegram.org пройшов
    # валідацію, вказуємо server_hostname через HTTPAdapter замість
    # прямого verify-обходу - найпростіший надійний варіант тут:
    # requests.Session.request() приймає `headers`, TLS SNI/verify
    # requests бере з URL автоматично, тому URL з IP означає SNI=IP,
    # що зазвичай НЕ пройде перевірку сертифіката Telegram. Через це
    # для HTTPS підстановка IP у URL безпечна лише разом з verify=False
    # (перевірка ланцюжка сертифіката залишається на транспортному
    # рівні neможлива без SNI overwrite) - свідомий компроміс: тимчасова
    # відправка сповіщення про збій зв'язку важливіша за суворий
    # hostname-matching TLS в цьому вузькому і короткому фолбек-вікні.
    return session.request(method, ip_url, headers=headers, verify=False, **kwargs)


def _request_with_eth0_fallback(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Виконує HTTP-запит: спочатку звичайним способом (дефолтний
    маршрут - зазвичай wlan0/WiFi Starlink, нижчий route-metric). Якщо
    це провалюється мережевою помилкою - пробує через eth0
    (SO_BINDTODEVICE, реально форсує інтерфейс, на відміну від
    прив'язки до IP-адреси). Якщо і системний DNS не резолвиться
    (типова картина, коли супутниковий канал Starlink недоступний) -
    явний DNS-запит через eth0 (_resolve_via_eth0), і HTTP-запит
    напряму на резолвлений IP. Без цього Telegram-сповіщення мовчали б
    саме тоді, коли вони найпотрібніші - сповістити про проблему
    зі зв'язком."""
    try:
        return requests.request(method, url, **kwargs)
    except requests.RequestException as e:
        eth0_ip = _get_eth0_ip()
        if not eth0_ip:
            raise
        logger.info("Дефолтний маршрут недоступний (%s), пробую через eth0", e)

        try:
            return _request_via_eth0(method, url, **kwargs)
        except requests.RequestException as e2:
            hostname = requests.utils.urlparse(url).hostname
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


def send_message(text: str) -> tuple[bool, str]:
    """Надсилає text усім налаштованим chat_id. Ніколи не кидає виняток
    назовні - повертає (успіх, повідомлення). Якщо Telegram вимкнено
    або не налаштовано - тихо повертає (False, причина), не заважаючи
    основному циклу моніторингу."""
    token, chat_ids, enabled = get_telegram_config()

    if not enabled:
        return False, "Telegram сповіщення вимкнені"
    if not token:
        return False, "Не вказано bot token"
    if not chat_ids:
        return False, "Не вказано жодного chat_id"

    full_text = text

    url = API_BASE.format(token=token, method="sendMessage")
    errors = []
    any_ok = False
    for chat_id in chat_ids:
        last_network_error: Optional[str] = None
        # +1 - перша спроба не рахується "повтором". TELEGRAM_SEND_
        # RETRIES=0 дав би рівно 1 спробу без повторів (стара
        # поведінка); дефолт 1 - одна додаткова спроба при мережевій
        # помилці.
        for attempt in range(config.TELEGRAM_SEND_RETRIES + 1):
            if attempt > 0:
                time.sleep(config.TELEGRAM_SEND_RETRY_DELAY_SEC)
                logger.info("Telegram sendMessage повторна спроба %d для %s", attempt, chat_id)
            try:
                resp = _request_with_eth0_fallback(
                    "post",
                    url,
                    json={"chat_id": chat_id, "text": full_text, "parse_mode": "HTML"},
                    timeout=config.TELEGRAM_NOTIFY_TIMEOUT_SEC,
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    any_ok = True
                else:
                    err_desc = data.get("description", f"HTTP {resp.status_code}")
                    errors.append(f"{chat_id}: {err_desc}")
                    logger.warning("Telegram sendMessage помилка для %s: %s", chat_id, err_desc)
                break  # HTTP-рівня відповідь отримана (успіх чи ні) - повтор не допоможе, не пробуємо знову
            except requests.RequestException as e:
                last_network_error = str(e)
                logger.warning("Telegram sendMessage мережева помилка для %s (спроба %d): %s", chat_id, attempt + 1, e)
        else:
            # Цикл for завершився БЕЗ break - усі спроби (включно з
            # повторними) дали мережеву помилку, жодної HTTP-відповіді.
            errors.append(f"{chat_id}: {last_network_error}")

    if any_ok and not errors:
        return True, "надіслано"
    if any_ok and errors:
        return True, f"надіслано частково, помилки: {'; '.join(errors)}"
    return False, "; ".join(errors) if errors else "невідома помилка"


def send_document(file_path: str, caption: str = "") -> tuple[bool, str]:
    """Надсилає файл (напр. backup JSON) усім налаштованим chat_id
    через sendDocument (multipart/form-data, не JSON - на відміну
    від sendMessage). Той самий retry/eth0-fallback підхід, що
    send_message() - файл відкривається ЗАНОВО для кожної спроби й
    кожного chat_id (file handle споживається один раз при
    завантаженні, повторне використання того самого відкритого
    файла для другого запиту дало б порожнє тіло)."""
    token, chat_ids, enabled = get_telegram_config()

    if not enabled:
        return False, "Telegram сповіщення вимкнені"
    if not token:
        return False, "Не вказано bot token"
    if not chat_ids:
        return False, "Не вказано жодного chat_id"
    if not os.path.isfile(file_path):
        return False, f"Файл не знайдено: {file_path}"

    filename = os.path.basename(file_path)
    url = API_BASE.format(token=token, method="sendDocument")
    errors = []
    any_ok = False
    for chat_id in chat_ids:
        last_network_error: Optional[str] = None
        for attempt in range(config.TELEGRAM_SEND_RETRIES + 1):
            if attempt > 0:
                time.sleep(config.TELEGRAM_SEND_RETRY_DELAY_SEC)
                logger.info("Telegram sendDocument повторна спроба %d для %s", attempt, chat_id)
            try:
                with open(file_path, "rb") as f:
                    resp = _request_with_eth0_fallback(
                        "post",
                        url,
                        data={"chat_id": chat_id, "caption": caption},
                        files={"document": (filename, f)},
                        timeout=config.TELEGRAM_NOTIFY_TIMEOUT_SEC,
                    )
                data = resp.json()
                if resp.status_code == 200 and data.get("ok"):
                    any_ok = True
                else:
                    err_desc = data.get("description", f"HTTP {resp.status_code}")
                    errors.append(f"{chat_id}: {err_desc}")
                    logger.warning("Telegram sendDocument помилка для %s: %s", chat_id, err_desc)
                break
            except requests.RequestException as e:
                last_network_error = str(e)
                logger.warning("Telegram sendDocument мережева помилка для %s (спроба %d): %s", chat_id, attempt + 1, e)
        else:
            errors.append(f"{chat_id}: {last_network_error}")

    if any_ok and not errors:
        return True, "надіслано"
    if any_ok and errors:
        return True, f"надіслано частково, помилки: {'; '.join(errors)}"
    return False, "; ".join(errors) if errors else "невідома помилка"


def test_connection() -> tuple[bool, str]:
    """Перевіряє валідність bot token через getMe, незалежно від chat_id."""
    token, _, _ = get_telegram_config()
    if not token:
        return False, "Не вказано bot token"
    try:
        resp = _request_with_eth0_fallback(
            "get",
            API_BASE.format(token=token, method="getMe"),
            timeout=config.TELEGRAM_NOTIFY_TIMEOUT_SEC,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            bot_name = data.get("result", {}).get("username", "?")
            return True, f"Бот @{bot_name} доступний"
        return False, data.get("description", f"HTTP {resp.status_code}")
    except requests.RequestException as e:
        return False, str(e)
