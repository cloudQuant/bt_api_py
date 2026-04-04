"""Tests for request_data module."""

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData


class TestRequestData:
    """Tests for RequestData class."""

    def test_init(self):
        """Test initialization."""
        request = RequestData(
            {"key": "value"},
            {"exchange_name": "BINANCE", "symbol_name": "BTCUSDT"}
        )

        assert request.event == "RequestEvent"
        assert request.input_data == {"key": "value"}
        assert request.exchange_name == "BINANCE"
        assert request.symbol_name == "BTCUSDT"

    def test_init_with_status(self):
        """Test initialization with status."""
        request = RequestData(
            {"key": "value"},
            {"exchange_name": "BINANCE"},
            status=True
        )

        assert request.status is True

    def test_init_data_without_normalize_func(self):
        """Test init_data without normalize function."""
        request = RequestData(
            {"key": "value"},
            {"exchange_name": "BINANCE"}
        )
        request.init_data()

        assert request.data == {"key": "value"}
        assert request.status is None

    def test_init_data_with_normalize_func(self):
        """Test init_data with normalize function."""
        def normalize_func(input_data, extra_data):
            return {"normalized": True}, True

        request = RequestData(
            {"key": "value"},
            {"exchange_name": "BINANCE"},
            normalize_func=normalize_func
        )
        request.init_data()

        assert request.data == {"normalized": True}
        assert request.status is True

    def test_set_data(self):
        """Test set_data method."""
        request = RequestData({}, {})
        request.set_data({"new": "data"})

        assert request.data == {"new": "data"}

    def test_set_status(self):
        """Test set_status method."""
        request = RequestData({}, {})
        request.set_status(True)

        assert request.status is True

    def test_get_event(self):
        """Test get_event method."""
        request = RequestData({}, {})

        assert request.get_event() == "RequestEvent"

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        request = RequestData({}, {"exchange_name": "BINANCE"})

        assert request.get_exchange_name() == "BINANCE"

    def test_get_symbol_name(self):
        """Test get_symbol_name method."""
        request = RequestData({}, {"symbol_name": "BTCUSDT"})

        assert request.get_symbol_name() == "BTCUSDT"

    def test_get_asset_type(self):
        """Test get_asset_type method."""
        request = RequestData({}, {"asset_type": "SPOT"})

        assert request.get_asset_type() == "SPOT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
