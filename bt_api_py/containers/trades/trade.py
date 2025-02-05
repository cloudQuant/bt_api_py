"""交易类，用于确定交易信息的属性和方法，
# 参考接口：https://binance-docs.github.io/apidocs/futures/cn/#060a012f0b
"""


class TradeData(object):
    """交易类，用于保存成交信息"""

    def __init__(self, trade_info, has_been_json_encoded=False):
        self.event = "TradeEvent"
        self.trade_info = trade_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name = None
        self.local_update_time = None
        self.asset_type = None
        self.symbol_name = None
        self.trade_data = trade_info if has_been_json_encoded else None
        self.server_time = None
        self.trade_id = None
        self.trade_symbol_name = None
        self.order_id = None
        self.client_order_id = None
        self.trade_side = None
        self.trade_offset = None
        self.trade_price = None
        self.trade_volume = None
        self.trade_type = None
        self.trade_time = None
        self.trade_fee = None
        self.trade_fee_symbol = None
        self.trade_accumulate_volume = None
        self.all_data = None

    def get_event(self):
        """# 事件类型"""
        return self.event

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "trade_symbol_name": self.trade_symbol_name,
                "trade_side": self.trade_side,
                "trade_price": self.trade_price,
                "trade_volume": self.trade_volume,
                "trade_type": self.trade_type,
                "trade_time": self.trade_time,
                "trade_fee_symbol": self.trade_fee_symbol,
                "trade_fee": self.trade_fee,
                "client_order_id": self.client_order_id,
                "order_id": self.order_id,
                "trade_id": self.trade_id,
            }
        return self.all_data

    def get_exchange_name(self):
        """# 交易所名称"""
        raise NotImplementedError

    def get_asset_type(self):
        """# 资产类型"""
        raise NotImplementedError

    def get_symbol_name(self):
        """# symbol名称"""
        raise NotImplementedError

    def get_server_time(self):
        """# 服务器时间戳"""
        raise NotImplementedError

    def get_local_update_time(self):
        """# 本地时间戳"""
        raise NotImplementedError

    def get_trade_id(self):
        """# 交易所返回唯一成交id"""
        raise NotImplementedError

    def get_trade_symbol_name(self):
        """# 返回成交的symbol"""
        raise NotImplementedError

    def get_order_id(self):
        """# 返回下单的id"""
        raise NotImplementedError

    def get_client_order_id(self):
        """# 返回下单的客户自定义Id"""
        raise NotImplementedError

    def get_trade_side(self):
        """# 返回交易的方向"""
        raise NotImplementedError

    def get_trade_offset(self):
        """# offset用于确定是开仓还是平仓"""
        raise NotImplementedError

    def get_trade_price(self):
        """# 成交价格"""
        raise NotImplementedError

    def get_trade_volume(self):
        """# 成交量"""
        raise NotImplementedError

    def get_trade_accumulate_volume(self):
        """# 累计成交量"""
        raise NotImplementedError

    def get_trade_type(self):
        """# 成交类型，maker还是taker"""
        raise NotImplementedError

    def get_trade_time(self):
        """# 成交时间"""
        raise NotImplementedError

    def get_trade_fee(self):
        """# 成交手续费"""
        raise NotImplementedError

    def get_trade_fee_symbol(self):
        """成交手续费币种"""
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
