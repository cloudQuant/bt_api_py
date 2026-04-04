"""Tests for OKX account greeks module."""

import pytest

from bt_api_py.containers.greeks.okx_account_greeks import OkxAccountGreeksData


class TestOkxAccountGreeksData:
    """Tests for OkxAccountGreeksData class."""

    def test_init(self):
        """Test initialization."""
        greeks = OkxAccountGreeksData(
            {"gtD": "0.5"},
            symbol_name="BTC",
            asset_type="OPTION",
            has_been_json_encoded=True
        )

        assert greeks.exchange_name == "OKX"
        assert greeks.symbol_name == "BTC"
        assert greeks.asset_type == "OPTION"

    def test_init_data(self):
        """Test init_data method."""
        greeks_info = {
            "gtD": "0.5",
            "gtG": "0.1",
            "gtT": "-0.01",
            "gtV": "0.02",
            "bsD": "0.5",
            "bsG": "0.1",
            "bsT": "-0.01",
            "bsV": "0.02",
            "uTime": "1700000000000",
        }
        greeks = OkxAccountGreeksData(
            greeks_info,
            symbol_name="BTC",
            asset_type="OPTION",
            has_been_json_encoded=True
        )
        greeks.init_data()

        assert greeks.pa_delta == 0.5
        assert greeks.pa_gamma == 0.1
        assert greeks.pa_theta == -0.01
        assert greeks.pa_vega == 0.02
        assert greeks.bs_delta == 0.5
        assert greeks.bs_gamma == 0.1
        assert greeks.bs_theta == -0.01
        assert greeks.bs_vega == 0.02
        assert greeks.update_time == 1700000000000.0

    def test_greeks_data_inheritance(self):
        """Test that OkxAccountGreeksData inherits from GreeksData."""
        greeks = OkxAccountGreeksData(
            {},
            symbol_name="BTC",
            asset_type="OPTION",
            has_been_json_encoded=True
        )

        assert hasattr(greeks, "greeks_info")
        assert hasattr(greeks, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
