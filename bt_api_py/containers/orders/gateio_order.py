"""
Gate.io Order Data Container
"""

import json
import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_int


class GateioOrderData(OrderData):
    """Gate.io order data container"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "GATEIO"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.status = None
        self.side = None
        self.type = None
        self.price = None
        self.amount = None
        self.filled_amount = None
        self.remaining_amount = None
        self.create_time = None
        self.update_time = None
        self.text = None
        self.account = None
        self.time_in_force = None
        self.fee = None
        self.fee_currency = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize and parse order data"""
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse order data
        self.order_id = from_dict_get_string(self.order_data, "id")
        self.status = from_dict_get_string(self.order_data, "status")
        self.side = from_dict_get_string(self.order_data, "side")
        self.type = from_dict_get_string(self.order_data, "type")
        self.price = from_dict_get_float(self.order_data, "price")
        self.amount = from_dict_get_float(self.order_data, "amount")
        self.filled_amount = from_dict_get_float(self.order_data, "filled_total")
        self.remaining_amount = from_dict_get_float(self.order_data, "left")
        self.create_time = from_dict_get_float(self.order_data, "create_time")
        self.update_time = from_dict_get_float(self.order_data, "update_time")
        self.text = from_dict_get_string(self.order_data, "text")
        self.account = from_dict_get_string(self.order_data, "account")
        self.time_in_force = from_dict_get_string(self.order_data, "time_in_force")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "fee_currency")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all order data as dictionary"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "status": self.status,
                "side": self.side,
                "type": self.type,
                "price": self.price,
                "amount": self.amount,
                "filled_amount": self.filled_amount,
                "remaining_amount": self.remaining_amount,
                "create_time": self.create_time,
                "update_time": self.update_time,
                "text": self.text,
                "account": self.account,
                "time_in_force": self.time_in_force,
                "fee": self.fee,
                "fee_currency": self.fee_currency,
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

    def get_status(self):
        return self.status

    def get_side(self):
        return self.side

    def get_type(self):
        return self.type

    def get_price(self):
        return self.price

    def get_amount(self):
        return self.amount

    def get_filled_amount(self):
        return self.filled_amount

    def get_remaining_amount(self):
        return self.remaining_amount

    def get_create_time(self):
        return self.create_time

    def get_update_time(self):
        return self.update_time

    def get_text(self):
        return self.text

    def get_account(self):
        return self.account

    def get_time_in_force(self):
        return self.time_in_force

    def get_fee(self):
        return self.fee

    def get_fee_currency(self):
        return self.fee_currency


class GateioRequestOrderData(GateioOrderData):
    """Gate.io REST API order data"""

    def init_data(self):
        """Initialize and parse REST API order data"""
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse REST API order data
        self.order_id = from_dict_get_string(self.order_data, "id")
        self.status = from_dict_get_string(self.order_data, "status")
        self.side = from_dict_get_string(self.order_data, "side")
        self.type = from_dict_get_string(self.order_data, "type")
        self.price = from_dict_get_float(self.order_data, "price")
        self.amount = from_dict_get_float(self.order_data, "amount")
        self.filled_amount = from_dict_get_float(self.order_data, "filled_total")
        self.remaining_amount = from_dict_get_float(self.order_data, "left")
        self.create_time = from_dict_get_float(self.order_data, "create_time")
        self.update_time = from_dict_get_float(self.order_data, "update_time")
        self.text = from_dict_get_string(self.order_data, "text")
        self.account = from_dict_get_string(self.order_data, "account")
        self.time_in_force = from_dict_get_string(self.order_data, "time_in_force")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "fee_currency")

        self.has_been_init_data = True
        return self


class GateioWssOrderData(GateioOrderData):
    """Gate.io WebSocket order data (placeholder for future implementation)"""

    def init_data(self):
        """Initialize and parse WebSocket order data"""
        # TODO: Implement WebSocket order data parsing
        return self