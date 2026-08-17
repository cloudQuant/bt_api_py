"""限额类型、状态与动态限额定义。"""

from __future__ import annotations

import time


class LimitType:
    """"""

    # 
    MAX_ORDER_SIZE = "max_order_size"  # 
    MAX_ORDERS_PER_MINUTE = "max_orders_per_minute"  # 
    MAX_ORDERS_PER_DAY = "max_orders_per_day"  # 
    MIN_MARGIN_REQUIREMENT = "min_margin_requirement"  # 

    # 
    MAX_POSITION_SIZE = "max_position_size"  # 
    MAX_NOTIONAL_EXPOSURE = "max_notional_exposure"  # 
    MAX_LEVERAGE = "max_leverage"  # 
    MAX_CONCENTRATION = "max_concentration"  # 

    # 
    MAX_VAR = "max_var"  # VaR
    MAX_DRAWDOWN = "max_drawdown"  # 
    MAX_CORRELATION = "max_correlation"  # 
    MIN_LIQUIDITY = "min_liquidity"  # 

    # 
    REGULATORY_LIMITS = "regulatory_limits"  # 
    REPORTING_THRESHOLDS = "reporting_thresholds"  # 


class LimitStatus:
    """"""

    WITHIN_LIMIT = "WITHIN_LIMIT"  # 
    WARNING = "WARNING"  #  ()
    BREACHED = "BREACHED"  # 
    CRITICAL = "CRITICAL"  # 


class DynamicLimit:
    """"""

    def __init__(
        self,
        limit_type: str,
        base_value: float,
        adjustment_factors: dict[str, float],
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> None:
        """__init__ method"""
        self.limit_type = limit_type
        self.base_value = base_value
        self.adjustment_factors = adjustment_factors
        self.min_value = min_value or base_value * 0.1
        self.max_value = max_value or base_value * 10.0
        self.current_value = base_value
        self.last_adjustment = int(time.time())

    def calculate_adjusted_value(self, risk_factors: dict[str, float]) -> float:
        """"""
        adjusted_value = self.base_value

        for factor_name, factor_value in risk_factors.items():
            if factor_name in self.adjustment_factors:
                adjustment = self.adjustment_factors[factor_name]
                adjusted_value *= 1 + adjustment * factor_value

        # 
        adjusted_value = max(self.min_value, min(self.max_value, adjusted_value))

        self.current_value = adjusted_value
        self.last_adjustment = int(time.time())

        return adjusted_value
