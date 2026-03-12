"""
Tests for PancakeSwap DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import os
import queue
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from bt_api_py.containers.exchanges.pancakeswap_exchange_data import PancakeSwapExchangeData
from bt_api_py.containers.pools.pancakeswap_pool import PancakeSwapPoolData, PancakeSwapPoolList
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.pancakeswap_ticker import PancakeSwapRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_pancakeswap.spot import PancakeSpotRequestData


class TestPancakeSpotRequestData:
    """Test PancakeSpot feed implementation."""

    @pytest.fixture
    def mock_feed(self):
        """Fixture to create a mock PancakeSpot feed."""
        data_queue = queue.Queue()
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            feed = PancakeSpotRequestData(data_queue)
            return feed

    def test_feed_capabilities(self, mock_feed):
        """Test feed capabilities."""
        capabilities = mock_feed._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

    def test_feed_initialization(self, mock_feed):
        """Test feed initialization."""
        assert mock_feed.exchange_name == "PANCAKESWAP___DEX"
        assert mock_feed.asset_type == "SPOT"
        assert hasattr(mock_feed, "request_logger")

    # ==================== Server Time Tests ====================

    def test_get_server_time(self, mock_feed):
        """Test getting server time returns RequestData."""
        result = mock_feed.get_server_time()
        assert isinstance(result, RequestData)

    def test_get_server_time_tuple(self, mock_feed):
        """Test _get_server_time returns (method, path, params, extra_data)."""
        method, path, params, extra_data = mock_feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"
        assert "server_time" in extra_data

    # ==================== Ticker Tests ====================

    @pytest.mark.ticker
    def test_get_tick(self, mock_feed):
        """Test get_tick method."""
        # Mock the request method
        mock_feed.request = Mock(return_value=Mock(data=Mock()))

        mock_feed.get_tick("BTCB/USDT")
        assert mock_feed.request.called

    @pytest.mark.ticker
    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        input_data = {"price": "50000.0", "symbol": "BTCB/USDT"}
        result, status = PancakeSpotRequestData._get_tick_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    # ==================== Depth Tests ====================

    @pytest.mark.orderbook
    def test_get_depth(self, mock_feed):
        """Test get_depth method."""
        # Mock the request method
        mock_feed.request = Mock(return_value=Mock(data=Mock()))

        mock_feed.get_depth("BTCB/USDT", 20)
        assert mock_feed.request.called

    @pytest.mark.orderbook
    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        input_data = {
            "bids": [["50000", "1.0"], ["49999", "2.0"]],
            "asks": [["50001", "1.0"], ["50002", "2.0"]],
        }
        result, status = PancakeSpotRequestData._get_depth_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    # ==================== Kline Tests ====================

    @pytest.mark.kline
    def test_get_kline(self, mock_feed):
        """Test get_kline method."""
        # Mock the request method
        mock_feed.request = Mock(return_value=Mock(data=Mock()))

        mock_feed.get_kline("BTCB/USDT", "1h", 100)
        assert mock_feed.request.called

    @pytest.mark.kline
    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = [
            [1234567890, "50000", "51000", "49000", "50500", "1000"],
            [1234567900, "50500", "51500", "50000", "51000", "1500"],
        ]
        result, status = PancakeSpotRequestData._get_kline_normalize_function(input_data, None)
        assert status
        assert len(result) == 2

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, mock_feed):
        """Test get_exchange_info method."""
        # Mock the request method
        mock_feed.request = Mock(return_value=Mock(data=Mock()))

        mock_feed.get_exchange_info()
        assert mock_feed.request.called

    # ==================== Pool Tests ====================

    def test_get_pools(self, mock_feed):
        """Test get_pools method."""
        # Mock the request method
        mock_feed.request = Mock(return_value=Mock(data=Mock()))

        mock_feed.get_pools(first=10, min_tvl=100000)
        assert mock_feed.request.called

    def test_get_pool(self, mock_feed):
        """Test get_pool method."""
        # Mock the request method
        mock_feed.request = Mock(return_value=Mock(data=Mock()))

        pool_address = "0x..."
        mock_feed.get_pool(pool_address)
        assert mock_feed.request.called

    def test_get_pool_normalize_function(self):
        """Test pool normalize function."""
        input_data = {"id": "0x...", "symbol": "BTCB/USDT", "tvl": "1000000", "volume24h": "50000"}
        result, status = PancakeSpotRequestData._get_pool_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    @pytest.mark.skip(reason="Integration test - requires network access")
    def test_integration_real_api_calls(self, mock_feed):
        """Integration test for real API calls - skipped."""

    @pytest.mark.skip(reason="Integration test - requires network access")
    def test_integration_config_loading(self):
        """Test configuration file loading - skipped."""


class TestPancakeSwapExchangeData:
    """Test PancakeSwap exchange data container."""

    @pytest.fixture
    def exchange_data(self):
        """Fixture to create PancakeSwap exchange data instance."""
        return PancakeSwapExchangeData()

    def test_exchange_data_initialization(self, exchange_data):
        """Test exchange data initialization."""
        assert exchange_data.exchange_name == "pancakeswap"
        assert exchange_data.rest_url == ""
        assert "1m" in exchange_data.kline_periods
        assert "USDT" in exchange_data.legal_currency
        assert "BNB" in exchange_data.legal_currency

    def test_load_from_config(self, exchange_data):
        """Test loading configuration from YAML."""
        result = exchange_data._load_from_config("spot")
        assert isinstance(result, bool)

    def test_get_supported_tokens(self, exchange_data):
        """Test getting supported tokens."""
        tokens = exchange_data.get_supported_tokens()
        assert isinstance(tokens, list)

    def test_get_supported_pairs(self, exchange_data):
        """Test getting supported trading pairs."""
        pairs = exchange_data.get_supported_pairs()
        assert isinstance(pairs, list)

    def test_get_fee_config(self, exchange_data):
        """Test getting fee configuration."""
        fees = exchange_data.get_fee_config("spot")
        if fees:
            assert "maker" in fees
            assert "taker" in fees

    def test_symbol_address_conversion(self, exchange_data):
        """Test symbol to address conversion."""
        # Test known symbol
        address = exchange_data.symbol_to_address("BTCB/USDT")
        assert isinstance(address, str)

    def test_get_minimum_trade_amount(self, exchange_data):
        """Test getting minimum trade amounts."""
        amount = exchange_data.get_minimum_trade_amount("USDT")
        assert isinstance(amount, (int, float))
        assert amount >= 0

    def test_get_capabilities(self, exchange_data):
        """Test getting exchange capabilities."""
        capabilities = exchange_data.get_capabilities("spot")
        assert isinstance(capabilities, list)

    @pytest.mark.kline
    def test_kline_periods(self, exchange_data):
        """Test kline periods are defined."""
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self, exchange_data):
        """Test legal currencies."""
        assert "USDT" in exchange_data.legal_currency
        assert "BNB" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency


class TestPancakeSwapTickerData:
    """Test PancakeSwap ticker data container."""

    @pytest.mark.ticker
    def test_ticker_creation(self):
        """Test creating ticker data."""
        ticker = PancakeSwapRequestTickerData(
            symbol="BTCB/USDT",
            price=50000.0,
            timestamp=1640995200000,
            volume=1.5,
            quote_volume=75000.0,
            high=51000.0,
            low=49000.0,
        )

        assert ticker.symbol == "BTCB/USDT"
        assert ticker.price == 50000.0
        assert ticker.volume == 1.5
        assert ticker.quote_volume == 75000.0

    @pytest.mark.ticker
    def test_ticker_calculations(self):
        """Test ticker calculation properties."""
        ticker = PancakeSwapRequestTickerData(
            symbol="BTCB/USDT",
            price=50000.0,
            timestamp=1640995200000,
            volume=1.5,
            quote_volume=75000.0,
            high=51000.0,
            low=49000.0,
        )

        # Test price change
        assert ticker.price_change == 2000.0

        # Test to_dict
        ticker_dict = ticker.to_dict()
        assert "symbol" in ticker_dict
        assert "price" in ticker_dict

    @pytest.mark.ticker
    def test_ticker_from_dict(self):
        """Test creating ticker from dictionary."""
        data = {
            "symbol": "ETH/USDT",
            "price": 3000.0,
            "timestamp": 1640995200000,
            "volume": 2.0,
            "quote_volume": 6000.0,
        }

        ticker = PancakeSwapRequestTickerData.from_dict(data)
        assert ticker.symbol == "ETH/USDT"
        assert ticker.price == 3000.0


class TestPancakeSwapPoolData:
    """Test PancakeSwap pool data container."""

    def test_pool_creation(self):
        """Test creating pool data."""
        from bt_api_py.containers.pools.pancakeswap_pool import PancakeSwapLiquidityData

        liquidity_data = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=5000.0,
            token0_symbol="USDT",
            token1_symbol="BTCB",
            token0_address="0x55d398326f99059fF775485246999027B3197955",
            token1_address="0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC",
            token0_decimals=18,
            token1_decimals=18,
        )

        pool = PancakeSwapPoolData(
            pool_address="0xabcdef1234567890",
            symbol="BTCB/USDT",
            liquidity_data=liquidity_data,
            volume_24h=10000.0,
            volume_24h_usd=500000000.0,
            tvl=1000000000.0,
        )

        assert pool.symbol == "BTCB/USDT"
        assert pool.volume_24h == 10000.0
        assert pool.tvl == 1000000000.0

    def test_pool_list_operations(self):
        """Test pool list operations."""
        from bt_api_py.containers.pools.pancakeswap_pool import PancakeSwapLiquidityData

        # Create sample pools
        pools = []
        for i in range(5):
            liquidity_data = PancakeSwapLiquidityData(
                token0_reserve=1000.0 * (i + 1),
                token1_reserve=5000.0 * (i + 1),
                token0_symbol="TOKEN",
                token1_symbol="USDT",
                token0_address=f"0x{'0' * 40}",
                token1_address=f"0x{'1' * 40}",
                token0_decimals=18,
                token1_decimals=18,
            )
            pool = PancakeSwapPoolData(
                pool_address=f"0x{i:40x}",
                symbol=f"TOKEN{i}/USDT",
                liquidity_data=liquidity_data,
                volume_24h=1000 * (i + 1),
                volume_24h_usd=1000000 * (i + 1),
                tvl=10000000 * (i + 1),
            )
            pools.append(pool)

        pool_list = PancakeSwapPoolList(
            pools=pools, total_pools=5, total_liquidity_usd=0, total_volume_24h_usd=0
        )

        # Test filtering
        filtered = pool_list.filter_by_volume(2000000)
        assert len(filtered.pools) == 4

        # Test sorting
        sorted_by_tvl = pool_list.sort_by_tvl(descending=True)
        assert sorted_by_tvl.pools[0].tvl == 50000000

    def test_pool_json_serialization(self):
        """Test pool JSON serialization."""
        from bt_api_py.containers.pools.pancakeswap_pool import PancakeSwapLiquidityData

        liquidity_data = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=5000.0,
            token0_symbol="USDT",
            token1_symbol="BTCB",
            token0_address="0x55d398326f99059fF775485246999027B3197955",
            token1_address="0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC",
            token0_decimals=18,
            token1_decimals=18,
        )

        pool = PancakeSwapPoolData(
            pool_address="0xabcdef1234567890",
            symbol="BTCB/USDT",
            liquidity_data=liquidity_data,
            volume_24h=10000.0,
            volume_24h_usd=500000000.0,
            tvl=1000000000.0,
        )

        # Test JSON serialization
        json_str = pool.to_json()
        assert isinstance(json_str, str)

        # Test deserialization
        pool_from_json = PancakeSwapPoolData.from_json(json_str)
        assert pool_from_json.symbol == pool.symbol


class TestPancakeSwapRegistration:
    """Test cases for PancakeSwap registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that PancakeSwap is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("PANCAKESWAP___DEX")
        assert exchange_class is not None
        assert exchange_class.__name__ == "PancakeSpotRequestData"


class TestPancakeStandardInterfaces:
    """Test standard Feed interface methods for PancakeSwap."""

    @pytest.fixture
    def mock_feed(self):
        data_queue = queue.Queue()
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            feed = PancakeSpotRequestData(data_queue)
            feed.request = Mock(return_value=Mock(spec=RequestData))
            return feed

    @pytest.mark.ticker
    def test_get_tick_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTCB/USDT")
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCB/USDT"

    @pytest.mark.orderbook
    def test_get_depth_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTCB/USDT")
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "BTCB/USDT"

    @pytest.mark.kline
    def test_get_kline_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTCB/USDT", "1h", 100)
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["interval"] == "1h"

    def test_get_exchange_info_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"
        assert extra_data["exchange_name"] == "PANCAKESWAP___DEX"

    def test_make_order_calls_request(self, mock_feed):
        mock_feed.make_order("BTCB/USDT", 1.0, 50000, "LIMIT")
        assert mock_feed.request.called
        extra_data = mock_feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, mock_feed):
        mock_feed.cancel_order("BTCB/USDT", "order_123")
        assert mock_feed.request.called
        extra_data = mock_feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, mock_feed):
        mock_feed.query_order("BTCB/USDT", "order_123")
        assert mock_feed.request.called
        extra_data = mock_feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, mock_feed):
        mock_feed.get_open_orders("BTCB/USDT")
        assert mock_feed.request.called
        extra_data = mock_feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, mock_feed):
        mock_feed.get_account("BTCB")
        assert mock_feed.request.called
        extra_data = mock_feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, mock_feed):
        mock_feed.get_balance("BTCB")
        assert mock_feed.request.called
        extra_data = mock_feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"


class TestPancakeBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_pancakeswap.request_base import PancakeSwapRequestData

        caps = PancakeSwapRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestPancakeNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = PancakeSpotRequestData._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = PancakeSpotRequestData._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = PancakeSpotRequestData._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = PancakeSpotRequestData._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pool_normalize_with_none(self):
        result, status = PancakeSpotRequestData._get_pool_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pools_normalize_with_none(self):
        result, status = PancakeSpotRequestData._get_pools_normalize_function(None, None)
        assert result == []
        assert status is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
