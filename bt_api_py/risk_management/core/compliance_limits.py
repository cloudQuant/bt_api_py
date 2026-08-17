"""合规限额检查。"""

from __future__ import annotations

from typing import Any

from ..containers.risk_metrics import RiskMetrics
from .limits_types import LimitStatus


class ComplianceLimitsMixin:
    """合规限额检查方法（供 LimitsManager 混入）。"""

    def _check_compliance_limits(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None,
    ) -> dict[str, Any]:
        """"""
        # 
        return {
            "limit_type": "compliance_limits",
            "status": LimitStatus.WITHIN_LIMIT,
            "warning": "",
            "restriction": "",
        }
