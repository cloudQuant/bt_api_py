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


class _RegistryMeta(type):
    """元类：将 ExchangeRegistry.method() 类级调用委托到全局默认实例，
    保持向后兼容。实例方法调用不受影响。

    使用 __getattribute__ 而非 __getattr__，因为实例方法作为函数存在于类字典中，
    __getattr__ 不会触发。__getattribute__ 拦截所有类级属性访问。
    """

    def __getattribute__(cls, name):
        # dunder 属性和元类基础设施走正常路径
        if name.startswith("__") or name in ("_default", "_get_default", "_default_lock"):
            return type.__getattribute__(cls, name)
        # 公共方法及实例数据属性：委托到全局默认实例
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

    @classmethod
    def _get_default(cls) -> "ExchangeRegistry":
        """获取全局默认实例（延迟初始化）"""
        if cls._default is None:
            cls()  # triggers __new__ which creates the default
        return cls._default

    # ── 实例方法（核心逻辑）───────────────────────────────────────

    def register_feed(self, exchange_name: str, feed_class: type) -> None:
        """注册 REST feed 类
        :param exchange_name: 交易所标识, 如 "BINANCE___SWAP", "CTP___FUTURE"
        :param feed_class: Feed 子类
        """
        with self._lock:
            self._feed_classes[exchange_name] = feed_class

    def register_stream(self, exchange_name: str, stream_type: str, stream_class: Any) -> None:
        """注册流式数据类
        :param exchange_name: 交易所标识
        :param stream_type: 流类型, 如 "market", "account", "kline"
        :param stream_class: BaseDataStream 子类
        """
        with self._lock:
            self._stream_classes.setdefault(exchange_name, {})[stream_type] = stream_class

    def register_exchange_data(self, exchange_name: str, exchange_data_class: type) -> None:
        """注册交易所配置类
        :param exchange_name: 交易所标识
        :param exchange_data_class: ExchangeData 子类
        """
        with self._lock:
            self._exchange_data_classes[exchange_name] = exchange_data_class

    def register_balance_handler(self, exchange_name: str, handler_func: Callable) -> None:
        """注册余额解析处理函数
        :param exchange_name: 交易所标识
        :param handler_func: callable(account_list) -> (value_result, cash_result)
        """
        with self._lock:
            self._balance_handlers[exchange_name] = handler_func

    def unregister_feed(self, exchange_name: str) -> bool:
        """移除已注册的 REST feed 类。"""
        with self._lock:
            return self._feed_classes.pop(exchange_name, None) is not None

    def get_feed_class(self, exchange_name: str) -> type | None:
        """获取已注册的 REST feed 类。"""
        with self._lock:
            return self._feed_classes.get(exchange_name)

    def get_feed_classes(self) -> dict[str, type]:
        """获取所有已注册的 REST feed 类快照。"""
        with self._lock:
            return self._feed_classes.copy()

    def create_feed(self, exchange_name: str, data_queue: Any, **kwargs) -> Any:
        """根据交易所标识创建 feed 实例
        :param exchange_name: 交易所标识
        :param data_queue: queue.Queue
        :param kwargs: 传递给 feed 构造函数的参数
        :return: Feed 实例
        """
        with self._lock:
            feed_cls = self._feed_classes.get(exchange_name)
            available = list(self._feed_classes.keys())
        if feed_cls is None:
            raise ExchangeNotFoundError(exchange_name, available)
        return feed_cls(data_queue, **kwargs)

    def create_exchange_data(self, exchange_name: str) -> Any:
        """根据交易所标识创建交易所配置实例
        :param exchange_name: 交易所标识
        :return: ExchangeData 实例
        """
        with self._lock:
            ed_cls = self._exchange_data_classes.get(exchange_name)
            available = list(self._exchange_data_classes.keys())
        if ed_cls is None:
            raise ExchangeNotFoundError(exchange_name, available)
        return ed_cls()

    def get_stream_classes(self, exchange_name: str) -> dict[str, Any]:
        """获取某个交易所的所有流式数据类
        :param exchange_name: 交易所标识
        :return: dict of {stream_type: stream_class}
        """
        with self._lock:
            return self._stream_classes.get(exchange_name, {}).copy()

    def get_stream_class(self, exchange_name: str, stream_type: str) -> Any | None:
        """获取某个交易所的某种流式数据类
        :param exchange_name: 交易所标识
        :param stream_type: 流类型
        :return: stream_class or None
        """
        with self._lock:
            return self._stream_classes.get(exchange_name, {}).get(stream_type)

    def get_balance_handler(self, exchange_name: str) -> Callable | None:
        """获取余额解析处理函数
        :param exchange_name: 交易所标识
        :return: handler_func or None
        """
        with self._lock:
            return self._balance_handlers.get(exchange_name)

    def has_exchange(self, exchange_name: str) -> bool:
        """检查交易所是否已注册"""
        with self._lock:
            return exchange_name in self._feed_classes

    def list_exchanges(self) -> list[str]:
        """列出所有已注册的交易所"""
        with self._lock:
            return list(self._feed_classes.keys())

    def clear(self) -> None:
        """清空所有注册（主要用于测试）"""
        with self._lock:
            self._feed_classes.clear()
            self._stream_classes.clear()
            self._exchange_data_classes.clear()
            self._balance_handlers.clear()
