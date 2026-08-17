""" - 


"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from bt_api_base.logging_factory import get_logger

from ..containers.risk_events import RiskLevel
from ..containers.risk_metrics import RiskMetrics


class RuleType:
    """"""

    # 
    CONDITION_BASED = "condition_based"  # 
    THRESHOLD_BASED = "threshold_based"  # 
    TIME_BASED = "time_based"  # 
    EVENT_BASED = "event_based"  # 

    # 
    AND_RULE = "and_rule"  # AND
    OR_RULE = "or_rule"  # OR
    NOT_RULE = "not_rule"  # NOT

    # 
    ML_PREDICTION = "ml_prediction"  # ML


class ActionType:
    """"""

    # 
    HALT_TRADING = "halt_trading"  # 
    LIMIT_ORDERS = "limit_orders"  # 
    CANCEL_ORDERS = "cancel_orders"  # 
    REDUCE_POSITIONS = "reduce_positions"  # 

    # 
    INCREASE_MARGIN = "increase_margin"  # 
    SEND_ALERT = "send_alert"  # 
    LOG_EVENT = "log_event"  # 
    NOTIFY_MANAGER = "notify_manager"  # 

    # 
    ADJUST_LIMITS = "adjust_limits"  # 
    UPDATE_MODEL = "update_model"  # 
    RUN_STRESS_TEST = "run_stress_test"  # 


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

        Args: data:

        Returns: bool:
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
        else: return False

    def _get_nested_value(self, data: dict[str, Any], field: str) -> Any:
        """"""
        keys = field.split(".")
        value = data

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else: return None

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

        Args: data:

        Returns: bool:
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
        else: return False

    def _evaluate_threshold_conditions(self, data: dict[str, Any]) -> bool:
        """"""
        # 
        return all(condition.evaluate(data) for condition in self.conditions)

    def trigger(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """

        Args: data:

        Returns: List[Dict[str, Any]]:
        """
        self.last_triggered = int(time.time())
        self.trigger_count += 1

        return self.actions


class PolicyEngine:
    """

    :
    1.  - 、、
    2.  - 
    3.  - 
    4.  - 
    5.  - 
    6.  - 
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """

        Args: config:
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

        Args: rule:

        Returns: bool:
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

        Args: rule_id: ID

        Returns: bool:
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

        Args: rule_id: ID
            updates: 

        Returns: bool:
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

        Args: exchange_name:
            account_id: ID
            order_data: 
            risk_metrics: 

        Returns: Dict[str, Any]:
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

        Args: risk_metrics:
            context: 

        Returns: Dict[str, Any]:
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

        Returns: Dict[str, Any]:
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

        Args: data:

        Returns: Tuple[List[Rule], List[Dict[str, Any]]]: (, )
        """
        triggered_rules = []
        actions = []

        for rule_id in self.active_rules[:
            self.max_rules_per_evaluation]:
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

    def _execute_action(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """

        Args: action:
            data: 

        Returns: Dict[str, Any]:
        """
        action_type = action.get("type", "")

        start_time = time.time()

        try:
            if action_type in self.action_handlers:
                result = self.action_handlers[action_type](action, data)
            elif action_type in self.default_actions:
                result = self.default_actions[action_type](action, data)
            else:
                    result = {
                    "success": False,
                    "message": f"Unknown action type: {action_type}",
                }

            execution_time = (time.time() - start_time) * 1000

            return {
                "action_type": action_type,
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "data": result.get("data", {}),
                "execution_time_ms": execution_time,
                "timestamp": int(time.time()),
            }

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000

            return {
                "action_type": action_type,
                "success": False,
                "message": f"Action execution error: {e}",
                "data": {},
                "execution_time_ms": execution_time,
                "timestamp": int(time.time()),
            }

    def _initialize_default_actions(self) -> dict[str, Callable]:
        """"""
        return {
            ActionType.SEND_ALERT: self._action_send_alert,
            ActionType.LOG_EVENT: self._action_log_event,
            ActionType.HALT_TRADING: self._action_halt_trading,
            ActionType.LIMIT_ORDERS: self._action_limit_orders,
            ActionType.INCREASE_MARGIN: self._action_increase_margin,
            ActionType.NOTIFY_MANAGER: self._action_notify_manager,
        }

    def _action_send_alert(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """"""
        alert_level = action.get("level", "MEDIUM")
        message = action.get("message", "Risk alert triggered")

        # 
        self.logger.warning(f"Risk Alert [{alert_level}]: {message}")

        return {
            "success": True,
            "message": f"Alert sent: {message}",
            "data": {
                "level": alert_level,
                "message": message,
                "timestamp": int(time.time()),
            },
        }

    def _action_log_event(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """"""
        event_type = action.get("event_type", "risk_event")
        message = action.get("message", "Risk event logged")

        self.logger.info(f"Risk Event [{event_type}]: {message}")

        return {
            "success": True,
            "message": f"Event logged: {message}",
            "data": {
                "event_type": event_type,
                "message": message,
                "timestamp": int(time.time()),
            },
        }

    def _action_halt_trading(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """"""
        scope = action.get("scope", "account")  # account, symbol, global
        duration = action.get("duration", 3600)  # 

        self.logger.warning(f"Trading halted for {scope}: {duration}s")

        return {
            "success": True,
            "message": f"Trading halted for {scope}",
            "data": {
                "scope": scope,
                "duration": duration,
                "timestamp": int(time.time()),
            },
        }

    def _action_limit_orders(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """"""
        limit_type = action.get("limit_type", "frequency")
        limit_value = action.get("limit_value", 10)

        self.logger.warning(f"Order limit applied: {limit_type} = {limit_value}")

        return {
            "success": True,
            "message": f"Order limit applied: {limit_type}",
            "data": {
                "limit_type": limit_type,
                "limit_value": limit_value,
                "timestamp": int(time.time()),
            },
        }

    def _action_increase_margin(
        self, action: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        increase_amount = action.get("increase_amount", 0.1)  # 10%
        reason = action.get("reason", "Risk mitigation")

        self.logger.warning(f"Margin increased by {increase_amount:.1%}: {reason}")

        return {
            "success": True,
            "message": f"Margin increased by {increase_amount:.1%}",
            "data": {
                "increase_amount": increase_amount,
                "reason": reason,
                "timestamp": int(time.time()),
            },
        }

    def _action_notify_manager(
        self, action: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        """"""
        message = action.get("message", "Risk notification")
        urgency = action.get("urgency", "medium")

        self.logger.error(f"Manager Notification [{urgency}]: {message}")

        return {
            "success": True,
            "message": f"Manager notified: {message}",
            "data": {
                "message": message,
                "urgency": urgency,
                "timestamp": int(time.time()),
            },
        }

    def _update_active_rules(self) -> None:
        """ ()"""
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
