"""
CTP K线数据容器
CTP 不直接提供K线 API，通常需要从 tick 数据合成
此容器定义了 CTP K线数据的标准格式
"""
from bt_api_py.containers.bars.bar import BarData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_int


class CtpBarData(BarData):
    """CTP K线数据（从 tick 合成或第三方获取）"""

    def __init__(self, bar_info, symbol_name=None, asset_type="FUTURE",
                 has_been_json_encoded=False):
        super().__init__(bar_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        self.open_time = None
        self.close_time = None
        self.open_price = None
        self.high_price = None
        self.low_price = None
        self.close_price = None
        self.volume_val = None
        self.amount_val = None
        self.open_interest = None        # CTP 特有: 持仓量
        self.settlement_price_val = None  # CTP 特有: 结算价

    def init_data(self):
        if self._initialized:
            return self
        info = self.bar_info
        if isinstance(info, dict):
            self.open_time = from_dict_get_string(info, 'open_time')
            self.close_time = from_dict_get_string(info, 'close_time')
            self.open_price = from_dict_get_float(info, 'open', 0.0)
            self.high_price = from_dict_get_float(info, 'high', 0.0)
            self.low_price = from_dict_get_float(info, 'low', 0.0)
            self.close_price = from_dict_get_float(info, 'close', 0.0)
            self.volume_val = from_dict_get_int(info, 'volume', 0)
            self.amount_val = from_dict_get_float(info, 'amount', 0.0)
            self.open_interest = from_dict_get_float(info, 'open_interest', 0.0)
            self.settlement_price_val = from_dict_get_float(info, 'settlement_price')
        self._initialized = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.close_time

    def get_open_time(self):
        return self.open_time

    def get_open_price(self):
        return self.open_price

    def get_high_price(self):
        return self.high_price

    def get_low_price(self):
        return self.low_price

    def get_close_price(self):
        return self.close_price

    def get_volume(self):
        return self.volume_val

    def get_amount(self):
        return self.amount_val

    def get_close_time(self):
        return self.close_time

    def get_bar_status(self):
        return True

    def get_open_interest(self):
        """CTP 特有: 持仓量"""
        return self.open_interest

    def get_settlement_price(self):
        """CTP 特有: 结算价"""
        return self.settlement_price_val

    def get_all_data(self):
        return {
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "close": self.close_price,
            "volume": self.volume_val,
            "amount": self.amount_val,
            "open_interest": self.open_interest,
            "settlement_price": self.settlement_price_val,
        }
