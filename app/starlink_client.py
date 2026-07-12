"""
Клієнт для локального gRPC API Starlink dish.

get_status(): використовує starlink_grpc.get_status() з проєкту
starlink-grpc-tools (https://github.com/sparky8512/starlink-grpc-tools).
Ця функція повертає СИРИЙ protobuf-об'єкт DishGetStatusResponse
(не dict, не namedtuple) — поля читаються напряму через атрибути,
структура підтверджена реальним дампом з живого dish:

  device_info { hardware_version, software_version, ... }
  device_state { uptime_s }
  obstruction_stats { fraction_obstructed, ... }
  downlink_throughput_bps, uplink_throughput_bps, pop_ping_latency_ms

reboot_dish(): викликає grpcurl як subprocess замість використання
внутрішніх protobuf-класів starlink_grpc, оскільки:
  1. Формат виклику задокументований і стабільний:
     grpcurl -plaintext -d '{"reboot":{}}' <addr> SpaceX.API.Device.Device/Handle
     (https://github.com/sparky8512/starlink-grpc-tools/wiki/Useful-grpcurl-commands)
  2. Не залежить від генерації protobuf-модулів через fetch_starlink_grpc.sh,
     яка може не спрацювати (grpcurl уже встановлюється в install.sh і потрібен
     для генерації модулів так чи інакше).
"""
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict
from typing import Optional

from app import config

logger = logging.getLogger("starlink_client")

try:
    from app.vendor import starlink_grpc
except ImportError:
    starlink_grpc = None
    logger.warning(
        "starlink_grpc не знайдено в app/vendor/. "
        "Запустіть scripts/install.sh або scripts/fetch_starlink_grpc.sh"
    )


@dataclass
class DishStatus:
    timestamp: float
    online: bool
    state: str = ""
    uptime_s: int = 0
    downlink_mbps: float = 0.0
    uplink_mbps: float = 0.0
    ping_latency_ms: float = 0.0
    ping_drop_ratio: float = 0.0
    obstruction_fraction: float = 0.0
    currently_obstructed: bool = False
    snr: Optional[float] = None
    software_version: str = ""
    hardware_version: str = ""
    error: str = ""

    def to_dict(self):
        return asdict(self)


class StarlinkClient:
    def __init__(self, dish_addr: str = None, timeout: float = None):
        self.dish_addr = dish_addr or config.DISH_ADDR
        self.timeout = timeout or config.DISH_HTTP_TIMEOUT

    def get_status(self) -> DishStatus:
        """Опитати dish. Ніколи не кидає виняток назовні — помилка кладеться в поле error."""
        if starlink_grpc is None:
            return DishStatus(timestamp=time.time(), online=False, error="starlink_grpc module missing")

        context = None
        try:
            context = starlink_grpc.ChannelContext(target=self.dish_addr)
            resp = starlink_grpc.get_status(context)
            # resp - сирий protobuf DishGetStatusResponse. Поля читаємо напряму
            # (не через dict()/namedtuple - той API нестабільний між версіями).
            device_state = getattr(resp, "device_state", None)
            device_info = getattr(resp, "device_info", None)
            obstruction_stats = getattr(resp, "obstruction_stats", None)

            downlink_bps = getattr(resp, "downlink_throughput_bps", 0.0) or 0.0
            uplink_bps = getattr(resp, "uplink_throughput_bps", 0.0) or 0.0
            ping_latency = getattr(resp, "pop_ping_latency_ms", 0.0) or 0.0
            # pop_ping_drop_rate не завжди присутнє в цій версії протоколу;
            # якщо немає - лишаємо 0 (не є ознакою недоступності dish)
            ping_drop = getattr(resp, "pop_ping_drop_rate", 0.0) or 0.0

            obstruction_fraction = 0.0
            currently_obstructed = False
            if obstruction_stats is not None:
                obstruction_fraction = getattr(obstruction_stats, "fraction_obstructed", 0.0) or 0.0
                currently_obstructed = bool(getattr(obstruction_stats, "currently_obstructed", False))

            uptime_s = 0
            if device_state is not None:
                uptime_s = int(getattr(device_state, "uptime_s", 0) or 0)

            software_version = ""
            hardware_version = ""
            if device_info is not None:
                software_version = str(getattr(device_info, "software_version", "") or "")
                hardware_version = str(getattr(device_info, "hardware_version", "") or "")

            # "стан" dish як єдиний рядок для дашборду: беремо disablement_code,
            # якщо доступний і не "OKAY" - інакше "OKAY"
            disablement = str(getattr(resp, "disablement_code", "") or "")
            state = disablement if disablement and disablement != "OKAY" else "OKAY"

            result = DishStatus(
                timestamp=time.time(),
                online=True,
                state=state,
                uptime_s=uptime_s,
                downlink_mbps=round(downlink_bps / 1e6, 2),
                uplink_mbps=round(uplink_bps / 1e6, 2),
                ping_latency_ms=round(ping_latency, 1),
                ping_drop_ratio=round(ping_drop, 4),
                obstruction_fraction=round(obstruction_fraction, 4),
                currently_obstructed=currently_obstructed,
                software_version=software_version,
                hardware_version=hardware_version,
            )
            return result
        except Exception as e:
            logger.warning("Не вдалося отримати статус dish: %s", e)
            return DishStatus(timestamp=time.time(), online=False, error=str(e))
        finally:
            if context is not None and hasattr(context, "close"):
                try:
                    context.close()
                except Exception:
                    pass

    def reboot_dish(self) -> tuple[bool, str]:
        """
        Виконує reboot dish через SpaceX.API.Device.Device/Handle з payload {"reboot": {}}.
        Використовує grpcurl як subprocess - надійніше за нестабільний внутрішній
        API starlink_grpc, і не потребує окремо згенерованих protobuf-модулів.
        Повертає (успіх, повідомлення).
        """
        grpcurl_bin = shutil.which("grpcurl")
        if not grpcurl_bin:
            return False, "grpcurl не знайдено в PATH (встановіть його через install.sh)"

        try:
            result = subprocess.run(
                [
                    grpcurl_bin,
                    "-plaintext",
                    "-d", '{"reboot":{}}',
                    self.dish_addr,
                    "SpaceX.API.Device.Device/Handle",
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout + 5,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "unknown error").strip()
                logger.error("Помилка reboot dish (grpcurl exit %d): %s", result.returncode, err)
                return False, err[:500]

            logger.info("Reboot dish виконано успішно (%s)", self.dish_addr)
            return True, "reboot command sent"
        except subprocess.TimeoutExpired:
            logger.error("Таймаут виконання reboot dish через grpcurl")
            return False, "timeout"
        except Exception as e:
            logger.error("Помилка reboot dish: %s", e)
            return False, str(e)
