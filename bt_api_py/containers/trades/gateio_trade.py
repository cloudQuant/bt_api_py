"""
Gate.io Trade Data Container
"""

import json
import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class GateioTradeData(TradeData):
    """Gate.io trade data container"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "GATEIO"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.trade_data = trade_info if has_been_json_encoded else None
        self.trade_id = None
        self.create_time = None
        self.create_time_ms = None
        self.side = None
        self.amount = None
        self.price = None
        self.role = None
        self.order_id = None
        self.fee = None
        self.fee_currency = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize and parse trade data"""
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse trade data
        self.trade_id = from_dict_get_string(self.trade_data, "id")
        self.create_time = from_dict_get_float(self.trade_data, "create_time")
        self.create_time_ms = from_dict_get_float(self.trade_data, "create_time_ms")
        self.side = from_dict_get_string(self.trade_data, "side")
        self.amount = from_dict_get_float(self.trade_data, "amount")
        self.price = from_dict_get_float(self.trade_data, "price")
        self.role = from_dict_get_string(self.trade_data, "role")
        self.order_id = from_dict_get_string(self.trade_data, "order_id")
        self.fee = from_dict_get_float(self.trade_data, "fee")
        self.fee_currency = from_dict_get_string(self.trade_data, "fee_currency")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all trade data as dictionary"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "trade_id": self.trade_id,
                "create_time": self.create_time,
                "create_time_ms": self.create_time_ms,
                "side": self.side,
                "amount": self.amount,
                "price": self.price,
                "role": self.role,
                "order_id": self.order_id,
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

    def get_trade_id(self):
        return self.trade_id

    def get_create_time(self):
        return self.create_time

    def get_create_time_ms(self):
        return self.create_time_ms

    def get_side(self):
        return self.side

    def get_amount(self):
        return self.amount

    def get_price(self):
        return self.price

    def get_role(self):
        return self.role

    def get_order_id(self):
        return self.order_id

    def get_fee(self):
        return self.fee

    def get_fee_currency(self):
        return self.fee_currency

    def get_value(self):
        """Get trade value (amount * price)"""
        if self.amount and self.price:
            return self.amount * self.price
        return 0.0

    def is_maker(self):
        """Check if trade was maker"""
        return self.role == "maker"

    def is_taker(self):
        """Check if trade was taker"""
        return self.role == "taker"


class GateioRequestTradeData(GateioTradeData):
    """Gate.io REST API trade data"""

    def init_data(self):
        """Initialize and parse REST API trade data"""
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse REST API trade data
        self.trade_id = from_dict_get_string(self.trade_data, "id")
        self.create_time = from_dict_get_float(self.trade_data, "create_time")
        self.create_time_ms = from_dict_get_float(self.trade_data, "create_time_ms")
        self.side = from_dict_get_string(self.trade_data, "side")
        self.amount = from_dict_get_float(self.trade_data, "amount")
        self.price = from_dict_get_float(self.trade_data, "price")
        self.role = from_dict_get_string(self.trade_data, "role")
        self.order_id = from_dict_get_string(self.trade_data, "order_id")
        self.fee = from_dict_get_float(self.trade_data, "fee")
        self.fee_currency = from_dict_get_string(self.trade_data, "fee_currency")

        self.has_been_init_data = True
        return self


class GateioWssTradeData(GateioTradeData):
    """Gate.io WebSocket trade data (placeholder for future implementation)"""

    def init_data(self):
        """Initialize and parse WebSocket trade data"""
        # TODO: Implement WebSocket trade data parsing
        return self


class GateioTradeHistory:
    """Gate.io trade history container for multiple trades"""

    def __init__(self, trade_list, symbol_name, asset_type="SPOT"):
        self.exchange_name = "GATEIO"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.trades = []
        self._parse_trades(trade_list)

    def _parse_trades(self, trade_list):
        """Parse trade list"""
        for trade_info in trade_list:
            trade = GateioRequestTradeData(trade_info, self.symbol_name, self.asset_type, True)
            self.trades.append(trade)

    def get_all_trades(self):
        """Get all trades"""
        return [trade.get_all_data() for trade in self.trades]

    def get_trades_by_side(self, side):
        """Get trades by side (buy/sell)"""
        return [trade for trade in self.trades if trade.get_side() == side]

    def get_maker_trades(self):
        """Get maker trades"""
        return [trade for trade in self.trades if trade.is_maker()]

    def get_taker_trades(self):
        """Get taker trades"""
        return [trade for trade in self.trades if trade.is_taker()]

    def get_total_volume(self):
        """Get total traded volume"""
        return sum(trade.get_amount() for trade in self.trades)

    def get_average_price(self):
        """Get average trade price"""
        if not self.trades:
            return 0.0

        total_value = sum(trade.get_value() for trade in self.trades)
        total_volume = sum(trade.get_amount() for trade in self.trades)
        return total_value / total_volume if total_volume > 0 else 0.0

    def get_total_fees(self):
        """Get total fees paid"""
        return sum(trade.get_fee() or 0 for trade in self.trades)

    def get_trades_by_time_range(self, start_time, end_time):
        """Get trades within time range"""
        return [trade for trade in self.trades if start_time <= trade.get_create_time() <= end_time]

    def __str__(self):
        return json.dumps(self.get_all_trades())

    def __repr__(self):
        return self.__str__()
