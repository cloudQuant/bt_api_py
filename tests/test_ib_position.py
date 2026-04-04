"""Tests for IB position module."""

import pytest

from bt_api_py.containers.ib.ib_position import IbPositionData


class TestIbPositionData:
    """Tests for IbPositionData class."""

    def test_init(self):
        """Test initialization."""
        position = IbPositionData(
            {"symbol": "AAPL"},
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True
        )

        assert position.exchange_name == "IB"
        assert position.symbol_name == "AAPL"
        assert position.asset_type == "STK"

    def test_init_data(self):
        """Test init_data method."""
        position_info = {
            "account": "U123456",
            "symbol": "AAPL",
            "secType": "STK",
            "position": 100,
            "avgCost": 150.0,
            "marketPrice": 155.0,
            "marketValue": 15500.0,
            "unrealizedPNL": 500.0,
            "realizedPNL": 200.0,
            "currency": "USD",
        }
        position = IbPositionData(
            position_info,
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True
        )
        position.init_data()

        assert position.account == "U123456"
        assert position.contract_symbol == "AAPL"
        assert position.sec_type == "STK"
        assert position.position_val == 100.0
        assert position.avg_cost == 150.0
        assert position.market_price_val == 155.0
        assert position.unrealized_pnl_val == 500.0
        assert position.realized_pnl_val == 200.0
        assert position.currency == "USD"

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        position = IbPositionData({}, has_been_json_encoded=True)

        assert position.get_exchange_name() == "IB"

    def test_position_data_inheritance(self):
        """Test that IbPositionData inherits from PositionData."""
        position = IbPositionData({}, has_been_json_encoded=True)

        assert hasattr(position, "position_info")
        assert hasattr(position, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
