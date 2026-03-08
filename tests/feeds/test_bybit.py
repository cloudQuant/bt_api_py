"""
Test Bybit exchange integration.

Run tests:
    pytest tests/feeds/test_bybit.py -v

Run with coverage:
    pytest tests/feeds/test_bybit.py --cov=bt_api_py.feeds.live_bybit --cov-report=term-missing
"""

import queue

import pytest

# Import registration to auto-register Bybit
import bt_api_py.exchange_registers.register_bybit  # noqa: F401
from bt_api_py.containers.balances.bybit_balance import (
    BybitSpotBalanceData,
)
from bt_api_py.containers.exchanges.bybit_exchange_data import (
    BybitExchangeDataSpot,
    BybitExchangeDataSwap,
)
from bt_api_py.containers.orderbooks.bybit_orderbook import (
    BybitSpotOrderBookData,
)
from bt_api_py.containers.orders.bybit_order import (
    BybitSpotOrderData,
)
from bt_api_py.containers.tickers.bybit_ticker import (
    BybitSpotTickerData,
    BybitSwapTickerData,
)
from bt_api_py.registry import ExchangeRegistry

# ===================== Exchange Data Tests =====================


class TestBybitExchangeData:
    """Test Bybit exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Bybit spot exchange data."""
        exchange_data = BybitExchangeDataSpot()
        assert exchange_data.exchange_name == "bybitSpot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert len(exchange_data.rest_paths) > 0

    def test_exchange_data_swap_creation(self):
        """Test creating Bybit swap exchange data."""
        exchange_data = BybitExchangeDataSwap()
        assert exchange_data.exchange_name == "bybitSwap"
        assert exchange_data.asset_type == "swap"
        assert exchange_data.rest_url != ""

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BybitExchangeDataSpot()
        assert exchange_data.get_symbol("btcusdt") == "BTCUSDT"
        assert exchange_data.get_symbol("ethusdt") == "ETHUSDT"

    def test_get_symbol_path(self):
        """Test symbol path format conversion."""
        exchange_data = BybitExchangeDataSpot()
        assert exchange_data.get_symbol_path("btcusdt") == "BTCUSDT"
        assert exchange_data.get_symbol_path("ethusdt") == "ETHUSDT"

    def test_get_period(self):
        """Test period conversion."""
        exchange_data = BybitExchangeDataSpot()
        assert exchange_data.get_period("1m") == "1"
        assert exchange_data.get_period("1h") == "60"
        assert exchange_data.get_period("1d") == "D"

    def test_get_period_path(self):
        """Test period path conversion."""
        exchange_data = BybitExchangeDataSpot()
        assert exchange_data.get_period_path("1m") == "1"
        assert exchange_data.get_period_path("1h") == "60"
        assert exchange_data.get_period_path("1d") == "D"

    def test_get_rest_path(self):
        """Test getting REST API paths by name."""
        exchange_data = BybitExchangeDataSpot()
        # get_rest_path takes single arg (path_name)
        path = exchange_data.get_rest_path("get_tick")
        assert path != ""
        assert "market" in path.lower() or "tickers" in path.lower()

        path = exchange_data.get_rest_path("get_depth")
        assert path != ""

        path = exchange_data.get_rest_path("get_kline")
        assert path != ""

        # Non-existent key returns empty string
        path = exchange_data.get_rest_path("nonexistent")
        assert path == ""

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BybitExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BybitExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency


# ===================== Data Container Tests =====================


class TestBybitDataContainers:
    """Test Bybit data containers."""

    @pytest.mark.ticker
    def test_spot_ticker_container(self):
        """Test spot ticker data container."""
        ticker_data = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "50000",
                        "bid1Price": "49990",
                        "ask1Price": "50010",
                        "bid1Size": "1.5",
                        "ask1Size": "2.0",
                        "volume24h": "1234.56",
                        "highPrice24h": "51000",
                        "lowPrice24h": "49000",
                        "turnover24h": "12345678",
                    }
                ]
            },
        }

        ticker = BybitSpotTickerData(ticker_data, "BTCUSDT", True)
        ticker.init_data()

        assert ticker.exchange_name == "BYBIT"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "spot"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49990.0
        assert ticker.ask_price == 50010.0
        assert ticker.bid_volume == 1.5
        assert ticker.ask_volume == 2.0
        assert ticker.high_price_24h == 51000.0
        assert ticker.low_price_24h == 49000.0
        assert ticker.volume_24h == 1234.56
        assert ticker.has_been_init_data is True

    @pytest.mark.ticker
    def test_spot_ticker_get_all_data(self):
        """Test ticker get_all_data method."""
        ticker_data = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "50000",
                        "bid1Price": "49990",
                        "ask1Price": "50010",
                    }
                ]
            },
        }
        ticker = BybitSpotTickerData(ticker_data, "BTCUSDT", True)
        all_data = ticker.get_all_data()
        assert isinstance(all_data, dict)
        assert all_data["exchange_name"] == "BYBIT"
        assert all_data["symbol_name"] == "BTCUSDT"

    @pytest.mark.ticker
    def test_swap_ticker_container(self):
        """Test swap ticker data container."""
        ticker_data = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "50000",
                        "bid1Price": "49990",
                        "ask1Price": "50010",
                    }
                ]
            },
        }
        ticker = BybitSwapTickerData(ticker_data, "BTCUSDT", True)
        ticker.init_data()

        assert ticker.asset_type == "swap"
        assert ticker.last_price == 50000.0

    @pytest.mark.orderbook
    def test_orderbook_container(self):
        """Test orderbook data container."""
        orderbook_data = {
            "retCode": 0,
            "result": {
                "b": [["49990", "1.5"], ["49980", "2.0"]],
                "a": [["50010", "1.0"], ["50020", "2.5"]],
                "ts": "1640995200000",
                "u": 12345,
            },
        }
        orderbook = BybitSpotOrderBookData(orderbook_data, "BTCUSDT", True)
        orderbook.init_data()

        assert orderbook.exchange_name == "BYBIT"
        assert orderbook.symbol_name == "BTCUSDT"
        assert orderbook.asset_type == "spot"
        assert len(orderbook.bids) == 2
        assert len(orderbook.asks) == 2
        assert orderbook.bids[0][0] == 49990.0
        assert orderbook.asks[0][0] == 50010.0
        assert orderbook.get_best_bid() == 49990.0
        assert orderbook.get_best_ask() == 50010.0
        assert orderbook.get_spread() == 20.0
        assert orderbook.is_valid() is True

    def test_order_container(self):
        """Test order data container."""
        order_data = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "orderId": "123456789",
                        "orderLinkId": "client_123",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "orderType": "Limit",
                        "orderStatus": "Filled",
                        "qty": "0.001",
                        "cumExecQty": "0.001",
                        "leavesQty": "0",
                        "price": "50000",
                        "avgPrice": "50000",
                        "createdTime": "1640995200000",
                        "updatedTime": "1640995201000",
                        "timeInForce": "GTC",
                    }
                ]
            },
        }
        order = BybitSpotOrderData(order_data, "BTCUSDT", True)
        order.init_data()

        assert order.exchange_name == "BYBIT"
        assert order.symbol_name == "BTCUSDT"
        assert order.asset_type == "spot"
        assert order.order_id == "123456789"
        assert order.order_link_id == "client_123"
        assert order.side == "Buy"
        assert order.order_type == "Limit"
        assert order.status == "Filled"
        assert order.is_filled() is True
        assert order.is_active() is False

    def test_balance_container(self):
        """Test balance data container."""
        balance_data = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "accountType": "SPOT",
                        "totalEquity": "10000",
                        "totalWalletBalance": "10000",
                        "totalAvailableBalance": "9000",
                        "coin": [
                            {
                                "coin": "BTC",
                                "walletBalance": "0.5",
                                "availableToWithdraw": "0.4",
                                "locked": "0.1",
                                "equity": "0.5",
                                "unrealisedPnl": "0",
                            }
                        ],
                    }
                ]
            },
        }

        balance = BybitSpotBalanceData(balance_data, True)
        balance.init_data()

        assert balance.exchange_name == "BYBIT"
        assert balance.asset_type == "spot"
        assert balance.account_type == "SPOT"
        assert balance.total_equity == "10000"
        assert balance.total_wallet_balance == "10000"
        assert balance.total_available_balance == "9000"
        assert len(balance.coins) == 1
        assert balance.coins[0]["coin"] == "BTC"
        assert balance.get_available_balance("BTC") == 0.4
        assert balance.get_locked_balance("BTC") == 0.1
        assert balance.has_balance("BTC") is True
        assert balance.has_balance("ETH") is False

    def test_balance_container_empty(self):
        """Test balance container with empty result."""
        balance_data = {"retCode": 0, "result": {"list": []}}
        balance = BybitSpotBalanceData(balance_data, True)
        balance.init_data()
        assert balance.account_type is None


# ===================== Feed Creation Tests =====================


class TestBybitFeedCreation:
    """Test Bybit feed creation."""

    def test_spot_feed_creation(self):
        """Test creating Bybit spot feed."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        data_queue = queue.Queue()
        feed = BybitRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        assert feed.asset_type == "spot"
        assert feed.public_key == "test_key"
        assert feed.private_key == "test_secret"
        assert feed._params is not None
        assert isinstance(feed._params, BybitExchangeDataSpot)

    def test_swap_feed_creation(self):
        """Test creating Bybit swap feed."""
        from bt_api_py.feeds.live_bybit.swap import BybitRequestDataSwap

        data_queue = queue.Queue()
        feed = BybitRequestDataSwap(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        assert feed.asset_type == "swap"
        assert isinstance(feed._params, BybitExchangeDataSwap)

    def test_spot_three_layer_methods_exist(self):
        """Test that three-layer methods exist in spot feed."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        data_queue = queue.Queue()
        feed = BybitRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        # Check _get_xxx methods exist
        assert hasattr(feed, "_get_ticker")
        assert hasattr(feed, "_get_depth")
        assert hasattr(feed, "_get_kline")
        assert hasattr(feed, "_get_balance")
        assert hasattr(feed, "_make_order")
        assert hasattr(feed, "_cancel_order")
        assert hasattr(feed, "_query_order")
        assert hasattr(feed, "_get_deals")

        # Check get_xxx methods exist
        assert hasattr(feed, "get_ticker")
        assert hasattr(feed, "get_tick")
        assert hasattr(feed, "get_depth")
        assert hasattr(feed, "get_kline")
        assert hasattr(feed, "get_balance")
        assert hasattr(feed, "get_account")
        assert hasattr(feed, "make_order")
        assert hasattr(feed, "cancel_order")
        assert hasattr(feed, "query_order")
        assert hasattr(feed, "get_deals")
        assert hasattr(feed, "get_server_time")
        assert hasattr(feed, "get_exchange_info")

        # Check async_get_xxx methods exist
        assert hasattr(feed, "async_get_ticker")
        assert hasattr(feed, "async_get_tick")
        assert hasattr(feed, "async_get_depth")
        assert hasattr(feed, "async_get_kline")
        assert hasattr(feed, "async_get_balance")
        assert hasattr(feed, "async_make_order")

    def test_swap_three_layer_methods_exist(self):
        """Test that three-layer methods exist in swap feed."""
        from bt_api_py.feeds.live_bybit.swap import BybitRequestDataSwap

        data_queue = queue.Queue()
        feed = BybitRequestDataSwap(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        assert hasattr(feed, "_get_ticker")
        assert hasattr(feed, "get_ticker")
        assert hasattr(feed, "async_get_ticker")
        assert hasattr(feed, "_get_depth")
        assert hasattr(feed, "get_depth")
        assert hasattr(feed, "_get_kline")
        assert hasattr(feed, "get_kline")
        assert hasattr(feed, "_get_balance")
        assert hasattr(feed, "get_balance")
        assert hasattr(feed, "_make_order")
        assert hasattr(feed, "make_order")


# ===================== Three-Layer Pattern Tests =====================


class TestBybitThreeLayerPattern:
    """Test the _get_xxx / get_xxx / async_get_xxx pattern."""

    def setup_method(self):
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        self.data_queue = queue.Queue()
        self.feed = BybitRequestDataSpot(
            self.data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

    @pytest.mark.ticker
    def test_get_ticker_layer1(self):
        """Test _get_ticker returns correct tuple."""
        path, params, extra_data = self.feed._get_ticker("BTCUSDT")
        assert "market" in path.lower() or "tickers" in path.lower()
        assert params["symbol"] == "BTCUSDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCUSDT"
        assert extra_data["normalize_function"] is not None

    @pytest.mark.orderbook
    def test_get_depth_layer1(self):
        """Test _get_depth returns correct tuple."""
        path, params, extra_data = self.feed._get_depth("BTCUSDT", limit=25)
        assert params["symbol"] == "BTCUSDT"
        assert params["limit"] == 25
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.kline
    def test_get_kline_layer1(self):
        """Test _get_kline returns correct tuple."""
        path, params, extra_data = self.feed._get_kline("BTCUSDT", period="1h", limit=100)
        assert params["symbol"] == "BTCUSDT"
        assert params["interval"] == "60"  # 1h → 60
        assert params["limit"] == 100
        assert extra_data["request_type"] == "get_kline"

    def test_get_balance_layer1(self):
        """Test _get_balance returns correct tuple."""
        path, params, extra_data = self.feed._get_balance(account_type="UNIFIED", coin="BTC")
        assert params["accountType"] == "UNIFIED"
        assert params["coin"] == "BTC"
        assert extra_data["request_type"] == "get_balance"

    def test_make_order_layer1(self):
        """Test _make_order returns correct tuple."""
        path, body, extra_data = self.feed._make_order(
            "BTCUSDT", qty=0.001, side="Buy", order_type="Limit", price=50000
        )
        assert body["category"] == "spot"
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "Buy"
        assert body["orderType"] == "Limit"
        assert body["qty"] == "0.001"
        assert body["price"] == "50000"
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_layer1(self):
        """Test _cancel_order returns correct tuple."""
        path, body, extra_data = self.feed._cancel_order("BTCUSDT", order_id="123456")
        assert body["category"] == "spot"
        assert body["symbol"] == "BTCUSDT"
        assert body["orderId"] == "123456"

    def test_query_order_layer1(self):
        """Test _query_order returns correct tuple."""
        path, params, extra_data = self.feed._query_order("BTCUSDT", order_id="123456")
        assert params["category"] == "spot"
        assert params["symbol"] == "BTCUSDT"
        assert params["orderId"] == "123456"


# ===================== Normalize Function Tests =====================


class TestBybitNormalizeFunctions:
    """Test normalize functions produce correct data containers."""

    @pytest.mark.ticker
    def test_ticker_normalize(self):
        """Test ticker normalize function."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        input_data = {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "50000",
                        "bid1Price": "49990",
                        "ask1Price": "50010",
                    }
                ]
            },
        }
        extra_data = {"symbol_name": "BTCUSDT", "asset_type": "spot"}
        data, status = BybitRequestDataSpot._get_ticker_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BybitSpotTickerData)

    @pytest.mark.ticker
    def test_ticker_normalize_error(self):
        """Test ticker normalize with retCode != 0."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        input_data = {"retCode": 10001, "retMsg": "Error"}
        extra_data = {"symbol_name": "BTCUSDT", "asset_type": "spot"}
        data, status = BybitRequestDataSpot._get_ticker_normalize_function(input_data, extra_data)
        assert status is False

    @pytest.mark.ticker
    def test_ticker_normalize_none(self):
        """Test ticker normalize with None input."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        extra_data = {"symbol_name": "BTCUSDT", "asset_type": "spot"}
        data, status = BybitRequestDataSpot._get_ticker_normalize_function(None, extra_data)
        assert status is False
        assert data == []

    @pytest.mark.orderbook
    def test_depth_normalize(self):
        """Test depth normalize function."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        input_data = {
            "retCode": 0,
            "result": {
                "b": [["49990", "1.5"]],
                "a": [["50010", "1.0"]],
                "ts": "12345",
            },
        }
        extra_data = {"symbol_name": "BTCUSDT", "asset_type": "spot"}
        data, status = BybitRequestDataSpot._get_depth_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BybitSpotOrderBookData)

    def test_balance_normalize(self):
        """Test balance normalize function."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        input_data = {
            "retCode": 0,
            "result": {"list": [{"accountType": "UNIFIED", "totalEquity": "10000", "coin": []}]},
        }
        extra_data = {"symbol_name": "ALL", "asset_type": "spot"}
        data, status = BybitRequestDataSpot._get_balance_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BybitSpotBalanceData)

    @pytest.mark.kline
    def test_kline_normalize(self):
        """Test kline normalize function."""
        from bt_api_py.feeds.live_bybit.spot import BybitRequestDataSpot

        input_data = {
            "retCode": 0,
            "result": {
                "list": [
                    ["1640995200000", "50000", "51000", "49000", "50500", "100", "5000000"],
                    ["1640995260000", "50500", "51500", "50000", "51000", "200", "10000000"],
                ]
            },
        }
        extra_data = {"symbol_name": "BTCUSDT", "asset_type": "spot"}
        data, status = BybitRequestDataSpot._get_kline_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 2


# ===================== Registration Tests =====================


class TestBybitRegistration:
    """Test Bybit registration."""

    def test_bybit_registered(self):
        """Test that Bybit is properly registered."""
        assert "BYBIT___SPOT" in ExchangeRegistry._feed_classes
        assert "BYBIT___SWAP" in ExchangeRegistry._feed_classes
        assert "BYBIT___SPOT" in ExchangeRegistry._exchange_data_classes
        assert "BYBIT___SWAP" in ExchangeRegistry._exchange_data_classes

    def test_bybit_create_exchange_data_spot(self):
        """Test creating Bybit spot exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BYBIT___SPOT")
        assert isinstance(exchange_data, BybitExchangeDataSpot)

    def test_bybit_create_exchange_data_swap(self):
        """Test creating Bybit swap exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BYBIT___SWAP")
        assert isinstance(exchange_data, BybitExchangeDataSwap)


# ===================== Signature Tests =====================


class TestBybitSignature:
    """Test Bybit HMAC SHA256 signature generation."""

    def test_signature_generation(self):
        """Test that signature is generated correctly."""
        from bt_api_py.feeds.live_bybit.request_base import BybitRequestData

        data_queue = queue.Queue()
        feed = BybitRequestData(
            data_queue,
            public_key="test_api_key",
            private_key="test_api_secret",
        )
        payload = "1234567890test_api_key5000symbol=BTCUSDT"
        sig = feed._generate_signature(payload)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex digest is 64 chars


# ===================== Integration Tests (skipped by default) =====================


class TestBybitIntegration:
    """Integration tests for Bybit (require network/API keys)."""

    @pytest.mark.skipif(True, reason="Integration tests require network")
    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    @pytest.mark.skipif(True, reason="Integration tests require API keys")
    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
