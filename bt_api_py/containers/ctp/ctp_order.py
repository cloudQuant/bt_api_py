"""
CTP 订单数据容器
对应 CTP 的 CThostFtdcOrderField 结构体
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import (
    from_dict_get_float,
    from_dict_get_int,
    from_dict_get_string,
)

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
    """CTP 订单数据."""

    def __init__(
        self,
        order_info: Any,
        symbol_name: str | None = None,
        asset_type: str = "FUTURE",
        has_been_json_encoded: bool = False,
    ) -> None:
        """
        Initialize CTP order data.

        Args:
            order_info: Order information dictionary.
            symbol_name: Symbol name.
            asset_type: Asset type (default: "FUTURE").
            has_been_json_encoded: Whether data has been JSON encoded.

        Returns:
            None
        """
        super().__init__(order_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        # CTP 订单字段
        self.instrument_id: str | None = None
        self.order_ref: str | None = None
        self.order_sys_id: str | None = None
        self.direction: str | None = None
        self.offset: str | None = None
        self.limit_price: float | None = None
        self.volume_total_original: int | None = None
        self.volume_traded: int | None = None
        self.volume_total: int | None = None
        self.order_status: OrderStatus | None = None
        self.insert_time: str | None = None
        self.update_time: str | None = None
        self.status_msg: str | None = None
        self.exchange_id: str | None = None
        self.front_id: int | None = None
        self.session_id: int | None = None

    def init_data(self) -> CtpOrderData:
        """
        Initialize data from order_info.

        Returns:
            Self instance for chaining.
        """
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

    def get_exchange_name(self) -> str:
        """
        Get exchange name.

        Returns:
            Exchange name string.
        """
        return self.exchange_name

    def get_asset_type(self) -> str:
        """
        Get asset type.

        Returns:
            Asset type string.
        """
        return self.asset_type

    def get_symbol_name(self) -> str | None:
        """
        Get symbol name.

        Returns:
            Symbol name or None.
        """
        return self.instrument_id or self.symbol_name

    def get_server_time(self) -> str | None:
        """
        Get server time.

        Returns:
            Server time string or None.
        """
        return self.insert_time

    def get_local_update_time(self) -> str | None:
        """
        Get local update time.

        Returns:
            Local update time string or None.
        """
        return self.update_time

    def get_order_id(self) -> str | None:
        """
        Get order ID.

        Returns:
            Order ID string or None.
        """
        return self.order_sys_id

    def get_client_order_id(self) -> str | None:
        """
        Get client order ID.

        Returns:
            Client order ID string or None.
        """
        return self.order_ref

    def get_order_size(self) -> int | None:
        """
        Get order size.

        Returns:
            Order size or None.
        """
        return self.volume_total_original

    def get_order_price(self) -> float | None:
        """
        Get order price.

        Returns:
            Order price or None.
        """
        return self.limit_price

    def get_order_side(self) -> str | None:
        """
        Get order side.

        Returns:
            Order side string or None.
        """
        return self.direction

    def get_order_status(self) -> OrderStatus | None:
        """
        Get order status.

        Returns:
            Order status or None.
        """
        return self.order_status

    def get_order_offset(self) -> str | None:
        """
        Get order offset.

        Returns:
            Offset: open / close / close_today / close_yesterday.
        """
        return self.offset

    def get_order_exchange_id(self) -> str | None:
        """
        Get order exchange ID.

        Returns:
            Exchange ID: CFFEX / SHFE / DCE / CZCE / INE / GFEX.
        """
        return self.exchange_id

    def get_executed_qty(self) -> int | None:
        """
        Get executed quantity.

        Returns:
            Executed quantity or None.
        """
        return self.volume_traded

    def get_order_symbol_name(self) -> str | None:
        """
        Get order symbol name.

        Returns:
            Order symbol name or None.
        """
        return self.instrument_id or self.symbol_name

    def get_order_type(self) -> str:
        """
        Get order type.

        Returns:
            Always "limit" for CTP.
        """
        return "limit"

    def get_order_avg_price(self) -> float | None:
        """
        Get order average price.

        Returns:
            Order average price or None.
        """
        return self.limit_price

    def get_order_time_in_force(self) -> str:
        """
        Get order time in force.

        Returns:
            Always "GFD" for CTP (Good For Day).
        """
        return "GFD"  # CTP 默认当日有效
