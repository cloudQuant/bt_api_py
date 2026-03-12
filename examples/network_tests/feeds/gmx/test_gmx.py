"""
Tests for GMX DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

from enum import Enum
from unittest.mock import MagicMock, Mock, patch

import pytest

from bt_api_py.containers.exchanges.gmx_exchange_data import (
    GmxChain,
    GmxExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_gmx.spot import GmxRequestDataSpot


class MockGmxChain(str, Enum):
    """Mock GmxChain for testing."""

    ARBITRUM = "arbitrum"
    AVALANCHE = "avalanche"


class TestGmxRequestDataSpot:
    """Test cases for GmxRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def gmx_spot(self, mock_data_queue):
        """Create GmxRequestDataSpot instance."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = GmxRequestDataSpot(mock_data_queue)
            return instance

    @pytest.fixture
    def gmx_spot_avalanche(self, mock_data_queue):
        """Create GmxRequestDataSpot instance on Avalanche."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = GmxRequestDataSpot(mock_data_queue, chain=GmxChain.AVALANCHE)
            return instance

    def test_init(self, gmx_spot):
        """Test initialization."""
        assert gmx_spot.exchange_name == "GMX___DEX"
        assert gmx_spot.asset_type == "SPOT"
        assert gmx_spot.chain == GmxChain.ARBITRUM

    def test_init_with_chain(self, gmx_spot_avalanche):
        """Test initialization with custom chain."""
        assert gmx_spot_avalanche.chain == GmxChain.AVALANCHE

    def test_capabilities(self, gmx_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = gmx_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

    # ==================== Ticker Tests ====================

    @pytest.mark.ticker
    def test_get_tick(self, gmx_spot):
        """Test get_tick method."""
        symbol = "BTC"
        path, params, extra_data = gmx_spot._get_tick(symbol)

        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == symbol
        assert extra_data["exchange_name"] == "GMX___DEX"
        assert extra_data["chain"] == "arbitrum"

    @pytest.mark.ticker
    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        input_data = {
            "BTC": {
                "minPrice": "49000",
                "maxPrice": "51000",
                "oraclePrice": "50000",
                "markPrice": "50001",
                "propagationTime": 1234567890,
            }
        }
        result, status = GmxRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    @pytest.mark.ticker
    def test_get_tick_with_symbol_filter(self, gmx_spot):
        """Test get_tick with symbol filtering."""
        # Mock the request method to return multiple tickers
        gmx_spot.request = Mock(
            return_value=Mock(data={"BTC": {"minPrice": "49000"}, "ETH": {"minPrice": "3000"}})
        )

        gmx_spot.get_tick("BTC")
        assert gmx_spot.request.called

    # ==================== Kline Tests ====================

    @pytest.mark.kline
    def test_get_kline(self, gmx_spot):
        """Test get_kline method."""
        symbol = "BTC"
        period = "1h"
        count = 100

        path, params, extra_data = gmx_spot._get_kline(symbol, period, count)

        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == symbol
        assert extra_data["period"] == period
        assert params["tokenSymbol"] == symbol
        assert params["period"] == "1h"

    @pytest.mark.kline
    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = {
            "period": "1h",
            "candles": [
                [1234567890, "50000", "51000", "49000", "50500", "1000"],
                [1234567900, "50500", "51500", "50000", "51000", "1500"],
            ],
        }
        result, status = GmxRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status
        assert len(result) == 1
        assert len(result[0]) == 2  # Two candles

    @pytest.mark.kline
    def test_get_kline_period_mapping(self, gmx_spot):
        """Test kline period mapping."""
        # Test various periods
        for period, expected in [("1m", "1m"), ("5m", "5m"), ("1h", "1h"), ("1d", "1d")]:
            path, params, extra_data = gmx_spot._get_kline("BTC", period, 10)
            assert params["period"] == expected

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, gmx_spot):
        """Test get_exchange_info method."""
        path, params, extra_data = gmx_spot._get_exchange_info()

        assert extra_data["request_type"] == "get_exchange_info"
        assert extra_data["exchange_name"] == "GMX___DEX"

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = [{"market": "BTC", "name": "Bitcoin"}, {"market": "ETH", "name": "Ethereum"}]
        result, status = GmxRequestDataSpot._get_exchange_info_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    def test_get_tokens_normalize_function(self):
        """Test tokens normalize function."""
        input_data = [{"symbol": "BTC", "name": "Bitcoin"}, {"symbol": "ETH", "name": "Ethereum"}]
        result, status = GmxRequestDataSpot._get_tokens_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    # ==================== Depth Tests ====================

    @pytest.mark.orderbook
    def test_get_depth(self, gmx_spot):
        """Test get_depth method."""
        symbol = "BTC"
        path, params, extra_data = gmx_spot._get_depth(symbol)

        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == symbol

    def test_get_markets_info_normalize_function(self):
        """Test markets info normalize function."""
        input_data = [
            {"market": "BTC", "liquidity": "1000000"},
            {"market": "ETH", "liquidity": "500000"},
        ]
        result, status = GmxRequestDataSpot._get_markets_info_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    # ==================== Signed Price Tests ====================

    def test_get_signed_prices(self, gmx_spot):
        """Test get_signed_prices method."""
        path, params, extra_data = gmx_spot._get_signed_prices()

        assert extra_data["request_type"] == "get_signed_prices"
        assert extra_data["exchange_name"] == "GMX___DEX"

    def test_get_signed_prices_normalize_function(self):
        """Test signed prices normalize function."""
        input_data = {"signedPrices": [{"token": "BTC", "price": "50000", "signature": "0x..."}]}
        result, status = GmxRequestDataSpot._get_signed_prices_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    @pytest.mark.skip(reason="Requires actual API call")
    @pytest.mark.ticker
    def test_integration_get_ticker(self, gmx_spot):
        """Integration test for get_ticker - skipped."""

    @pytest.mark.skip(reason="Requires actual API call")
    @pytest.mark.kline
    def test_integration_get_kline(self, gmx_spot):
        """Integration test for get_kline - skipped."""

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_exchange_info(self, gmx_spot):
        """Integration test for get_exchange_info - skipped."""


class TestGmxExchangeDataSpot:
    """Test cases for GmxExchangeDataSpot."""

    def test_init_with_chain_enum(self):
        """Test initialization with chain enum."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert exchange_data.chain == GmxChain.ARBITRUM

    def test_init_with_chain_string(self):
        """Test initialization with chain string."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain="arbitrum")
            assert exchange_data.chain.value == "arbitrum"

    def test_get_chain_value(self):
        """Test get_chain_value method."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.AVALANCHE)
            assert exchange_data.get_chain_value() == "avalanche"

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert exchange_data.get_rest_url() == "https://arbitrum-api.gmxinfra.io"

    def test_api_urls(self):
        """Test API URLs are defined."""
        assert GmxExchangeDataSpot.API_URLS is not None
        assert GmxChain.ARBITRUM in GmxExchangeDataSpot.API_URLS
        assert GmxChain.AVALANCHE in GmxExchangeDataSpot.API_URLS

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert "BTC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency
            assert "USD" in exchange_data.legal_currency

    def test_supported_symbols(self):
        """Test supported symbols."""
        with patch(
            "bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config", return_value=None
        ):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert "BTC" in exchange_data.supported_symbols
            assert "ETH" in exchange_data.supported_symbols


class TestGmxRegistration:
    """Test cases for GMX registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that GMX is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("GMX___DEX")
        assert exchange_class is not None
        assert exchange_class.__name__ == "GmxRequestDataSpot"


class TestGmxStandardInterfaces:
    """Test standard Feed interface methods for GMX."""

    @pytest.fixture
    def gmx_spot(self):
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = GmxRequestDataSpot(Mock())
            instance.request = Mock(return_value=Mock(spec=RequestData))
            return instance

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, gmx_spot):
        path, params, extra_data = gmx_spot._get_tick("BTC")
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, gmx_spot):
        gmx_spot.get_depth("BTC")
        assert gmx_spot.request.called
        (
            gmx_spot.request.call_args[1].get("extra_data") or gmx_spot.request.call_args[0][2]
            if len(gmx_spot.request.call_args[0]) > 2
            else None
        )

    @pytest.mark.kline
    def test_get_kline_calls_request(self, gmx_spot):
        gmx_spot.get_kline("BTC", "1h")
        assert gmx_spot.request.called

    def test_get_server_time(self):
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            inst = GmxRequestDataSpot(Mock())
            result = inst.get_server_time()
            assert isinstance(result, RequestData)

    def test_get_server_time_extra_data(self):
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            inst = GmxRequestDataSpot(Mock())
            path, params, extra_data = inst._get_server_time()
            assert extra_data["request_type"] == "get_server_time"
            assert "server_time" in extra_data

    def test_make_order_calls_request(self, gmx_spot):
        gmx_spot.make_order("BTC", 1.0, 50000, "LIMIT")
        assert gmx_spot.request.called
        extra_data = gmx_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, gmx_spot):
        gmx_spot.cancel_order("BTC", "order_123")
        assert gmx_spot.request.called
        extra_data = gmx_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, gmx_spot):
        gmx_spot.query_order("BTC", "order_123")
        assert gmx_spot.request.called
        extra_data = gmx_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, gmx_spot):
        gmx_spot.get_open_orders("BTC")
        assert gmx_spot.request.called
        extra_data = gmx_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, gmx_spot):
        gmx_spot.get_account("BTC")
        assert gmx_spot.request.called
        extra_data = gmx_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, gmx_spot):
        gmx_spot.get_balance("BTC")
        assert gmx_spot.request.called
        extra_data = gmx_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"


class TestGmxBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.capability import Capability
        from bt_api_py.feeds.live_gmx.request_base import GmxRequestData

        caps = GmxRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestGmxDataContainers:
    """Test GMX data containers init_data() returns self."""

    @pytest.mark.ticker
    def test_ticker_init_data_returns_self(self):
        from bt_api_py.containers.tickers.gmx_ticker import GmxRequestTickerData

        ticker_data = {"BTC": {"minPrice": "49000", "maxPrice": "51000"}}
        ticker = GmxRequestTickerData(ticker_data, "BTC", "SPOT", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker
        assert ticker.get_symbol_name() == "BTC"
        assert ticker.get_last_price() == 49000.0

    @pytest.mark.ticker
    def test_ticker_init_data_idempotent(self):
        from bt_api_py.containers.tickers.gmx_ticker import GmxRequestTickerData

        ticker_data = {"ETH": {"minPrice": "3000"}}
        ticker = GmxRequestTickerData(ticker_data, "ETH", "SPOT", has_been_json_encoded=True)
        r1 = ticker.init_data()
        r2 = ticker.init_data()
        assert r1 is ticker
        assert r2 is ticker

    @pytest.mark.ticker
    def test_ticker_symbol_name_preserved(self):
        from bt_api_py.containers.tickers.gmx_ticker import GmxRequestTickerData

        ticker = GmxRequestTickerData({}, "SOL", "SPOT", has_been_json_encoded=True)
        ticker.init_data()
        assert ticker.get_symbol_name() == "SOL"


class TestGmxNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_signed_prices_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_signed_prices_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tokens_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_tokens_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_markets_info_normalize_with_none(self):
        result, status = GmxRequestDataSpot._get_markets_info_normalize_function(None, None)
        assert result == []
        assert status is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
