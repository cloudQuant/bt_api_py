"""
连接管理混入类 — 仅用于 Feed 基类

注意: BaseDataStream 已有完整的 ConnectionState 管理，无需此 Mixin。
此 Mixin 仅为 Feed（REST 请求类）添加统一的连接生命周期接口。
HTTP 场所的 connect/disconnect 默认为 no-op（保持向后兼容）。
"""

from __future__ import annotations

import threading
from enum import Enum, unique


@unique
class FeedConnectionState(Enum):
    """Feed 连接状态枚举

    与 base_stream.py 中的 ConnectionState 同名但独立，
    避免 Feed 层和 DataStream 层的状态耦合。
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"


class ConnectionMixin:
    """连接管理混入类 — 仅用于 Feed 基类

    HTTP 场所（CEX/类CEX DEX）：connect/disconnect 默认为 no-op。
    非 HTTP 场所（CTP/IB/QMT）：子类覆盖为显式连接管理。
    """

    def __init_connection__(self) -> None:
        """初始化连接状态 — 需由 Feed.__init__ 显式调用"""
        self._conn_state = FeedConnectionState.DISCONNECTED
        self._conn_lock = threading.Lock()

    @property
    def connection_state(self) -> FeedConnectionState:
        lock = getattr(self, "_conn_lock", None)
        if lock:
            with lock:
                return self._conn_state
        return getattr(self, "_conn_state", FeedConnectionState.DISCONNECTED)

    def _set_connection_state(self, new_state: FeedConnectionState) -> None:
        lock = getattr(self, "_conn_lock", None)
        if lock:
            with lock:
                self._conn_state = new_state
        else:
            self._conn_state = new_state

    def connect(self) -> None:
        """建立连接 — HTTP 场所默认 no-op，CTP/IB/QMT 子类需覆盖"""
        self._set_connection_state(FeedConnectionState.CONNECTED)

    def disconnect(self) -> None:
        """断开连接 — HTTP 场所默认 no-op，CTP/IB/QMT 子类需覆盖"""
        self._set_connection_state(FeedConnectionState.DISCONNECTED)

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.connection_state in (
            FeedConnectionState.CONNECTED,
            FeedConnectionState.AUTHENTICATED,
        )
