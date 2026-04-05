"""Tests for OKX liquidation warning data container."""

from __future__ import annotations

from bt_api_py.containers.liquidations.okx_liquidation_warning import OkxLiquidationWarningData


def test_okx_liquidation_warning_data():
    """Test OkxLiquidationWarningData initialization and methods."""
    data = {
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "pos": "1",
        "posCcy": "BTC",
        "liqPx": "20000",
        "markPx": "25000",
        "tz": "0",
    }

    lw = OkxLiquidationWarningData(data, "BTC-USDT-SWAP", "SWAP", True)
    lw.init_data()

    assert lw.get_exchange_name() == "OKX"
    assert lw.get_asset_type() == "SWAP"
    assert lw.get_symbol_name() == "BTC-USDT-SWAP"
    assert lw.get_inst_id() == "BTC-USDT-SWAP"
    assert lw.get_inst_type() == "SWAP"
    assert lw.get_position() == 1.0
    assert lw.get_position_ccy() == "BTC"
    assert lw.get_liquidation_price() == 20000.0
    assert lw.get_mark_price() == 25000.0

    all_data = lw.get_all_data()
    assert all_data["exchange_name"] == "OKX"
    assert all_data["inst_id"] == "BTC-USDT-SWAP"
    assert all_data["liquidation_price"] == 20000.0

    # Test __str__ method
    lw_str = str(lw)
    assert "BTC-USDT-SWAP" in lw_str
    assert "20000" in lw_str


def test_okx_liquidation_warning_with_pos_side():
    """Test OkxLiquidationWarningData with posSide field."""
    data = {
        "instId": "ETH-USDT-SWAP",
        "instType": "SWAP",
        "pos": "10",
        "posSide": "long",
        "posCcy": "ETH",
        "liqPx": "1500",
        "markPx": "1800",
        "tz": "0",
    }

    lw = OkxLiquidationWarningData(data, "ETH-USDT-SWAP", "SWAP", True)
    lw.init_data()

    assert lw.get_pos_side() == "long"
    assert lw.get_position() == 10.0
    assert lw.get_liquidation_price() == 1500.0


def test_okx_liquidation_warning_empty_data():
    """Test OkxLiquidationWarningData with missing fields."""
    data = {
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
    }

    lw = OkxLiquidationWarningData(data, "BTC-USDT-SWAP", "SWAP", True)
    lw.init_data()

    assert lw.get_inst_id() == "BTC-USDT-SWAP"
    assert lw.get_position() is None
    assert lw.get_liquidation_price() is None


if __name__ == "__main__":
    test_okx_liquidation_warning_data()
    test_okx_liquidation_warning_with_pos_side()
    test_okx_liquidation_warning_empty_data()
    print("All liquidation warning tests passed!")
