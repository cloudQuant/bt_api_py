"""
事件总线 — 提供发布/订阅模式的事件分发机制
支持 Queue 模式（现有行为）和 Callback 模式（适配 CTP SPI / IB EWrapper 等回调驱动 API）
"""

import threading
import traceback
from collections import defaultdict
from collections.abc import Callable
from enum import Enum
from typing import Any

from bt_api_py.exceptions import BtApiError
from bt_api_py.logging_factory import get_logger

__all__ = ["EventBus", "ErrorHandlerMode", "ErrorSeverity"]


class ErrorHandlerMode(Enum):
    """事件处理错误策略"""

    LOG = "log"  # 记录日志并继续（默认）
    RAISE = "raise"  # 立即抛出异常
    COLLECT = "collect"  # 收集所有错误，最后统一处理


class ErrorSeverity(Enum):
    """错误严重程度分类"""

    USER_ERROR = "user_error"  # 用户代码错误（TypeError, ValueError 等）
    BUSINESS_ERROR = "business_error"  # 业务逻辑错误（BtApiError 子类）
    SYSTEM_ERROR = "system_error"  # 系统/网络错误（ConnectionError, OSError 等）


def _classify_error(error: Exception) -> ErrorSeverity:
    """根据异常类型分类错误严重程度

    Args:
        error: 捕获的异常

    Returns:
        ErrorSeverity 枚举值
    """
    if isinstance(error, BtApiError):
        return ErrorSeverity.BUSINESS_ERROR

    user_error_types = (TypeError, ValueError, AttributeError, KeyError, IndexError)
    system_error_types = (ConnectionError, OSError, TimeoutError, RuntimeError)

    if isinstance(error, user_error_types):
        return ErrorSeverity.USER_ERROR
    if isinstance(error, system_error_types):
        return ErrorSeverity.SYSTEM_ERROR

    return ErrorSeverity.SYSTEM_ERROR


class EventBus:
    """轻量级事件总线，支持按事件类型注册回调"""

    def __init__(
        self,
        logger: Any = None,
        error_mode: ErrorHandlerMode = ErrorHandlerMode.LOG,
    ) -> None:
        self._handlers: defaultdict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._lock = threading.RLock()
        self.logger = logger or get_logger("event_bus")
        self.error_mode = error_mode
        self._last_errors: list[tuple[str, Callable[..., Any], Exception]] = []

    def on(self, event_type: str, handler: Callable[..., Any]) -> None:
        if not event_type:
            raise ValueError("event_type must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def off(self, event_type: str, handler: Callable[..., Any] | None = None) -> None:
        with self._lock:
            if handler is None:
                self._handlers.pop(event_type, None)
            else:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)

    def emit(self, event_type: str, data: Any) -> list[Exception]:
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))

        errors: list[Exception] = []
        self._last_errors = []

        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                handler_name = getattr(handler, "__name__", repr(handler))
                error_info = (event_type, handler, e)
                self._last_errors.append(error_info)
                errors.append(e)

                if self.error_mode == ErrorHandlerMode.RAISE:
                    raise

                severity = _classify_error(e)
                severity_label = {
                    ErrorSeverity.USER_ERROR: "handler error",
                    ErrorSeverity.BUSINESS_ERROR: "business error",
                    ErrorSeverity.SYSTEM_ERROR: "system error",
                }[severity]

                log_msg = (
                    f"EventBus {severity_label}: "
                    f"event={event_type}, handler={handler_name}, error={e}\n"
                    f"{traceback.format_exc()}"
                )

                if severity == ErrorSeverity.SYSTEM_ERROR:
                    self.logger.error(log_msg)
                else:
                    self.logger.warning(log_msg)

        return errors

    def get_last_errors(self) -> list[tuple[str, Callable[..., Any], Exception]]:
        """获取最近一次 emit 调用产生的错误列表"""
        return list(self._last_errors)

    def clear_errors(self) -> None:
        """清空错误记录"""
        self._last_errors = []

    def has_handlers(self, event_type: str) -> bool:
        with self._lock:
            return len(self._handlers.get(event_type, [])) > 0

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._last_errors = []
