"""
Poloniex Order Data Container
"""

import json
import time

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_int


class PoloniexOrderData(OrderData):
    """Poloniex Order Data Container"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "POLONIEX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_order_id = None
        self.symbol = None
        self.order_side = None
        self.order_type = None
        self.order_price = None
        self.order_qty = None
        self.order_filled_qty = None
        self.order_avg_price = None
        self.order_status = None
        self.order_time = None
        self.update_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
                "symbol": self.symbol,
                "order_side": self.order_side,
                "order_type": self.order_type,
                "order_price": self.order_price,
                "order_qty": self.order_qty,
                "order_filled_qty": self.order_filled_qty,
                "order_avg_price": self.order_avg_price,
                "order_status": self.order_status.value if self.order_status else None,
                "order_time": self.order_time,
                "update_time": self.update_time,
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

    def get_order_id(self):
        return self.order_id

    def get_client_order_id(self):
        return self.client_order_id

    def get_symbol(self):
        return self.symbol

    def get_order_side(self):
        return self.order_side

    def get_order_type(self):
        return self.order_type

    def get_order_price(self):
        return self.order_price

    def get_order_qty(self):
        return self.order_qty

    def get_order_filled_qty(self):
        return self.order_filled_qty

    def get_order_avg_price(self):
        return self.order_avg_price

    def get_order_status(self):
        return self.order_status

    def get_order_time(self):
        return self.order_time

    def get_update_time(self):
        return self.update_time


class PoloniexRequestOrderData(PoloniexOrderData):
    """Poloniex REST API Order Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.order_id = from_dict_get_string(self.order_data, "id")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOrderId")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.order_side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "type")
        self.order_price = from_dict_get_float(self.order_data, "price")
        self.order_qty = from_dict_get_float(self.order_data, "quantity")
        self.order_filled_qty = from_dict_get_float(self.order_data, "filledQuantity")
        self.order_avg_price = from_dict_get_float(self.order_data, "avgPrice")
        self.order_time = from_dict_get_int(self.order_data, "createTime")
        self.update_time = from_dict_get_int(self.order_data, "updateTime")

        # Map status
        status_str = from_dict_get_string(self.order_data, "state")
        status_map = {
            "NEW": OrderStatus.LIVE,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }
        self.order_status = status_map.get(status_str, OrderStatus.UNKNOWN)

        self.has_been_init_data = True
        return self


class PoloniexWssOrderData(PoloniexOrderData):
    """Poloniex WebSocket Order Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # WebSocket format may differ slightly
        self.order_id = from_dict_get_string(self.order_data, "orderId") or from_dict_get_string(self.order_data, "id")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOrderId")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.order_side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "type")
        self.order_price = from_dict_get_float(self.order_data, "price")
        self.order_qty = from_dict_get_float(self.order_data, "qty") or from_dict_get_float(self.order_data, "quantity")
        self.order_filled_qty = from_dict_get_float(self.order_data, "filledQty") or from_dict_get_float(self.order_data, "accumulatedQty")
        self.order_avg_price = from_dict_get_float(self.order_data, "avgPrice")
        self.order_time = from_dict_get_int(self.order_data, "time") or from_dict_get_int(self.order_data, "createTime")
        self.update_time = from_dict_get_int(self.order_data, "updateTime") or self.order_time

        status_str = from_dict_get_string(self.order_data, "orderStatus") or from_dict_get_string(self.order_data, "state")
        status_map = {
            "NEW": OrderStatus.LIVE,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }
        self.order_status = status_map.get(status_str, OrderStatus.UNKNOWN)

        self.has_been_init_data = True
        return self
