"""订单限额检查（最大订单/下单频率）。"""

from __future__ import annotations

import time
from typing import Any

from ..containers.risk_metrics import RiskMetrics
from .limits_types import LimitStatus, LimitType


class OrderLimitsMixin:
    """订单限额检查方法（供 LimitsManager 混入）。"""

    critical_threshold: float
    warning_threshold: float

    def get_current_limits(self, exchange_name: str, account_id: str) -> dict[str, Any]: ...

    def _check_max_order_size(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None,
    ) -> dict[str, Any]:
        """"""
        order_size = order_data.get("size", 0) * order_data.get("price", 1)
        limits = self.get_current_limits(exchange_name, account_id)
        limit_value = limits.get(LimitType.MAX_ORDER_SIZE, {}).get("value", 1000000)

        utilization = order_size / limit_value if limit_value > 0 else 0

        if utilization > self.critical_threshold:
            status = LimitStatus.CRITICAL
            restriction = "Order exceeds maximum size limit"
        elif utilization > self.warning_threshold:
            status = LimitStatus.WARNING
            restriction = "Order approaching size limit"
            warning = f"Order size {order_size:,.0f} is {utilization:.1%} of limit"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MAX_ORDER_SIZE,
            "current_value": order_size,
            "limit_value": limit_value,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }

    def _check_order_frequency(
        self, exchange_name: str, account_id: str, order_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        #  -
        current_time = int(time.time())
        key = f"{exchange_name}:{account_id}"

        #  ()
        recent_orders = getattr(self, "_recent_orders", {})
        if key not in recent_orders:
            recent_orders[key] = []

        # 1
        recent_orders[key] = [t for t in recent_orders[key] if current_time - t < 60]
        recent_orders[key].append(current_time)

        orders_per_minute = len(recent_orders[key])
        limits = self.get_current_limits(exchange_name, account_id)
        limit_value = limits.get(LimitType.MAX_ORDERS_PER_MINUTE, {}).get("value", 60)

        utilization = orders_per_minute / limit_value if limit_value > 0 else 0

        if utilization > self.critical_threshold:
            status = LimitStatus.CRITICAL
            restriction = "Order frequency exceeds limit"
        elif utilization > self.warning_threshold:
            status = LimitStatus.WARNING
            restriction = "Order frequency approaching limit"
            warning = f"Orders per minute {orders_per_minute} is {utilization:.1%} of limit"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MAX_ORDERS_PER_MINUTE,
            "current_value": orders_per_minute,
            "limit_value": limit_value,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }
