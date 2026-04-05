"""Tests for DydxBalanceData container."""


from bt_api_py.containers.balances.dydx_balance import DydxBalanceData


class TestDydxBalanceData:
    """Tests for DydxBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = DydxBalanceData({}, symbol_name="BTC", asset_type="SWAP")

        assert balance.exchange_name == "DYDX"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SWAP"
        assert balance.has_been_init_data is False

    def test_init_data_subaccount(self):
        """Test init_data with subaccount format."""
        data = {
            "subaccount": {
                "equity": "10000.0",
                "freeCollateral": "8000.0",
                "openPnl": "100.0",
                "initialMarginRequirement": "500.0",
                "marginBalance": "9500.0",
                "availableMargin": "8000.0",
                "positionMargin": "500.0",
                "accountValue": "10000.0",
            }
        }
        balance = DydxBalanceData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.equity == 10000.0
        assert balance.free_collateral == 8000.0

    def test_init_data_single(self):
        """Test init_data with single balance format."""
        data = {
            "symbol": "BTC",
            "equity": "1.5",
            "freeCollateral": "1.0",
            "unrealizedPnl": "100.0",
        }
        balance = DydxBalanceData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.equity == 1.5
        assert balance.free_collateral == 1.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = DydxBalanceData({}, symbol_name="BTC", asset_type="SWAP")
        # Set _initialized to prevent AutoInitMixin
        balance._initialized = True
        assert balance.get_exchange_name() == "DYDX"

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"subaccount": {"equity": "10000.0"}}
        balance = DydxBalanceData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "DYDX"
        assert result["equity"] == 10000.0

    def test_str_representation(self):
        """Test __str__ method."""
        balance = DydxBalanceData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(balance)

        assert "DYDX" in result
