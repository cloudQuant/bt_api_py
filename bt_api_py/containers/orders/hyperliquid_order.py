import json
import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HyperliquidRequestOrderData(OrderData):
    """保存Hyperliquid订单请求数据"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "HYPERLIQUID"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_order_id = None
        self.status = None
        self.side = None
        self.type = None
        self.quantity = None
        self.price = None
        self.filled_quantity = None
        self.remaining_quantity = None
        self.cost = None
        self.fee = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化订单数据"""
        if self.has_been_init_data:
            return

        try:
            if not self.has_been_json_encoded:
                self.order_data = json.loads(self.order_info)

            # Process order response from Hyperliquid
            if isinstance(self.order_data, dict):
                if "statuses" in self.order_data and self.order_data["statuses"]:
                    # Response after order placement
                    status = self.order_data["statuses"][0]
                    if "resting" in status:
                        self.order_id = status["resting"].get("oid")
                        self.status = "NEW"
                        self.side = from_dict_get_string(status["resting"], "side")
                        self.type = from_dict_get_string(status["resting"], "type")
                        self.quantity = from_dict_get_float(status["resting"], "sz")
                        self.price = from_dict_get_float(status["resting"], "limit_px")
                        self.timestamp = time.time()

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error initializing Hyperliquid order data: {e}")

    def get_all_data(self):
        """获取所有订单数据"""
        if self.all_data is None:
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
                "quantity": self.quantity,
                "price": self.price,
                "filled_quantity": self.filled_quantity,
                "remaining_quantity": self.remaining_quantity,
                "cost": self.cost,
                "fee": self.fee,
                "timestamp": self.timestamp,
            }
        return self.all_data

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

    def get_status(self):
        return self.status

    def get_side(self):
        return self.side

    def get_type(self):
        return self.type

    def get_quantity(self):
        return self.quantity

    def get_price(self):
        return self.price

    def get_filled_quantity(self):
        return self.filled_quantity

    def get_remaining_quantity(self):
        return self.remaining_quantity

    def get_cost(self):
        return self.cost

    def get_fee(self):
        return self.fee

    def get_timestamp(self):
        return self.timestamp

    def __str__(self):
        return f"HyperliquidRequestOrderData(order_id={self.order_id}, status={self.status}, side={self.side}, quantity={self.quantity})"


class HyperliquidSpotWssOrderData(OrderData):
    """保存Hyperliquid WebSocket订单数据"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "HYPERLIQUID"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_order_id = None
        self.status = None
        self.side = None
        self.type = None
        self.quantity = None
        self.price = None
        self.filled_quantity = None
        self.remaining_quantity = None
        self.cost = None
        self.fee = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化WebSocket订单数据"""
        if self.has_been_init_data:
            return

        try:
            if not self.has_been_json_encoded:
                self.order_data = json.loads(self.order_info)

            # Process WebSocket order update
            if isinstance(self.order_data, dict):
                self.order_id = from_dict_get_string(self.order_data, "oid")
                self.status = from_dict_get_string(self.order_data, "status")
                self.side = from_dict_get_string(self.order_data, "side")
                self.type = from_dict_get_string(self.order_data, "type")
                self.quantity = from_dict_get_float(self.order_data, "sz")
                self.price = from_dict_get_float(self.order_data, "limit_px")
                self.filled_quantity = from_dict_get_float(self.order_data, "filledSz")
                self.remaining_quantity = from_dict_get_float(self.order_data, "remainingSz")
                self.timestamp = from_dict_get_float(self.order_data, "time")

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error initializing Hyperliquid WebSocket order data: {e}")

    def get_all_data(self):
        """获取所有订单数据"""
        if self.all_data is None:
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
                "quantity": self.quantity,
                "price": self.price,
                "filled_quantity": self.filled_quantity,
                "remaining_quantity": self.remaining_quantity,
                "cost": self.cost,
                "fee": self.fee,
                "timestamp": self.timestamp,
            }
        return self.all_data

    # Getters (same as request order data)
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

    def get_status(self):
        return self.status

    def get_side(self):
        return self.side

    def get_type(self):
        return self.type

    def get_quantity(self):
        return self.quantity

    def get_price(self):
        return self.price

    def get_filled_quantity(self):
        return self.filled_quantity

    def get_remaining_quantity(self):
        return self.remaining_quantity

    def get_cost(self):
        return self.cost

    def get_fee(self):
        return self.fee

    def get_timestamp(self):
        return self.timestamp

    def __str__(self):
        return f"HyperliquidSpotWssOrderData(order_id={self.order_id}, status={self.status}, side={self.side})"