"""
HitBTC Integration Tests

This module provides basic tests for HitBTC exchange integration.
Tests basic functionality without requiring API keys.
"""

import queue

import pytest

from bt_api_py.feeds.live_hitbtc.spot import HitBtcSpotRequestData


class TestHitBtcIntegration:
    """Integration tests for HitBTC"""

    @pytest.mark.skip(reason="Requires API keys for real API calls")
    def test_market_data_api(self):
        """Test market data API calls (requires network)"""
        data_queue = queue.Queue()
        feed = HitBtcSpotRequestData(data_queue)

        # Test server time
        server_time = feed.get_server_time()
        assert server_time.status is True

        # Test ticker
        ticker = feed.get_ticker("BTCUSDT")
        assert ticker.status is True

    @pytest.mark.skip(reason="Requires API keys for real API calls")
    def test_trading_api(self):
        """Test trading API calls (requires API keys)"""
        queue.Queue()

        # This would require API keys to test
        # feed = HitBtcSpotRequestData(data_queue, public_key="key", private_key="secret")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
