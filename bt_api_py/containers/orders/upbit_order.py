from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string
from bt_api_py.logging_factory import get_logger

logger = get_logger("container")


class UpbitOrderData(OrderData):
    """保存 Upbit 订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "UPBIT"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = order_info if has_been_json_encoded else None
        self.order_uuid = None
        self.identifier = None
        self.state = None
        self.side = None
        self.ord_type = None
        self.price = None
        self.volume = None
        self.executed_volume = None
        self.remaining_volume = None
        self.trades = None
        self.created_at = None
        self.updated_at = None
        self.completed_at = None
        self.cancelled_at = None
        self.currency = None
        self.fee = None
        self.fee_currency = None
        self.market = None
        self.type = None
        self.price_avg = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化订单数据"""
        try:
            if not self.has_been_json_encoded:
                self.order_data = json.loads(self.raw_data)

            # 基础信息
            self.order_uuid = from_dict_get_string(self.order_data, "uuid")
            self.identifier = from_dict_get_string(self.order_data, "identifier")
            self.state = from_dict_get_string(self.order_data, "state")
            self.side = from_dict_get_string(self.order_data, "side")  # "bid" or "ask"
            self.ord_type = from_dict_get_string(
                self.order_data, "ord_type"
            )  # "limit", "market", etc.
            self.market = from_dict_get_string(self.order_data, "market")

            # 价格和数量
            self.price = from_dict_get_float(self.order_data, "price")
            self.volume = from_dict_get_float(self.order_data, "volume")
            self.executed_volume = from_dict_get_float(self.order_data, "executed_volume")
            self.remaining_volume = from_dict_get_float(self.order_data, "remaining_volume")

            # 成交信息
            self.trades = from_dict_get_float(self.order_data, "trades")

            # 时间信息
            self.created_at = from_dict_get_string(self.order_data, "created_at")
            self.updated_at = from_dict_get_string(self.order_data, "updated_at")
            self.completed_at = from_dict_get_string(self.order_data, "completed_at")
            self.cancelled_at = from_dict_get_string(self.order_data, "cancelled_at")

            # 费用信息
            self.currency = from_dict_get_string(self.order_data, "currency")
            self.fee = from_dict_get_float(self.order_data, "fee")
            self.fee_currency = from_dict_get_string(self.order_data, "fee_currency")
            self.price_avg = from_dict_get_float(self.order_data, "trades_price_avg")

            # 订单类型标识
            if self.side == "bid":
                self.type = "buy"
            elif self.side == "ask":
                self.type = "sell"
            else:
                self.type = self.side

            self.has_been_init_data = True

        except Exception as e:
            logger.error(f"Error initializing Upbit order data: {e}", exc_info=True)

    def get_all_data(self):
        """获取所有订单数据"""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_uuid": self.order_uuid,
                "identifier": self.identifier,
                "state": self.state,
                "side": self.side,
                "ord_type": self.ord_type,
                "price": self.price,
                "volume": self.volume,
                "executed_volume": self.executed_volume,
                "remaining_volume": self.remaining_volume,
                "trades": self.trades,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "completed_at": self.completed_at,
                "cancelled_at": self.cancelled_at,
                "currency": self.currency,
                "fee": self.fee,
                "fee_currency": self.fee_currency,
                "market": self.market,
                "type": self.type,
                "price_avg": self.price_avg,
            }
        return self.all_data

    def is_open(self):
        """检查订单是否开放"""
        return self.state in ["wait", "watch"]

    def is_filled(self):
        """检查订单是否已成交"""
        return self.state == "done"

    def is_cancelled(self):
        """检查订单是否已撤销"""
        return self.state == "cancel"

    def fill_percentage(self):
        """计算成交百分比"""
        if self.volume and self.volume > 0:
            return (self.executed_volume or 0) / self.volume * 100
        return 0.0

    def __str__(self):
        """字符串表示"""
        if not self.has_been_init_data:
            self.init_data()

        return (
            f"UpbitOrder(id={self.order_uuid or self.identifier}, "
            f"symbol={self.symbol_name}, "
            f"type={self.type}, "
            f"side={self.side}, "
            f"price={self.price}, "
            f"vol={self.volume}, "
            f"filled={self.fill_percentage():.1f}%, "
            f"status={self.state})"
        )
