"""
Tests for Binance Grid Trading API Request Implementation
测试 Binance 网格交易 API 请求实现
"""

import queue

from bt_api_py.feeds.live_binance.grid import BinanceRequestDataGrid


def test_grid_request_init():
    """测试 Grid Request 初始化"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(
        data_queue, public_key="test_public_key", private_key="test_private_key"
    )
    assert grid.asset_type == "GRID"
    assert grid.logger_name == "binance_grid_feed.log"
    assert grid.exchange_name == "binance_grid"
    assert grid._params.rest_url == "https://api.binance.com"


def test_grid_request_has_methods():
    """测试 Grid Request 有所有必需方法"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(data_queue)

    required_methods = [
        "futures_grid_new_order",
        "futures_grid_cancel_order",
        "get_futures_grid_orders",
        "get_futures_grid_position",
        "get_futures_grid_income",
    ]

    for method in required_methods:
        assert hasattr(grid, method), f"Missing method: {method}"
        assert callable(getattr(grid, method)), f"Method not callable: {method}"


def test_grid_request_futures_grid_new_order_params():
    """测试 futures_grid_new_order 参数构建"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(data_queue)

    path, params, extra_data = grid._futures_grid_new_order(
        symbol="BTC-USDT", upper_price=50000, lower_price=40000, grid_quantity=10, base_quantity=100
    )

    assert path == "POST /sapi/v1/futures/fortune/order"
    assert params["pair"] == "BTCUSDT"  # symbol转换
    assert params["upperPrice"] == 50000
    assert params["lowerPrice"] == 40000
    assert params["gridNumber"] == 10
    assert params["investAmount"] == 100


def test_grid_request_futures_grid_cancel_order_params():
    """测试 futures_grid_cancel_order 参数构建"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(data_queue)

    path, params, extra_data = grid._futures_grid_cancel_order(symbol="BTC-USDT", order_id=12345)

    assert path == "DELETE /sapi/v1/futures/fortune/order"
    assert params["pair"] == "BTCUSDT"
    assert params["orderId"] == 12345


def test_grid_request_get_futures_grid_orders_params():
    """测试 get_futures_grid_orders 参数构建"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(data_queue)

    path, params, extra_data = grid._get_futures_grid_orders(symbol="BTC-USDT")

    assert path == "GET /sapi/v1/futures/fortune/order"
    assert params["pair"] == "BTCUSDT"


def test_grid_request_get_futures_grid_position_params():
    """测试 get_futures_grid_position 参数构建"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(data_queue)

    path, params, extra_data = grid._get_futures_grid_position(symbol="BTC-USDT")

    assert path == "GET /sapi/v1/futures/fortune/position"
    assert params["pair"] == "BTCUSDT"


def test_grid_request_get_futures_grid_income_params():
    """测试 get_futures_grid_income 参数构建"""
    data_queue = queue.Queue()
    grid = BinanceRequestDataGrid(data_queue)

    path, params, extra_data = grid._get_futures_grid_income(symbol="BTC-USDT", limit=10)

    assert path == "GET /sapi/v1/futures/fortune/income"
    assert params["pair"] == "BTCUSDT"
    assert params["limit"] == 10


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
