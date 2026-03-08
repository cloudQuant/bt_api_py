"""
Tests for Raydium DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

from enum import Enum
from unittest.mock import MagicMock, Mock, patch

import pytest


class MockRaydiumChain(str, Enum):
    """Mock RaydiumChain for testing."""

    SOLANA = "SOLANA"


from bt_api_py.containers.exchanges.raydium_exchange_data import (
    RaydiumChain,
    RaydiumExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_raydium.spot import RaydiumRequestDataSpot


class TestRaydiumRequestDataSpot:
    """Test cases for RaydiumRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def raydium_spot(self, mock_data_queue):
        """Create RaydiumRequestDataSpot instance."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = RaydiumRequestDataSpot(mock_data_queue)
            return instance

    def test_init(self, raydium_spot):
        """Test initialization."""
        assert raydium_spot.exchange_name == "RAYDIUM___DEX"
        assert raydium_spot.asset_type == "DEX"
        assert raydium_spot.chain == RaydiumChain.SOLANA

    def test_capabilities(self, raydium_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = raydium_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

    def test_get_server_time(self, raydium_spot):
        """Test get_server_time returns RequestData."""
        result = raydium_spot.get_server_time()
        assert isinstance(result, RequestData)

    def test_get_server_time_tuple(self, raydium_spot):
        """Test _get_server_time returns tuple."""
        path, params, extra_data = raydium_spot._get_server_time()
        assert extra_data["request_type"] == "get_server_time"
        assert "server_time" in extra_data

    # ==================== Pools Tests ====================

    def test_get_pools(self, raydium_spot):
        """Test get_pools method."""
        path, params, extra_data = raydium_spot._get_pools(first=10, min_tvl=100000)

        assert extra_data["request_type"] == "get_pools"
        assert extra_data["exchange_name"] == "RAYDIUM___DEX"
        assert params["pageSize"] == 10

    def test_get_pools_normalize_function(self):
        """Test pools normalize function."""
        input_data = {
            "success": True,
            "data": [
                {"id": "pool1", "name": "Pool 1", "tvl": 100000},
                {"id": "pool2", "name": "Pool 2", "tvl": 200000},
            ],
        }
        result, status = RaydiumRequestDataSpot._get_pools_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== Pool Detail Tests ====================

    def test_get_pool(self, raydium_spot):
        """Test get_pool method."""
        pool_id = "pool123"
        path, params, extra_data = raydium_spot._get_pool(pool_id)

        assert extra_data["request_type"] == "get_pool_ids"
        assert extra_data["pool_id"] == pool_id
        assert params["ids"] == pool_id

    def test_get_pool_normalize_function(self):
        """Test pool normalize function."""
        input_data = {
            "success": True,
            "data": [{"id": "pool123", "name": "Test Pool", "tvl": 100000}],
        }
        result, status = RaydiumRequestDataSpot._get_pool_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Ticker Tests ====================

    @pytest.mark.ticker
    def test_get_tick(self, raydium_spot):
        """Test get_tick method."""
        symbol = "SOL/USDC"
        path, params, extra_data = raydium_spot._get_tick(symbol)

        assert extra_data["request_type"] == "get_pool_by_mint"
        assert extra_data["symbol_name"] == symbol
        assert params["mint1"] == "SOL"
        assert params["mint2"] == "USDC"

    @pytest.mark.ticker
    def test_get_tick_normalize_function(self):
        """Test tick normalize function."""
        input_data = {"success": True, "data": [{"id": "pool1", "name": "SOL/USDC", "price": 100}]}
        result, status = RaydiumRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Depth Tests ====================

    @pytest.mark.orderbook
    def test_get_depth(self, raydium_spot):
        """Test get_depth method."""
        symbol = "SOL/USDC"
        path, params, extra_data = raydium_spot._get_depth(symbol, count=20)

        assert extra_data["request_type"] == "get_pool_by_mint"
        assert extra_data["symbol_name"] == symbol

    @pytest.mark.orderbook
    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        input_data = {"success": True, "data": [{"id": "pool1", "liquidity": "1000000"}]}
        result, status = RaydiumRequestDataSpot._get_depth_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Mint Prices Tests ====================

    def test_get_mint_prices(self, raydium_spot):
        """Test get_mint_prices method."""
        path, params, extra_data = raydium_spot._get_mint_prices()

        assert extra_data["request_type"] == "get_mint_price"

    def test_get_mint_prices_normalize_function(self):
        """Test mint prices normalize function."""
        input_data = {
            "success": True,
            "data": [{"address": "mint1", "price": 100}, {"address": "mint2", "price": 1}],
        }
        result, status = RaydiumRequestDataSpot._get_mint_prices_normalize_function(
            input_data, None
        )
        assert status == True
        assert len(result) == 2

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, raydium_spot):
        """Test get_exchange_info method."""
        path, params, extra_data = raydium_spot._get_exchange_info()

        assert extra_data["request_type"] == "get_pools"

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_pools(self, raydium_spot):
        """Integration test for get_pools - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    @pytest.mark.ticker
    def test_integration_get_tick(self, raydium_spot):
        """Integration test for get_tick - skipped."""
        pass


class TestRaydiumExchangeDataSpot:
    """Test cases for RaydiumExchangeDataSpot."""

    def test_init(self):
        """Test initialization."""
        with patch(
            "bt_api_py.containers.exchanges.raydium_exchange_data._get_raydium_config",
            return_value=None,
        ):
            exchange_data = RaydiumExchangeDataSpot()
            assert exchange_data.chain == RaydiumChain.SOLANA
            assert exchange_data.rest_url == "https://api-v3.raydium.io"

    def test_get_chain_value(self):
        """Test get_chain_value method."""
        with patch(
            "bt_api_py.containers.exchanges.raydium_exchange_data._get_raydium_config",
            return_value=None,
        ):
            exchange_data = RaydiumExchangeDataSpot()
            assert exchange_data.get_chain_value() == "SOLANA"

    def test_get_symbol(self):
        """Test get_symbol method."""
        with patch(
            "bt_api_py.containers.exchanges.raydium_exchange_data._get_raydium_config",
            return_value=None,
        ):
            exchange_data = RaydiumExchangeDataSpot()
            assert exchange_data.get_symbol("SOL-USDC") == "SOL/USDC"
            assert exchange_data.get_symbol("sol/usdc") == "SOL/USDC"

    def test_get_period(self):
        """Test get_period method."""
        with patch(
            "bt_api_py.containers.exchanges.raydium_exchange_data._get_raydium_config",
            return_value=None,
        ):
            exchange_data = RaydiumExchangeDataSpot()
            assert exchange_data.get_period("1m") == "60"
            assert exchange_data.get_period("1h") == "3600"

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch(
            "bt_api_py.containers.exchanges.raydium_exchange_data._get_raydium_config",
            return_value=None,
        ):
            exchange_data = RaydiumExchangeDataSpot()
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch(
            "bt_api_py.containers.exchanges.raydium_exchange_data._get_raydium_config",
            return_value=None,
        ):
            exchange_data = RaydiumExchangeDataSpot()
            assert "SOL" in exchange_data.legal_currency
            assert "USDC" in exchange_data.legal_currency
            assert "USDT" in exchange_data.legal_currency


class TestRaydiumRegistration:
    """Test cases for Raydium registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that Raydium is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("RAYDIUM___DEX")
        assert exchange_class is not None
        assert exchange_class.__name__ == "RaydiumRequestDataSpot"


class TestRaydiumStandardInterfaces:
    """Test standard Feed interface methods for Raydium."""

    @pytest.fixture
    def raydium_spot(self):
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = RaydiumRequestDataSpot(Mock())
            instance.request = Mock(return_value=Mock(spec=RequestData))
            return instance

    def test_make_order_calls_request(self, raydium_spot):
        result = raydium_spot.make_order("SOL/USDC", 1.0, 100, "LIMIT")
        assert raydium_spot.request.called
        extra_data = raydium_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, raydium_spot):
        result = raydium_spot.cancel_order("SOL/USDC", "order_123")
        assert raydium_spot.request.called
        extra_data = raydium_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, raydium_spot):
        result = raydium_spot.query_order("SOL/USDC", "order_123")
        assert raydium_spot.request.called
        extra_data = raydium_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, raydium_spot):
        result = raydium_spot.get_open_orders("SOL/USDC")
        assert raydium_spot.request.called
        extra_data = raydium_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, raydium_spot):
        result = raydium_spot.get_account("SOL")
        assert raydium_spot.request.called
        extra_data = raydium_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, raydium_spot):
        result = raydium_spot.get_balance("SOL")
        assert raydium_spot.request.called
        extra_data = raydium_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"


class TestRaydiumBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.capability import Capability
        from bt_api_py.feeds.live_raydium.request_base import RaydiumRequestData

        caps = RaydiumRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestRaydiumNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = RaydiumRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = RaydiumRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pools_normalize_with_none(self):
        result, status = RaydiumRequestDataSpot._get_pools_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pool_normalize_with_none(self):
        result, status = RaydiumRequestDataSpot._get_pool_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_mint_prices_normalize_with_none(self):
        result, status = RaydiumRequestDataSpot._get_mint_prices_normalize_function(None, None)
        assert result == []
        assert status is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
