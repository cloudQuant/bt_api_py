"""
Tests for GMX DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import queue
import pytest
from unittest.mock import Mock, patch, MagicMock
from enum import Enum


class MockGmxChain(str, Enum):
    """Mock GmxChain for testing."""
    ARBITRUM = "arbitrum"
    AVALANCHE = "avalanche"


from bt_api_py.feeds.live_gmx.spot import GmxRequestDataSpot
from bt_api_py.containers.exchanges.gmx_exchange_data import (
    GmxExchangeDataSpot,
    GmxChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData


class TestGmxRequestDataSpot:
    """Test cases for GmxRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def gmx_spot(self, mock_data_queue):
        """Create GmxRequestDataSpot instance."""
        with patch('bt_api_py.feeds.http_client.HttpClient', return_value=MagicMock()):
            instance = GmxRequestDataSpot(mock_data_queue)
            return instance

    @pytest.fixture
    def gmx_spot_avalanche(self, mock_data_queue):
        """Create GmxRequestDataSpot instance on Avalanche."""
        with patch('bt_api_py.feeds.http_client.HttpClient', return_value=MagicMock()):
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
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_KLINE in capabilities

    # ==================== Ticker Tests ====================

    def test_get_tick(self, gmx_spot):
        """Test get_tick method."""
        symbol = "BTC"
        path, params, extra_data = gmx_spot._get_tick(symbol)

        assert extra_data['request_type'] == "get_tick"
        assert extra_data['symbol_name'] == symbol
        assert extra_data['exchange_name'] == "GMX___DEX"
        assert extra_data['chain'] == "arbitrum"

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        input_data = {
            "BTC": {
                "minPrice": "49000",
                "maxPrice": "51000",
                "oraclePrice": "50000",
                "markPrice": "50001",
                "propagationTime": 1234567890
            }
        }
        result, status = GmxRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_tick_with_symbol_filter(self, gmx_spot):
        """Test get_tick with symbol filtering."""
        # Mock the request method to return multiple tickers
        gmx_spot.request = Mock(return_value=Mock(data={
            "BTC": {"minPrice": "49000"},
            "ETH": {"minPrice": "3000"}
        }))

        result = gmx_spot.get_tick("BTC")
        assert gmx_spot.request.called

    # ==================== Kline Tests ====================

    def test_get_kline(self, gmx_spot):
        """Test get_kline method."""
        symbol = "BTC"
        period = "1h"
        count = 100

        path, params, extra_data = gmx_spot._get_kline(symbol, period, count)

        assert extra_data['request_type'] == "get_kline"
        assert extra_data['symbol_name'] == symbol
        assert extra_data['period'] == period
        assert params['tokenSymbol'] == symbol
        assert params['period'] == "1h"

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = {
            "period": "1h",
            "candles": [
                [1234567890, "50000", "51000", "49000", "50500", "1000"],
                [1234567900, "50500", "51500", "50000", "51000", "1500"],
            ]
        }
        result, status = GmxRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2  # Two candles

    def test_get_kline_period_mapping(self, gmx_spot):
        """Test kline period mapping."""
        # Test various periods
        for period, expected in [("1m", "1m"), ("5m", "5m"), ("1h", "1h"), ("1d", "1d")]:
            path, params, extra_data = gmx_spot._get_kline("BTC", period, 10)
            assert params['period'] == expected

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, gmx_spot):
        """Test get_exchange_info method."""
        path, params, extra_data = gmx_spot._get_exchange_info()

        assert extra_data['request_type'] == "get_exchange_info"
        assert extra_data['exchange_name'] == "GMX___DEX"

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = [
            {"market": "BTC", "name": "Bitcoin"},
            {"market": "ETH", "name": "Ethereum"}
        ]
        result, status = GmxRequestDataSpot._get_exchange_info_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_tokens_normalize_function(self):
        """Test tokens normalize function."""
        input_data = [
            {"symbol": "BTC", "name": "Bitcoin"},
            {"symbol": "ETH", "name": "Ethereum"}
        ]
        result, status = GmxRequestDataSpot._get_tokens_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Depth Tests ====================

    def test_get_depth(self, gmx_spot):
        """Test get_depth method."""
        symbol = "BTC"
        path, params, extra_data = gmx_spot._get_depth(symbol)

        assert extra_data['request_type'] == "get_depth"
        assert extra_data['symbol_name'] == symbol

    def test_get_markets_info_normalize_function(self):
        """Test markets info normalize function."""
        input_data = [
            {"market": "BTC", "liquidity": "1000000"},
            {"market": "ETH", "liquidity": "500000"}
        ]
        result, status = GmxRequestDataSpot._get_markets_info_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Signed Price Tests ====================

    def test_get_signed_prices(self, gmx_spot):
        """Test get_signed_prices method."""
        path, params, extra_data = gmx_spot._get_signed_prices()

        assert extra_data['request_type'] == "get_signed_prices"
        assert extra_data['exchange_name'] == "GMX___DEX"

    def test_get_signed_prices_normalize_function(self):
        """Test signed prices normalize function."""
        input_data = {
            "signedPrices": [
                {"token": "BTC", "price": "50000", "signature": "0x..."}
            ]
        }
        result, status = GmxRequestDataSpot._get_signed_prices_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_ticker(self, gmx_spot):
        """Integration test for get_ticker - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_kline(self, gmx_spot):
        """Integration test for get_kline - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_exchange_info(self, gmx_spot):
        """Integration test for get_exchange_info - skipped."""
        pass


class TestGmxExchangeDataSpot:
    """Test cases for GmxExchangeDataSpot."""

    def test_init_with_chain_enum(self):
        """Test initialization with chain enum."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert exchange_data.chain == GmxChain.ARBITRUM

    def test_init_with_chain_string(self):
        """Test initialization with chain string."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
            exchange_data = GmxExchangeDataSpot(chain="arbitrum")
            assert exchange_data.chain.value == "arbitrum"

    def test_get_chain_value(self):
        """Test get_chain_value method."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.AVALANCHE)
            assert exchange_data.get_chain_value() == "avalanche"

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert exchange_data.get_rest_url() == "https://arbitrum-api.gmxinfra.io"

    def test_api_urls(self):
        """Test API URLs are defined."""
        assert GmxExchangeDataSpot.API_URLS is not None
        assert GmxChain.ARBITRUM in GmxExchangeDataSpot.API_URLS
        assert GmxChain.AVALANCHE in GmxExchangeDataSpot.API_URLS

    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
            exchange_data = GmxExchangeDataSpot(chain=GmxChain.ARBITRUM)
            assert "BTC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency
            assert "USD" in exchange_data.legal_currency

    def test_supported_symbols(self):
        """Test supported symbols."""
        with patch('bt_api_py.containers.exchanges.gmx_exchange_data._get_gmx_config', return_value=None):
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
