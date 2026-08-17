"""持仓限额检查（持仓规模/名义敞口/杠杆/集中度）。"""

from __future__ import annotations

from typing import Any

from ..containers.risk_metrics import RiskMetrics
from .limits_types import LimitStatus, LimitType


class PositionLimitsMixin:
    """持仓限额检查方法（供 LimitsManager 混入）。"""

    def _check_position_limits(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None,
    ) -> dict[str, Any]:
        """"""
        #  - 
        if not current_metrics:
            return {
                "limit_type": "position_limits",
                "status": LimitStatus.WITHIN_LIMIT,
                "warning": "",
                "restriction": "",
            }

        # 
        checks = []

        # 
        current_position = getattr(current_metrics, "total_position_value", 0)
        limits = self.get_current_limits(exchange_name, account_id)
        max_position = limits.get(LimitType.MAX_POSITION_SIZE, {}).get("value", 10000000)

        if max_position > 0:
            utilization = current_position / max_position
            if utilization > self.critical_threshold:
                checks.append(
                    {
                        "limit_type": LimitType.MAX_POSITION_SIZE,
                        "status": LimitStatus.CRITICAL,
                        "restriction": "Position size exceeds limit",
                    }
                )
            elif utilization > self.warning_threshold:
                checks.append(
                    {
                        "limit_type": LimitType.MAX_POSITION_SIZE,
                        "status": LimitStatus.WARNING,
                        "warning": f"Position approaching limit: {utilization:.1%}",
                    }
                )

        # 
        if checks:
            worst_check = max(
                checks, key=lambda x: {"CRITICAL": 3, "WARNING": 2, "WITHIN_LIMIT": 1}[x["status"]]
            )
            return worst_check
        else: return {
                "limit_type": "position_limits",
                "status": LimitStatus.WITHIN_LIMIT,
                "warning": "",
                "restriction": "",
            }

    def _check_max_position_size(
        self, exchange_name: str, account_id: str, position_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        total_position = position_data.get("total_value", 0)
        limits = self.get_current_limits(exchange_name, account_id)
        max_position = limits.get(LimitType.MAX_POSITION_SIZE, {}).get("value", 10000000)

        utilization = total_position / max_position if max_position > 0 else 0

        if utilization > self.critical_threshold:
            status = LimitStatus.CRITICAL
            restriction = "Position size exceeds limit"
        elif utilization > self.warning_threshold:
            status = LimitStatus.WARNING
            restriction = ""
            warning = f"Position size {total_position:,.0f} is {utilization:.1%} of limit"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MAX_POSITION_SIZE,
            "current_value": total_position,
            "limit_value": max_position,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }

    def _check_notional_exposure(
        self, exchange_name: str, account_id: str, position_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        notional_exposure = position_data.get("notional_exposure", 0)
        limits = self.get_current_limits(exchange_name, account_id)
        max_notional = limits.get(LimitType.MAX_NOTIONAL_EXPOSURE, {}).get("value", 50000000)

        utilization = notional_exposure / max_notional if max_notional > 0 else 0

        if utilization > self.critical_threshold:
            status = LimitStatus.CRITICAL
            restriction = "Notional exposure exceeds limit"
        elif utilization > self.warning_threshold:
            status = LimitStatus.WARNING
            restriction = ""
            warning = f"Notional exposure {notional_exposure:,.0f} is {utilization:.1%} of limit"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MAX_NOTIONAL_EXPOSURE,
            "current_value": notional_exposure,
            "limit_value": max_notional,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }

    def _check_leverage_limit(
        self, exchange_name: str, account_id: str, position_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        current_leverage = position_data.get("leverage", 1.0)
        limits = self.get_current_limits(exchange_name, account_id)
        max_leverage = limits.get(LimitType.MAX_LEVERAGE, {}).get("value", 10.0)

        utilization = current_leverage / max_leverage if max_leverage > 0 else 0

        if utilization > self.critical_threshold:
            status = LimitStatus.CRITICAL
            restriction = "Leverage exceeds limit"
        elif utilization > self.warning_threshold:
            status = LimitStatus.WARNING
            restriction = ""
            warning = f"Leverage {current_leverage:.1f}x is {utilization:.1%} of limit"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MAX_LEVERAGE,
            "current_value": current_leverage,
            "limit_value": max_leverage,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }

    def _check_concentration_limit(
        self, exchange_name: str, account_id: str, position_data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        concentration_ratio = position_data.get("concentration_ratio", 0)
        limits = self.get_current_limits(exchange_name, account_id)
        max_concentration = limits.get(LimitType.MAX_CONCENTRATION, {}).get("value", 0.3)

        utilization = concentration_ratio / max_concentration if max_concentration > 0 else 0

        if utilization > self.critical_threshold:
            status = LimitStatus.CRITICAL
            restriction = "Concentration exceeds limit"
        elif utilization > self.warning_threshold:
            status = LimitStatus.WARNING
            restriction = ""
            warning = f"Concentration {concentration_ratio:.1%} is {utilization:.1%} of limit"
        else:
            status = LimitStatus.WITHIN_LIMIT
            restriction = ""
            warning = ""

        return {
            "limit_type": LimitType.MAX_CONCENTRATION,
            "current_value": concentration_ratio,
            "limit_value": max_concentration,
            "utilization_ratio": utilization,
            "status": status,
            "warning": warning,
            "restriction": restriction,
        }
