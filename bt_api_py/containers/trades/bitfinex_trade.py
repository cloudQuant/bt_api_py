import json
import time

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitfinexRequestTradeData(TradeData):
    """Bitfinex Request Trade Data"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.trade_data = trade_info if has_been_json_encoded else None
        self.trade_id = None
        self.price = None
        self.amount = None
        self.timestamp = None
        self.side = None
        self.fee = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.trade_data, list):
            for trade in self.trade_data:
                if isinstance(trade, dict):
                    self.trade_id = trade.get("id")
                    self.price = trade.get("price")
                    self.amount = trade.get("amount")
                    self.timestamp = trade.get("timestamp")
                    self.side = trade.get("side")
                    self.fee = trade.get("fee")
                elif isinstance(trade, list) and len(trade) >= 4:
                    # Format: [ID, MTS, AMOUNT, PRICE]
                    self.trade_id = trade[0]
                    self.timestamp = trade[1]
                    self.amount = trade[2]
                    self.price = trade[3]
                    # Determine side based on amount sign
                    self.side = "BUY" if self.amount > 0 else "SELL"

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
                "price": self.price,
                "amount": abs(self.amount),
                "timestamp": self.timestamp,
                "side": self.side,
                "fee": self.fee,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_trade_id(self):
        return self.trade_id

    def get_price(self):
        return self.price

    def get_amount(self):
        return abs(self.amount)

    def get_timestamp(self):
        return self.timestamp

    def get_side(self):
        return self.side

    def get_fee(self):
        return self.fee

    def get_value(self):
        """Get trade value"""
        return self.price * abs(self.amount) if self.price is not None and self.amount is not None else 0

    def is_buy(self):
        """Check if trade is a buy"""
        return self.side == "BUY"

    def is_sell(self):
        """Check if trade is a sell"""
        return self.side == "SELL"


class BitfinexSpotWssTradeData(BitfinexRequestTradeData):
    """Bitfinex Spot WebSocket Trade Data"""
    pass  # Same structure as request data