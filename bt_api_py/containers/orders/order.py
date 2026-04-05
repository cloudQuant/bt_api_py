"""订单类，用于确定订单的属性和方法。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class OrderStatus(Enum):
    SUBMITTED = "submitted"
    ACCEPTED = "new"
    PARTIAL = "partially_filled"
    COMPLETED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    MARGIN = "margin"
    EXPIRED = "expired"
    MMP_CANCELED = "mmp_canceled"
    EXPIRED_IN_MATCH = "expired_in_match"
    UNKNOWN = "unknown"

    LIVE = ACCEPTED
    PARTIALLY_FILLED = PARTIAL
    FILLED = COMPLETED

    def __str__(self) -> str:
        return self.value

    @classmethod
    def get_static_dict(cls) -> dict[str, OrderStatus]:
        return {
            "submitted": cls.SUBMITTED,
            "accepted": cls.ACCEPTED,
            "margin": cls.MARGIN,
            "NEW": cls.ACCEPTED,
            "new": cls.ACCEPTED,
            "live": cls.ACCEPTED,
            "PARTIALLY_FILLED": cls.PARTIAL,
            "partially_filled": cls.PARTIAL,
            "FILLED": cls.COMPLETED,
            "filled": cls.COMPLETED,
            "CANCELED": cls.CANCELED,
            "canceled": cls.CANCELED,
            "REJECTED": cls.REJECTED,
            "EXPIRED": cls.EXPIRED,
            "EXPIRED_IN_MATCH": cls.EXPIRED_IN_MATCH,
            "mmp_canceled": cls.MMP_CANCELED,
        }

    @classmethod
    def from_value(cls, status_value: str | None) -> OrderStatus:
        if status_value is None:
            return cls.REJECTED
        try:
            return cls.get_static_dict()[status_value]
        except KeyError as err:
            raise ValueError(f"Invalid order status value: {status_value}") from err


class OrderData(AutoInitMixin):
    """保存订单相关信息"""

    def __init__(self, order_info: Any, has_been_json_encoded: bool = False) -> None:
        self.event = "OrderEvent"
        self.order_info = order_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.symbol_name: str | None = None
        self.asset_type: str | None = None
        self.local_update_time: float | None = None
        self.order_data: Any = order_info if has_been_json_encoded else None
        self.server_time: float | None = None
        self.trade_id: str | None = None
        self.client_order_id: str | None = None
        self.cum_quote: float | None = None
        self.executed_qty: float | None = None
        self.order_id: str | None = None
        self.order_size: float | None = None
        self.order_price: float | None = None
        self.reduce_only: bool | None = None
        self.order_side: str | None = None
        self.order_status: OrderStatus | str | None = None
        self.order_symbol_name: str | None = None
        self.order_time_in_force: str | None = None
        self.order_type: str | None = None
        self.order_avg_price: float | None = None
        self.origin_order_type: str | None = None
        self.position_side: str | None = None
        self.trailing_stop_price: float | None = None
        self.trailing_stop_trigger_price: float | None = None
        self.trailing_stop_callback_rate: float | None = None
        self.trailing_stop_trigger_price_type: str | None = None
        self.stop_loss_price: float | None = None
        self.stop_loss_trigger_price: float | None = None
        self.stop_loss_trigger_price_type: str | None = None
        self.take_profit_price: float | None = None
        self.take_profit_trigger_price: float | None = None
        self.take_profit_trigger_price_type: str | None = None
        self.close_position: bool | None = None
        self.order_offset: str | None = None
        self.order_exchange_id: str | None = None
        self.all_data: dict[str, Any] | None = None

    def get_event(self) -> str:
        """# 事件类型"""
        return self.event

    def init_data(self) -> None | Self:
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "trade_id": self.trade_id,
                "client_order_id": self.client_order_id,
                "cum_quote": self.cum_quote,
                "executed_qty": self.executed_qty,
                "order_id": self.order_id,
                "order_size": self.order_size,
                "order_price": self.order_price,
                "reduce_only": self.reduce_only,
                "order_side": self.order_side,
                "order_status": self.order_status.value
                if isinstance(self.order_status, OrderStatus)
                else self.order_status,
                "order_symbol_name": self.order_symbol_name,
                "order_time_in_force": self.order_time_in_force,
                "order_type": self.order_type,
                "order_avg_price": self.order_avg_price,
                "origin_order_type": self.origin_order_type,
                "position_side": self.position_side,
                "trailing_stop_price": self.trailing_stop_price,
                "trailing_stop_trigger_price": self.trailing_stop_trigger_price,
                "trailing_stop_callback_rate": self.trailing_stop_callback_rate,
                "trailing_stop_trigger_price_type": self.trailing_stop_trigger_price_type,
                "stop_loss_price": self.stop_loss_price,
                "stop_loss_trigger_price": self.stop_loss_trigger_price,
                "stop_loss_trigger_price_type": self.stop_loss_trigger_price_type,
                "take_profit_price": self.take_profit_price,
                "take_profit_trigger_price": self.take_profit_trigger_price,
                "take_profit_trigger_price_type": self.take_profit_trigger_price_type,
                "close_position": self.close_position,
                "order_offset": self.order_offset,
                "order_exchange_id": self.order_exchange_id,
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        """# 交易所名称"""
        raise NotImplementedError

    def get_asset_type(self) -> str | None:
        """# 资产类型"""
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        """# symbol名称"""
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        """# 服务器时间戳"""
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        """# 本地时间戳"""
        raise NotImplementedError

    def get_trade_id(self) -> str | None:
        """# 交易所返回唯一成交id"""
        raise NotImplementedError

    def get_client_order_id(self) -> str | None:
        """# 客户端自定订单ID"""
        raise NotImplementedError

    def get_cum_quote(self) -> float | None:
        """# 累计成交额"""
        raise NotImplementedError

    def get_executed_qty(self) -> float | None:
        """# 已执行的成交量"""
        raise NotImplementedError

    def get_order_id(self) -> str | None:
        """# 订单id"""
        raise NotImplementedError

    def get_order_size(self) -> float | None:
        """# 订单原始数量"""
        raise NotImplementedError

    def get_order_price(self) -> float | None:
        """# 订单价格"""
        raise NotImplementedError

    def get_reduce_only(self) -> bool | None:
        """# 是否是只减仓单"""
        raise NotImplementedError

    def get_order_side(self) -> str | None:
        """# 订单方向"""
        raise NotImplementedError

    def get_order_status(self) -> OrderStatus | str | None:
        """# 订单状态"""
        raise NotImplementedError

    def get_order_symbol_name(self) -> str | None:
        """# 品种"""
        raise NotImplementedError

    def get_order_time_in_force(self) -> str | None:
        """# 订单有效期类型"""
        raise NotImplementedError

    def get_order_type(self) -> str | None:
        """# 订单类型"""
        raise NotImplementedError

    def get_order_avg_price(self) -> float | None:
        """# 平均价格"""
        raise NotImplementedError

    def get_origin_order_type(self) -> str | None:
        """# 原始类型"""
        raise NotImplementedError

    def get_position_side(self) -> str | None:
        """# 持仓方向"""
        raise NotImplementedError

    def get_trailing_stop_price(self) -> float | None:
        """# 止损价"""
        raise NotImplementedError

    def get_trailing_stop_trigger_price(self) -> float | None:
        """# 激活价格"""
        raise NotImplementedError

    def get_trailing_stop_callback_rate(self) -> float | None:
        """# 回调比例"""
        raise NotImplementedError

    def get_trailing_stop_trigger_price_type(self) -> str | None:
        """# 触发价类型"""
        raise NotImplementedError

    def get_stop_loss_price(self) -> float | None:
        raise NotImplementedError

    def get_stop_loss_trigger_price(self) -> float | None:
        raise NotImplementedError

    def get_stop_loss_trigger_price_type(self) -> str | None:
        raise NotImplementedError

    def get_take_profit_price(self) -> float | None:
        raise NotImplementedError

    def get_take_profit_trigger_price(self) -> float | None:
        raise NotImplementedError

    def get_take_profit_trigger_price_type(self) -> str | None:
        raise NotImplementedError

    def get_close_position(self) -> bool | None:
        """# 是否为触发平仓单; 仅在条件订单情况下会推送此字段"""
        raise NotImplementedError

    def get_order_offset(self) -> str | None:
        """# 开平方向: open / close / close_today / close_yesterday"""
        return None

    def get_order_exchange_id(self) -> str | None:
        """# 交易所代码, 如 'CFFEX', 'SHFE', 'SMART' 等"""
        return None

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
