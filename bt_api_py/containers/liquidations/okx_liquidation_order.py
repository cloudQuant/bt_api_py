"""OKX Liquidation Order data container."""

import json
import time

from bt_api_py.containers.liquidations.liquidation import LiquidationData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxLiquidationOrderData(LiquidationData):
    """OKX liquidation order data container.

    WebSocket channel: liquidation-orders
    Pushes public liquidation events (not account-specific warnings).

    Example data:
    {
        "arg": {"channel": "liquidation-orders", "instType": "SWAP"},
        "data": [{
            "instId": "BTC-USDT-SWAP",
            "tradeId": "123456",
            "px": "20000",
            "sz": "100",
            "side": "sell",
            "posSide": "long",
            "bkPx": "19900",
            "ts": "1630000000000"
        }]
    }
    """

    def __init__(
        self, liquidation_info, symbol_name, asset_type, has_been_json_encoded=False
    ) -> None:
        super().__init__(liquidation_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.liquidation_data = liquidation_info if has_been_json_encoded else None
        self.inst_id = None
        self.inst_type = None
        self.trade_id = None
        self.price = None
        self.size = None
        self.side = None
        self.pos_side = None
        self.bankruptcy_price = None
        self.server_time = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.liquidation_data = json.loads(self.liquidation_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.inst_id = from_dict_get_string(self.liquidation_data, "instId")
        self.inst_type = from_dict_get_string(self.liquidation_data, "instType")
        self.trade_id = from_dict_get_string(self.liquidation_data, "tradeId")
        self.price = from_dict_get_float(self.liquidation_data, "px")
        self.size = from_dict_get_float(self.liquidation_data, "sz")
        self.side = from_dict_get_string(self.liquidation_data, "side")
        self.pos_side = from_dict_get_string(self.liquidation_data, "posSide")
        self.bankruptcy_price = from_dict_get_float(self.liquidation_data, "bkPx")
        self.server_time = from_dict_get_float(self.liquidation_data, "ts")
        self.has_been_init_data = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.symbol_name

    def get_server_time(self):
        return self.server_time

    def get_local_update_time(self):
        return self.local_update_time

    def get_inst_id(self):
        """Instrument ID."""
        if not self.has_been_init_data:
            self.init_data()
        return self.inst_id

    def get_inst_type(self):
        """Instrument type."""
        if not self.has_been_init_data:
            self.init_data()
        return self.inst_type

    def get_trade_id(self):
        """Trade ID of the liquidation order."""
        if not self.has_been_init_data:
            self.init_data()
        return self.trade_id

    def get_price(self):
        """Liquidation price."""
        if not self.has_been_init_data:
            self.init_data()
        return self.price

    def get_size(self):
        """Position size liquidated."""
        if not self.has_been_init_data:
            self.init_data()
        return self.size

    def get_side(self):
        """Order side (buy/sell)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.side

    def get_pos_side(self):
        """Position side that was liquidated (long/short)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.pos_side

    def get_bankruptcy_price(self):
        """Bankruptcy price."""
        if not self.has_been_init_data:
            self.init_data()
        return self.bankruptcy_price

    def get_all_data(self):
        if not self.has_been_init_data:
            self.init_data()
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "inst_id": self.inst_id,
                "inst_type": self.inst_type,
                "trade_id": self.trade_id,
                "price": self.price,
                "size": self.size,
                "side": self.side,
                "pos_side": self.pos_side,
                "bankruptcy_price": self.bankruptcy_price,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()
