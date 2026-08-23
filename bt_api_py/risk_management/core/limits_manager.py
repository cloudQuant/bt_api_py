"""限额管理门面 -

按检查类别拆分为子模块（order_limits/position_limits/margin_limits/risk_limits/
compliance_limits），本模块保留编排逻辑并通过 mixin 继承。
"""

from __future__ import annotations

import time
from typing import Any, cast

from bt_api_base.logging_factory import get_logger

from ..containers.risk_metrics import RiskMetrics
from .compliance_limits import ComplianceLimitsMixin
from .limits_types import DynamicLimit, LimitStatus, LimitType
from .margin_limits import MarginLimitsMixin
from .order_limits import OrderLimitsMixin
from .position_limits import PositionLimitsMixin
from .risk_limits import RiskLimitsMixin

__all__ = ["DynamicLimit", "LimitStatus", "LimitType", "LimitsManager"]


class LimitsManager(
    OrderLimitsMixin,
    PositionLimitsMixin,
    MarginLimitsMixin,
    RiskLimitsMixin,
    ComplianceLimitsMixin,
):
    """
    限额管理门面，聚合各类限额检查。

    类别:
    1. 订单限额 - OrderLimitsMixin
    2. 持仓限额 - PositionLimitsMixin
    3. 保证金限额 - MarginLimitsMixin
    4. 风险限额 - RiskLimitsMixin
    5. 合规限额 - ComplianceLimitsMixin
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        初始化。

        Args: config: 配置字典
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
        设置静态限额。

        Args: limit_type: 限额类型
            exchange_name: 交易所标识
            account_id: 账户 ID
            value: 限额值
            **kwargs: 附加参数
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
        设置动态限额。

        Args: limit_type: 限额类型
            exchange_name: 交易所标识
            account_id: 账户 ID
            base_value: 基准值
            adjustment_factors: 调整因子
            **kwargs: 附加参数
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
        检查交易前限额。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID
            order_data: 订单数据
            current_metrics: 当前风险指标

        Returns: Dict[str, Any]: 检查结果
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
        检查持仓限额。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID
            position_data: 持仓数据
            current_metrics: 当前风险指标

        Returns: Dict[str, Any]: 检查结果
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
        获取当前限额。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID
            risk_factors: 风险因子（用于动态限额调整）

        Returns: Dict[str, Any]: 当前限额
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
        调整动态限额。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID
            risk_factors: 风险因子
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
        获取限额违约记录。

        Args: exchange_name: 交易所标识（可选）
            account_id: 账户 ID（可选）
            time_window: 时间窗口（秒，可选）

        Returns: List[Dict[str, Any]]: 违约记录
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
        获取限额利用率。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID

        Returns: Dict[str, float]: 限额利用率
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

    def _record_limit_check(self, check_record: dict[str, Any]) -> None:
        """"""
        check_record["timestamp"] = int(time.time())
        self.check_history.append(check_record)

        #
        if len(self.check_history) > 10000:
            self.check_history = self.check_history[-5000:]
