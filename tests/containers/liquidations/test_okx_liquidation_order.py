"""Tests for OkxLiquidationOrderData container."""


from bt_api_py.containers.liquidations.okx_liquidation_order import OkxLiquidationOrderData


class TestOkxLiquidationOrderData:
    """Tests for OkxLiquidationOrderData."""

    def test_init(self):
        """Test initialization."""
        liquidation = OkxLiquidationOrderData({}, symbol_name="BTC-USDT-SWAP", asset_type="SWAP")

        assert liquidation.exchange_name == "OKX"
        assert liquidation.symbol_name == "BTC-USDT-SWAP"
        assert liquidation.asset_type == "SWAP"
        assert liquidation.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with liquidation info."""
        data = {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "tradeId": "123456",
            "px": "20000",
            "sz": "100",
            "side": "sell",
            "posSide": "long",
            "bkPx": "19900",
            "ts": "1630000000000",
        }
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        liquidation.init_data()

        assert liquidation.inst_id == "BTC-USDT-SWAP"
        assert liquidation.inst_type == "SWAP"
        assert liquidation.trade_id == "123456"
        assert liquidation.price == 20000.0
        assert liquidation.size == 100.0
        assert liquidation.side == "sell"
        assert liquidation.pos_side == "long"
        assert liquidation.bankruptcy_price == 19900.0
        assert liquidation.server_time == 1630000000000.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "instId": "BTC-USDT-SWAP",
            "tradeId": "123456",
        }
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        liquidation.init_data()
        first_trade_id = liquidation.trade_id

        liquidation.init_data()
        assert liquidation.trade_id == first_trade_id

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        liquidation = OkxLiquidationOrderData({}, symbol_name="BTC-USDT-SWAP", asset_type="SWAP")
        assert liquidation.get_exchange_name() == "OKX"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        liquidation = OkxLiquidationOrderData({}, symbol_name="BTC-USDT-SWAP", asset_type="SWAP")
        assert liquidation.get_symbol_name() == "BTC-USDT-SWAP"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        liquidation = OkxLiquidationOrderData({}, symbol_name="BTC-USDT-SWAP", asset_type="SWAP")
        assert liquidation.get_asset_type() == "SWAP"

    def test_get_inst_id(self):
        """Test get_inst_id."""
        data = {"instId": "BTC-USDT-SWAP"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_inst_id() == "BTC-USDT-SWAP"

    def test_get_trade_id(self):
        """Test get_trade_id."""
        data = {"tradeId": "123456"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_trade_id() == "123456"

    def test_get_price(self):
        """Test get_price."""
        data = {"px": "20000"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_price() == 20000.0

    def test_get_size(self):
        """Test get_size."""
        data = {"sz": "100"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_size() == 100.0

    def test_get_side(self):
        """Test get_side."""
        data = {"side": "sell"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_side() == "sell"

    def test_get_pos_side(self):
        """Test get_pos_side."""
        data = {"posSide": "long"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_pos_side() == "long"

    def test_get_bankruptcy_price(self):
        """Test get_bankruptcy_price."""
        data = {"bkPx": "19900"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert liquidation.get_bankruptcy_price() == 19900.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "instId": "BTC-USDT-SWAP",
            "tradeId": "123456",
            "px": "20000",
            "sz": "100",
        }
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        result = liquidation.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["inst_id"] == "BTC-USDT-SWAP"
        assert result["trade_id"] == "123456"
        assert result["price"] == 20000.0
        assert result["size"] == 100.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"instId": "BTC-USDT-SWAP"}
        liquidation = OkxLiquidationOrderData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(liquidation)

        assert "OKX" in result
        assert "BTC-USDT-SWAP" in result
