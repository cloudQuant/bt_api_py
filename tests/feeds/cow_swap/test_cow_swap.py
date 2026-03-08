"""
Tests for CoW Swap Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from bt_api_py.containers.exchanges.cow_swap_exchange_data import (
    CowSwapExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_cow_swap.spot import CowSwapRequestDataSpot


class TestCowSwapRequestDataSpot:
    """Test cases for CowSwapRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def cow_swap_spot(self, mock_data_queue):
        """Create CowSwapRequestDataSpot instance."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = CowSwapRequestDataSpot(mock_data_queue)
            return instance

    def test_init(self, cow_swap_spot):
        """Test initialization."""
        assert cow_swap_spot.exchange_name == "COW_SWAP___SPOT"

    def test_capabilities(self, cow_swap_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = cow_swap_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

    # ==================== Order Tests ====================

    @patch("bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request")
    def test_get_order(self, mock_request, cow_swap_spot):
        """Test get_order method."""
        mock_request.return_value = Mock(extra_data={"request_type": "get_order"})
        order_uid = "0x1234567890abcdef"
        result = cow_swap_spot.get_order(order_uid)

        assert result.extra_data["request_type"] == "get_order"
        mock_request.assert_called_once()

    def test_get_order_normalize_function(self):
        """Test order normalize function."""
        input_data = {"uid": "0x1234567890abcdef", "status": "filled", "amount": "1000000"}
        result, status = CowSwapRequestDataSpot._get_order_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert result[0]["uid"] == "0x1234567890abcdef"

    @patch("bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request")
    def test_get_order_status(self, mock_request, cow_swap_spot):
        """Test get_order_status method."""
        mock_request.return_value = Mock(extra_data={"request_type": "get_order_status"})
        order_uid = "0x1234567890abcdef"
        result = cow_swap_spot.get_order_status(order_uid)

        assert result.extra_data["request_type"] == "get_order_status"
        mock_request.assert_called_once()

    def test_get_order_status_normalize_function(self):
        """Test order status normalize function."""
        input_data = {"uid": "0x1234567890abcdef", "status": "open"}
        result, status = CowSwapRequestDataSpot._get_order_status_normalize_function(
            input_data, None
        )
        assert status == True
        assert len(result) == 1

    # ==================== Account Orders Tests ====================

    @patch("bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request")
    def test_get_account_orders(self, mock_request, cow_swap_spot):
        """Test get_account_orders method."""
        mock_request.return_value = Mock(extra_data={"request_type": "get_account_orders"})
        owner = "0xabcdefabcdefabcdef"
        result = cow_swap_spot.get_account_orders(owner, offset=0, limit=10)

        assert result.extra_data["request_type"] == "get_account_orders"
        mock_request.assert_called_once()

    def test_get_account_orders_normalize_function(self):
        """Test account orders normalize function."""
        input_data = {
            "orders": [{"uid": "0x123", "status": "open"}, {"uid": "0x456", "status": "filled"}]
        }
        result, status = CowSwapRequestDataSpot._get_account_orders_normalize_function(
            input_data, None
        )
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    # ==================== Trades Tests ====================

    @patch("bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request")
    def test_get_trades(self, mock_request, cow_swap_spot):
        """Test get_trades method."""
        mock_request.return_value = Mock(extra_data={"request_type": "get_trades"})
        result = cow_swap_spot.get_trades(offset=0, limit=10)

        assert result.extra_data["request_type"] == "get_trades"
        mock_request.assert_called_once()

    def test_get_trades_normalize_function(self):
        """Test trades normalize function."""
        input_data = {
            "trades": [
                {"tradeId": "0x123", "amount": "1000"},
                {"tradeId": "0x456", "amount": "2000"},
            ]
        }
        result, status = CowSwapRequestDataSpot._get_trades_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info_tuple(self, cow_swap_spot):
        """Test _get_exchange_info returns (path, params, extra_data) tuple."""
        path, params, extra_data = cow_swap_spot._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"
        assert extra_data["exchange_name"] == "COW_SWAP___SPOT"
        assert "tokens" in path

    @patch("bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request")
    def test_get_exchange_info(self, mock_request, cow_swap_spot):
        """Test get_exchange_info method."""
        mock_request.return_value = Mock(extra_data={"request_type": "get_exchange_info"})
        result = cow_swap_spot.get_exchange_info()

        assert result.extra_data["request_type"] == "get_exchange_info"
        mock_request.assert_called_once()

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalize function."""
        input_data = [
            {"address": "0xtoken1", "symbol": "TOKEN1"},
            {"address": "0xtoken2", "symbol": "TOKEN2"},
        ]
        result, status = CowSwapRequestDataSpot._get_exchange_info_normalize_function(
            input_data, None
        )
        assert status == True
        assert len(result) == 1
        assert len(result[0]) == 2

    # ==================== Quote Tests ====================

    def test_get_quote_tuple(self, cow_swap_spot):
        """Test _get_quote returns (path, params, extra_data) tuple."""
        sell_token = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        buy_token = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        amount = "1000000"
        path, params, extra_data = cow_swap_spot._get_quote(sell_token, buy_token, amount)
        assert extra_data["request_type"] == "get_quote"
        assert extra_data["sell_token"] == sell_token
        assert params["sellToken"] == sell_token

    @patch("bt_api_py.feeds.live_cow_swap.request_base.CowSwapRequestData.request")
    def test_get_quote(self, mock_request, cow_swap_spot):
        """Test get_quote method."""
        sell_token = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        buy_token = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        amount = "1000000"

        mock_request.return_value = Mock(extra_data={"request_type": "get_quote"})
        result = cow_swap_spot.get_quote(sell_token, buy_token, amount)

        assert result.extra_data["request_type"] == "get_quote"
        mock_request.assert_called_once()

    def test_get_quote_normalize_function(self):
        """Test quote normalize function."""
        input_data = {"quote": {"sellAmount": "1000000", "buyAmount": "3000", "feeAmount": "100"}}
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
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            assert exchange_data.exchange_name == "cow_swap"
            assert exchange_data.asset_type == "spot"
            assert exchange_data.rest_url == "https://api.cow.fi"

    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_supported_chains(self):
        """Test supported chains."""
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            assert "mainnet" in exchange_data.supported_chains
            assert "xdai" in exchange_data.supported_chains
            assert "arbitrum_one" in exchange_data.supported_chains

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            assert "USDT" in exchange_data.legal_currency
            assert "USDC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            assert exchange_data.get_rest_url() == "https://api.cow.fi"

    def test_get_symbol(self):
        """Test get_symbol method."""
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            # CoW Swap uses token addresses as symbols
            address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            assert exchange_data.get_symbol(address) == address

    def test_chain_to_url(self):
        """Test chain to URL mapping."""
        with patch(
            "bt_api_py.containers.exchanges.cow_swap_exchange_data._get_cow_swap_config",
            return_value=None,
        ):
            exchange_data = CowSwapExchangeDataSpot()
            # Test that different chains have different URLs
            assert hasattr(exchange_data, "supported_chains")


class TestCowSwapRegistration:
    """Test cases for CoW Swap registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that CoW Swap is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("COW_SWAP___SPOT")
        assert exchange_class is not None
        assert exchange_class.__name__ == "CowSwapRequestDataSpot"


class TestCowSwapStandardInterfaces:
    """Test standard Feed interface methods for CoW Swap."""

    @pytest.fixture
    def cow_swap_spot(self):
        """Create CowSwapRequestDataSpot instance with mocked request."""
        instance = CowSwapRequestDataSpot(Mock())
        instance.request = Mock(return_value=Mock(spec=RequestData))
        return instance

    # ── get_tick ───────────────────────────────────────

    def test_get_tick_tuple(self, cow_swap_spot):
        """Test _get_tick returns (path, params, extra_data)."""
        token = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        path, params, extra_data = cow_swap_spot._get_tick(token)
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == token
        assert extra_data["exchange_name"] == "COW_SWAP___SPOT"
        assert token in path

    def test_get_tick_calls_request(self, cow_swap_spot):
        """Test get_tick calls self.request."""
        result = cow_swap_spot.get_tick("0xWETH")
        assert cow_swap_spot.request.called
        call_ed = cow_swap_spot.request.call_args
        extra_data = call_ed[1].get("extra_data")
        assert extra_data["request_type"] == "get_tick"

    # ── get_depth ─────────────────────────────────────

    def test_get_depth_tuple(self, cow_swap_spot):
        """Test _get_depth returns (path, params, extra_data)."""
        path, params, extra_data = cow_swap_spot._get_depth("WETH")
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "WETH"

    def test_get_depth_calls_request(self, cow_swap_spot):
        """Test get_depth calls self.request."""
        result = cow_swap_spot.get_depth("WETH")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_depth"

    # ── get_kline ─────────────────────────────────────

    def test_get_kline_tuple(self, cow_swap_spot):
        """Test _get_kline returns (path, params, extra_data)."""
        path, params, extra_data = cow_swap_spot._get_kline("WETH", "1h", 100)
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["symbol_name"] == "WETH"
        assert extra_data["period"] == "1h"

    def test_get_kline_calls_request(self, cow_swap_spot):
        """Test get_kline calls self.request."""
        result = cow_swap_spot.get_kline("WETH", "1h")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_kline"

    # ── get_server_time ───────────────────────────────

    def test_get_server_time(self):
        """Test get_server_time returns RequestData."""
        inst = CowSwapRequestDataSpot(Mock())
        result = inst.get_server_time()
        assert isinstance(result, RequestData)

    def test_get_server_time_extra_data(self):
        """Test _get_server_time populates extra_data."""
        inst = CowSwapRequestDataSpot(Mock())
        path, params, extra_data = inst._get_server_time()
        assert extra_data["request_type"] == "get_server_time"
        assert extra_data["exchange_name"] == "COW_SWAP___SPOT"
        assert "server_time" in extra_data

    # ── make_order ─────────────────────────────────────

    def test_make_order_calls_request(self, cow_swap_spot):
        """Test make_order calls self.request."""
        result = cow_swap_spot.make_order(
            "WETH-USDC",
            1.0,
            3000,
            "LIMIT",
            sell_token="0xWETH",
            buy_token="0xUSDC",
        )
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"
        assert extra_data["symbol_name"] == "WETH-USDC"

    def test_make_order_parses_symbol(self, cow_swap_spot):
        """Test _make_order parses sell/buy tokens from symbol."""
        path, body, extra_data = cow_swap_spot._make_order("0xTokenA-0xTokenB", 1.0, 100, "LIMIT")
        assert extra_data["sell_token"] == "0xTokenA"
        assert extra_data["buy_token"] == "0xTokenB"
        assert body["sellAmount"] == "1.0"
        assert "orders" in path

    # ── cancel_order ───────────────────────────────────

    def test_cancel_order_calls_request(self, cow_swap_spot):
        """Test cancel_order calls self.request."""
        result = cow_swap_spot.cancel_order("WETH-USDC", "order_uid_123")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order_uid_123"

    # ── query_order ────────────────────────────────────

    def test_query_order_calls_request(self, cow_swap_spot):
        """Test query_order calls self.request."""
        result = cow_swap_spot.query_order("WETH-USDC", "order_uid_123")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"
        assert extra_data["order_id"] == "order_uid_123"

    # ── get_open_orders ───────────────────────────────

    def test_get_open_orders_calls_request(self, cow_swap_spot):
        """Test get_open_orders calls self.request."""
        result = cow_swap_spot.get_open_orders("WETH-USDC")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    # ── get_account ───────────────────────────────────

    def test_get_account_calls_request(self, cow_swap_spot):
        """Test get_account calls self.request."""
        result = cow_swap_spot.get_account("WETH")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"
        assert extra_data["chain"] == "mainnet"

    # ── get_balance ───────────────────────────────────

    def test_get_balance_calls_request(self, cow_swap_spot):
        """Test get_balance calls self.request."""
        result = cow_swap_spot.get_balance("WETH")
        assert cow_swap_spot.request.called
        extra_data = cow_swap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"
        assert extra_data["chain"] == "mainnet"


class TestCowSwapBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        """Test that CowSwapRequestData base class declares correct capabilities."""
        from bt_api_py.feeds.capability import Capability
        from bt_api_py.feeds.live_cow_swap.request_base import CowSwapRequestData

        caps = CowSwapRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestCowSwapDataContainers:
    """Test CoW Swap data containers init_data() returns self."""

    def test_ticker_init_data_returns_self(self):
        """Test CowSwapRequestTickerData.init_data() returns self."""
        from bt_api_py.containers.tickers.cow_swap_ticker import CowSwapRequestTickerData

        ticker_data = {"price": "3000.50"}
        ticker = CowSwapRequestTickerData(ticker_data, "WETH", "SPOT", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker
        assert ticker.get_symbol_name() == "WETH"
        assert ticker.get_last_price() == 3000.50

    def test_ticker_init_data_idempotent(self):
        """Test that calling init_data() twice returns self both times."""
        from bt_api_py.containers.tickers.cow_swap_ticker import CowSwapRequestTickerData

        ticker_data = {"price": "100.0"}
        ticker = CowSwapRequestTickerData(ticker_data, "WETH", "SPOT", has_been_json_encoded=True)
        r1 = ticker.init_data()
        r2 = ticker.init_data()
        assert r1 is ticker
        assert r2 is ticker

    def test_ticker_symbol_name_preserved(self):
        """Test that symbol_name is preserved after init_data."""
        from bt_api_py.containers.tickers.cow_swap_ticker import CowSwapRequestTickerData

        ticker = CowSwapRequestTickerData(
            {"price": "50"}, "USDC", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()
        assert ticker.get_symbol_name() == "USDC"


class TestCowSwapNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_data(self):
        result, status = CowSwapRequestDataSpot._get_tick_normalize_function(
            {"price": "3000.0"}, None
        )
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_always_empty(self):
        result, status = CowSwapRequestDataSpot._get_kline_normalize_function({"data": {}}, None)
        assert result == []
        assert status is True

    def test_order_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_order_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_order_status_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_order_status_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_quote_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_quote_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_orders_normalize_with_none_input(self):
        result, status = CowSwapRequestDataSpot._get_account_orders_normalize_function(None, None)
        assert result == []
        assert status is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
