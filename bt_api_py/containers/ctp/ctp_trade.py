"""
CTP 成交数据容器
对应 CTP 的 CThostFtdcTradeField 结构体
"""

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class CtpTradeData(TradeData):
    """CTP 成交数据"""

    def __init__(
        self, trade_info, symbol_name=None, asset_type="FUTURE", has_been_json_encoded=False
    ):
        super().__init__(trade_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        # CTP 成交字段
        self.instrument_id = None
        self.trade_id_value = None
        self.order_ref = None
        self.order_sys_id = None
        self.direction = None
        self.offset = None
        self.price = None
        self.volume = None
        self.trade_date = None
        self.trade_time = None
        self.exchange_id = None

    def init_data(self):
        if self._initialized:
            return self
        info = self.trade_info
        if isinstance(info, dict):
            self.instrument_id = from_dict_get_string(info, "InstrumentID")
            self.trade_id_value = from_dict_get_string(info, "TradeID")
            self.order_ref = from_dict_get_string(info, "OrderRef")
            self.order_sys_id = from_dict_get_string(info, "OrderSysID")
            direction_char = from_dict_get_string(info, "Direction", "0")
            self.direction = "buy" if direction_char == "0" else "sell"
            offset_char = from_dict_get_string(info, "OffsetFlag", "0")
            offset_map = {"0": "open", "1": "close", "3": "close_today", "4": "close_yesterday"}
            self.offset = offset_map.get(offset_char, "open")
            self.price = from_dict_get_float(info, "Price", 0.0)
            self.volume = from_dict_get_int(info, "Volume", 0)
            self.trade_date = from_dict_get_string(info, "TradeDate")
            self.trade_time = from_dict_get_string(info, "TradeTime")
            self.exchange_id = from_dict_get_string(info, "ExchangeID")
        self._initialized = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.instrument_id or self.symbol_name

    def get_server_time(self):
        return self.trade_time

    def get_trade_id(self):
        return self.trade_id_value

    def get_order_id(self):
        return self.order_sys_id

    def get_client_order_id(self):
        return self.order_ref

    def get_trade_side(self):
        return self.direction

    def get_trade_offset(self):
        """开平方向: open / close / close_today / close_yesterday"""
        return self.offset

    def get_trade_price(self):
        return self.price

    def get_trade_volume(self):
        return self.volume

    def get_trade_time(self):
        return self.trade_time

    def get_trade_fee(self):
        return 0.0  # CTP 成交回报不含手续费

    def get_trade_fee_symbol(self):
        return "CNY"

    def get_all_data(self):
        return {
            "exchange_name": self.exchange_name,
            "instrument_id": self.instrument_id,
            "trade_id": self.trade_id_value,
            "order_sys_id": self.order_sys_id,
            "direction": self.direction,
            "offset": self.offset,
            "price": self.price,
            "volume": self.volume,
            "trade_date": self.trade_date,
            "trade_time": self.trade_time,
            "exchange_id": self.exchange_id,
        }
