"""
Integrate all exchange APIs using this BtApi class
通过 ExchangeRegistry 实现交易所的即插即用，新增交易所无需修改此文件
"""

from __future__ import annotations

# 导入注册模块，确保交易所在使用前完成注册
# 自动扫描 exchange_registers/ 下所有模块，无需手动维护 import 列表
import queue
import warnings
from copy import deepcopy
from typing import Any

from bt_api_base.event_bus import EventBus
from bt_api_base.exceptions import (
    ExchangeNotFoundError,
    InvalidOrderError,
    SubscribeError,
)
from bt_api_base.logging_factory import _LoggerProxy, get_logger
from bt_api_base.registry import ExchangeRegistry

from ._contracts.errors import CapabilityNotSupportedError
from ._contracts.models import Consistency, ForwardingConfig, TransportMode
from .balance_manager import BalanceManagerMixin
from .data_downloader import DataDownloaderMixin

__all__ = ["BtApi"]

DATANAME_SEPARATOR = "___"


_reg_logger = get_logger("registry")


class _RuntimeRegistrar:
    """Minimal runtime registrar for adapter registration."""

    def __init__(self) -> None:
        self._adapters: dict[str, type] = {}

    def register_adapter(self, exchange_type: str, adapter_cls: type) -> None:
        normalized = str(exchange_type).strip().upper()
        self._adapters[normalized] = adapter_cls

    def get_adapter(self, exchange_type: str) -> type | None:
        return self._adapters.get(str(exchange_type).strip().upper())

    def list_adapters(self) -> list[str]:
        return list(self._adapters.keys())


_runtime_registrar = _RuntimeRegistrar()
_plugins_loaded = False


def _initialize_plugin_and_legacy_registrations() -> None:
    """Load all plugins via entry points and commit registrations to the global registry."""
    from bt_api_base.plugins.loader import PluginLoader

    registry = ExchangeRegistry._get_default()
    loader = PluginLoader(registry, _runtime_registrar)
    loader.load_all()


def _ensure_plugins_loaded() -> None:
    """惰性加载插件（首次 BtApi 实例化时），坏插件不阻断整体导入。"""
    global _plugins_loaded
    if _plugins_loaded:
        return
    try:
        _initialize_plugin_and_legacy_registrations()
    except Exception as exc:
        get_logger("api").warning(f"Plugin loading degraded: {type(exc).__name__}: {exc}")
    finally:
        _plugins_loaded = True


# 常用异步方法白名单（A-09：__getattr__ 只代理白名单内的方法，避免 hasattr 恒真）
class BtApi(DataDownloaderMixin, BalanceManagerMixin):
    """统一多交易所 API 入口，通过 ExchangeRegistry 实现交易所即插即用。"""

    exchange_kwargs: dict[str, Any]
    debug: bool
    data_queues: dict[str, queue.Queue[Any]]
    exchange_feeds: dict[str, Any]
    logger: _LoggerProxy
    _value_dict: dict[str, Any]
    _cash_dict: dict[str, Any]
    subscribe_bar_num: int
    event_bus: EventBus
    _subscription_flags: dict[str, bool]
    transport_mode: TransportMode
    _backend: Any

    def __init__(
        self,
        exchange_kwargs: dict[str, Any] | None = None,
        debug: bool = True,
        event_bus: EventBus | None = None,
        *,
        transport_mode: TransportMode = TransportMode.DIRECT,
        forwarding_config: ForwardingConfig | None = None,
    ) -> None:
        """初始化 BtApi 实例。

        Args:
            exchange_kwargs: 交易所配置 dict，key 为 exchange_name，value 为对应参数。
            debug: 是否开启 debug 模式，控制日志输出。
            event_bus: 事件总线实例，用于 BarEvent/OrderEvent 等回调；None 则创建默认实例。
            transport_mode: direct（默认，直接持有 Feed）或 zmq（经转发网关）。
            forwarding_config: ZMQ 模式下的转发网关端点与 scope 配置。
        """
        self.exchange_kwargs = {}
        self.debug = debug
        self.data_queues = {}
        self.exchange_feeds = {}
        self.logger = self.init_logger()
        self._value_dict = {}
        self._cash_dict = {}
        self.subscribe_bar_num = 0
        self.event_bus = event_bus or EventBus()
        self._subscription_flags = {}
        self.transport_mode = transport_mode
        self._backend = self._build_backend(transport_mode, forwarding_config)
        _ensure_plugins_loaded()
        self.init_exchange(exchange_kwargs or {})

    def _build_backend(
        self, transport_mode: TransportMode, forwarding_config: ForwardingConfig | None
    ) -> Any:
        if transport_mode is TransportMode.ZMQ:
            if forwarding_config is None:
                raise ValueError("transport_mode=ZMQ requires forwarding_config")
            from .forwarding.btapi_backend import ZmqBtApiBackend

            return ZmqBtApiBackend(forwarding_config)
        return None

    def init_exchange(self, exchange_kwargs: dict[str, Any]) -> None:
        """根据 exchange_kwargs 初始化并添加交易所。

        Args:
            exchange_kwargs: {exchange_name: params} 格式的配置。
        """
        for exchange_name in exchange_kwargs:
            exchange_params = exchange_kwargs[exchange_name]
            self.add_exchange(exchange_name, exchange_params)

    def init_logger(self) -> _LoggerProxy:
        """Initialize and return the API logger instance."""
        return get_logger("api", print_info=bool(self.debug))

    def log(self, txt: str, level: str = "info") -> None:
        if level in ("info", "warning", "error", "debug"):
            getattr(self.logger, level)(txt)
        else:
            self.logger.warning(f"Unknown log level '{level}', message: {txt}")

    def _parse_dataname(self, dataname: str) -> tuple[str, str, str]:
        if not isinstance(dataname, str) or not dataname:
            raise SubscribeError("", detail="dataname must be a non-empty string")
        parts = dataname.split(DATANAME_SEPARATOR)
        if len(parts) != 3 or not all(parts):
            raise SubscribeError("", detail=f"invalid dataname format: {dataname}")
        return parts[0], parts[1], parts[2]

    def _validate_order_args(
        self,
        exchange_name: str,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
    ) -> str:
        if volume <= 0:
            raise InvalidOrderError(exchange_name, symbol, "volume must be > 0")
        if price < 0:
            raise InvalidOrderError(exchange_name, symbol, "price must be >= 0")
        if not isinstance(order_type, str) or not order_type:
            raise InvalidOrderError(exchange_name, symbol, "order_type must be a non-empty string")

        normalized_order_type = order_type.lower()
        if normalized_order_type not in {"limit", "market"}:
            raise InvalidOrderError(
                exchange_name,
                symbol,
                "order_type must be one of: limit, market",
            )
        if normalized_order_type == "limit" and price <= 0:
            raise InvalidOrderError(exchange_name, symbol, "price must be > 0 for limit order")
        return normalized_order_type

    @staticmethod
    def _copy_exchange_params(exchange_params: dict[str, Any] | None) -> dict[str, Any]:
        if exchange_params is None:
            return {}
        try:
            return deepcopy(dict(exchange_params))
        except (TypeError, ValueError) as exc:
            raise TypeError("exchange_params must be a mapping") from exc

    @staticmethod
    def _normalize_subscribe_topics(
        topics: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(topics, list):
            raise SubscribeError("", detail="topics must be a list of dict items")

        normalized_topics: list[dict[str, Any]] = []
        subscribe_bar_num = 0
        for index, topic in enumerate(topics):
            if not isinstance(topic, dict):
                raise SubscribeError("", detail=f"invalid topic at index {index}: expected dict")
            topic_name = topic.get("topic")
            if not isinstance(topic_name, str) or not topic_name:
                raise SubscribeError(
                    "",
                    detail=f"invalid topic at index {index}: missing non-empty 'topic'",
                )
            normalized_topics.append(deepcopy(dict(topic)))
            if topic_name == "kline":
                subscribe_bar_num += 1
        return normalized_topics, subscribe_bar_num

    def add_exchange(self, exchange_name: str, exchange_params: dict[str, Any]) -> None:
        """Add a new exchange to the API instance.

        Args:
            exchange_name: Exchange identifier (e.g., "BINANCE___SPOT", "OKX___SWAP")
            exchange_params: Exchange-specific parameters (api_key, secret, etc.)

        Example:
            >>> api = BtApi()
            >>> api.add_exchange("BINANCE___SPOT", {
            ...     "api_key": "your_key",
            ...     "secret": "your_secret",
            ...     "testnet": True
            ... })
        """
        if exchange_name not in self.exchange_feeds:
            if exchange_name in self.data_queues:
                raise ExchangeNotFoundError(
                    exchange_name, "data_queue exists but feed does not — inconsistent state"
                )
            stored_exchange_params = self._copy_exchange_params(exchange_params)
            data_queue: queue.Queue[Any] = queue.Queue()
            self.data_queues[exchange_name] = data_queue
            self.exchange_kwargs[exchange_name] = stored_exchange_params
            self.log(f"adding exchange: {exchange_name}")
            try:
                self.exchange_feeds[exchange_name] = ExchangeRegistry.create_feed(
                    exchange_name, data_queue, **stored_exchange_params
                )
            except Exception:
                self.data_queues.pop(exchange_name, None)
                self.exchange_kwargs.pop(exchange_name, None)
                raise
        else:
            self.log(f"exchange_name: {exchange_name} already exists")

    def get_request_api(self, exchange_name: str) -> Any:
        """Get the REST Feed instance for the specified exchange (synchronous API).

        ZMQ transport has no direct feed escape hatch and raises
        ``CapabilityNotSupportedError`` instead of returning ``None``.
        """
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "get_request_api", detail="ZMQ transport has no direct feed escape hatch"
            )
        api = self.exchange_feeds.get(exchange_name)
        if api is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return api

    def get_async_request_api(self, exchange_name: str) -> Any:
        """Deprecated alias for get_request_api."""
        warnings.warn(
            "get_async_request_api is deprecated; use get_request_api instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_request_api(exchange_name)

    def get_data_queue(self, exchange_name: str) -> queue.Queue | None:
        """Get the data queue for the specified exchange.

        The data queue receives market data and order updates from WebSocket streams.

        Args:
            exchange_name: Exchange identifier (e.g., "BINANCE___SPOT")

        Returns:
            Queue instance if exchange exists, None otherwise

        Example:
            >>> api = BtApi({"BINANCE___SPOT": {...}})
            >>> queue = api.get_data_queue("BINANCE___SPOT")
            >>> data = queue.get()  # Blocks until data arrives
        """
        data_queue = self.data_queues.get(exchange_name)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return data_queue

    def subscribe(self, dataname: str, topics: list[dict[str, Any]]) -> None:
        """通过 ExchangeRegistry 查找订阅处理函数，无需硬编码交易所类型"""
        exchange, asset_type, symbol = self._parse_dataname(dataname)
        exchange_name = exchange + DATANAME_SEPARATOR + asset_type
        normalized_topics, subscribe_bar_num = self._normalize_subscribe_topics(topics)
        exchange_params = self._copy_exchange_params(self.exchange_kwargs.get(exchange_name, {}))
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            raise SubscribeError(exchange_name, detail="exchange not registered")

        subscribe_handler = ExchangeRegistry.get_stream_class(exchange_name, "subscribe")
        if subscribe_handler is None:
            raise CapabilityNotSupportedError(
                "subscribe", detail=f"no stream handler registered for {exchange_name}"
            )
        subscribe_handler(data_queue, exchange_params, normalized_topics, self)
        self.subscribe_bar_num += subscribe_bar_num

    def push_bar_data_to_queue(self, exchange_name: str, data: Any) -> None:
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            raise ExchangeNotFoundError(exchange_name, list(self.data_queues.keys()))
        bar_list = data.get_data()
        for bar in bar_list:
            data_queue.put(bar)

    def get_event_bus(self) -> EventBus:
        """获取事件总线实例"""
        return self.event_bus

    def put_ticker(self, ticker_data: Any, exchange_name: str | None = None) -> Any:
        """Push a simulated ticker update into the event bus and optional exchange queue."""
        self.event_bus.emit("ticker", ticker_data)
        if exchange_name is not None and exchange_name in self.data_queues:
            self.data_queues[exchange_name].put(ticker_data)
        return ticker_data

    def list_exchanges(self) -> list[str]:
        """列出所有已添加的交易所"""
        return list(self.exchange_feeds.keys())

    def close(self) -> None:
        """Close all exchange feeds (WebSocket streams + HTTP clients)."""
        errors: list[str] = []
        for exchange_name, feed in self.exchange_feeds.items():
            try:
                if hasattr(feed, "disconnect"):
                    feed.disconnect()
            except Exception as exc:
                errors.append(f"{exchange_name}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("failed to close feeds: " + "; ".join(errors))

    def __enter__(self) -> BtApi:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()

    async def __aenter__(self) -> BtApi:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.async_close()

    async def async_close(self) -> None:
        """Close all exchange feeds (WebSocket streams + HTTP clients)."""
        # Feed.disconnect() is sync-only; no async variant exists in the base class.
        errors: list[str] = []
        for exchange_name, feed in self.exchange_feeds.items():
            try:
                if hasattr(feed, "disconnect"):
                    feed.disconnect()
            except Exception as exc:
                errors.append(f"{exchange_name}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("failed to close feeds: " + "; ".join(errors))

    @staticmethod
    def list_available_exchanges() -> list[str]:
        """列出所有已注册可用的交易所"""
        return ExchangeRegistry.list_exchanges()

    # ══════════════════════════════════════════════════════════════
    # 统一接口 — 直接在 BtApi 上调用，自动路由到对应交易所的 Feed
    # 用法:
    #   bt_api.get_tick("BINANCE___SWAP", "BTC-USDT")
    #   bt_api.make_order("OKX___SWAP", "BTC-USDT", 0.001, 50000, "limit")
    # 原有接口 (get_request_api -> feed.method) 保持不变
    # ══════════════════════════════════════════════════════════════

    def _get_feed(self, exchange_name: str) -> Any:
        """获取指定交易所的 Feed 实例"""
        feed = self.exchange_feeds.get(exchange_name)
        if feed is None:
            raise ExchangeNotFoundError(exchange_name, list(self.exchange_feeds.keys()))
        return feed

    # ── 行情查询（同步）────────────────────────────────────────────

    def get_tick(
        self, exchange_name: str, symbol: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """获取最新行情
        :param exchange_name: 交易所标识, 如 "BINANCE___SWAP"
        :param symbol: 交易对, 如 "BTC-USDT"
        """
        return self._get_feed(exchange_name).get_tick(symbol, extra_data=extra_data, **kwargs)

    def get_depth(
        self,
        exchange_name: str,
        symbol: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """获取深度数据
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param count: 深度档数
        """
        return self._get_feed(exchange_name).get_depth(
            symbol, count=count, extra_data=extra_data, **kwargs
        )

    def get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """获取K线数据
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param period: K线周期, 如 "1m", "5m", "1H", "1D"
        :param count: K线数量
        """
        return self._get_feed(exchange_name).get_kline(
            symbol, period, count=count, extra_data=extra_data, **kwargs
        )

    # ── 交易操作（同步）────────────────────────────────────────────

    def make_order(
        self,
        exchange_name: str,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """下单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param volume: 数量
        :param price: 价格 (市价单传0)
        :param order_type: 订单类型, "limit" / "market"
        :param offset: 开平方向, "open" / "close" / "close_today" / "close_yesterday"
        :param post_only: 是否只做 maker
        :param client_order_id: 客户端自定义订单ID
        """
        normalized_order_type = self._validate_order_args(
            exchange_name=exchange_name,
            symbol=symbol,
            volume=volume,
            price=price,
            order_type=order_type,
        )
        return self._get_feed(exchange_name).make_order(
            symbol,
            volume,
            price,
            normalized_order_type,
            offset=offset,
            post_only=post_only,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )

    def cancel_order(
        self, exchange_name: str, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """撤单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param order_id: 订单ID
        """
        return self._get_feed(exchange_name).cancel_order(
            symbol, order_id, extra_data=extra_data, **kwargs
        )

    def cancel_all(
        self, exchange_name: str, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """撤销所有订单
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        return self._get_feed(exchange_name).cancel_all(symbol, extra_data=extra_data, **kwargs)

    def query_order(
        self, exchange_name: str, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询订单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param order_id: 订单ID
        """
        return self._get_feed(exchange_name).query_order(
            symbol, order_id, extra_data=extra_data, **kwargs
        )

    def get_open_orders(
        self, exchange_name: str, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询挂单
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        return self._get_feed(exchange_name).get_open_orders(
            symbol, extra_data=extra_data, **kwargs
        )

    def get_deals(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询账户成交/成交明细，用于获取真实手续费。

        Different exchange feeds expose private fills as ``get_deals``. Keep
        the unified facade thin so callers can pass through exchange-specific
        arguments such as ``limit``, ``count``, ``start_time`` or ``end_time``.
        """
        return self._get_feed(exchange_name).get_deals(symbol, extra_data=extra_data, **kwargs)

    def get_trades(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询成交记录。

        Some venues use this for public recent trades, while gateway adapters
        may map it to account fills. Callers that require real account fees
        should prefer :meth:`get_deals` when the feed supports it.
        """
        return self._get_feed(exchange_name).get_trades(symbol, extra_data=extra_data, **kwargs)

    # ── 账户查询（同步）────────────────────────────────────────────

    def get_balance(
        self, exchange_name: str, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询余额
        :param exchange_name: 交易所标识
        :param symbol: 币种 (None 表示全部)
        """
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_balance(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return self._get_feed(exchange_name).get_balance(symbol, extra_data=extra_data, **kwargs)

    def get_account(
        self, exchange_name: str, symbol: str = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询账户信息
        :param exchange_name: 交易所标识
        :param symbol: 币种
        """
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_account(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return self._get_feed(exchange_name).get_account(symbol, extra_data=extra_data, **kwargs)

    def get_position(
        self, exchange_name: str, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询持仓
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_position(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return self._get_feed(exchange_name).get_position(symbol, extra_data=extra_data, **kwargs)

    @staticmethod
    def _pop_consistency(kwargs: dict[str, Any]) -> Consistency:
        value = kwargs.pop("consistency", Consistency.LIVE)
        if isinstance(value, Consistency):
            return value
        return Consistency(str(value))

    def get_capabilities(self, exchange_name: str) -> Any:
        """Return a read-only capability report for the given exchange."""
        from ._contracts.capabilities import CapabilityReport

        if self.transport_mode is TransportMode.ZMQ:
            return CapabilityReport(exchange_name=exchange_name, status="experimental")
        feed = self.exchange_feeds.get(exchange_name)
        if feed is None:
            return CapabilityReport(exchange_name=exchange_name, status="retired")
        capabilities = getattr(feed, "capabilities", None)
        operations: dict[str, bool] = {}
        if capabilities is not None and hasattr(capabilities, "as_dict"):
            operations = capabilities.as_dict()
        return CapabilityReport(
            exchange_name=exchange_name,
            status="loadable",
            operations=operations,
        )

    # ── 异步接口（显式方法，替代动态 __getattr__ 代理）────────────────

    async def async_get_tick(
        self, exchange_name: str, symbol: str, *args: Any, **kwargs: Any
    ) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_tick(exchange_name, symbol)
        return await self._get_feed(exchange_name).async_get_tick(symbol, *args, **kwargs)

    async def async_get_depth(
        self, exchange_name: str, symbol: str, count: int = 20, **kwargs: Any
    ) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_depth(exchange_name, symbol, count)
        return await self._get_feed(exchange_name).async_get_depth(symbol, count=count, **kwargs)

    async def async_get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 20,
        **kwargs: Any,
    ) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_kline(exchange_name, symbol, period, count)
        return await self._get_feed(exchange_name).async_get_kline(
            symbol, period, count=count, **kwargs
        )

    async def async_make_order(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_make_order", detail="ZMQ order submission uses typed OrderRequest"
            )
        return await self._get_feed(exchange_name).async_make_order(*args, **kwargs)

    async def async_cancel_order(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_cancel_order", detail="ZMQ cancel uses typed CancelOrderRequest"
            )
        return await self._get_feed(exchange_name).async_cancel_order(*args, **kwargs)

    async def async_cancel_all(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_cancel_all", detail="ZMQ cancel-all uses typed CancelAllRequest"
            )
        return await self._get_feed(exchange_name).async_cancel_all(*args, **kwargs)

    async def async_query_order(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_query_order", detail="ZMQ query uses typed QueryOrderRequest"
            )
        return await self._get_feed(exchange_name).async_query_order(*args, **kwargs)

    async def async_get_open_orders(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_open_orders(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._get_feed(exchange_name).async_get_open_orders(*args, **kwargs)

    async def async_get_balance(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_balance(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._get_feed(exchange_name).async_get_balance(*args, **kwargs)

    async def async_get_account(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_account(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._get_feed(exchange_name).async_get_account(*args, **kwargs)

    async def async_get_position(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_position(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._get_feed(exchange_name).async_get_position(*args, **kwargs)

    # ── 批量操作 ───────────────────────────────────────────────────

    def get_all_ticks(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> dict[str, Any]:
        """从所有已连接的交易所获取行情
        :param symbol: 交易对
        :return: dict {exchange_name: ticker_data 或 Exception}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_tick(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"get_tick failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results

    def get_all_balances(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """从所有已连接的交易所查询余额
        :return: dict {exchange_name: balance_data 或 Exception}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_balance(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"get_balance failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results

    def get_all_positions(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """从所有已连接的交易所查询持仓
        :return: dict {exchange_name: position_data 或 Exception}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_position(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"get_position failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results

    def cancel_all_orders(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """撤销所有已连接交易所的所有订单
        :return: dict {exchange_name: result 或 Exception}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.cancel_all(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"cancel_all failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results
