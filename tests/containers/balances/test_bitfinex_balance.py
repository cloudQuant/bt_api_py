"""Tests for BitfinexSpotRequestBalanceData container."""


from bt_api_py.containers.balances.bitfinex_balance import BitfinexSpotRequestBalanceData


class TestBitfinexSpotRequestBalanceData:
    """Tests for BitfinexSpotRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = BitfinexSpotRequestBalanceData({}, asset_type="SPOT")

        assert balance.exchange_name == "BITFINEX"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "currency": "BTC",
            "balance": 1.5,
            "available": 1.0,
            "reserved": 0.5,
        }
        balance = BitfinexSpotRequestBalanceData(
            data, asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.balance == 1.5
        assert balance.available == 1.0
        assert balance.reserved == 0.5

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {"currency": "BTC", "balance": 1.0}
        balance = BitfinexSpotRequestBalanceData(
            data, asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()
        first_currency = balance.currency

        balance.init_data()
        assert balance.currency == first_currency

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = BitfinexSpotRequestBalanceData({}, asset_type="SPOT")
        assert balance.get_exchange_name() == "BITFINEX"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = BitfinexSpotRequestBalanceData({}, asset_type="SPOT")
        assert balance.get_asset_type() == "SPOT"

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"currency": "BTC", "balance": 1.0}
        balance = BitfinexSpotRequestBalanceData(
            data, asset_type="SPOT", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["asset_type"] == "SPOT"
        assert result["currency"] == "BTC"
        assert result["balance"] == 1.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"currency": "BTC"}
        balance = BitfinexSpotRequestBalanceData(
            data, asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(balance)

        assert "BITFINEX" in result
        assert "BTC" in result
