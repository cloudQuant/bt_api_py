"""Tests for CTP position container."""

from bt_api_py.containers.ctp.ctp_position import CTP_POS_DIRECTION_MAP, CtpPositionData


class TestCtpPositionData:
    """Tests for CtpPositionData."""

    def test_init(self):
        """Test initialization."""
        position = CtpPositionData({}, symbol_name="rb2505", asset_type="FUTURE")

        assert position.exchange_name == "CTP"
        assert position.symbol_name == "rb2505"
        assert position.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data with position info."""
        data = {
            "InstrumentID": "rb2505",
            "PosiDirection": "2",
            "Position": 10,
            "TodayPosition": 5,
            "YdPosition": 5,
            "OpenCost": 35000.0,
            "PositionCost": 35000.0,
            "UseMargin": 10000.0,
            "PositionProfit": 500.0,
            "CloseProfit": 1000.0,
            "SettlementPrice": 3550.0,
            "ExchangeID": "SHFE",
        }
        position = CtpPositionData(data, symbol_name="rb2505", asset_type="FUTURE")
        position.init_data()

        assert position.instrument_id == "rb2505"
        assert position.position_direction == "long"
        assert position.position_volume == 10
        assert position.today_position == 5
        assert position.yd_position == 5
        assert position.open_cost == 35000.0
        assert position.position_cost == 35000.0
        assert position.use_margin == 10000.0
        assert position.position_profit == 500.0
        assert position.close_profit == 1000.0
        assert position.settlement_price == 3550.0
        assert position.exchange_id == "SHFE"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "InstrumentID": "rb2505",
            "Position": 10,
        }
        position = CtpPositionData(data)
        position.init_data()
        first_volume = position.position_volume

        position.init_data()
        assert position.position_volume == first_volume

    def test_position_direction_short(self):
        """Test short position direction."""
        data = {"PosiDirection": "3"}
        position = CtpPositionData(data)
        position.init_data()

        assert position.position_direction == "short"

    def test_position_direction_net(self):
        """Test net position direction."""
        data = {"PosiDirection": "1"}
        position = CtpPositionData(data)
        position.init_data()

        assert position.position_direction == "net"

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        position = CtpPositionData({})
        assert position.get_exchange_name() == "CTP"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        position = CtpPositionData({}, asset_type="FUTURE")
        assert position.get_asset_type() == "FUTURE"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        data = {"InstrumentID": "rb2505"}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_symbol_name() == "rb2505"

    def test_get_position_volume(self):
        """Test get_position_volume."""
        data = {"Position": 10}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_position_volume() == 10

    def test_get_avg_price(self):
        """Test get_avg_price calculation."""
        data = {
            "Position": 10,
            "PositionCost": 35000.0,
        }
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_avg_price() == 3500.0

    def test_get_avg_price_zero_volume(self):
        """Test get_avg_price with zero volume."""
        data = {"Position": 0, "PositionCost": 35000.0}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_avg_price() == 0.0

    def test_get_mark_price(self):
        """Test get_mark_price."""
        data = {"SettlementPrice": 3550.0}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_mark_price() == 3550.0

    def test_get_liquidation_price(self):
        """Test get_liquidation_price returns None for CTP."""
        position = CtpPositionData({})
        assert position.get_liquidation_price() is None

    def test_get_initial_margin(self):
        """Test get_initial_margin."""
        data = {"UseMargin": 10000.0}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_initial_margin() == 10000.0

    def test_get_maintain_margin(self):
        """Test get_maintain_margin."""
        data = {"UseMargin": 10000.0}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_maintain_margin() == 10000.0

    def test_get_position_unrealized_pnl(self):
        """Test get_position_unrealized_pnl."""
        data = {"PositionProfit": 500.0}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_position_unrealized_pnl() == 500.0

    def test_get_position_funding_value(self):
        """Test get_position_funding_value returns 0 for CTP."""
        position = CtpPositionData({})
        assert position.get_position_funding_value() == 0.0

    def test_get_position_direction(self):
        """Test get_position_direction."""
        data = {"PosiDirection": "2"}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_position_direction() == "long"

    def test_get_today_position(self):
        """Test get_today_position."""
        data = {"TodayPosition": 5}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_today_position() == 5

    def test_get_yesterday_position(self):
        """Test get_yesterday_position."""
        data = {"YdPosition": 5}
        position = CtpPositionData(data)
        position.init_data()

        assert position.get_yesterday_position() == 5

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "InstrumentID": "rb2505",
            "Position": 10,
            "PositionProfit": 500.0,
        }
        position = CtpPositionData(data)
        position.init_data()

        result = position.get_all_data()

        assert result["exchange_name"] == "CTP"
        assert result["instrument_id"] == "rb2505"
        assert result["position_volume"] == 10
        assert result["position_profit"] == 500.0


class TestCtpPosDirectionMap:
    """Tests for CTP_POS_DIRECTION_MAP."""

    def test_map_keys(self):
        """Test CTP_POS_DIRECTION_MAP has expected keys."""
        assert "1" in CTP_POS_DIRECTION_MAP
        assert "2" in CTP_POS_DIRECTION_MAP
        assert "3" in CTP_POS_DIRECTION_MAP

    def test_map_values(self):
        """Test CTP_POS_DIRECTION_MAP values."""
        assert CTP_POS_DIRECTION_MAP["1"] == "net"
        assert CTP_POS_DIRECTION_MAP["2"] == "long"
        assert CTP_POS_DIRECTION_MAP["3"] == "short"
