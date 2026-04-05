"""Tests for OKX position module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.positions.okx_position import OkxPositionData


class TestOkxPositionData:
    """Tests for OkxPositionData class."""

    def test_init(self):
        """Test initialization."""
        position = OkxPositionData(
            {"instId": "BTC-USDT-SWAP"},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert position.exchange_name == "OKX"
        assert position.symbol_name == "BTC-USDT-SWAP"
        assert position.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        position_info = {
            "uTime": "1705315800000",
            "mgnMode": "cross",
            "lever": "10",
            "instId": "BTC-USDT-SWAP",
            "pos": "100",
            "posSide": "long",
            "avgPx": "40000.0",
            "markPx": "41000.0",
            "imr": "1000.0",
            "mmr": "500.0",
            "fee": "10.0",
            "realizedPnl": "100.0",
            "upl": "1000.0",
            "fundingFee": "5.0",
        }
        position = OkxPositionData(
            position_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        position.init_data()

        assert position.server_time == 1705315800000.0
        assert position.margin_type == "cross"
        assert position.is_isolated is False
        assert position.leverage == 10.0
        assert position.position_symbol_name == "BTC-USDT-SWAP"
        assert position.position_volume == 100.0
        assert position.position_side == "long"
        assert position.avg_price == 40000.0
        assert position.mark_price == 41000.0

    def test_position_data_inheritance(self):
        """Test that OkxPositionData inherits from PositionData."""
        position = OkxPositionData(
            {}, symbol_name="BTC-USDT-SWAP", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert hasattr(position, "position_info")
        assert hasattr(position, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
