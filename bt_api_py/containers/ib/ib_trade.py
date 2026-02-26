"""
IB 成交数据容器
对应 IB TWS API 的 Execution
"""
from bt_api_py.containers.trades.trade import TradeData


class IbTradeData(TradeData):
    """IB 成交数据"""

    def __init__(self, trade_info, symbol_name=None, asset_type="STK",
                 has_been_json_encoded=False):
        super().__init__(trade_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "IB"
        self._initialized = False
        self.exec_id = None
        self.order_id_val = None
        self.perm_id = None
        self.side = None             # BOT / SLD
        self.shares = None
        self.price_val = None
        self.cum_qty = None
        self.avg_price_val = None
        self.exec_time = None
        self.commission_val = None
        self.exchange_val = None

    def init_data(self):
        if self._initialized:
            return self
        info = self.trade_info
        if isinstance(info, dict):
            self.exec_id = info.get('execId', '')
            self.order_id_val = info.get('orderId')
            self.perm_id = info.get('permId')
            self.side = info.get('side', 'BOT')
            self.shares = float(info.get('shares', 0))
            self.price_val = float(info.get('price', 0))
            self.cum_qty = float(info.get('cumQty', 0))
            self.avg_price_val = float(info.get('avgPrice', 0))
            self.exec_time = info.get('time', '')
            self.commission_val = float(info.get('commission', 0))
            self.exchange_val = info.get('exchange', '')
        self._initialized = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.symbol_name

    def get_server_time(self):
        return self.exec_time

    def get_trade_id(self):
        return self.exec_id

    def get_order_id(self):
        return self.order_id_val

    def get_client_order_id(self):
        return self.perm_id

    def get_trade_side(self):
        return 'buy' if self.side == 'BOT' else 'sell'

    def get_trade_offset(self):
        return None  # IB 没有开平方向概念

    def get_trade_price(self):
        return self.price_val

    def get_trade_volume(self):
        return self.shares

    def get_trade_time(self):
        return self.exec_time

    def get_trade_fee(self):
        return self.commission_val or 0.0

    def get_trade_fee_symbol(self):
        return "USD"

    def get_all_data(self):
        return {
            "exchange_name": self.exchange_name,
            "exec_id": self.exec_id,
            "order_id": self.order_id_val,
            "side": self.side,
            "shares": self.shares,
            "price": self.price_val,
            "cum_qty": self.cum_qty,
            "avg_price": self.avg_price_val,
            "exec_time": self.exec_time,
            "commission": self.commission_val,
            "exchange": self.exchange_val,
        }
