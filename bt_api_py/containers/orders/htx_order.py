"""HTX Order Data Container"""

import json
import time

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HtxRequestOrderData(OrderData):
    """HTX REST API order data."""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_order_id = None
        self.order_symbol_name = None
        self.order_side = None
        self.order_type = None
        self.order_price = None
        self.order_qty = None
        self.order_avg_price = None
        self.order_filled_qty = None
        self.order_status = None
        self.create_time = None
        self.update_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize order data from HTX response.

        HTX order response format:
        {
            "id": 123456789,
            "symbol": "btcusdt",
            "account-id": 123456,
            "amount": "0.001",
            "price": "50000",
            "created-at": 1688671955000,
            "type": "buy-limit",
            "field-amount": "0.001",
            "field-cash-amount": "50",
            "field-fees": "0.00001",
            "finished-at": 1688671960000,
            "source": "spot-api",
            "state": "filled",
            "canceled-at": 0
        }
        """
        if self.has_been_init_data:
            return

        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)

        # Order ID
        self.order_id = str(from_dict_get_float(self.order_data, "id"))

        # Client order ID (if provided)
        self.client_order_id = from_dict_get_string(self.order_data, "client-order-id")

        # Symbol
        self.order_symbol_name = from_dict_get_string(self.order_data, "symbol")

        # Parse order type (e.g., "buy-limit", "sell-market")
        order_type_str = from_dict_get_string(self.order_data, "type")
        if order_type_str:
            parts = order_type_str.split("-")
            if len(parts) >= 2:
                side_str = parts[0]
                type_str = parts[1]
                self.order_side = side_str.upper()
                self.order_type = type_str.upper()

        # Price and quantity
        self.order_price = from_dict_get_float(self.order_data, "price")
        self.order_qty = from_dict_get_float(self.order_data, "amount")
        self.order_avg_price = from_dict_get_float(self.order_data, "field-amount")  # field-cash-amount / field-amount
        self.order_filled_qty = from_dict_get_float(self.order_data, "field-amount")

        # Order status
        status_str = from_dict_get_string(self.order_data, "state")
        self.order_status = OrderStatus.from_value(status_str)

        # Timestamps
        self.create_time = from_dict_get_float(self.order_data, "created-at")
        finished_at = from_dict_get_float(self.order_data, "finished-at")
        self.update_time = finished_at if finished_at and finished_at > 0 else self.create_time

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
                "order_symbol_name": self.order_symbol_name,
                "order_side": self.order_side,
                "order_type": self.order_type,
                "order_price": self.order_price,
                "order_qty": self.order_qty,
                "order_avg_price": self.order_avg_price,
                "order_filled_qty": self.order_filled_qty,
                "order_status": self.order_status.value if self.order_status else None,
                "create_time": self.create_time,
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
        self.init_data()
        return self.order_id

    def get_client_order_id(self):
        self.init_data()
        return self.client_order_id

    def get_order_symbol_name(self):
        self.init_data()
        return self.order_symbol_name

    def get_order_side(self):
        self.init_data()
        return self.order_side  # String: 'BUY' or 'SELL'

    def get_order_type(self):
        self.init_data()
        return self.order_type  # String: 'LIMIT', 'MARKET', etc.

    def get_order_price(self):
        self.init_data()
        return self.order_price

    def get_order_qty(self):
        self.init_data()
        return self.order_qty

    def get_order_avg_price(self):
        self.init_data()
        return self.order_avg_price

    def get_order_filled_qty(self):
        self.init_data()
        return self.order_filled_qty

    def get_order_status(self):
        self.init_data()
        return self.order_status

    def get_create_time(self):
        self.init_data()
        return self.create_time

    def get_update_time(self):
        self.init_data()
        return self.update_time
