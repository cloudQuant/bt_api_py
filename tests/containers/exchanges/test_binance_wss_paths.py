"""
Tests for Binance WebSocket API - New implementations
测试 Binance WebSocket API 新增实现
"""

import pytest

from bt_api_py.containers.exchanges.binance_exchange_data import (
    BinanceExchangeDataSpot,
    BinanceExchangeDataSwap,
)


@pytest.mark.kline
def test_spot_wss_kline_timezone():
    """测试 Spot WebSocket kline_timezone 路径"""
    spot = BinanceExchangeDataSpot()
    assert "kline_timezone" in spot.wss_paths
    assert spot.wss_paths["kline_timezone"]["params"] == ["<symbol>@kline_<period>@+08:00"]
    assert spot.wss_paths["kline_timezone"]["method"] == "SUBSCRIBE"


def test_spot_wss_liquidation_order():
    """测试 Spot WebSocket liquidation_order 路径"""
    spot = BinanceExchangeDataSpot()
    assert "liquidation_order" in spot.wss_paths
    assert spot.wss_paths["liquidation_order"]["params"] == ["<symbol>@forceOrder"]
    # Also verify it's the same as force_order
    assert spot.wss_paths["liquidation_order"] == spot.wss_paths["force_order"]


def test_swap_wss_liquidation_order():
    """测试 Swap WebSocket liquidation_order 路径"""
    swap = BinanceExchangeDataSwap()
    assert "liquidation_order" in swap.wss_paths
    assert swap.wss_paths["liquidation_order"]["params"] == ["<symbol>@forceOrder"]
    # Also verify existing liquidation alias
    assert "liquidation" in swap.wss_paths
    assert swap.wss_paths["liquidation"] == swap.wss_paths["force_order"]


def test_swap_all_liquidation_order_stream():
    """测试合约全部强平订单流"""
    swap = BinanceExchangeDataSwap()
    assert "all_force_order" in swap.wss_paths
    assert swap.wss_paths["all_force_order"]["params"] == ["!forceOrder@arr"]


def test_wss_paths_count():
    """测试 WebSocket 路径数量"""
    spot = BinanceExchangeDataSpot()
    swap = BinanceExchangeDataSwap()

    # Verify kline_timezone was added
    assert "kline_timezone" in spot.wss_paths

    # Verify liquidation_order was added to both
    assert "liquidation_order" in spot.wss_paths
    assert "liquidation_order" in swap.wss_paths


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
