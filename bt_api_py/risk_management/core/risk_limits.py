"""风险限额检查（VaR 等）。"""

from __future__ import annotations

from typing import Any

from ..containers.risk_metrics import RiskMetrics
from .limits_types import LimitStatus, LimitType


class RiskLimitsMixin:
    """风险限额检查方法（供 LimitsManager 混入）。"""

    critical_threshold: float
    warning_threshold: float

    def get_current_limits(self, exchange_name: str, account_id: str) -> dict[str, Any]: ...

    def _check_risk_limits(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None,
    ) -> dict[str, Any]:
        """"""
        if not current_metrics:
            return {
                "limit_type": "risk_limits",
                "status": LimitStatus.WITHIN_LIMIT,
                "warning": "",
                "restriction": "",
            }

        limits = self.get_current_limits(exchange_name, account_id)
        checks = []

        # VaR
        current_var = float(current_metrics.market_risk.value_at_risk_1d)
        max_var = limits.get(LimitType.MAX_VAR, {}).get("value", 1000000)

        if max_var > 0:
            utilization = current_var / max_var
            if utilization > self.critical_threshold:
                checks.append(
                    {
                        "limit_type": LimitType.MAX_VAR,
                        "status": LimitStatus.CRITICAL,
                        "restriction": "VaR exceeds limit",
                    }
                )
            elif utilization > self.warning_threshold:
                checks.append(
                    {
                        "limit_type": LimitType.MAX_VAR,
                        "status": LimitStatus.WARNING,
                        "warning": f"VaR approaching limit: {utilization:.1%}",
                    }
                )

        #
        if checks:
            worst_check = max(
                checks, key=lambda x: {"CRITICAL": 3, "WARNING": 2, "WITHIN_LIMIT": 1}[x["status"]]
            )
            return worst_check
        else:
            return {
                "limit_type": "risk_limits",
                "status": LimitStatus.WITHIN_LIMIT,
                "warning": "",
                "restriction": "",
            }
