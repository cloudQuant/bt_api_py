"""SimNow CTP compatibility tests inspired by the backtrader live suite.

These tests focus on scenarios that are safe to run repeatedly:
- live connection / account / position checks
- mock market-data conversion
- order request construction on top of a live Trader session
- multi-environment connection probing

They intentionally avoid submitting a real order to SimNow during CI/local
verification. For order-related coverage we connect to the real Trader front,
but proxy ``ReqOrderInsert`` so the request payload can be validated without
placing a live order.
"""

from __future__ import annotations

import atexit
import os
import queue
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest
from dotenv import load_dotenv

from bt_api_py.bt_api import BtApi
from bt_api_py.containers.ctp.ctp_order import CtpOrderData
from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData
from bt_api_py.ctp_env_selector import apply_ctp_env
from bt_api_py.feeds.live_ctp_feed import (
    CtpMarketStream,
    CtpRequestDataFuture,
    _ctp_field_to_dict,
)

pytestmark = [pytest.mark.integration, pytest.mark.network, pytest.mark.ctp]


SIMNOW_ENVIRONMENTS = {
    "group1": {
        "name": "第一组（生产环境）",
        "td_front": "tcp://180.168.146.187:10130",
        "md_front": "tcp://180.168.146.187:10131",
    },
    "group2": {
        "name": "第二组（生产环境）",
        "td_front": "tcp://180.168.146.187:10131",
        "md_front": "tcp://180.168.146.187:10141",
    },
    "group3": {
        "name": "第三组（生产环境）",
        "td_front": "tcp://180.168.146.187:10132",
        "md_front": "tcp://180.168.146.187:10142",
    },
    "7x24": {
        "name": "7x24 环境",
        "td_front": "tcp://180.168.146.187:10133",
        "md_front": "tcp://180.168.146.187:10143",
    },
    "new_group1": {
        "name": "新第一组（看穿式前置）",
        "td_front": "tcp://182.254.243.31:30001",
        "md_front": "tcp://182.254.243.31:30011",
    },
    "new_group2": {
        "name": "新第二组（看穿式前置）",
        "td_front": "tcp://182.254.243.31:30002",
        "md_front": "tcp://182.254.243.31:30012",
    },
    "new_group3": {
        "name": "新第三组（看穿式前置）",
        "td_front": "tcp://182.254.243.31:30003",
        "md_front": "tcp://182.254.243.31:30013",
    },
    "new_7x24": {
        "name": "新 7x24 环境（看穿式前置）",
        "td_front": "tcp://182.254.243.31:40001",
        "md_front": "tcp://182.254.243.31:40011",
    },
}


load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_CTP_ATEXIT_REGISTERED = False
DEFAULT_INSTRUMENT = os.environ.get("CTP_INSTRUMENT", "SA605")
DEFAULT_EXCHANGE = os.environ.get("CTP_EXCHANGE", "CZCE")


def _ctp_atexit_handler() -> None:
    """Exit directly to avoid SWIG GC cleanup crashes on macOS."""
    os._exit(0)


def _ensure_ctp_atexit() -> None:
    global _CTP_ATEXIT_REGISTERED
    if not _CTP_ATEXIT_REGISTERED:
        atexit.register(_ctp_atexit_handler)
        _CTP_ATEXIT_REGISTERED = True


def get_simnow_credentials() -> tuple[str, str, str, str, str]:
    """Return broker, user, password, app_id and auth_code from env vars."""
    broker_id = os.getenv("CTP_BROKER_ID", "9999")
    user_id = os.getenv("CTP_USER_ID") or os.getenv("SIMNOW_USER_ID") or ""
    password = os.getenv("CTP_PASSWORD") or os.getenv("SIMNOW_PASSWORD") or ""
    app_id = os.getenv("CTP_APP_ID", "simnow_client_test")
    auth_code = os.getenv("CTP_AUTH_CODE", "0000000000000000")
    return broker_id, user_id, password, app_id, auth_code


def _default_env_key() -> str:
    explicit = os.getenv("SIMNOW_ENV", "").strip()
    if explicit:
        return explicit

    ctp_env = os.getenv("CTP_ENV", "").strip().lower()
    if ctp_env == "set1":
        group = os.getenv("CTP_SET1_GROUP", "1").strip()
        return f"new_group{group}"
    if ctp_env == "set2":
        return "new_7x24"
    return "new_7x24"


def create_simnow_config(env_key: str | None = None) -> dict[str, str]:
    """Create a bt_api_py-compatible CTP config for a SimNow environment."""
    broker_id, user_id, password, app_id, auth_code = get_simnow_credentials()
    if not user_id or not password:
        raise RuntimeError("CTP_USER_ID / CTP_PASSWORD not configured")

    if env_key is None:
        explicit = os.getenv("SIMNOW_ENV", "").strip()
        if explicit:
            env_key = explicit
        else:
            td_front, md_front, _env_name = apply_ctp_env()
            return {
                "broker_id": broker_id,
                "user_id": user_id,
                "password": password,
                "app_id": app_id,
                "auth_code": auth_code,
                "td_front": td_front,
                "md_front": md_front,
            }

    if env_key not in SIMNOW_ENVIRONMENTS:
        valid = ", ".join(sorted(SIMNOW_ENVIRONMENTS))
        raise ValueError(f"Invalid SimNow environment: {env_key}. Valid values: {valid}")

    env = SIMNOW_ENVIRONMENTS[env_key]
    return {
        "broker_id": broker_id,
        "user_id": user_id,
        "password": password,
        "app_id": app_id,
        "auth_code": auth_code,
        "td_front": env["td_front"],
        "md_front": env["md_front"],
    }


def _build_mock_tick():
    class MockTick:
        InstrumentID = DEFAULT_INSTRUMENT
        LastPrice = 3550.0
        BidPrice1 = 3549.0
        BidVolume1 = 10
        AskPrice1 = 3551.0
        AskVolume1 = 8
        Volume = 50000
        OpenInterest = 120000.0
        UpperLimitPrice = 3700.0
        LowerLimitPrice = 3400.0
        UpdateTime = "09:30:01"
        UpdateMillisec = 500

    return MockTick()


@contextmanager
def _capture_order_insert(feed: CtpRequestDataFuture):
    """Proxy ReqOrderInsert so tests can validate a live order payload safely."""

    class OrderInsertProxy:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.field_dict = None
            self.request_id = None

        def ReqOrderInsert(self, field, request_id):
            self.field_dict = _ctp_field_to_dict(field)
            self.request_id = request_id
            return 0

        def __getattr__(self, item):
            return getattr(self._wrapped, item)

    original_api = feed.trader_client._api
    proxy = OrderInsertProxy(original_api)
    feed.trader_client._api = proxy
    try:
        yield proxy
    finally:
        feed.trader_client._api = original_api


def _connect_feed(config: dict[str, str]) -> CtpRequestDataFuture:
    _ensure_ctp_atexit()
    feed = CtpRequestDataFuture(queue.Queue(), connect_timeout=20, **config)
    feed.connect()
    assert feed._connected, f"Failed to connect to {config['td_front']}"
    assert feed.trader_client is not None
    assert feed.trader_client.is_ready
    return feed


def _probe_env_connection(env_key: str, env: dict[str, str], creds: tuple[str, str, str, str, str]):
    """Probe one SimNow environment in a subprocess for better CTP isolation."""
    broker_id, user_id, password, app_id, auth_code = creds
    child_env = os.environ.copy()
    child_env.update(
        {
            "BTAPI_SIMNOW_BROKER_ID": broker_id,
            "BTAPI_SIMNOW_USER_ID": user_id,
            "BTAPI_SIMNOW_PASSWORD": password,
            "BTAPI_SIMNOW_APP_ID": app_id,
            "BTAPI_SIMNOW_AUTH_CODE": auth_code,
            "BTAPI_SIMNOW_TD_FRONT": env["td_front"],
        }
    )
    script = """
import os
import sys
from bt_api_py.ctp.client import TraderClient

client = TraderClient(
    os.environ["BTAPI_SIMNOW_TD_FRONT"],
    os.environ["BTAPI_SIMNOW_BROKER_ID"],
    os.environ["BTAPI_SIMNOW_USER_ID"],
    os.environ["BTAPI_SIMNOW_PASSWORD"],
    app_id=os.environ["BTAPI_SIMNOW_APP_ID"],
    auth_code=os.environ["BTAPI_SIMNOW_AUTH_CODE"],
)
client.start(block=False)
ready = client.wait_ready(timeout=6)
print(f"{ready=}", flush=True)
client.stop()
os._exit(0 if ready else 1)
""".strip()
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    details = (result.stdout + result.stderr).strip()
    return env_key, result.returncode == 0, details


@pytest.fixture(scope="module")
def simnow_config() -> dict[str, str]:
    """Return the selected SimNow config for the live tests."""
    config = create_simnow_config()
    print(f"\nSimNow TD: {config['td_front']}")
    print(f"SimNow MD: {config['md_front']}")
    return config


@pytest.fixture(scope="module")
def simnow_api(simnow_config):
    """Create one shared BtApi session for the main SimNow tests."""
    _ensure_ctp_atexit()
    api = BtApi({"CTP___FUTURE": dict(simnow_config)}, debug=True)
    feed = api.get_request_api("CTP___FUTURE")
    assert feed is not None
    feed.connect()
    if not feed._connected:
        pytest.skip(
            f"Skipped (connection failed, likely network): failed to connect to {simnow_config['td_front']}"
        )
    assert feed.trader_client is not None
    assert feed.trader_client.is_ready
    try:
        yield api
    finally:
        with suppress(Exception):
            feed.disconnect()


@pytest.fixture(scope="module")
def simnow_feed(simnow_api) -> CtpRequestDataFuture:
    """Return the shared live CTP request feed."""
    feed = simnow_api.get_request_api("CTP___FUTURE")
    assert feed is not None
    return feed


skip_if_no_creds = pytest.mark.skipif(
    not get_simnow_credentials()[1] or not get_simnow_credentials()[2],
    reason="CTP_USER_ID and CTP_PASSWORD must be configured for SimNow tests",
)


@skip_if_no_creds
def test_simnow_connection(simnow_feed):
    """Test live connection to the currently selected SimNow Trader front."""
    assert simnow_feed._connected
    assert simnow_feed.trader_client.is_ready


@skip_if_no_creds
def test_simnow_market_data(simnow_config):
    """Test CTP tick conversion logic using mock market data."""
    data_queue: queue.Queue = queue.Queue()
    stream = CtpMarketStream(data_queue, **simnow_config)

    stream._on_tick(_build_mock_tick())

    tick = data_queue.get(timeout=1)
    assert isinstance(tick, CtpTickerData)
    tick.init_data()
    assert tick.get_symbol_name() == DEFAULT_INSTRUMENT
    assert tick.get_last_price() == 3550.0
    assert tick.get_bid_price() == 3549.0
    assert tick.get_ask_price() == 3551.0


@skip_if_no_creds
def test_simnow_account_balance(simnow_api):
    """Test live account balance retrieval from SimNow."""
    time.sleep(1)
    account_data = simnow_api.get_account("CTP___FUTURE")
    accounts = account_data.get_data()
    assert accounts, "get_account returned no data"
    account = accounts[0]
    account.init_data()
    assert account.get_margin() >= 0
    assert account.get_available_margin() >= 0


@skip_if_no_creds
def test_simnow_positions(simnow_api):
    """Test live position retrieval from SimNow."""
    time.sleep(1)
    positions_data = simnow_api.get_position("CTP___FUTURE")
    positions = positions_data.get_data()
    assert isinstance(positions, list)
    for position in positions:
        position.init_data()
        assert position.get_position_volume() >= 0


@skip_if_no_creds
def test_simnow_order_placement(simnow_feed, simnow_config):
    """Test order request construction on top of a live SimNow session."""
    time.sleep(1)
    with _capture_order_insert(simnow_feed) as proxy:
        result = simnow_feed.make_order(
            symbol=DEFAULT_INSTRUMENT,
            volume=1,
            price=1.0,
            order_type="buy-limit",
            offset="open",
            exchange_id=DEFAULT_EXCHANGE,
        )

    assert result.get_status() is True
    order = result.get_data()[0]
    assert isinstance(order, CtpOrderData)
    order.init_data()
    assert proxy.field_dict is not None
    assert proxy.field_dict["InstrumentID"] == DEFAULT_INSTRUMENT
    assert proxy.field_dict["ExchangeID"] == DEFAULT_EXCHANGE
    assert proxy.field_dict["UserID"] == simnow_config["user_id"]
    assert order.get_client_order_id() == proxy.field_dict["OrderRef"]
    assert order.front_id == simnow_feed.trader_client._front_id
    assert order.session_id == simnow_feed.trader_client._session_id


@skip_if_no_creds
def test_simnow_full_trading_cycle(simnow_api, simnow_feed):
    """Test connect -> account -> positions -> mock order -> disconnect."""
    account_data = simnow_api.get_account("CTP___FUTURE")
    accounts = account_data.get_data()
    assert accounts, "SimNow account query returned no data"

    time.sleep(1)
    positions_data = simnow_api.get_position("CTP___FUTURE")
    positions = positions_data.get_data()
    assert isinstance(positions, list)

    with _capture_order_insert(simnow_feed) as proxy:
        order_data = simnow_feed.make_order(
            symbol=DEFAULT_INSTRUMENT,
            volume=1,
            price=1.0,
            order_type="buy-limit",
            offset="open",
            exchange_id=DEFAULT_EXCHANGE,
        )

    assert order_data.get_status() is True
    assert proxy.field_dict is not None
    assert proxy.field_dict["OrderRef"]


@skip_if_no_creds
@pytest.mark.slow
def test_all_simnow_environments():
    """Test connection to all known SimNow environments sequentially."""
    creds = get_simnow_credentials()
    success_count = 0
    results = []

    for env_key, env in SIMNOW_ENVIRONMENTS.items():
        env_key, ready, details = _probe_env_connection(env_key, env, creds)
        results.append((env_key, ready, details))
        if ready:
            success_count += 1

    assert success_count > 0, f"No SimNow environment connected successfully: {results}"
