"""保证金限额检查。"""

from __future__ import annotations

from typing import Any

from ..containers.risk_metrics import RiskMetrics
from .limits_types import LimitStatus, LimitType


class MarginLimitsMixin:
    """保证金限额检查方法（供 LimitsManager 混入）。"""

    def _check_margin_requirement(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None,
    ) -> dict[str, Any]:
        """"""
        # 
        order_value = order_data.get("size", 0) * order_data.get("price", 1)
        current_margin = current_metrics.credit_risk.credit_utilization if current_metrics else 0
        limits = self.get_current_limits(exchange_name, account_id)
        min_margin_ratio = limits.get(LimitType.MIN_MARGIN_REQUIREMENT, {}).get("value", 0.1)

        required_margin = order_value * min_margin_ratio
        available_margin = order_value * (1 - current_margin)
        margin_sufficient = available_margin >= required_margin

        utilization = current_margin if margin_sufficient else 1.0

        if not margin_sufficient:
            status = LimitStatus.CRITICAL
            restriction = "Insufficient margin for order"
            warning = f"Required margin: {required_margin:,.0f}, Available: {available_margin:,.0f}"
        elif current_margin > 0.8:
            status = LimitStatus.WARNING
            restriction = ""
            warning = f"High margin utilization: {current_margin:.1%}"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MIN_MARGIN_REQUIREMENT,
            "current_value": current_margin,
            "limit_value": min_margin_ratio,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }
