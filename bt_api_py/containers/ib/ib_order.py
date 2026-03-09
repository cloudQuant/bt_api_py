"""IB 订单数据容器
对应 IB TWS API 的 Order / OrderState.
"""

from typing import Any

from bt_api_py.containers.orders.order import OrderData, OrderStatus

# IB 订单状态映射
IB_ORDER_STATUS_MAP = {
    "PendingSubmit": OrderStatus.SUBMITTED,
    "PendingCancel": OrderStatus.SUBMITTED,
    "PreSubmitted": OrderStatus.SUBMITTED,
    "Submitted": OrderStatus.ACCEPTED,
    "ApiPending": OrderStatus.SUBMITTED,
    "ApiCancelled": OrderStatus.CANCELED,
    "Cancelled": OrderStatus.CANCELED,
    "Filled": OrderStatus.COMPLETED,
    "Inactive": OrderStatus.REJECTED,
}


class IbOrderData(OrderData):
    """IB 订单数据."""

    def __init__(
        self,
        order_info: Any,
        symbol_name: Any = None,
        asset_type: Any = "STK",
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(order_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "IB"
        self._initialized = False
        self.order_id_val = None
        self.perm_id = None
        self.client_id = None
        self.action = None  # BUY / SELL
        self.total_quantity = None
        self.order_type_val = None  # LMT / MKT / STP / TRAIL 等
        self.lmt_price = None
        self.aux_price = None  # 止损价/追踪价
        self.tif = None  # 有效期: DAY / GTC / IOC / FOK
        self.oca_group = None  # OCA 组
        self.status_val = None
        self.filled = None
        self.remaining = None
        self.avg_fill_price = None
        self.last_fill_time = None

    def init_data(self) -> None:
        if self._initialized:
            return self
        info = self.order_info
        if isinstance(info, dict):
            self.order_id_val = info.get("orderId")
            self.perm_id = info.get("permId")
            self.client_id = info.get("clientId")
            self.action = info.get("action", "BUY")
            self.total_quantity = float(info.get("totalQuantity", 0))
            self.order_type_val = info.get("orderType", "LMT")
            self.lmt_price = float(info.get("lmtPrice", 0))
            self.aux_price = float(info.get("auxPrice", 0))
            self.tif = info.get("tif", "DAY")
            self.oca_group = info.get("ocaGroup", "")
            status_str = info.get("status", "PendingSubmit")
            self.status_val = IB_ORDER_STATUS_MAP.get(status_str, OrderStatus.SUBMITTED)
            self.filled = float(info.get("filled", 0))
            self.remaining = float(info.get("remaining", 0))
            self.avg_fill_price = float(info.get("avgFillPrice", 0))
            self.last_fill_time = info.get("lastFillTime")
        self._initialized = True
        return self

    def get_exchange_name(self) -> None:
        return self.exchange_name

    def get_asset_type(self) -> None:
        return self.asset_type

    def get_symbol_name(self) -> None:
        return self.symbol_name

    def get_server_time(self) -> None:
        return self.last_fill_time

    def get_local_update_time(self) -> None:
        return self.last_fill_time

    def get_order_id(self) -> None:
        return self.order_id_val

    def get_client_order_id(self) -> None:
        return self.perm_id

    def get_order_size(self) -> None:
        return self.total_quantity

    def get_order_price(self) -> None:
        return self.lmt_price

    def get_order_side(self) -> None:
        return self.action.lower() if self.action else None

    def get_order_status(self) -> None:
        return self.status_val

    def get_executed_qty(self) -> None:
        return self.filled

    def get_order_symbol_name(self) -> None:
        return self.symbol_name

    def get_order_type(self) -> None:
        return self.order_type_val

    def get_order_avg_price(self) -> None:
        return self.avg_fill_price

    def get_order_time_in_force(self) -> None:
        return self.tif

    def get_order_exchange_id(self) -> None:
        return "SMART"

    def __str__(self) -> None:
        return (
            f"IbOrder({self.symbol_name}, {self.action}, "
            f"type={self.order_type_val}, price={self.lmt_price}, "
            f"qty={self.total_quantity}, status={self.status_val})"
        )

    def __repr__(self) -> None:
        return self.__str__()
