"""
CTP Feed 集成测试
使用 SimNow 7x24 环境进行实盘连接测试

运行方式:
    pytest examples/network_tests/test_ctp_feed_network.py -v -s

环境变量 (.env):
    CTP_BROKER_ID, CTP_USER_ID, CTP_PASSWORD, CTP_APP_ID, CTP_AUTH_CODE
    CTP_MD_FRONT, CTP_TD_FRONT, CTP_INSTRUMENT, CTP_EXCHANGE

注意: CTP C++ API 在 macOS 上进程退出时可能产生 segfault (exit code 139)，
      这是 SWIG 对象 GC 清理的已知问题，不影响测试结果。
"""

import atexit
import contextlib
import os
import queue
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from bt_api_py.ctp_env_selector import apply_ctp_env

_CTP_ATEXIT_REGISTERED = False


def _ctp_atexit_handler():
    """在 pytest 完成后强制退出，跳过 SWIG 对象的 GC 清理。"""
    os._exit(0)


def _ensure_ctp_atexit():
    global _CTP_ATEXIT_REGISTERED
    if not _CTP_ATEXIT_REGISTERED:
        atexit.register(_ctp_atexit_handler)
        _CTP_ATEXIT_REGISTERED = True


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_td, _md, _env_name = apply_ctp_env()

BROKER_ID = os.environ.get("CTP_BROKER_ID", "9999")
USER_ID = os.environ.get("CTP_USER_ID", "")
PASSWORD = os.environ.get("CTP_PASSWORD", "")
APP_ID = os.environ.get("CTP_APP_ID", "simnow_client_test")
AUTH_CODE = os.environ.get("CTP_AUTH_CODE", "0000000000000000")
MD_FRONT = _md
TD_FRONT = _td
INSTRUMENT = os.environ.get("CTP_INSTRUMENT", "SA605")
EXCHANGE = os.environ.get("CTP_EXCHANGE", "CZCE")

SKIP_REASON = ""
if not USER_ID or not PASSWORD:
    SKIP_REASON = "CTP_USER_ID and CTP_PASSWORD not set in .env"

skip_if_no_creds = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON)

EXCHANGE_PARAMS = {
    "broker_id": BROKER_ID,
    "user_id": USER_ID,
    "password": PASSWORD,
    "app_id": APP_ID,
    "auth_code": AUTH_CODE,
    "md_front": MD_FRONT,
    "td_front": TD_FRONT,
}


@skip_if_no_creds
@pytest.mark.network
class TestCtpTraderIntegration:
    """TraderApi 集成测试 — 连接、查询账户、查询持仓、下单、Feed层、BtApi层

    所有测试共享一个 TraderClient 连接（通过 CtpRequestDataFuture），
    按声明顺序执行，避免重复创建连接。
    """

    @pytest.fixture(autouse=True, scope="class")
    def setup_feed(self, request):
        """类级别 fixture: 创建一个共享的 CtpRequestDataFuture"""
        _ensure_ctp_atexit()
        from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture

        data_queue = queue.Queue()
        feed = CtpRequestDataFuture(data_queue, **EXCHANGE_PARAMS)
        feed.connect()
        if not feed._connected:
            pytest.skip(f"Skipped (connection failed, likely network): failed to connect to {TD_FRONT}")
        request.cls.feed = feed
        request.cls.data_queue = data_queue
        yield
        with contextlib.suppress(Exception):
            feed.disconnect()

    def test_01_trader_connect(self):
        """验证 TraderClient 连接成功"""
        assert self.feed._connected
        assert self.feed._trader is not None
        assert self.feed._trader.is_ready
        print(f"\n[TD] Connected to {TD_FRONT}")

    def test_02_query_account(self):
        """查询账户资金"""
        result = self.feed.get_account()
        assert result is not None
        data_list = result.get_data()
        assert len(data_list) > 0, "get_account returned empty"

        account = data_list[0]
        account.init_data()
        assert account.get_exchange_name() == "CTP"
        assert account.get_margin() >= 0
        print(
            f"\n[TD] Balance={account.get_margin():.2f}, "
            f"Available={account.get_available_margin():.2f}, "
            f"UnrealizedPnL={account.get_unrealized_profit():.2f}"
        )

    def test_03_query_positions(self):
        """查询持仓"""
        time.sleep(1)
        result = self.feed.get_position()
        assert result is not None
        data_list = result.get_data()
        assert isinstance(data_list, list)
        print(f"\n[TD] Positions: {len(data_list)} records")
        for pos in data_list:
            pos.init_data()
            print(
                f"  {pos.get_symbol_name()} {pos.get_position_direction()} "
                f"{pos.get_position_volume()}手"
            )

    def test_04_make_order(self):
        """下单测试 — 使用极低价格的限价单，验证 API 可调用"""
        time.sleep(1)
        print(f"\n[TD] Sending limit buy-open: {INSTRUMENT} @ 1.0")
        result = self.feed.make_order(
            symbol=INSTRUMENT,
            volume=1,
            price=1.0,
            order_type="buy-limit",
            offset="open",
            exchange_id=EXCHANGE,
        )
        assert result is not None
        print(f"[TD] Order API called, status={result.get_status()}")

    def test_05_feed_via_btapi(self):
        """通过 BtApi 添加 CTP 交易所并查询余额"""
        from bt_api_py.bt_api import BtApi

        exchange_kwargs = {"CTP___FUTURE": dict(EXCHANGE_PARAMS)}
        api = BtApi(exchange_kwargs, debug=True)
        assert "CTP___FUTURE" in api.list_exchanges()

        feed = api.get_request_api("CTP___FUTURE")
        assert feed is not None

        result = feed.get_account()
        data_list = result.get_data()
        assert len(data_list) > 0
        account = data_list[0]
        account.init_data()
        print(f"\n[BtApi] Balance={account.get_margin():.2f}")

        time.sleep(1)

        api.update_total_balance()
        value_dict = api.get_total_value()
        cash_dict = api.get_total_cash()
        print(f"[BtApi] value_dict: {value_dict}")
        print(f"[BtApi] cash_dict: {cash_dict}")
        assert "CTP___FUTURE" in value_dict
        assert "CTP___FUTURE" in cash_dict


@skip_if_no_creds
@pytest.mark.network
class TestCtpMdIntegration:
    """MdApi 集成测试 — 行情连接和 tick 接收

    注意:
    - CTP C++ 在 macOS 上同一进程只允许一个 MdApi 实例存活
      (第二个 CreateFtdcMdApi 会在 Join 时 segfault)
    - 此 class 仅使用 MdApi, 不创建 TraderApi
    - 建议与 TraderApi 测试分开运行:
        pytest examples/network_tests/test_ctp_feed_network.py::TestCtpTraderIntegration -v -s
        pytest examples/network_tests/test_ctp_feed_network.py::TestCtpMdIntegration -v -s
    """

    @pytest.mark.ticker
    def test_md_connect_receive_tick_and_convert(self):
        """通过 MdClient 连接行情，收到原始 tick，并验证 CtpTickerData 转换"""
        _ensure_ctp_atexit()
        from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData
        from bt_api_py.ctp.client import MdClient
        from bt_api_py.feeds.live_ctp_feed import _ctp_field_to_dict

        ticks_received = []

        def on_tick(data):
            ticks_received.append(data)

        client = MdClient(MD_FRONT, BROKER_ID, USER_ID, PASSWORD)
        client.on_tick = on_tick
        client.subscribe([INSTRUMENT])
        client.start(block=False)

        ready = client.wait_ready(timeout=15)
        assert ready, f"MdClient failed to connect to {MD_FRONT}"
        print(f"\n[MD] Connected, waiting for ticks on {INSTRUMENT}...")

        deadline = time.time() + 30
        while time.time() < deadline and len(ticks_received) == 0:
            time.sleep(0.5)

        assert len(ticks_received) > 0, (
            f"No ticks received for {INSTRUMENT} within 30s. "
            f"SimNow 7x24 may be in maintenance window."
        )

        raw_tick = ticks_received[0]
        print(
            f"[MD] Raw tick: {raw_tick.InstrumentID} "
            f"Last={raw_tick.LastPrice} Bid={raw_tick.BidPrice1} Ask={raw_tick.AskPrice1}"
        )
        assert raw_tick.InstrumentID.strip() != ""

        tick_dict = _ctp_field_to_dict(raw_tick)
        assert "InstrumentID" in tick_dict
        assert "LastPrice" in tick_dict
        assert tick_dict["LastPrice"] == raw_tick.LastPrice

        ticker = CtpTickerData(tick_dict)
        ticker.init_data()
        print(
            f"[MD] CtpTickerData: symbol={ticker.get_symbol_name()} "
            f"last={ticker.get_last_price()} "
            f"bid={ticker.get_bid_price()} ask={ticker.get_ask_price()}"
        )
        assert ticker.get_symbol_name().strip() != ""
        assert ticker.get_last_price() == raw_tick.LastPrice

        with contextlib.suppress(Exception):
            client.stop()
