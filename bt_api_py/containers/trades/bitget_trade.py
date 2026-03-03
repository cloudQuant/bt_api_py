"""
Bitget Trade Data Container
"""

import json
import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitgetTradeData(TradeData):
    """保存Bitget成交信息"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.trade_data = trade_info if has_been_json_encoded else None
        self.trade_id = None
        self.order_id = None
        self.symbol = None
        self.side = None
        self.order_type = None
        self.price = None
        self.size = None
        self.fee = None
        self.fee_currency = None
        self.time = None
        self.is_maker = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.trade_id = from_dict_get_string(self.trade_data, "tradeId") or from_dict_get_string(self.trade_data, "id")
        self.order_id = from_dict_get_string(self.trade_data, "orderId")
        self.symbol = from_dict_get_string(self.trade_data, "symbol")
        self.side = from_dict_get_string(self.trade_data, "side")
        self.order_type = from_dict_get_string(self.trade_data, "orderType")
        self.price = from_dict_get_float(self.trade_data, "price")
        self.size = from_dict_get_float(self.trade_data, "size")
        self.fee = from_dict_get_float(self.trade_data, "fee")
        self.fee_currency = from_dict_get_string(self.trade_data, "feeCurrency")
        self.time = from_dict_get_float(self.trade_data, "time") or from_dict_get_float(self.trade_data, "ts")
        self.is_maker = from_dict_get_string(self.trade_data, "isMaker")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "trade_id": self.trade_id,
                "order_id": self.order_id,
                "symbol": self.symbol,
                "side": self.side,
                "order_type": self.order_type,
                "price": self.price,
                "size": self.size,
                "fee": self.fee,
                "fee_currency": self.fee_currency,
                "time": self.time,
                "is_maker": self.is_maker,
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
        return self.trade_id

    def get_order_id(self):
        return self.order_id

    def get_symbol(self):
        return self.symbol

    def get_side(self):
        return self.side

    def get_order_type(self):
        return self.order_type

    def get_price(self):
        return self.price

    def get_size(self):
        return self.size

    def get_fee(self):
        return self.fee

    def get_fee_currency(self):
        return self.fee_currency

    def get_time(self):
        return self.time

    def get_is_maker(self):
        return self.is_maker


class BitgetWssTradeData(BitgetTradeData):
    """Bitget WebSocket Trade Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.trade_id = from_dict_get_string(self.trade_data, "t")
        self.order_id = from_dict_get_string(self.trade_data, "O")
        self.symbol = from_dict_get_string(self.trade_data, "s")
        self.side = from_dict_get_string(self.trade_data, "S")
        self.order_type = from_dict_get_string(self.trade_data, "ot")
        self.price = from_dict_get_float(self.trade_data, "p")
        self.size = from_dict_get_float(self.trade_data, "z")
        self.fee = from_dict_get_float(self.trade_data, "n")
        self.fee_currency = from_dict_get_string(self.trade_data, "N")
        self.time = from_dict_get_float(self.trade_data, "E")
        self.is_maker = from_dict_get_string(self.trade_data, "m")
        self.has_been_init_data = True
        return self


class BitgetRequestTradeData(BitgetTradeData):
    """Bitget REST API Trade Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.trade_id = from_dict_get_string(self.trade_data, "tradeId")
        self.order_id = from_dict_get_string(self.trade_data, "orderId")
        self.symbol = from_dict_get_string(self.trade_data, "symbol")
        self.side = from_dict_get_string(self.trade_data, "side")
        self.order_type = from_dict_get_string(self.trade_data, "orderType")
        self.price = from_dict_get_float(self.trade_data, "price")
        self.size = from_dict_get_float(self.trade_data, "size")
        self.fee = from_dict_get_float(self.trade_data, "fee")
        self.fee_currency = from_dict_get_string(self.trade_data, "feeCurrency")
        self.time = from_dict_get_float(self.trade_data, "time")
        self.is_maker = from_dict_get_string(self.trade_data, "isMaker")
        self.has_been_init_data = True
        return self