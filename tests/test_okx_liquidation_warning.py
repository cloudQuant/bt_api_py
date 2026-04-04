"""Tests for OKX liquidation warning module."""

import pytest

from bt_api_py.containers.liquidations.okx_liquidation_warning import OkxLiquidationWarningData


class TestOkxLiquidationWarningData:
    """Tests for OkxLiquidationWarningData class."""

    def test_init(self):
        """Test initialization."""
        liquidation = OkxLiquidationWarningData(
            {"instId": "BTC-USDT-SWAP"},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert liquidation.exchange_name == "OKX"
        assert liquidation.symbol_name == "BTC-USDT-SWAP"
        assert liquidation.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        liquidation_info = {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "pos": "1",
            "posCcy": "BTC",
            "liqPx": "20000",
            "markPx": "25000",
        }
        liquidation = OkxLiquidationWarningData(
            liquidation_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )
        liquidation.init_data()

        assert liquidation.inst_id == "BTC-USDT-SWAP"
        assert liquidation.inst_type == "SWAP"
        assert liquidation.position == 1.0
        assert liquidation.position_ccy == "BTC"
        assert liquidation.liquidation_price == 20000.0
        assert liquidation.mark_price == 25000.0

    def test_liquidation_data_inheritance(self):
        """Test that OkxLiquidationWarningData inherits from LiquidationData."""
        liquidation = OkxLiquidationWarningData(
            {},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert hasattr(liquidation, "liquidation_info")
        assert hasattr(liquidation, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
