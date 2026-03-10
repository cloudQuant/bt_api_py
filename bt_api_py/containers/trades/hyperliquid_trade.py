import json
import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string
from bt_api_py.logging_factory import get_logger

logger = get_logger("container")


class HyperliquidSpotWssTradeData(TradeData):
    """保存Hyperliquid现货WebSocket成交数据"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "HYPERLIQUID"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.trade_data = trade_info if has_been_json_encoded else None
        self.trade_id = None
        self.order_id = None
        self.side = None
        self.price = None
        self.quantity = None
        self.timestamp = None
        self.fee = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化成交数据"""
        if self.has_been_init_data:
            return self

        try:
            if not self.has_been_json_encoded:
                self.trade_data = json.loads(self.trade_info)

            # Process trade data
            if isinstance(self.trade_data, dict):
                self.trade_id = from_dict_get_string(self.trade_data, "tid")
                self.order_id = from_dict_get_string(self.trade_data, "orderOid")
                self.side = from_dict_get_string(self.trade_data, "side")
                self.price = from_dict_get_float(self.trade_data, "px")
                self.quantity = from_dict_get_float(self.trade_data, "sz")
                self.timestamp = from_dict_get_float(self.trade_data, "time")
                self.fee = from_dict_get_float(self.trade_data, "fee")

            self.has_been_init_data = True

        except Exception as e:
            logger.error(f"Error initializing Hyperliquid trade data: {e}", exc_info=True)

        return self

    def get_all_data(self):
        """获取所有成交数据"""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "trade_id": self.trade_id,
                "order_id": self.order_id,
                "side": self.side,
                "price": self.price,
                "quantity": self.quantity,
                "timestamp": self.timestamp,
                "fee": self.fee,
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

    def get_trade_id(self):
        return self.trade_id

    def get_order_id(self):
        return self.order_id

    def get_side(self):
        return self.side

    def get_price(self):
        return self.price

    def get_quantity(self):
        return self.quantity

    def get_timestamp(self):
        return self.timestamp

    def get_fee(self):
        return self.fee

    def __str__(self):
        return f"HyperliquidSpotWssTradeData(trade_id={self.trade_id}, side={self.side}, price={self.price}, quantity={self.quantity})"
