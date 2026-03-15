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


class _ClassMethodOrInstance:
    """描述符：类调用时使用类方法，实例调用时使用实例方法"""

    def __init__(self, class_method_name: str, instance_method_name: str):
        self.class_method_name = class_method_name
        self.instance_method_name = instance_method_name

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return getattr(objtype, self.class_method_name)
        return getattr(obj, self.instance_method_name)


class ExchangeRegistry:
    """交易所注册表，管理 feed 类、流式数据类、交易所配置类的注册与创建

    全局使用（向后兼容）:
        ExchangeRegistry.register_feed("BINANCE___SPOT", BinanceSpotFeed)
        feed = ExchangeRegistry.create_feed("BINANCE___SPOT", queue)

    测试隔离:
        registry = ExchangeRegistry()
        registry.register_feed("TEST___SPOT", MockFeed)
    """

    _default: "ExchangeRegistry | None" = None
    _default_lock = threading.Lock()

    _feed_classes: dict[str, type[Any]]
    _stream_classes: dict[str, dict[str, Any]]
    _exchange_data_classes: dict[str, type]
    _balance_handlers: dict[str, Callable[..., Any]]
    _lock: threading.RLock

    def __new__(cls) -> "ExchangeRegistry":
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
                cls._default = instance
            return cls._default

    def __init__(self) -> None:
        """Initialize registry instance (singleton pattern handled by __new__)."""

    @classmethod
    def _get_default(cls) -> "ExchangeRegistry":
        if cls._default is None:
            cls()
            if cls._default is None:
                raise RuntimeError("Failed to initialize ExchangeRegistry singleton")
        return cls._default

    @classmethod
    def create_isolated(cls) -> "ExchangeRegistry":
        instance = object.__new__(cls)
        instance._feed_classes = {}
        instance._stream_classes = {}
        instance._exchange_data_classes = {}
        instance._balance_handlers = {}
        instance._lock = threading.RLock()
        return instance

    # ── 内部实现方法 ────────────────────────────────────────────────────

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

    # ── 类级方法（委托到默认实例）─────────────────────────────────────────

    @classmethod
    def _cls_register_feed(cls, exchange_name: str, feed_class: type) -> None:
        cls._get_default()._register_feed(exchange_name, feed_class)

    @classmethod
    def _cls_register_stream(cls, exchange_name: str, stream_type: str, stream_class: Any) -> None:
        cls._get_default()._register_stream(exchange_name, stream_type, stream_class)

    @classmethod
    def _cls_register_exchange_data(cls, exchange_name: str, exchange_data_class: type) -> None:
        cls._get_default()._register_exchange_data(exchange_name, exchange_data_class)

    @classmethod
    def _cls_register_balance_handler(cls, exchange_name: str, handler_func: Callable) -> None:
        cls._get_default()._register_balance_handler(exchange_name, handler_func)

    @classmethod
    def _cls_unregister_feed(cls, exchange_name: str) -> bool:
        return cls._get_default()._unregister_feed(exchange_name)

    @classmethod
    def _cls_get_feed_class(cls, exchange_name: str) -> type | None:
        return cls._get_default()._get_feed_class(exchange_name)

    @classmethod
    def _cls_get_feed_classes(cls) -> dict[str, type]:
        return cls._get_default()._get_feed_classes()

    @classmethod
    def _cls_create_feed(cls, exchange_name: str, data_queue: Any, **kwargs: Any) -> Any:
        return cls._get_default()._create_feed(exchange_name, data_queue, **kwargs)

    @classmethod
    def _cls_create_exchange_data(cls, exchange_name: str) -> Any:
        return cls._get_default()._create_exchange_data(exchange_name)

    @classmethod
    def _cls_get_stream_classes(cls, exchange_name: str) -> dict[str, Any]:
        return cls._get_default()._get_stream_classes(exchange_name)

    @classmethod
    def _cls_get_stream_class(cls, exchange_name: str, stream_type: str) -> Any | None:
        return cls._get_default()._get_stream_class(exchange_name, stream_type)

    @classmethod
    def _cls_get_balance_handler(cls, exchange_name: str) -> Callable | None:
        return cls._get_default()._get_balance_handler(exchange_name)

    @classmethod
    def _cls_has_exchange(cls, exchange_name: str) -> bool:
        return cls._get_default()._has_exchange(exchange_name)

    @classmethod
    def _cls_list_exchanges(cls) -> list[str]:
        return cls._get_default()._list_exchanges()

    @classmethod
    def _cls_clear(cls) -> None:
        cls._get_default()._clear()

    # ── 公共 API（描述符实现类/实例双模式）──────────────────────────────

    register_feed = _ClassMethodOrInstance("_cls_register_feed", "_register_feed")
    register_stream = _ClassMethodOrInstance("_cls_register_stream", "_register_stream")
    register_exchange_data = _ClassMethodOrInstance(
        "_cls_register_exchange_data", "_register_exchange_data"
    )
    register_balance_handler = _ClassMethodOrInstance(
        "_cls_register_balance_handler", "_register_balance_handler"
    )
    unregister_feed = _ClassMethodOrInstance("_cls_unregister_feed", "_unregister_feed")
    get_feed_class = _ClassMethodOrInstance("_cls_get_feed_class", "_get_feed_class")
    get_feed_classes = _ClassMethodOrInstance("_cls_get_feed_classes", "_get_feed_classes")
    create_feed = _ClassMethodOrInstance("_cls_create_feed", "_create_feed")
    create_exchange_data = _ClassMethodOrInstance(
        "_cls_create_exchange_data", "_create_exchange_data"
    )
    get_stream_classes = _ClassMethodOrInstance("_cls_get_stream_classes", "_get_stream_classes")
    get_stream_class = _ClassMethodOrInstance("_cls_get_stream_class", "_get_stream_class")
    get_balance_handler = _ClassMethodOrInstance("_cls_get_balance_handler", "_get_balance_handler")
    has_exchange = _ClassMethodOrInstance("_cls_has_exchange", "_has_exchange")
    list_exchanges = _ClassMethodOrInstance("_cls_list_exchanges", "_list_exchanges")
    clear = _ClassMethodOrInstance("_cls_clear", "_clear")
