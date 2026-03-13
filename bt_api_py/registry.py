"""
交易所注册表 — 使用 Registry Pattern 实现交易所的即插即用
新交易所只需注册 feed_class / stream_classes / exchange_data_class，无需修改核心代码

支持两种使用方式:
1. 全局单例（向后兼容）: ExchangeRegistry.register_feed(...)
2. 独立实例（测试隔离）: registry = ExchangeRegistry(); registry.register_feed(...)
"""

import threading
from collections.abc import Callable
from typing import Any

from bt_api_py.exceptions import ExchangeNotFoundError

__all__ = ["ExchangeRegistry"]


_PUBLIC_METHODS = frozenset(
    {
        "register_feed",
        "register_stream",
        "register_exchange_data",
        "register_balance_handler",
        "unregister_feed",
        "get_feed_class",
        "get_feed_classes",
        "create_feed",
        "create_exchange_data",
        "get_stream_classes",
        "get_stream_class",
        "get_balance_handler",
        "has_exchange",
        "list_exchanges",
        "clear",
    }
)


class _RegistryMeta(type):
    """元类：将 ExchangeRegistry.method() 类级调用委托到全局默认实例，
    保持向后兼容。显式 @classmethod 包装器便于 mypy 类型检查。
    """

    def __getattribute__(cls, name):
        if name.startswith("__") or name in ("_default", "_get_default", "_default_lock"):
            return type.__getattribute__(cls, name)
        if name in _PUBLIC_METHODS:
            return type.__getattribute__(cls, name)  # 使用显式 @classmethod
        try:
            default = type.__getattribute__(cls, "_get_default")()
            return getattr(default, name)
        except (AttributeError, TypeError):
            return type.__getattribute__(cls, name)


class ExchangeRegistry(metaclass=_RegistryMeta):
    """交易所注册表，管理 feed 类、流式数据类、交易所配置类的注册与创建

    全局使用（向后兼容）:
        ExchangeRegistry.register_feed("BINANCE___SPOT", BinanceSpotFeed)
        feed = ExchangeRegistry.create_feed("BINANCE___SPOT", queue)

    测试隔离:
        registry = ExchangeRegistry()
        registry.register_feed("TEST___SPOT", MockFeed)
    """

    _default: "ExchangeRegistry | None" = None  # 延迟初始化的全局默认实例
    _default_lock = threading.Lock()

    # 实例属性（在 __new__ 中初始化，此处声明以便 mypy 识别）
    _feed_classes: dict[str, type]
    _stream_classes: dict[str, dict[str, Any]]
    _exchange_data_classes: dict[str, type]
    _balance_handlers: dict[str, Callable[..., Any]]
    _lock: threading.RLock
    _initialized: bool

    def __new__(cls):
        """确保 ExchangeRegistry() 返回全局单例实例"""
        if cls._default is not None:
            return cls._default
        with cls._default_lock:
            if cls._default is None:
                instance = super().__new__(cls)
                instance._feed_classes = {}
                instance._stream_classes = {}
                instance._exchange_data_classes = {}
                instance._balance_handlers = {}
                instance._lock = threading.RLock()
                instance._initialized = True
                cls._default = instance
            return cls._default

    def __init__(self):
        pass

    def __getattribute__(self, name):
        if name in _PUBLIC_METHODS:
            internal_name = f"_{name}"
            try:
                return object.__getattribute__(self, internal_name)
            except AttributeError:
                pass
        return object.__getattribute__(self, name)

    @classmethod
    def _get_default(cls) -> "ExchangeRegistry":
        """获取全局默认实例（延迟初始化）"""
        if cls._default is None:
            cls()  # triggers __new__ which creates the default
        assert cls._default is not None
        return cls._default

    @classmethod
    def create_isolated(cls) -> "ExchangeRegistry":
        instance = object.__new__(cls)
        instance._feed_classes = {}
        instance._stream_classes = {}
        instance._exchange_data_classes = {}
        instance._balance_handlers = {}
        instance._lock = threading.RLock()
        instance._initialized = True
        return instance

    # ── 类级 API（显式 classmethod，便于 mypy 理解）─────────────────

    @classmethod
    def register_feed(cls, exchange_name: str, feed_class: type) -> None:
        """注册 REST feed 类（类级调用）"""
        cls._get_default()._register_feed(exchange_name, feed_class)

    @classmethod
    def register_stream(cls, exchange_name: str, stream_type: str, stream_class: Any) -> None:
        """注册流式数据类（类级调用）"""
        cls._get_default()._register_stream(exchange_name, stream_type, stream_class)

    @classmethod
    def register_exchange_data(cls, exchange_name: str, exchange_data_class: type) -> None:
        """注册交易所配置类（类级调用）"""
        cls._get_default()._register_exchange_data(exchange_name, exchange_data_class)

    @classmethod
    def register_balance_handler(cls, exchange_name: str, handler_func: Callable) -> None:
        """注册余额解析处理函数（类级调用）"""
        cls._get_default()._register_balance_handler(exchange_name, handler_func)

    @classmethod
    def unregister_feed(cls, exchange_name: str) -> bool:
        """移除已注册的 REST feed 类（类级调用）"""
        return cls._get_default()._unregister_feed(exchange_name)

    @classmethod
    def get_feed_class(cls, exchange_name: str) -> type | None:
        """获取已注册的 REST feed 类（类级调用）"""
        return cls._get_default()._get_feed_class(exchange_name)

    @classmethod
    def get_feed_classes(cls) -> dict[str, type]:
        """获取所有已注册的 REST feed 类快照（类级调用）"""
        return cls._get_default()._get_feed_classes()

    @classmethod
    def create_feed(cls, exchange_name: str, data_queue: Any, **kwargs: Any) -> Any:
        """根据交易所标识创建 feed 实例（类级调用）"""
        return cls._get_default()._create_feed(exchange_name, data_queue, **kwargs)

    @classmethod
    def create_exchange_data(cls, exchange_name: str) -> Any:
        """根据交易所标识创建交易所配置实例（类级调用）"""
        return cls._get_default()._create_exchange_data(exchange_name)

    @classmethod
    def get_stream_classes(cls, exchange_name: str) -> dict[str, Any]:
        """获取某个交易所的所有流式数据类（类级调用）"""
        return cls._get_default()._get_stream_classes(exchange_name)

    @classmethod
    def get_stream_class(cls, exchange_name: str, stream_type: str) -> Any | None:
        """获取某个交易所的某种流式数据类（类级调用）"""
        return cls._get_default()._get_stream_class(exchange_name, stream_type)

    @classmethod
    def get_balance_handler(cls, exchange_name: str) -> Callable | None:
        """获取余额解析处理函数（类级调用）"""
        return cls._get_default()._get_balance_handler(exchange_name)

    @classmethod
    def has_exchange(cls, exchange_name: str) -> bool:
        """检查交易所是否已注册（类级调用）"""
        return cls._get_default()._has_exchange(exchange_name)

    @classmethod
    def list_exchanges(cls) -> list[str]:
        """列出所有已注册的交易所（类级调用）"""
        return cls._get_default()._list_exchanges()

    @classmethod
    def clear(cls) -> None:
        """清空所有注册（类级调用，主要用于测试）"""
        cls._get_default()._clear()

    # ── 实例实现方法（classmethod 和 instance.method() 均委托到此处）──

    def _register_feed(self, exchange_name: str, feed_class: type) -> None:
        with self._lock:
            self._feed_classes[exchange_name] = feed_class

    def _register_stream(self, exchange_name: str, stream_type: str, stream_class: Any) -> None:
        with self._lock:
            self._stream_classes.setdefault(exchange_name, {})[stream_type] = stream_class

    def _register_exchange_data(self, exchange_name: str, exchange_data_class: type) -> None:
        with self._lock:
            self._exchange_data_classes[exchange_name] = exchange_data_class

    def _register_balance_handler(self, exchange_name: str, handler_func: Callable) -> None:
        with self._lock:
            self._balance_handlers[exchange_name] = handler_func

    def _unregister_feed(self, exchange_name: str) -> bool:
        with self._lock:
            return self._feed_classes.pop(exchange_name, None) is not None

    def _get_feed_class(self, exchange_name: str) -> type | None:
        with self._lock:
            return self._feed_classes.get(exchange_name)

    def _get_feed_classes(self) -> dict[str, type]:
        with self._lock:
            return self._feed_classes.copy()

    def _create_feed(self, exchange_name: str, data_queue: Any, **kwargs: Any) -> Any:
        with self._lock:
            feed_cls = self._feed_classes.get(exchange_name)
            available = list(self._feed_classes.keys())
        if feed_cls is None:
            raise ExchangeNotFoundError(exchange_name, available)
        return feed_cls(data_queue, **kwargs)

    def _create_exchange_data(self, exchange_name: str) -> Any:
        with self._lock:
            ed_cls = self._exchange_data_classes.get(exchange_name)
            available = list(self._exchange_data_classes.keys())
        if ed_cls is None:
            raise ExchangeNotFoundError(exchange_name, available)
        return ed_cls()

    def _get_stream_classes(self, exchange_name: str) -> dict[str, Any]:
        with self._lock:
            return self._stream_classes.get(exchange_name, {}).copy()

    def _get_stream_class(self, exchange_name: str, stream_type: str) -> Any | None:
        with self._lock:
            return self._stream_classes.get(exchange_name, {}).get(stream_type)

    def _get_balance_handler(self, exchange_name: str) -> Callable | None:
        with self._lock:
            return self._balance_handlers.get(exchange_name)

    def _has_exchange(self, exchange_name: str) -> bool:
        with self._lock:
            return exchange_name in self._feed_classes

    def _list_exchanges(self) -> list[str]:
        with self._lock:
            return list(self._feed_classes.keys())

    def _clear(self) -> None:
        with self._lock:
            self._feed_classes.clear()
            self._stream_classes.clear()
            self._exchange_data_classes.clear()
            self._balance_handlers.clear()
