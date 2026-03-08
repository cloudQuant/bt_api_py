"""
事件总线 — 提供发布/订阅模式的事件分发机制
支持 Queue 模式（现有行为）和 Callback 模式（适配 CTP SPI / IB EWrapper 等回调驱动 API）
"""

import threading
import traceback
from collections import defaultdict

from bt_api_py.logging_factory import get_logger

__all__ = ["EventBus"]


class EventBus:
    """轻量级事件总线，支持按事件类型注册回调"""

    def __init__(self, logger=None):
        self._handlers = defaultdict(list)  # event_type -> [callback_func, ...]
        self._lock = threading.RLock()
        self.logger = logger or get_logger("event_bus")

    def on(self, event_type, handler):
        """注册事件处理函数
        :param event_type: 事件类型字符串, 如 "BarEvent", "OrderEvent", "TradeEvent"
        :param handler: callable(data), 事件处理函数
        """
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def off(self, event_type, handler=None):
        """移除事件处理函数
        :param event_type: 事件类型字符串
        :param handler: 要移除的处理函数, 如果为 None 则移除该事件类型的所有处理函数
        """
        with self._lock:
            if handler is None:
                self._handlers.pop(event_type, None)
            else:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)

    def emit(self, event_type, data):
        """触发事件，调用所有注册的处理函数
        :param event_type: 事件类型字符串
        :param data: 事件数据
        """
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))

        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                handler_name = getattr(handler, "__name__", repr(handler))
                self.logger.warning(
                    f"EventBus handler error: event={event_type}, "
                    f"handler={handler_name}, error={e}\n"
                    f"{traceback.format_exc()}"
                )

    def has_handlers(self, event_type):
        """检查某个事件类型是否有注册的处理函数"""
        with self._lock:
            return len(self._handlers.get(event_type, [])) > 0

    def clear(self):
        """清空所有事件处理函数"""
        with self._lock:
            self._handlers.clear()
