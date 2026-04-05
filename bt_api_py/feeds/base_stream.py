"""
流式数据抽象基类 — 支持 WebSocket / CTP SPI / IB TWS 等不同协议
所有流式数据连接器都应继承此类，实现 connect/disconnect/subscribe_topics/_run_loop 方法
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from bt_api_py.logging_factory import get_logger


class ConnectionState(Enum):
    """连接状态枚举"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"


class BaseDataStream(ABC):
    """通用流式数据抽象基类

    子类需实现:
      - connect(): 建立连接
      - disconnect(): 断开连接
      - subscribe_topics(topics): 订阅主题
      - _run_loop(): 主循环（在独立线程中运行）
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self.stream_name = kwargs.get("stream_name", self.__class__.__name__)
        self._running = False
        self._state = ConnectionState.DISCONNECTED
        self._thread: threading.Thread | None = None
        self.logger = get_logger("unknown")

    @property
    def state(self) -> ConnectionState:
        return self._state

    @state.setter
    def state(self, new_state: ConnectionState) -> None:
        old_state = self._state
        self._state = new_state
        self.on_state_change(old_state, new_state)

    def on_state_change(self, old_state: ConnectionState, new_state: ConnectionState) -> None:
        """连接状态变化回调，子类可重写"""
        self.logger.info(f"{self.stream_name} state: {old_state.value} -> {new_state.value}")

    @abstractmethod
    def connect(self) -> None:
        """建立连接"""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    def subscribe_topics(self, topics: list[dict[str, Any]]) -> None:
        """订阅主题
        :param topics: list of topic dicts, e.g. [{"topic": "kline", "symbol": "BTC-USDT", "period": "1m"}]
        """
        ...

    @abstractmethod
    def _run_loop(self) -> None:
        """主循环，在独立 daemon 线程中运行"""
        ...

    def start(self) -> None:
        """启动流式数据连接"""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止流式数据连接"""
        self._running = False
        self.disconnect()

    def is_running(self) -> bool:
        return self._running

    def push_data(self, data: Any) -> None:
        """推送数据到队列"""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            self.logger.warning(f"{self.stream_name}: data_queue is None, data dropped")

    def wait_connected(self, timeout: float = 30, interval: float = 0.5) -> bool:
        """阻塞等待连接建立
        :param timeout: 最大等待秒数
        :param interval: 轮询间隔秒数
        :return: True if connected, False if timeout
        """
        import time

        elapsed = 0.0
        while elapsed < timeout:
            if self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED):
                return True
            time.sleep(interval)
            elapsed += interval
        self.logger.warning(f"{self.stream_name}: wait_connected timeout after {timeout}s")
        return False
