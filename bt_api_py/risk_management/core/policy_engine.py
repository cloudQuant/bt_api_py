"""策略引擎 - 执行风险管理策略和规则

基于条件和触发器执行预定义的风险管理策略
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from bt_api_py.logging_factory import get_logger

from ..containers.risk_events import RiskLevel
from ..containers.risk_metrics import RiskMetrics


class RuleType:
    """规则类型常量"""

    # 条件规则
    CONDITION_BASED = "condition_based"  # 基于条件的规则
    THRESHOLD_BASED = "threshold_based"  # 基于阈值的规则
    TIME_BASED = "time_based"  # 基于时间的规则
    EVENT_BASED = "event_based"  # 基于事件的规则

    # 复合规则
    AND_RULE = "and_rule"  # AND逻辑规则
    OR_RULE = "or_rule"  # OR逻辑规则
    NOT_RULE = "not_rule"  # NOT逻辑规则

    # 机器学习规则
    ML_PREDICTION = "ml_prediction"  # 基于ML预测的规则


class ActionType:
    """动作类型常量"""

    # 交易动作
    HALT_TRADING = "halt_trading"  # 暂停交易
    LIMIT_ORDERS = "limit_orders"  # 限制订单
    CANCEL_ORDERS = "cancel_orders"  # 撤销订单
    REDUCE_POSITIONS = "reduce_positions"  # 减少仓位

    # 风险管理动作
    INCREASE_MARGIN = "increase_margin"  # 增加保证金
    SEND_ALERT = "send_alert"  # 发送告警
    LOG_EVENT = "log_event"  # 记录事件
    NOTIFY_MANAGER = "notify_manager"  # 通知管理员

    # 系统动作
    ADJUST_LIMITS = "adjust_limits"  # 调整限制
    UPDATE_MODEL = "update_model"  # 更新模型
    RUN_STRESS_TEST = "run_stress_test"  # 运行压力测试


class RuleCondition:
    """规则条件"""

    def __init__(self, field: str, operator: str, value: Any, description: str = "") -> None:
        self.field = field
        self.operator = operator  # eq, ne, gt, gte, lt, lte, in, contains
        self.value = value
        self.description = description

    def evaluate(self, data: dict[str, Any]) -> bool:
        """评估条件

        Args:
            data: 评估数据

        Returns:
            bool: 条件是否满足
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
        """获取嵌套字段值"""
        keys = field.split(".")
        value = data

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None

        return value


class Rule:
    """风险规则"""

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
        cooldown: int = 0,  # 冷却时间 (秒)
    ):
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
        """评估规则

        Args:
            data: 评估数据

        Returns:
            bool: 规则是否触发
        """
        if not self.enabled:
            return False

        # 检查冷却时间
        current_time = int(time.time())
        if current_time - self.last_triggered < self.cooldown:
            return False

        # 评估条件
        if self.rule_type == RuleType.CONDITION_BASED:
            return all(condition.evaluate(data) for condition in self.conditions)
        elif self.rule_type == RuleType.THRESHOLD_BASED:
            return self._evaluate_threshold_conditions(data)
        else:
            return False

    def _evaluate_threshold_conditions(self, data: dict[str, Any]) -> bool:
        """评估阈值条件"""
        # 简化实现
        return all(condition.evaluate(data) for condition in self.conditions)

    def trigger(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """触发规则

        Args:
            data: 触发数据

        Returns:
            List[Dict[str, Any]]: 执行的动作列表
        """
        self.last_triggered = int(time.time())
        self.trigger_count += 1

        return self.actions


class PolicyEngine:
    """策略引擎

    风险策略执行引擎:
    1. 规则管理 - 创建、更新、删除规则
    2. 条件评估 - 实时评估规则条件
    3. 动作执行 - 执行预定义的风险管理动作
    4. 优先级管理 - 规则优先级和冲突解决
    5. 冷却机制 - 防止规则频繁触发
    6. 性能监控 - 规则执行性能统计
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化策略引擎

        Args:
            config: 引擎配置
        """
        self.logger = get_logger("policy_engine")
        self.config = config or {}

        # 规则存储
        self.rules: dict[str, Rule] = {}
        self.rule_groups: dict[str, set[str]] = {}  # 规则分组
        self.active_rules: list[str] = []  # 当前活跃规则ID列表

        # 动作处理器
        self.action_handlers: dict[str, Callable] = {}
        self.default_actions = self._initialize_default_actions()

        # 执行历史
        self.execution_history: list[dict[str, Any]] = []

        # 配置参数
        self.max_rules_per_evaluation = self.config.get("max_rules_per_evaluation", 100)
        self.execution_timeout = self.config.get("execution_timeout", 5.0)  # 秒
        self.enable_rule_cache = self.config.get("enable_rule_cache", True)

        # 性能统计
        self.performance_stats: dict[str, Any] = {
            "total_evaluations": 0,
            "total_triggers": 0,
            "total_actions": 0,
            "average_evaluation_time_ms": 0.0,
            "rule_hit_rates": {},
        }

        # 初始化默认规则
        self._initialize_default_rules()

        self.logger.info("PolicyEngine initialized")

    def add_rule(self, rule: Rule) -> bool:
        """添加规则

        Args:
            rule: 规则对象

        Returns:
            bool: 是否添加成功
        """
        try:
            if rule.rule_id in self.rules:
                self.logger.warning(f"Rule {rule.rule_id} already exists, updating...")

            self.rules[rule.rule_id] = rule

            # 更新活跃规则列表 (按优先级排序)
            self._update_active_rules()

            self.logger.info(f"Rule added: {rule.rule_id} - {rule.name}")
            return True

        except Exception as e:
            self.logger.error(f"Error adding rule {rule.rule_id}: {e}")
            return False

    def remove_rule(self, rule_id: str) -> bool:
        """删除规则

        Args:
            rule_id: 规则ID

        Returns:
            bool: 是否删除成功
        """
        try:
            if rule_id in self.rules:
                del self.rules[rule_id]

                # 更新活跃规则列表
                self._update_active_rules()

                # 从规则组中移除
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
        """更新规则

        Args:
            rule_id: 规则ID
            updates: 更新字段

        Returns:
            bool: 是否更新成功
        """
        try:
            if rule_id not in self.rules:
                self.logger.error(f"Rule {rule_id} not found")
                return False

            rule = self.rules[rule_id]

            # 更新字段
            for field, value in updates.items():
                if hasattr(rule, field):
                    setattr(rule, field, value)

            # 更新活跃规则列表
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
        """评估订单策略

        Args:
            exchange_name: 交易所名称
            account_id: 账户ID
            order_data: 订单数据
            risk_metrics: 风险指标

        Returns:
            Dict[str, Any]: 评估结果
        """
        start_time = time.time()

        try:
            # 准备评估数据
            evaluation_data = {
                "exchange_name": exchange_name,
                "account_id": account_id,
                "order_data": order_data,
                "risk_metrics": risk_metrics.__dict__ if risk_metrics else {},
                "timestamp": int(time.time()),
                "evaluation_type": "order_policy",
            }

            # 评估规则
            triggered_rules, actions = self._evaluate_rules(evaluation_data)

            # 执行动作
            execution_results = []
            for action in actions:
                result = self._execute_action(action, evaluation_data)
                execution_results.append(result)

            # 生成评估结果
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

            # 记录执行历史
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
        """评估风险策略

        Args:
            risk_metrics: 风险指标
            context: 上下文信息

        Returns:
            Dict[str, Any]: 评估结果
        """
        start_time = time.time()

        try:
            # 准备评估数据
            evaluation_data = {
                "risk_metrics": risk_metrics.__dict__,
                "context": context or {},
                "timestamp": int(time.time()),
                "evaluation_type": "risk_policy",
            }

            # 评估规则
            triggered_rules, actions = self._evaluate_rules(evaluation_data)

            # 执行动作
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

            # 记录执行历史
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
        """获取规则统计信息

        Returns:
            Dict[str, Any]: 统计信息
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

    # 私有方法

    def _evaluate_rules(self, data: dict[str, Any]) -> tuple[list[Rule], list[dict[str, Any]]]:
        """评估规则

        Args:
            data: 评估数据

        Returns:
            Tuple[List[Rule], List[Dict[str, Any]]]: (触发的规则, 执行的动作)
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

    def _execute_action(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """执行动作

        Args:
            action: 动作配置
            data: 上下文数据

        Returns:
            Dict[str, Any]: 执行结果
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
        """初始化默认动作处理器"""
        return {
            ActionType.SEND_ALERT: self._action_send_alert,
            ActionType.LOG_EVENT: self._action_log_event,
            ActionType.HALT_TRADING: self._action_halt_trading,
            ActionType.LIMIT_ORDERS: self._action_limit_orders,
            ActionType.INCREASE_MARGIN: self._action_increase_margin,
            ActionType.NOTIFY_MANAGER: self._action_notify_manager,
        }

    def _action_send_alert(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """发送告警动作"""
        alert_level = action.get("level", "MEDIUM")
        message = action.get("message", "Risk alert triggered")

        # 记录告警
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
        """记录事件动作"""
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
        """暂停交易动作"""
        scope = action.get("scope", "account")  # account, symbol, global
        duration = action.get("duration", 3600)  # 秒

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
        """限制订单动作"""
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
        """增加保证金动作"""
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
        """通知管理员动作"""
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
        """更新活跃规则列表 (按优先级排序)"""
        enabled_rules = [rule for rule in self.rules.values() if rule.enabled]
        self.active_rules = sorted(
            [rule.rule_id for rule in enabled_rules],
            key=lambda rule_id: self.rules[rule_id].priority,
            reverse=True,
        )

    def _update_performance_stats(
        self, rules_evaluated: int, rules_triggered: int, evaluation_time: float
    ) -> None:
        """更新性能统计"""
        self.performance_stats["total_evaluations"] += 1
        self.performance_stats["total_triggers"] += rules_triggered

        # 更新平均评估时间
        current_avg = self.performance_stats["average_evaluation_time_ms"]
        new_avg = current_avg * 0.9 + evaluation_time * 0.1
        self.performance_stats["average_evaluation_time_ms"] = new_avg

        # 更新规则命中率
        if rules_evaluated > 0:
            hit_rate = rules_triggered / rules_evaluated
            # 简化统计 - 实际应该按规则分别统计
            self.performance_stats["rule_hit_rates"]["overall"] = (
                self.performance_stats["rule_hit_rates"].get("overall", 0) * 0.9 + hit_rate * 0.1
            )

    def _record_execution(self, execution_record: dict[str, Any]) -> None:
        """记录执行历史"""
        execution_record["timestamp"] = int(time.time())
        self.execution_history.append(execution_record)

        # 限制历史记录大小
        if len(self.execution_history) > 10000:
            self.execution_history = self.execution_history[-5000:]

    def _initialize_default_rules(self) -> None:
        """初始化默认规则"""
        # 高风险暂停交易规则
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
            cooldown=300,  # 5分钟冷却
        )

        # 保证金不足规则
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
            cooldown=600,  # 10分钟冷却
        )

        # 异常波动规则
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
            cooldown=1800,  # 30分钟冷却
        )

        # 添加默认规则
        self.add_rule(high_risk_rule)
        self.add_rule(margin_rule)
        self.add_rule(volatility_rule)

        self.logger.info(f"Initialized {len(self.rules)} default rules")
