"""Tests for TradeData base class."""

import pytest

from bt_api_py.containers.trades.trade import TradeData


class TestTradeData:
    """Tests for TradeData base class."""

    def test_init(self):
        """Test initialization."""
        trade = TradeData({})

        assert trade.event == "TradeEvent"
        assert trade.trade_info == {}
        assert trade.has_been_json_encoded is False
        assert trade.exchange_name is None
        assert trade.symbol_name is None
        assert trade.asset_type is None

    def test_init_with_params(self):
        """Test initialization with parameters."""
        data = {"test": "data"}
        trade = TradeData(
            data, has_been_json_encoded=True, symbol_name="BTCUSDT", asset_type="SPOT"
        )

        assert trade.has_been_json_encoded is True
        assert trade.trade_data == data
        assert trade.symbol_name == "BTCUSDT"
        assert trade.asset_type == "SPOT"

    def test_get_event(self):
        """Test get_event."""
        trade = TradeData({})
        assert trade.get_event() == "TradeEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = TradeData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        trade._initialized = True
        trade.exchange_name = "BINANCE"
        trade.symbol_name = "BTCUSDT"
        trade.trade_id = "12345"
        trade.trade_price = 50000.0
        trade.trade_volume = 1.0
        trade.trade_fee = 0.1
        trade.trade_fee_symbol = "BNB"

        result = trade.get_all_data()

        assert result["exchange_name"] == "BINANCE"
        assert result["symbol_name"] == "BTCUSDT"
        assert result["trade_id"] == "12345"
        assert result["trade_price"] == 50000.0
        assert result["trade_volume"] == 1.0
        assert result["trade_fee"] == 0.1
        assert result["trade_fee_symbol"] == "BNB"

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_exchange_name()

    def test_get_symbol_name_raises_not_implemented(self):
        """Test get_symbol_name raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_symbol_name()

    def test_get_trade_id_raises_not_implemented(self):
        """Test get_trade_id raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_id()

    def test_get_trade_price_raises_not_implemented(self):
        """Test get_trade_price raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_price()

    def test_get_trade_volume_raises_not_implemented(self):
        """Test get_trade_volume raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_volume()

    def test_get_trade_side_raises_not_implemented(self):
        """Test get_trade_side raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_side()

    def test_get_trade_fee_raises_not_implemented(self):
        """Test get_trade_fee raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_fee()

    def test_get_trade_fee_symbol_raises_not_implemented(self):
        """Test get_trade_fee_symbol raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_fee_symbol()

    def test_get_order_id_raises_not_implemented(self):
        """Test get_order_id raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_order_id()

    def test_get_trade_offset_raises_not_implemented(self):
        """Test get_trade_offset raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            trade.get_trade_offset()

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            str(trade)

    def test_repr_raises_not_implemented(self):
        """Test __repr__ raises NotImplementedError."""
        trade = TradeData({})

        with pytest.raises(NotImplementedError):
            repr(trade)
