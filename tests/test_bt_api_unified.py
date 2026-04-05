"""验证 BtApi 统一接口的基本正确性（不需要真实API密钥）"""

from __future__ import annotations

from datetime import timedelta

import pytest

from bt_api_py.bt_api import KLINE_PERIOD_DELTAS, BtApi
from bt_api_py.exceptions import ExchangeNotFoundError, InvalidOrderError, SubscribeError


class _ValidationFeed:
    def make_order(self, *args, **kwargs):
        return {"ok": True, "args": args, "kwargs": kwargs}


class TestBtApiUnifiedInterface:
    """测试 BtApi 统一接口方法是否存在且向后兼容"""

    def setup_method(self):
        self.bt = BtApi(None, debug=False)

    def test_old_methods_exist(self):
        """原有接口应保持不变"""
        old_methods = [
            "get_request_api",
            "get_async_request_api",
            "get_data_queue",
            "subscribe",
            "download_history_bars",
            "update_total_balance",
            "update_balance",
            "get_cash",
            "get_value",
            "get_total_cash",
            "get_total_value",
            "get_event_bus",
            "list_exchanges",
            "list_available_exchanges",
            "add_exchange",
            "push_bar_data_to_queue",
        ]
        for m in old_methods:
            assert hasattr(self.bt, m), f"backward-compat method {m} missing"

    def test_unified_sync_methods_exist(self):
        """统一同步接口方法应存在"""
        sync_methods = [
            "get_tick",
            "get_depth",
            "get_kline",
            "make_order",
            "cancel_order",
            "cancel_all",
            "query_order",
            "get_open_orders",
            "get_balance",
            "get_account",
            "get_position",
        ]
        for m in sync_methods:
            assert hasattr(self.bt, m), f"unified sync method {m} missing"
            assert callable(getattr(self.bt, m))

    def test_unified_async_methods_exist(self):
        """统一异步接口方法应存在"""
        async_methods = [
            "async_get_tick",
            "async_get_depth",
            "async_get_kline",
            "async_make_order",
            "async_cancel_order",
            "async_cancel_all",
            "async_query_order",
            "async_get_open_orders",
            "async_get_balance",
            "async_get_account",
            "async_get_position",
        ]
        for m in async_methods:
            assert hasattr(self.bt, m), f"unified async method {m} missing"
            assert callable(getattr(self.bt, m))

    def test_batch_methods_exist(self):
        """批量操作方法应存在"""
        batch_methods = [
            "get_all_ticks",
            "get_all_balances",
            "get_all_positions",
            "cancel_all_orders",
        ]
        for m in batch_methods:
            assert hasattr(self.bt, m), f"batch method {m} missing"
            assert callable(getattr(self.bt, m))

    def test_get_feed_raises_on_invalid_exchange(self):
        """访问不存在的交易所应抛出 ExchangeNotFoundError"""
        with pytest.raises(ExchangeNotFoundError):
            self.bt._get_feed("INVALID___EXCHANGE")

    def test_unified_method_raises_on_invalid_exchange(self):
        """统一接口方法对不存在的交易所应抛出 ExchangeNotFoundError"""
        with pytest.raises(ExchangeNotFoundError):
            self.bt.get_tick("INVALID___EXCHANGE", "BTC-USDT")

        with pytest.raises(ExchangeNotFoundError):
            self.bt.make_order("INVALID___EXCHANGE", "BTC-USDT", 0.001, 50000, "limit")

        with pytest.raises(ExchangeNotFoundError):
            self.bt.get_balance("INVALID___EXCHANGE")

    def test_batch_methods_return_empty_when_no_exchanges(self):
        """无交易所时批量操作应返回空字典"""
        assert self.bt.get_all_ticks("BTC-USDT") == {}
        assert self.bt.get_all_balances() == {}
        assert self.bt.get_all_positions() == {}
        assert self.bt.cancel_all_orders() == {}

    @pytest.mark.parametrize(
        ("volume", "price", "order_type", "error_match"),
        [
            (0, 50000, "limit", "volume"),
            (-1, 50000, "limit", "volume"),
            (1, -1, "limit", "price"),
            (1, 1, "stop_limit", "order_type"),
        ],
    )
    def test_make_order_rejects_invalid_parameters(
        self, volume: float, price: float, order_type: str, error_match: str
    ):
        self.bt.exchange_feeds["TEST_VALIDATION___SPOT"] = _ValidationFeed()
        with pytest.raises(InvalidOrderError, match=error_match):
            self.bt.make_order("TEST_VALIDATION___SPOT", "BTC-USDT", volume, price, order_type)

    def test_subscribe_rejects_invalid_dataname_format(self):
        with pytest.raises(SubscribeError, match="dataname"):
            self.bt.subscribe("BINANCE_SPOT_BTCUSDT", [{"topic": "kline"}])


class TestKlinePeriodDeltas:
    """测试 K 线周期时间映射的正确性"""

    def test_kline_period_deltas_correctness(self):
        """验证 K 线周期时间映射正确"""
        expected = {
            "1m": timedelta(minutes=1),
            "3m": timedelta(minutes=3),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "30m": timedelta(minutes=30),
            "1H": timedelta(hours=1),
            "1D": timedelta(days=1),
        }
        for period, expected_delta in expected.items():
            actual = KLINE_PERIOD_DELTAS.get(period)
            assert actual is not None, f"Missing period: {period}"
            assert actual == expected_delta, (
                f"Period {period}: expected {expected_delta}, got {actual}"
            )

    def test_kline_period_delta_minutes_not_hours(self):
        """确保分钟周期不会被错误映射为小时"""
        assert KLINE_PERIOD_DELTAS["1m"] == timedelta(minutes=1), (
            "1m should be 1 minute, not 1 hour"
        )
        assert KLINE_PERIOD_DELTAS["1H"] == timedelta(hours=1), "1H should be 1 hour"
        assert KLINE_PERIOD_DELTAS["1D"] == timedelta(days=1), "1D should be 1 day"
