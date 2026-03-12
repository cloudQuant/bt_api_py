"""
Crypto.com Order Data Container
"""

import json
import time

from bt_api_py.containers.orders.order import OrderData


class CryptoComOrder(OrderData):
    """Crypto.com order implementation."""

    def __init__(self, order_info, symbol_name, asset_type="SPOT", has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "CRYPTOCOM"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.client_oid = None
        self.side = None
        self.type = None
        self.quantity = None
        self.price = None
        self.status = None
        self.filled_quantity = None
        self.remaining_quantity = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize order data from raw response."""
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        assert self.order_data is not None
        self.order_id = self.order_data.get("order_id")
        self.client_oid = self.order_data.get("client_oid")
        self.side = self.order_data.get("side")
        self.type = self.order_data.get("type")
        self.quantity = float(self.order_data.get("quantity", 0))
        self.price = float(self.order_data.get("price", 0))
        self.status = self.order_data.get("status")
        self.filled_quantity = float(self.order_data.get("filled_quantity", 0))
        self.remaining_quantity = float(self.order_data.get("remaining_quantity", 0))

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all order data as dictionary."""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "client_oid": self.client_oid,
                "side": self.side,
                "type": self.type,
                "quantity": self.quantity,
                "price": self.price,
                "status": self.status,
                "filled_quantity": self.filled_quantity,
                "remaining_quantity": self.remaining_quantity,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_event(self):
        return "OrderEvent"

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return None

    def get_order_id(self):
        return self.order_id

    def get_client_order_id(self):
        return self.client_oid

    def get_cum_quote(self):
        return None

    def get_executed_qty(self):
        return self.filled_quantity

    def get_order_size(self):
        return self.quantity

    def get_order_price(self):
        return self.price

    def get_reduce_only(self):
        return None

    def get_order_side(self):
        return self.side

    def get_order_status(self):
        return self.status

    def get_order_symbol_name(self):
        return self.symbol_name

    def get_order_time_in_force(self):
        return None

    def get_order_type(self):
        return self.type

    def get_order_avg_price(self):
        if self.filled_quantity and self.filled_quantity > 0:
            return self.price
        return None

    def get_origin_order_type(self):
        return self.type

    def get_position_side(self):
        return None

    def get_trailing_stop_price(self):
        return None

    def get_trailing_stop_trigger_price(self):
        return None

    def get_trailing_stop_callback_rate(self):
        return None

    def get_trailing_stop_trigger_price_type(self):
        return None

    def get_stop_loss_price(self):
        return None

    def get_stop_loss_trigger_price(self):
        return None

    def get_stop_loss_trigger_price_type(self):
        return None

    def get_take_profit_price(self):
        return None

    def get_take_profit_trigger_price(self):
        return None

    def get_take_profit_trigger_price_type(self):
        return None

    def get_close_position(self):
        return None

    def get_order_offset(self):
        return None

    def get_order_exchange_id(self):
        return None

    def to_dict(self):
        """Convert order to dictionary format."""
        self.init_data()
        return self.get_all_data()

    @classmethod
    def from_api_response(cls, data, symbol=None):
        """Create order from API response.

        This is a convenience method for testing.
        For production use, use the standard constructor with json-encoded data.
        """
        import json

        return cls(
            order_info=json.dumps(data),
            symbol_name=symbol or data.get("instrument_name"),
            asset_type="SPOT",
            has_been_json_encoded=False,
        )
