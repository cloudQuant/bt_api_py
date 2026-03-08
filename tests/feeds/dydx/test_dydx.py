"""
Tests for dYdX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_dydx.spot import DydxRequestDataSpot


class TestDydxRequestDataSpot:
    """Test cases for DydxRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def dydx_spot(self, mock_data_queue):
        """Create DydxRequestDataSpot instance."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = DydxRequestDataSpot(mock_data_queue)
            return instance

    def test_init(self, dydx_spot):
        """Test initialization."""
        assert dydx_spot.exchange_name == "DYDX___SWAP"
        assert dydx_spot.asset_type == "swap"
        assert dydx_spot.logger_name == "dydx_spot_feed.log"

    def test_capabilities(self, dydx_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = dydx_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    # ==================== Ticker Tests ====================

    @pytest.mark.ticker
    def test_get_ticker_spot(self, dydx_spot):
        """Test get_ticker_spot method."""
        symbol = "BTC-USD"
        path, params, extra_data = dydx_spot.get_ticker_spot(symbol)

        assert extra_data["request_type"] == "get_ticker"
        assert extra_data["symbol_name"] == symbol
        assert extra_data["exchange_name"] == "DYDX___SWAP"

    @pytest.mark.ticker
    def test_get_ticker_normalize_function(self):
        """Test ticker normalize function."""
        input_data = {
            "code": 0,
            "markets": {
                "BTC-USD": {
                    "snapshotAt": 1234567890,
                    "oraclePrice": "50000",
                    "markPrice": "50001",
                    "lastPrice": "50002",
                    "volume24H": "1000",
                    "high24H": "51000",
                    "low24H": "49000",
                    "volumeNotional24H": "50000000",
                }
            },
        }
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(
            input_data, extra_data
        )
        assert status == True
        assert len(result) == 9
        assert result[1] == 50000.0  # oraclePrice

    @pytest.mark.ticker
    def test_get_ticker_normalize_function_error(self):
        """Test ticker normalize function with error."""
        input_data = {"code": 1, "markets": {}}
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(
            input_data, extra_data
        )
        assert status == False

    # ==================== Balance Tests ====================

    def test_get_balance_spot(self, dydx_spot):
        """Test get_balance_spot method."""
        address = "0x1234567890123456789012345678901234567890"
        subaccount_number = 0

        path, params, extra_data = dydx_spot.get_balance_spot(address, subaccount_number)

        assert extra_data["request_type"] == "get_subaccount"
        assert extra_data["exchange_name"] == "DYDX___SWAP"

    def test_get_balance_normalize_function(self):
        """Test balance normalize function."""
        input_data = {
            "subaccount": {
                "equity": "10000",
                "freeCollateral": "5000",
                "availableMargin": "3000",
                "positionMargin": "2000",
                "marginBalance": "8000",
            }
        }

        result, status = DydxRequestDataSpot._get_balance_spot_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert result[0]["symbol"] == "USD"
        assert result[0]["equity"] == 10000.0

    # ==================== Kline Tests ====================

    @pytest.mark.kline
    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
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
                    "volume": "150",
                },
            ]
        }

        result, status = DydxRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== OrderBook Tests ====================

    @pytest.mark.orderbook
    def test_get_orderbook_normalize_function(self):
        """Test orderbook normalize function."""
        input_data = {
            "bids": [{"price": "50000", "size": "1.0"}, {"price": "49999", "size": "2.0"}],
            "asks": [{"price": "50001", "size": "1.0"}, {"price": "50002", "size": "2.0"}],
        }

        result, status = DydxRequestDataSpot._get_orderbook_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, dydx_spot):
        """Test get_exchange_info method returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_exchange_info()
        assert dydx_spot.request.called

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = {
            "markets": {"BTC-USD": {"market": "BTC-USD"}, "ETH-USD": {"market": "ETH-USD"}}
        }

        result, status = DydxRequestDataSpot._get_exchange_info_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== Method Wrappers ====================

    @pytest.mark.ticker
    def test_get_ticker(self, dydx_spot):
        """Test get_ticker method."""
        # Mock the request method
        dydx_spot.request = Mock(return_value=Mock(data=Mock()))

        result = dydx_spot.get_ticker("BTC-USD")
        assert dydx_spot.request.called

    def test_get_balance(self, dydx_spot):
        """Test get_balance method."""
        # Mock the request method
        dydx_spot.request = Mock(return_value=Mock(data=Mock()))

        result = dydx_spot.get_balance("0x123", 0)
        assert dydx_spot.request.called

    # ==================== Standard Interface Tests ====================

    @pytest.mark.ticker
    def test_get_tick(self, dydx_spot):
        """Test get_tick returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_tick("BTC-USD")
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = (
            call_args[1].get("extra_data") or call_args[0][2]
            if len(call_args[0]) > 2
            else call_args[1].get("extra_data")
        )
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-USD"

    @pytest.mark.orderbook
    def test_get_depth(self, dydx_spot):
        """Test get_depth returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_depth("BTC-USD", count=10)
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "BTC-USD"

    @pytest.mark.kline
    def test_get_kline(self, dydx_spot):
        """Test get_kline returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_kline("BTC-USD", "1m", count=20)
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == "BTC-USD"

    def test_get_server_time(self, dydx_spot):
        """Test get_server_time returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_server_time()
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_server_time"

    def test_make_order(self, dydx_spot):
        """Test make_order returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.make_order("BTC-USD", 0.1, 50000.0, "limit", side="BUY")
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"
        assert extra_data["symbol_name"] == "BTC-USD"

    def test_cancel_order(self, dydx_spot):
        """Test cancel_order returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.cancel_order("BTC-USD", "order123")
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order123"

    def test_query_order(self, dydx_spot):
        """Test query_order returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.query_order("BTC-USD", "order123")
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"
        assert extra_data["order_id"] == "order123"

    def test_get_open_orders(self, dydx_spot):
        """Test get_open_orders returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_open_orders("BTC-USD")
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account(self, dydx_spot):
        """Test get_account returns RequestData."""
        dydx_spot.request = Mock(return_value=Mock(spec=RequestData))
        result = dydx_spot.get_account("ALL", address="0xabc", subaccount_number=0)
        assert dydx_spot.request.called
        call_args = dydx_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"
        assert extra_data["symbol_name"] == "ALL"

    @pytest.mark.skip(reason="Requires actual API call")
    @pytest.mark.ticker
    def test_integration_get_ticker(self, dydx_spot):
        """Integration test for get_ticker - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_balance(self, dydx_spot):
        """Integration test for get_balance - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    @pytest.mark.orderbook
    def test_integration_get_orderbook(self, dydx_spot):
        """Integration test for get_orderbook - skipped."""
        pass


class TestDydxStandardCapabilities:
    """Test standard Feed capabilities."""

    def test_capabilities_complete(self):
        """Test that all expected capabilities are declared."""
        from bt_api_py.feeds.capability import Capability
        from bt_api_py.feeds.live_dydx.request_base import DydxRequestData

        caps = DydxRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps
        assert Capability.QUERY_ORDER in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.GET_EXCHANGE_INFO in caps


class TestDydxDataContainers:
    """Test dYdX data containers init_data() returns self."""

    def test_order_init_data_returns_self(self):
        """Test DydxOrderData.init_data() returns self."""
        from bt_api_py.containers.orders.dydx_order import DydxOrderData

        order_data = {
            "id": "order123",
            "clientId": "client456",
            "status": "OPEN",
            "side": "BUY",
            "type": "LIMIT",
            "size": "0.1",
            "price": "50000",
            "createdAt": "2024-01-01T00:00:00Z",
            "filledSize": "0",
            "remainingSize": "0.1",
        }
        order = DydxOrderData(order_data, "BTC-USD", "swap", has_been_json_encoded=True)
        result = order.init_data()
        assert result is order
        assert order.get_order_id() == "order123"
        assert order.get_symbol_name() == "BTC-USD"

    def test_balance_init_data_returns_self(self):
        """Test DydxBalanceData.init_data() returns self."""
        from bt_api_py.containers.balances.dydx_balance import DydxBalanceData

        balance_data = {
            "subaccount": {
                "equity": "10000",
                "freeCollateral": "5000",
                "openPnl": "100",
                "initialMarginRequirement": "200",
                "marginBalance": "8000",
                "availableMargin": "3000",
                "positionMargin": "2000",
                "accountValue": "10100",
            }
        }
        balance = DydxBalanceData(balance_data, "USD", "swap", has_been_json_encoded=True)
        result = balance.init_data()
        assert result is balance
        assert balance.get_equity() == 10000.0
        assert balance.get_symbol_name() == "USD"

    @pytest.mark.ticker
    def test_request_ticker_init_data_returns_self(self):
        """Test DydxRequestTickerData.init_data() returns self."""
        from bt_api_py.containers.tickers.dydx_ticker import DydxRequestTickerData

        ticker_data = {
            "markets": {
                "BTC-USD": {
                    "snapshotAt": "2024-01-01T00:00:00Z",
                    "oraclePrice": "50000",
                    "volume24H": "1000",
                    "high24H": "51000",
                    "low24H": "49000",
                    "markPrice": "50001",
                }
            }
        }
        ticker = DydxRequestTickerData(ticker_data, "BTC-USD", "swap", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker
        assert ticker.get_symbol_name() == "BTC-USD"
        assert ticker.get_last_price() == 50000.0

    @pytest.mark.ticker
    def test_wss_ticker_init_data_returns_self(self):
        """Test DydxWssTickerData.init_data() returns self."""
        from bt_api_py.containers.tickers.dydx_ticker import DydxWssTickerData

        ticker_data = {
            "markets": {
                "ETH-USD": {
                    "oraclePrice": "3000",
                    "volume24H": "500",
                    "currentFundingRate": "0.0001",
                }
            }
        }
        ticker = DydxWssTickerData(ticker_data, "ETH-USD", "swap", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker
        assert ticker.get_symbol_name() == "ETH-USD"

    def test_order_init_data_idempotent(self):
        """Test that calling init_data() twice returns self both times."""
        from bt_api_py.containers.orders.dydx_order import DydxOrderData

        order_data = {
            "id": "o1",
            "status": "OPEN",
            "side": "BUY",
            "type": "LIMIT",
            "size": "1",
            "price": "100",
            "createdAt": "2024-01-01T00:00:00Z",
        }
        order = DydxOrderData(order_data, "BTC-USD", "swap", has_been_json_encoded=True)
        r1 = order.init_data()
        r2 = order.init_data()
        assert r1 is order
        assert r2 is order


class TestDydxExchangeDataSwap:
    """Test cases for DydxExchangeDataSwap."""

    def test_init(self):
        """Test initialization."""
        exchange_data = DydxExchangeDataSwap()
        assert exchange_data.exchange_name == "DydxSwap"
        assert exchange_data.rest_url == "https://indexer.dydx.trade/v4"

    def test_get_symbol(self):
        """Test get_symbol method."""
        exchange_data = DydxExchangeDataSwap()
        assert exchange_data.get_symbol("btc-usd") == "BTC-USD"
        assert exchange_data.get_symbol("BTC-USDT") == "BTC-USD"

    def test_get_period(self):
        """Test get_period method."""
        exchange_data = DydxExchangeDataSwap()
        # Mock kline_periods
        exchange_data.kline_periods = {"1m": "1MIN"}
        assert exchange_data.get_period("1m") == "1MIN"

    def test_supported_symbols(self):
        """Test supported symbols."""
        exchange_data = DydxExchangeDataSwap()
        assert "BTC-USD" in exchange_data.supported_symbols
        assert "ETH-USD" in exchange_data.supported_symbols

    def test_rest_url(self):
        """Test REST URL."""
        exchange_data = DydxExchangeDataSwap()
        assert exchange_data.rest_url == "https://indexer.dydx.trade/v4"
        assert exchange_data.testnet_rest_url == "https://indexer.v4testnet.dydx.exchange/v4"

    def test_wss_url(self):
        """Test WSS URL."""
        exchange_data = DydxExchangeDataSwap()
        assert exchange_data.wss_url == "wss://indexer.dydx.trade/v4/ws"

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = DydxExchangeDataSwap()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = DydxExchangeDataSwap()
        assert "USD" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency


class TestDydxRegistration:
    """Test cases for dYdX registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that dYdX is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("DYDX___SWAP")
        assert exchange_class is not None
        assert exchange_class.__name__ == "DydxRequestDataSpot"


class TestDydxNormalizeFunctions:
    """Test normalize functions produce correct (data, status) tuples."""

    @pytest.mark.ticker
    def test_ticker_normalize_with_missing_symbol(self):
        """Test ticker normalize returns None for missing symbol."""
        input_data = {"code": 0, "markets": {"ETH-USD": {}}}
        extra_data = {"symbol_name": "BTC-USD"}
        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(
            input_data, extra_data
        )
        assert result is None
        assert status is False

    @pytest.mark.orderbook
    def test_orderbook_normalize(self):
        """Test orderbook normalize includes symbol."""
        input_data = {"bids": [["50000", "1"]], "asks": [["50001", "2"]]}
        extra_data = {"symbol_name": "BTC-USD"}
        result, status = DydxRequestDataSpot._get_orderbook_normalize_function(
            input_data, extra_data
        )
        assert status is True
        assert result[0]["symbol"] == "BTC-USD"

    def test_exchange_info_normalize_empty(self):
        """Test exchange info normalize with empty markets."""
        input_data = {"markets": {}}
        result, status = DydxRequestDataSpot._get_exchange_info_normalize_function(input_data, None)
        assert status is True
        assert result == []

    @pytest.mark.kline
    def test_kline_normalize_empty(self):
        """Test kline normalize with no candles."""
        input_data = {"candles": []}
        result, status = DydxRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status is True
        assert result == []

    def test_balance_normalize_empty_subaccount(self):
        """Test balance normalize with empty subaccount."""
        input_data = {"subaccount": {}}
        result, status = DydxRequestDataSpot._get_balance_spot_normalize_function(input_data, None)
        assert status is True
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
