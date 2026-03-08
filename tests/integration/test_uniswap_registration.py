"""
Integration test for Uniswap registration.

This test verifies that the Uniswap integration can be registered
and used through the ExchangeRegistry.
"""

from unittest.mock import Mock, patch

import pytest

# Import the registration module to ensure Uniswap is registered
import bt_api_py.exchange_registers.register_uniswap  # noqa: F401 - module import triggers registration
from bt_api_py.containers.exchanges.uniswap_exchange_data import (
    UniswapChain,
    UniswapExchangeDataSpot,
)
from bt_api_py.feeds.live_uniswap.spot import UniswapRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


class TestUniswapRegistration:
    """Test cases for Uniswap registration."""

    def test_uniswap_feed_registration(self):
        """Test that Uniswap feed is registered."""
        # Check if Uniswap feed is registered using the public API
        assert ExchangeRegistry.has_exchange("UNISWAP___DEX")

        # Verify it's in the list of registered exchanges
        assert "UNISWAP___DEX" in ExchangeRegistry.list_exchanges()

    def test_uniswap_feed_class_correct(self):
        """Test that the registered feed class is correct."""
        # Access the internal registry to verify the class

        # The class in registry should match the imported class
        assert ExchangeRegistry._feed_classes.get("UNISWAP___DEX") == UniswapRequestDataSpot

    def test_uniswap_exchange_data_registration(self):
        """Test that Uniswap exchange data is registered."""
        # Access the internal registry to verify the exchange data class
        exchange_data_class = ExchangeRegistry._exchange_data_classes.get("UNISWAP___DEX")

        # Verify it's registered
        assert exchange_data_class is not None

        # Verify it's the correct class
        assert exchange_data_class == UniswapExchangeDataSpot

    @patch("bt_api_py.feeds.http_client.httpx.Client")
    def test_uniswap_spot_instantiation(self, mock_httpx_client):
        """Test that Uniswap spot feed can be instantiated."""
        # Create a mock data queue
        mock_data_queue = Mock()

        # Mock the httpx client response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {}}
        mock_client = Mock()
        mock_client.request.return_value = mock_response
        mock_httpx_client.return_value = mock_client

        # Instantiate the feed
        feed = UniswapRequestDataSpot(mock_data_queue, chain="ETHEREUM")

        # Verify the feed is created correctly
        assert feed.exchange_name == "UNISWAP___DEX"
        assert feed.chain.value == "ETHEREUM"
        assert feed.asset_type == "ethereum"

    def test_uniswap_balance_handler_registration(self):
        """Test that Uniswap balance handler is registered."""
        # Access the internal registry to verify the balance handler
        balance_handler = ExchangeRegistry._balance_handlers.get("UNISWAP___DEX")

        # Verify it's registered
        assert balance_handler is not None

        # Verify it's callable
        assert callable(balance_handler)

        # Test it works with a simple input
        test_accounts = [{"asset": "ETH", "balance": "1.0"}]
        value_result, cash_result = balance_handler(test_accounts)
        assert value_result == test_accounts
        assert cash_result == test_accounts

    def test_uniswap_exchange_data_instantiation(self):
        """Test that Uniswap exchange data can be instantiated."""
        # Instantiate the exchange data
        exchange_data = UniswapExchangeDataSpot(chain=UniswapChain.ETHEREUM)

        # Verify the exchange data is created correctly
        assert exchange_data.chain.value == "ETHEREUM"
        assert exchange_data.asset_type == "ethereum"
        assert exchange_data.rest_url == "https://trade-api.gateway.uniswap.org/v1"

    @patch.dict("os.environ", {"UNISWAP_API_KEY": "test_key"})
    def test_uniswap_api_key(self):
        """Test that Uniswap API key can be retrieved."""
        from bt_api_py.containers.exchanges.uniswap_exchange_data import UniswapExchangeData

        exchange_data = UniswapExchangeData(chain=UniswapChain.ETHEREUM)

        # The API key should be retrieved from environment
        assert exchange_data.get_api_key() == "test_key"

    def test_uniswap_can_create_feed_via_registry(self):
        """Test that Uniswap feed can be created via ExchangeRegistry."""
        mock_data_queue = Mock()

        # Create feed using the registry
        feed = ExchangeRegistry.create_feed("UNISWAP___DEX", mock_data_queue, chain="ETHEREUM")

        # Verify the feed is created correctly
        assert isinstance(feed, UniswapRequestDataSpot)
        assert feed.exchange_name == "UNISWAP___DEX"
        assert feed.chain.value == "ETHEREUM"

    def test_uniswap_can_create_exchange_data_via_registry(self):
        """Test that Uniswap exchange data can be created via ExchangeRegistry."""
        # Create exchange data using the registry
        exchange_data = ExchangeRegistry.create_exchange_data("UNISWAP___DEX")

        # Verify the exchange data is created correctly
        assert isinstance(exchange_data, UniswapExchangeDataSpot)


if __name__ == "__main__":
    pytest.main([__file__])
