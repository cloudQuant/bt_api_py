import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HitBtcRequestTradeData(TradeData):
    """保存HitBTC成交信息"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.trade_data = trade_info if has_been_json_encoded else None
        self.trade_id = None
        self.price = None
        self.quantity = None
        self.side = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return

        if self.trade_data is None:
            return

        # 提取数据
        self.trade_id = from_dict_get_string(self.trade_data, "id")
        self.price = from_dict_get_float(self.trade_data, "price")
        self.quantity = from_dict_get_float(self.trade_data, "quantity")
        self.side = from_dict_get_string(self.trade_data, "side")
        self.timestamp = from_dict_get_float(self.trade_data, "timestamp")

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "trade_id": self.trade_id,
                "price": self.price,
                "quantity": self.quantity,
                "side": self.side,
                "timestamp": self.timestamp,
            }
        return self.all_data

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_trade_id(self):
        """Get trade ID."""
        return self.trade_id

    def get_trade_symbol_name(self):
        """Get trade symbol name."""
        return self.symbol_name

    def get_order_id(self):
        """Get order ID (not available in HitBTC trade data)."""
        return

    def get_client_order_id(self):
        """Get client order ID (not available in HitBTC trade data)."""
        return

    def get_trade_side(self):
        """Get trade side."""
        return self.side

    def get_trade_offset(self):
        """Get trade offset (not applicable for spot trading)."""
        return

    def get_trade_price(self):
        """Get trade price."""
        return self.price

    def get_trade_volume(self):
        """Get trade volume."""
        return self.quantity

    def get_trade_type(self):
        """Get trade type (not available in HitBTC trade data)."""
        return

    def get_trade_time(self):
        """Get trade time."""
        return self.timestamp

    def get_trade_fee(self):
        """Get trade fee (not available in HitBTC trade data)."""
        return

    def get_trade_fee_symbol(self):
        """Get trade fee symbol (not available in HitBTC trade data)."""
        return

    def get_trade_accumulate_volume(self):
        """Get accumulated volume (not available in HitBTC trade data)."""
        return

    def __str__(self):
        return f"HITBTC Trade {self.symbol_name}: {self.side} {self.quantity} @ {self.price}"

    def __repr__(self):
        return f"<HitBtcTradeData {self.symbol_name} {self.side} {self.quantity}>"
