"""Tests for OKX account Greeks data container."""

from __future__ import annotations

from bt_api_py.containers.greeks.okx_account_greeks import OkxAccountGreeksData


def test_okx_account_greeks_data():
    """Test OkxAccountGreeksData initialization and methods."""
    data = {
        "gtD": "0.5",
        "gtG": "0.1",
        "gtT": "-0.05",
        "gtV": "0.2",
        "bsD": "0.48",
        "bsG": "0.09",
        "bsT": "-0.04",
        "bsV": "0.18",
        "uTime": "1700000000000",
    }

    greeks = OkxAccountGreeksData(data, "ANY", "SWAP", True)
    greeks.init_data()

    assert greeks.get_exchange_name() == "OKX"
    assert greeks.get_asset_type() == "SWAP"
    assert greeks.get_symbol_name() == "ANY"
    assert greeks.get_pa_delta() == 0.5
    assert greeks.get_pa_gamma() == 0.1
    assert greeks.get_pa_theta() == -0.05
    assert greeks.get_pa_vega() == 0.2
    assert greeks.get_bs_delta() == 0.48
    assert greeks.get_bs_gamma() == 0.09
    assert greeks.get_bs_theta() == -0.04
    assert greeks.get_bs_vega() == 0.18
    assert greeks.get_update_time() == 1700000000000.0
    assert greeks.get_server_time() == 1700000000000.0

    all_data = greeks.get_all_data()
    assert all_data["exchange_name"] == "OKX"
    assert all_data["pa_delta"] == 0.5
    assert all_data["bs_delta"] == 0.48

    # Test __str__ method
    greeks_str = str(greeks)
    assert "0.5" in greeks_str
    assert "0.48" in greeks_str


def test_okx_account_greeks_zero_values():
    """Test OkxAccountGreeksData with zero values."""
    data = {
        "gtD": "0",
        "gtG": "0",
        "gtT": "0",
        "gtV": "0",
        "bsD": "0",
        "bsG": "0",
        "bsT": "0",
        "bsV": "0",
        "uTime": "1700000000000",
    }

    greeks = OkxAccountGreeksData(data, "ANY", "SWAP", True)
    greeks.init_data()

    assert greeks.get_pa_delta() == 0.0
    assert greeks.get_pa_gamma() == 0.0
    assert greeks.get_bs_delta() == 0.0


def test_okx_account_greeks_partial_data():
    """Test OkxAccountGreeksData with missing fields."""
    data = {
        "gtD": "0.5",
        "bsD": "0.48",
    }

    greeks = OkxAccountGreeksData(data, "ANY", "SWAP", True)
    greeks.init_data()

    assert greeks.get_pa_delta() == 0.5
    assert greeks.get_bs_delta() == 0.48
    assert greeks.get_pa_gamma() is None
    assert greeks.get_bs_gamma() is None


if __name__ == "__main__":
    test_okx_account_greeks_data()
    test_okx_account_greeks_zero_values()
    test_okx_account_greeks_partial_data()
    print("All account Greeks tests passed!")
