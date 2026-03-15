"""
Integrate all exchange APIs using this BtApi class
通过 ExchangeRegistry 实现交易所的即插即用，新增交易所无需修改此文件
"""

# 导入注册模块，确保交易所在使用前完成注册
# 自动扫描 exchange_registers/ 下所有模块，无需手动维护 import 列表
import importlib
import pkgutil
import queue
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from bt_api_py.event_bus import EventBus
from bt_api_py.exceptions import (
    BtApiError,
    CurrencyNotFoundError,
    DataParseError,
    ExchangeNotFoundError,
    InvalidOrderError,
    RequestError,
    RequestFailedError,
    RequestTimeoutError,
    SubscribeError,
)
from bt_api_py.logging_factory import _LoggerProxy, get_logger
from bt_api_py.registry import ExchangeRegistry

__all__ = ["BtApi"]

DATANAME_SEPARATOR = "___"
DOWNLOAD_RETRY_DELAY_SEC = 3
DOWNLOAD_MAX_RETRIES = 10
DOWNLOAD_RETRY_BACKOFF_FACTOR = 2.0
DOWNLOAD_RETRY_MAX_DELAY_SEC = 60
KLINE_PERIOD_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1H": timedelta(hours=1),
    "1D": timedelta(days=1),
}


def _calculate_time_delta(period: str) -> timedelta:
    if period in KLINE_PERIOD_DELTAS:
        return KLINE_PERIOD_DELTAS[period]
    raise DataParseError(detail=f"Unsupported period: {period}")


def _parse_time(input_time: str | datetime | None) -> datetime | None:
    if isinstance(input_time, str):
        try:
            local_time = datetime.fromisoformat(input_time)
        except ValueError as e:
            raise DataParseError(detail=f"Invalid ISO time format: {input_time}") from e
        return local_time.astimezone(UTC)
    if isinstance(input_time, datetime):
        local_time = (
            input_time.replace(tzinfo=UTC).astimezone() if input_time.tzinfo is None else input_time
        )
        return local_time.astimezone(UTC)
    if input_time is None:
        return None
    raise DataParseError(detail=f"Unsupported time format: {type(input_time)}")


_reg_logger = get_logger("registry")

import bt_api_py.exchange_registers as _exchange_reg_pkg  # noqa: E402

for _finder, _name, _ispkg in pkgutil.iter_modules(_exchange_reg_pkg.__path__):
    try:
        importlib.import_module(f"bt_api_py.exchange_registers.{_name}")
    except ImportError as e:
        _reg_logger.debug(f"{_name} register skipped: {e}")


class BtApi:
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
    _async_proxy_cache: dict[str, Callable[..., Any]]

    def __init__(
        self,
        exchange_kwargs: dict[str, Any] | None = None,
        debug: bool = True,
        event_bus: EventBus | None = None,
    ) -> None:
        """初始化 BtApi 实例。

        Args:
            exchange_kwargs: 交易所配置 dict，key 为 exchange_name，value 为对应参数。
            debug: 是否开启 debug 模式，控制日志输出。
            event_bus: 事件总线实例，用于 BarEvent/OrderEvent 等回调；None 则创建默认实例。
        """
        self.exchange_kwargs = exchange_kwargs or {}
        self.debug = debug
        self.data_queues = {}
        self.exchange_feeds = {}
        self.logger = self.init_logger()
        self._value_dict = {}
        self._cash_dict = {}
        self.subscribe_bar_num = 0
        self.event_bus = event_bus or EventBus()
        self._subscription_flags = {}
        self._async_proxy_cache = {}
        self.init_exchange(exchange_kwargs or {})

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
            self.data_queues[exchange_name] = queue.Queue()
            self.log(f"adding exchange: {exchange_name}")
            data_queue = self.get_data_queue(exchange_name)
            self.exchange_feeds[exchange_name] = ExchangeRegistry.create_feed(
                exchange_name, data_queue, **exchange_params
            )
        else:
            self.log(f"exchange_name: {exchange_name} already exists")

    def get_request_api(self, exchange_name: str) -> Any:
        """Get the REST Feed instance for the specified exchange (synchronous API).

        Args:
            exchange_name: Exchange identifier (e.g., "BINANCE___SPOT")

        Returns:
            Feed instance if exchange exists, None otherwise

        Example:
            >>> api = BtApi({"BINANCE___SPOT": {...}})
            >>> feed = api.get_request_api("BINANCE___SPOT")
            >>> ticker = feed.get_tick("BTCUSDT")
        """
        api = self.exchange_feeds.get(exchange_name)
        if api is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return api

    def get_async_request_api(self, exchange_name: str) -> Any:
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
        exchange_params = self.exchange_kwargs.get(exchange_name, {})
        for topic in topics:
            if topic["topic"] == "kline":
                self.subscribe_bar_num += 1
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
            return

        subscribe_handler = ExchangeRegistry.get_stream_class(exchange_name, "subscribe")
        if subscribe_handler is not None:
            subscribe_handler(data_queue, exchange_params, topics, self)
        else:
            self.log(f"No subscribe handler registered for {exchange_name}", level="error")

    def push_bar_data_to_queue(self, exchange_name: str, data: Any) -> None:
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            raise ExchangeNotFoundError(exchange_name, list(self.data_queues.keys()))
        bar_list = data.get_data()
        for bar in bar_list:
            data_queue.put(bar)

    def download_history_bars(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 100,
        start_time: str | datetime | None = None,
        end_time: str | datetime | None = None,
        extra_data: Any = None,
    ) -> None:
        begin_time = _parse_time(start_time)
        stop_time = _parse_time(end_time)
        feed = self._get_feed(exchange_name)

        if begin_time is None:
            self._download_kline_by_count(feed, exchange_name, symbol, period, count, extra_data)
            return

        self._download_kline_by_range(
            feed, exchange_name, symbol, period, begin_time, stop_time, extra_data
        )

    def _download_kline_by_count(
        self,
        feed: Any,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int,
        extra_data: Any,
    ) -> None:
        data = feed.get_kline(symbol, period, count, extra_data=extra_data)
        self.push_bar_data_to_queue(exchange_name, data)
        self.log(f"download completely: {symbol}, new {count} bar")

    def _download_kline_by_range(
        self,
        feed: Any,
        exchange_name: str,
        symbol: str,
        period: str,
        begin_time: datetime,
        stop_time: datetime | None,
        extra_data: Any,
    ) -> None:
        if stop_time is None:
            stop_time = self._calculate_aligned_stop_time(period)

        retry_count = 0
        current_delay = DOWNLOAD_RETRY_DELAY_SEC
        while begin_time < stop_time:
            if retry_count >= DOWNLOAD_MAX_RETRIES:
                self.log(
                    f"download aborted: max retries ({DOWNLOAD_MAX_RETRIES}) exceeded for {symbol}",
                    level="error",
                )
                return
            try:
                begin_time = self._download_single_batch(
                    feed, exchange_name, symbol, period, begin_time, stop_time, extra_data
                )
                retry_count = 0
                current_delay = DOWNLOAD_RETRY_DELAY_SEC
            except (
                RequestError,
                RequestTimeoutError,
                RequestFailedError,
                ValueError,
                KeyError,
            ) as e:
                retry_count += 1
                self.log(
                    f"download fail (attempt {retry_count}/{DOWNLOAD_MAX_RETRIES}), "
                    f"retry in {current_delay}s: {e}",
                    level="warning",
                )
                time.sleep(current_delay)
                current_delay = min(
                    current_delay * DOWNLOAD_RETRY_BACKOFF_FACTOR,
                    DOWNLOAD_RETRY_MAX_DELAY_SEC,
                )

        self.log(f"download all data completely: {symbol}, period: {period}")

    def _calculate_aligned_stop_time(self, period: str) -> datetime:
        now = datetime.now(UTC)
        period_seconds = int(period[:-1]) * (60 if "m" in period else 3600)
        return now - timedelta(seconds=now.timestamp() % period_seconds)

    def _download_single_batch(
        self,
        feed: Any,
        exchange_name: str,
        symbol: str,
        period: str,
        begin_time: datetime,
        stop_time: datetime,
        extra_data: Any,
    ) -> datetime:
        time_delta = _calculate_time_delta(period)
        current_end_time = min(begin_time + time_delta, stop_time)

        begin_stamp = int(1000.0 * begin_time.timestamp())
        end_stamp = int(1000.0 * current_end_time.timestamp())

        data = feed.get_kline(
            symbol, period, start_time=begin_stamp, end_time=end_stamp, extra_data=extra_data
        )
        self.push_bar_data_to_queue(exchange_name, data)
        self.log(
            f"download successfully: {symbol}, period: {period}, "
            f"begin: {begin_time}, end: {current_end_time}"
        )

        return current_end_time

    def update_total_balance(self) -> None:
        """通过 ExchangeRegistry 查找余额解析函数，无需硬编码交易所类型"""
        for exchange_name in self.exchange_feeds:
            feed = self.exchange_feeds[exchange_name]
            balance_data = feed.get_balance()
            balance_data.init_data()
            account_list = balance_data.get_data()

            balance_handler = ExchangeRegistry.get_balance_handler(exchange_name)
            if balance_handler is not None:
                value_result, cash_result = balance_handler(account_list)
                self._value_dict[exchange_name] = value_result
                self._cash_dict[exchange_name] = cash_result
            else:
                self.log(f"No balance handler registered for {exchange_name}", level="warning")

    def update_balance(self, exchange_name: str, currency: str | None = None) -> None:
        feed = self.exchange_feeds[exchange_name]
        balance_data = feed.get_balance()
        balance_data.init_data()
        account_list = balance_data.get_data()
        for account in account_list:
            account.init_data()
            if currency is not None:
                if account.get_account_type() == currency:
                    self._value_dict[exchange_name][currency]["value"] = (
                        account.get_margin() + account.get_unrealized_profit()
                    )
                    self._cash_dict[exchange_name][currency]["cash"] = (
                        account.get_available_margin()
                    )
            else:
                self._value_dict[exchange_name][account.get_account_type()]["value"] = (
                    account.get_margin() + account.get_unrealized_profit()
                )
                self._cash_dict[exchange_name][account.get_account_type()]["cash"] = (
                    account.get_available_margin()
                )

    def get_cash(self, exchange_name: str, currency: str) -> float:
        if exchange_name not in self._cash_dict:
            raise ExchangeNotFoundError(exchange_name, list(self._cash_dict.keys()))
        if currency not in self._cash_dict[exchange_name]:
            raise CurrencyNotFoundError(exchange_name, currency)
        return self._cash_dict[exchange_name][currency]["cash"]

    def get_value(self, exchange_name: str, currency: str) -> float:
        if exchange_name not in self._value_dict:
            raise ExchangeNotFoundError(exchange_name, list(self._value_dict.keys()))
        if currency not in self._value_dict[exchange_name]:
            raise CurrencyNotFoundError(exchange_name, currency)
        return self._value_dict[exchange_name][currency]["value"]

    def get_total_cash(self) -> dict[str, Any]:
        return self._cash_dict

    def get_total_value(self) -> dict[str, Any]:
        return self._value_dict

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
        """关闭所有 feed 的 HTTP 连接，释放资源。"""
        for exchange_name, feed in self.exchange_feeds.items():
            client = getattr(feed, "_http_client", None)
            if client is not None and hasattr(client, "close"):
                try:
                    client.close()
                except Exception as e:
                    self.log(
                        f"Error closing feed client for {exchange_name}: {type(e).__name__}: {e}",
                        level="warning",
                    )

    def __enter__(self) -> "BtApi":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()

    async def __aenter__(self) -> "BtApi":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.async_close()

    async def async_close(self) -> None:
        """异步关闭所有 feed 的 HTTP 连接，释放资源。"""
        for exchange_name, feed in self.exchange_feeds.items():
            client = getattr(feed, "_http_client", None)
            if client is not None and hasattr(client, "async_close"):
                try:
                    await client.async_close()
                except Exception as e:
                    self.log(
                        f"Error async closing feed client for {exchange_name}: "
                        f"{type(e).__name__}: {e}",
                        level="warning",
                    )
            elif client is not None and hasattr(client, "close"):
                try:
                    client.close()
                except Exception as e:
                    self.log(
                        f"Error closing feed client for {exchange_name}: {type(e).__name__}: {e}",
                        level="warning",
                    )

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

    # ── 账户查询（同步）────────────────────────────────────────────

    def get_balance(
        self, exchange_name: str, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询余额
        :param exchange_name: 交易所标识
        :param symbol: 币种 (None 表示全部)
        """
        return self._get_feed(exchange_name).get_balance(symbol, extra_data=extra_data, **kwargs)

    def get_account(
        self, exchange_name: str, symbol: str = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询账户信息
        :param exchange_name: 交易所标识
        :param symbol: 币种
        """
        return self._get_feed(exchange_name).get_account(symbol, extra_data=extra_data, **kwargs)

    def get_position(
        self, exchange_name: str, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询持仓
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        return self._get_feed(exchange_name).get_position(symbol, extra_data=extra_data, **kwargs)

    # ── 异步接口（自动代理）──────────────────────────────────────────
    # async_get_tick / async_make_order / async_cancel_order 等异步方法
    # 通过 __getattr__ 自动生成，调用对应 feed 的 async_* 方法。
    # 用法与手写版完全一致:
    #   bt_api.async_get_tick("BINANCE___SWAP", "BTC-USDT")
    #   bt_api.async_make_order("OKX___SWAP", "BTC-USDT", 0.001, 50000, "limit")

    def __getattr__(self, name: str) -> Any:
        """动态代理 async_* 方法到对应 Feed 实例（带缓存）。"""
        if name.startswith("async_"):
            cache = self.__dict__.setdefault("_async_proxy_cache", {})
            if name in cache:
                return cache[name]

            def _async_proxy(exchange_name: str, *args: Any, **kwargs: Any) -> Any:
                feed = self._get_feed(exchange_name)
                feed_method = getattr(feed, name, None)
                if feed_method is None:
                    raise AttributeError(f"Feed for {exchange_name} has no method {name!r}")
                return feed_method(*args, **kwargs)

            _async_proxy.__name__ = name
            cache[name] = _async_proxy
            return _async_proxy
        raise AttributeError(f"'BtApi' object has no attribute {name!r}")

    # ── 批量操作 ───────────────────────────────────────────────────

    def get_all_ticks(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> dict[str, Any]:
        """从所有已连接的交易所获取行情
        :param symbol: 交易对
        :return: dict {exchange_name: ticker_data}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_tick(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except BtApiError as e:
                self.log(f"get_tick failed for {exchange_name}: {e}", level="warning")
        return results

    def get_all_balances(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """从所有已连接的交易所查询余额
        :return: dict {exchange_name: balance_data}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_balance(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except BtApiError as e:
                self.log(f"get_balance failed for {exchange_name}: {e}", level="warning")
        return results

    def get_all_positions(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """从所有已连接的交易所查询持仓
        :return: dict {exchange_name: position_data}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_position(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except BtApiError as e:
                self.log(f"get_position failed for {exchange_name}: {e}", level="warning")
        return results

    def cancel_all_orders(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """撤销所有已连接交易所的所有订单
        :return: dict {exchange_name: result}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.cancel_all(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except BtApiError as e:
                self.log(f"cancel_all failed for {exchange_name}: {e}", level="warning")
        return results
