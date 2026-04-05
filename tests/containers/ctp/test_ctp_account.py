"""Tests for CTP account container."""

from bt_api_py.containers.ctp.ctp_account import CtpAccountData


class TestCtpAccountData:
    """Tests for CtpAccountData."""

    def test_init(self):
        """Test initialization."""
        account = CtpAccountData({}, symbol_name="rb2505", asset_type="FUTURE")

        assert account.exchange_name == "CTP"
        assert account.symbol_name == "rb2505"
        assert account.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "BrokerID": "1234",
            "AccountID": "test_account",
            "PreBalance": 100000.0,
            "Balance": 110000.0,
            "Available": 80000.0,
            "Commission": 500.0,
            "FrozenMargin": 30000.0,
            "CurrMargin": 25000.0,
            "CloseProfit": 5000.0,
            "PositionProfit": 10000.0,
            "Withdraw": 0.0,
            "Deposit": 0.0,
        }
        account = CtpAccountData(data, symbol_name="rb2505", asset_type="FUTURE")
        account.init_data()

        assert account.broker_id == "1234"
        assert account.account_id == "test_account"
        assert account.pre_balance == 100000.0
        assert account.balance == 110000.0
        assert account.available == 80000.0
        assert account.commission == 500.0
        assert account.frozen_margin == 30000.0
        assert account.curr_margin == 25000.0
        assert account.close_profit == 5000.0
        assert account.position_profit == 10000.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "AccountID": "test",
            "Balance": 100000.0,
        }
        account = CtpAccountData(data)
        account.init_data()
        first_balance = account.balance

        account.init_data()
        assert account.balance == first_balance

    def test_risk_degree_calculation(self):
        """Test risk degree calculation."""
        data = {
            "Balance": 100000.0,
            "CurrMargin": 30000.0,
        }
        account = CtpAccountData(data)
        account.init_data()

        assert account.risk_degree == 0.3

    def test_risk_degree_zero_balance(self):
        """Test risk degree with zero balance."""
        data = {
            "Balance": 0.0,
            "CurrMargin": 30000.0,
        }
        account = CtpAccountData(data)
        account.init_data()

        assert account.risk_degree == 0.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        account = CtpAccountData({})
        assert account.get_exchange_name() == "CTP"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        account = CtpAccountData({}, asset_type="FUTURE")
        assert account.get_asset_type() == "FUTURE"

    def test_get_account_type(self):
        """Test get_account_type."""
        data = {"AccountID": "test_account"}
        account = CtpAccountData(data)
        account.init_data()

        assert account.get_account_type() == "test_account"

    def test_get_margin(self):
        """Test get_margin returns balance."""
        data = {"Balance": 100000.0}
        account = CtpAccountData(data)
        account.init_data()

        assert account.get_margin() == 100000.0

    def test_get_available_margin(self):
        """Test get_available_margin."""
        data = {"Available": 80000.0}
        account = CtpAccountData(data)
        account.init_data()

        assert account.get_available_margin() == 80000.0

    def test_get_unrealized_profit(self):
        """Test get_unrealized_profit."""
        data = {"PositionProfit": 5000.0}
        account = CtpAccountData(data)
        account.init_data()

        assert account.get_unrealized_profit() == 5000.0

    def test_get_balances(self):
        """Test get_balances returns self in list."""
        account = CtpAccountData({})
        balances = account.get_balances()

        assert balances == [account]

    def test_get_positions(self):
        """Test get_positions returns empty list."""
        account = CtpAccountData({})
        assert account.get_positions() == []

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "AccountID": "test",
            "Balance": 100000.0,
            "Available": 80000.0,
        }
        account = CtpAccountData(data)
        account.init_data()

        result = account.get_all_data()

        assert result["exchange_name"] == "CTP"
        assert result["account_id"] == "test"
        assert result["balance"] == 100000.0
        assert result["available"] == 80000.0
