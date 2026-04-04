"""Tests for CTP position module."""

import pytest

from bt_api_py.containers.ctp.ctp_position import CtpPositionData, CTP_POS_DIRECTION_MAP


class TestCtpPositionData:
    """Tests for CtpPositionData class."""

    def test_init(self):
        """Test initialization."""
        position = CtpPositionData(
            {"InstrumentID": "IF2506"},
            symbol_name="IF2506",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert position.exchange_name == "CTP"
        assert position.symbol_name == "IF2506"
        assert position.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        position_info = {
            "InstrumentID": "IF2506",
            "PosiDirection": "2",
            "Position": 10,
            "TodayPosition": 5,
            "YdPosition": 5,
            "OpenCost": 4000000.0,
            "PositionCost": 4000000.0,
            "UseMargin": 400000.0,
            "PositionProfit": 10000.0,
            "CloseProfit": 5000.0,
            "SettlementPrice": 4000.0,
            "ExchangeID": "CFFEX",
        }
        position = CtpPositionData(
            position_info,
            symbol_name="IF2506",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )
        position.init_data()

        assert position.instrument_id == "IF2506"
        assert position.position_direction == "long"
        assert position.position_volume == 10
        assert position.today_position == 5
        assert position.yd_position == 5
        assert position.open_cost == 4000000.0
        assert position.position_profit == 10000.0
        assert position.exchange_id == "CFFEX"

    def test_ctp_pos_direction_map(self):
        """Test CTP position direction map."""
        assert CTP_POS_DIRECTION_MAP["1"] == "net"
        assert CTP_POS_DIRECTION_MAP["2"] == "long"
        assert CTP_POS_DIRECTION_MAP["3"] == "short"

    def test_position_data_inheritance(self):
        """Test that CtpPositionData inherits from PositionData."""
        position = CtpPositionData({}, has_been_json_encoded=True)

        assert hasattr(position, "position_info")
        assert hasattr(position, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
