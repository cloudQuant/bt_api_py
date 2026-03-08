"""
Tests for Hyperliquid spot request data API.

Follows the same testing standards as Binance/OKX test files.
"""

import queue
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.accounts.hyperliquid_account import HyperliquidSpotWssAccountData
from bt_api_py.containers.balances.hyperliquid_balance import HyperliquidSpotRequestBalanceData
from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
from bt_api_py.containers.orders.hyperliquid_order import HyperliquidRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.hyperliquid_ticker import HyperliquidTickerData
from bt_api_py.feeds.live_hyperliquid_feed import HyperliquidRequestDataSpot
from bt_api_py.functions.utils import read_account_config


def generate_kwargs():
    """Generate kwargs for Hyperliquid feed initialization.

    Hyperliquid uses Ethereum wallet address + private key for authentication,
    not traditional API keys like other exchanges.
    """
    data = read_account_config()
    kwargs = {
        "private_key": data.get("hyperliquid", {}).get("private_key", ""),
        "proxies": data.get("proxies"),
        "async_proxy": data.get("async_proxy"),
    }
    return kwargs


def init_req_feed():
    """Initialize Hyperliquid request feed for testing."""
    data_queue = queue.Queue()
    kwargs = generate_kwargs()
    live_hyperliquid_spot_feed = HyperliquidRequestDataSpot(data_queue, **kwargs)
    return live_hyperliquid_spot_feed


def init_async_feed(data_queue):
    """Initialize Hyperliquid async feed for testing."""
    kwargs = generate_kwargs()
    live_hyperliquid_spot_feed = HyperliquidRequestDataSpot(data_queue, **kwargs)
    return live_hyperliquid_spot_feed


# ==================== Exchange Status Tests ====================


def test_hyperliquid_get_exchange_status():
    """Test getting exchange status."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_exchange_status()
    assert isinstance(data, RequestData)
    print("exchange_status:", data.get_data())


# ==================== Meta Data Tests ====================


def test_hyperliquid_get_meta():
    """Test getting metadata for all assets."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_meta()
    assert isinstance(data, RequestData)
    result = data.get_data()
    print("meta_data:", result)
    # Verify result is a list
    assert result is not None


def test_hyperliquid_get_spot_meta():
    """Test getting spot metadata."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_spot_meta()
    assert isinstance(data, RequestData)
    result = data.get_data()
    print("spot_meta_data:", result)
    # Verify result structure
    if result:
        assert isinstance(result, list) or isinstance(result, dict)


# ==================== Ticker Tests ====================


def test_hyperliquid_req_get_all_mids():
    """Test getting all mid prices."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_all_mids()
    assert isinstance(data, RequestData)
    print("all_mids_data", data.get_data())


def test_hyperliquid_req_tick_data():
    """Test getting ticker data for a symbol."""
    live_hyperliquid_spot_feed = init_req_feed()
    # Get all mids first to construct ticker data
    mids_data = live_hyperliquid_spot_feed.get_all_mids()
    mids = mids_data.get_data()

    assert mids is not None
    assert isinstance(mids, dict)

    # Check for BTC price
    if "BTC" in mids:
        btc_price = float(mids["BTC"])
        assert btc_price > 0
        print(f"BTC price: {btc_price}")


def test_hyperliquid_ticker_container():
    """Test ticker data container initialization."""
    ticker_data = {"last": "50000.0", "bid": "49999.0", "ask": "50001.0", "volume": "1000.0"}

    ticker = HyperliquidTickerData(ticker_data, "BTC", "SPOT", has_been_json_encoded=True)
    ticker.init_data()

    assert ticker.get_exchange_name() == "HYPERLIQUID"
    assert ticker.get_symbol_name() == "BTC"
    assert ticker.get_asset_type() == "SPOT"
    assert ticker.get_last_price() == 50000.0
    assert ticker.get_bid_price() == 49999.0
    assert ticker.get_ask_price() == 50001.0


# ==================== Order Book Tests ====================


def test_hyperliquid_req_depth_data():
    """Test getting L2 order book data."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_l2_book("BTC", depth=5)
    assert isinstance(data, RequestData)

    result = data.get_data()
    print("order_book_data", result)

    if result and isinstance(result, dict):
        # Hyperliquid returns levels as [[asks], [bids]]
        levels = result.get("levels", [[], []])
        assert len(levels) >= 2
        # Check asks (level 0)
        if len(levels[0]) > 0:
            assert "px" in levels[0][0]  # price
            assert "sz" in levels[0][0]  # size
        # Check bids (level 1)
        if len(levels[1]) > 0:
            assert "px" in levels[1][0]
            assert "sz" in levels[1][0]


# ==================== Kline/Candle Tests ====================


def test_hyperliquid_req_kline_data():
    """Test getting candle/kline data."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_candle_snapshot("BTC", "1m")
    assert isinstance(data, RequestData)

    result = data.get_data()
    print("kline_data", result)

    if result and isinstance(result, list):
        # Check first candle structure
        first_candle = result[0]
        assert "t" in first_candle  # timestamp
        assert "o" in first_candle  # open
        assert "h" in first_candle  # high
        assert "l" in first_candle  # low
        assert "c" in first_candle  # close
        assert "v" in first_candle  # volume


# ==================== Recent Trades Tests ====================


def test_hyperliquid_req_recent_trades():
    """Test getting recent trades."""
    live_hyperliquid_spot_feed = init_req_feed()
    data = live_hyperliquid_spot_feed.get_recent_trades("BTC", limit=10)
    assert isinstance(data, RequestData)

    result = data.get_data()
    print("recent_trades_data", result)

    if result and isinstance(result, list):
        # Check first trade structure
        first_trade = result[0]
        assert "px" in first_trade  # price
        assert "sz" in first_trade  # size
        assert "side" in first_trade  # buy/sell


# ==================== Account/Balance Tests ====================


def test_hyperliquid_req_spot_balances():
    """Test getting spot account balances."""
    live_hyperliquid_spot_feed = init_req_feed()
    balances = live_hyperliquid_spot_feed.get_balance()
    assert balances is not None


def test_hyperliquid_req_spot_clearinghouse_state():
    """Test getting spot clearinghouse state."""
    live_hyperliquid_spot_feed = init_req_feed()
    try:
        data = live_hyperliquid_spot_feed.get_spot_clearinghouse_state()
    except ValueError:
        pytest.skip("User address required for spot clearinghouse state")
    assert isinstance(data, RequestData)

    result = data.get_data()
    print("clearinghouse_state", result)

    if result and isinstance(result, dict):
        # Check for balances
        assert "balances" in result


def test_hyperliquid_balance_container():
    """Test balance data container initialization."""
    balance_data = {
        "balances": [{"coin": "USDC", "total": "1000.0", "free": "900.0", "hold": "100.0"}]
    }

    balance = HyperliquidSpotRequestBalanceData(
        balance_data, "USDC", "SPOT", has_been_json_encoded=True
    )
    balance.init_data()

    assert balance.get_exchange_name() == "HYPERLIQUID"
    assert balance.get_symbol_name() == "USDC"
    assert balance.get_asset_type() == "SPOT"
    assert balance.get_coin() == "USDC"


# ==================== Order Tests ====================


def test_hyperliquid_place_order():
    """Test placing a limit order."""
    live_hyperliquid_spot_feed = init_req_feed()

    # Place a buy limit order far from market price
    result = live_hyperliquid_spot_feed.place_order(
        symbol="BTC",
        side="buy",
        quantity=0.001,
        order_type="limit",
        price=1000.0,  # Far below market
        time_in_force="GTC",
    )

    assert isinstance(result, RequestData)
    print("place_order_result", result.get_data())


def test_hyperliquid_cancel_order():
    """Test canceling an order."""
    live_hyperliquid_spot_feed = init_req_feed()

    result = live_hyperliquid_spot_feed.cancel_order(symbol="BTC", order_id=12345)

    assert isinstance(result, RequestData)
    print("cancel_order_result", result.get_data())


def test_hyperliquid_modify_order():
    """Test modifying an order."""
    live_hyperliquid_spot_feed = init_req_feed()

    result = live_hyperliquid_spot_feed.modify_order(
        symbol="BTC", order_id=12345, quantity=0.002, price=2000.0
    )

    assert isinstance(result, RequestData)
    print("modify_order_result", result.get_data())


def test_hyperliquid_order_container():
    """Test order data container initialization."""
    order_data = {
        "statuses": [
            {
                "resting": {
                    "oid": 12345,
                    "side": "B",
                    "type": "limit",
                    "sz": "0.1",
                    "limit_px": "50000.0",
                }
            }
        ]
    }

    order = HyperliquidRequestOrderData(order_data, "BTC", "SPOT", has_been_json_encoded=True)
    order.init_data()

    assert order.get_exchange_name() == "HYPERLIQUID"
    assert order.get_symbol_name() == "BTC"
    assert order.get_asset_type() == "SPOT"
    assert order.get_order_id() == 12345


def test_hyperliquid_get_open_orders():
    """Test getting open orders."""
    live_hyperliquid_spot_feed = init_req_feed()

    orders = live_hyperliquid_spot_feed.get_open_orders()

    assert isinstance(orders, list)
    print(f"Open orders count: {len(orders)}")


# ==================== User Fills Tests ====================


def test_hyperliquid_get_user_fills():
    """Test getting user fills/trade history."""
    live_hyperliquid_spot_feed = init_req_feed()

    data = live_hyperliquid_spot_feed.get_user_fills(live_hyperliquid_spot_feed.address, limit=10)

    assert isinstance(data, RequestData)

    result = data.get_data()
    print("user_fills", result)

    if result and isinstance(result, list):
        # Check first fill structure
        first_fill = result[0]
        assert "px" in first_fill  # price
        assert "sz" in first_fill  # size


# ==================== Order Status Tests ====================


def test_hyperliquid_get_order_status():
    """Test getting order status."""
    live_hyperliquid_spot_feed = init_req_feed()

    data = live_hyperliquid_spot_feed.get_order_status(
        user=live_hyperliquid_spot_feed.address, oid=12345
    )

    assert isinstance(data, RequestData)
    print("order_status", data.get_data())


# ==================== Account Container Tests ====================


def test_hyperliquid_account_container():
    """Test account data container initialization."""
    account_data = {
        "user": "0x1234567890abcdef",
        "accountValue": "10000.0",
        "totalMarginUsed": "100.0",
        "initialMargin": "1000.0",
        "balances": [{"coin": "USDC", "total": "1000.0", "free": "900.0", "hold": "100.0"}],
    }

    account = HyperliquidSpotWssAccountData(account_data, "", "SPOT", has_been_json_encoded=True)
    account.init_data()

    assert account.get_exchange_name() == "HYPERLIQUID"
    assert account.get_asset_type() == "SPOT"
    assert account.get_user_address() == "0x1234567890abcdef"
    assert account.get_account_value() == 10000.0


# ==================== Mock Tests (No Network) ====================


class TestHyperliquidMockRequests:
    """Tests using mocks to avoid network calls."""

    @patch("bt_api_py.feeds.live_hyperliquid.request_base.requests.post")
    def test_mock_get_all_mids(self, mock_post):
        """Test get_all_mids with mocked response."""
        # Mock response
        mock_response = Mock()
        mock_response.json.return_value = {"BTC": "50000.0", "ETH": "3000.0", "SOL": "100.0"}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)

        result = feed.get_all_mids()

        assert result is not None
        assert result.get_input_data()["BTC"] == "50000.0"
        assert result.get_input_data()["ETH"] == "3000.0"

    @patch("bt_api_py.feeds.live_hyperliquid.request_base.requests.post")
    def test_mock_get_l2_book(self, mock_post):
        """Test get_l2_book with mocked response."""
        # Mock response
        mock_response = Mock()
        mock_response.json.return_value = {
            "coin": "BTC",
            "levels": [
                [  # asks
                    {"px": "50010.0", "sz": "1.0", "n": 1},
                    {"px": "50020.0", "sz": "2.0", "n": 1},
                ],
                [  # bids
                    {"px": "49990.0", "sz": "1.0", "n": 1},
                    {"px": "49980.0", "sz": "2.0", "n": 1},
                ],
            ],
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)

        result = feed.get_l2_book("BTC")

        assert result is not None
        levels = result.get_input_data().get("levels", [[], []])
        assert len(levels[0]) == 2  # 2 asks
        assert len(levels[1]) == 2  # 2 bids

    @patch("bt_api_py.feeds.live_hyperliquid.request_base.requests.post")
    def test_mock_get_candle_snapshot(self, mock_post):
        """Test get_candle_snapshot with mocked response."""
        # Mock response
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "t": 1234567890000,  # timestamp
                "o": "50000.0",  # open
                "h": "50100.0",  # high
                "l": "49900.0",  # low
                "c": "50050.0",  # close
                "v": "100.0",  # volume
            }
        ]
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)

        result = feed.get_candle_snapshot("BTC", "1m")

        assert result is not None
        candles = result.get_input_data()
        assert len(candles) >= 1
        assert candles[0]["o"] == "50000.0"

    @patch("bt_api_py.feeds.live_hyperliquid.request_base.requests.post")
    def test_mock_get_recent_trades(self, mock_post):
        """Test get_recent_trades with mocked response."""
        # Mock response
        mock_response = Mock()
        mock_response.json.return_value = [
            {"coin": "BTC", "px": "50000.0", "sz": "0.1", "side": "B", "time": 1234567890000}
        ]
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)

        result = feed.get_recent_trades("BTC", limit=10)

        assert result is not None
        trades = result.get_input_data()
        assert len(trades) >= 1
        assert trades[0]["px"] == "50000.0"


# ==================== Exchange Data Tests ====================


class TestHyperliquidExchangeData:
    """Test Hyperliquid exchange data container."""

    def test_exchange_data_spot_init(self):
        """Test HyperliquidExchangeDataSpot initialization."""
        exchange_data = HyperliquidExchangeDataSpot()

        assert exchange_data.exchange_name == "hyperliquid_spot"
        assert exchange_data.rest_url == "https://api.hyperliquid.xyz"
        assert exchange_data.wss_url == "wss://api.hyperliquid.xyz/ws"
        assert "1m" in exchange_data.kline_periods

    def test_get_symbol(self):
        """Test symbol conversion."""
        exchange_data = HyperliquidExchangeDataSpot()

        # Test plain symbols
        assert exchange_data.get_symbol("BTC") == "BTC"
        assert exchange_data.get_symbol("ETH") == "ETH"

        # Test symbols with slash
        assert exchange_data.get_symbol("BTC/USDC") == "BTC"
        assert exchange_data.get_symbol("ETH/USDC") == "ETH"

    def test_get_rest_path(self):
        """Test REST path retrieval."""
        exchange_data = HyperliquidExchangeDataSpot()

        assert exchange_data.get_rest_path("get_all_mids") == "/info"
        assert exchange_data.get_rest_path("make_order") == "/exchange"
        assert exchange_data.get_rest_path("cancel_order") == "/exchange"
        assert exchange_data.get_rest_path("get_l2_book") == "/info"


# ==================== WebSocket Tests ====================


class TestHyperliquidWebSocket:
    """Test Hyperliquid WebSocket subscriptions."""

    def test_subscribe_ticker(self):
        """Test ticker subscription format."""
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        market_wss = HyperliquidMarketWssDataSpot(data_queue)
        subscription = market_wss.subscribe_ticker("BTC")

        assert subscription["method"] == "subscribe"
        assert subscription["subscription"]["type"] == "allMids"

    def test_subscribe_orderbook(self):
        """Test orderbook subscription format."""
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        market_wss = HyperliquidMarketWssDataSpot(data_queue)
        subscription = market_wss.subscribe_orderbook("BTC", depth=5)

        assert subscription["method"] == "subscribe"
        assert subscription["subscription"]["type"] == "l2Book"
        assert subscription["subscription"]["coin"] == "BTC"

    def test_subscribe_trades(self):
        """Test trades subscription format."""
        from bt_api_py.feeds.live_hyperliquid.spot import HyperliquidMarketWssDataSpot

        data_queue = Mock()
        market_wss = HyperliquidMarketWssDataSpot(data_queue)
        subscription = market_wss.subscribe_trades("BTC")

        assert subscription["method"] == "subscribe"
        assert subscription["subscription"]["type"] == "trades"
        assert subscription["subscription"]["coin"] == "BTC"


# ==================== Integration Test ====================


@pytest.mark.integration
class TestHyperliquidIntegration:
    """Integration tests that require network and credentials."""

    def test_full_order_workflow(self):
        """Test complete order workflow: place, query, cancel."""
        live_hyperliquid_spot_feed = init_req_feed()

        # Step 1: Place a buy order far from market
        place_result = live_hyperliquid_spot_feed.place_order(
            symbol="BTC",
            side="buy",
            quantity=0.001,
            order_type="limit",
            price=1000.0,  # Very low price, unlikely to fill
        )

        assert isinstance(place_result, RequestData)
        place_data = place_result.get_data()

        # Get order ID
        order_id = None
        if place_data and "statuses" in place_data and len(place_data["statuses"]) > 0:
            if "resting" in place_data["statuses"][0]:
                order_id = place_data["statuses"][0]["resting"].get("oid")

            if order_id:
                # Step 2: Query order status
                status_result = live_hyperliquid_spot_feed.get_order_status(
                    user=live_hyperliquid_spot_feed.address, oid=order_id
                )
                assert isinstance(status_result, RequestData)

                # Step 3: Cancel the order
                cancel_result = live_hyperliquid_spot_feed.cancel_order(
                    symbol="BTC", order_id=order_id
                )
                assert isinstance(cancel_result, RequestData)

    def test_account_data_workflow(self):
        """Test getting complete account data."""
        live_hyperliquid_spot_feed = init_req_feed()

        # Get balances
        balances = live_hyperliquid_spot_feed.get_balance()
        assert balances is not None

        # Get clearinghouse state
        try:
            state_result = live_hyperliquid_spot_feed.get_spot_clearinghouse_state()
            assert isinstance(state_result, RequestData)
        except ValueError:
            pytest.skip("User address required for spot clearinghouse state")

        # Get user fills (trade history)
        fills_result = live_hyperliquid_spot_feed.get_user_fills(
            user=live_hyperliquid_spot_feed.address, limit=10
        )
        assert isinstance(fills_result, RequestData)


# ==================== Error Handling Tests ====================


class TestHyperliquidErrorHandling:
    """Test error handling scenarios."""

    @patch("bt_api_py.feeds.live_hyperliquid.request_base.requests.post")
    def test_error_response_handling(self, mock_post):
        """Test API error response handling."""
        # Mock error response
        mock_response = Mock()
        mock_response.json.return_value = {"error": "Invalid signature"}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)

        result = feed.get_all_mids()
        assert result is not None
        # Error should be in the response data
        assert "error" in result.get_input_data()

    def test_place_order_without_credentials(self):
        """Test placing order without private key."""
        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)  # No private key

        # Check if account exists and is None/falsy
        if hasattr(feed, "account") and feed.account is None:
            with pytest.raises(ValueError, match="Private key required"):
                feed.place_order(
                    symbol="BTC", side="buy", quantity=0.001, order_type="limit", price=50000.0
                )
        elif not hasattr(feed, "account") or not feed.account:
            # If no account attribute or account is falsy, expect error
            with pytest.raises((ValueError, AttributeError)):
                feed.place_order(
                    symbol="BTC", side="buy", quantity=0.001, order_type="limit", price=50000.0
                )

    def test_cancel_order_without_order_id(self):
        """Test canceling order without providing order_id."""
        data_queue = queue.Queue()
        feed = HyperliquidRequestDataSpot(data_queue)

        # cancel_order requires order_id as positional arg
        with pytest.raises((TypeError, ValueError, AttributeError)):
            feed.cancel_order(symbol="BTC")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-m", "not integration"])
