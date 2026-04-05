"""Tests for IB account container."""

from bt_api_py.containers.ib.ib_account import IbAccountData


class TestIbAccountData:
    """Tests for IbAccountData."""

    def test_init(self):
        """Test initialization."""
        account = IbAccountData({}, symbol_name="AAPL", asset_type="STK")

        assert account.exchange_name == "IB"
        assert account.symbol_name == "AAPL"
        assert account.asset_type == "STK"

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "AccountID": "U123456",
            "NetLiquidation": 100000.0,
            "TotalCashValue": 50000.0,
            "BuyingPower": 200000.0,
            "GrossPositionValue": 50000.0,
            "MaintMarginReq": 25000.0,
            "AvailableFunds": 75000.0,
            "UnrealizedPnL": 5000.0,
            "RealizedPnL": 3000.0,
            "Currency": "USD",
        }
        account = IbAccountData(data, asset_type="STK")
        account.init_data()

        assert account.account_id == "U123456"
        assert account.net_liquidation == 100000.0
        assert account.total_cash_value == 50000.0
        assert account.buying_power == 200000.0
        assert account.gross_position_value == 50000.0
        assert account.maintenance_margin == 25000.0
        assert account.available_funds == 75000.0
        assert account.unrealized_pnl == 5000.0
        assert account.realized_pnl == 3000.0
        assert account.currency == "USD"

    def test_init_data_with_alternate_keys(self):
        """Test init_data with alternate key names."""
        data = {
            "account": "U654321",
            "NetLiquidation": 50000.0,
        }
        account = IbAccountData(data)
        account.init_data()

        assert account.account_id == "U654321"
        assert account.net_liquidation == 50000.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "AccountID": "U123456",
            "NetLiquidation": 100000.0,
        }
        account = IbAccountData(data)
        account.init_data()
        first_value = account.net_liquidation

        account.init_data()
        assert account.net_liquidation == first_value

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        account = IbAccountData({})
        assert account.get_exchange_name() == "IB"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        account = IbAccountData({}, asset_type="STK")
        assert account.get_asset_type() == "STK"

    def test_get_account_type(self):
        """Test get_account_type returns currency."""
        data = {"Currency": "EUR"}
        account = IbAccountData(data)
        account.init_data()

        assert account.get_account_type() == "EUR"

    def test_get_account_type_default(self):
        """Test get_account_type default is USD."""
        account = IbAccountData({})
        account.init_data()

        assert account.get_account_type() == "USD"

    def test_get_server_time(self):
        """Test get_server_time returns 0.0."""
        account = IbAccountData({})
        assert account.get_server_time() == 0.0

    def test_get_total_wallet_balance(self):
        """Test get_total_wallet_balance."""
        data = {"NetLiquidation": 100000.0}
        account = IbAccountData(data)
        account.init_data()

        assert account.get_total_wallet_balance() == 100000.0

    def test_get_margin(self):
        """Test get_margin returns net_liquidation."""
        data = {"NetLiquidation": 100000.0}
        account = IbAccountData(data)
        account.init_data()

        assert account.get_margin() == 100000.0

    def test_get_available_margin(self):
        """Test get_available_margin."""
        data = {"AvailableFunds": 75000.0}
        account = IbAccountData(data)
        account.init_data()

        assert account.get_available_margin() == 75000.0

    def test_get_unrealized_profit(self):
        """Test get_unrealized_profit."""
        data = {"UnrealizedPnL": 5000.0}
        account = IbAccountData(data)
        account.init_data()

        assert account.get_unrealized_profit() == 5000.0

    def test_get_balances(self):
        """Test get_balances returns self in list."""
        account = IbAccountData({})
        balances = account.get_balances()

        assert balances == [account]

    def test_get_positions(self):
        """Test get_positions returns empty list."""
        account = IbAccountData({})
        assert account.get_positions() == []

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "AccountID": "U123456",
            "NetLiquidation": 100000.0,
            "AvailableFunds": 75000.0,
        }
        account = IbAccountData(data)
        account.init_data()

        result = account.get_all_data()

        assert result["exchange_name"] == "IB"
        assert result["account_id"] == "U123456"
        assert result["net_liquidation"] == 100000.0
        assert result["available_funds"] == 75000.0
