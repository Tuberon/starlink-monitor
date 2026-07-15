"""
Клієнт для локального gRPC API Starlink dish та роутера Starlink Mini.

Starlink Mini складається з ДВОХ логічних пристроїв в одному корпусі
(підтверджено офіційним застосунком Starlink Live та живими викликами):
  - dish (тарілка): 192.168.100.1:9200, id "ut...", hw "mini1_panda_prod2"
  - router (роутер): 192.168.1.1:9000, id "Router-...", hw "v4"
Кожен має ВЛАСНУ версію прошивки, яка оновлюється незалежно.

get_status(): використовує starlink_grpc.get_status() з проєкту
starlink-grpc-tools (https://github.com/sparky8512/starlink-grpc-tools).
Ця функція повертає СИРИЙ protobuf-об'єкт DishGetStatusResponse
(не dict, не namedtuple) — поля читаються напряму через атрибути,
структура підтверджена реальним дампом з живого dish, а поля оновлення
та попереджень - через grpcurl describe напряму на dish користувача:

  device_info { hardware_version, software_version, ... }
  device_state { uptime_s }
  obstruction_stats { fraction_obstructed, ... }
  downlink_throughput_bps, uplink_throughput_bps, pop_ping_latency_ms
  software_update_stats {
    software_update_state: enum SOFTWARE_UPDATE_STATE_UNKNOWN|IDLE|FETCHING|
      PRE_CHECK|WRITING|POST_CHECK|REBOOT_REQUIRED|DISABLED|FAULTED
    software_update_progress: float 0.0-1.0
    update_requires_reboot: bool
    reboot_scheduled_utc_time: int64
  }
  alerts { motors_stuck, thermal_shutdown, thermal_throttle,
    unexpected_location, mast_not_near_vertical, slow_ethernet_speeds,
    roaming, install_pending, is_heating, power_supply_thermal_throttle,
    is_power_save_idle, dbf_telem_stale, low_motor_current,
    lower_signal_than_predicted, slow_ethernet_speeds_100,
    obstruction_map_reset, dish_water_detected, router_water_detected,
    upsu_router_port_slow, no_ethernet_link - усі bool }

get_router_info(): звертається на ОКРЕМУ адресу роутера (192.168.1.1:9000,
не 192.168.100.1:9200 — dish на цю адресу не відповідає своїми даними,
target_id в запиті на адресу dish ігнорується) з payload
{"get_status":{}} (не {"get_device_info":{}} — той дає лише статичну
DeviceInfo без стану оновлення/попереджень):
  grpcurl -plaintext -d '{"get_status":{}}' 192.168.1.1:9000 \
    SpaceX.API.Device.Device/Handle
Повертає WifiGetStatusResponse зі СВОЄЮ окремою схемою, відмінною від
dish (підтверджено grpcurl describe на живому роутері):
  device_info { hardware_version, software_version, ... }
  software_update_stats: WifiSoftwareUpdateStats {
    state: enum WifiSoftwareUpdateState NOT_RUN|GETTING_TARGET_VERSION|
      DOWNLOADING_UPDATE_IMAGE|FLASHING|NO_UPDATE_REQUIRED|REBOOT_PENDING|
      GETTING_TARGET_VERSION_FAILED|GETTING_TARGET_VERSION_EXHAUSTED|
      NO_VALID_ARTIFACT|ILLEGAL_ARTIFACT|DOWNLOADING_UPDATE_IMAGE_FAILED|
      DOWNLOADING_UPDATE_IMAGE_EXHAUSTED|FLASHING_FAILED
    software_download_progress: float
    running_version, version_in_progress: string
  }
  alerts: WifiAlerts { thermal_throttle, install_pending, freshly_fused,
    lan_eth_slow_link_10/100, wan_eth_poor_connection,
    mesh_topology_changing_often, mesh_unreliable_backhaul,
    radius_missing_process, eth_switch_error, poe_on_dish_unreachable,
    poe_fuse_blown, poe_router_overcurrent, poe_off_current_nominal,
    poe_vin_overvoltage, poe_vin_undervoltage, high_cable_ping_drop_rate,
    sandbox_disabled, only_overflight_blocked, offline_networks_disabled,
    wired_mesh_not_using_wan_iface - усі bool }
Використовує grpcurl subprocess + JSON-парсинг (не starlink_grpc, яка
заточена під схему DishGetStatusResponse dish, а не WifiGetStatusResponse
роутера).

reboot_dish(): викликає grpcurl як subprocess замість використання
внутрішніх protobuf-класів starlink_grpc, оскільки:
  1. Формат виклику задокументований і стабільний:
     grpcurl -plaintext -d '{"reboot":{}}' <addr> SpaceX.API.Device.Device/Handle
     (https://github.com/sparky8512/starlink-grpc-tools/wiki/Useful-grpcurl-commands)
  2. Не залежить від генерації protobuf-модулів через fetch_starlink_grpc.sh,
     яка може не спрацювати (grpcurl уже встановлюється в install.sh і потрібен
     для генерації модулів так чи інакше).
Reboot виконується ЛИШЕ через dish_addr (192.168.100.1:9200) - dish і
router фізично один пристрій ("cohoused"), тож reboot dish перезавантажує
обидва логічні компоненти одночасно. Окремого reboot_router() немає.
"""
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict, field
from typing import List

from app import config

logger = logging.getLogger("starlink_client")


def _snake_to_camel(name: str) -> str:
    """grpcurl видає JSON-ключі в camelCase, а grpcurl describe - назви полів
    у snake_case. Конвертація потрібна, щоб мапити ROUTER_ALERT_FIELD_NAMES
    (snake_case, для збереження в БД/показу) на реальні ключі відповіді."""
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])

# Точна відповідність до enum SpaceX.API.Device.SoftwareUpdateState
SOFTWARE_UPDATE_STATE_NAMES = {
    0: "SOFTWARE_UPDATE_STATE_UNKNOWN",
    1: "IDLE",
    2: "FETCHING",
    3: "PRE_CHECK",
    4: "WRITING",
    5: "POST_CHECK",
    6: "REBOOT_REQUIRED",
    7: "DISABLED",
    8: "FAULTED",
}

# Точна відповідність до полів message DishAlerts (номери полів з grpcurl describe)
ALERT_FIELD_NAMES = [
    "motors_stuck",
    "thermal_shutdown",
    "thermal_throttle",
    "unexpected_location",
    "mast_not_near_vertical",
    "slow_ethernet_speeds",
    "roaming",
    "install_pending",
    "is_heating",
    "power_supply_thermal_throttle",
    "is_power_save_idle",
    "dbf_telem_stale",
    "low_motor_current",
    "lower_signal_than_predicted",
    "slow_ethernet_speeds_100",
    "obstruction_map_reset",
    "dish_water_detected",
    "router_water_detected",
    "upsu_router_port_slow",
    "no_ethernet_link",
]

# Точна відповідність до enum SpaceX.API.Device.WifiSoftwareUpdateState (роутер)
ROUTER_UPDATE_STATE_NAMES = {
    0: "NOT_RUN",
    1: "GETTING_TARGET_VERSION",
    2: "DOWNLOADING_UPDATE_IMAGE",
    3: "FLASHING",
    4: "NO_UPDATE_REQUIRED",
    5: "REBOOT_PENDING",
    6: "GETTING_TARGET_VERSION_FAILED",
    7: "GETTING_TARGET_VERSION_EXHAUSTED",
    8: "NO_VALID_ARTIFACT",
    9: "ILLEGAL_ARTIFACT",
    10: "DOWNLOADING_UPDATE_IMAGE_FAILED",
    11: "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED",
    12: "FLASHING_FAILED",
}

# Точна відповідність до полів message WifiAlerts (номери полів з grpcurl describe)
ROUTER_ALERT_FIELD_NAMES = [
    "thermal_throttle",
    "install_pending",
    "freshly_fused",
    "lan_eth_slow_link_10",
    "lan_eth_slow_link_100",
    "wan_eth_poor_connection",
    "mesh_topology_changing_often",
    "mesh_unreliable_backhaul",
    "radius_missing_process",
    "eth_switch_error",
    "poe_on_dish_unreachable",
    "poe_fuse_blown",
    "poe_router_overcurrent",
    "poe_off_current_nominal",
    "poe_vin_overvoltage",
    "poe_vin_undervoltage",
    "high_cable_ping_drop_rate",
    "sandbox_disabled",
    "only_overflight_blocked",
    "offline_networks_disabled",
    "wired_mesh_not_using_wan_iface",
]

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
    software_version: str = ""
    hardware_version: str = ""
    dish_id: str = ""
    error: str = ""
    # Стан оновлення ПЗ dish
    update_state: str = ""
    update_progress_pct: float = 0.0
    update_requires_reboot: bool = False
    update_install_pending: bool = False
    # Попередження dish (активні alert-прапорці)
    active_alerts: List[str] = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["active_alerts"] = json.dumps(d["active_alerts"], ensure_ascii=False)
        return d


@dataclass
class RouterInfo:
    timestamp: float
    online: bool
    software_version: str = ""
    hardware_version: str = ""
    bootcount: int = 0
    error: str = ""
    # Стан оновлення ПЗ роутера (окрема схема WifiSoftwareUpdateStats)
    update_state: str = ""
    update_progress_pct: float = 0.0
    # Попередження роутера (активні WifiAlerts-прапорці)
    active_alerts: List[str] = field(default_factory=list)
    update_install_pending: bool = False
    # Список клієнтів, під'єднаних до WiFi роутера (WifiClient[])
    clients: List[dict] = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["active_alerts"] = json.dumps(d["active_alerts"], ensure_ascii=False)
        d["clients"] = json.dumps(d["clients"], ensure_ascii=False)
        return d


class StarlinkClient:
    def __init__(self, dish_addr: str = None, router_addr: str = None, timeout: float = None):
        self.dish_addr = dish_addr or config.DISH_ADDR
        self.router_addr = router_addr or config.ROUTER_ADDR
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
            dish_id = ""
            if device_info is not None:
                software_version = str(getattr(device_info, "software_version", "") or "")
                hardware_version = str(getattr(device_info, "hardware_version", "") or "")
                dish_id = str(getattr(device_info, "id", "") or "")

            # "стан" dish як єдиний рядок для дашборду: беремо disablement_code,
            # якщо доступний і не "OKAY" - інакше "OKAY"
            disablement = str(getattr(resp, "disablement_code", "") or "")
            state = disablement if disablement and disablement != "OKAY" else "OKAY"

            # Стан оновлення ПЗ: message SoftwareUpdateStats { software_update_state,
            # software_update_progress, update_requires_reboot, reboot_scheduled_utc_time }
            update_state = ""
            update_progress_pct = 0.0
            update_requires_reboot = False
            sw_update_stats = getattr(resp, "software_update_stats", None)
            if sw_update_stats is not None:
                raw_state = getattr(sw_update_stats, "software_update_state", None)
                # protobuf enum може прийти як int (значення) - мапимо через
                # точну таблицю з grpcurl describe; якщо вже рядок - лишаємо як є.
                if isinstance(raw_state, int):
                    update_state = SOFTWARE_UPDATE_STATE_NAMES.get(raw_state, str(raw_state))
                elif raw_state:
                    update_state = str(raw_state)
                progress_raw = getattr(sw_update_stats, "software_update_progress", 0.0) or 0.0
                update_progress_pct = round(progress_raw * 100, 1)
                update_requires_reboot = bool(getattr(sw_update_stats, "update_requires_reboot", False))

            # Попередження: message DishAlerts - набір bool-прапорців (не список).
            # Збираємо назви лише тих, що активні (True).
            active_alerts = []
            alerts_obj = getattr(resp, "alerts", None)
            update_install_pending = False
            if alerts_obj is not None:
                for name in ALERT_FIELD_NAMES:
                    if bool(getattr(alerts_obj, name, False)):
                        active_alerts.append(name)
                update_install_pending = bool(getattr(alerts_obj, "install_pending", False))

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
                dish_id=dish_id,
                update_state=update_state,
                update_progress_pct=update_progress_pct,
                update_requires_reboot=update_requires_reboot,
                update_install_pending=update_install_pending,
                active_alerts=active_alerts,
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

    def get_router_info(self) -> RouterInfo:
        """
        Опитує ОКРЕМИЙ роутерний компонент Starlink Mini (інша адреса,
        ніж dish - див. докстрінг модуля). Ніколи не кидає виняток назовні.
        """
        grpcurl_bin = shutil.which("grpcurl")
        if not grpcurl_bin:
            return RouterInfo(timestamp=time.time(), online=False, error="grpcurl не знайдено в PATH")

        try:
            result = subprocess.run(
                [
                    grpcurl_bin,
                    "-plaintext",
                    "-d", '{"get_status":{}}',
                    self.router_addr,
                    "SpaceX.API.Device.Device/Handle",
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "unknown error").strip()
                return RouterInfo(timestamp=time.time(), online=False, error=err[:500])

            payload = json.loads(result.stdout)
            wifi_status = payload.get("wifiGetStatus", {})
            device_info = wifi_status.get("deviceInfo", {})
            if not device_info:
                return RouterInfo(timestamp=time.time(), online=False, error="empty deviceInfo in response")

            # Стан оновлення ПЗ роутера: WifiSoftwareUpdateStats { state, software_download_progress, ... }
            update_state = ""
            update_progress_pct = 0.0
            sw_stats = wifi_status.get("softwareUpdateStats", {})
            if sw_stats:
                raw_state = sw_stats.get("state")
                if isinstance(raw_state, str) and raw_state.isdigit():
                    raw_state = int(raw_state)
                if isinstance(raw_state, int):
                    update_state = ROUTER_UPDATE_STATE_NAMES.get(raw_state, str(raw_state))
                elif raw_state:
                    update_state = str(raw_state)
                update_progress_pct = round(float(sw_stats.get("softwareDownloadProgress", 0.0) or 0.0) * 100, 1)

            # Попередження: WifiAlerts - набір bool-прапорців (не список)
            active_alerts = []
            update_install_pending = False
            alerts_obj = wifi_status.get("alerts", {})
            if alerts_obj:
                for name in ROUTER_ALERT_FIELD_NAMES:
                    camel = _snake_to_camel(name)
                    if bool(alerts_obj.get(camel, False)):
                        active_alerts.append(name)
                update_install_pending = bool(alerts_obj.get("installPending", False))

            # Клієнти, під'єднані до WiFi роутера (WifiClient[]) - беремо
            # лише поля, потрібні для відображення, ігноруючи детальну
            # телеметрію (fqcodelInfo, rxStats/txStats тощо)
            clients = []
            for c in wifi_status.get("clients", []) or []:
                clients.append({
                    "name": str(c.get("name", "") or c.get("macAddress", "невідомо")),
                    "mac": str(c.get("macAddress", "")),
                    "ip": str(c.get("ipAddress", "")),
                    "iface": str(c.get("iface", "")),
                    "signal": c.get("signalStrength"),
                    "role": str(c.get("role", "")),
                    "connected_s": c.get("associatedTimeS"),
                })

            return RouterInfo(
                timestamp=time.time(),
                online=True,
                software_version=str(device_info.get("softwareVersion", "")),
                hardware_version=str(device_info.get("hardwareVersion", "")),
                bootcount=int(device_info.get("bootcount", 0) or 0),
                update_state=update_state,
                update_progress_pct=update_progress_pct,
                active_alerts=active_alerts,
                update_install_pending=update_install_pending,
                clients=clients,
            )
        except subprocess.TimeoutExpired:
            return RouterInfo(timestamp=time.time(), online=False, error="timeout")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Не вдалося розпарсити відповідь роутера: %s", e)
            return RouterInfo(timestamp=time.time(), online=False, error=f"parse error: {e}")
        except Exception as e:
            logger.warning("Не вдалося отримати статус роутера: %s", e)
            return RouterInfo(timestamp=time.time(), online=False, error=str(e))

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
