"""
Tests for Hyperliquid exchange integration
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json


class TestHyperliquidExchangeData:
    """Test Hyperliquid exchange data container"""

    def test_exchange_data_spot_init(self):
        """Test HyperliquidExchangeDataSpot initialization"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot

        exchange_data = HyperliquidExchangeDataSpot()

        assert exchange_data.exchange_name == "hyperliquid_spot"
        assert exchange_data.rest_url == "https://api.hyperliquid.xyz"
        assert exchange_data.wss_url == "wss://api.hyperliquid.xyz/ws"
        assert "1m" in exchange_data.kline_periods
        # kline_periods values are strings from YAML config
        assert exchange_data.kline_periods["1h"] == "1h"

    def test_get_symbol(self):
        """Test symbol conversion"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot

        exchange_data = HyperliquidExchangeDataSpot()

        # Test perp symbols
        assert exchange_data.get_symbol("BTC") == "BTC"
        assert exchange_data.get_symbol("ETH") == "ETH"

        # Test spot symbols with slash
        assert exchange_data.get_symbol("BTC/USDC") == "BTC"
        assert exchange_data.get_symbol("ETH/USDC") == "ETH"

    def test_get_rest_path(self):
        """Test REST path retrieval"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot

        exchange_data = HyperliquidExchangeDataSpot()

        assert exchange_data.get_rest_path("get_all_mids") == "/info"
        assert exchange_data.get_rest_path("make_order") == "/exchange"
        assert exchange_data.get_rest_path("cancel_order") == "/exchange"

    def test_get_timeframe_minutes(self):
        """Test timeframe conversion - returns string value from config"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot

        exchange_data = HyperliquidExchangeDataSpot()

        # get_timeframe_minutes returns the string value from kline_periods
        assert exchange_data.get_timeframe_minutes("1m") == "1m"
        assert exchange_data.get_timeframe_minutes("1h") == "1h"
        assert exchange_data.get_timeframe_minutes("1d") == "1d"

    def test_get_timeframe_from_minutes(self):
        """Test minutes to timeframe conversion"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot

        exchange_data = HyperliquidExchangeDataSpot()

        # Note: reverse_kline_periods maps string to string
        # This test verifies the reverse lookup works
        assert exchange_data.get_timeframe_from_minutes("1m") == "1m"
        assert exchange_data.get_timeframe_from_minutes("1h") == "1h"
        assert exchange_data.get_timeframe_from_minutes("1d") == "1d"


class TestHyperliquidRequestData:
    """Test Hyperliquid request data"""

    def test_request_data_init(self):
        """Test HyperliquidRequestData initialization"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        request_data = HyperliquidRequestData(data_queue, **kwargs)

        assert request_data.asset_type == "SPOT"
        assert request_data._params is not None
        assert request_data._params.exchange_name == "hyperliquid_spot"

    @patch('bt_api_py.feeds.live_hyperliquid.request_base.requests.post')
    def test_get_all_mids(self, mock_post):
        """Test getting all mid prices"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData

        # Mock response - Hyperliquid returns the whole response as a dict
        mock_response = Mock()
        mock_response.json.return_value = {
            "BTC": "50000.0",
            "ETH": "3000.0"
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        request_data = HyperliquidRequestData(data_queue, **kwargs)
        result = request_data.get_all_mids()

        assert result is not None
        # Use get_input_data() to get the raw response
        assert result.get_input_data()["BTC"] == "50000.0"
        assert result.get_input_data()["ETH"] == "3000.0"

    @patch('bt_api_py.feeds.live_hyperliquid.request_base.requests.post')
    def test_get_meta(self, mock_post):
        """Test getting metadata"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData

        # Mock response - Hyperliquid meta response
        mock_response = Mock()
        mock_response.json.return_value = [
            {"name": "BTC", "szDecimals": 3, "maxLeverage": 100},
            {"name": "ETH", "szDecimals": 2, "maxLeverage": 50}
        ]
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        request_data = HyperliquidRequestData(data_queue, **kwargs)
        result = request_data.get_meta()

        assert result is not None
        # Use get_input_data() to get the raw response
        # Meta returns a list
        assert len(result.get_input_data()) == 2
        assert result.get_input_data()[0]["name"] == "BTC"


class TestHyperliquidRequestDataSpot:
    """Test Hyperliquid spot trading request data"""

    def test_spot_request_data_init(self):
        """Test HyperliquidRequestDataSpot initialization"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidRequestDataSpot

        data_queue = Mock()
        kwargs = {"asset_type": "SPOT"}

        request_data = HyperliquidRequestDataSpot(data_queue, **kwargs)

        assert request_data.asset_type == "SPOT"
        assert request_data._params.exchange_name == "hyperliquid_spot"


class TestHyperliquidMarketWssData:
    """Test Hyperliquid market WebSocket data"""

    def test_market_wss_init(self):
        """Test HyperliquidMarketWssDataSpot initialization"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        market_wss = HyperliquidMarketWssDataSpot(data_queue, **kwargs)

        assert market_wss.asset_type == "SPOT"
        assert market_wss._params is not None

    def test_subscribe_ticker(self):
        """Test ticker subscription"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        market_wss = HyperliquidMarketWssDataSpot(data_queue, **kwargs)
        subscription = market_wss.subscribe_ticker("BTC")

        assert subscription["method"] == "subscribe"
        assert subscription["subscription"]["type"] == "allMids"

    def test_subscribe_orderbook(self):
        """Test orderbook subscription"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        market_wss = HyperliquidMarketWssDataSpot(data_queue, **kwargs)
        subscription = market_wss.subscribe_orderbook("BTC")

        assert subscription["method"] == "subscribe"
        assert subscription["subscription"]["type"] == "l2Book"
        assert subscription["subscription"]["coin"] == "BTC"

    def test_subscribe_trades(self):
        """Test trades subscription"""
        from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        kwargs = {
            "asset_type": "SPOT",
            "exchange_data": HyperliquidExchangeDataSpot()
        }

        market_wss = HyperliquidMarketWssDataSpot(data_queue, **kwargs)
        subscription = market_wss.subscribe_trades("BTC")

        assert subscription["method"] == "subscribe"
        assert subscription["subscription"]["type"] == "trades"
        assert subscription["subscription"]["coin"] == "BTC"


class TestHyperliquidRegistration:
    """Test Hyperliquid registration"""

    def test_registration_exists(self):
        """Test that Hyperliquid is registered"""
        # Import to ensure registration happens
        from bt_api_py.feeds.register_hyperliquid import register_hyperliquid
        from bt_api_py.registry import ExchangeRegistry

        # Ensure registration
        register_hyperliquid()

        # Check if SPOT is registered
        spot_feed = ExchangeRegistry._feed_classes.get("HYPERLIQUID___SPOT")
        assert spot_feed is not None

        # Check if exchange data is registered
        spot_data = ExchangeRegistry._exchange_data_classes.get("HYPERLIQUID___SPOT")
        assert spot_data is not None

    def test_create_spot_feed(self):
        """Test creating Hyperliquid spot feed"""
        from bt_api_py.feeds.register_hyperliquid import register_hyperliquid
        from bt_api_py.registry import ExchangeRegistry

        # Ensure registration
        register_hyperliquid()

        # Create feed
        data_queue = Mock()
        feed = ExchangeRegistry.create_feed("HYPERLIQUID___SPOT", data_queue, asset_type="SPOT")

        assert feed is not None
        assert feed.asset_type == "SPOT"

    def test_create_spot_exchange_data(self):
        """Test creating Hyperliquid spot exchange data"""
        from bt_api_py.feeds.register_hyperliquid import register_hyperliquid
        from bt_api_py.registry import ExchangeRegistry

        # Ensure registration
        register_hyperliquid()

        # Create exchange data
        exchange_data = ExchangeRegistry.create_exchange_data("HYPERLIQUID___SPOT")

        assert exchange_data is not None
        assert exchange_data.exchange_name == "hyperliquid_spot"


class TestHyperliquidDataContainers:
    """Test Hyperliquid data containers"""

    def test_ticker_container(self):
        """Test ticker data container"""
        from bt_api_py.containers.tickers.hyperliquid_ticker import HyperliquidTickerData

        # Pass has_been_json_encoded=True since we're passing a dict
        # that's already been parsed
        ticker_data = {
            "last": "50000.0",
            "symbol": "BTC"
        }

        ticker = HyperliquidTickerData(ticker_data, "BTC", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.get_symbol_name() == "BTC"
        # The SPOT ticker should read "last" field as float
        assert ticker.get_last_price() == 50000.0

    def test_order_container(self):
        """Test order data container"""
        from bt_api_py.containers.orders.hyperliquid_order import HyperliquidRequestOrderData

        # Order placement response format from Hyperliquid
        order_data = {
            "statuses": [
                {
                    "resting": {
                        "oid": 12345,
                        "side": "B",
                        "type": "limit",
                        "sz": "0.1",
                        "limit_px": "50000.0"
                    }
                }
            ]
        }

        order = HyperliquidRequestOrderData(order_data, "BTC", "SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.get_symbol_name() == "BTC"
        assert order.get_order_id() == 12345

    def test_balance_container(self):
        """Test balance data container"""
        from bt_api_py.containers.balances.hyperliquid_balance import HyperliquidSpotRequestBalanceData

        # Spot clearinghouse state response format
        balance_data = {
            "balances": [
                {
                    "coin": "USDC",
                    "total": "1000.0",
                    "free": "900.0",
                    "hold": "100.0"
                }
            ]
        }

        balance = HyperliquidSpotRequestBalanceData(balance_data, "USDC", "SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.get_symbol_name() == "USDC"
        assert balance.get_coin() == "USDC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
