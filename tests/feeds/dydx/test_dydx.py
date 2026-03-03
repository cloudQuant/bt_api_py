"""
Tests for dYdX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import queue
import pytest
from unittest.mock import Mock, patch, MagicMock

from bt_api_py.feeds.live_dydx.spot import DydxRequestDataSpot
from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData


class TestDydxRequestDataSpot:
    """Test cases for DydxRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def dydx_spot(self, mock_data_queue):
        """Create DydxRequestDataSpot instance."""
        with patch('bt_api_py.feeds.http_client.HttpClient', return_value=MagicMock()):
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

    def test_get_ticker_spot(self, dydx_spot):
        """Test get_ticker_spot method."""
        symbol = "BTC-USD"
        path, params, extra_data = dydx_spot.get_ticker_spot(symbol)

        assert extra_data['request_type'] == "get_ticker"
        assert extra_data['symbol_name'] == symbol
        assert extra_data['exchange_name'] == "DYDX___SWAP"

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
                    "volumeNotional24H": "50000000"
                }
            }
        }
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(input_data, extra_data)
        assert status == True
        assert len(result) == 9
        assert result[1] == 50000.0  # oraclePrice

    def test_get_ticker_normalize_function_error(self):
        """Test ticker normalize function with error."""
        input_data = {
            "code": 1,
            "markets": {}
        }
        extra_data = {"symbol_name": "BTC-USD"}

        result, status = DydxRequestDataSpot._get_ticker_spot_normalize_function(input_data, extra_data)
        assert status == False

    # ==================== Balance Tests ====================

    def test_get_balance_spot(self, dydx_spot):
        """Test get_balance_spot method."""
        address = "0x1234567890123456789012345678901234567890"
        subaccount_number = 0

        path, params, extra_data = dydx_spot.get_balance_spot(address, subaccount_number)

        assert extra_data['request_type'] == "get_subaccount"
        assert extra_data['exchange_name'] == "DYDX___SWAP"

    def test_get_balance_normalize_function(self):
        """Test balance normalize function."""
        input_data = {
            "subaccount": {
                "equity": "10000",
                "freeCollateral": "5000",
                "availableMargin": "3000",
                "positionMargin": "2000",
                "marginBalance": "8000"
            }
        }

        result, status = DydxRequestDataSpot._get_balance_spot_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert result[0]['symbol'] == "USD"
        assert result[0]['equity'] == 10000.0

    # ==================== Kline Tests ====================

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = {
            "candles": [
                {"startedAt": "2024-01-01T00:00:00Z", "open": "50000", "high": "51000", "low": "49000", "close": "50500", "volume": "100"},
                {"startedAt": "2024-01-01T01:00:00Z", "open": "50500", "high": "51500", "low": "50000", "close": "51000", "volume": "150"}
            ]
        }

        result, status = DydxRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== OrderBook Tests ====================

    def test_get_orderbook_normalize_function(self):
        """Test orderbook normalize function."""
        input_data = {
            "bids": [
                {"price": "50000", "size": "1.0"},
                {"price": "49999", "size": "2.0"}
            ],
            "asks": [
                {"price": "50001", "size": "1.0"},
                {"price": "50002", "size": "2.0"}
            ]
        }

        result, status = DydxRequestDataSpot._get_orderbook_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, dydx_spot):
        """Test get_exchange_info method."""
        path, params, extra_data = dydx_spot.get_exchange_info()

        assert extra_data['request_type'] == "get_exchange_info"

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = {
            "markets": {
                "BTC-USD": {"market": "BTC-USD"},
                "ETH-USD": {"market": "ETH-USD"}
            }
        }

        result, status = DydxRequestDataSpot._get_exchange_info_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== Method Wrappers ====================

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

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_ticker(self, dydx_spot):
        """Integration test for get_ticker - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_balance(self, dydx_spot):
        """Integration test for get_balance - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_orderbook(self, dydx_spot):
        """Integration test for get_orderbook - skipped."""
        pass


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
