"""Tests for CTP order container."""

from bt_api_py.containers.ctp.ctp_order import (
    CTP_DIRECTION_MAP,
    CTP_OFFSET_MAP,
    CTP_ORDER_STATUS_MAP,
    CtpOrderData,
)
from bt_api_py.containers.orders.order import OrderStatus


class TestCtpOrderData:
    """Tests for CtpOrderData."""

    def test_init(self):
        """Test initialization."""
        order = CtpOrderData({}, symbol_name="rb2505", asset_type="FUTURE")

        assert order.exchange_name == "CTP"
        assert order.symbol_name == "rb2505"
        assert order.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "InstrumentID": "rb2505",
            "OrderRef": "000001",
            "OrderSysID": "123456",
            "Direction": "0",
            "CombOffsetFlag": "0",
            "LimitPrice": 3500.0,
            "VolumeTotalOriginal": 10,
            "VolumeTraded": 5,
            "VolumeTotal": 5,
            "OrderStatus": "1",
            "InsertTime": "14:30:00",
            "UpdateTime": "14:30:30",
            "StatusMsg": "部分成交",
            "ExchangeID": "SHFE",
            "FrontID": 1,
            "SessionID": 100,
        }
        order = CtpOrderData(data, symbol_name="rb2505", asset_type="FUTURE")
        order.init_data()

        assert order.instrument_id == "rb2505"
        assert order.order_ref == "000001"
        assert order.order_sys_id == "123456"
        assert order.direction == "buy"
        assert order.offset == "open"
        assert order.limit_price == 3500.0
        assert order.volume_total_original == 10
        assert order.volume_traded == 5
        assert order.volume_total == 5
        assert order.order_status == OrderStatus.PARTIAL
        assert order.insert_time == "14:30:00"
        assert order.update_time == "14:30:30"
        assert order.exchange_id == "SHFE"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "InstrumentID": "rb2505",
            "OrderRef": "000001",
        }
        order = CtpOrderData(data)
        order.init_data()
        first_ref = order.order_ref

        order.init_data()
        assert order.order_ref == first_ref

    def test_direction_sell(self):
        """Test sell direction mapping."""
        data = {"Direction": "1"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.direction == "sell"

    def test_offset_close(self):
        """Test close offset mapping."""
        data = {"CombOffsetFlag": "1"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.offset == "close"

    def test_offset_close_today(self):
        """Test close_today offset mapping."""
        data = {"CombOffsetFlag": "3"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.offset == "close_today"

    def test_order_status_completed(self):
        """Test completed order status."""
        data = {"OrderStatus": "0"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.order_status == OrderStatus.COMPLETED

    def test_order_status_canceled(self):
        """Test canceled order status."""
        data = {"OrderStatus": "5"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.order_status == OrderStatus.CANCELED

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        order = CtpOrderData({})
        assert order.get_exchange_name() == "CTP"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        order = CtpOrderData({}, asset_type="FUTURE")
        assert order.get_asset_type() == "FUTURE"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        data = {"InstrumentID": "rb2505"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_symbol_name() == "rb2505"

    def test_get_order_id(self):
        """Test get_order_id."""
        data = {"OrderSysID": "123456"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_id() == "123456"

    def test_get_client_order_id(self):
        """Test get_client_order_id."""
        data = {"OrderRef": "000001"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_client_order_id() == "000001"

    def test_get_order_size(self):
        """Test get_order_size."""
        data = {"VolumeTotalOriginal": 10}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_size() == 10

    def test_get_order_price(self):
        """Test get_order_price."""
        data = {"LimitPrice": 3500.0}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_price() == 3500.0

    def test_get_order_side(self):
        """Test get_order_side."""
        data = {"Direction": "0"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_side() == "buy"

    def test_get_order_status(self):
        """Test get_order_status."""
        data = {"OrderStatus": "1"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_status() == OrderStatus.PARTIAL

    def test_get_order_offset(self):
        """Test get_order_offset."""
        data = {"CombOffsetFlag": "0"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_offset() == "open"

    def test_get_order_exchange_id(self):
        """Test get_order_exchange_id."""
        data = {"ExchangeID": "SHFE"}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_order_exchange_id() == "SHFE"

    def test_get_executed_qty(self):
        """Test get_executed_qty."""
        data = {"VolumeTraded": 5}
        order = CtpOrderData(data)
        order.init_data()

        assert order.get_executed_qty() == 5

    def test_get_order_type(self):
        """Test get_order_type always returns limit."""
        order = CtpOrderData({})
        assert order.get_order_type() == "limit"

    def test_get_order_time_in_force(self):
        """Test get_order_time_in_force always returns GFD."""
        order = CtpOrderData({})
        assert order.get_order_time_in_force() == "GFD"


class TestCtpMaps:
    """Tests for CTP mapping constants."""

    def test_order_status_map_keys(self):
        """Test CTP_ORDER_STATUS_MAP has expected keys."""
        assert "0" in CTP_ORDER_STATUS_MAP
        assert "1" in CTP_ORDER_STATUS_MAP
        assert "5" in CTP_ORDER_STATUS_MAP

    def test_direction_map_keys(self):
        """Test CTP_DIRECTION_MAP has expected keys."""
        assert "0" in CTP_DIRECTION_MAP
        assert "1" in CTP_DIRECTION_MAP
        assert CTP_DIRECTION_MAP["0"] == "buy"
        assert CTP_DIRECTION_MAP["1"] == "sell"

    def test_offset_map_keys(self):
        """Test CTP_OFFSET_MAP has expected keys."""
        assert "0" in CTP_OFFSET_MAP
        assert "1" in CTP_OFFSET_MAP
        assert "3" in CTP_OFFSET_MAP
        assert CTP_OFFSET_MAP["0"] == "open"
        assert CTP_OFFSET_MAP["1"] == "close"
        assert CTP_OFFSET_MAP["3"] == "close_today"
