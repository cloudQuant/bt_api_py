import pytest

pytest.skip("Moved offline tests to tests/test_ctp_feed.py; network tests are in test_ctp_feed_network.py", allow_module_level=True)


# ========================================================================
#  1. 单元测试 — 不需要网络连接
# ========================================================================


class TestCtpImports:
    """测试 CTP 子模块导入"""

    def test_internal_ctp_module_import(self):
        """验证 bt_api_py.ctp 子模块可以正常导入"""
        from bt_api_py.ctp import CThostFtdcMdApi, CThostFtdcTraderApi

        assert CThostFtdcMdApi is not None
        assert CThostFtdcTraderApi is not None

    def test_ctp_client_import(self):
        """验证 MdClient / TraderClient 可以正常导入"""
        from bt_api_py.ctp.client import MdClient, TraderClient

        assert MdClient is not None
        assert TraderClient is not None

    def test_ctp_feed_import(self):
        """验证 CTP Feed 类可以正常导入"""
        from bt_api_py.feeds.live_ctp_feed import (
            CTP_DIRECTION_FLAG,
            CTP_OFFSET_FLAG,
        )

        assert CTP_OFFSET_FLAG["open"] == "0"
        assert CTP_OFFSET_FLAG["close_today"] == "3"
        assert CTP_DIRECTION_FLAG["buy"] == "0"
        assert CTP_DIRECTION_FLAG["sell"] == "1"

    def test_ctp_containers_import(self):
        """验证 CTP 容器类可以正常导入"""
        from bt_api_py.containers.ctp import (
            CtpAccountData,
        )

        assert CtpAccountData is not None

    def test_ctp_registry(self):
        """验证 CTP 在 ExchangeRegistry 中已注册"""
        import bt_api_py.exchange_registers.register_ctp  # noqa: F401
        from bt_api_py.registry import ExchangeRegistry

        assert ExchangeRegistry.has_exchange("CTP___FUTURE")
        assert ExchangeRegistry.get_balance_handler("CTP___FUTURE") is not None
        assert ExchangeRegistry.get_stream_class("CTP___FUTURE", "subscribe") is not None

    def test_btapi_includes_ctp(self):
        """验证 BtApi 可用交易所列表包含 CTP"""
        from bt_api_py.bt_api import BtApi

        available = BtApi.list_available_exchanges()
        assert "CTP___FUTURE" in available

    def test_split_submodule_imports(self):
        """验证拆分后的子模块可以独立导入"""
        from bt_api_py.ctp.ctp_constants import THOST_TERT_RESTART
        from bt_api_py.ctp.ctp_md_api import CThostFtdcMdApi
        from bt_api_py.ctp.ctp_trader_api import CThostFtdcTraderSpi

        assert CThostFtdcMdApi is not None
        assert CThostFtdcTraderSpi is not None
        assert THOST_TERT_RESTART is not None

    def test_split_submodule_backward_compat(self):
        """验证拆分子模块与原始包导入返回相同对象 (向后兼容)"""
        from bt_api_py.ctp import CThostFtdcMdApi as CThostFtdcMdApiInit
        from bt_api_py.ctp.ctp_md_api import CThostFtdcMdApi as CThostFtdcMdApiSubmod

        assert CThostFtdcMdApiInit is CThostFtdcMdApiSubmod

        from bt_api_py.ctp import CThostFtdcInputOrderField as CThostFtdcInputOrderFieldInit
        from bt_api_py.ctp.ctp_structs_order import (
            CThostFtdcInputOrderField as CThostFtdcInputOrderFieldSubmod,
        )

        assert CThostFtdcInputOrderFieldInit is CThostFtdcInputOrderFieldSubmod

    def test_split_submodule_field_instantiation(self):
        """验证通过拆分子模块导入的 Field 可正常实例化和赋值"""
        from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderField

        field = CThostFtdcInputOrderField()
        field.InstrumentID = "IF2506"
        field.VolumeTotalOriginal = 1
        assert field.InstrumentID == "IF2506"
        assert field.VolumeTotalOriginal == 1

    def test_split_submodule_all_modules_importable(self):
        """验证所有 12 个拆分子模块都可以导入"""
        import importlib

        modules = [
            "bt_api_py.ctp.ctp_constants",
            "bt_api_py.ctp.ctp_md_api",
            "bt_api_py.ctp.ctp_trader_api",
            "bt_api_py.ctp.ctp_structs_common",
            "bt_api_py.ctp.ctp_structs_order",
            "bt_api_py.ctp.ctp_structs_trade",
            "bt_api_py.ctp.ctp_structs_position",
            "bt_api_py.ctp.ctp_structs_account",
            "bt_api_py.ctp.ctp_structs_market",
            "bt_api_py.ctp.ctp_structs_query",
            "bt_api_py.ctp.ctp_structs_transfer",
            "bt_api_py.ctp.ctp_structs_risk",
        ]
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            assert hasattr(mod, "__all__"), f"{mod_name} missing __all__"
            assert len(mod.__all__) > 0, f"{mod_name} has empty __all__"


class TestCtpContainerParsing:
    """测试 CTP 容器类数据解析（纯单元测试，不需要网络）"""

    def test_account_data(self):
        from bt_api_py.containers.ctp.ctp_account import CtpAccountData

        account = CtpAccountData(
            {
                "BrokerID": "9999",
                "AccountID": "123456",
                "Balance": 500000.0,
                "Available": 300000.0,
                "CurrMargin": 150000.0,
                "PositionProfit": 5000.0,
                "CloseProfit": 1000.0,
                "Commission": 200.0,
                "PreBalance": 495000.0,
                "FrozenMargin": 10000.0,
            }
        )
        account.init_data()
        assert account.get_exchange_name() == "CTP"
        assert account.get_margin() == 500000.0
        assert account.get_available_margin() == 300000.0
        assert account.get_unrealized_profit() == 5000.0
        assert account.get_account_type() == "123456"
        all_data = account.get_all_data()
        assert all_data["balance"] == 500000.0
        assert all_data["risk_degree"] == 150000.0 / 500000.0

    def test_order_data(self):
        from bt_api_py.containers.ctp.ctp_order import CtpOrderData

        order = CtpOrderData(
            {
                "InstrumentID": "IF2506",
                "OrderRef": "1",
                "OrderSysID": "123",
                "Direction": "0",
                "CombOffsetFlag": "0",
                "LimitPrice": 3500.0,
                "VolumeTotalOriginal": 1,
                "VolumeTraded": 0,
                "VolumeTotal": 1,
                "OrderStatus": "3",
                "InsertTime": "09:30:01",
                "ExchangeID": "CFFEX",
            }
        )
        order.init_data()
        assert order.get_symbol_name() == "IF2506"
        assert order.get_order_side() == "buy"
        assert order.get_order_offset() == "open"
        assert order.get_order_exchange_id() == "CFFEX"
        assert order.get_order_price() == 3500.0
        assert order.get_order_size() == 1

    def test_position_data(self):
        from bt_api_py.containers.ctp.ctp_position import CtpPositionData

        pos = CtpPositionData(
            {
                "InstrumentID": "IF2506",
                "PosiDirection": "2",
                "Position": 5,
                "TodayPosition": 3,
                "YdPosition": 2,
                "UseMargin": 100000.0,
                "PositionProfit": 2500.0,
                "SettlementPrice": 3550.0,
                "ExchangeID": "CFFEX",
            }
        )
        pos.init_data()
        assert pos.get_position_direction() == "long"
        assert pos.get_position_volume() == 5
        assert pos.get_today_position() == 3
        assert pos.get_yesterday_position() == 2

    def test_trade_data(self):
        from bt_api_py.containers.ctp.ctp_trade import CtpTradeData

        trade = CtpTradeData(
            {
                "InstrumentID": "IF2506",
                "TradeID": "T001",
                "OrderSysID": "123",
                "Direction": "0",
                "OffsetFlag": "0",
                "Price": 3500.0,
                "Volume": 1,
                "TradeDate": "20250226",
                "TradeTime": "09:30:01",
                "ExchangeID": "CFFEX",
            },
            symbol_name="IF2506",
        )
        trade.init_data()
        assert trade.get_trade_side() == "buy"
        assert trade.get_trade_offset() == "open"
        assert trade.get_trade_price() == 3500.0

    @pytest.mark.ticker
    def test_ticker_data(self):
        from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData

        tick = CtpTickerData(
            {
                "InstrumentID": "IF2506",
                "LastPrice": 3550.0,
                "BidPrice1": 3549.0,
                "BidVolume1": 10,
                "AskPrice1": 3551.0,
                "AskVolume1": 8,
                "Volume": 50000,
                "OpenInterest": 120000.0,
                "UpperLimitPrice": 3700.0,
                "LowerLimitPrice": 3400.0,
                "UpdateTime": "09:30:01",
                "UpdateMillisec": 500,
            }
        )
        tick.init_data()
        assert tick.get_last_price() == 3550.0
        assert tick.get_bid_price() == 3549.0
        assert tick.get_ask_price() == 3551.0
        assert tick.get_open_interest() == 120000.0

    def test_field_to_dict(self):
        from bt_api_py.feeds.live_ctp_feed import _ctp_field_to_dict

        class MockField:
            InstrumentID = "rb2510"
            LastPrice = 3800.0
            Volume = 1000

            def _internal(self):
                pass

        d = _ctp_field_to_dict(MockField())
        assert d["InstrumentID"] == "rb2510"
        assert d["LastPrice"] == 3800.0
        assert "_internal" not in d

    def test_balance_handler(self):
        import bt_api_py.exchange_registers.register_ctp  # noqa: F401
        from bt_api_py.containers.ctp.ctp_account import CtpAccountData
        from bt_api_py.registry import ExchangeRegistry

        account = CtpAccountData(
            {
                "AccountID": "TEST",
                "Balance": 100000.0,
                "Available": 80000.0,
                "PositionProfit": 3000.0,
            }
        )
        account.init_data()
        handler = ExchangeRegistry.get_balance_handler("CTP___FUTURE")
        value_result, cash_result = handler([account])
        assert value_result["TEST"]["value"] == 103000.0  # balance + position_profit
        assert cash_result["TEST"]["cash"] == 80000.0


class TestCtpOrderThreadingRegression:
    """回归测试：验证下单回报在线程之间安全传递。"""

    def test_trader_client_next_order_ref_is_thread_safe(self):
        from bt_api_py.ctp.client import TraderClient

        client = TraderClient("tcp://test", "9999", "demo", "secret")
        client._max_order_ref = 100

        refs = []
        refs_lock = threading.Lock()

        def worker():
            order_ref = int(client.next_order_ref())
            with refs_lock:
                refs.append(order_ref)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(refs) == list(range(101, 113))

    def test_trader_client_snapshots_order_and_trade_callbacks(self):
        from bt_api_py.ctp.client import TraderClient, _TraderSpi

        class MockOrderField:
            def __init__(self):
                self.InstrumentID = "IF2506"
                self.OrderRef = "101"
                self.OrderSysID = "SYS001"
                self.Direction = "0"
                self.CombOffsetFlag = "0"

        class MockTradeField:
            def __init__(self):
                self.InstrumentID = "IF2506"
                self.TradeID = "TRADE001"
                self.OrderRef = "101"
                self.OrderSysID = "SYS001"
                self.Direction = "0"
                self.OffsetFlag = "0"
                self.Price = 3500.0
                self.Volume = 1
                self.ExchangeID = "CFFEX"

        client = TraderClient("tcp://test", "9999", "demo", "secret")
        seen_order_refs = []
        client.on_order = lambda order_field: seen_order_refs.append(order_field.OrderRef)
        spi = _TraderSpi(client)

        spi.OnRtnOrder(MockOrderField())
        spi.OnRtnTrade(MockTradeField())

        order_event = client.wait_order_event(timeout=0.01)
        trade_event = client.wait_trade_event(timeout=0.01)

        assert order_event["OrderRef"] == "101"
        assert order_event["InstrumentID"] == "IF2506"
        assert trade_event["TradeID"] == "TRADE001"
        assert trade_event["Price"] == 3500.0
        assert seen_order_refs == ["101"]

    def test_trader_client_snapshots_order_insert_errors(self):
        from bt_api_py.ctp.client import TraderClient, _TraderSpi

        class MockInputOrder:
            def __init__(self):
                self.InstrumentID = "IF2506"
                self.OrderRef = "105"

        class MockRspInfo:
            def __init__(self):
                self.ErrorID = 32
                self.ErrorMsg = "order rejected"

        client = TraderClient("tcp://test", "9999", "demo", "secret")
        seen_errors = []
        client.on_error = lambda rsp_info: seen_errors.append((rsp_info.ErrorID, rsp_info.ErrorMsg))
        spi = _TraderSpi(client)

        spi.OnErrRtnOrderInsert(MockInputOrder(), MockRspInfo())

        error_event = client.wait_error_event(timeout=0.01)
        assert error_event["event"] == "order_insert_error"
        assert error_event["error_id"] == 32
        assert error_event["error_msg"] == "order rejected"
        assert error_event["field"]["OrderRef"] == "105"
        assert seen_errors == [(32, "order rejected")]

    def test_make_order_sets_required_ctp_fields(self):
        from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture

        class FakeApi:
            def __init__(self):
                self.field = None
                self.req_id = None

            def ReqOrderInsert(self, field, req_id):
                self.field = field
                self.req_id = req_id
                return 0

        class FakeTrader:
            def __init__(self):
                self.api = FakeApi()
                self.is_ready = True
                self._req_id = 7
                self._front_id = 11
                self._session_id = 22

            def next_order_ref(self):
                return "108"

        feed = CtpRequestDataFuture(
            queue.Queue(),
            broker_id="9999",
            user_id="demo",
            password="secret",
            td_front="tcp://test",
        )
        feed._trader = FakeTrader()
        feed._connected = True

        result = feed.make_order(
            symbol="IF2506",
            volume=1,
            price=3500.0,
            order_type="buy-limit",
            offset="open",
            exchange_id="CFFEX",
        )

        sent_field = feed._trader.api.field
        assert sent_field is not None
        assert sent_field.OrderRef == "108"
        assert sent_field.UserID == "demo"
        assert sent_field.MinVolume == 1
        assert sent_field.RequestID == 8
        assert feed._trader.api.req_id == 8
        assert result.get_status() is True

        order = result.get_data()[0]
        order.init_data()
        assert order.get_client_order_id() == "108"
        assert order.front_id == 11
        assert order.session_id == 22


# ========================================================================
#  2. 集成测试 — 需要 SimNow 网络连接（进程内执行）
#
#  注意事项:
#  - CTP C++ API 在 macOS 上混用 MdApi 和 TraderApi 会导致 segfault,
#    因此 MdApi 和 TraderApi 测试分开到不同的 test class 中。
#  - 同类型 API 可以在同一进程内先后创建多个实例（前一个 stop 后再创建新的）。
#  - 进程退出时 CTP C++ 对象的 GC 清理可能导致 segfault (exit code 139)，
#    这不影响测试结果。conftest_ctp 中的 os._exit hook 可缓解此问题。
# ========================================================================


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
        assert feed._connected, f"Failed to connect to {TD_FRONT}"
        request.cls.feed = feed
        request.cls.data_queue = data_queue
        yield
        # 尝试断开，忽略异常；atexit hook 会兜底跳过 GC
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
        time.sleep(1)  # CTP 流控
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
        time.sleep(1)  # CTP 流控
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
        # make_order 返回 RequestData; 即使余额不足被拒，API 调用本身应该成功
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

        time.sleep(1)  # CTP 流控

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
        pytest tests/test_ctp_feed.py::TestCtpTraderIntegration -v -s
        pytest tests/test_ctp_feed.py::TestCtpMdIntegration -v -s
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

        # 1) 验证原始 tick
        raw_tick = ticks_received[0]
        print(
            f"[MD] Raw tick: {raw_tick.InstrumentID} "
            f"Last={raw_tick.LastPrice} Bid={raw_tick.BidPrice1} Ask={raw_tick.AskPrice1}"
        )
        assert raw_tick.InstrumentID.strip() != ""

        # 2) 验证 _ctp_field_to_dict 转换
        tick_dict = _ctp_field_to_dict(raw_tick)
        assert "InstrumentID" in tick_dict
        assert "LastPrice" in tick_dict
        assert tick_dict["LastPrice"] == raw_tick.LastPrice

        # 3) 验证 CtpTickerData 容器解析 (模拟 CtpMarketStream 的处理流程)
        ticker = CtpTickerData(tick_dict)
        ticker.init_data()
        print(
            f"[MD] CtpTickerData: symbol={ticker.get_symbol_name()} "
            f"last={ticker.get_last_price()} "
            f"bid={ticker.get_bid_price()} ask={ticker.get_ask_price()}"
        )
        assert ticker.get_symbol_name().strip() != ""
        assert ticker.get_last_price() == raw_tick.LastPrice

        # 清理 MdClient，忽略异常；atexit hook 会兜底
        with contextlib.suppress(Exception):
            client.stop()
