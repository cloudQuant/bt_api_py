"""OKX Liquidation Warning data container."""

import json
import time

from bt_api_py.containers.liquidations.liquidation import LiquidationData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxLiquidationWarningData(LiquidationData):
    """OKX liquidation warning data container.

    WebSocket channel: liquidation-warning
    Pushes when account is at risk of liquidation.

    Example data:
    {
        "arg": {"channel": "liquidation-warning", "instType": "SWAP"},
        "data": [{
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "pos": "1",
            "posCcy": "BTC",
            "liqPx": "20000",
            "markPx": "25000",
            "tz": "0"
        }]
    }
    """

    def __init__(self, liquidation_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(liquidation_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.liquidation_data = liquidation_info if has_been_json_encoded else None
        self.inst_id = None
        self.inst_type = None
        self.position = None
        # posSide may be present in some messages
        self.pos_side = None
        self.position_ccy = None
        self.liquidation_price = None
        self.mark_price = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.liquidation_data = json.loads(self.liquidation_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.inst_id = from_dict_get_string(self.liquidation_data, "instId")
        self.inst_type = from_dict_get_string(self.liquidation_data, "instType")
        self.position = from_dict_get_float(self.liquidation_data, "pos")
        self.pos_side = from_dict_get_string(self.liquidation_data, "posSide")
        self.position_ccy = from_dict_get_string(self.liquidation_data, "posCcy")
        self.liquidation_price = from_dict_get_float(self.liquidation_data, "liqPx")
        self.mark_price = from_dict_get_float(self.liquidation_data, "markPx")
        self.has_been_init_data = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.symbol_name

    def get_server_time(self):
        return self.local_update_time

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

    def get_position(self):
        """Position size."""
        if not self.has_been_init_data:
            self.init_data()
        return self.position

    def get_pos_side(self):
        """Position side (long/short/net)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.pos_side

    def get_position_ccy(self):
        """Position currency."""
        if not self.has_been_init_data:
            self.init_data()
        return self.position_ccy

    def get_liquidation_price(self):
        """Liquidation price."""
        if not self.has_been_init_data:
            self.init_data()
        return self.liquidation_price

    def get_mark_price(self):
        """Current mark price."""
        if not self.has_been_init_data:
            self.init_data()
        return self.mark_price

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
                "position": self.position,
                "pos_side": self.pos_side,
                "position_ccy": self.position_ccy,
                "liquidation_price": self.liquidation_price,
                "mark_price": self.mark_price,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()
