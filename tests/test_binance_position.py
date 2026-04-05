"""Tests for Binance position module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.positions.binance_position import BinancePositionData


class TestBinancePositionData:
    """Tests for BinancePositionData class."""

    def test_init(self):
        """Test initialization."""
        position = BinancePositionData(
            {"symbol": "BTCUSDT"},
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert position.exchange_name == "BINANCE"
        assert position.symbol_name == "BTCUSDT"
        assert position.asset_type == "FUTURE"

    def test_init_data_raises_not_implemented(self):
        """Test that init_data raises NotImplementedError."""
        position = BinancePositionData(
            {"symbol": "BTCUSDT"},
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        with pytest.raises(NotImplementedError):
            position.init_data()

    def test_position_data_inheritance(self):
        """Test that BinancePositionData inherits from PositionData."""
        position = BinancePositionData(
            {}, symbol_name="BTCUSDT", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert hasattr(position, "position_info")
        assert hasattr(position, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
