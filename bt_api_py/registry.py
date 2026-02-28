"""
交易所注册表 — 使用 Registry Pattern 实现交易所的即插即用
新交易所只需注册 feed_class / stream_classes / exchange_data_class，无需修改核心代码
"""

from typing import Any, Callable, Dict, List, Optional, Type


class ExchangeRegistry:
    """全局交易所注册表，管理 feed 类、流式数据类、交易所配置类的注册与创建"""

    _feed_classes: Dict[str, Type] = {}  # {"BINANCE___SWAP": BinanceRequestDataSwap, ...}
    _stream_classes: Dict[str, Dict[str, Any]] = (
        {}
    )  # {"BINANCE___SWAP": {"market": cls, "account": cls}}
    _exchange_data_classes: Dict[str, Type] = {}  # {"BINANCE___SWAP": BinanceExchangeDataSwap, ...}
    _balance_handlers: Dict[str, Callable] = {}  # {"BINANCE___SWAP": handler_func, ...}

    @classmethod
    def register_feed(cls, exchange_name: str, feed_class: Type) -> None:
        """注册 REST feed 类
        :param exchange_name: 交易所标识, 如 "BINANCE___SWAP", "CTP___FUTURE"
        :param feed_class: Feed 子类
        """
        cls._feed_classes[exchange_name] = feed_class

    @classmethod
    def register_stream(cls, exchange_name: str, stream_type: str, stream_class: Any) -> None:
        """注册流式数据类
        :param exchange_name: 交易所标识
        :param stream_type: 流类型, 如 "market", "account", "kline"
        :param stream_class: BaseDataStream 子类
        """
        cls._stream_classes.setdefault(exchange_name, {})[stream_type] = stream_class

    @classmethod
    def register_exchange_data(cls, exchange_name: str, exchange_data_class: Type) -> None:
        """注册交易所配置类
        :param exchange_name: 交易所标识
        :param exchange_data_class: ExchangeData 子类
        """
        cls._exchange_data_classes[exchange_name] = exchange_data_class

    @classmethod
    def register_balance_handler(cls, exchange_name: str, handler_func: Callable) -> None:
        """注册余额解析处理函数
        :param exchange_name: 交易所标识
        :param handler_func: callable(account_list) -> (value_result, cash_result)
        """
        cls._balance_handlers[exchange_name] = handler_func

    @classmethod
    def create_feed(cls, exchange_name: str, data_queue: Any, **kwargs) -> Any:
        """根据交易所标识创建 feed 实例
        :param exchange_name: 交易所标识
        :param data_queue: queue.Queue
        :param kwargs: 传递给 feed 构造函数的参数
        :return: Feed 实例
        """
        feed_cls = cls._feed_classes.get(exchange_name)
        if feed_cls is None:
            raise ValueError(
                f"Unknown exchange feed: {exchange_name}. "
                f"Available: {list(cls._feed_classes.keys())}"
            )
        return feed_cls(data_queue, **kwargs)

    @classmethod
    def create_exchange_data(cls, exchange_name: str) -> Any:
        """根据交易所标识创建交易所配置实例
        :param exchange_name: 交易所标识
        :return: ExchangeData 实例
        """
        ed_cls = cls._exchange_data_classes.get(exchange_name)
        if ed_cls is None:
            raise ValueError(
                f"Unknown exchange data: {exchange_name}. "
                f"Available: {list(cls._exchange_data_classes.keys())}"
            )
        return ed_cls()

    @classmethod
    def get_stream_classes(cls, exchange_name: str) -> Dict[str, Any]:
        """获取某个交易所的所有流式数据类
        :param exchange_name: 交易所标识
        :return: dict of {stream_type: stream_class}
        """
        return cls._stream_classes.get(exchange_name, {})

    @classmethod
    def get_stream_class(cls, exchange_name: str, stream_type: str) -> Optional[Any]:
        """获取某个交易所的某种流式数据类
        :param exchange_name: 交易所标识
        :param stream_type: 流类型
        :return: stream_class or None
        """
        return cls._stream_classes.get(exchange_name, {}).get(stream_type)

    @classmethod
    def get_balance_handler(cls, exchange_name: str) -> Optional[Callable]:
        """获取余额解析处理函数
        :param exchange_name: 交易所标识
        :return: handler_func or None
        """
        return cls._balance_handlers.get(exchange_name)

    @classmethod
    def has_exchange(cls, exchange_name: str) -> bool:
        """检查交易所是否已注册"""
        return exchange_name in cls._feed_classes

    @classmethod
    def list_exchanges(cls) -> List[str]:
        """列出所有已注册的交易所"""
        return list(cls._feed_classes.keys())

    @classmethod
    def clear(cls) -> None:
        """清空所有注册（主要用于测试）"""
        cls._feed_classes.clear()
        cls._stream_classes.clear()
        cls._exchange_data_classes.clear()
        cls._balance_handlers.clear()
