"""
KuCoin order data container.
"""

import json
import time

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


class KuCoinOrderData(OrderData):
    """Base class for KuCoin order data."""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "KUCOIN"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.server_time = None
        self.trade_id = None
        self.client_order_id = None
        self.executed_qty = None
        self.order_id = None
        self.order_size = None
        self.order_price = None
        self.reduce_only = None
        self.order_side = None
        self.order_status = None
        self.order_symbol_name = None
        self.order_type = None
        self.order_avg_price = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        raise NotImplementedError("Subclasses must implement init_data")

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
                "order_symbol_name": self.order_symbol_name,
                "order_type": self.order_type,
                "order_status": self.order_status.value if self.order_status else None,
                "order_size": self.order_size,
                "order_price": self.order_price,
                "executed_qty": self.executed_qty,
                "order_avg_price": self.order_avg_price,
                "reduce_only": self.reduce_only,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.server_time

    def get_local_update_time(self):
        return self.local_update_time

    def get_trade_id(self):
        return self.trade_id

    def get_client_order_id(self):
        return self.client_order_id

    def get_cum_quote(self):
        return None

    def get_executed_qty(self):
        return self.executed_qty

    def get_order_id(self):
        return self.order_id

    def get_order_size(self):
        return self.order_size

    def get_order_price(self):
        return self.order_price

    def get_reduce_only(self):
        return self.reduce_only

    def get_order_side(self):
        return self.order_side

    def get_order_status(self):
        return self.order_status

    def get_trailing_stop_price(self):
        return None

    def get_trailing_stop_trigger_price(self):
        return None

    def get_trailing_stop_trigger_price_type(self):
        return None

    def get_trailing_stop_callback_rate(self):
        return None

    def get_order_symbol_name(self):
        return self.order_symbol_name

    def get_order_time_in_force(self):
        return None

    def get_order_type(self):
        return self.order_type

    def get_order_avg_price(self):
        return self.order_avg_price

    def get_origin_order_type(self):
        return None

    def get_position_side(self):
        return None

    def get_close_position(self):
        return None

    def get_take_profit_price(self):
        return None

    def get_take_profit_trigger_price(self):
        return None

    def get_take_profit_trigger_price_type(self):
        return None

    def get_stop_loss_price(self):
        return None

    def get_stop_loss_trigger_price(self):
        return None

    def get_stop_loss_trigger_price_type(self):
        return None


class KuCoinRequestOrderData(KuCoinOrderData):
    """KuCoin REST API order data.

    API response format:
    {
        "code": "200000",
        "data": {
            "id": "5bd6e9286d99522a52e458de",
            "symbol": "BTC-USDT",
            "opType": "DEAL",
            "type": "limit",
            "side": "buy",
            "price": "50000",
            "size": "0.001",
            "funds": "0",
            "dealFunds": "50",
            "dealSize": "0.001",
            "fee": "0.05",
            "feeCurrency": "USDT",
            "stp": "",
            "stop": "",
            "stopTriggered": false,
            "stopPrice": "0",
            "timeInForce": "GTC",
            "postOnly": false,
            "hidden": false,
            "iceberg": false,
            "visibleSize": "0",
            "cancelAfter": 0,
            "channel": "API",
            "clientOid": "client-order-id",
            "remark": null,
            "tags": null,
            "isActive": false,
            "cancelExist": false,
            "createdAt": 1688671955000,
            "tradeType": "TRADE"
        }
    }
    """

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.order_info, str):
                self.order_info = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data field from response
        self.order_data = self.order_info.get("data", {})

        self.server_time = from_dict_get_float(self.order_data, "createdAt")
        self.trade_id = from_dict_get_string(self.order_data, "id")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOid")
        self.order_id = from_dict_get_string(self.order_data, "id")
        self.order_size = from_dict_get_float(self.order_data, "size")
        self.order_price = from_dict_get_float(self.order_data, "price")
        self.executed_qty = from_dict_get_float(self.order_data, "dealSize")
        self.reduce_only = from_dict_get_bool(self.order_data, "reduceOnly", False)
        self.order_side = from_dict_get_string(self.order_data, "side")
        self.order_symbol_name = from_dict_get_string(self.order_data, "symbol")
        self.order_type = from_dict_get_string(self.order_data, "type")
        self.order_avg_price = from_dict_get_float(self.order_data, "avgPx")
        if self.order_avg_price is None:
            # Calculate from dealFunds if avgPx not available
            deal_funds = from_dict_get_float(self.order_data, "dealFunds")
            if deal_funds and self.executed_qty and self.executed_qty > 0:
                self.order_avg_price = deal_funds / self.executed_qty

        # Map KuCoin status to OrderStatus
        # KuCoin statuses: open, done, match, pending (for STP orders)
        is_active = from_dict_get_bool(self.order_data, "isActive")
        if is_active:
            self.order_status = OrderStatus.ACCEPTED
        else:
            # Check if partially filled
            if self.executed_qty and self.executed_qty > 0:
                if self.executed_qty < self.order_size:
                    self.order_status = OrderStatus.PARTIAL
                else:
                    self.order_status = OrderStatus.COMPLETED
            else:
                self.order_status = OrderStatus.CANCELED

        self.has_been_init_data = True
        return self


class KuCoinWssOrderData(KuCoinOrderData):
    """KuCoin WebSocket order data.

    WebSocket order message format varies based on event type.
    """

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_info = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data from WebSocket message
        data = self.order_info.get("data", {})
        self.order_data = data

        self.server_time = from_dict_get_float(data, "createdAt") or from_dict_get_float(data, "timestamp")
        self.trade_id = from_dict_get_string(data, "tradeId") or from_dict_get_string(data, "orderId")
        self.client_order_id = from_dict_get_string(data, "clientOid")
        self.order_id = from_dict_get_string(data, "orderId")
        self.order_size = from_dict_get_float(data, "size")
        self.order_price = from_dict_get_float(data, "price")
        self.executed_qty = from_dict_get_float(data, "filledSize") or from_dict_get_float(data, "dealSize")
        self.reduce_only = from_dict_get_bool(data, "reduceOnly", False)
        self.order_side = from_dict_get_string(data, "side")
        self.order_symbol_name = from_dict_get_string(data, "symbol")
        self.order_type = from_dict_get_string(data, "type")

        # Calculate average price if not provided
        if from_dict_get_float(data, "avgPrice"):
            self.order_avg_price = from_dict_get_float(data, "avgPrice")
        elif self.executed_qty and self.executed_qty > 0:
            funds = from_dict_get_float(data, "dealFunds") or 0
            self.order_avg_price = funds / self.executed_qty if funds > 0 else None

        # Map status
        status = from_dict_get_string(data, "status")
        if status == "open":
            self.order_status = OrderStatus.ACCEPTED
        elif status == "filled":
            self.order_status = OrderStatus.COMPLETED
        elif status == "canceled":
            self.order_status = OrderStatus.CANCELED
        elif status == "partial_filled":
            self.order_status = OrderStatus.PARTIAL
        else:
            self.order_status = OrderStatus.REJECTED

        self.has_been_init_data = True
        return self
