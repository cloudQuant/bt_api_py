"""Tests for IB position container."""


from bt_api_py.containers.ib.ib_position import IbPositionData


class TestIbPositionData:
    """Tests for IbPositionData."""

    def test_init(self):
        """Test initialization."""
        position = IbPositionData({}, symbol_name="AAPL", asset_type="STK")

        assert position.exchange_name == "IB"
        assert position.symbol_name == "AAPL"
        assert position.asset_type == "STK"

    def test_init_data(self):
        """Test init_data with position info."""
        data = {
            "account": "U123456",
            "symbol": "AAPL",
            "secType": "STK",
            "position": 100,
            "avgCost": 150.0,
            "marketPrice": 155.0,
            "marketValue": 15500.0,
            "unrealizedPNL": 500.0,
            "realizedPNL": 1000.0,
            "currency": "USD",
        }
        position = IbPositionData(data, symbol_name="AAPL", asset_type="STK")
        position.init_data()

        assert position.account == "U123456"
        assert position.contract_symbol == "AAPL"
        assert position.sec_type == "STK"
        assert position.position_val == 100
        assert position.avg_cost == 150.0
        assert position.market_price_val == 155.0
        assert position.market_value == 15500.0
        assert position.unrealized_pnl_val == 500.0
        assert position.realized_pnl_val == 1000.0
        assert position.currency == "USD"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "symbol": "AAPL",
            "position": 100,
        }
        position = IbPositionData(data)
        position.init_data()
        first_position = position.position_val

        position.init_data()
        assert position.position_val == first_position

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        position = IbPositionData({})
        assert position.get_exchange_name() == "IB"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        data = {"secType": "FUT"}
        position = IbPositionData(data)
        position.init_data()

        assert position.get_asset_type() == "FUT"

    def test_get_asset_type_fallback(self):
        """Test get_asset_type fallback."""
        position = IbPositionData({}, asset_type="STK")
        assert position.get_asset_type() == "STK"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        data = {"symbol": "AAPL"}
        position = IbPositionData(data)
        position.init_data()

        assert position.get_symbol_name() == "AAPL"

    def test_get_symbol_name_fallback(self):
        """Test get_symbol_name fallback."""
        position = IbPositionData({}, symbol_name="MSFT")
        assert position.get_symbol_name() == "MSFT"

    def test_get_position_volume(self):
        """Test get_position_volume."""
        data = {"position": 100}
        position = IbPositionData(data)
        position.init_data()

        assert position.get_position_volume() == 100

    def test_get_avg_price(self):
        """Test get_avg_price."""
        data = {"avgCost": 150.0}
        position = IbPositionData(data)
        position.init_data()

        assert position.get_avg_price() == 150.0

    def test_get_mark_price(self):
        """Test get_mark_price."""
        data = {"marketPrice": 155.0}
        position = IbPositionData(data)
        position.init_data()

        assert position.get_mark_price() == 155.0

    def test_get_liquidation_price(self):
        """Test get_liquidation_price returns None for IB."""
        position = IbPositionData({})
        assert position.get_liquidation_price() is None

    def test_get_initial_margin(self):
        """Test get_initial_margin returns 0.0 for IB."""
        position = IbPositionData({})
        assert position.get_initial_margin() == 0.0

    def test_get_maintain_margin(self):
        """Test get_maintain_margin returns 0.0 for IB."""
        position = IbPositionData({})
        assert position.get_maintain_margin() == 0.0

    def test_get_position_unrealized_pnl(self):
        """Test get_position_unrealized_pnl."""
        data = {"unrealizedPNL": 500.0}
        position = IbPositionData(data)
        position.init_data()

        assert position.get_position_unrealized_pnl() == 500.0

    def test_get_position_funding_value(self):
        """Test get_position_funding_value returns 0.0 for IB."""
        position = IbPositionData({})
        assert position.get_position_funding_value() == 0.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "account": "U123456",
            "symbol": "AAPL",
            "position": 100,
            "unrealizedPNL": 500.0,
        }
        position = IbPositionData(data)
        position.init_data()

        result = position.get_all_data()

        assert result["exchange_name"] == "IB"
        assert result["account"] == "U123456"
        assert result["symbol"] == "AAPL"
        assert result["position"] == 100
        assert result["unrealized_pnl"] == 500.0
