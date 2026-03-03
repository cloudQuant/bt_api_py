import json
import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_int


class MexcOrderData(OrderData):
    """保存订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "MEXC"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # 订单的类型
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_order_id = None
        self.status = None
        self.side = None
        self.type = None
        self.time_in_force = None
        self.quantity = None
        self.executed_qty = None
        self.cummulative_quote_qty = None
        self.price = None
        self.stop_price = None
        self.iceberg_qty = None
        self.time = None
        self.update_time = None
        self.is_working = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.order_info, str):
                self.order_data = json.loads(self.order_info)
            else:
                self.order_data = self.order_info

        if self.order_data:
            self.order_id = from_dict_get_int(self.order_data, "orderId")
            self.client_order_id = from_dict_get_string(self.order_data, "clientOrderId")
            self.status = from_dict_get_string(self.order_data, "status")
            self.side = from_dict_get_string(self.order_data, "side")
            self.type = from_dict_get_string(self.order_data, "type")
            self.time_in_force = from_dict_get_string(self.order_data, "timeInForce")
            self.quantity = from_dict_get_float(self.order_data, "origQty", 0.0)
            self.executed_qty = from_dict_get_float(self.order_data, "executedQty", 0.0)
            self.cummulative_quote_qty = from_dict_get_float(self.order_data, "cummulativeQuoteQty", 0.0)
            self.price = from_dict_get_float(self.order_data, "price", 0.0)
            self.stop_price = from_dict_get_float(self.order_data, "stopPrice", 0.0)
            self.iceberg_qty = from_dict_get_float(self.order_data, "icebergQty", 0.0)
            self.time = from_dict_get_int(self.order_data, "time")
            self.update_time = from_dict_get_int(self.order_data, "updateTime")
            self.is_working = self.order_data.get("isWorking")

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
                "status": self.status,
                "side": self.side,
                "type": self.type,
                "time_in_force": self.time_in_force,
                "quantity": self.quantity,
                "executed_qty": self.executed_qty,
                "cummulative_quote_qty": self.cummulative_quote_qty,
                "price": self.price,
                "stop_price": self.stop_price,
                "iceberg_qty": self.iceberg_qty,
                "time": self.time,
                "update_time": self.update_time,
                "is_working": self.is_working,
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
        if not self.has_been_init_data:
            self.init_data()
        return self.order_id

    def get_client_order_id(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.client_order_id

    def get_status(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.status

    def get_side(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.side

    def get_type(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.type

    def get_time_in_force(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.time_in_force

    def get_quantity(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.quantity

    def get_executed_qty(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.executed_qty

    def get_cummulative_quote_qty(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.cummulative_quote_qty

    def get_price(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.price

    def get_stop_price(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.stop_price

    def get_iceberg_qty(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.iceberg_qty

    def get_time(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.time

    def get_update_time(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.update_time

    def get_is_working(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.is_working

    def is_open(self):
        return self.status in ["NEW", "PARTIALLY_FILLED"]

    def is_closed(self):
        return self.status in ["FILLED", "CANCELED", "EXPIRED", "REJECTED"]

    def is_filled(self):
        return self.status == "FILLED"

    def is_canceled(self):
        return self.status == "CANCELED"

    def get_filled_percentage(self):
        if self.quantity and self.quantity > 0:
            return (self.executed_qty / self.quantity) * 100
        return 0.0


class MexcWssOrderData(MexcOrderData):
    """保存WebSocket订单信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True

        if self.order_data and "data" in self.order_data:
            order = self.order_data["data"]
            self.order_id = from_dict_get_int(order, "i")
            self.client_order_id = from_dict_get_string(order, "c")
            self.status = from_dict_get_string(order, "s")
            self.side = from_dict_get_string(order, "S")
            self.type = from_dict_get_string(order, "o")
            self.quantity = from_dict_get_float(order, "q", 0.0)
            self.executed_qty = from_dict_get_float(order, "z", 0.0)
            self.price = from_dict_get_float(order, "p", 0.0)
            self.time = from_dict_get_int(self.order_data, "E")

        self.has_been_init_data = True
        return self


class MexcRequestOrderData(MexcOrderData):
    """保存请求返回的订单信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.order_info, str):
                self.order_data = json.loads(self.order_info)
            else:
                self.order_data = self.order_info

        if self.order_data:
            # 处理订单响应数据
            self.order_id = from_dict_get_int(self.order_data, "orderId")
            self.client_order_id = from_dict_get_string(self.order_data, "clientOrderId")
            self.status = from_dict_get_string(self.order_data, "status")
            self.side = from_dict_get_string(self.order_data, "side")
            self.type = from_dict_get_string(self.order_data, "type")
            self.time_in_force = from_dict_get_string(self.order_data, "timeInForce")
            self.quantity = from_dict_get_float(self.order_data, "origQty", 0.0)
            self.executed_qty = from_dict_get_float(self.order_data, "executedQty", 0.0)
            self.cummulative_quote_qty = from_dict_get_float(self.order_data, "cummulativeQuoteQty", 0.0)
            self.price = from_dict_get_float(self.order_data, "price", 0.0)
            self.stop_price = from_dict_get_float(self.order_data, "stopPrice", 0.0)
            self.iceberg_qty = from_dict_get_float(self.order_data, "icebergQty", 0.0)
            self.time = from_dict_get_int(self.order_data, "time")
            self.update_time = from_dict_get_int(self.order_data, "updateTime")
            self.is_working = self.order_data.get("isWorking")

        self.has_been_init_data = True
        return self
