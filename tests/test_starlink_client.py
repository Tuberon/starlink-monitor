"""
Тести для app/starlink_client.py get_status() - найдовша "чиста логіка"
функція проєкту (~100 рядків), яка парсить ~20 полів сирої protobuf-
відповіді через getattr-ланцюжки. Саме тут проєкт уже двічі мав реальні
проблеми (розбіжність версій прошивки, парсинг software_version), і саме
її неможливо було перевірити без фізичного dish - до цих тестів.

Ключова техніка: замість реального gRPC - fake-об'єкт із рівно тими
полями, що має справжня DishGetStatusResponse. Це перевіряє САМЕ логіку
парсингу (getattr-fallback'и, enum-мапінг, конвертацію одиниць), не
мережевий стек.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import starlink_client
from app.starlink_client import DishStatus, StarlinkClient


def make_resp(**overrides):
    """Мінімальна реалістична protobuf-подібна відповідь. SimpleNamespace
    поводиться як protobuf для getattr-доступу, що і використовує
    get_status() (навмисно не dict()/namedtuple - той API нестабільний
    між версіями, як зазначено в коді)."""
    base = dict(
        device_state=SimpleNamespace(uptime_s=12345),
        device_info=SimpleNamespace(
            software_version="2026.07.16.mr82459.1",
            hardware_version="mini1_panda_prod2",
            id="ut51c88d90-02724404-198fc3bd",
        ),
        obstruction_stats=SimpleNamespace(fraction_obstructed=0.0234, currently_obstructed=False),
        downlink_throughput_bps=150_000_000.0,
        uplink_throughput_bps=15_000_000.0,
        pop_ping_latency_ms=42.37,
        pop_ping_drop_rate=0.0012,
        disablement_code="OKAY",
        software_update_stats=SimpleNamespace(
            software_update_state=1,  # IDLE
            software_update_progress=0.0,
            update_requires_reboot=False,
        ),
        alerts=SimpleNamespace(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def client_with_resp():
    """Повертає фабрику: підставляє fake-відповідь замість реального
    gRPC-виклику, повертає розпарсений DishStatus."""
    def _run(resp) -> DishStatus:
        fake_grpc = SimpleNamespace(
            ChannelContext=lambda target: SimpleNamespace(close=lambda: None),
            get_status=lambda ctx: resp,
        )
        with patch.object(starlink_client, "starlink_grpc", fake_grpc):
            return StarlinkClient().get_status()
    return _run


# ---- Базовий парсинг і конвертація одиниць ----

def test_parses_full_valid_response(client_with_resp):
    s = client_with_resp(make_resp())
    assert s.online is True
    assert s.error == ""
    assert s.uptime_s == 12345
    assert s.software_version == "2026.07.16.mr82459.1"
    assert s.hardware_version == "mini1_panda_prod2"
    assert s.dish_id == "ut51c88d90-02724404-198fc3bd"


def test_converts_bps_to_mbps(client_with_resp):
    """bps -> Mbps (÷1e6), не Mib/s (÷2^20) - помилка тут дала б
    ~5% розбіжність з офіційним застосунком, важко помітну на око."""
    s = client_with_resp(make_resp(
        downlink_throughput_bps=150_000_000.0,
        uplink_throughput_bps=15_000_000.0,
    ))
    assert s.downlink_mbps == 150.0
    assert s.uplink_mbps == 15.0


def test_rounds_values_to_expected_precision(client_with_resp):
    s = client_with_resp(make_resp())
    assert s.ping_latency_ms == 42.4       # 1 знак
    assert s.ping_drop_ratio == 0.0012     # 4 знаки
    assert s.obstruction_fraction == 0.0234  # 4 знаки


# ---- getattr-fallback'и: відсутні поля НЕ мають ламати парсинг ----

def test_missing_optional_fields_do_not_crash(client_with_resp):
    """Реальний сценарій: різні версії прошивки dish віддають різний
    набір полів. Відсутність необов'язкових полів МАЄ давати нулі,
    а не виняток і не online=False."""
    s = client_with_resp(SimpleNamespace())  # взагалі порожня відповідь
    assert s.online is True, "порожня відповідь - це НЕ ознака offline"
    assert s.uptime_s == 0
    assert s.downlink_mbps == 0.0
    assert s.software_version == ""
    assert s.active_alerts == []


def test_missing_ping_drop_rate_is_not_offline_signal(client_with_resp):
    """Явно задокументовано в коді: pop_ping_drop_rate не завжди
    присутнє в цій версії протоколу - його відсутність не є ознакою
    недоступності dish."""
    resp = make_resp()
    del resp.pop_ping_drop_rate
    s = client_with_resp(resp)
    assert s.online is True
    assert s.ping_drop_ratio == 0.0


def test_none_values_treated_as_zero(client_with_resp):
    """protobuf може віддати None замість числа - `or 0.0` fallback."""
    s = client_with_resp(make_resp(
        downlink_throughput_bps=None,
        pop_ping_latency_ms=None,
    ))
    assert s.downlink_mbps == 0.0
    assert s.ping_latency_ms == 0.0


def test_missing_device_info_gives_empty_strings_not_none(client_with_resp):
    """None у version-полях зламав би порівняння версій (db.is_older_
    version очікує str) - тому саме порожні рядки, не None."""
    s = client_with_resp(make_resp(device_info=None))
    assert s.software_version == ""
    assert s.hardware_version == ""
    assert s.dish_id == ""


# ---- enum-мапінг update_state (реальне джерело минулих проблем) ----

@pytest.mark.parametrize("raw,expected", [
    (1, "IDLE"),
    (2, "FETCHING"),
    (6, "REBOOT_REQUIRED"),
    (8, "FAULTED"),
])
def test_maps_int_enum_to_state_name(client_with_resp, raw, expected):
    """protobuf enum приходить як int - мапиться через точну таблицю
    з grpcurl describe. Помилка тут зламала б auto-reboot логіку
    (яка спрацьовує саме на REBOOT_REQUIRED)."""
    s = client_with_resp(make_resp(software_update_stats=SimpleNamespace(
        software_update_state=raw, software_update_progress=0.0, update_requires_reboot=False,
    )))
    assert s.update_state == expected


def test_unknown_enum_int_falls_back_to_string(client_with_resp):
    """Нове, невідоме значення enum (майбутня версія прошивки) НЕ має
    ламати парсинг - показуємо сире число як рядок."""
    s = client_with_resp(make_resp(software_update_stats=SimpleNamespace(
        software_update_state=99, software_update_progress=0.0, update_requires_reboot=False,
    )))
    assert s.update_state == "99"


def test_string_enum_passed_through_unchanged(client_with_resp):
    """Якщо версія протоколу вже віддає рядок - лишаємо як є."""
    s = client_with_resp(make_resp(software_update_stats=SimpleNamespace(
        software_update_state="REBOOT_REQUIRED", software_update_progress=0.0, update_requires_reboot=False,
    )))
    assert s.update_state == "REBOOT_REQUIRED"


def test_update_progress_converted_to_percent(client_with_resp):
    """0.0-1.0 fraction -> 0-100 відсотків."""
    s = client_with_resp(make_resp(software_update_stats=SimpleNamespace(
        software_update_state=4, software_update_progress=0.457, update_requires_reboot=False,
    )))
    assert s.update_progress_pct == 45.7


# ---- alerts: bool-прапорці, не список ----

def test_collects_only_active_alerts(client_with_resp):
    """DishAlerts - набір bool-полів (не список). Збираємо назви
    ЛИШЕ активних."""
    s = client_with_resp(make_resp(alerts=SimpleNamespace(
        motors_stuck=True,
        thermal_throttle=False,
        roaming=True,
        install_pending=False,
    )))
    assert set(s.active_alerts) == {"motors_stuck", "roaming"}


def test_install_pending_extracted_separately(client_with_resp):
    """install_pending читається окремо (не лише в active_alerts) -
    від нього залежить auto-reboot-при-готовому-оновленні."""
    s = client_with_resp(make_resp(alerts=SimpleNamespace(install_pending=True)))
    assert s.update_install_pending is True


def test_no_alerts_object_gives_empty_list(client_with_resp):
    s = client_with_resp(make_resp(alerts=None))
    assert s.active_alerts == []
    assert s.update_install_pending is False


# ---- disablement_code -> state ----

def test_okay_disablement_gives_okay_state(client_with_resp):
    s = client_with_resp(make_resp(disablement_code="OKAY"))
    assert s.state == "OKAY"


def test_real_disablement_code_becomes_state(client_with_resp):
    """Реальний сценарій, який користувач бачив у сторонньому
    застосунку: ACCOUNT_DISABLED."""
    s = client_with_resp(make_resp(disablement_code="ACCOUNT_DISABLED"))
    assert s.state == "ACCOUNT_DISABLED"


def test_empty_disablement_gives_okay_state(client_with_resp):
    s = client_with_resp(make_resp(disablement_code=""))
    assert s.state == "OKAY"


# ---- Обробка помилок ----

def test_grpc_exception_returns_offline_with_error(client_with_resp):
    """get_status() НІКОЛИ не кидає виняток назовні (документовано) -
    помилка кладеться в поле error, watchdog далі сам вирішує, що
    робити з offline-станом."""
    fake_grpc = SimpleNamespace(
        ChannelContext=lambda target: SimpleNamespace(close=lambda: None),
        get_status=lambda ctx: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )
    with patch.object(starlink_client, "starlink_grpc", fake_grpc):
        s = StarlinkClient().get_status()

    assert s.online is False
    assert "connection refused" in s.error


def test_missing_grpc_module_returns_offline(client_with_resp):
    with patch.object(starlink_client, "starlink_grpc", None):
        s = StarlinkClient().get_status()
    assert s.online is False
    assert "missing" in s.error


def test_channel_closed_even_on_exception():
    """Витік gRPC-каналів при кожній невдалій спробі (кожні ~10с)
    вичерпав би ресурси Pi Zero 2W за години - finally-блок МАЄ
    закривати канал навіть при винятку."""
    closed = []
    fake_grpc = SimpleNamespace(
        ChannelContext=lambda target: SimpleNamespace(close=lambda: closed.append(True)),
        get_status=lambda ctx: (_ for _ in ()).throw(RuntimeError("боом")),
    )
    with patch.object(starlink_client, "starlink_grpc", fake_grpc):
        StarlinkClient().get_status()

    assert closed == [True], "канал МАЄ закриватись навіть при винятку"


# ---- to_dict(): серіалізація для БД ----

def test_to_dict_serializes_alerts_as_json(client_with_resp):
    """active_alerts - список, а SQLite не має типу list: серіалізація
    в JSON-рядок МАЄ відбуватись саме тут, інакше db.insert_metric()
    отримав би непідтримуваний тип."""
    s = client_with_resp(make_resp(alerts=SimpleNamespace(motors_stuck=True, roaming=True)))
    d = s.to_dict()
    assert isinstance(d["active_alerts"], str)
    assert "motors_stuck" in d["active_alerts"]
