"""Tests for OKX liquidation order module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.liquidations.okx_liquidation_order import OkxLiquidationOrderData


class TestOkxLiquidationOrderData:
    """Tests for OkxLiquidationOrderData class."""

    def test_init(self):
        """Test initialization."""
        liquidation = OkxLiquidationOrderData(
            {"instId": "BTC-USDT-SWAP"},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert liquidation.exchange_name == "OKX"
        assert liquidation.symbol_name == "BTC-USDT-SWAP"
        assert liquidation.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        liquidation_info = {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "tradeId": "123456",
            "px": "20000",
            "sz": "100",
            "side": "sell",
            "posSide": "long",
            "bkPx": "19900",
            "ts": "1630000000000",
        }
        liquidation = OkxLiquidationOrderData(
            liquidation_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        liquidation.init_data()

        assert liquidation.inst_id == "BTC-USDT-SWAP"
        assert liquidation.inst_type == "SWAP"
        assert liquidation.trade_id == "123456"
        assert liquidation.price == 20000.0
        assert liquidation.size == 100.0
        assert liquidation.side == "sell"
        assert liquidation.pos_side == "long"
        assert liquidation.bankruptcy_price == 19900.0
        assert liquidation.server_time == 1630000000000.0

    def test_liquidation_data_inheritance(self):
        """Test that OkxLiquidationOrderData inherits from LiquidationData."""
        liquidation = OkxLiquidationOrderData(
            {}, symbol_name="BTC-USDT-SWAP", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert hasattr(liquidation, "liquidation_info")
        assert hasattr(liquidation, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
