"""
CTP Tick 数据容器
对应 CTP 的 CThostFtdcDepthMarketDataField 结构体
包含最新价、买卖盘、涨跌停板、持仓量等
"""

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class CtpTickerData(TickerData):
    """CTP Tick 行情数据"""

    def __init__(
        self, ticker_info, symbol_name=None, asset_type="FUTURE", has_been_json_encoded=False
    ):
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        # CTP Tick 字段
        self.instrument_id = None
        self.last_price_val = None
        self.pre_settlement_price = None
        self.pre_close_price = None
        self.open_price_val = None
        self.highest_price = None
        self.lowest_price = None
        self.bid_price_1 = None
        self.bid_volume_1 = None
        self.ask_price_1 = None
        self.ask_volume_1 = None
        self.volume_val = None
        self.turnover = None
        self.open_interest = None
        self.upper_limit_price = None  # 涨停板价
        self.lower_limit_price = None  # 跌停板价
        self.update_time_val = None
        self.update_millisec = None
        self.trading_day = None
        self.exchange_id = None

    def init_data(self):
        if self._initialized:
            return self
        info = self.ticker_info
        if isinstance(info, dict):
            self.instrument_id = from_dict_get_string(info, "InstrumentID")
            self.last_price_val = from_dict_get_float(info, "LastPrice")
            self.pre_settlement_price = from_dict_get_float(info, "PreSettlementPrice")
            self.pre_close_price = from_dict_get_float(info, "PreClosePrice")
            self.open_price_val = from_dict_get_float(info, "OpenPrice")
            self.highest_price = from_dict_get_float(info, "HighestPrice")
            self.lowest_price = from_dict_get_float(info, "LowestPrice")
            self.bid_price_1 = from_dict_get_float(info, "BidPrice1")
            self.bid_volume_1 = from_dict_get_int(info, "BidVolume1", 0)
            self.ask_price_1 = from_dict_get_float(info, "AskPrice1")
            self.ask_volume_1 = from_dict_get_int(info, "AskVolume1", 0)
            self.volume_val = from_dict_get_int(info, "Volume", 0)
            self.turnover = from_dict_get_float(info, "Turnover", 0.0)
            self.open_interest = from_dict_get_float(info, "OpenInterest", 0.0)
            self.upper_limit_price = from_dict_get_float(info, "UpperLimitPrice")
            self.lower_limit_price = from_dict_get_float(info, "LowerLimitPrice")
            self.update_time_val = from_dict_get_string(info, "UpdateTime")
            self.update_millisec = from_dict_get_int(info, "UpdateMillisec", 0)
            self.trading_day = from_dict_get_string(info, "TradingDay")
            self.exchange_id = from_dict_get_string(info, "ExchangeID")
        self._initialized = True
        return self

    def get_exchange_name(self):
        self._ensure_init()
        return self.exchange_name

    def get_local_update_time(self):
        self._ensure_init()
        return self.update_time_val

    def get_symbol_name(self):
        self._ensure_init()
        return self.instrument_id or self.symbol_name

    def get_ticker_symbol_name(self):
        return self.instrument_id or self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.update_time_val

    def get_bid_price(self):
        self._ensure_init()
        return self.bid_price_1

    def get_ask_price(self):
        self._ensure_init()
        return self.ask_price_1

    def get_bid_volume(self):
        return self.bid_volume_1

    def get_ask_volume(self):
        return self.ask_volume_1

    def get_last_price(self):
        self._ensure_init()
        return self.last_price_val

    def get_last_volume(self):
        return self.volume_val

    def get_upper_limit_price(self):
        """涨停板价"""
        return self.upper_limit_price

    def get_lower_limit_price(self):
        """跌停板价"""
        return self.lower_limit_price

    def get_open_interest(self):
        """持仓量"""
        return self.open_interest

    def get_all_data(self):
        return {
            "exchange_name": self.exchange_name,
            "instrument_id": self.instrument_id,
            "last_price": self.last_price_val,
            "bid_price_1": self.bid_price_1,
            "bid_volume_1": self.bid_volume_1,
            "ask_price_1": self.ask_price_1,
            "ask_volume_1": self.ask_volume_1,
            "volume": self.volume_val,
            "turnover": self.turnover,
            "open_interest": self.open_interest,
            "upper_limit_price": self.upper_limit_price,
            "lower_limit_price": self.lower_limit_price,
            "update_time": self.update_time_val,
            "trading_day": self.trading_day,
            "exchange_id": self.exchange_id,
        }
