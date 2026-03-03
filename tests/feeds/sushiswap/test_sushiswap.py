"""
Tests for SushiSwap DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import queue
import pytest
from unittest.mock import Mock, patch, MagicMock
from enum import Enum
from typing import Any


class MockSushiSwapChain(str, Enum):
    """Mock SushiSwapChain for testing."""
    ETHEREUM = "ETHEREUM"
    POLYGON = "POLYGON"
    ARBITRUM = "ARBITRUM"


from bt_api_py.feeds.live_sushiswap.spot import SushiSwapRequestDataSpot
from bt_api_py.containers.exchanges.sushiswap_exchange_data import (
    SushiSwapExchangeDataSpot,
    SushiSwapChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData


class TestSushiSwapRequestDataSpot:
    """Test cases for SushiSwapRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def sushiswap_spot(self, mock_data_queue):
        """Create SushiSwapRequestDataSpot instance."""
        with patch('bt_api_py.feeds.http_client.HttpClient', return_value=MagicMock()):
            instance = SushiSwapRequestDataSpot(mock_data_queue)
            return instance

    def test_init(self, sushiswap_spot):
        """Test initialization."""
        assert sushiswap_spot.exchange_name == "SUSHISWAP___DEX"
        assert sushiswap_spot.asset_type == "ethereum"
        assert sushiswap_spot.chain == SushiSwapChain.ETHEREUM

    def test_capabilities(self, sushiswap_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = sushiswap_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_KLINE in capabilities

    def test_get_tick(self, sushiswap_spot):
        """Test get_tick method."""
        symbol = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        path, params, extra_data = sushiswap_spot._get_tick(symbol)

        assert extra_data['request_type'] == "get_tick"
        assert extra_data['symbol_name'] == symbol
        assert extra_data['exchange_name'] == "SUSHISWAP___DEX"
        assert extra_data['chain'] == "ETHEREUM"

    def test_get_tick_normalize_function(self):
        """Test tick normalize function."""
        input_data = {"price": "3000.50"}
        extra_data = {"symbol_name": "0x...", "chain": "ETHEREUM"}

        result, status = SushiSwapRequestDataSpot._get_tick_normalize_function(input_data, extra_data)
        assert status == True
        assert len(result) == 1
        assert result[0]['symbol'] == "0x..."
        assert result[0]['price'] == "3000.50"

    def test_get_tick_normalize_with_price_dict(self):
        """Test tick normalize with price dict."""
        input_data = {"price": 3000.50}
        extra_data = {"symbol_name": "WETH", "chain": "ETHEREUM"}

        result, status = SushiSwapRequestDataSpot._get_tick_normalize_function(input_data, extra_data)
        assert status == True
        assert result[0]['price'] == 3000.50

    def test_get_pool(self, sushiswap_spot):
        """Test get_pool method."""
        pool_address = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda"
        path, params, extra_data = sushiswap_spot._get_pool(pool_address)

        assert extra_data['request_type'] == "get_pool"
        assert extra_data['pool_address'] == pool_address
        assert extra_data['exchange_name'] == "SUSHISWAP___DEX"

    def test_get_pool_normalize_function(self):
        """Test pool normalize function."""
        input_data = {
            "address": "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda",
            "name": "SUSHI/ETH",
            "tvl": "1000000"
        }
        result, status = SushiSwapRequestDataSpot._get_pool_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Token Info Tests ====================

    def test_get_token_info(self, sushiswap_spot):
        """Test get_token_info method."""
        token_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        path, params, extra_data = sushiswap_spot._get_token_info(token_address)

        assert extra_data['request_type'] == "get_token_info"

    def test_get_token_info_normalize_function(self):
        """Test token info normalize function."""
        input_data = {
            "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            "symbol": "WETH",
            "name": "Wrapped Ether",
            "decimals": 18
        }
        result, status = SushiSwapRequestDataSpot._get_token_info_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_swap_quote(self, sushiswap_spot):
        """Test get_swap_quote method."""
        token_in = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        token_out = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        amount = "1"
        slippage_tolerance = 0.5

        path, params, extra_data = sushiswap_spot._get_swap_quote(
            token_in, token_out, amount, slippage_tolerance
        )

        assert extra_data['request_type'] == "get_quote"
        assert extra_data['token_in'] == token_in
        assert extra_data['token_out'] == token_out
        assert params['tokenIn'] == token_in
        assert params['tokenOut'] == token_out
        assert params['amount'] == amount
        assert params['maxSlippage'] == "0.005"

    def test_get_swap_quote_normalize_function(self):
        """Test swap quote normalize function."""
        input_data = {
            "amountIn": "1000000000000000000",
            "amountOut": "3000000000",
            "priceImpact": "0.005"
        }
        result, status = SushiSwapRequestDataSpot._get_swap_quote_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_exchange_info(self, sushiswap_spot):
        """Test get_exchange_info method."""
        path, params, extra_data = sushiswap_spot._get_exchange_info()

        assert extra_data['request_type'] == "get_exchange_info"
        assert extra_data['exchange_name'] == "SUSHISWAP___DEX"

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = [
            {"address": "0xtoken1", "symbol": "TOKEN1"},
            {"address": "0xtoken2", "symbol": "TOKEN2"}
        ]
        extra_data = {"chain": "ETHEREUM"}

        result, status = SushiSwapRequestDataSpot._get_exchange_info_normalize_function(input_data, extra_data)
        assert status == True
        assert len(result) == 1
        assert result[0]['tokens'] == input_data
        assert result[0]['count'] == 2

    def test_get_depth(self, sushiswap_spot):
        """Test get_depth method."""
        symbol = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda"
        path, params, extra_data = sushiswap_spot._get_depth(symbol)

        assert extra_data['request_type'] == "get_depth"
        assert extra_data['symbol_name'] == symbol

    def test_get_kline(self, sushiswap_spot):
        """Test get_kline method."""
        symbol = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        period = "1h"

        path, params, extra_data = sushiswap_spot._get_kline(symbol, period)

        assert extra_data['request_type'] == "get_kline"
        assert extra_data['symbol_name'] == symbol
        assert extra_data['period'] == period

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = [
            {"timestamp": 1234567890, "open": "50000", "high": "51000", "low": "49000", "close": "50500"},
            {"timestamp": 1234567900, "open": "50500", "high": "51500", "low": "50000", "close": "51000"}
        ]
        result, status = SushiSwapRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        input_data = {
            "liquidity": "1000000",
            "reserve0": "100",
            "reserve1": "1000"
        }
        result, status = SushiSwapRequestDataSpot._get_depth_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_tokens_normalize_function(self):
        """Test tokens normalize function."""
        input_data = [
            {"id": "token1", "symbol": "TOKEN1"},
            {"id": "token2", "symbol": "TOKEN2"}
        ]
        result, status = SushiSwapRequestDataSpot._get_tokens_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_tick(self, sushiswap_spot):
        """Integration test for get_tick - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_pool(self, sushiswap_spot):
        """Integration test for get_pool - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_swap_quote(self, sushiswap_spot):
        """Integration test for get_swap_quote - skipped."""
        pass


class TestSushiSwapExchangeDataSpot:
    """Test cases for SushiSwapExchangeDataSpot."""

    def test_init_with_chain_enum(self):
        """Test initialization with chain enum."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ETHEREUM)
            assert exchange_data.chain == SushiSwapChain.ETHEREUM
            assert exchange_data.rest_url == "https://api.sushi.com"

    def test_init_with_chain_string(self):
        """Test initialization with chain string."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain="ETHEREUM")
            assert exchange_data.chain.value == "ETHEREUM"

    def test_get_chain_id(self):
        """Test get_chain_id method."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ETHEREUM)
            assert exchange_data.get_chain_id() == "1"

    def test_get_chain_id_arbitrum(self):
        """Test get_chain_id for Arbitrum."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ARBITRUM)
            assert exchange_data.get_chain_id() == "42161"

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ETHEREUM)
            assert exchange_data.get_rest_url() == "https://api.sushi.com"

    def test_get_symbol(self):
        """Test get_symbol method."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ETHEREUM)
            address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            assert exchange_data.get_symbol(address) == address

    def test_chain_ids(self):
        """Test chain IDs are defined."""
        assert SushiSwapExchangeDataSpot.CHAIN_IDS is not None
        assert SushiSwapChain.ETHEREUM in SushiSwapExchangeDataSpot.CHAIN_IDS
        assert SushiSwapChain.ARBITRUM in SushiSwapExchangeDataSpot.CHAIN_IDS

    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ETHEREUM)
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.ETHEREUM)
            assert "USDT" in exchange_data.legal_currency
            assert "USDC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency

    def test_supported_chains(self):
        """Test supported chains."""
        assert SushiSwapChain.ETHEREUM in SushiSwapExchangeDataSpot.CHAIN_IDS
        assert SushiSwapChain.POLYGON in SushiSwapExchangeDataSpot.CHAIN_IDS
        assert SushiSwapChain.ARBITRUM in SushiSwapExchangeDataSpot.CHAIN_IDS

    def test_get_chain_id_polygon(self):
        """Test get_chain_id for Polygon."""
        with patch('bt_api_py.containers.exchanges.sushiswap_exchange_data._get_sushiswap_config', return_value=None):
            exchange_data = SushiSwapExchangeDataSpot(chain=SushiSwapChain.POLYGON)
            assert exchange_data.get_chain_id() == "137"  # Polygon


class TestSushiSwapRegistration:
    """Test cases for SushiSwap registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that SushiSwap is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("SUSHISWAP___DEX")
        assert exchange_class is not None
        assert exchange_class.__name__ == "SushiSwapRequestDataSpot"


if __name__ == "__main__":
    pytest.main([__file__])
