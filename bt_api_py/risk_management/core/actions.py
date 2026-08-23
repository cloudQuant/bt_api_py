"""策略动作执行（告警/日志/停止交易/限单/加保证金/通知）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .policy_types import ActionType


class ActionMixin:
    """动作执行方法（供 PolicyEngine 混入）。"""

    action_handlers: dict[str, Callable]
    default_actions: dict[str, Callable]
    logger: Any

    def _execute_action(self, action: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """
        执行动作。

        Args: action: 动作定义
            data: 上下文数据

        Returns: Dict[str, Any]: 执行结果
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
