"""
Bitget Order Data Container
"""

import json
import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitgetOrderData(OrderData):
    """保存Bitget订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_order_id = None
        self.symbol = None
        self.side = None
        self.order_type = None
        self.status = None
        self.size = None
        self.filled_size = None
        self.remaining_size = None
        self.price = None
        self.avg_price = None
        self.create_time = None
        self.update_time = None
        self.done_at = None
        self.fee = None
        self.fee_currency = None
        self.stop_price = None
        self.trigger_price = None
        self.time_in_force = None
        self.post_only = None
        self.hidden = None
        self.reduce_only = None
        self.iceberg = None
        self.iceberg_size = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.order_id = from_dict_get_string(self.order_data, "orderId") or from_dict_get_string(
            self.order_data, "id"
        )
        self.client_order_id = from_dict_get_string(self.order_data, "clientOid")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "orderType")
        self.status = from_dict_get_string(self.order_data, "status")
        self.size = from_dict_get_float(self.order_data, "size")
        self.filled_size = from_dict_get_float(self.order_data, "filledSize")
        self.remaining_size = from_dict_get_float(self.order_data, "remainingSize")
        self.price = from_dict_get_float(self.order_data, "price")
        self.avg_price = from_dict_get_float(self.order_data, "avgPrice") or from_dict_get_float(
            self.order_data, "avgFillPrice"
        )
        self.create_time = from_dict_get_float(self.order_data, "cTime") or from_dict_get_float(
            self.order_data, "createTime"
        )
        self.update_time = from_dict_get_float(self.order_data, "uTime") or from_dict_get_float(
            self.order_data, "updateTime"
        )
        self.done_at = from_dict_get_float(self.order_data, "doneAt")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "feeCurrency")
        self.stop_price = from_dict_get_float(self.order_data, "stopPrice")
        self.trigger_price = from_dict_get_float(self.order_data, "triggerPrice")
        self.time_in_force = from_dict_get_string(self.order_data, "tif")
        self.post_only = from_dict_get_string(self.order_data, "postOnly")
        self.hidden = from_dict_get_string(self.order_data, "hidden")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly")
        self.iceberg = from_dict_get_string(self.order_data, "iceberg")
        self.iceberg_size = from_dict_get_float(self.order_data, "icebergSize")
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
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
                "symbol": self.symbol,
                "side": self.side,
                "order_type": self.order_type,
                "status": self.status,
                "size": self.size,
                "filled_size": self.filled_size,
                "remaining_size": self.remaining_size,
                "price": self.price,
                "avg_price": self.avg_price,
                "create_time": self.create_time,
                "update_time": self.update_time,
                "done_at": self.done_at,
                "fee": self.fee,
                "fee_currency": self.fee_currency,
                "stop_price": self.stop_price,
                "trigger_price": self.trigger_price,
                "time_in_force": self.time_in_force,
                "post_only": self.post_only,
                "hidden": self.hidden,
                "reduce_only": self.reduce_only,
                "iceberg": self.iceberg,
                "iceberg_size": self.iceberg_size,
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

    def get_side(self):
        return self.side

    def get_order_type(self):
        return self.order_type

    def get_status(self):
        return self.status

    def get_size(self):
        return self.size

    def get_filled_size(self):
        return self.filled_size

    def get_remaining_size(self):
        return self.remaining_size

    def get_price(self):
        return self.price

    def get_avg_price(self):
        return self.avg_price

    def get_create_time(self):
        return self.create_time

    def get_update_time(self):
        return self.update_time

    def get_done_at(self):
        return self.done_at

    def get_fee(self):
        return self.fee

    def get_fee_currency(self):
        return self.fee_currency

    def get_stop_price(self):
        return self.stop_price

    def get_trigger_price(self):
        return self.trigger_price

    def get_time_in_force(self):
        return self.time_in_force

    def get_post_only(self):
        return self.post_only

    def get_hidden(self):
        return self.hidden

    def get_reduce_only(self):
        return self.reduce_only

    def get_iceberg(self):
        return self.iceberg

    def get_iceberg_size(self):
        return self.iceberg_size


class BitgetWssOrderData(BitgetOrderData):
    """Bitget WebSocket Order Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.order_id = from_dict_get_string(self.order_data, "o")
        self.client_order_id = from_dict_get_string(self.order_data, "C")
        self.symbol = from_dict_get_string(self.order_data, "s")
        self.side = from_dict_get_string(self.order_data, "S")
        self.order_type = from_dict_get_string(self.order_data, "ot")
        self.status = from_dict_get_string(self.order_data, "X")
        self.size = from_dict_get_float(self.order_data, "z")
        self.filled_size = from_dict_get_float(self.order_data, "L")
        self.remaining_size = from_dict_get_float(self.order_data, "r")
        self.price = from_dict_get_float(self.order_data, "p")
        self.avg_price = from_dict_get_float(self.order_data, "P")
        self.create_time = from_dict_get_float(self.order_data, "O")
        self.update_time = from_dict_get_float(self.order_data, "E")
        self.done_at = from_dict_get_float(self.order_data, "T")
        self.fee = from_dict_get_float(self.order_data, "n")
        self.fee_currency = from_dict_get_string(self.order_data, "N")
        self.stop_price = from_dict_get_float(self.order_data, "SP")
        self.trigger_price = from_dict_get_float(self.order_data, "trdPx")
        self.time_in_force = from_dict_get_string(self.order_data, "tif")
        self.post_only = from_dict_get_string(self.order_data, "postOnly")
        self.hidden = from_dict_get_string(self.order_data, "h")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly")
        self.iceberg = from_dict_get_string(self.order_data, "ice")
        self.iceberg_size = from_dict_get_float(self.order_data, "iSz")
        self.has_been_init_data = True
        return self


class BitgetRequestOrderData(BitgetOrderData):
    """Bitget REST API Order Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.order_id = from_dict_get_string(self.order_data, "orderId")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOid")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "orderType")
        self.status = from_dict_get_string(self.order_data, "status")
        self.size = from_dict_get_float(self.order_data, "size")
        self.filled_size = from_dict_get_float(self.order_data, "filledSize")
        self.remaining_size = from_dict_get_float(self.order_data, "remainingSize")
        self.price = from_dict_get_float(self.order_data, "price")
        self.avg_price = from_dict_get_float(self.order_data, "avgPrice")
        self.create_time = from_dict_get_float(self.order_data, "cTime")
        self.update_time = from_dict_get_float(self.order_data, "uTime")
        self.done_at = from_dict_get_float(self.order_data, "doneAt")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "feeCurrency")
        self.stop_price = from_dict_get_float(self.order_data, "stopPrice")
        self.trigger_price = from_dict_get_float(self.order_data, "triggerPrice")
        self.time_in_force = from_dict_get_string(self.order_data, "tif")
        self.post_only = from_dict_get_string(self.order_data, "postOnly")
        self.hidden = from_dict_get_string(self.order_data, "hidden")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly")
        self.iceberg = from_dict_get_string(self.order_data, "iceberg")
        self.iceberg_size = from_dict_get_float(self.order_data, "icebergSize")
        self.has_been_init_data = True
        return self
