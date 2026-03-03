"""HTX Trade Data Container"""

import json
import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HtxRequestTradeData(TradeData):
    """HTX REST API trade data."""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.trade_data = trade_info if has_been_json_encoded else None
        self.trade_id = None
        self.order_id = None
        self.trade_symbol_name = None
        self.trade_side = None
        self.trade_price = None
        self.trade_qty = None
        self.trade_fee = None
        self.trade_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize trade data from HTX response.

        HTX trade response format (from match results):
        {
            "id": 987654321,
            "order-id": 123456789,
            "match-id": 111222333,
            "symbol": "btcusdt",
            "type": "buy-limit",
            "source": "spot-api",
            "price": "50000",
            "filled-amount": "0.001",
            "filled-fees": "0.00001",
            "created-at": 1688671955000
        }
        """
        if self.has_been_init_data:
            return

        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)

        # Trade ID
        self.trade_id = str(from_dict_get_float(self.trade_data, "id"))

        # Order ID
        self.order_id = str(from_dict_get_float(self.trade_data, "order-id"))

        # Symbol
        self.trade_symbol_name = from_dict_get_string(self.trade_data, "symbol")

        # Parse trade side from order type
        trade_type = from_dict_get_string(self.trade_data, "type")
        if trade_type:
            parts = trade_type.split("-")
            if len(parts) >= 1:
                side_str = parts[0]
                self.trade_side = side_str.upper()

        # Price and quantity
        self.trade_price = from_dict_get_float(self.trade_data, "price")
        self.trade_qty = from_dict_get_float(self.trade_data, "filled-amount")

        # Fee
        self.trade_fee = from_dict_get_float(self.trade_data, "filled-fees")

        # Timestamp
        self.trade_time = from_dict_get_float(self.trade_data, "created-at")

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "trade_id": self.trade_id,
                "order_id": self.order_id,
                "trade_symbol_name": self.trade_symbol_name,
                "trade_side": self.trade_side,
                "trade_price": self.trade_price,
                "trade_qty": self.trade_qty,
                "trade_fee": self.trade_fee,
                "trade_time": self.trade_time,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_trade_id(self):
        self.init_data()
        return self.trade_id

    def get_order_id(self):
        self.init_data()
        return self.order_id

    def get_trade_symbol_name(self):
        self.init_data()
        return self.trade_symbol_name

    def get_trade_side(self):
        self.init_data()
        return self.trade_side

    def get_trade_price(self):
        self.init_data()
        return self.trade_price

    def get_trade_qty(self):
        self.init_data()
        return self.trade_qty

    def get_trade_fee(self):
        self.init_data()
        return self.trade_fee

    def get_trade_time(self):
        self.init_data()
        return self.trade_time
