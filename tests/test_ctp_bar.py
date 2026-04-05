"""Tests for CTP bar module."""

import pytest

from bt_api_py.containers.ctp.ctp_bar import CtpBarData


class TestCtpBarData:
    """Tests for CtpBarData class."""

    def test_init(self):
        """Test initialization."""
        bar = CtpBarData(
            {"open": 4000.0}, symbol_name="IF2506", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert bar.exchange_name == "CTP"
        assert bar.symbol_name == "IF2506"
        assert bar.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        bar_info = {
            "open_time": "2025-01-01 09:30:00",
            "close_time": "2025-01-01 09:31:00",
            "open": 4000.0,
            "high": 4010.0,
            "low": 3990.0,
            "close": 4005.0,
            "volume": 10000,
            "amount": 40000000.0,
            "open_interest": 50000.0,
            "settlement_price": 4002.0,
        }
        bar = CtpBarData(
            bar_info, symbol_name="IF2506", asset_type="FUTURE", has_been_json_encoded=True
        )
        bar.init_data()

        assert bar.open_time == "2025-01-01 09:30:00"
        assert bar.close_time == "2025-01-01 09:31:00"
        assert bar.open_price == 4000.0
        assert bar.high_price == 4010.0
        assert bar.low_price == 3990.0
        assert bar.close_price == 4005.0
        assert bar.volume_val == 10000
        assert bar.amount_val == 40000000.0
        assert bar.open_interest == 50000.0
        assert bar.settlement_price_val == 4002.0

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        bar = CtpBarData({}, has_been_json_encoded=True)

        assert bar.get_exchange_name() == "CTP"

    def test_bar_data_inheritance(self):
        """Test that CtpBarData inherits from BarData."""
        bar = CtpBarData({}, has_been_json_encoded=True)

        assert hasattr(bar, "bar_info")
        assert hasattr(bar, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
