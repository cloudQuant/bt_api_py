""" - 

、、
"""

from __future__ import annotations

import time
from typing import Any, cast

from bt_api_base.logging_factory import get_logger

from ..containers.risk_metrics import RiskMetrics


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


class LimitsManager:
    """

    :
    1.  - 
    2.  - 
    3.  - VaR、
    4.  - 
    5.  - 
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """

        Args: config:
        """
        self.logger = get_logger("limits_manager")
        self.config = config or {}

        # 
        self.static_limits: dict[str, dict[str, Any]] = {}  # 
        self.dynamic_limits: dict[str, DynamicLimit] = {}  # 
        self.user_limits: dict[str, dict[str, Any]] = {}  # 
        self.exchange_limits: dict[str, dict[str, float]] = {}  # 

        # 
        self.check_history: list[dict[str, Any]] = []

        # 
        self.warning_threshold = self.config.get("warning_threshold", 0.8)  #  (80%)
        self.critical_threshold = self.config.get("critical_threshold", 1.0)  #  (100%)
        self.check_cache_ttl = self.config.get("check_cache_ttl", 60)  # 

        # 
        self.check_cache: dict[str, dict[str, Any]] = {}

        # 
        self._initialize_default_limits()

        self.logger.info("LimitsManager initialized")

    def set_static_limit(
        self, limit_type: str, exchange_name: str, account_id: str, value: float, **kwargs
    ) -> None:
        """

        Args: limit_type:
            exchange_name: 
            account_id: ID
            value: 
            **kwargs: 
        """
        key = f"{exchange_name}:{account_id}"

        if key not in self.static_limits:
            self.static_limits[key] = {}

        self.static_limits[key][limit_type] = {
            "value": value,
            "updated_at": int(time.time()),
            **kwargs,
        }

        self.logger.info(f"Static limit set: {limit_type} = {value} for {key}")

    def set_dynamic_limit(
        self,
        limit_type: str,
        exchange_name: str,
        account_id: str,
        base_value: float,
        adjustment_factors: dict[str, float],
        **kwargs,
    ) -> None:
        """

        Args: limit_type:
            exchange_name: 
            account_id: ID
            base_value: 
            adjustment_factors: 
            **kwargs: 
        """
        key = f"{exchange_name}:{account_id}"
        limit_key = f"{key}:{limit_type}"

        dynamic_limit = DynamicLimit(
            limit_type=limit_type,
            base_value=base_value,
            adjustment_factors=adjustment_factors,
            **kwargs,
        )

        self.dynamic_limits[limit_key] = dynamic_limit

        self.logger.info(f"Dynamic limit set: {limit_type} for {key}")

    def check_pre_trade_limits(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        current_metrics: RiskMetrics | None = None,
    ) -> dict[str, Any]:
        """

        Args: exchange_name:
            account_id: ID
            order_data: 
            current_metrics: 

        Returns: Dict[str, Any]:
        """
        cache_key = f"pre_trade:{exchange_name}:{account_id}:{hash(str(order_data))}"

        # 
        if cache_key in self.check_cache:
            cached_result = self.check_cache[cache_key]
            if int(time.time()) - cached_result["timestamp"] < self.check_cache_ttl:
                return cast("dict[str, Any]", cached_result["result"])

        try:
            checks = []
            warnings = []
            restrictions = []
            mitigation_required = False

            # 
            order_size_check = self._check_max_order_size(
                exchange_name, account_id, order_data, current_metrics
            )
            checks.append(order_size_check)

            # 
            frequency_check = self._check_order_frequency(exchange_name, account_id, order_data)
            checks.append(frequency_check)

            # 
            margin_check = self._check_margin_requirement(
                exchange_name, account_id, order_data, current_metrics
            )
            checks.append(margin_check)

            # 
            position_check = self._check_position_limits(
                exchange_name, account_id, order_data, current_metrics
            )
            checks.append(position_check)

            # 
            risk_check = self._check_risk_limits(
                exchange_name, account_id, order_data, current_metrics
            )
            checks.append(risk_check)

            # 
            compliance_check = self._check_compliance_limits(
                exchange_name, account_id, order_data, current_metrics
            )
            checks.append(compliance_check)

            # 
            approved = True
            for check in checks:
                if check["status"] in [LimitStatus.BREACHED, LimitStatus.CRITICAL]:
                    approved = False
                    mitigation_required = True
                    restrictions.append(check["restriction"])
                elif check["status"] == LimitStatus.WARNING:
                    warnings.append(check["warning"])

            result = {
                "approved": approved,
                "warnings": warnings,
                "restrictions": restrictions,
                "mitigation_required": mitigation_required,
                "detailed_checks": checks,
                "timestamp": int(time.time()),
            }

            # 
            self.check_cache[cache_key] = {
                "result": result,
                "timestamp": int(time.time()),
            }

            # 
            self._record_limit_check(
                {
                    "type": "pre_trade",
                    "exchange_name": exchange_name,
                    "account_id": account_id,
                    "order_data": order_data,
                    "result": result,
                }
            )

            return result

        except Exception as e:
            self.logger.error(f"Error checking pre-trade limits: {e}")
            return {
                "approved": False,
                "warnings": [f"Limit check error: {e}"],
                "restrictions": ["system_error"],
                "mitigation_required": True,
                "detailed_checks": [],
                "timestamp": int(time.time()),
            }

    def check_position_limits(
        self,
        exchange_name: str,
        account_id: str,
        position_data: dict[str, Any],
        current_metrics: RiskMetrics | None = None,
    ) -> dict[str, Any]:
        """

        Args: exchange_name:
            account_id: ID
            position_data: 
            current_metrics: 

        Returns: Dict[str, Any]:
        """
        checks = []
        warnings = []

        try:
            # 
            max_position_check = self._check_max_position_size(
                exchange_name, account_id, position_data
            )
            checks.append(max_position_check)

            # 
            notional_check = self._check_notional_exposure(exchange_name, account_id, position_data)
            checks.append(notional_check)

            # 
            leverage_check = self._check_leverage_limit(exchange_name, account_id, position_data)
            checks.append(leverage_check)

            # 
            concentration_check = self._check_concentration_limit(
                exchange_name, account_id, position_data
            )
            checks.append(concentration_check)

            # 
            approved = True
            for check in checks:
                if check["status"] in [LimitStatus.BREACHED, LimitStatus.CRITICAL]:
                    approved = False
                elif check["status"] == LimitStatus.WARNING:
                    warnings.append(check["warning"])

            return {
                "approved": approved,
                "warnings": warnings,
                "detailed_checks": checks,
                "timestamp": int(time.time()),
            }

        except Exception as e:
            self.logger.error(f"Error checking position limits: {e}")
            return {
                "approved": False,
                "warnings": [f"Position limit check error: {e}"],
                "detailed_checks": [],
                "timestamp": int(time.time()),
            }

    def get_current_limits(
        self, exchange_name: str, account_id: str, risk_factors: dict[str, float] | None = None
    ) -> dict[str, Any]:
        """

        Args: exchange_name:
            account_id: ID
            risk_factors:  ()

        Returns: Dict[str, Any]:
        """
        key = f"{exchange_name}:{account_id}"
        current_limits: dict[str, Any] = {}

        # 
        if key in self.static_limits:
            current_limits.update(self.static_limits[key])

        # 
        for limit_key, dynamic_limit in self.dynamic_limits.items():
            if limit_key.startswith(key):
                limit_type = limit_key.split(":")[-1]

                if risk_factors:
                    adjusted_value = dynamic_limit.calculate_adjusted_value(risk_factors)
                else:
                    adjusted_value = dynamic_limit.current_value

                current_limits[limit_type] = {
                    "value": adjusted_value,
                    "base_value": dynamic_limit.base_value,
                    "is_dynamic": True,
                    "last_adjustment": dynamic_limit.last_adjustment,
                }

        # 
        if key in self.user_limits:
            current_limits.update(self.user_limits[key])

        # 
        if exchange_name in self.exchange_limits:
            current_limits.update(self.exchange_limits[exchange_name])

        return current_limits

    def adjust_dynamic_limits(
        self, exchange_name: str, account_id: str, risk_factors: dict[str, float]
    ) -> None:
        """

        Args: exchange_name:
            account_id: ID
            risk_factors: 
        """
        key = f"{exchange_name}:{account_id}"

        for limit_key, dynamic_limit in self.dynamic_limits.items():
            if limit_key.startswith(key):
                dynamic_limit.calculate_adjusted_value(risk_factors)

        self.logger.info(f"Dynamic limits adjusted for {key}")

    def get_limit_breaches(
        self,
        exchange_name: str | None = None,
        account_id: str | None = None,
        time_window: int | None = None,
    ) -> list[dict[str, Any]]:
        """

        Args: exchange_name:
            account_id: ID
            time_window:  ()

        Returns: List[Dict[str, Any]]:
        """
        breaches = []
        current_time = int(time.time())

        for check_record in self.check_history:
            # 
            if exchange_name and check_record.get("exchange_name") != exchange_name:
                continue
            if account_id and check_record.get("account_id") != account_id:
                continue
            if time_window and current_time - check_record.get("timestamp", 0) > time_window:
                continue

            result = check_record.get("result", {})
            detailed_checks = result.get("detailed_checks", [])

            for check in detailed_checks:
                if check.get("status") in [LimitStatus.BREACHED, LimitStatus.CRITICAL]:
                    breaches.append(
                        {
                            "timestamp": check_record.get("timestamp"),
                            "exchange_name": check_record.get("exchange_name"),
                            "account_id": check_record.get("account_id"),
                            "limit_type": check.get("limit_type"),
                            "current_value": check.get("current_value"),
                            "limit_value": check.get("limit_value"),
                            "utilization_ratio": check.get("utilization_ratio"),
                            "status": check.get("status"),
                        }
                    )

        return sorted(breaches, key=lambda x: x["timestamp"], reverse=True)

    def get_limit_utilization(self, exchange_name: str, account_id: str) -> dict[str, float]:
        """

        Args: exchange_name:
            account_id: ID

        Returns: Dict[str, float]:
        """
        utilization: dict[str, float] = {}

        # 
        recent_checks = [
            check
            for check in self.check_history
            if (
                check.get("exchange_name") == exchange_name
                and check.get("account_id") == account_id
                and int(time.time()) - check.get("timestamp", 0) < 3600
            )  # 1
        ]

        for check_record in recent_checks:
            detailed_checks = check_record.get("result", {}).get("detailed_checks", [])
            for check in detailed_checks:
                limit_type = check.get("limit_type")
                util_ratio = check.get("utilization_ratio", 0)

                if (
                    limit_type
                    and limit_type not in utilization
                    or limit_type
                    and util_ratio > utilization[limit_type]
                ):
                    utilization[limit_type] = util_ratio

        return utilization

    # 

    def _initialize_default_limits(self) -> None:
        """"""
        # 
        default_pre_trade_limits = {
            LimitType.MAX_ORDER_SIZE: 1000000,  # 100
            LimitType.MAX_ORDERS_PER_MINUTE: 60,
            LimitType.MAX_ORDERS_PER_DAY: 10000,
            LimitType.MIN_MARGIN_REQUIREMENT: 0.1,  # 10%
        }

        # 
        default_position_limits = {
            LimitType.MAX_POSITION_SIZE: 10000000,  # 1000
            LimitType.MAX_NOTIONAL_EXPOSURE: 50000000,  # 5000
            LimitType.MAX_LEVERAGE: 10.0,
            LimitType.MAX_CONCENTRATION: 0.3,  # 30%
        }

        # 
        default_risk_limits = {
            LimitType.MAX_VAR: 1000000,  # 100
            LimitType.MAX_DRAWDOWN: 0.2,  # 20%
            LimitType.MAX_CORRELATION: 0.9,  # 90%
            LimitType.MIN_LIQUIDITY: 0.6,  # 60%
        }

        # 
        all_default_limits = {
            **default_pre_trade_limits,
            **default_position_limits,
            **default_risk_limits,
        }

        self.logger.info(f"Initialized {len(all_default_limits)} default limits")

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
        else: return {
                "limit_type": "risk_limits",
                "status": LimitStatus.WITHIN_LIMIT,
                "warning": "",
                "restriction": "",
            }

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

    def _record_limit_check(self, check_record: dict[str, Any]) -> None:
        """"""
        check_record["timestamp"] = int(time.time())
        self.check_history.append(check_record)

        # 
        if len(self.check_history) > 10000:
            self.check_history = self.check_history[-5000:]
