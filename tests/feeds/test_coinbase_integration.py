"""
Coinbase Exchange Integration Tests

Tests for Coinbase spot trading implementation including:
    pass
- Configuration loading
- Exchange data container
- Request feed functionality
- Data containers (tickers, orders, trades, balances)
- Registration module
"""

import json
from queue import Queue

import pytest

from bt_api_py.containers.balances.coinbase_balance import CoinbaseBalanceData
from bt_api_py.containers.exchanges.coinbase_exchange_data import (
    CoinbaseExchangeData,
    CoinbaseExchangeDataSpot,
)
from bt_api_py.containers.orders.coinbase_order import (
    CoinbaseRequestOrderData,
    CoinbaseWssOrderData,
)
from bt_api_py.containers.tickers.coinbase_ticker import (
    CoinbaseRequestTickerData,
    CoinbaseWssTickerData,
)
from bt_api_py.containers.trades.coinbase_trade import (
    CoinbaseRequestTradeData,
)
from bt_api_py.feeds.live_coinbase.spot import CoinbaseRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


class TestCoinbaseExchangeData:
    """Test Coinbase exchange data container"""

    def test_exchange_data_base_initialization(self):
        """Test base CoinbaseExchangeData initialization"""
        data = CoinbaseExchangeData()
        assert data.exchange_name == "COINBASE___SPOT"
        assert data.rest_url == "https://api.coinbase.com/api/v3"
        assert isinstance(data.kline_periods, dict)
        assert "1m" in data.kline_periods
        assert data.kline_periods["1m"] == "ONE_MINUTE"

    def test_exchange_data_spot_initialization(self):
        """Test CoinbaseExchangeDataSpot initialization"""
        data = CoinbaseExchangeDataSpot()
        assert data.exchange_name == "COINBASE___SPOT"
        assert "USD" in data.legal_currency

    def test_get_symbol_conversion(self):
        """Test symbol format conversion"""
        data = CoinbaseExchangeDataSpot()
        # Coinbase uses BASE-QUOTE format natively
        symbol = data.get_symbol("BTC-USD")
        # Coinbase keeps the dash in symbol format
        assert symbol == "BTC-USD"

    def test_get_period_conversion(self):
        """Test kline period conversion"""
        data = CoinbaseExchangeDataSpot()
        period = data.get_period("1m")
        assert period == "ONE_MINUTE"
        period = data.get_period("1h")
        assert period == "ONE_HOUR"


class TestCoinbaseTickerData:
    """Test Coinbase ticker data container"""

    def test_request_ticker_data_init(self):
        """Test REST API ticker data initialization"""
        ticker_response = {
            "product_id": "BTC-USD",
            "price": "50000.00",
            "best_bid": "49999.00",
            "best_ask": "50001.00",
            "volume_24h": "1000.00",
            "price_percentage_change_24h": "2.5",
            "time": "2023-07-06T12:34:56Z",
        }
        ticker = CoinbaseRequestTickerData(json.dumps(ticker_response), "BTC-USD", "SPOT", True)
        assert ticker.get_exchange_name() == "COINBASE"
        assert ticker.get_symbol_name() == "BTC-USD"
        ticker.init_data()
        # The ticker might use "price" or "last_trade" for last price
        last_price = ticker.get_last_price()
        bid_price = ticker.get_bid_price()
        ask_price = ticker.get_ask_price()
        # At least some data should be parsed
        assert last_price is not None or bid_price is not None
        if last_price is not None:
            assert last_price == 50000.00
            if bid_price is not None:
                pass
            assert bid_price == 49999.00
            if ask_price is not None:
                pass
            assert ask_price == 50001.00

    def test_wss_ticker_data_init(self):
        """Test WebSocket ticker data initialization"""
        ticker_response = {
            "type": "ticker",
            "product_id": "BTC-USD",
            "price": "50000.00",
            "volume_24h": "1000.00",
            "time": "2023-07-06T12:34:56Z",
        }
        ticker = CoinbaseWssTickerData(json.dumps(ticker_response), "BTC-USD", "SPOT", True)
        ticker.init_data()
        assert ticker.get_last_price() == 50000.00


class TestCoinbaseOrderData:
    """Test Coinbase order data container"""

    def test_request_order_data_init(self):
        """Test REST API order data initialization"""
        order_response = {
            "order_id": "11111111-1111-1111-1111-111111111111",
            "product_id": "BTC-USD",
            "side": "BUY",
            "status": "FILLED",
            "price": "50000.00",
            "size": "0.001",
            "filled_size": "0.001",
            "created_time": "2023-07-06T12:34:56Z",
        }
        order = CoinbaseRequestOrderData(json.dumps(order_response), "BTC-USD", "SPOT", True)
        assert order.get_exchange_name() == "COINBASE"
        order.init_data()
        assert order.get_order_id() == "11111111-1111-1111-1111-111111111111"
        assert order.get_side() == "BUY"
        assert order.get_status() == "FILLED"
        assert order.get_price() == 50000.00

    def test_wss_order_data_init(self):
        """Test WebSocket order data initialization"""
        order_response = {
            "order_id": "22222222-2222-2222-2222-222222222222",
            "product_id": "BTC-USD",
            "side": "SELL",
            "type": "limit",
            "status": "OPEN",
            "price": "51000.00",
            "size": "0.002",
        }
        order = CoinbaseWssOrderData(json.dumps(order_response), "BTC-USD", "SPOT", True)
        order.init_data()
        assert order.get_order_id() == "22222222-2222-2222-2222-222222222222"
        assert order.get_order_type() == "limit"


class TestCoinbaseTradeData:
    """Test Coinbase trade data container"""

    def test_request_trade_data_init(self):
        """Test REST API trade data initialization"""
        trade_response = {
            "entry_id": "fill-id-1",
            "order_id": "11111111-1111-1111-1111-111111111111",
            "product_id": "BTC-USD",
            "trade_type": "FILL",
            "side": "BUY",
            "price": "50000.00",
            "size": "0.001",
            "commission": "0.50",
            "trade_time": "2023-07-06T12:34:56Z",
            "liquidity_indicator": "TAKER",
        }
        trade = CoinbaseRequestTradeData(json.dumps(trade_response), "BTC-USD", "SPOT", True)
        assert trade.get_exchange_name() == "COINBASE"
        trade.init_data()
        assert trade.get_trade_id() == "fill-id-1"
        assert trade.get_side() == "BUY"
        assert trade.get_price() == 50000.00
        assert trade.get_liquidity_indicator() == "TAKER"


class TestCoinbaseBalanceData:
    """Test Coinbase balance data container"""

    def test_balance_data_init(self):
        """Test balance data initialization"""
        balance_response = {
            "uuid": "account-uuid-1",
            "currency": "BTC",
            "available_balance": {"value": "1.00000000", "currency": "BTC"},
            "hold": {"value": "0.00000000", "currency": "BTC"},
        }
        balance = CoinbaseBalanceData(json.dumps(balance_response), "SPOT", True)
        assert balance.get_exchange_name() == "COINBASE"
        balance.init_data()
        assert balance.get_currency() == "BTC"
        assert balance.get_available() == 1.0


class TestCoinbaseRequestDataSpot:
    """Test Coinbase spot request feed"""

    def test_request_data_initialization(self):
        """Test request data feed initialization"""
        data_queue = Queue()
        feed = CoinbaseRequestDataSpot(data_queue, api_key="test_key", private_key="test_secret")
        assert feed.exchange_name == "COINBASE___SPOT"
        assert feed.asset_type == "SPOT"

    def test_make_order_limit(self):
        """Test limit order creation"""
        data_queue = Queue()
        feed = CoinbaseRequestDataSpot(data_queue, api_key="test_key", private_key="test_secret")
        # Test _make_order method (internal)
        path, params, extra_data = feed._make_order(
            symbol="BTC-USD", vol="0.001", price="50000", order_type="buy-limit"
        )
        assert path is not None
        assert params["side"] == "BUY"
        assert "product_id" in params
        assert "order_configuration" in params

    def test_make_order_market(self):
        """Test market order creation"""
        data_queue = Queue()
        feed = CoinbaseRequestDataSpot(data_queue, api_key="test_key", private_key="test_secret")
        path, params, extra_data = feed._make_order(
            symbol="BTC-USD", vol="100", order_type="buy-market"
        )
        assert params["side"] == "BUY"
        assert "order_configuration" in params

    def test_cancel_order(self):
        """Test order cancellation"""
        data_queue = Queue()
        feed = CoinbaseRequestDataSpot(data_queue, api_key="test_key", private_key="test_secret")
        path, params, extra_data = feed._cancel_order(symbol="BTC-USD", order_id="test-order-id")
        assert "order_ids" in params
        assert "test-order-id" in params["order_ids"]


class TestCoinbaseRegistration:
    """Test Coinbase registration module"""

    def test_registration(self):
        """Test that Coinbase registration works"""
        # Import to trigger registration
        import bt_api_py.exchange_registers.register_coinbase  # noqa: F401

        # Check that exchange is registered
        assert ExchangeRegistry.has_exchange("COINBASE___SPOT")

        # Check that feed is registered
        feed_class = ExchangeRegistry._feed_classes.get("COINBASE___SPOT")
        assert feed_class is not None

        # Check that exchange data is registered
        data_class = ExchangeRegistry._exchange_data_classes.get("COINBASE___SPOT")
        assert data_class is not None

        # Check that balance handler is registered
        handler = ExchangeRegistry._balance_handlers.get("COINBASE___SPOT")
        assert handler is not None

    def test_get_registered_feed(self):
        """Test getting registered feed class"""
        import bt_api_py.exchange_registers.register_coinbase  # noqa: F401

        # Use create_feed to get an instance
        data_queue = Queue()
        feed = ExchangeRegistry.create_feed(
            "COINBASE___SPOT", data_queue, api_key="test_key", private_key="test_secret"
        )
        assert feed is not None
        assert isinstance(feed, CoinbaseRequestDataSpot)

    def test_get_registered_exchange_data(self):
        """Test getting registered exchange data class"""
        import bt_api_py.exchange_registers.register_coinbase  # noqa: F401

        # Use create_exchange_data to get an instance
        data = ExchangeRegistry.create_exchange_data("COINBASE___SPOT")
        assert data is not None
        assert isinstance(data, CoinbaseExchangeDataSpot)


class TestCoinbaseConfig:
    """Test Coinbase configuration loading"""

    def test_config_loading(self):
        """Test that config can be loaded"""
        data = CoinbaseExchangeDataSpot()
        # After loading from config
        assert data.exchange_name == "COINBASE___SPOT"
        assert isinstance(data.rest_paths, dict)
        assert len(data.rest_paths) > 0

    def test_rest_paths_exist(self):
        """Test that required REST paths are defined"""
        data = CoinbaseExchangeDataSpot()
        required_paths = [
            "make_order",
            "cancel_order",
            "get_account",
            "get_balance",
        ]
        for path_key in required_paths:
            assert path_key in data.rest_paths
            assert data.rest_paths[path_key] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
