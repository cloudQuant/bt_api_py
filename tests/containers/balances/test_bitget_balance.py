"""Tests for Bitget balance containers."""


from bt_api_py.containers.balances.bitget_balance import (
    BitgetBalanceData,
    BitgetRequestBalanceData,
    BitgetSpotWssAccountData,
    BitgetWssBalanceData,
)


class TestBitgetBalanceData:
    """Tests for BitgetBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = BitgetBalanceData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert balance.exchange_name == "BITGET"
        assert balance.symbol_name == "BTCUSDT"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "coin": "USDT",
            "coinType": "token",
            "available": "1000.0",
            "frozen": "100.0",
            "stored": "0.0",
            "usdValue": "1000.0",
            "eq": "1100.0",
        }
        balance = BitgetBalanceData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.coin == "USDT"
        assert balance.available == 1000.0
        assert balance.frozen == 100.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = BitgetBalanceData({}, symbol_name="BTCUSDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_exchange_name() == "BITGET"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        balance = BitgetBalanceData({}, symbol_name="BTCUSDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_symbol_name() == "BTCUSDT"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = BitgetBalanceData({}, symbol_name="BTCUSDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_asset_type() == "SPOT"

    def test_get_total(self):
        """Test get_total returns sum of available, frozen, stored."""
        data = {"available": "1000.0", "frozen": "100.0", "stored": "50.0"}
        balance = BitgetBalanceData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )

        assert balance.get_total() == 1150.0

    def test_is_empty(self):
        """Test is_empty."""
        data = {"available": "0.0", "frozen": "0.0", "stored": "0.0"}
        balance = BitgetBalanceData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )

        assert balance.is_empty() is True

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"coin": "USDT", "available": "1000.0"}
        balance = BitgetBalanceData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "BITGET"
        assert result["coin"] == "USDT"


class TestBitgetWssBalanceData:
    """Tests for BitgetWssBalanceData."""

    def test_init_data_wss_format(self):
        """Test init_data with WebSocket format keys."""
        data = {
            "a": "USDT",
            "t": "token",
            "b": "1000.0",
            "f": "100.0",
            "s": "0.0",
        }
        balance = BitgetWssBalanceData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.coin == "USDT"
        assert balance.available == 1000.0
        assert balance.frozen == 100.0


class TestBitgetRequestBalanceData:
    """Tests for BitgetRequestBalanceData."""

    def test_init_data_request_format(self):
        """Test init_data with REST API format keys."""
        data = {
            "coin": "USDT",
            "coinType": "token",
            "available": "1000.0",
            "frozen": "100.0",
            "equity": "1100.0",
        }
        balance = BitgetRequestBalanceData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.coin == "USDT"
        assert balance.available == 1000.0
        assert balance.equity == 1100.0


class TestBitgetSpotWssAccountData:
    """Tests for BitgetSpotWssAccountData."""

    def test_init(self):
        """Test initialization."""
        account = BitgetSpotWssAccountData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert account.exchange_name == "BITGET"
        assert account.symbol_name == "BTCUSDT"
        assert account.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "data": {
                "balances": [
                    {"a": "USDT", "b": "1000.0", "f": "100.0"},
                    {"a": "BTC", "b": "1.0", "f": "0.0"},
                ]
            }
        }
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert len(account.balances) == 2

    def test_get_balances(self):
        """Test get_balances."""
        data = {"data": {"balances": [{"a": "USDT", "b": "1000.0"}]}}
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balances = account.get_balances()

        assert len(balances) == 1

    def test_get_balance(self):
        """Test get_balance for specific coin."""
        data = {"data": {"balances": [{"a": "USDT", "b": "1000.0"}]}}
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance = account.get_balance("USDT")

        assert balance is not None
        assert balance.coin == "USDT"

    def test_get_balance_not_found(self):
        """Test get_balance returns None for missing coin."""
        data = {"data": {"balances": [{"a": "USDT", "b": "1000.0"}]}}
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance = account.get_balance("ETH")

        assert balance is None
