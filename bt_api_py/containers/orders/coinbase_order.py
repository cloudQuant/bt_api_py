"""
Coinbase Order Data Container
"""

from __future__ import annotations

import json
import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string
from bt_api_py.logging_factory import get_logger

logger = get_logger("container")


class CoinbaseOrderData(OrderData):
    """保存Coinbase订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        # If already JSON encoded, parse it to dict; otherwise store raw
        if has_been_json_encoded:
            self.order_data = json.loads(order_info) if isinstance(order_info, str) else order_info
        else:
            self.order_data = None
        self.order_id = None
        self.client_order_id = None
        self.product_id = None
        self.side = None
        self.order_type = None
        self.status = None
        self.price = None
        self.size = None
        self.filled_size = None
        self.remaining_size = None
        self.funds = None
        self.filled_funds = None
        self.remaining_funds = None
        self.settled = None
        self.created_time = None
        self.done_time = None
        self.done_reason = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        # Ensure order_data is a dict
        if isinstance(self.order_data, str):
            self.order_data = json.loads(self.order_data)
        if self.has_been_init_data:
            return self
        try:
            # Parse order data
            if isinstance(self.order_data, dict):
                self.order_id = from_dict_get_string(self.order_data, "order_id")
                self.client_order_id = from_dict_get_string(self.order_data, "client_order_id")
                self.product_id = from_dict_get_string(self.order_data, "product_id")
                self.side = from_dict_get_string(self.order_data, "side")
                self.status = from_dict_get_string(self.order_data, "status")

                # Price and size
                self.price = from_dict_get_float(self.order_data, "price")
                self.size = from_dict_get_float(self.order_data, "size")
                self.filled_size = from_dict_get_float(self.order_data, "filled_size")
                self.remaining_size = from_dict_get_float(self.order_data, "remaining_size")

                # Funds
                self.funds = from_dict_get_float(self.order_data, "funds")
                self.filled_funds = from_dict_get_float(self.order_data, "filled_funds")
                self.remaining_funds = from_dict_get_float(self.order_data, "remaining_funds")

                # Time
                self.created_time = from_dict_get_string(self.order_data, "created_time")
                self.done_time = from_dict_get_string(self.order_data, "done_time")
                self.done_reason = from_dict_get_string(self.order_data, "done_reason")

                # Settled
                self.settled = from_dict_get_string(self.order_data, "settled")

                # Calculate order type from size/funds
                if self.size:
                    self.order_type = "limit" if self.price else "market"
        except Exception as e:
            logger.error(f"Error parsing order data: {e}", exc_info=True)

            self.order_data = {}
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
                "product_id": self.product_id,
                "side": self.side,
                "order_type": self.order_type,
                "status": self.status,
                "price": self.price,
                "size": self.size,
                "filled_size": self.filled_size,
                "remaining_size": self.remaining_size,
                "funds": self.funds,
                "filled_funds": self.filled_funds,
                "remaining_funds": self.remaining_funds,
                "settled": self.settled,
                "created_time": self.created_time,
                "done_time": self.done_time,
                "done_reason": self.done_reason,
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

    def get_product_id(self):
        self.init_data()
        return self.product_id

    def get_side(self):
        self.init_data()
        return self.side

    def get_order_type(self):
        self.init_data()
        return self.order_type

    def get_status(self):
        self.init_data()
        return self.status

    def get_price(self):
        self.init_data()
        return self.price

    def get_size(self):
        self.init_data()
        return self.size

    def get_filled_size(self):
        self.init_data()
        return self.filled_size

    def get_remaining_size(self):
        self.init_data()
        return self.remaining_size

    def get_funds(self):
        self.init_data()
        return self.funds

    def get_filled_funds(self):
        self.init_data()
        return self.filled_funds

    def get_remaining_funds(self):
        self.init_data()
        return self.remaining_funds

    def get_settled(self):
        self.init_data()
        return self.settled

    def get_created_time(self):
        self.init_data()
        return self.created_time

    def get_done_time(self):
        self.init_data()
        return self.done_time

    def get_done_reason(self):
        self.init_data()
        return self.done_reason


class CoinbaseWssOrderData(CoinbaseOrderData):
    """保存WebSocket订单信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        # Ensure order_data is a dict
        if isinstance(self.order_data, str):
            self.order_data = json.loads(self.order_data)
        if self.has_been_init_data:
            return self
        try:
            # WebSocket order data (executionReport format)
            if isinstance(self.order_data, dict):
                self.order_id = from_dict_get_string(self.order_data, "order_id")
                self.client_order_id = from_dict_get_string(self.order_data, "client_order_id")
                self.product_id = from_dict_get_string(self.order_data, "product_id")
                self.side = from_dict_get_string(self.order_data, "side")
                self.status = from_dict_get_string(self.order_data, "status")

                # Price and size
                self.price = from_dict_get_float(self.order_data, "price")
                self.size = from_dict_get_float(self.order_data, "size")
                self.filled_size = from_dict_get_float(self.order_data, "filled_size")
                self.remaining_size = from_dict_get_float(self.order_data, "remaining_size")

                # Time
                self.created_time = from_dict_get_string(self.order_data, "created_time")
                self.done_time = from_dict_get_string(self.order_data, "done_time")

                # Determine order type from order_type field
                self.order_type = from_dict_get_string(self.order_data, "type")

                # Settled
                self.settled = from_dict_get_string(self.order_data, "settled")
        except Exception as e:
            logger.error(f"Error parsing WebSocket order data: {e}", exc_info=True)

            self.order_data = {}
        self.has_been_init_data = True
        return self


class CoinbaseRequestOrderData(CoinbaseOrderData):
    """保存REST API订单信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        # Ensure order_data is a dict
        if isinstance(self.order_data, str):
            self.order_data = json.loads(self.order_data)
        if self.has_been_init_data:
            return self
        try:
            # REST API order data
            if isinstance(self.order_data, dict):
                self.order_id = from_dict_get_string(self.order_data, "order_id")
                self.client_order_id = from_dict_get_string(self.order_data, "client_order_id")
                self.product_id = from_dict_get_string(self.order_data, "product_id")
                self.side = from_dict_get_string(self.order_data, "side")
                self.status = from_dict_get_string(self.order_data, "status")

                # Price and size
                self.price = from_dict_get_float(self.order_data, "price")
                self.size = from_dict_get_float(self.order_data, "size")
                self.filled_size = from_dict_get_float(self.order_data, "filled_size")
                self.remaining_size = from_dict_get_float(self.order_data, "remaining_size")

                # Funds
                self.funds = from_dict_get_float(self.order_data, "funds")
                self.filled_funds = from_dict_get_float(self.order_data, "filled_funds")
                self.remaining_funds = from_dict_get_float(self.order_data, "remaining_funds")

                # Time
                self.created_time = from_dict_get_string(self.order_data, "created_time")
                self.done_time = from_dict_get_string(self.order_data, "done_time")
                self.done_reason = from_dict_get_string(self.order_data, "done_reason")

                # Settled
                self.settled = from_dict_get_string(self.order_data, "settled")

                # Determine order type - support both "type" field and calculation
                self.order_type = from_dict_get_string(self.order_data, "type")
                if not self.order_type:
                    if self.price and self.size:
                        self.order_type = "limit"
                    elif self.funds:
                        self.order_type = "market"
        except Exception as e:
            logger.error(f"Error parsing REST order data: {e}", exc_info=True)

            self.order_data = {}
        self.has_been_init_data = True
        return self
