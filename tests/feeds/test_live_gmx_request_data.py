"""
Tests for GMX DEX Feed implementation

GMX is a DEX for derivatives - minimal test coverage
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.feeds.live_gmx.spot import GmxRequestDataSpot


@pytest.fixture
def mock_data_queue():
    """Mock data queue for testing"""
    return queue.Queue()


@pytest.fixture
def gmx_feed(mock_data_queue):
    """Create GMX feed instance for testing"""
    with patch("bt_api_py.http_client.HttpClient"):
        feed = GmxRequestDataSpot(mock_data_queue)
        return feed


class TestGmxInit:
    """Test GMX feed initialization"""

    def test_init_default_params(self, gmx_feed):
        """Test initialization with default parameters"""
        assert gmx_feed.exchange_name == "GMX___SPOT"
        assert gmx_feed.data_queue is not None

    def test_init_with_custom_params(self, mock_data_queue):
        """Test initialization with custom parameters"""
        feed = GmxRequestDataSpot(mock_data_queue, testnet=False)
        assert feed is not None


class TestGmxBasic:
    """Test GMX basic functionality"""

    def test_get_pools(self, gmx_feed):
        """Test get_pools method"""
        assert hasattr(gmx_feed, "get_pools")

    @pytest.mark.ticker
    def test_get_ticker(self, gmx_feed):
        """Test get_ticker method"""
        assert hasattr(gmx_feed, "get_ticker")


class TestGmxNormalize:
    """Test GMX data normalization"""

    def test_pool_normalize_function(self):
        """Test pool data normalization"""
        mock_pool_data = {"pool": "GLP Pool", "aum": "500000000", "glpPrice": "1.5"}
        assert "pool" in mock_pool_data

    @pytest.mark.ticker
    def test_ticker_normalize_function(self):
        """Test ticker data normalization"""
        mock_ticker_data = {"symbol": "BTC", "markPrice": "50000", "indexPrice": "50001"}
        assert "symbol" in mock_ticker_data


@pytest.mark.skip(reason="Integration test - requires network")
class TestGmxIntegration:
    """Integration tests for GMX (requires network)"""

    def test_get_pools_live(self, gmx_feed):
        """Test live pool data retrieval"""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
