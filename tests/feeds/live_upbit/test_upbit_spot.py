"""
Upbit Spot Trading Feed Tests

测试 Upbit Spot 交易数据获取和 WebSocket 功能
"""

import pytest

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.containers.tickers.upbit_ticker import UpbitTickerData


class TestUpbitExchangeDataSpot:
    """测试 Upbit Exchange Data"""

    def test_exchange_data_initialization(self):
        """测试交易所数据初始化"""
        exchange_data = UpbitExchangeDataSpot()

        assert exchange_data.exchange_name == "UPBIT___SPOT"
        assert exchange_data.rest_url == "https://api.upbit.com"
        assert exchange_data.wss_url == "wss://api.upbit.com/websocket/v1"

        # 检查基本的 REST 路径
        assert "ticker" in exchange_data.get_rest_path("get_tick")
        assert "orderbook" in exchange_data.get_rest_path("get_depth")

    def test_symbol_formatting(self):
        """测试交易对格式化"""
        exchange_data = UpbitExchangeDataSpot()

        # Upbit get_symbol returns symbol as-is
        assert exchange_data.get_symbol("KRW-BTC") == "KRW-BTC"
        assert exchange_data.get_symbol("BTC-USDT") == "BTC-USDT"

    def test_period_conversion(self):
        """测试周期转换"""
        exchange_data = UpbitExchangeDataSpot()

        # 测试周期转换
        assert exchange_data.get_period("1m") == "1"
        assert exchange_data.get_period("1h") == "60"
        assert exchange_data.get_period("1d") == "D"


class TestUpbitTickerData:
    """测试 Upbit Ticker 数据"""

    def test_ticker_initialization(self):
        """测试 ticker 初始化"""
        ticker_data = {
            "market": "KRW-BTC",
            "trade_price": "50000000",
            "bid_price": "49900000",
            "ask_price": "50100000",
            "acc_trade_volume_24h": "1234.56",
            "high_price": "51000000",
            "low_price": "48000000",
            "opening_price": "49000000",
            "prev_closing_price": "48500000",
            "change": "RISE",
            "signed_change_rate": "0.0325",
            "timestamp": 1642696800000,
        }

        ticker = UpbitTickerData(ticker_data, "KRW-BTC", "spot")
        ticker.init_data()

        assert ticker.exchange_name == "UPBIT"
        assert ticker.symbol_name == "KRW-BTC"
        assert ticker.last_price == 50000000.0
        assert ticker.bid_price == 49900000.0
        assert ticker.ask_price == 50100000.0
        assert ticker.high_price == 51000000.0
        assert ticker.low_price == 48000000.0
        assert ticker.change == "RISE"
        assert ticker.change_rate == 0.0325

    def test_ticker_all_data(self):
        """测试 ticker 所有数据获取"""
        ticker_data = {"market": "KRW-BTC", "trade_price": "50000000", "timestamp": 1642696800000}

        ticker = UpbitTickerData(ticker_data, "KRW-BTC", "spot")
        ticker.init_data()  # Initialize data first
        all_data = ticker.get_all_data()

        assert all_data["exchange_name"] == "UPBIT"
        assert all_data["symbol_name"] == "KRW-BTC"
        assert all_data["last_price"] == 50000000.0


def test_upbit_integration():
    """端到端测试"""
    # 创建交易所数据实例
    exchange_data = UpbitExchangeDataSpot()

    # 测试 REST 路径
    assert "market/all" in exchange_data.get_rest_path("get_exchange_info")
    assert "ticker" in exchange_data.get_rest_path("get_tick")

    # 测试配置加载
    assert exchange_data.exchange_name == "UPBIT___SPOT"
    assert exchange_data.rest_url == "https://api.upbit.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
