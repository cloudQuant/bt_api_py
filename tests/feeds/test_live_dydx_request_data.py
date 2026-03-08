"""
Tests for dYdX Spot Feed implementation

Comprehensive test suite covering REST API functionality.
dYdX is a decentralized exchange - WebSocket is not implemented yet.
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.feeds.live_dydx.spot import DydxRequestDataSpot


@pytest.fixture
def mock_data_queue():
    """Mock data queue for testing"""
    return queue.Queue()


@pytest.fixture
def dydx_spot(mock_data_queue):
    """Create DydxRequestDataSpot instance for testing"""
    with patch("bt_api_py.feeds.http_client.HttpClient"):
        feed = DydxRequestDataSpot(mock_data_queue)
        feed._params = DydxExchangeDataSwap()
        return feed


class TestDydxSpotInit:
    """Test DydxRequestDataSpot initialization"""

    def test_init_default_params(self, dydx_spot):
        """Test initialization with default parameters"""
        assert dydx_spot.exchange_name == "DYDX___SWAP"
        assert dydx_spot.asset_type == "swap"
        assert dydx_spot.logger_name == "dydx_spot_feed.log"
        assert dydx_spot.data_queue is not None
        assert isinstance(dydx_spot._params, DydxExchangeDataSwap)

    def test_init_with_testnet(self, mock_data_queue):
        """Test initialization with testnet enabled"""
        feed = DydxRequestDataSpot(mock_data_queue, testnet=True)
        assert feed._params.testnet_rest_url == "https://indexer.v4testnet.dydx.exchange/v4"

    def test_init_with_custom_params(self, mock_data_queue):
        """Test initialization with custom parameters"""
        feed = DydxRequestDataSpot(
            mock_data_queue,
            api_key="test_key",
            private_key="test_private",
            address="0x1234567890abcdef",
            subaccount_number=1,
        )
        assert feed.api_key == "test_key"
        assert feed.private_key == "test_private"
        assert feed.address == "0x1234567890abcdef"
        assert feed.subaccount_number == 1

    def test_capabilities(self, dydx_spot):
        """Test that declared capabilities are correct"""
        from bt_api_py.feeds.capability import Capability

        caps = dydx_spot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps
        assert Capability.QUERY_ORDER in caps


class TestDydxTicker:
    """Test dYdX ticker functionality"""

    @pytest.mark.ticker
    def test_get_ticker_spot(self, dydx_spot):
        """Test get_ticker_spot method returns correct path and params"""
        symbol = "BTC-USD"
        path, params, extra_data = dydx_spot.get_ticker_spot(symbol)

        assert path == "/v4/markets"
        assert params == {"instId": "BTC-USD"}
        assert extra_data["request_type"] == "get_ticker"
        assert extra_data["symbol_name"] == "BTC-USD"
        assert extra_data["exchange_name"] == "DYDX___SWAP"
        assert "normalize_function" in extra_data

    @pytest.mark.ticker
    def test_ticker_normalize_function_valid_data(self):
        """Test ticker normalize function with valid data"""
        input_data = {
            "code": 0,
            "markets": {
                "BTC-USD": {
                    "snapshotAt": "2024-01-01T00:00:00Z",
                    "oraclePrice": "50000",
                    "markPrice": "50001",
                    "lastPrice": "50002",
                    "volume24H": "1000",
                    "high24H": "51000",
                    "low24H": "49000",
                    "volumeNotional24H": "50000000",
                    "openInterest24H": "500",
                }
            },
        }
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert len(result) == 9
        assert result[1] == 50000  # oraclePrice
        assert result[2] == 50001  # markPrice
        assert result[3] == 50002  # lastPrice

    @pytest.mark.ticker
    def test_ticker_normalize_function_missing_symbol(self):
        """Test ticker normalize function with missing symbol"""
        input_data = {
            "code": 0,
            "markets": {
                "ETH-USD": {
                    "oraclePrice": "3000",
                }
            },
        }
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(
            input_data, extra_data
        )

        assert result is None
        assert status is False

    @pytest.mark.ticker
    def test_ticker_normalize_function_error_code(self):
        """Test ticker normalize function with error response"""
        input_data = {"code": 1, "message": "Invalid symbol"}
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(
            input_data, extra_data
        )

        assert result is None
        assert status is False


class TestDydxBalance:
    """Test dYdX balance functionality"""

    def test_get_balance_spot(self, dydx_spot):
        """Test get_balance_spot method"""
        address = "0x1234567890abcdef"
        subaccount_number = 0

        path, params, extra_data = dydx_spot.get_balance_spot(address, subaccount_number)

        assert "get_subaccount" in path
        assert extra_data["request_type"] == "get_subaccount"
        assert extra_data["exchange_name"] == "DYDX___SWAP"
        assert "normalize_function" in extra_data

    def test_balance_normalize_function_valid_data(self):
        """Test balance normalize function with valid data"""
        input_data = {
            "subaccount": {
                "equity": "10000",
                "freeCollateral": "5000",
                "availableMargin": "3000",
                "positionMargin": "200",
                "marginBalance": "8000",
            }
        }

        result, status = DydxRequestDataSpot._get_balance_spot_normalize_function(input_data, None)

        assert status is True
        assert result is not None
        assert len(result) == 1
        assert result[0]["symbol"] == "USD"
        assert result[0]["equity"] == 10000.0
        assert result[0]["freeCollateral"] == 5000.0

    def test_balance_normalize_function_empty_subaccount(self):
        """Test balance normalize function with empty subaccount"""
        input_data = {"subaccount": {}}

        result, status = DydxRequestDataSpot._get_balance_spot_normalize_function(input_data, None)

        assert status is True
        if result is not None:
            assert len(result) == 1
            assert result[0]["symbol"] == "USD"
            assert result[0]["equity"] == 0.0


class TestDydxKline:
    """Test dYdX kline/candlestick functionality"""

    @pytest.mark.kline
    def test_get_kline_spot(self, dydx_spot):
        """Test get_kline_spot method"""
        symbol = "BTC-USD"
        period = "1h"

        path, params, extra_data = dydx_spot.get_kline_spot(symbol, period)

        assert "candles" in path
        assert "resolution" in params
        assert extra_data["request_type"] == "get_candles"
        assert extra_data["symbol_name"] == "BTC-USD"
        assert "normalize_function" in extra_data

    @pytest.mark.kline
    def test_kline_normalize_function_valid_data(self):
        """Test kline normalize function with valid data"""
        input_data = {
            "candles": [
                {
                    "startedAt": "2024-01-01T00:00:00Z",
                    "open": "50000",
                    "high": "51000",
                    "low": "49000",
                    "close": "50500",
                    "volume": "100",
                },
                {
                    "startedAt": "2024-01-01T01:00:00Z",
                    "open": "50500",
                    "high": "51500",
                    "low": "50000",
                    "close": "51000",
                    "volume": "120",
                },
            ]
        }

        result, status = DydxRequestDataSpot._get_kline_normalize_function(input_data, None)

        assert status is True
        assert len(result) == 2
        assert result[0]["open"] == 50000.0
        assert result[1]["close"] == 51000.0


class TestDydxOrder:
    """Test dYdX order functionality"""

    def test_make_order(self, dydx_spot):
        """Test make_order method prepares correct request"""
        symbol = "BTC-USD"
        volume = 0.1
        price = 50000
        order_type = "limit"

        path, body, extra_data = dydx_spot._make_order(symbol, volume, price, order_type)

        assert "POST" in path
        assert body["market"] == "BTC-USD"
        assert body["side"] == "BUY"
        assert body["type"] == "LIMIT"
        assert body["size"] == "0.1"
        assert body["price"] == "50000"
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order(self, dydx_spot):
        """Test cancel_order method prepares correct request"""
        symbol = "BTC-USD"
        order_id = "order123"

        path, body, extra_data = dydx_spot._cancel_order(symbol, order_id)

        assert "DELETE" in path
        assert order_id in path
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == order_id

    def test_query_order(self, dydx_spot):
        """Test query_order method prepares correct request"""
        symbol = "BTC-USD"
        order_id = "order123"

        path, params, extra_data = dydx_spot._query_order(symbol, order_id)

        assert "orders" in path
        assert params["orderId"] == order_id
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders(self, dydx_spot):
        """Test get_open_orders method prepares correct request"""
        path, params, extra_data = dydx_spot._get_open_orders()

        assert "orders" in path
        assert params["status"] == "OPEN"
        assert extra_data["request_type"] == "get_open_orders"


class TestDydxOrderbook:
    """Test dYdX orderbook functionality"""

    @pytest.mark.orderbook
    def test_orderbook_normalize_function(self):
        """Test orderbook normalize function"""
        input_data = {
            "bids": [["50000", "1.0"], ["49999", "2.0"]],
            "asks": [["50001", "1.5"], ["50002", "2.5"]],
        }
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_orderbook_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert len(result) == 1
        assert result[0]["symbol"] == "BTC-USD"
        assert len(result[0]["bids"]) == 2
        assert len(result[0]["asks"]) == 2


class TestDydxExchangeInfo:
    """Test dYdX exchange info functionality"""

    def test_exchange_info_normalize_function(self):
        """Test exchange info normalize function"""
        input_data = {"markets": {"BTC-USD": {"status": "ACTIVE"}, "ETH-USD": {"status": "ACTIVE"}}}

        result, status = DydxRequestDataSpot._get_exchange_info_normalize_function(input_data, None)

        assert status is True
        assert len(result) == 2
        assert result[0]["symbol"] == "BTC-USD"
        assert result[0]["status"] == "ACTIVE"


class TestDydxErrorHandling:
    """Test dYdX error handling"""

    def test_error_translator(self, dydx_spot):
        """Test error translator functionality"""
        error_response = {"code": 400, "message": "Invalid symbol"}

        error = dydx_spot._error_translator.translate(error_response, "DYDX___SWAP")

        assert error is not None

    def test_rate_limiter_initialization(self, dydx_spot):
        """Test rate limiter is properly initialized"""
        assert dydx_spot._rate_limiter is not None


@pytest.mark.unit
class TestDydxIntegration:
    """Integration tests for dYdX (marked to skip in CI)"""

    @pytest.mark.skip(reason="Integration test - requires API credentials")
    @pytest.mark.ticker
    def test_get_ticker_live(self, dydx_spot):
        """Test live ticker request (requires network)"""
        pass

    @pytest.mark.skip(reason="Integration test - requires API credentials")
    def test_get_balance_live(self, dydx_spot):
        """Test live balance request (requires network)"""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
