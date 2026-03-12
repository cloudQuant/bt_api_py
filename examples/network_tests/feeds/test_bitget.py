"""
Test Bitget exchange integration.

Run tests:
    pytest tests/feeds/test_bitget.py -v
"""

import queue

import pytest

# Import registration to auto-register Bitget
import bt_api_py.exchange_registers.register_bitget  # noqa: F401
from bt_api_py.containers.balances.bitget_balance import BitgetBalanceData
from bt_api_py.containers.exchanges.bitget_exchange_data import (
    BitgetExchangeDataSpot,
    BitgetExchangeDataSwap,
)
from bt_api_py.containers.orderbooks.bitget_orderbook import BitgetOrderBookData
from bt_api_py.containers.orders.bitget_order import BitgetOrderData
from bt_api_py.containers.tickers.bitget_ticker import BitgetTickerData
from bt_api_py.registry import ExchangeRegistry

# ===================== Exchange Data Tests =====================


class TestBitgetExchangeData:
    """Test Bitget exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BitgetExchangeDataSpot()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert len(exchange_data.rest_paths) > 0

    def test_exchange_data_swap_creation(self):
        exchange_data = BitgetExchangeDataSwap()
        assert exchange_data.asset_type == "swap"
        assert exchange_data.rest_url != ""
        assert len(exchange_data.rest_paths) > 0

    def test_get_symbol(self):
        exchange_data = BitgetExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USDT") == "BTCUSDT"
        assert exchange_data.get_symbol("ETH-USDT") == "ETHUSDT"

    def test_get_period(self):
        exchange_data = BitgetExchangeDataSpot()
        # Bitget period converts via kline_periods mapping from YAML config
        assert exchange_data.get_period("1m") == "1min"
        assert exchange_data.get_period("1h") == "1h"

    def test_get_rest_path(self):
        exchange_data = BitgetExchangeDataSpot()
        path = exchange_data.get_rest_path("get_tick")
        assert path != ""
        assert "tickers" in path.lower() or "market" in path.lower()

        path = exchange_data.get_rest_path("get_depth")
        assert path != ""

        path = exchange_data.get_rest_path("get_kline")
        assert path != ""

    @pytest.mark.kline
    def test_kline_periods(self):
        exchange_data = BitgetExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods

    def test_legal_currencies(self):
        exchange_data = BitgetExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency


# ===================== Data Container Tests =====================


class TestBitgetDataContainers:
    """Test Bitget data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker_data = {
            "symbol": "BTCUSDT",
            "last": "50000",
            "bidPx": "49990",
            "askPx": "50010",
            "bidSz": "1.5",
            "askSz": "2.0",
            "high24h": "51000",
            "low24h": "49000",
            "volume24h": "1234.56",
            "turnover24h": "12345678",
            "ts": "1640995200000",
        }
        ticker = BitgetTickerData(ticker_data, "BTC-USDT", "spot", True)
        result = ticker.init_data()
        assert result is ticker  # init_data returns self
        assert ticker.exchange_name == "BITGET"
        assert ticker.symbol_name == "BTC-USDT"
        assert ticker.asset_type == "spot"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49990.0
        assert ticker.ask_price == 50010.0
        assert ticker.bid_volume == 1.5
        assert ticker.ask_volume == 2.0
        assert ticker.has_been_init_data is True

    @pytest.mark.ticker
    def test_ticker_init_data_returns_self(self):
        ticker_data = {"symbol": "BTCUSDT", "last": "50000"}
        ticker = BitgetTickerData(ticker_data, "BTC-USDT", "spot", True)
        assert ticker.init_data() is ticker

    @pytest.mark.orderbook
    def test_orderbook_container(self):
        orderbook_data = {
            "bids": [["49990", "1.5"], ["49980", "2.0"]],
            "asks": [["50010", "1.0"], ["50020", "2.5"]],
            "ts": "1640995200000",
        }
        ob = BitgetOrderBookData(orderbook_data, "BTC-USDT", "spot", True)
        result = ob.init_data()
        assert result is ob  # init_data returns self
        assert ob.exchange_name == "BITGET"
        assert ob.symbol_name == "BTC-USDT"
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.bids[0][0] == 49990.0
        assert ob.asks[0][0] == 50010.0

    @pytest.mark.orderbook
    def test_orderbook_init_data_returns_self(self):
        ob_data = {"bids": [], "asks": [], "ts": "123"}
        ob = BitgetOrderBookData(ob_data, "BTC-USDT", "spot", True)
        assert ob.init_data() is ob

    def test_order_container(self):
        order_data = {
            "orderId": "123456789",
            "clientOid": "client_123",
            "symbol": "BTCUSDT",
            "side": "buy",
            "orderType": "limit",
            "status": "filled",
            "size": "0.001",
            "price": "50000",
        }
        order = BitgetOrderData(order_data, "BTC-USDT", "spot", True)
        result = order.init_data()
        assert result is order  # init_data returns self
        assert order.exchange_name == "BITGET"
        assert order.order_id == "123456789"
        assert order.client_order_id == "client_123"
        assert order.side == "buy"
        assert order.order_type == "limit"

    def test_order_init_data_returns_self(self):
        order_data = {"orderId": "1"}
        order = BitgetOrderData(order_data, "BTC-USDT", "spot", True)
        assert order.init_data() is order

    def test_balance_container(self):
        balance_data = {
            "coin": "BTC",
            "available": "0.5",
            "frozen": "0.1",
            "stored": "0.0",
            "usdValue": "25000",
        }
        balance = BitgetBalanceData(balance_data, "ALL", "spot", True)
        result = balance.init_data()
        assert result is balance  # init_data returns self
        assert balance.exchange_name == "BITGET"
        assert balance.coin == "BTC"
        assert balance.available == 0.5
        assert balance.frozen == 0.1

    def test_balance_init_data_returns_self(self):
        balance_data = {"coin": "ETH"}
        b = BitgetBalanceData(balance_data, "ALL", "spot", True)
        assert b.init_data() is b


# ===================== Feed Creation Tests =====================


class TestBitgetFeedCreation:
    """Test Bitget feed creation."""

    def test_spot_feed_creation(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        data_queue = queue.Queue()
        feed = BitgetRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            passphrase="test_pass",
        )
        assert feed.asset_type == "spot"
        assert feed.public_key == "test_key"
        assert feed.private_key == "test_secret"
        assert feed.passphrase == "test_pass"
        assert isinstance(feed._params, BitgetExchangeDataSpot)

    def test_swap_feed_creation(self):
        from bt_api_py.feeds.live_bitget.swap import BitgetRequestDataSwap

        data_queue = queue.Queue()
        feed = BitgetRequestDataSwap(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            passphrase="test_pass",
        )
        assert feed.asset_type == "swap"
        assert isinstance(feed._params, BitgetExchangeDataSwap)

    def test_spot_three_layer_methods_exist(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        data_queue = queue.Queue()
        feed = BitgetRequestDataSpot(
            data_queue,
            public_key="k",
            private_key="s",
            passphrase="p",
        )
        # _get_xxx
        assert hasattr(feed, "_get_ticker")
        assert hasattr(feed, "_get_depth")
        assert hasattr(feed, "_get_kline")
        assert hasattr(feed, "_get_balance")
        assert hasattr(feed, "_make_order")
        assert hasattr(feed, "_cancel_order")
        assert hasattr(feed, "_query_order")
        assert hasattr(feed, "_get_deals")
        # get_xxx
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
        # async_get_xxx
        assert hasattr(feed, "async_get_ticker")
        assert hasattr(feed, "async_get_tick")
        assert hasattr(feed, "async_get_depth")
        assert hasattr(feed, "async_get_kline")
        assert hasattr(feed, "async_get_balance")
        assert hasattr(feed, "async_make_order")

    def test_swap_three_layer_methods_exist(self):
        from bt_api_py.feeds.live_bitget.swap import BitgetRequestDataSwap

        data_queue = queue.Queue()
        feed = BitgetRequestDataSwap(
            data_queue,
            public_key="k",
            private_key="s",
            passphrase="p",
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


class TestBitgetThreeLayerPattern:
    """Test the _get_xxx / get_xxx / async_get_xxx pattern."""

    def setup_method(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        self.data_queue = queue.Queue()
        self.feed = BitgetRequestDataSpot(
            self.data_queue,
            public_key="test_key",
            private_key="test_secret",
            passphrase="test_pass",
        )

    @pytest.mark.ticker
    def test_get_ticker_layer1(self):
        path, params, extra_data = self.feed._get_ticker("BTC-USDT")
        assert "tickers" in path.lower() or "market" in path.lower()
        assert params["symbol"] == "BTCUSDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-USDT"
        assert extra_data["normalize_function"] is not None

    @pytest.mark.orderbook
    def test_get_depth_layer1(self):
        path, params, extra_data = self.feed._get_depth("BTC-USDT", limit=20)
        assert params["symbol"] == "BTCUSDT"
        assert params["limit"] == 20
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.kline
    def test_get_kline_layer1(self):
        path, params, extra_data = self.feed._get_kline("BTC-USDT", period="1h", limit=100)
        assert params["symbol"] == "BTCUSDT"
        assert params["granularity"] == "1h"
        assert params["limit"] == 100
        assert extra_data["request_type"] == "get_kline"

    def test_get_balance_layer1(self):
        path, params, extra_data = self.feed._get_balance()
        assert extra_data["request_type"] == "get_balance"
        assert extra_data["normalize_function"] is not None

    def test_make_order_layer1(self):
        path, body, extra_data = self.feed._make_order(
            "BTC-USDT", vol=1, price=50000, order_type="buy-limit", client_order_id="test_order_123"
        )
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "BUY"
        assert body["orderType"] == "LIMIT"
        assert body["size"] == 1
        assert body["price"] == 50000
        assert body["clientOid"] == "test_order_123"
        assert extra_data["request_type"] == "make_order"

    def test_make_market_order_layer1(self):
        path, body, extra_data = self.feed._make_order("ETH-USDT", vol=10, order_type="sell-market")
        assert body["symbol"] == "ETHUSDT"
        assert body["side"] == "SELL"
        assert body["orderType"] == "MARKET"
        assert "price" not in body

    def test_cancel_order_layer1(self):
        path, body, extra_data = self.feed._cancel_order("BTC-USDT", order_id="123456")
        assert body["symbol"] == "BTCUSDT"
        assert body["orderId"] == "123456"
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_layer1(self):
        path, params, extra_data = self.feed._query_order("BTC-USDT", order_id="123456")
        assert params["symbol"] == "BTCUSDT"
        assert params["orderId"] == "123456"
        assert extra_data["request_type"] == "query_order"

    def test_get_deals_layer1(self):
        path, params, extra_data = self.feed._get_deals("BTC-USDT", limit=20)
        assert params["limit"] == 20
        assert "BTCUSDT" in str(params.get("symbol", ""))
        assert extra_data["request_type"] == "get_deals"


# ===================== Normalize Function Tests =====================


class TestBitgetNormalizeFunctions:
    """Test normalize functions produce correct data containers."""

    @pytest.mark.ticker
    def test_ticker_normalize(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {
            "code": "00000",
            "data": [{"symbol": "BTCUSDT", "last": "50000", "bidPx": "49990", "askPx": "50010"}],
        }
        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._get_ticker_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BitgetTickerData)

    @pytest.mark.ticker
    def test_ticker_normalize_error(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {"code": "40001", "msg": "Error"}
        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._get_ticker_normalize_function(input_data, extra_data)
        assert status is False

    @pytest.mark.ticker
    def test_ticker_normalize_none(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._get_ticker_normalize_function(None, extra_data)
        assert status is False
        assert data == []

    @pytest.mark.orderbook
    def test_depth_normalize(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {
            "code": "00000",
            "data": {
                "bids": [["49990", "1.5"]],
                "asks": [["50010", "1.0"]],
                "ts": "12345",
            },
        }
        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._get_depth_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BitgetOrderBookData)

    def test_balance_normalize(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {
            "code": "00000",
            "data": [{"coin": "BTC", "available": "0.5", "frozen": "0.1"}],
        }
        extra_data = {"symbol_name": "ALL", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._get_balance_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BitgetBalanceData)

    def test_make_order_normalize(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {
            "code": "00000",
            "data": {"orderId": "123456", "clientOid": "abc"},
        }
        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._make_order_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BitgetOrderData)

    def test_query_order_normalize(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {
            "code": "00000",
            "data": [{"orderId": "123", "symbol": "BTCUSDT", "side": "buy"}],
        }
        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._query_order_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], BitgetOrderData)

    @pytest.mark.kline
    def test_kline_normalize(self):
        from bt_api_py.feeds.live_bitget.spot import BitgetRequestDataSpot

        input_data = {
            "code": "00000",
            "data": [
                ["1640995200000", "50000", "51000", "49000", "50500", "100"],
                ["1640995260000", "50500", "51500", "50000", "51000", "200"],
            ],
        }
        extra_data = {"symbol_name": "BTC-USDT", "asset_type": "spot"}
        data, status = BitgetRequestDataSpot._get_kline_normalize_function(input_data, extra_data)
        assert status is True
        assert len(data) == 2

    def test_extract_data_normalize_function(self):
        from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData

        input_data = {"code": "00000", "data": {"serverTime": "1640995200000"}}
        data, status = BitgetRequestData._extract_data_normalize_function(input_data, {})
        assert status is True
        assert len(data) == 1

    def test_extract_data_normalize_list(self):
        from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData

        input_data = {"code": "00000", "data": [{"a": 1}, {"b": 2}]}
        data, status = BitgetRequestData._extract_data_normalize_function(input_data, {})
        assert status is True
        assert len(data) == 2

    def test_extract_data_normalize_error(self):
        from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData

        input_data = {"code": "40001", "msg": "error"}
        data, status = BitgetRequestData._extract_data_normalize_function(input_data, {})
        assert status is False


# ===================== Registration Tests =====================


class TestBitgetRegistration:
    """Test Bitget registration."""

    def test_bitget_spot_registered(self):
        assert "BITGET___SPOT" in ExchangeRegistry._feed_classes
        assert "BITGET___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITGET___SPOT"] == BitgetExchangeDataSpot

    def test_bitget_swap_registered(self):
        assert "BITGET___SWAP" in ExchangeRegistry._feed_classes
        assert "BITGET___SWAP" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITGET___SWAP"] == BitgetExchangeDataSwap

    def test_bitget_create_exchange_data_spot(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BITGET___SPOT")
        assert isinstance(exchange_data, BitgetExchangeDataSpot)

    def test_bitget_create_exchange_data_swap(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BITGET___SWAP")
        assert isinstance(exchange_data, BitgetExchangeDataSwap)


# ===================== Signature Tests =====================


class TestBitgetSignature:
    """Test Bitget HMAC SHA256 + Base64 signature generation."""

    def test_signature_generation(self):
        from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData

        data_queue = queue.Queue()
        feed = BitgetRequestData(
            data_queue,
            public_key="test_api_key",
            private_key="test_api_secret",
            passphrase="test_pass",
        )
        msg = "1234567890GET/api/v2/spot/market/tickers?symbol=BTCUSDT"
        sig = feed._generate_signature(msg)
        assert isinstance(sig, str)
        # Base64 encoded SHA256 HMAC is ~44 chars (not 64 like hex)
        assert len(sig) > 0

    def test_auth_headers(self):
        from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData

        data_queue = queue.Queue()
        feed = BitgetRequestData(
            data_queue,
            public_key="test_api_key",
            private_key="test_api_secret",
            passphrase="test_pass",
        )
        headers = feed._build_auth_headers("GET", "/api/v2/spot/account/assets")
        assert headers["ACCESS-KEY"] == "test_api_key"
        assert headers["ACCESS-PASSPHRASE"] == "test_pass"
        assert "ACCESS-SIGN" in headers
        assert "ACCESS-TIMESTAMP" in headers
        assert headers["Content-Type"] == "application/json"


# ===================== Integration Tests (skipped by default) =====================


class TestBitgetIntegration:
    """Integration tests for Bitget (require network/API keys)."""

    @pytest.mark.skipif(True, reason="Integration tests require network")
    def test_market_data_api(self):
        pass

    @pytest.mark.skipif(True, reason="Integration tests require API keys")
    def test_trading_api(self):
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
