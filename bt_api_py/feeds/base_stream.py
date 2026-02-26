"""
流式数据抽象基类 — 支持 WebSocket / CTP SPI / IB TWS 等不同协议
所有流式数据连接器都应继承此类，实现 connect/disconnect/subscribe_topics/_run_loop 方法
"""
import threading
from abc import ABC, abstractmethod
from enum import Enum

from bt_api_py.functions.log_message import SpdLogManager


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

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.stream_name = kwargs.get('stream_name', self.__class__.__name__)
        self._running = False
        self._state = ConnectionState.DISCONNECTED
        self._thread = None
        log_file = kwargs.get('log_file_name', f"./logs/{self.stream_name}.log")
        self.logger = SpdLogManager(log_file, self.stream_name, 0, 0, False).create_logger()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        old_state = self._state
        self._state = new_state
        self.on_state_change(old_state, new_state)

    def on_state_change(self, old_state, new_state):
        """连接状态变化回调，子类可重写"""
        self.logger.info(f"{self.stream_name} state: {old_state.value} -> {new_state.value}")

    @abstractmethod
    def connect(self):
        """建立连接"""
        ...

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        ...

    @abstractmethod
    def subscribe_topics(self, topics):
        """订阅主题
        :param topics: list of topic dicts, e.g. [{"topic": "kline", "symbol": "BTC-USDT", "period": "1m"}]
        """
        ...

    @abstractmethod
    def _run_loop(self):
        """主循环，在独立 daemon 线程中运行"""
        ...

    def start(self):
        """启动流式数据连接"""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止流式数据连接"""
        self._running = False
        self.disconnect()

    def is_running(self):
        return self._running

    def push_data(self, data):
        """推送数据到队列"""
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            self.logger.warn(f"{self.stream_name}: data_queue is None, data dropped")

    def wait_connected(self, timeout=30, interval=0.5):
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
        self.logger.warn(f"{self.stream_name}: wait_connected timeout after {timeout}s")
        return False
