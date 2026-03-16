"""
Registry, BalanceUtils, Exceptions 单元测试
纯单元测试，不需要网络连接
"""

import pytest

from bt_api_py.balance_utils import nested_balance_handler, simple_balance_handler
from bt_api_py.exceptions import (
    BtApiError,
    DataParseError,
    ExchangeNotFoundError,
    OrderError,
    RequestTimeoutError,
    SubscribeError,
)
from bt_api_py.exceptions import (
    ExchangeConnectionAlias as BtConnectionError,
)
from bt_api_py.registry import ExchangeRegistry

# ========================================================================
#  ExchangeRegistry 测试
# ========================================================================


class TestExchangeRegistry:
    def setup_method(self):
        """每个测试前保存注册表状态，测试后恢复"""
        reg = ExchangeRegistry()
        self._saved_feeds = dict(reg._feed_classes)
        self._saved_streams = {k: dict(v) for k, v in reg._stream_classes.items()}
        self._saved_ed = dict(reg._exchange_data_classes)
        self._saved_bh = dict(reg._balance_handlers)

    def teardown_method(self):
        reg = ExchangeRegistry()
        reg._feed_classes.clear()
        reg._feed_classes.update(self._saved_feeds)
        reg._stream_classes.clear()
        reg._stream_classes.update({k: dict(v) for k, v in self._saved_streams.items()})
        reg._exchange_data_classes.clear()
        reg._exchange_data_classes.update(self._saved_ed)
        reg._balance_handlers.clear()
        reg._balance_handlers.update(self._saved_bh)

    def test_register_and_create_feed(self):
        class MockFeed:
            def __init__(self, dq, **kw):
                self.dq = dq
                self.kw = kw

        ExchangeRegistry.register_feed("TEST___MOCK", MockFeed)
        assert ExchangeRegistry.has_exchange("TEST___MOCK")
        feed = ExchangeRegistry.create_feed("TEST___MOCK", "fake_queue", key1="val1")
        assert feed.dq == "fake_queue"
        assert feed.kw["key1"] == "val1"

    def test_create_feed_unknown_raises(self):
        with pytest.raises(ExchangeNotFoundError):
            ExchangeRegistry.create_feed("NONEXISTENT___X", None)

    def test_register_stream(self):
        ExchangeRegistry.register_stream("TEST___S", "market", "MarketCls")
        ExchangeRegistry.register_stream("TEST___S", "account", "AccountCls")
        assert ExchangeRegistry.get_stream_class("TEST___S", "market") == "MarketCls"
        assert ExchangeRegistry.get_stream_class("TEST___S", "account") == "AccountCls"
        assert ExchangeRegistry.get_stream_class("TEST___S", "nonexist") is None
        classes = ExchangeRegistry.get_stream_classes("TEST___S")
        assert len(classes) == 2

    def test_register_balance_handler(self):
        def handler(x):
            return ({"v": 1}, {"c": 2})

        ExchangeRegistry.register_balance_handler("TEST___BH", handler)
        assert ExchangeRegistry.get_balance_handler("TEST___BH") is handler
        assert ExchangeRegistry.get_balance_handler("NONEXIST") is None

    def test_list_exchanges(self):
        ExchangeRegistry.register_feed("TEST___LIST", object)
        exchanges = ExchangeRegistry.list_exchanges()
        assert "TEST___LIST" in exchanges

    def test_binance_registered(self):
        """验证 Binance 在 register_binance() 调用后已注册"""
        from bt_api_py.exchange_registers.register_binance import register_binance

        register_binance()  # 显式调用，避免并行测试中 import 顺序导致的问题
        assert ExchangeRegistry.has_exchange("BINANCE___SWAP")
        assert ExchangeRegistry.has_exchange("BINANCE___SPOT")
        assert ExchangeRegistry.get_balance_handler("BINANCE___SWAP") is not None
        assert ExchangeRegistry.get_stream_class("BINANCE___SWAP", "subscribe") is not None

    def test_okx_registered(self):
        """验证 OKX 在 register_okx() 调用后已注册"""
        from bt_api_py.exchange_registers.register_okx import register_okx

        register_okx()  # 显式调用，避免并行测试中 import 顺序导致的问题
        assert ExchangeRegistry.has_exchange("OKX___SWAP")
        assert ExchangeRegistry.has_exchange("OKX___SPOT")
        assert ExchangeRegistry.get_balance_handler("OKX___SWAP") is not None
        assert ExchangeRegistry.get_stream_class("OKX___SWAP", "subscribe") is not None


# ========================================================================
#  BalanceUtils 测试
# ========================================================================


class MockAccount:
    """模拟 AccountData，用于测试 balance_handler"""

    def __init__(self, account_type, margin, available, unrealized_pnl):
        self._type = account_type
        self._margin = margin
        self._available = available
        self._unrealized_pnl = unrealized_pnl
        self._initialized = False

    def init_data(self):
        self._initialized = True
        return self

    def get_account_type(self):
        return self._type

    def get_margin(self):
        return self._margin

    def get_available_margin(self):
        return self._available

    def get_unrealized_profit(self):
        return self._unrealized_pnl


class MockNestedAccount:
    """模拟 OKX 嵌套 AccountData"""

    def __init__(self, balances):
        self._balances = balances

    def init_data(self):
        return self

    def get_balances(self):
        return self._balances


class MockBalance:
    """模拟 OKX BalanceData"""

    def __init__(self, symbol, margin, available, unrealized_pnl):
        self._symbol = symbol
        self._margin = margin
        self._available = available
        self._unrealized_pnl = unrealized_pnl

    def init_data(self):
        return self

    def get_symbol_name(self):
        return self._symbol

    def get_margin(self):
        return self._margin

    def get_available_margin(self):
        return self._available

    def get_unrealized_profit(self):
        return self._unrealized_pnl


class TestBalanceUtils:
    def test_simple_balance_handler_single_account(self):
        accounts = [MockAccount("USDT", 10000.0, 8000.0, 500.0)]
        value_result, cash_result = simple_balance_handler(accounts)
        assert value_result["USDT"]["value"] == 10500.0  # margin + unrealized
        assert cash_result["USDT"]["cash"] == 8000.0

    def test_simple_balance_handler_multi_account(self):
        accounts = [
            MockAccount("USDT", 10000.0, 8000.0, 500.0),
            MockAccount("BTC", 1.5, 1.0, 0.1),
        ]
        value_result, cash_result = simple_balance_handler(accounts)
        assert "USDT" in value_result and "BTC" in value_result
        assert value_result["BTC"]["value"] == 1.6
        assert cash_result["BTC"]["cash"] == 1.0

    def test_simple_balance_handler_empty(self):
        value_result, cash_result = simple_balance_handler([])
        assert value_result == {}
        assert cash_result == {}

    def test_simple_balance_handler_tolerates_missing_numeric_values(self):
        accounts = [MockAccount("USDT", None, None, None)]
        value_result, cash_result = simple_balance_handler(accounts)
        assert value_result["USDT"]["value"] == 0.0
        assert cash_result["USDT"]["cash"] == 0.0

    def test_nested_balance_handler(self):
        balances = [
            MockBalance("USDT", 5000.0, 4000.0, 200.0),
            MockBalance("BTC", 2.0, 1.5, 0.05),
        ]
        accounts = [MockNestedAccount(balances)]
        value_result, cash_result = nested_balance_handler(accounts)
        assert value_result["USDT"]["value"] == 5200.0
        assert cash_result["USDT"]["cash"] == 4000.0
        assert value_result["BTC"]["value"] == 2.05
        assert cash_result["BTC"]["cash"] == 1.5

    def test_nested_balance_handler_tolerates_missing_numeric_values(self):
        balances = [MockBalance("USDT", None, None, None)]
        accounts = [MockNestedAccount(balances)]
        value_result, cash_result = nested_balance_handler(accounts)
        assert value_result["USDT"]["value"] == 0.0
        assert cash_result["USDT"]["cash"] == 0.0


# ========================================================================
#  Exceptions 测试
# ========================================================================


class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(ExchangeNotFoundError, BtApiError)
        assert issubclass(BtConnectionError, BtApiError)
        assert issubclass(RequestTimeoutError, BtApiError)
        assert issubclass(OrderError, BtApiError)
        assert issubclass(SubscribeError, BtApiError)
        assert issubclass(DataParseError, BtApiError)

    def test_exchange_not_found(self):
        err = ExchangeNotFoundError("FOO___BAR", "available: [A, B]")
        assert "FOO___BAR" in str(err)
        assert err.exchange_name == "FOO___BAR"

    def test_connection_error(self):
        err = BtConnectionError("CTP", "timeout")
        assert "CTP" in str(err)
        assert "timeout" in str(err)

    def test_request_timeout(self):
        err = RequestTimeoutError("BINANCE", "https://api.binance.com/v1/klines", 30)
        assert err.timeout == 30
        assert "BINANCE" in str(err)

    def test_order_error(self):
        err = OrderError("OKX", "BTC-USDT", "insufficient margin")
        assert "BTC-USDT" in str(err)
        assert "insufficient margin" in str(err)
