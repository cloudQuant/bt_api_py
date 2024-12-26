import time
import json
from btpy.containers.trades.trade import TradeData
from btpy.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string

class BinanceAggTradeData(object):
    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        self.exchange_name = "BINANCE"
        self.event = "AggTradeUpdate"
        self.trade_info = trade_info
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.trade_data = self.trade_info if has_been_json_encoded else None
        self.has_been_init_data = False
        self.has_been_json_encoded = has_been_json_encoded
        self.server_time = None
        self.trade_id = None
        self.first_trade_id = None
        self.last_trade_id = None
        self.trade_symbol_name = None
        self.trade_price = None
        self.trade_volume = None
        self.trade_type = None
        self.trade_time = None

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.trade_data, "E")
        self.trade_id = from_dict_get_string(self.trade_data, "a")
        self.first_trade_id = from_dict_get_string(self.trade_data, "f")
        self.last_trade_id = from_dict_get_string(self.trade_data, "l")
        self.trade_symbol_name = from_dict_get_string(self.trade_data, "s")
        self.trade_price = from_dict_get_float(self.trade_data, "p")
        self.trade_volume = from_dict_get_float(self.trade_data, "q")
        is_maker = from_dict_get_bool(self.trade_data, "m")
        self.trade_type = "maker" if is_maker else "taker"
        self.trade_time = from_dict_get_float(self.trade_data, "T")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if not self.has_been_init_data:
            self.init_data()
        content = {"server_time": self.server_time,
                   "local_update_time": self.local_update_time,
                   "trade_id": self.trade_id,
                   "first_trade_id": self.first_trade_id,
                   "last_trade_id": self.last_trade_id,
                   "symbol_name": self.symbol_name,
                   "trade_symbol_name": self.trade_symbol_name,
                   "trade_price": self.trade_price,
                   "trade_volume": self.trade_volume,
                   "trade_type": self.trade_type,
                   "trade_time": self.trade_time,
                   }
        return content

    def __str__(self):
        """输出字符串"""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

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

    def get_first_trade_id(self):
        return self.first_trade_id

    def get_last_trade_id(self):
        return self.last_trade_id

    def get_trade_symbol_name(self):
        """# 返回成交的symbol"""
        return self.trade_symbol_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_trade_price(self):
        """# 成交价格"""
        return self.trade_price

    def get_trade_volume(self):
        """# 成交量"""
        return self.trade_volume

    def get_trade_type(self):
        """# 成交类型，maker还是taker"""
        return self.trade_type

    def get_trade_time(self):
        """# 成交时间"""
        return self.trade_time

class BinanceTradeData(TradeData):
    """交易类，用于保存成交信息"""

    def __init__(self, trade_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceTradeData, self).__init__(trade_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.trade_data = self.trade_info if has_been_json_encoded else None
        self.has_been_init_data = False

    def init_data(self):
        raise NotImplementedError

    def _init_wss_data(self):
        self.server_time = from_dict_get_float(self.trade_info, "E")
        self.trade_id = from_dict_get_string(self.trade_data, "t")
        self.trade_symbol_name = from_dict_get_string(self.trade_data, "s")
        self.order_id = from_dict_get_string(self.trade_data, "i")
        self.client_order_id = from_dict_get_string(self.trade_data, "c")
        self.trade_price = from_dict_get_float(self.trade_data, "L")
        self.trade_volume = from_dict_get_float(self.trade_data, "l")
        self.trade_accumulate_volume = from_dict_get_float(self.trade_data, "z")
        is_maker = from_dict_get_bool(self.trade_data, "m")
        self.trade_type = "maker" if is_maker else "taker"
        self.trade_time = from_dict_get_float(self.trade_data, "T")
        self.trade_fee = from_dict_get_float(self.trade_data, "n")
        self.trade_fee_symbol = from_dict_get_string(self.trade_data, "N")

    def __str__(self):
        """输出字符串"""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

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


class BinanceRequestTradeData(BinanceTradeData):
    """交易类，用于保存成交信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.trade_data, "time")
        self.trade_id = from_dict_get_string(self.trade_data, "id")
        self.trade_symbol_name = from_dict_get_string(self.trade_data, "symbol")
        self.order_id = from_dict_get_string(self.trade_data, "orderId")
        self.client_order_id = None
        self.trade_side = from_dict_get_string(self.trade_data, "side")
        self.trade_offset = None
        self.trade_price = from_dict_get_float(self.trade_data, "price")
        self.trade_volume = from_dict_get_float(self.trade_data, "qty")
        self.trade_accumulate_volume = None
        is_maker = from_dict_get_bool(self.trade_data, "maker")
        self.trade_type = "maker" if is_maker else "taker"
        self.trade_time = from_dict_get_float(self.trade_data, "time")
        self.trade_fee = from_dict_get_float(self.trade_data, "commission")
        self.trade_fee_symbol = from_dict_get_string(self.trade_data, "commissionAsset")
        self.has_been_init_data = True
        return self


class BinanceSwapWssTradeData(BinanceTradeData):
    """交易类，用于保存成交信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_info = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.trade_data = self.trade_info["o"]
        self.trade_side = self._get_trade_side()
        self._init_wss_data()
        self.has_been_init_data = True
        return self

    def _get_trade_side(self):
        return from_dict_get_string(self.trade_data, "ps")


class BinanceSpotWssTradeData(BinanceTradeData):
    """spot交易类，用于保存成交信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.trade_data = json.loads(self.trade_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self._init_wss_data()
        self.has_been_init_data = True
        return self

    def _get_trade_side(self):
        pass
