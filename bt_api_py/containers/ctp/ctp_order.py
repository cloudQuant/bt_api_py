"""
CTP 订单数据容器
对应 CTP 的 CThostFtdcOrderField 结构体
"""

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string

# CTP 报单状态映射
CTP_ORDER_STATUS_MAP = {
    "0": OrderStatus.COMPLETED,  # 全部成交
    "1": OrderStatus.PARTIAL,  # 部分成交还在队列中
    "2": OrderStatus.PARTIAL,  # 部分成交不在队列中
    "3": OrderStatus.ACCEPTED,  # 未成交还在队列中
    "4": OrderStatus.ACCEPTED,  # 未成交不在队列中
    "5": OrderStatus.CANCELED,  # 撤单
    "a": OrderStatus.SUBMITTED,  # 未知
    "b": OrderStatus.SUBMITTED,  # 尚未触发
    "c": OrderStatus.SUBMITTED,  # 已触发
}

# CTP 买卖方向
CTP_DIRECTION_MAP = {
    "0": "buy",
    "1": "sell",
}

# CTP 开平标志
CTP_OFFSET_MAP = {
    "0": "open",
    "1": "close",
    "2": "force_close",
    "3": "close_today",
    "4": "close_yesterday",
    "5": "force_close_yesterday",
    "6": "local_force_close",
}


class CtpOrderData(OrderData):
    """CTP 订单数据"""

    def __init__(
        self, order_info, symbol_name=None, asset_type="FUTURE", has_been_json_encoded=False
    ):
        super().__init__(order_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        # CTP 订单字段
        self.instrument_id = None
        self.order_ref = None
        self.order_sys_id = None
        self.direction = None
        self.offset = None
        self.limit_price = None
        self.volume_total_original = None
        self.volume_traded = None
        self.volume_total = None
        self.order_status = None
        self.insert_time = None
        self.update_time = None
        self.status_msg = None
        self.exchange_id = None
        self.front_id = None
        self.session_id = None

    def init_data(self):
        if self._initialized:
            return self
        info = self.order_info
        if isinstance(info, dict):
            self.instrument_id = from_dict_get_string(info, "InstrumentID")
            self.order_ref = from_dict_get_string(info, "OrderRef")
            self.order_sys_id = from_dict_get_string(info, "OrderSysID")
            direction_char = from_dict_get_string(info, "Direction", "0")
            self.direction = CTP_DIRECTION_MAP.get(direction_char, "buy")
            offset_char = from_dict_get_string(info, "CombOffsetFlag", "0")
            self.offset = CTP_OFFSET_MAP.get(offset_char[0] if offset_char else "0", "open")
            self.limit_price = from_dict_get_float(info, "LimitPrice", 0.0)
            self.volume_total_original = from_dict_get_int(info, "VolumeTotalOriginal", 0)
            self.volume_traded = from_dict_get_int(info, "VolumeTraded", 0)
            self.volume_total = from_dict_get_int(info, "VolumeTotal", 0)
            status_char = from_dict_get_string(info, "OrderStatus", "a")
            self.order_status = CTP_ORDER_STATUS_MAP.get(status_char, OrderStatus.SUBMITTED)
            self.insert_time = from_dict_get_string(info, "InsertTime")
            self.update_time = from_dict_get_string(info, "UpdateTime")
            self.status_msg = from_dict_get_string(info, "StatusMsg")
            self.exchange_id = from_dict_get_string(info, "ExchangeID")
            self.front_id = from_dict_get_int(info, "FrontID")
            self.session_id = from_dict_get_int(info, "SessionID")
        self._initialized = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.instrument_id or self.symbol_name

    def get_server_time(self):
        return self.insert_time

    def get_local_update_time(self):
        return self.update_time

    def get_order_id(self):
        return self.order_sys_id

    def get_client_order_id(self):
        return self.order_ref

    def get_order_size(self):
        return self.volume_total_original

    def get_order_price(self):
        return self.limit_price

    def get_order_side(self):
        return self.direction

    def get_order_status(self):
        return self.order_status

    def get_order_offset(self):
        """开平方向: open / close / close_today / close_yesterday"""
        return self.offset

    def get_order_exchange_id(self):
        """交易所代码: CFFEX / SHFE / DCE / CZCE / INE / GFEX"""
        return self.exchange_id

    def get_executed_qty(self):
        return self.volume_traded

    def get_order_symbol_name(self):
        return self.instrument_id or self.symbol_name

    def get_order_type(self):
        return "limit"

    def get_order_avg_price(self):
        return self.limit_price

    def get_order_time_in_force(self):
        return "GFD"  # CTP 默认当日有效

    def __str__(self):
        return (
            f"CtpOrder({self.instrument_id}, {self.direction}, {self.offset}, "
            f"price={self.limit_price}, vol={self.volume_total_original}, "
            f"status={self.order_status})"
        )

    def __repr__(self):
        return self.__str__()
