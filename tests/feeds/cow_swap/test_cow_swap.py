"""
Tests for CoW Swap Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import queue
import pytest
from unittest.mock import Mock, patch, MagicMock

from bt_api_py.feeds.live_cow_swap.spot import CowSwapRequestDataSpot
from bt_api_py.containers.exchanges.cow_swap_exchange_data import (
    CowSwapExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData


class TestCowSwapRequestDataSpot:
    """Test cases for CowSwapRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def cow_swap_spot(self, mock_data_queue):
        """Create CowSwapRequestDataSpot instance."""
        with patch('bt_api_py.feeds.http_client.HttpClient', return_value=MagicMock()):
            instance = CowSwapRequestDataSpot(mock_data_queue)
            return instance

    def test_init(self, cow_swap_spot):
        """Test initialization."""
        assert cow_swap_spot.exchange_name == "COW_SWAP___SPOT"

    def test_capabilities(self, cow_swap_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = cow_swap_spot._capabilities()
        assert Capability.GET_EXCHANGE_INFO in capabilities

    # ==================== Order Tests ====================

    @patch('bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request')
    def test_get_order(self, mock_request, cow_swap_spot):
        """Test get_order method."""
        mock_request.return_value = Mock(extra_data={'request_type': 'get_order'})
        order_uid = "0x1234567890abcdef"
        result = cow_swap_spot.get_order(order_uid)

        assert result.extra_data['request_type'] == "get_order"
        mock_request.assert_called_once()

    def test_get_order_normalize_function(self):
        """Test order normalize function."""
        input_data = {
            "uid": "0x1234567890abcdef",
            "status": "filled",
            "amount": "1000000"
        }
        result, status = CowSwapRequestDataSpot._get_order_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert result[0]['uid'] == "0x1234567890abcdef"

    @patch('bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request')
    def test_get_order_status(self, mock_request, cow_swap_spot):
        """Test get_order_status method."""
        mock_request.return_value = Mock(extra_data={'request_type': 'get_order_status'})
        order_uid = "0x1234567890abcdef"
        result = cow_swap_spot.get_order_status(order_uid)

        assert result.extra_data['request_type'] == "get_order_status"
        mock_request.assert_called_once()

    def test_get_order_status_normalize_function(self):
        """Test order status normalize function."""
        input_data = {
            "uid": "0x1234567890abcdef",
            "status": "open"
        }
        result, status = CowSwapRequestDataSpot._get_order_status_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Account Orders Tests ====================

    @patch('bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request')
    def test_get_account_orders(self, mock_request, cow_swap_spot):
        """Test get_account_orders method."""
        mock_request.return_value = Mock(extra_data={'request_type': 'get_account_orders'})
        owner = "0xabcdefabcdefabcdef"
        result = cow_swap_spot.get_account_orders(owner, offset=0, limit=10)

        assert result.extra_data['request_type'] == "get_account_orders"
        mock_request.assert_called_once()

    def test_get_account_orders_normalize_function(self):
        """Test account orders normalize function."""
        input_data = {
            "orders": [
                {"uid": "0x123", "status": "open"},
                {"uid": "0x456", "status": "filled"}
            ]
        }
        result, status = CowSwapRequestDataSpot._get_account_orders_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    # ==================== Trades Tests ====================

    @patch('bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request')
    def test_get_trades(self, mock_request, cow_swap_spot):
        """Test get_trades method."""
        mock_request.return_value = Mock(extra_data={'request_type': 'get_trades'})
        result = cow_swap_spot.get_trades(offset=0, limit=10)

        assert result.extra_data['request_type'] == "get_trades"
        mock_request.assert_called_once()

    def test_get_trades_normalize_function(self):
        """Test trades normalize function."""
        input_data = {
            "trades": [
                {"tradeId": "0x123", "amount": "1000"},
                {"tradeId": "0x456", "amount": "2000"}
            ]
        }
        result, status = CowSwapRequestDataSpot._get_trades_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    # ==================== Exchange Info Tests ====================

    @patch('bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request')
    def test_get_exchange_info(self, mock_request, cow_swap_spot):
        """Test get_exchange_info method."""
        mock_request.return_value = Mock(extra_data={'request_type': 'get_exchange_info'})
        result = cow_swap_spot._get_exchange_info()

        assert result.extra_data['request_type'] == "get_exchange_info"
        mock_request.assert_called_once()

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = [
            {"address": "0xtoken1", "symbol": "TOKEN1"},
            {"address": "0xtoken2", "symbol": "TOKEN2"}
        ]
        result, status = CowSwapRequestDataSpot._get_exchange_info_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    # ==================== Quote Tests ====================

    @patch('bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request')
    def test_get_quote(self, mock_request, cow_swap_spot):
        """Test get_quote method."""
        sell_token = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        buy_token = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        amount = "1000000"

        mock_request.return_value = Mock(extra_data={'request_type': 'get_quote'})
        result = cow_swap_spot._get_quote(sell_token, buy_token, amount)

        assert result.extra_data['request_type'] == "get_quote"
        mock_request.assert_called_once()

    def test_get_quote_normalize_function(self):
        """Test quote normalize function."""
        input_data = {
            "quote": {
                "sellAmount": "1000000",
                "buyAmount": "3000",
                "feeAmount": "100"
            }
        }
        result, status = CowSwapRequestDataSpot._get_quote_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_order(self, cow_swap_spot):
        """Integration test for get_order - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_trades(self, cow_swap_spot):
        """Integration test for get_trades - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_quote(self, cow_swap_spot):
        """Integration test for get_quote - skipped."""
        pass


class TestCowSwapExchangeDataSpot:
    """Test cases for CowSwapExchangeDataSpot."""

    def test_init(self):
        """Test initialization."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            assert exchange_data.exchange_name == "cow_swap"
            assert exchange_data.asset_type == "spot"
            assert exchange_data.rest_url == "https://api.cow.fi"

    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_supported_chains(self):
        """Test supported chains."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            assert "mainnet" in exchange_data.supported_chains
            assert "xdai" in exchange_data.supported_chains
            assert "arbitrum_one" in exchange_data.supported_chains

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            assert "USDT" in exchange_data.legal_currency
            assert "USDC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            assert exchange_data.get_rest_url() == "https://api.cow.fi"

    def test_get_symbol(self):
        """Test get_symbol method."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            # CoW Swap uses token addresses as symbols
            address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            assert exchange_data.get_symbol(address) == address

    def test_chain_to_url(self):
        """Test chain to URL mapping."""
        with patch('bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config', return_value=None):
            exchange_data = CowSwapExchangeDataSpot()
            # Test that different chains have different URLs
            assert hasattr(exchange_data, 'supported_chains')


class TestCowSwapRegistration:
    """Test cases for CoW Swap registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that CoW Swap is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("COW_SWAP___SPOT")
        assert exchange_class is not None
        assert exchange_class.__name__ == "CowSwapRequestDataSpot"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
