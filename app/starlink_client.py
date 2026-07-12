"""
Клієнт для локального gRPC API Starlink dish.

Використовує бібліотеку starlink_grpc з проєкту starlink-grpc-tools
(https://github.com/sparky8512/starlink-grpc-tools) для читання статусу,
і виконує прямий unary gRPC виклик SpaceX.API.Device.Device/Handle
з payload {"reboot": {}} для перезавантаження.

starlink_grpc.py потрібно покласти поруч (app/vendor/starlink_grpc.py) —
дивись scripts/install.sh, який його завантажує з upstream репозиторію.
"""
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

import grpc

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

        try:
            context = starlink_grpc.ChannelContext(target=self.dish_addr)
            status = starlink_grpc.get_status(context)
            # status - namedtuple з полями відповідно до status_field_names()
            # структура залежить від версії бібліотеки, тому дістаємо обережно
            d = status._asdict() if hasattr(status, "_asdict") else dict(status)

            obstruction = d.get("fraction_obstructed") or 0.0
            result = DishStatus(
                timestamp=time.time(),
                online=True,
                state=str(d.get("state", "")),
                uptime_s=int(d.get("uptime", 0) or 0),
                downlink_mbps=round((d.get("downlink_throughput_bps") or 0) / 1e6, 2),
                uplink_mbps=round((d.get("uplink_throughput_bps") or 0) / 1e6, 2),
                ping_latency_ms=round(d.get("pop_ping_latency_ms") or 0.0, 1),
                ping_drop_ratio=round(d.get("pop_ping_drop_rate") or 0.0, 4),
                obstruction_fraction=round(obstruction, 4),
                currently_obstructed=bool(d.get("currently_obstructed", False)),
                software_version=str(d.get("software_version", "")),
                hardware_version=str(d.get("hardware_version", "")),
            )
            context.close()
            return result
        except Exception as e:
            logger.warning("Не вдалося отримати статус dish: %s", e)
            return DishStatus(timestamp=time.time(), online=False, error=str(e))

    def reboot_dish(self) -> tuple[bool, str]:
        """
        Виконує reboot dish через SpaceX.API.Device.Device/Handle з payload {"reboot": {}}.
        Повертає (успіх, повідомлення).
        """
        if starlink_grpc is None:
            return False, "starlink_grpc module missing"

        try:
            context = starlink_grpc.ChannelContext(target=self.dish_addr)
            # starlink_grpc надає низькорівневий доступ до stub-а
            stub = context.get_stub() if hasattr(context, "get_stub") else None
            if stub is None:
                # fallback: власний unary виклик через grpc напряму,
                # використовуючи ті самі згенеровані protobuf-модулі,
                # що й starlink_grpc (device_pb2, device_pb2_grpc)
                from app.vendor.spacex.api.device import device_pb2, device_pb2_grpc

                channel = grpc.insecure_channel(self.dish_addr)
                stub = device_pb2_grpc.DeviceStub(channel)
                request = device_pb2.Request()
                request.reboot.SetInParent()
                stub.Handle(request, timeout=self.timeout)
                channel.close()
            else:
                from app.vendor.spacex.api.device import device_pb2
                request = device_pb2.Request()
                request.reboot.SetInParent()
                stub.Handle(request, timeout=self.timeout)

            logger.info("Reboot dish виконано успішно (%s)", self.dish_addr)
            return True, "reboot command sent"
        except Exception as e:
            logger.error("Помилка reboot dish: %s", e)
            return False, str(e)
