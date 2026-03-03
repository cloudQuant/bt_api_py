"""
Coinbase Trade Data Container
"""

import json
import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class CoinbaseTradeData(TradeData):
    """保存Coinbase成交信息"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        # If already JSON encoded, parse it to dict; otherwise store raw
        if has_been_json_encoded:
            self.trade_data = json.loads(trade_info) if isinstance(trade_info, str) else trade_info
        else:
            self.trade_data = None
        self.trade_id = None
        self.order_id = None
        self.product_id = None
        self.trade_type = None
        self.side = None
        self.price = None
        self.size = None
        self.commission = None
        self.trade_time = None
        self.liquidity_indicator = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        # Ensure trade_data is a dict
        if isinstance(self.trade_data, str):
            self.trade_data = json.loads(self.trade_data)
        if self.has_been_init_data:
            return self
        try:
            # Parse trade data
            if isinstance(self.trade_data, dict):
                self.trade_id = from_dict_get_string(self.trade_data, "entry_id")
                self.order_id = from_dict_get_string(self.trade_data, "order_id")
                self.product_id = from_dict_get_string(self.trade_data, "product_id")
                self.trade_type = from_dict_get_string(self.trade_data, "trade_type")
                self.side = from_dict_get_string(self.trade_data, "side")

                self.price = from_dict_get_float(self.trade_data, "price")
                self.size = from_dict_get_float(self.trade_data, "size")
                self.commission = from_dict_get_float(self.trade_data, "commission")

                self.trade_time = from_dict_get_string(self.trade_data, "trade_time")
                self.liquidity_indicator = from_dict_get_string(self.trade_data, "liquidity_indicator")
        except Exception as e:
            print(f"Error parsing trade data: {e}")
            self.trade_data = {}
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
                "trade_id": self.trade_id,
                "order_id": self.order_id,
                "product_id": self.product_id,
                "trade_type": self.trade_type,
                "side": self.side,
                "price": self.price,
                "size": self.size,
                "commission": self.commission,
                "trade_time": self.trade_time,
                "liquidity_indicator": self.liquidity_indicator,
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
        self.init_data()
        return self.trade_id

    def get_order_id(self):
        self.init_data()
        return self.order_id

    def get_product_id(self):
        self.init_data()
        return self.product_id

    def get_trade_type(self):
        self.init_data()
        return self.trade_type

    def get_side(self):
        self.init_data()
        return self.side

    def get_price(self):
        self.init_data()
        return self.price

    def get_size(self):
        self.init_data()
        return self.size

    def get_commission(self):
        self.init_data()
        return self.commission

    def get_trade_time(self):
        self.init_data()
        return self.trade_time

    def get_liquidity_indicator(self):
        self.init_data()
        return self.liquidity_indicator


class CoinbaseWssTradeData(CoinbaseTradeData):
    """保存WebSocket成交信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        # Ensure trade_data is a dict
        if isinstance(self.trade_data, str):
            self.trade_data = json.loads(self.trade_data)
        if self.has_been_init_data:
            return self
        try:
            # WebSocket trade data
            if isinstance(self.trade_data, dict):
                self.trade_id = from_dict_get_string(self.trade_data, "trade_id")
                self.order_id = from_dict_get_string(self.trade_data, "order_id")
                self.product_id = from_dict_get_string(self.trade_data, "product_id")
                self.side = from_dict_get_string(self.trade_data, "side")

                self.price = from_dict_get_float(self.trade_data, "price")
                self.size = from_dict_get_float(self.trade_data, "size")
                self.commission = from_dict_get_float(self.trade_data, "commission")

                self.trade_time = from_dict_get_string(self.trade_data, "time")
                self.liquidity_indicator = from_dict_get_string(self.trade_data, "liquidity_indicator")
        except Exception as e:
            print(f"Error parsing WebSocket trade data: {e}")
            self.trade_data = {}
        self.has_been_init_data = True
        return self


class CoinbaseRequestTradeData(CoinbaseTradeData):
    """保存REST API成交信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        # Ensure trade_data is a dict
        if isinstance(self.trade_data, str):
            self.trade_data = json.loads(self.trade_data)
        if self.has_been_init_data:
            return self
        try:
            # REST API trade data (from /brokerage/orders/historical/fills)
            if isinstance(self.trade_data, dict):
                self.trade_id = from_dict_get_string(self.trade_data, "entry_id")
                self.order_id = from_dict_get_string(self.trade_data, "order_id")
                self.product_id = from_dict_get_string(self.trade_data, "product_id")
                self.trade_type = from_dict_get_string(self.trade_data, "trade_type")
                self.side = from_dict_get_string(self.trade_data, "side")

                self.price = from_dict_get_float(self.trade_data, "price")
                self.size = from_dict_get_float(self.trade_data, "size")
                self.commission = from_dict_get_float(self.trade_data, "commission")

                self.trade_time = from_dict_get_string(self.trade_data, "trade_time")
                self.liquidity_indicator = from_dict_get_string(self.trade_data, "liquidity_indicator")
        except Exception as e:
            print(f"Error parsing REST trade data: {e}")
            self.trade_data = {}
        self.has_been_init_data = True
        return self
