"""Tests for HTX account container."""

from bt_api_py.containers.accounts.htx_account import HtxSpotRequestAccountData


class TestHtxSpotRequestAccountData:
    """Tests for HtxSpotRequestAccountData."""

    def test_init(self):
        """Test initialization."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")

        assert account.exchange_name == "HTX"
        assert account.account_type == "SPOT"
        assert account.symbol_name == "btcusdt"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "status": "ok",
            "data": [
                {
                    "id": 123456,
                    "type": "spot",
                    "subtype": "",
                    "state": "working",
                }
            ],
        }
        account = HtxSpotRequestAccountData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        # Note: from_dict_get_float returns float, so id becomes "123456.0"
        assert account.account_id == "123456.0"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {"data": [{"id": 123456}]}
        account = HtxSpotRequestAccountData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()
        first_id = account.account_id

        account.init_data()
        assert account.account_id == first_id

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")
        assert account.get_exchange_name() == "HTX"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")
        assert account.get_symbol_name() == "btcusdt"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")
        assert account.get_asset_type() == "SPOT"

    def test_get_account_id(self):
        """Test get_account_id."""
        data = {"data": [{"id": 123456}]}
        account = HtxSpotRequestAccountData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = account.get_account_id()

        assert result == "123456.0"

    def test_get_account_type(self):
        """Test get_account_type."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")
        assert account.get_account_type() == "SPOT"

    def test_get_available_margin(self):
        """Test get_available_margin."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")
        account.available_margin = 10000.0

        assert account.get_available_margin() == 10000.0

    def test_get_used_margin(self):
        """Test get_used_margin."""
        account = HtxSpotRequestAccountData({}, symbol_name="btcusdt", asset_type="SPOT")
        account.used_margin = 5000.0

        assert account.get_used_margin() == 5000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"data": [{"id": 123456}]}
        account = HtxSpotRequestAccountData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()
        result = account.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["account_id"] == "123456.0"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"data": [{"id": 123456}]}
        account = HtxSpotRequestAccountData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(account)

        assert "HTX" in result
        assert "123456.0" in result
