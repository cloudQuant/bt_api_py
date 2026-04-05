"""Tests for OkxBarData container."""


from bt_api_py.containers.bars.okx_bar import OkxBarData


class TestOkxBarData:
    """Tests for OkxBarData."""

    def test_init(self):
        """Test initialization."""
        bar = OkxBarData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert bar.exchange_name == "OKX"
        assert bar.symbol_name == "BTC-USDT"
        assert bar.asset_type == "SPOT"
        assert bar.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with bar info."""
        data = [
            1688671955000,
            "50000.0",
            "51000.0",
            "49000.0",
            "50500.0",
            "1000.0",
            "50000000.0",
            "50000000.0",
            "1",
        ]
        bar = OkxBarData(
            data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        bar.init_data()

        assert bar.has_been_init_data is True
        assert bar.open_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data method."""
        data = [
            1688671955000,
            "50000.0",
            "51000.0",
            "49000.0",
            "50500.0",
            "1000.0",
            "50000000.0",
            "50000000.0",
            "1",
        ]
        bar = OkxBarData(
            data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = bar.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "BTC-USDT"
