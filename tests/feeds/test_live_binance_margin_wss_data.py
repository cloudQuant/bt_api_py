"""
Tests for Binance Margin WebSocket API
测试 Binance 杠杆 WebSocket API
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.accounts.binance_account import BinanceSpotWssAccountData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataMargin
from bt_api_py.containers.orders.binance_order import BinanceSpotWssOrderData
from bt_api_py.containers.trades.binance_trade import BinanceSpotWssTradeData
from bt_api_py.feeds.live_binance_feed import BinanceAccountWssDataMargin


def init_margin_wss():
    """初始化 Margin WSS 实例 (mock wss_author 避免网络调用)"""
    data_queue = queue.Queue()
    kwargs = {
        "exchange_data": BinanceExchangeDataMargin(),
    }
    # Mock wss_author to avoid actual network calls
    with patch.object(BinanceAccountWssDataMargin, "wss_author", return_value=None):
        margin_wss = BinanceAccountWssDataMargin(data_queue, **kwargs)
        # Set listen_key manually
        margin_wss.listen_key = "test_listen_key"
    return margin_wss, data_queue


def test_margin_account_wss_has_handle_data():
    """测试 Margin Account WSS 有 handle_data 方法"""
    margin_wss, _ = init_margin_wss()
    assert hasattr(margin_wss, "handle_data")
    assert callable(margin_wss.handle_data)


def test_margin_account_wss_handle_data_execution_report():
    """测试处理 executionReport 事件"""
    margin_wss, data_queue = init_margin_wss()

    # 模拟 executionReport 事件 (非成交)
    content = {
        "e": "executionReport",
        "E": 123456789,
        "s": "BTCUSDT",
        "c": "CLIENT_ORDER_ID",
        "S": "BUY",
        "o": "LIMIT",
        "f": "GTC",
        "q": "1.00000000",
        "p": "50000.00000000",
        "x": "NEW",  # 非 TRADE 事件
        "X": "NEW",
        "r": "NONE",
        "i": 12345678,
        "l": "0.00000000",
        "z": "0.00000000",
        "L": "0.00000000",
        "n": "0",
        "N": None,
        "T": 123456789,
        "t": 0,
        "I": 12345678,
        "w": True,
        "m": False,
        "M": False,
        "O": 123456789,
        "Z": "0.00000000",
        "Y": "0.00000000",
        "Q": "0.00000000",
    }

    margin_wss.handle_data(content)

    # 应该接收到订单数据
    try:
        data = data_queue.get(timeout=1)
        assert isinstance(data, BinanceSpotWssOrderData)
    except queue.Empty:
        pytest.fail("No order data received")


def test_margin_account_wss_handle_data_outbound_account_position():
    """测试处理 outboundAccountPosition 事件"""
    margin_wss, data_queue = init_margin_wss()

    # 模拟 outboundAccountPosition 事件
    content = {
        "e": "outboundAccountPosition",
        "E": 123456789,
        "u": 123456789,
        "B": [
            {"a": "USDT", "f": "1000.00000000", "l": "0.00000000"},
            {"a": "BTC", "f": "0.50000000", "l": "0.00000000"},
        ],
    }

    margin_wss.handle_data(content)

    # 应该接收到账户数据
    try:
        data = data_queue.get(timeout=1)
        assert isinstance(data, BinanceSpotWssAccountData)
    except queue.Empty:
        pytest.fail("No account data received")


def test_margin_account_wss_handle_data_execution_report_trade():
    """测试处理 executionReport 成交事件"""
    margin_wss, data_queue = init_margin_wss()

    # 模拟 executionReport 成交事件
    content = {
        "e": "executionReport",
        "E": 123456789,
        "s": "BTCUSDT",
        "c": "CLIENT_ORDER_ID",
        "S": "BUY",
        "o": "LIMIT",
        "f": "GTC",
        "q": "1.00000000",
        "p": "50000.00000000",
        "x": "TRADE",  # 成交事件
        "X": "FILLED",
        "r": "NONE",
        "i": 12345678,
        "l": "1.00000000",
        "z": "1.00000000",
        "L": "50000.00000000",
        "n": "0",
        "N": None,
        "T": 123456789,
        "t": 12345678,
        "I": 12345678,
        "w": True,
        "m": False,
        "M": False,
        "O": 123456789,
        "Z": "50000.00000000",
        "Y": "0.00000000",
        "Q": "0.00000000",
    }

    margin_wss.handle_data(content)

    # 应该接收到成交数据
    try:
        data = data_queue.get(timeout=1)
        assert isinstance(data, BinanceSpotWssTradeData)
    except queue.Empty:
        pytest.fail("No trade data received")


def test_margin_account_wss_handle_data_balance_update():
    """测试处理 balanceUpdate 事件"""
    margin_wss, data_queue = init_margin_wss()

    # 模拟 balanceUpdate 事件 (分红等)
    content = {
        "e": "balanceUpdate",
        "E": 1573200697114,
        "s": "BTC",
        "u": "15896533547050558808",
        "B": "500.00000000",
    }

    margin_wss.handle_data(content)

    # 应该接收到余额更新数据
    try:
        data = data_queue.get(timeout=1)
        assert isinstance(data, BinanceSpotWssAccountData)
    except queue.Empty:
        pytest.fail("No balance update data received")


def test_margin_account_wss_has_push_methods():
    """测试 Margin Account WSS 有 push 方法"""
    margin_wss, _ = init_margin_wss()

    assert hasattr(margin_wss, "push_account")
    assert hasattr(margin_wss, "push_order")
    assert hasattr(margin_wss, "push_trade")
    assert hasattr(margin_wss, "push_balance")
    assert callable(margin_wss.push_account)
    assert callable(margin_wss.push_order)
    assert callable(margin_wss.push_trade)
    assert callable(margin_wss.push_balance)


def test_spot_account_wss_has_push_balance():
    """测试 Spot Account WSS 有 push_balance 方法"""
    from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
    from bt_api_py.feeds.live_binance_feed import BinanceAccountWssDataSpot

    data_queue = queue.Queue()
    kwargs = {"exchange_data": BinanceExchangeDataSpot()}
    # Mock wss_author to avoid actual network calls
    with patch.object(BinanceAccountWssDataSpot, "wss_author", return_value=None):
        spot_wss = BinanceAccountWssDataSpot(data_queue, **kwargs)
        spot_wss.listen_key = "test_listen_key"

    assert hasattr(spot_wss, "push_balance")
    assert callable(spot_wss.push_balance)


def test_spot_account_wss_handle_balance_update():
    """测试 Spot 处理 balanceUpdate 事件"""
    from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
    from bt_api_py.feeds.live_binance_feed import BinanceAccountWssDataSpot

    data_queue = queue.Queue()
    kwargs = {"exchange_data": BinanceExchangeDataSpot()}
    # Mock wss_author to avoid actual network calls
    with patch.object(BinanceAccountWssDataSpot, "wss_author", return_value=None):
        spot_wss = BinanceAccountWssDataSpot(data_queue, **kwargs)
        spot_wss.listen_key = "test_listen_key"

    # 模拟 balanceUpdate 事件
    content = {
        "e": "balanceUpdate",
        "E": 1573200697114,
        "s": "BTC",
        "u": "15896533547050558808",
        "B": "500.00000000",
    }

    spot_wss.handle_data(content)

    # 应该接收到余额更新数据
    try:
        data = data_queue.get(timeout=1)
        assert isinstance(data, BinanceSpotWssAccountData)
    except queue.Empty:
        pytest.fail("No balance update data received")


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
