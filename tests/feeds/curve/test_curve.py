"""
Tests for Curve DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import queue
import pytest
from unittest.mock import Mock, patch, MagicMock
from enum import Enum


class MockCurveChain(str, Enum):
    """Mock CurveChain for testing."""
    ETHEREUM = "ETHEREUM"
    ARBITRUM = "ARBITRUM"
    POLYGON = "POLYGON"


from bt_api_py.feeds.live_curve.spot import CurveRequestDataSpot
from bt_api_py.containers.exchanges.curve_exchange_data import (
    CurveExchangeDataSpot,
    CurveChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData


class TestCurveRequestDataSpot:
    """Test cases for CurveRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def curve_spot(self, mock_data_queue):
        """Create CurveRequestDataSpot instance."""
        with patch('bt_api_py.feeds.http_client.HttpClient', return_value=MagicMock()):
            instance = CurveRequestDataSpot(mock_data_queue, chain=MockCurveChain.ETHEREUM)
            return instance

    def test_init(self, curve_spot):
        """Test initialization."""
        assert curve_spot.exchange_name == "CURVE___DEX"
        # The chain is converted to CurveChain enum
        assert curve_spot.chain == CurveChain.ETHEREUM

    def test_capabilities(self, curve_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = curve_spot._capabilities()
        assert Capability.GET_EXCHANGE_INFO in capabilities

    # ==================== Pools Tests ====================

    def test_get_pools_normalize_function(self):
        """Test pools normalize function."""
        input_data = {
            "data": {
                "poolData": [
                    {"id": "pool1", "name": "Pool 1", "totalLiquidity": "1000000"},
                    {"id": "pool2", "name": "Pool 2", "totalLiquidity": "2000000"},
                ]
            }
        }
        result, status = CurveRequestDataSpot._get_pools_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_get_pools(self, curve_spot):
        """Test get_pools method - mocked."""
        # Mock the request method to avoid actual API call
        with patch.object(curve_spot, 'request') as mock_request:
            mock_request.return_value = Mock(extra_data={'request_type': 'get_pools'})
            result = curve_spot.get_pools()
            # Verify request was called
            assert mock_request.called
            # Check the extra_data passed to request
            call_args = mock_request.call_args
            assert call_args[1]['extra_data']['request_type'] == "get_pools"

    # ==================== Volumes Tests ====================

    def test_get_volumes_normalize_function(self):
        """Test volumes normalize function."""
        input_data = {
            "data": {
                "dailyVolume": "1000000",
                "weeklyVolume": "5000000"
            }
        }
        result, status = CurveRequestDataSpot._get_volumes_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_volumes(self, curve_spot):
        """Test get_volumes method - mocked."""
        with patch.object(curve_spot, 'request') as mock_request:
            mock_request.return_value = Mock(extra_data={'request_type': 'get_volumes'})
            result = curve_spot.get_volumes()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]['extra_data']['request_type'] == "get_volumes"

    # ==================== TVL Tests ====================

    def test_get_tvl_normalize_function(self):
        """Test TVL normalize function."""
        input_data = {
            "data": {
                "totalTVL": "10000000"
            }
        }
        result, status = CurveRequestDataSpot._get_tvl_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_tvl(self, curve_spot):
        """Test get_tvl method - mocked."""
        with patch.object(curve_spot, 'request') as mock_request:
            mock_request.return_value = Mock(extra_data={'request_type': 'get_tvl'})
            result = curve_spot.get_tvl()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]['extra_data']['request_type'] == "get_tvl"

    # ==================== APY Tests ====================

    def test_get_apys_normalize_function(self):
        """Test APYs normalize function."""
        input_data = {
            "data": {
                "poolApy": {
                    "pool1": 0.05,
                    "pool2": 0.10
                }
            }
        }
        result, status = CurveRequestDataSpot._get_apys_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_apys(self, curve_spot):
        """Test get_apys method - mocked."""
        with patch.object(curve_spot, 'request') as mock_request:
            mock_request.return_value = Mock(extra_data={'request_type': 'get_apys'})
            result = curve_spot.get_apys()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]['extra_data']['request_type'] == "get_apys"

    # ==================== Pool Detail Tests ====================

    def test_get_pool_summary_normalize_function(self):
        """Test pool summary normalize function."""
        input_data = {
            "data": {
                "poolData": {
                    "name": "3pool",
                    "address": "0x...",
                    "totalLiquidity": "10000000"
                }
            }
        }
        result, status = CurveRequestDataSpot._get_pool_summary_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    def test_get_pool_summary(self, curve_spot):
        """Test get_pool_summary method."""
        pool_address = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"

        path, params, extra_data = curve_spot._get_pool_summary(pool_address)

        assert extra_data['request_type'] == "get_pool_summary"
        assert extra_data['pool_address'] == pool_address

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, curve_spot):
        """Test get_exchange_info method - mocked."""
        # _get_exchange_info internally calls _get_pools which calls request
        with patch.object(curve_spot, 'request') as mock_request:
            mock_request.return_value = Mock(extra_data={'request_type': 'get_pools'})
            result = curve_spot._get_exchange_info()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]['extra_data']['request_type'] == "get_pools"

    # ==================== Ticker Tests (via pools) ====================

    def test_get_tick(self, curve_spot):
        """Test get_tick method - Curve uses pool data."""
        pool_address = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"

        path, params, extra_data = curve_spot._get_tick(pool_address)

        assert extra_data['request_type'] == "get_pool_summary"

    def test_get_tick_normalize_function(self):
        """Test tick normalize function - uses pool summary."""
        input_data = {
            "data": {
                "poolData": {
                    "name": "3pool",
                    "virtualPrice": "1.01"
                }
            }
        }
        result, status = CurveRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_pools(self, curve_spot):
        """Integration test for get_pools - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_tvl(self, curve_spot):
        """Integration test for get_tvl - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_apys(self, curve_spot):
        """Integration test for get_apys - skipped."""
        pass


class TestCurveExchangeDataSpot:
    """Test cases for CurveExchangeDataSpot."""

    def test_init_with_chain_enum(self):
        """Test initialization with chain enum."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert exchange_data.chain == MockCurveChain.ETHEREUM
            assert exchange_data.rest_url == "https://api.curve.finance"

    def test_init_with_chain_string(self):
        """Test initialization with chain string."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain="ETHEREUM")
            assert exchange_data.chain.value == "ETHEREUM"

    def test_get_chain_value(self):
        """Test get_chain_value method."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert exchange_data.get_chain_value() == "ethereum"

    def test_get_chain_name(self):
        """Test get_chain_name method."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ARBITRUM)
            assert exchange_data.get_chain_name() == "arbitrum"

    def test_get_rest_path(self):
        """Test get_rest_path method."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            path = exchange_data.get_rest_path("get_pools")
            assert "getPools/ethereum/main" in path

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert exchange_data.get_rest_url() == "https://api.curve.finance"

    def test_factory_addresses(self):
        """Test factory addresses are defined."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert hasattr(CurveExchangeDataSpot, 'FACTORY_ADDRESSES')
            assert CurveChain.ETHEREUM in exchange_data.FACTORY_ADDRESSES

    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch('bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config', return_value=None):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert "USDT" in exchange_data.legal_currency
            assert "USDC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency


class TestCurveRegistration:
    """Test cases for Curve registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that Curve is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("CURVE___DEX")
        assert exchange_class is not None
        assert exchange_class.__name__ == "CurveRequestDataSpot"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
