"""Tests for IB bar module."""

import pytest

from bt_api_py.containers.ib.ib_bar import IbBarData


class TestIbBarData:
    """Tests for IbBarData class."""

    def test_init(self):
        """Test initialization."""
        bar = IbBarData(
            {"date": "20250101 09:30"},
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True,
        )

        assert bar.exchange_name == "IB"
        assert bar.symbol_name == "AAPL"
        assert bar.asset_type == "STK"

    def test_init_data(self):
        """Test init_data method."""
        bar_info = {
            "date": "20250101 09:30",
            "open": 150.0,
            "high": 151.0,
            "low": 149.0,
            "close": 150.5,
            "volume": 1000000,
            "wap": 150.25,
            "barCount": 5000,
        }
        bar = IbBarData(bar_info, symbol_name="AAPL", asset_type="STK", has_been_json_encoded=True)
        bar.init_data()

        assert bar.date_val == "20250101 09:30"
        assert bar.open_val == 150.0
        assert bar.high_val == 151.0
        assert bar.low_val == 149.0
        assert bar.close_val == 150.5
        assert bar.volume_val == 1000000
        assert bar.wap_val == 150.25
        assert bar.bar_count == 5000

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        bar = IbBarData({}, has_been_json_encoded=True)

        assert bar.get_exchange_name() == "IB"

    def test_bar_data_inheritance(self):
        """Test that IbBarData inherits from BarData."""
        bar = IbBarData({}, has_been_json_encoded=True)

        assert hasattr(bar, "bar_info")
        assert hasattr(bar, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
