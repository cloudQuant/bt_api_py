"""策略引擎门面 -

规则条件与动作执行分离（动作执行拆到 actions.py），本模块保留规则定义与编排逻辑。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from bt_api_base.logging_factory import get_logger

from ..containers.risk_events import RiskLevel
from ..containers.risk_metrics import RiskMetrics
from .actions import ActionMixin
from .policy_types import ActionType, RuleType

__all__ = ["ActionType", "PolicyEngine", "Rule", "RuleCondition", "RuleType"]


class RuleCondition:
    """"""

    def __init__(self, field: str, operator: str, value: Any, description: str = "") -> None:
        """__init__ method"""
        self.field = field
        self.operator = operator  # eq, ne, gt, gte, lt, lte, in, contains
        self.value = value
        self.description = description

    def evaluate(self, data: dict[str, Any]) -> bool:
        """
        评估条件。

        Args: data: 上下文数据

        Returns: bool: 是否满足
        """
        field_value = self._get_nested_value(data, self.field)

        if self.operator == "eq":
            return bool(field_value == self.value)
        elif self.operator == "ne":
            return bool(field_value != self.value)
        elif self.operator == "gt":
            return float(field_value) > float(str(self.value))
        elif self.operator == "gte":
            return float(field_value) >= float(str(self.value))
        elif self.operator == "lt":
            return float(field_value) < float(str(self.value))
        elif self.operator == "lte":
            return float(field_value) <= float(str(self.value))
        elif self.operator == "in":
            return field_value in self.value
        elif self.operator == "contains":
            return self.value in str(field_value)
        else:
            return False

    def _get_nested_value(self, data: dict[str, Any], field: str) -> Any:
        """"""
        keys = field.split(".")
        value = data

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None

        return value


class Rule:
    """"""

    def __init__(
        self,
        rule_id: str,
        name: str,
        description: str,
        conditions: list[RuleCondition],
        actions: list[dict[str, Any]],
        rule_type: str = RuleType.CONDITION_BASED,
        enabled: bool = True,
        priority: int = 0,
        cooldown: int = 0,  #  ()
    ):
        """__init__ method"""
        self.rule_id = rule_id
        self.name = name
        self.description = description
        self.conditions = conditions
        self.actions = actions
        self.rule_type = rule_type
        self.enabled = enabled
        self.priority = priority
        self.cooldown = cooldown
        self.last_triggered = 0
        self.trigger_count = 0
        self.created_at = int(time.time())

    def evaluate(self, data: dict[str, Any]) -> bool:
        """
        评估规则。

        Args: data: 上下文数据

        Returns: bool: 是否触发
        """
        if not self.enabled:
            return False

        #
        current_time = int(time.time())
        if current_time - self.last_triggered < self.cooldown:
            return False

        #
        if self.rule_type == RuleType.CONDITION_BASED:
            return all(condition.evaluate(data) for condition in self.conditions)
        elif self.rule_type == RuleType.THRESHOLD_BASED:
            return self._evaluate_threshold_conditions(data)
        else:
            return False

    def _evaluate_threshold_conditions(self, data: dict[str, Any]) -> bool:
        """"""
        #
        return all(condition.evaluate(data) for condition in self.conditions)

    def trigger(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """
        触发规则。

        Args: data: 上下文数据

        Returns: List[Dict[str, Any]]: 动作列表
        """
        self.last_triggered = int(time.time())
        self.trigger_count += 1

        return self.actions


class PolicyEngine(ActionMixin):
    """
    策略引擎门面。

    能力:
    1. 规则管理 - 添加/删除/更新规则
    2. 条件评估 - 规则条件匹配
    3. 动作执行 - ActionMixin
    4. 性能统计 - 命中率等
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        初始化。

        Args: config: 配置字典
        """
        self.logger = get_logger("policy_engine")
        self.config = config or {}

        #
        self.rules: dict[str, Rule] = {}
        self.rule_groups: dict[str, set[str]] = {}  #
        self.active_rules: list[str] = []  # ID

        #
        self.action_handlers: dict[str, Callable] = {}
        self.default_actions = self._initialize_default_actions()

        #
        self.execution_history: list[dict[str, Any]] = []

        #
        self.max_rules_per_evaluation = self.config.get("max_rules_per_evaluation", 100)
        self.execution_timeout = self.config.get("execution_timeout", 5.0)  #
        self.enable_rule_cache = self.config.get("enable_rule_cache", True)

        #
        self.performance_stats: dict[str, Any] = {
            "total_evaluations": 0,
            "total_triggers": 0,
            "total_actions": 0,
            "average_evaluation_time_ms": 0.0,
            "rule_hit_rates": {},
        }

        #
        self._initialize_default_rules()

        self.logger.info("PolicyEngine initialized")

    def add_rule(self, rule: Rule) -> bool:
        """
        添加规则。

        Args: rule: 规则对象

        Returns: bool: 是否成功
        """
        try:
            if rule.rule_id in self.rules:
                self.logger.warning(f"Rule {rule.rule_id} already exists, updating...")

            self.rules[rule.rule_id] = rule

            #  ()
            self._update_active_rules()

            self.logger.info(f"Rule added: {rule.rule_id} - {rule.name}")
            return True

        except Exception as e:
            self.logger.error(f"Error adding rule {rule.rule_id}: {e}")
            return False

    def remove_rule(self, rule_id: str) -> bool:
        """
        删除规则。

        Args: rule_id: 规则 ID

        Returns: bool: 是否成功
        """
        try:
            if rule_id in self.rules:
                del self.rules[rule_id]

                #
                self._update_active_rules()

                #
                for rule_ids in self.rule_groups.values():
                    if rule_id in rule_ids:
                        rule_ids.remove(rule_id)

                self.logger.info(f"Rule removed: {rule_id}")
                return True
            else:
                self.logger.warning(f"Rule {rule_id} not found")
                return False

        except Exception as e:
            self.logger.error(f"Error removing rule {rule_id}: {e}")
            return False

    def update_rule(self, rule_id: str, updates: dict[str, Any]) -> bool:
        """
        更新规则。

        Args: rule_id: 规则 ID
            updates: 更新字段

        Returns: bool: 是否成功
        """
        try:
            if rule_id not in self.rules:
                self.logger.error(f"Rule {rule_id} not found")
                return False

            rule = self.rules[rule_id]

            #
            for field, value in updates.items():
                if hasattr(rule, field):
                    setattr(rule, field, value)

            #
            self._update_active_rules()

            self.logger.info(f"Rule updated: {rule_id}")
            return True

        except Exception as e:
            self.logger.error(f"Error updating rule {rule_id}: {e}")
            return False

    def evaluate_order_policy(
        self,
        exchange_name: str,
        account_id: str,
        order_data: dict[str, Any],
        risk_metrics: RiskMetrics | None = None,
    ) -> dict[str, Any]:
        """
        评估订单策略。

        Args: exchange_name: 交易所标识
            account_id: 账户 ID
            order_data: 订单数据
            risk_metrics: 风险指标

        Returns: Dict[str, Any]: 评估结果
        """
        start_time = time.time()

        try:
            #
            evaluation_data = {
                "exchange_name": exchange_name,
                "account_id": account_id,
                "order_data": order_data,
                "risk_metrics": risk_metrics.__dict__ if risk_metrics else {},
                "timestamp": int(time.time()),
                "evaluation_type": "order_policy",
            }

            #
            triggered_rules, actions = self._evaluate_rules(evaluation_data)

            #
            execution_results = []
            for action in actions:
                result = self._execute_action(action, evaluation_data)
                execution_results.append(result)

            #
            approved = not any(
                result.get("action_type") in [ActionType.HALT_TRADING, ActionType.CANCEL_ORDERS]
                and not result.get("success", False)
                for result in execution_results
            )

            warnings = [
                result.get("message", "")
                for result in execution_results
                if result.get("action_type") == ActionType.SEND_ALERT
            ]

            restrictions = [
                result.get("message", "")
                for result in execution_results
                if result.get("action_type") in [ActionType.LIMIT_ORDERS, ActionType.HALT_TRADING]
            ]

            evaluation_time = (time.time() - start_time) * 1000
            self._update_performance_stats(
                len(self.active_rules), len(triggered_rules), evaluation_time
            )

            result = {
                "approved": approved,
                "warnings": warnings,
                "restrictions": restrictions,
                "mitigation_required": len(triggered_rules) > 0,
                "triggered_rules": [rule.rule_id for rule in triggered_rules],
                "actions_executed": len(execution_results),
                "execution_results": execution_results,
                "evaluation_time_ms": evaluation_time,
            }

            #
            self._record_execution(
                {
                    "type": "order_policy",
                    "exchange_name": exchange_name,
                    "account_id": account_id,
                    "order_id": order_data.get("order_id", ""),
                    "result": result,
                    "triggered_rules": len(triggered_rules),
                    "execution_time": evaluation_time,
                }
            )

            return result

        except Exception as e:
            self.logger.error(f"Error evaluating order policy: {e}")
            return {
                "approved": False,
                "warnings": [f"Policy evaluation error: {e}"],
                "restrictions": ["system_error"],
                "mitigation_required": True,
                "triggered_rules": [],
                "actions_executed": 0,
                "execution_results": [],
                "evaluation_time_ms": (time.time() - start_time) * 1000,
            }

    def evaluate_risk_policy(
        self, risk_metrics: RiskMetrics, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        评估风险策略。

        Args: risk_metrics: 风险指标
            context: 上下文

        Returns: Dict[str, Any]: 评估结果
        """
        start_time = time.time()

        try:
            #
            evaluation_data = {
                "risk_metrics": risk_metrics.__dict__,
                "context": context or {},
                "timestamp": int(time.time()),
                "evaluation_type": "risk_policy",
            }

            #
            triggered_rules, actions = self._evaluate_rules(evaluation_data)

            #
            execution_results = []
            for action in actions:
                result = self._execute_action(action, evaluation_data)
                execution_results.append(result)

            evaluation_time = (time.time() - start_time) * 1000
            self._update_performance_stats(
                len(self.active_rules), len(triggered_rules), evaluation_time
            )

            result = {
                "triggered_rules": [rule.rule_id for rule in triggered_rules],
                "actions_executed": len(execution_results),
                "execution_results": execution_results,
                "evaluation_time_ms": evaluation_time,
                "risk_level": risk_metrics.risk_level,
                "risk_score": float(risk_metrics.overall_risk_score),
            }

            #
            self._record_execution(
                {
                    "type": "risk_policy",
                    "exchange_name": risk_metrics.exchange_name,
                    "account_id": risk_metrics.account_id,
                    "result": result,
                    "triggered_rules": len(triggered_rules),
                    "execution_time": evaluation_time,
                }
            )

            return result

        except Exception as e:
            self.logger.error(f"Error evaluating risk policy: {e}")
            return {
                "triggered_rules": [],
                "actions_executed": 0,
                "execution_results": [],
                "evaluation_time_ms": (time.time() - start_time) * 1000,
                "error": str(e),
            }

    def get_rule_statistics(self) -> dict[str, Any]:
        """
        获取规则统计。

        Returns: Dict[str, Any]: 统计信息
        """
        rule_stats = {}

        for rule_id, rule in self.rules.items():
            rule_stats[rule_id] = {
                "name": rule.name,
                "description": rule.description,
                "enabled": rule.enabled,
                "priority": rule.priority,
                "trigger_count": rule.trigger_count,
                "last_triggered": rule.last_triggered,
                "cooldown": rule.cooldown,
                "created_at": rule.created_at,
            }

        return {
            "total_rules": len(self.rules),
            "active_rules": len(self.active_rules),
            "rule_groups": {
                group_id: len(rule_ids) for group_id, rule_ids in self.rule_groups.items()
            },
            "rule_details": rule_stats,
            "performance_stats": self.performance_stats,
            "execution_history_size": len(self.execution_history),
        }

    #

    def _evaluate_rules(self, data: dict[str, Any]) -> tuple[list[Rule], list[dict[str, Any]]]:
        """
        评估所有规则。

        Args: data: 上下文数据

        Returns: Tuple[List[Rule], List[Dict[str, Any]]]: (触发规则, 动作列表)
        """
        triggered_rules = []
        actions = []

        for rule_id in self.active_rules[: self.max_rules_per_evaluation]:
            if rule_id not in self.rules:
                continue

            rule = self.rules[rule_id]

            try:
                if rule.evaluate(data):
                    triggered_rules.append(rule)
                    rule_actions = rule.trigger(data)
                    actions.extend(rule_actions)

                    self.logger.debug(f"Rule triggered: {rule_id} - {rule.name}")

            except Exception as e:
                self.logger.error(f"Error evaluating rule {rule_id}: {e}")

        return triggered_rules, actions

    def _update_active_rules(self) -> None:
        """更新活动规则列表（按优先级排序）"""
        enabled_rules = [rule for rule in self.rules.values() if rule.enabled]
        self.active_rules = sorted(
            [rule.rule_id for rule in enabled_rules],
            key=lambda rule_id: self.rules[rule_id].priority,
            reverse=True,
        )

    def _update_performance_stats(
        self, rules_evaluated: int, rules_triggered: int, evaluation_time: float
    ) -> None:
        """"""
        self.performance_stats["total_evaluations"] += 1
        self.performance_stats["total_triggers"] += rules_triggered

        #
        current_avg = self.performance_stats["average_evaluation_time_ms"]
        new_avg = current_avg * 0.9 + evaluation_time * 0.1
        self.performance_stats["average_evaluation_time_ms"] = new_avg

        #
        if rules_evaluated > 0:
            hit_rate = rules_triggered / rules_evaluated
            #  -
            self.performance_stats["rule_hit_rates"]["overall"] = (
                self.performance_stats["rule_hit_rates"].get("overall", 0) * 0.9 + hit_rate * 0.1
            )

    def _record_execution(self, execution_record: dict[str, Any]) -> None:
        """"""
        execution_record["timestamp"] = int(time.time())
        self.execution_history.append(execution_record)

        #
        if len(self.execution_history) > 10000:
            self.execution_history = self.execution_history[-5000:]

    def _initialize_default_rules(self) -> None:
        """"""
        #
        high_risk_rule = Rule(
            rule_id="high_risk_halt_trading",
            name="High Risk Trading Halt",
            description="Halt trading when risk level is CRITICAL",
            conditions=[
                RuleCondition("risk_metrics.risk_level", "eq", RiskLevel.CRITICAL.value),
            ],
            actions=[
                {
                    "type": ActionType.HALT_TRADING,
                    "scope": "account",
                    "duration": 3600,
                    "message": "Critical risk level detected, trading halted",
                },
                {
                    "type": ActionType.SEND_ALERT,
                    "level": "CRITICAL",
                    "message": "Critical risk level triggered trading halt",
                },
            ],
            rule_type=RuleType.CONDITION_BASED,
            priority=100,
            cooldown=300,  # 5
        )

        #
        margin_rule = Rule(
            rule_id="insufficient_margin",
            name="Insufficient Margin",
            description="Increase margin requirement when margin utilization is high",
            conditions=[
                RuleCondition("risk_metrics.credit_risk.credit_utilization", "gt", 0.8),
            ],
            actions=[
                {
                    "type": ActionType.INCREASE_MARGIN,
                    "increase_amount": 0.2,
                    "reason": "High margin utilization detected",
                },
                {
                    "type": ActionType.SEND_ALERT,
                    "level": "HIGH",
                    "message": "High margin utilization, margin requirement increased",
                },
            ],
            rule_type=RuleType.CONDITION_BASED,
            priority=80,
            cooldown=600,  # 10
        )

        #
        volatility_rule = Rule(
            rule_id="high_volatility_alert",
            name="High Volatility Alert",
            description="Send alert when market volatility is unusually high",
            conditions=[
                RuleCondition("risk_metrics.market_risk.volatility", "gt", 0.5),
            ],
            actions=[
                {
                    "type": ActionType.SEND_ALERT,
                    "level": "MEDIUM",
                    "message": "High market volatility detected",
                },
                {
                    "type": ActionType.LOG_EVENT,
                    "event_type": "high_volatility",
                    "message": "Market volatility exceeded threshold",
                },
            ],
            rule_type=RuleType.CONDITION_BASED,
            priority=60,
            cooldown=1800,  # 30
        )

        #
        self.add_rule(high_risk_rule)
        self.add_rule(margin_rule)
        self.add_rule(volatility_rule)

        self.logger.info(f"Initialized {len(self.rules)} default rules")
