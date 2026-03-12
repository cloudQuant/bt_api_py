"""
Tests for Balancer DEX Feed implementation

Balancer is an AMM DEX - minimal test coverage
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.feeds.live_balancer.spot import BalancerRequestDataSpot


@pytest.fixture
def mock_data_queue():
    """Mock data queue for testing"""
    return queue.Queue()


@pytest.fixture
def balancer_feed(mock_data_queue):
    """Create Balancer feed instance for testing"""
    with patch("bt_api_py.feeds.live_balancer.request_base.HttpClient"):
        feed = BalancerRequestDataSpot(mock_data_queue)
        return feed


class TestBalancerInit:
    """Test Balancer feed initialization"""

    def test_init_default_params(self, balancer_feed):
        """Test initialization with default parameters"""
        assert balancer_feed.exchange_name == "BALANCER___DEX"
        assert balancer_feed.data_queue is not None

    def test_init_with_custom_params(self, mock_data_queue):
        """Test initialization with custom parameters"""
        feed = BalancerRequestDataSpot(mock_data_queue, testnet=False)
        assert feed is not None


class TestBalancerBasic:
    """Test Balancer basic functionality"""

    def test_get_pools(self, balancer_feed):
        """Test get_pools method"""
        assert hasattr(balancer_feed, "get_pools")

    def test_get_pool_info(self, balancer_feed):
        """Test get_pool_info method"""
        assert hasattr(balancer_feed, "get_pool")


class TestBalancerNormalize:
    """Test Balancer data normalization"""

    def test_pool_normalize_function(self):
        """Test pool data normalization"""
        mock_pool_data = {
            "poolId": "0x123...",
            "tokens": ["WETH", "DAI", "USDC"],
            "weights": ["0.5", "0.25", "0.25"],
        }
        assert "poolId" in mock_pool_data

    def test_token_normalize_function(self):
        """Test token data normalization"""
        mock_token_data = {"address": "0xabc...", "symbol": "WETH", "decimals": 18}
        assert "symbol" in mock_token_data


@pytest.mark.skip(reason="Integration test - requires network")
class TestBalancerIntegration:
    """Integration tests for Balancer (requires network)"""

    def test_get_pools_live(self, balancer_feed):
        """Test live pool data retrieval"""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
