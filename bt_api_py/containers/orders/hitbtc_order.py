import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HitBtcRequestOrderData(OrderData):
    """保存HitBTC订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        # Always store order_info for init_data() to parse
        self.order_data = order_info
        self.client_order_id = None
        self.order_id = None
        self.symbol = None
        self.side = None
        self.type = None
        self.status = None
        self.quantity = None
        self.quantity_filled = None
        self.price = None
        self.price_filled = None
        self.time_in_force = None
        self.timestamp = None
        self.updated_at = None
        self.fee = None
        self.fee_currency = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return

        if self.order_data is None:
            return

        # 提取数据
        self.client_order_id = from_dict_get_string(self.order_data, "client_order_id")
        self.order_id = from_dict_get_string(self.order_data, "id")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.side = from_dict_get_string(self.order_data, "side")
        self.type = from_dict_get_string(self.order_data, "type")
        self.status = from_dict_get_string(self.order_data, "status")
        self.quantity = from_dict_get_float(self.order_data, "quantity")
        self.quantity_filled = from_dict_get_float(self.order_data, "quantity_filled")
        self.price = from_dict_get_float(self.order_data, "price")
        self.price_filled = from_dict_get_float(self.order_data, "price_filled")
        self.time_in_force = from_dict_get_string(self.order_data, "time_in_force")
        self.timestamp = from_dict_get_float(self.order_data, "created_at")
        self.updated_at = from_dict_get_float(self.order_data, "updated_at")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "fee_currency")

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "client_order_id": self.client_order_id,
                "order_id": self.order_id,
                "symbol": self.symbol,
                "side": self.side,
                "type": self.type,
                "status": self.status,
                "quantity": self.quantity,
                "quantity_filled": self.quantity_filled,
                "price": self.price,
                "price_filled": self.price_filled,
                "time_in_force": self.time_in_force,
                "timestamp": self.timestamp,
                "updated_at": self.updated_at,
                "fee": self.fee,
                "fee_currency": self.fee_currency,
            }
        return self.all_data

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_trade_id(self):
        """Get trade ID (uses order_id for HitBTC)."""
        return self.order_id

    def get_client_order_id(self):
        """Get client order ID."""
        return self.client_order_id

    def get_cum_quote(self):
        """Get cumulative quote volume."""
        if self.quantity_filled and self.price_filled:
            return self.quantity_filled * self.price_filled
        return None

    def get_order_id(self):
        """Get order ID."""
        return self.order_id

    def get_price(self):
        """Get order price."""
        return self.price

    def get_quantity(self):
        """Get order quantity."""
        return self.quantity

    def get_side(self):
        """Get order side."""
        return self.side

    def get_order_type(self):
        """Get order type."""
        return self.type

    def get_order_status(self):
        """Get order status."""
        return self.status

    def get_avg_price(self):
        """Get average filled price."""
        if self.quantity_filled and self.quantity_filled > 0:
            if hasattr(self, "price_filled") and self.price_filled:
                return self.price_filled
        return None

    def get_filled_ratio(self):
        """获取成交比例"""
        if self.quantity and self.quantity_filled:
            return self.quantity_filled / self.quantity
        return 0.0

    def is_active(self):
        """判断订单是否活跃"""
        return self.status in ["new", "partiallyFilled"]

    def is_filled(self):
        """判断订单是否已成交"""
        return self.status == "filled"

    def is_cancelled(self):
        """判断订单是否已取消"""
        return self.status == "canceled"

    def __str__(self):
        return f"HITBTC Order {self.symbol_name}: {self.side} {self.quantity} @ {self.price} [{self.status}]"

    def __repr__(self):
        return f"<HitBtcOrderData {self.symbol_name} {self.side} {self.type}>"
