"""
Tests for Curve DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

from enum import Enum
from unittest.mock import MagicMock, Mock, patch

import pytest

from bt_api_py.containers.exchanges.curve_exchange_data import (
    CurveChain,
    CurveExchangeDataSpot,
)
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_curve.spot import CurveRequestDataSpot


class MockCurveChain(str, Enum):
    """Mock CurveChain for testing."""

    ETHEREUM = "ETHEREUM"
    ARBITRUM = "ARBITRUM"
    POLYGON = "POLYGON"


class TestCurveRequestDataSpot:
    """Test cases for CurveRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def curve_spot(self, mock_data_queue):
        """Create CurveRequestDataSpot instance."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = CurveRequestDataSpot(mock_data_queue, chain=MockCurveChain.ETHEREUM)
            return instance

    def test_init(self, curve_spot):
        """Test initialization."""
        assert curve_spot.exchange_name == "CURVE___DEX"
        # The chain is converted to CurveChain enum
        assert curve_spot.chain == CurveChain.ETHEREUM

    def test_capabilities(self, curve_spot):
        """Test declared capabilities."""
        from bt_api_base.feeds.capability import Capability

        capabilities = curve_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

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
        assert status
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_get_pools_tuple(self, curve_spot):
        """Test _get_pools returns (path, params, extra_data)."""
        path, params, extra_data = curve_spot._get_pools()
        assert extra_data["request_type"] == "get_pools"
        assert extra_data["exchange_name"] == "CURVE___DEX"

    def test_get_pools(self, curve_spot):
        """Test get_pools method - mocked."""
        with patch.object(curve_spot, "request") as mock_request:
            mock_request.return_value = Mock(extra_data={"request_type": "get_pools"})
            curve_spot.get_pools()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]["extra_data"]["request_type"] == "get_pools"

    # ==================== Volumes Tests ====================

    def test_get_volumes_normalize_function(self):
        """Test volumes normalize function."""
        input_data = {"data": {"dailyVolume": "1000000", "weeklyVolume": "5000000"}}
        result, status = CurveRequestDataSpot._get_volumes_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    def test_get_volumes_tuple(self, curve_spot):
        """Test _get_volumes returns tuple."""
        path, params, extra_data = curve_spot._get_volumes()
        assert extra_data["request_type"] == "get_volumes"

    def test_get_volumes(self, curve_spot):
        """Test get_volumes method - mocked."""
        with patch.object(curve_spot, "request") as mock_request:
            mock_request.return_value = Mock(extra_data={"request_type": "get_volumes"})
            curve_spot.get_volumes()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]["extra_data"]["request_type"] == "get_volumes"

    # ==================== TVL Tests ====================

    def test_get_tvl_normalize_function(self):
        """Test TVL normalize function."""
        input_data = {"data": {"totalTVL": "10000000"}}
        result, status = CurveRequestDataSpot._get_tvl_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    def test_get_tvl_tuple(self, curve_spot):
        """Test _get_tvl returns tuple."""
        path, params, extra_data = curve_spot._get_tvl()
        assert extra_data["request_type"] == "get_tvl"

    def test_get_tvl(self, curve_spot):
        """Test get_tvl method - mocked."""
        with patch.object(curve_spot, "request") as mock_request:
            mock_request.return_value = Mock(extra_data={"request_type": "get_tvl"})
            curve_spot.get_tvl()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]["extra_data"]["request_type"] == "get_tvl"

    # ==================== APY Tests ====================

    def test_get_apys_normalize_function(self):
        """Test APYs normalize function."""
        input_data = {"data": {"poolApy": {"pool1": 0.05, "pool2": 0.10}}}
        result, status = CurveRequestDataSpot._get_apys_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    def test_get_apys_tuple(self, curve_spot):
        """Test _get_apys returns tuple."""
        path, params, extra_data = curve_spot._get_apys()
        assert extra_data["request_type"] == "get_apys"

    def test_get_apys(self, curve_spot):
        """Test get_apys method - mocked."""
        with patch.object(curve_spot, "request") as mock_request:
            mock_request.return_value = Mock(extra_data={"request_type": "get_apys"})
            curve_spot.get_apys()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]["extra_data"]["request_type"] == "get_apys"

    # ==================== Pool Detail Tests ====================

    def test_get_pool_summary_normalize_function(self):
        """Test pool summary normalize function."""
        input_data = {
            "data": {
                "poolData": {"name": "3pool", "address": "0x...", "totalLiquidity": "10000000"}
            }
        }
        result, status = CurveRequestDataSpot._get_pool_summary_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    def test_get_pool_summary(self, curve_spot):
        """Test get_pool_summary method."""
        pool_address = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"

        path, params, extra_data = curve_spot._get_pool_summary(pool_address)

        assert extra_data["request_type"] == "get_pool_summary"
        assert extra_data["pool_address"] == pool_address

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info_tuple(self, curve_spot):
        """Test _get_exchange_info returns tuple."""
        path, params, extra_data = curve_spot._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"
        assert extra_data["exchange_name"] == "CURVE___DEX"

    def test_get_exchange_info(self, curve_spot):
        """Test get_exchange_info method - mocked."""
        with patch.object(curve_spot, "request") as mock_request:
            mock_request.return_value = Mock(extra_data={"request_type": "get_exchange_info"})
            curve_spot.get_exchange_info()
            assert mock_request.called
            call_args = mock_request.call_args
            assert call_args[1]["extra_data"]["request_type"] == "get_exchange_info"

    # ==================== Ticker Tests (via pools) ====================

    @pytest.mark.ticker
    def test_get_tick(self, curve_spot):
        """Test get_tick method - Curve uses pool data."""
        pool_address = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"

        path, params, extra_data = curve_spot._get_tick(pool_address)

        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == pool_address

    @pytest.mark.ticker
    def test_get_tick_normalize_function(self):
        """Test tick normalize function - uses pool summary."""
        input_data = {"data": {"poolData": {"name": "3pool", "virtualPrice": "1.01"}}}
        result, status = CurveRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert status
        assert len(result) == 1

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_pools(self, curve_spot):
        """Integration test for get_pools - skipped."""

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_tvl(self, curve_spot):
        """Integration test for get_tvl - skipped."""

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_apys(self, curve_spot):
        """Integration test for get_apys - skipped."""


class TestCurveExchangeDataSpot:
    """Test cases for CurveExchangeDataSpot."""

    def test_init_with_chain_enum(self):
        """Test initialization with chain enum."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert exchange_data.chain == MockCurveChain.ETHEREUM
            assert exchange_data.rest_url == "https://api.curve.finance"

    def test_init_with_chain_string(self):
        """Test initialization with chain string."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain="ETHEREUM")
            assert exchange_data.chain.value == "ETHEREUM"

    def test_get_chain_value(self):
        """Test get_chain_value method."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert exchange_data.get_chain_value() == "ethereum"

    def test_get_chain_name(self):
        """Test get_chain_name method."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ARBITRUM)
            assert exchange_data.get_chain_name() == "arbitrum"

    def test_get_rest_path(self):
        """Test get_rest_path method."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            path = exchange_data.get_rest_path("get_pools")
            assert "getPools/ethereum/main" in path

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert exchange_data.get_rest_url() == "https://api.curve.finance"

    def test_factory_addresses(self):
        """Test factory addresses are defined."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert hasattr(CurveExchangeDataSpot, "FACTORY_ADDRESSES")
            assert CurveChain.ETHEREUM in exchange_data.FACTORY_ADDRESSES

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
            exchange_data = CurveExchangeDataSpot(chain=MockCurveChain.ETHEREUM)
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch(
            "bt_api_py.containers.exchanges.curve_exchange_data._get_curve_config",
            return_value=None,
        ):
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


class TestCurveStandardInterfaces:
    """Test standard Feed interface methods for Curve."""

    @pytest.fixture
    def curve_spot(self):
        """Create CurveRequestDataSpot instance with mocked request."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = CurveRequestDataSpot(Mock(), chain=MockCurveChain.ETHEREUM)
            instance.request = Mock(return_value=Mock(spec=RequestData))
            return instance

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, curve_spot):
        """Test get_tick calls self.request."""
        curve_spot.get_tick("0xPool")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_tuple(self, curve_spot):
        """Test _get_depth returns tuple."""
        path, params, extra_data = curve_spot._get_depth("0xPool")
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "0xPool"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, curve_spot):
        """Test get_depth calls self.request."""
        curve_spot.get_depth("0xPool")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.kline
    def test_get_kline_tuple(self, curve_spot):
        """Test _get_kline returns tuple."""
        path, params, extra_data = curve_spot._get_kline("0xPool", "1h", 100)
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["period"] == "1h"

    @pytest.mark.kline
    def test_get_kline_calls_request(self, curve_spot):
        """Test get_kline calls self.request."""
        curve_spot.get_kline("0xPool", "1h")
        assert curve_spot.request.called

    def test_get_server_time(self):
        """Test get_server_time returns RequestData."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            inst = CurveRequestDataSpot(Mock(), chain=MockCurveChain.ETHEREUM)
            result = inst.get_server_time()
            assert isinstance(result, RequestData)

    def test_get_server_time_extra_data(self):
        """Test _get_server_time populates extra_data."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            inst = CurveRequestDataSpot(Mock(), chain=MockCurveChain.ETHEREUM)
            path, params, extra_data = inst._get_server_time()
            assert extra_data["request_type"] == "get_server_time"
            assert "server_time" in extra_data

    def test_make_order_calls_request(self, curve_spot):
        """Test make_order calls self.request."""
        curve_spot.make_order("0xPool", 1.0, 3000, "LIMIT")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, curve_spot):
        """Test cancel_order calls self.request."""
        curve_spot.cancel_order("0xPool", "order_123")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, curve_spot):
        """Test query_order calls self.request."""
        curve_spot.query_order("0xPool", "order_123")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, curve_spot):
        """Test get_open_orders calls self.request."""
        curve_spot.get_open_orders("0xPool")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, curve_spot):
        """Test get_account calls self.request."""
        curve_spot.get_account("ETH")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, curve_spot):
        """Test get_balance calls self.request."""
        curve_spot.get_balance("ETH")
        assert curve_spot.request.called
        extra_data = curve_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"


class TestCurveBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        """Test CurveRequestData base class capabilities."""
        from bt_api_base.feeds.capability import Capability
        from bt_api_py.feeds.live_curve.request_base import CurveRequestData

        caps = CurveRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestCurveDataContainers:
    """Test Curve data containers init_data() returns self."""

    @pytest.mark.ticker
    def test_ticker_init_data_returns_self(self):
        """Test CurveRequestTickerData.init_data() returns self."""
        from bt_api_py.containers.tickers.curve_ticker import CurveRequestTickerData

        ticker_data = {"data": {"virtualPrice": "1.01", "name": "3pool"}}
        ticker = CurveRequestTickerData(ticker_data, "3pool", "DEX", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker
        assert ticker.get_symbol_name() == "3pool"
        assert ticker.get_last_price() == 1.01

    @pytest.mark.ticker
    def test_ticker_init_data_idempotent(self):
        """Test that calling init_data() twice returns self both times."""
        from bt_api_py.containers.tickers.curve_ticker import CurveRequestTickerData

        ticker_data = {"data": {"virtualPrice": "1.0"}}
        ticker = CurveRequestTickerData(ticker_data, "pool", "DEX", has_been_json_encoded=True)
        r1 = ticker.init_data()
        r2 = ticker.init_data()
        assert r1 is ticker
        assert r2 is ticker

    @pytest.mark.ticker
    def test_ticker_symbol_name_preserved(self):
        """Test that symbol_name is preserved after init_data."""
        from bt_api_py.containers.tickers.curve_ticker import CurveRequestTickerData

        ticker = CurveRequestTickerData({"data": {}}, "my_pool", "DEX", has_been_json_encoded=True)
        ticker.init_data()
        assert ticker.get_symbol_name() == "my_pool"


class TestCurveNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_always_empty(self):
        result, status = CurveRequestDataSpot._get_kline_normalize_function({}, None)
        assert result == []
        assert status is True

    def test_pools_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_pools_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_volumes_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_volumes_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tvl_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_tvl_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_apys_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_apys_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pool_summary_normalize_with_none(self):
        result, status = CurveRequestDataSpot._get_pool_summary_normalize_function(None, None)
        assert result == []
        assert status is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
