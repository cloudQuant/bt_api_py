import time
import json
from btpy.containers.trades.trade import TradeData
from btpy.functions.utils import from_dict_get_string, from_dict_get_float


class OkxTradeData(TradeData):
    """交易类，用于保存成交信息"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(OkxTradeData, self).__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.trade_data = trade_info if has_been_json_encoded else None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = self._get_server_time()
        self.trade_id = from_dict_get_float(self.trade_data, "tradeId")
        self.trade_symbol_name = from_dict_get_string(self.trade_data, "instId")
        self.order_id = from_dict_get_string(self.trade_data, "ordId")
        self.client_order_id = from_dict_get_string(self.trade_data, "clOrdId")
        self.trade_side = from_dict_get_string(self.trade_data, "side")
        self.trade_price = from_dict_get_float(self.trade_data, "fillPx")
        self.trade_volume = from_dict_get_float(self.trade_data, "fillSz")
        trade_type = from_dict_get_string(self.trade_data, "execType")
        self.trade_type = "maker" if trade_type == "M" else "taker"
        self.trade_time = from_dict_get_float(self.trade_data, "fillTime")
        self.trade_fee = from_dict_get_float(self.trade_data, "fee")
        self.trade_fee_symbol = from_dict_get_string(self.trade_data, "feeCcy")
        self.has_been_init_data = True
        return self

    def _get_server_time(self):
        raise NotImplementedError

    def get_exchange_name(self):
        """# 交易所名称"""
        return self.exchange_name

    def get_asset_type(self):
        """# 资产类型"""
        return self.asset_type

    def get_server_time(self):
        """# 服务器时间戳"""
        return self.server_time

    def get_local_update_time(self):
        """# 本地时间戳"""
        return self.local_update_time

    def get_trade_id(self):
        """# 交易所返回唯一成交id"""
        return self.trade_id

    def get_trade_symbol_name(self):
        """# 返回成交的symbol"""
        return self.trade_symbol_name

    def get_order_id(self):
        """# 返回下单的id"""
        return self.order_id

    def get_client_order_id(self):
        """# 返回下单的客户自定义Id"""
        return self.client_order_id

    def get_trade_side(self):
        """# 返回交易的方向"""
        return self.trade_side

    def get_trade_offset(self):
        """# offset用于确定是开仓还是平仓"""
        return None

    def get_trade_price(self):
        """# 成交价格"""
        return self.trade_price

    def get_trade_volume(self):
        """# 成交量"""
        return self.trade_volume

    def get_trade_accumulate_volume(self):
        """# 累计成交量"""
        return self.trade_accumulate_volume

    def get_trade_type(self):
        """# 成交类型，maker还是taker"""
        return self.trade_type

    def get_trade_time(self):
        """# 成交时间"""
        return self.trade_time

    def get_trade_fee(self):
        """# 成交手续费"""
        return self.trade_fee

    def get_trade_fee_symbol(self):
        """成交手续费币种"""
        return self.trade_fee_symbol

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()


class OkxRequestTradeData(OkxTradeData):
    """交易类，用于保存成交信息"""

    def _get_server_time(self):
        return from_dict_get_float(self.trade_data, "ts")


class OkxWssTradeData(OkxTradeData):
    """交易类，用于保存从order推送过来的信息"""

    def _get_server_time(self):
        return from_dict_get_float(self.trade_data, "uTime")
