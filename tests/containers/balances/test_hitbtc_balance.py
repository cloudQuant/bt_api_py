"""Tests for HitBtcRequestBalanceData container."""

from bt_api_py.containers.balances.hitbtc_balance import HitBtcRequestBalanceData


class TestHitBtcRequestBalanceData:
    """Tests for HitBtcRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = HitBtcRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "HITBTC"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "currency": "BTC",
            "available": "1.5",
            "reserved": "0.5",
            "timestamp": 1234567890.0,
        }
        balance = HitBtcRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.available == 1.5
        assert balance.reserved == 0.5

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"currency": "BTC", "available": "1.5", "reserved": "0.5"}
        balance = HitBtcRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "HITBTC"
        assert result["currency"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"currency": "BTC", "available": "1.5"}
        balance = HitBtcRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(balance)

        assert "HITBTC" in result
