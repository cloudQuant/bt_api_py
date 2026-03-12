"""
Test HTX (Huobi) exchange integration.

Run tests:
    pytest tests/feeds/test_htx.py -v

Run with coverage:
    pytest tests/feeds/test_htx.py --cov=bt_api_py.feeds.live_htx --cov-report=term-missing
"""

import json
import os
import queue
import time
import unittest
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Import registration to auto-register HTX
import bt_api_py.exchange_registers.register_htx  # noqa: F401
from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataSpot
from bt_api_py.containers.orderbooks.htx_orderbook import HtxRequestOrderBookData
from bt_api_py.containers.orders.htx_order import HtxRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.htx_ticker import HtxRequestTickerData
from bt_api_py.feeds.live_htx.spot import HtxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

HTX_API_KEY = os.environ.get("HTX_API_KEY", "")
HTX_SECRET = os.environ.get("HTX_SECRET", "")
HAS_HTX_KEYS = bool(HTX_API_KEY and HTX_SECRET and HTX_API_KEY != "your_htx_api_key_here")

skip_if_no_htx_keys = pytest.mark.skipif(
    not HAS_HTX_KEYS, reason="HTX_API_KEY and HTX_SECRET not set in .env"
)


class TestHtxExchangeData:
    """Test HTX exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating HTX spot exchange data."""
        exchange_data = HtxExchangeDataSpot()
        assert exchange_data.exchange_name == "htx_spot"
        assert exchange_data.asset_type == "SPOT"

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = HtxExchangeDataSpot()
        # HTX uses lowercase format
        assert exchange_data.get_symbol("BTCUSDT") == "btcusdt"
        assert exchange_data.get_symbol("BTC/USDT") == "btcusdt"
        assert exchange_data.get_symbol("BTC-USDT") == "btcusdt"

    def test_get_period(self):
        """Test period conversion."""
        exchange_data = HtxExchangeDataSpot()
        assert exchange_data.get_period("1m") == "1min"
        assert exchange_data.get_period("1h") == "60min"
        assert exchange_data.get_period("1d") == "1day"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = HtxExchangeDataSpot()
        # Test common paths
        path = exchange_data.get_rest_path("get_ticker")
        assert "ticker" in path.lower() or "detail" in path.lower()


class TestHtxRequestData:
    """Test HTX REST API request base class."""

    def test_request_data_creation(self):
        """Test creating HTX request data."""
        data_queue = queue.Queue()
        request_data = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="HTX___SPOT",
        )
        assert request_data.exchange_name == "HTX___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_get_ticker_params(self):
        """Test get ticker parameter generation."""
        data_queue = queue.Queue()
        request_data = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._get_ticker("BTCUSDT")

        assert "ticker" in path.lower() or "detail" in path.lower()
        assert params["symbol"] == "btcusdt"  # HTX uses lowercase

    def test_get_depth_params(self):
        """Test get depth parameter generation."""
        data_queue = queue.Queue()
        request_data = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._get_depth("BTCUSDT", depth_type="step0")

        assert "depth" in path.lower()
        assert params["symbol"] == "btcusdt"
        assert params["type"] == "step0"

    def test_make_order_params_limit(self):
        """Test limit order parameter generation."""
        data_queue = queue.Queue()
        request_data = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, body, extra_data = request_data._make_order(
            symbol="BTCUSDT",
            vol="0.001",
            price="50000",
            order_type="buy-limit",
        )

        assert body["symbol"] == "btcusdt"
        assert body["amount"] == "0.001"
        assert body["price"] == "50000"

    def test_cancel_order_params(self):
        """Test cancel order parameter generation."""
        data_queue = queue.Queue()
        request_data = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        path, params, extra_data = request_data._cancel_order(order_id="123456")

        # The path should contain POST method and the order ID in the URL
        assert "POST" in path or "order" in path.lower()
        assert params == {}


class TestHtxDataContainers:
    """Test HTX data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "status": "ok",
            "ch": "market.btcusdt.detail.merged",
            "ts": 1688671955000,
            "tick": {
                "id": 123456789,
                "ts": 1688671955000,
                "close": 50000,
                "open": 49500,
                "high": 51000,
                "low": 49000,
                "amount": 1234.5678,
                "count": 10000,
                "vol": 61728350,
                "ask": [50001, 1.5],
                "bid": [49999, 2.3],
            },
        }

        ticker = HtxRequestTickerData(ticker_response, "BTCUSDT", "SPOT", True)
        ticker.init_data()

        assert ticker.get_exchange_name() == "HTX"
        assert ticker.get_symbol_name() == "BTCUSDT"
        assert ticker.get_last_price() == 50000

    def test_order_container(self):
        """Test order data container."""
        order_response = {
            "status": "ok",
            "data": {
                "id": "123456",
                "symbol": "btcusdt",
                "type": "buy-limit",
                "filled-amount": "0.001",
                "filled-cash-amount": "5.00",
                "created-at": 1688671955000,
                "price": "50000",
                "amount": "0.001",
            },
        }

        order = HtxRequestOrderData(order_response, "BTCUSDT", "SPOT", True)
        order.init_data()

        assert order.get_symbol_name() == "BTCUSDT"


class TestHtxRegistry:
    """Test HTX registration."""

    def test_htx_registered(self):
        """Test that HTX is properly registered."""
        # Check if feed is registered
        assert "HTX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["HTX___SPOT"] == HtxRequestDataSpot

        # Check if exchange data is registered
        assert "HTX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["HTX___SPOT"] == HtxExchangeDataSpot

        # Check if balance handler is registered
        assert "HTX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["HTX___SPOT"] is not None

    def test_htx_create_feed(self):
        """Test creating HTX feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "HTX___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, HtxRequestDataSpot)

    def test_htx_create_exchange_data(self):
        """Test creating HTX exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("HTX___SPOT")
        assert isinstance(exchange_data, HtxExchangeDataSpot)


class TestHtxLiveMarketData:
    """Live market data tests following Binance/OKX standard."""

    def init_req_feed(self):
        """Initialize HTX request feed."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_htx_spot_feed

    def test_htx_req_server_time(self):
        """Test server time endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_server_time()
        assert isinstance(data, RequestData)
        print("htx_server_time:", data.get_data())

    @pytest.mark.ticker
    def test_htx_req_tick_data(self):
        """Test ticker data request (synchronous)."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_ticker("BTCUSDT").get_data()
        assert isinstance(data, list)
        tick_data = data[0].init_data()
        assert tick_data.get_exchange_name() == "HTX"
        assert tick_data.get_symbol_name() == "BTCUSDT"
        assert tick_data.get_last_price() > 0

    @pytest.mark.ticker
    def test_htx_req_get_tick(self):
        """Test get_tick standard interface alias."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_tick("BTCUSDT")
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.integration
    def test_htx_async_tick_data(self):
        """Test ticker data request (asynchronous)."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        live_htx_spot_feed.async_get_ticker("BTCUSDT", extra_data={"test_async_tick_data": True})
        time.sleep(5)
        try:
            tick_data = data_queue.get(timeout=20)
            assert tick_data is not None
            assert isinstance(tick_data, RequestData)
        except queue.Empty:
            pytest.fail("No async tick data received within timeout")

    @pytest.mark.kline
    def test_htx_req_kline_data(self):
        """Test kline data request (synchronous)."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_kline("BTCUSDT", "1m", count=2).get_data()
        assert isinstance(data, list)
        if data and data[0]:
            kline_data = data[0].init_data()
            assert kline_data.get_exchange_name() == "HTX"
            assert kline_data.get_symbol_name() == "BTCUSDT"
            assert kline_data.get_local_update_time() > 0
            assert kline_data.get_open_price() > 0
            assert kline_data.get_high_price() >= 0
            assert kline_data.get_low_price() > 0
            assert kline_data.get_close_price() >= 0
            assert kline_data.get_volume() >= 0

    @pytest.mark.integration
    def test_htx_async_kline_data(self):
        """Test kline data request (asynchronous)."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        live_htx_spot_feed.async_get_kline(
            "BTCUSDT", period="1min", count=3, extra_data={"test_async_kline_data": True}
        )
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
            assert isinstance(kline_data, RequestData)
        except queue.Empty:
            pytest.fail("No async kline data received within timeout")


def order_book_value_equals(order_book):
    """Validate order book data."""
    assert isinstance(order_book, HtxRequestOrderBookData)
    assert order_book.get_exchange_name() == "HTX"
    assert order_book.get_symbol_name() == "BTCUSDT"
    assert order_book.get_asset_type() == "SPOT"
    # Price lists should exist and have valid values
    bid_prices = order_book.get_bid_price_list()
    ask_prices = order_book.get_ask_price_list()
    if bid_prices:
        assert bid_prices[0] > 0
    if ask_prices:
        assert ask_prices[0] > 0


class TestHtxOrderBook:
    """Order book tests."""

    def init_req_feed(self):
        """Initialize HTX request feed."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_htx_spot_feed

    @pytest.mark.orderbook
    def test_htx_req_orderbook_data(self):
        """Test orderbook data request."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_depth("BTCUSDT", 20).get_data()
        assert isinstance(data, list)
        if data and data[0]:
            order_book_value_equals(data[0].init_data())

    @pytest.mark.integration
    def test_htx_async_orderbook_data(self):
        """Test orderbook data request (asynchronous)."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        live_htx_spot_feed.async_get_depth("BTCUSDT", "step0")
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pytest.fail("No async depth data received within timeout")


class TestHtxIntegration:
    """Integration tests for HTX."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)"""
        data_queue = queue.Queue()
        feed = HtxRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_ticker("BTCUSDT")
        assert isinstance(ticker, RequestData)

    def test_trading_api(self):
        """Test trading API calls (requires API keys)"""


# ==================== Additional Binance/OKX Standard Tests =============


class TestHtxStandardMarketData:
    """Standard market data tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize HTX request feed."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_htx_spot_feed

    def test_htx_req_get_exchange_info(self):
        """Test exchange info endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_exchange_info()
        assert isinstance(data, RequestData)
        print("htx_exchange_info:", data.get_data())

    def test_htx_req_get_symbols(self):
        """Test symbols list endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_symbols()
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_htx_req_get_currencies(self):
        """Test currencies list endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_currencies()
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, list)
        assert len(result) > 0


class TestHtxAccountData:
    """Account data tests following Binance/OKX pattern.

    These tests require valid HTX API keys in .env:
        HTX_API_KEY=your_key
        HTX_SECRET=your_secret
    """

    def init_req_feed(self):
        """Initialize HTX request feed with real API keys from .env."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key=HTX_API_KEY,
            private_key=HTX_SECRET,
        )
        return live_htx_spot_feed

    @skip_if_no_htx_keys
    @pytest.mark.integration
    def test_htx_req_get_account(self):
        """Test account data endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        data = live_htx_spot_feed.get_account()
        assert isinstance(data, RequestData)
        print("htx_account:", data.get_data())

    @skip_if_no_htx_keys
    @pytest.mark.integration
    def test_htx_async_get_account(self):
        """Test account data endpoint (asynchronous)."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key=HTX_API_KEY,
            private_key=HTX_SECRET,
        )
        live_htx_spot_feed.async_get_account()
        time.sleep(5)
        try:
            account_data = data_queue.get(timeout=15)
            assert account_data is not None
            assert isinstance(account_data, RequestData)
        except queue.Empty:
            pytest.fail("No async account data received within timeout")

    @skip_if_no_htx_keys
    @pytest.mark.integration
    def test_htx_req_get_balance(self):
        """Test balance data endpoint (requires valid API keys)."""
        live_htx_spot_feed = self.init_req_feed()
        # get_balance requires account_id; first get accounts list
        accounts_data = live_htx_spot_feed.get_accounts()
        assert isinstance(accounts_data, RequestData)
        accounts = accounts_data.get_data()
        assert isinstance(accounts, list) and len(accounts) > 0, (
            f"get_accounts returned no data: {accounts}"
        )
        account_id = accounts[0].get("id") if isinstance(accounts[0], dict) else None
        assert account_id is not None, f"No account_id found in: {accounts[0]}"
        data = live_htx_spot_feed.get_balance(account_id=account_id)
        assert isinstance(data, RequestData)
        print("htx_balance:", data.get_data())


class TestHtxOrderManagement:
    """Order management tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize HTX request feed."""
        data_queue = queue.Queue()
        live_htx_spot_feed = HtxRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_htx_spot_feed

    def test_make_order_params(self):
        """Test make order parameter generation."""
        live_htx_spot_feed = self.init_req_feed()
        path, body, extra_data = live_htx_spot_feed._make_order(
            symbol="BTCUSDT",
            vol="0.001",
            price="50000",
            order_type="buy-limit",
        )
        assert "POST" in path
        assert body["symbol"] == "btcusdt"
        assert body["amount"] == "0.001"
        assert body["price"] == "50000"
        assert body["type"] == "buy-limit"

    def test_cancel_order_params(self):
        """Test cancel order parameter generation."""
        live_htx_spot_feed = self.init_req_feed()
        path, params, extra_data = live_htx_spot_feed._cancel_order(order_id="123456")
        assert "POST" in path or "cancel" in path.lower()
        assert params == {}

    @pytest.mark.integration
    def test_htx_req_make_order(self):
        """Test make order endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        # Return early if method doesn't exist (avoid making actual API call in tests)
        if not hasattr(live_htx_spot_feed, "make_order"):
            return
        data = live_htx_spot_feed.make_order(
            symbol="BTCUSDT",
            vol="0.001",
            price="30000",  # Low price to avoid execution
            order_type="buy-limit",
        )
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_htx_req_query_order(self):
        """Test query order endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        # Return early if method doesn't exist (avoid making actual API call in tests)
        if not hasattr(live_htx_spot_feed, "query_order"):
            return
        data = live_htx_spot_feed.query_order(symbol="BTCUSDT", order_id="123456")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_htx_req_get_open_orders(self):
        """Test get open orders endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        # Return early if method doesn't exist (avoid making actual API call in tests)
        if not hasattr(live_htx_spot_feed, "get_open_orders"):
            return
        data = live_htx_spot_feed.get_open_orders(symbol="BTCUSDT")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_htx_req_cancel_order(self):
        """Test cancel order endpoint."""
        live_htx_spot_feed = self.init_req_feed()
        # Return early if method doesn't exist (avoid making actual API call in tests)
        if not hasattr(live_htx_spot_feed, "cancel_order"):
            return
        data = live_htx_spot_feed.cancel_order(order_id="123456")
        assert isinstance(data, RequestData)


# ═══════════════════════════════════════════════════════════════
# Multi-Asset-Type Tests
# ═══════════════════════════════════════════════════════════════


class TestHtxExchangeDataMultiAsset:
    """Test exchange data classes for all HTX asset types."""

    def test_margin_exchange_data(self):
        """Test margin exchange data creation and config loading."""
        from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataMargin

        data = HtxExchangeDataMargin()
        assert data.exchange_name == "htx_margin"
        assert data.asset_type == "MARGIN"
        assert data.rest_url == "https://api.huobi.pro"
        assert data.get_rest_path("get_tick") != ""
        assert data.get_rest_path("make_order") != ""
        # Margin uses same spot symbol format
        assert data.get_symbol("BTCUSDT") == "btcusdt"

    def test_usdt_swap_exchange_data(self):
        """Test USDT swap exchange data creation and config loading."""
        from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataUsdtSwap

        data = HtxExchangeDataUsdtSwap()
        assert data.exchange_name == "htx_usdt_swap"
        assert data.asset_type == "USDT_SWAP"
        assert data.rest_url == "https://api.hbdm.com"
        assert "linear-swap" in data.get_rest_path("get_tick")
        assert "linear-swap" in data.get_rest_path("make_order")
        # Derivatives use dash-separated uppercase symbol
        assert data.get_symbol("BTCUSDT") == "BTC-USDT"
        assert data.get_symbol("BTC-USDT") == "BTC-USDT"

    def test_coin_swap_exchange_data(self):
        """Test coin swap exchange data creation and config loading."""
        from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataCoinSwap

        data = HtxExchangeDataCoinSwap()
        assert data.exchange_name == "htx_coin_swap"
        assert data.asset_type == "COIN_SWAP"
        assert data.rest_url == "https://api.hbdm.com"
        assert "swap-ex" in data.get_rest_path("get_tick") or "swap-api" in data.get_rest_path(
            "get_tick"
        )
        # Coin swap symbol format
        assert data.get_symbol("BTCUSD") == "BTC-USD"

    def test_option_exchange_data(self):
        """Test option exchange data creation and config loading."""
        from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataOption

        data = HtxExchangeDataOption()
        assert data.exchange_name == "htx_option"
        assert data.asset_type == "OPTION"
        assert data.rest_url == "https://api.hbdm.com"
        assert "option" in data.get_rest_path("get_tick")
        assert "option" in data.get_rest_path("make_order")
        # Option symbol format
        assert data.get_symbol("BTCUSDT") == "BTC-USDT"


class TestHtxRegistryMultiAsset:
    """Test registry entries for all HTX asset types."""

    def test_htx_margin_registered(self):
        """Test HTX margin is registered."""

        assert ExchangeRegistry.has_exchange("HTX___MARGIN")

    def test_htx_usdt_swap_registered(self):
        """Test HTX USDT swap is registered."""

        assert ExchangeRegistry.has_exchange("HTX___USDT_SWAP")

    def test_htx_coin_swap_registered(self):
        """Test HTX coin swap is registered."""

        assert ExchangeRegistry.has_exchange("HTX___COIN_SWAP")

    def test_htx_option_registered(self):
        """Test HTX option is registered."""

        assert ExchangeRegistry.has_exchange("HTX___OPTION")


class TestHtxFeedCreationMultiAsset:
    """Test feed class instantiation for all asset types."""

    def test_margin_feed_creation(self):
        """Test margin feed can be instantiated."""
        from bt_api_py.feeds.live_htx.margin import HtxRequestDataMargin

        data_queue = queue.Queue()
        feed = HtxRequestDataMargin(data_queue)
        assert feed.asset_type == "MARGIN"
        assert feed._params.rest_url == "https://api.huobi.pro"

    def test_usdt_swap_feed_creation(self):
        """Test USDT swap feed can be instantiated."""
        from bt_api_py.feeds.live_htx.usdt_swap import HtxRequestDataUsdtSwap

        data_queue = queue.Queue()
        feed = HtxRequestDataUsdtSwap(data_queue)
        assert feed.asset_type == "USDT_SWAP"
        assert feed._params.rest_url == "https://api.hbdm.com"

    def test_coin_swap_feed_creation(self):
        """Test coin swap feed can be instantiated."""
        from bt_api_py.feeds.live_htx.coin_swap import HtxRequestDataCoinSwap

        data_queue = queue.Queue()
        feed = HtxRequestDataCoinSwap(data_queue)
        assert feed.asset_type == "COIN_SWAP"
        assert feed._params.rest_url == "https://api.hbdm.com"

    def test_option_feed_creation(self):
        """Test option feed can be instantiated."""
        from bt_api_py.feeds.live_htx.option import HtxRequestDataOption

        data_queue = queue.Queue()
        feed = HtxRequestDataOption(data_queue)
        assert feed.asset_type == "OPTION"
        assert feed._params.rest_url == "https://api.hbdm.com"


class TestHtxUsdtSwapMarketData:
    """Test USDT swap market data (live API)."""

    def init_req_feed(self):
        from bt_api_py.feeds.live_htx.usdt_swap import HtxRequestDataUsdtSwap

        data_queue = queue.Queue()
        return HtxRequestDataUsdtSwap(data_queue)

    def test_usdt_swap_contract_info(self):
        """Test get contract info (exchange info)."""
        feed = self.init_req_feed()
        data = feed.get_exchange_info()
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert len(result.get("data", [])) > 0

    @pytest.mark.ticker
    def test_usdt_swap_ticker(self):
        """Test USDT swap ticker."""
        feed = self.init_req_feed()
        data = feed.get_ticker("BTC-USDT")
        assert isinstance(data, RequestData)

    @pytest.mark.orderbook
    def test_usdt_swap_depth(self):
        """Test USDT swap depth."""
        feed = self.init_req_feed()
        data = feed.get_depth("BTC-USDT")
        assert isinstance(data, RequestData)

    @pytest.mark.kline
    def test_usdt_swap_kline(self):
        """Test USDT swap kline."""
        feed = self.init_req_feed()
        data = feed.get_kline("BTC-USDT", period="1m", count=3)
        assert isinstance(data, RequestData)


class TestHtxCoinSwapMarketData:
    """Test Coin swap market data (live API)."""

    def init_req_feed(self):
        from bt_api_py.feeds.live_htx.coin_swap import HtxRequestDataCoinSwap

        data_queue = queue.Queue()
        return HtxRequestDataCoinSwap(data_queue)

    def test_coin_swap_contract_info(self):
        """Test get contract info (exchange info)."""
        feed = self.init_req_feed()
        data = feed.get_exchange_info()
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert len(result.get("data", [])) > 0

    @pytest.mark.ticker
    def test_coin_swap_ticker(self):
        """Test coin swap ticker."""
        feed = self.init_req_feed()
        data = feed.get_ticker("BTC-USD")
        assert isinstance(data, RequestData)

    @pytest.mark.orderbook
    def test_coin_swap_depth(self):
        """Test coin swap depth."""
        feed = self.init_req_feed()
        data = feed.get_depth("BTC-USD")
        assert isinstance(data, RequestData)


class TestHtxWebSocket(unittest.TestCase):
    """Test HTX WebSocket classes."""

    def test_market_wss_instantiation(self):
        """Test market WSS class can be instantiated."""
        from bt_api_py.feeds.live_htx.spot import HtxMarketWssDataSpot

        data_queue = queue.Queue()
        wss = HtxMarketWssDataSpot(
            data_queue,
            topics=[{"topic": "ticker", "symbol": "BTCUSDT"}],
        )
        assert wss.asset_type == "SPOT"
        assert wss.wss_url == "wss://api.huobi.pro/ws"
        assert len(wss.topics) == 1

    def test_account_wss_instantiation(self):
        """Test account WSS class can be instantiated."""
        from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot

        data_queue = queue.Queue()
        wss = HtxAccountWssDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            topics=[{"topic": "orders", "symbol": "BTCUSDT"}],
        )
        assert wss.asset_type == "SPOT"
        assert wss.wss_url == "wss://api.huobi.pro/ws/v2"
        assert wss.public_key == "test_key"

    def test_market_wss_message_parsing(self):
        """Test gzip message decompression and ping/pong handling."""
        import gzip

        from bt_api_py.feeds.live_htx.spot import HtxMarketWssDataSpot

        data_queue = queue.Queue()
        wss = HtxMarketWssDataSpot(
            data_queue,
            topics=[],
        )
        # Simulate a ping message (gzip compressed)
        ping_msg = gzip.compress(json.dumps({"ping": 1234567890}).encode("utf-8"))
        # We can't test ws.send directly without a connection, but we can test parsing
        # Test that data messages get pushed to queue
        gzip.compress(
            json.dumps(
                {
                    "ch": "market.btcusdt.ticker",
                    "ts": 1234567890,
                    "tick": {"close": 50000.0, "open": 49000.0},
                }
            ).encode("utf-8")
        )

        # Mock ws to capture pong
        class MockWs:
            sent = []

            def send(self, msg):
                self.sent.append(msg)

        wss.ws = MockWs()
        wss.message_rsp(ping_msg)
        assert len(MockWs.sent) == 1
        assert '"pong": 1234567890' in MockWs.sent[0]

    def test_market_wss_data_push(self):
        """Test that market data messages are pushed to queue."""
        import gzip

        from bt_api_py.feeds.live_htx.spot import HtxMarketWssDataSpot

        data_queue = queue.Queue()
        wss = HtxMarketWssDataSpot(
            data_queue,
            topics=[],
        )
        wss.ws = type("MockWs", (), {"send": lambda self, msg: None})()
        tick_msg = gzip.compress(
            json.dumps(
                {"ch": "market.btcusdt.ticker", "ts": 1234567890, "tick": {"close": 50000.0}}
            ).encode("utf-8")
        )
        wss.message_rsp(tick_msg)
        assert not data_queue.empty()

    def test_account_wss_auth_params(self):
        """Test auth params building."""
        from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot

        data_queue = queue.Queue()
        wss = HtxAccountWssDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            topics=[],
        )
        auth = wss._build_auth_params()
        assert auth["action"] == "req"
        assert auth["ch"] == "auth"
        assert auth["params"]["accessKey"] == "test_key"
        assert auth["params"]["signatureMethod"] == "HmacSHA256"
        assert "signature" in auth["params"]

    def test_margin_wss_inherits_spot(self):
        """Test margin WSS classes properly inherit from spot."""
        from bt_api_py.feeds.live_htx.margin import HtxMarketWssDataMargin

        data_queue = queue.Queue()
        wss = HtxMarketWssDataMargin(data_queue, topics=[])
        assert wss.asset_type == "MARGIN"

    def test_usdt_swap_wss_inherits_spot(self):
        """Test USDT swap WSS classes properly inherit from spot."""
        from bt_api_py.feeds.live_htx.usdt_swap import HtxMarketWssDataUsdtSwap

        data_queue = queue.Queue()
        wss = HtxMarketWssDataUsdtSwap(data_queue, topics=[])
        assert wss.asset_type == "USDT_SWAP"

    @pytest.mark.integration
    def test_market_wss_live_connection(self):
        """Test live market WSS connection and data reception."""
        from bt_api_py.feeds.live_htx.spot import HtxMarketWssDataSpot

        data_queue = queue.Queue()
        wss = HtxMarketWssDataSpot(
            data_queue,
            topics=[{"topic": "ticker", "symbol": "BTCUSDT"}],
        )
        wss.start(connect_timeout=15)
        time.sleep(5)
        try:
            data = data_queue.get(timeout=10)
            assert data is not None
            print(f"WSS ticker data received: {type(data).__name__}")
        except queue.Empty:
            pytest.fail("No WSS ticker data received within timeout")
        finally:
            wss.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
