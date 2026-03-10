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
from datetime import UTC, datetime, timedelta

from bt_api_py.event_bus import EventBus
from bt_api_py.exceptions import (
    BtApiError,
    ExchangeNotFoundError,
    RequestError,
    RequestFailedError,
    RequestTimeoutError,
)
from bt_api_py.logging_factory import get_logger
from bt_api_py.registry import ExchangeRegistry

__all__ = ["BtApi"]

_reg_logger = get_logger("registry")

import bt_api_py.exchange_registers as _exchange_reg_pkg  # noqa: E402

for _finder, _name, _ispkg in pkgutil.iter_modules(_exchange_reg_pkg.__path__):
    try:
        importlib.import_module(f"bt_api_py.exchange_registers.{_name}")
    except ImportError as e:
        _reg_logger.debug(f"{_name} register skipped: {e}")


class BtApi:
    def __init__(self, exchange_kwargs=None, debug=True, event_bus=None):
        self.exchange_kwargs = exchange_kwargs
        self.debug = debug  # 是否是debug模式，默认是
        self.data_queues = {}  # 保存各个交易所的数据队列
        self.exchange_feeds = {}  # 保存各个交易所的feed接口
        self.logger = self.init_logger()  # 初始化日志
        self._value_dict = {}  # 保存各个交易所账户的净值
        self._cash_dict = {}  # 保存各个交易所账户的现金
        self.subscribe_bar_num = 0  # 记录订阅了多少个品种的K线
        self.event_bus = event_bus or EventBus()  # 事件总线，支持回调模式
        self._subscription_flags = {}  # 跟踪各交易所账户订阅状态
        self.init_exchange(exchange_kwargs)  # 根据提供的交易所列表进行相应的初始化

    def init_exchange(self, exchange_kwargs):
        if exchange_kwargs is None:
            return
        for exchange_name in exchange_kwargs:
            exchange_params = exchange_kwargs[exchange_name]
            self.add_exchange(exchange_name, exchange_params)

    def init_logger(self):
        return get_logger("api", print_info=bool(self.debug))

    def log(self, txt, level="info"):
        if level == "info":
            self.logger.info(txt)
        elif level == "warning":
            self.logger.warning(txt)
        elif level == "error":
            self.logger.error(txt)
        elif level == "debug":
            self.logger.debug(txt)
        else:
            self.logger.warning(f"Unknown log level '{level}', message: {txt}")

    def add_exchange(self, exchange_name, exchange_params):
        """通过 ExchangeRegistry 创建 feed，无需硬编码交易所类型"""
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

    def get_request_api(self, exchange_name):
        api = self.exchange_feeds.get(exchange_name, None)
        if api is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return api

    def get_async_request_api(self, exchange_name):
        return self.get_request_api(exchange_name)

    def get_data_queue(self, exchange_name):
        data_queue = self.data_queues.get(exchange_name, None)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return data_queue

    def subscribe(self, dataname, topics):
        """通过 ExchangeRegistry 查找订阅处理函数，无需硬编码交易所类型"""
        exchange, asset_type, symbol = dataname.split("___")
        exchange_name = exchange + "___" + asset_type
        exchange_params = self.exchange_kwargs[exchange_name]
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

    def push_bar_data_to_queue(self, exchange_name, data):
        data_queue = self.get_data_queue(exchange_name)
        bar_list = data.get_data()
        for bar in bar_list:
            data_queue.put(bar)

    def download_history_bars(
        self,
        exchange_name,
        symbol,
        period,
        count=100,
        start_time=None,
        end_time=None,
        extra_data=None,
    ):
        def calculate_time_delta(period_):
            """根据 period 计算增量时间"""
            time_deltas = {
                "1m": timedelta(hours=1),
                "3m": timedelta(hours=5),
                "5m": timedelta(hours=9),
                "15m": timedelta(hours=25),
                "30m": timedelta(hours=50),
                "1H": timedelta(hours=100),
                "1D": timedelta(days=100),
            }
            if period_ in time_deltas:
                return time_deltas[period_]
            raise ValueError(f"Unsupported period: {period_}")

        def parse_time(input_time):
            """解析时间，支持字符串和 datetime 类型，并将时间转换为 UTC"""
            if isinstance(input_time, str):
                local_time = datetime.fromisoformat(input_time)
                return local_time.astimezone(UTC)
            elif isinstance(input_time, datetime):
                if input_time.tzinfo is None:
                    local_time = input_time.replace(tzinfo=UTC).astimezone()
                else:
                    local_time = input_time
                return local_time.astimezone(UTC)
            elif input_time is None:
                return None
            else:
                raise TypeError(f"Unsupported time format: {type(input_time)}")

        begin_time = parse_time(start_time)
        stop_time = parse_time(end_time)

        feed = self.exchange_feeds[exchange_name]
        if begin_time is None and count is not None:
            data = feed.get_kline(symbol, period, count, extra_data=extra_data)
            self.push_bar_data_to_queue(exchange_name, data)
            self.log(f"download completely: {symbol}, new {count} bar")
            return

        if begin_time is not None:
            if stop_time is None:
                now = datetime.now(UTC)
                period_seconds = int(period[:-1]) * 60 if "m" in period else int(period[:-1]) * 3600
                stop_time = now - timedelta(seconds=now.timestamp() % period_seconds)

            while begin_time < stop_time:
                try:
                    time_delta = calculate_time_delta(period)
                    current_end_time = min(begin_time + time_delta, stop_time)

                    begin_stamp = int(1000.0 * begin_time.timestamp())
                    end_stamp = int(1000.0 * current_end_time.timestamp())

                    data = feed.get_kline(
                        symbol,
                        period,
                        start_time=begin_stamp,
                        end_time=end_stamp,
                        extra_data=extra_data,
                    )
                    self.push_bar_data_to_queue(exchange_name, data)
                    self.log(
                        f"download successfully: {symbol}, period: {period}, "
                        f"begin: {begin_time}, end: {current_end_time}"
                    )

                    begin_time = current_end_time

                    if begin_time >= stop_time:
                        break
                except (
                    RequestError,
                    RequestTimeoutError,
                    RequestFailedError,
                    ValueError,
                    KeyError,
                ) as e:
                    self.log(f"download fail, retry: {e}", level="warning")
                    time.sleep(3)
            self.log(f"download all data completely: {symbol}, period: {period}")

    def update_total_balance(self):
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

    def update_balance(self, exchange_name, currency=None):
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
            elif currency is None:
                self._value_dict[exchange_name][account.get_account_type()]["value"] = (
                    account.get_margin() + account.get_unrealized_profit()
                )
                self._cash_dict[exchange_name][account.get_account_type()]["cash"] = (
                    account.get_available_margin()
                )

    def get_cash(self, exchange_name, currency):
        return self._cash_dict[exchange_name][currency]["cash"]

    def get_value(self, exchange_name, currency):
        return self._value_dict[exchange_name][currency]["value"]

    def get_total_cash(self):
        return self._cash_dict

    def get_total_value(self):
        return self._value_dict

    def get_event_bus(self):
        """获取事件总线实例"""
        return self.event_bus

    def put_ticker(self, ticker_data, exchange_name=None):
        """Push a simulated ticker update into the event bus and optional exchange queue."""
        self.event_bus.emit("ticker", ticker_data)
        if exchange_name is not None and exchange_name in self.data_queues:
            self.data_queues[exchange_name].put(ticker_data)
        return ticker_data

    def list_exchanges(self):
        """列出所有已添加的交易所"""
        return list(self.exchange_feeds.keys())

    @staticmethod
    def list_available_exchanges():
        """列出所有已注册可用的交易所"""
        return ExchangeRegistry.list_exchanges()

    # ══════════════════════════════════════════════════════════════
    # 统一接口 — 直接在 BtApi 上调用，自动路由到对应交易所的 Feed
    # 用法:
    #   bt_api.get_tick("BINANCE___SWAP", "BTC-USDT")
    #   bt_api.make_order("OKX___SWAP", "BTC-USDT", 0.001, 50000, "limit")
    # 原有接口 (get_request_api -> feed.method) 保持不变
    # ══════════════════════════════════════════════════════════════

    def _get_feed(self, exchange_name):
        """获取 feed 实例，不存在时抛出 ExchangeNotFoundError"""
        feed = self.exchange_feeds.get(exchange_name)
        if feed is None:
            raise ExchangeNotFoundError(exchange_name, list(self.exchange_feeds.keys()))
        return feed

    # ── 行情查询（同步）────────────────────────────────────────────

    def get_tick(self, exchange_name, symbol, extra_data=None, **kwargs):
        """获取最新行情
        :param exchange_name: 交易所标识, 如 "BINANCE___SWAP"
        :param symbol: 交易对, 如 "BTC-USDT"
        """
        return self._get_feed(exchange_name).get_tick(symbol, extra_data=extra_data, **kwargs)

    def get_depth(self, exchange_name, symbol, count=20, extra_data=None, **kwargs):
        """获取深度数据
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param count: 深度档数
        """
        return self._get_feed(exchange_name).get_depth(
            symbol, count=count, extra_data=extra_data, **kwargs
        )

    def get_kline(self, exchange_name, symbol, period, count=20, extra_data=None, **kwargs):
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
        exchange_name,
        symbol,
        volume,
        price,
        order_type,
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
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
        return self._get_feed(exchange_name).make_order(
            symbol,
            volume,
            price,
            order_type,
            offset=offset,
            post_only=post_only,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )

    def cancel_order(self, exchange_name, symbol, order_id, extra_data=None, **kwargs):
        """撤单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param order_id: 订单ID
        """
        return self._get_feed(exchange_name).cancel_order(
            symbol, order_id, extra_data=extra_data, **kwargs
        )

    def cancel_all(self, exchange_name, symbol=None, extra_data=None, **kwargs):
        """撤销所有订单
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        return self._get_feed(exchange_name).cancel_all(symbol, extra_data=extra_data, **kwargs)

    def query_order(self, exchange_name, symbol, order_id, extra_data=None, **kwargs):
        """查询订单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param order_id: 订单ID
        """
        return self._get_feed(exchange_name).query_order(
            symbol, order_id, extra_data=extra_data, **kwargs
        )

    def get_open_orders(self, exchange_name, symbol=None, extra_data=None, **kwargs):
        """查询挂单
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        return self._get_feed(exchange_name).get_open_orders(
            symbol, extra_data=extra_data, **kwargs
        )

    # ── 账户查询（同步）────────────────────────────────────────────

    def get_balance(self, exchange_name, symbol=None, extra_data=None, **kwargs):
        """查询余额
        :param exchange_name: 交易所标识
        :param symbol: 币种 (None 表示全部)
        """
        return self._get_feed(exchange_name).get_balance(symbol, extra_data=extra_data, **kwargs)

    def get_account(self, exchange_name, symbol="ALL", extra_data=None, **kwargs):
        """查询账户信息
        :param exchange_name: 交易所标识
        :param symbol: 币种
        """
        return self._get_feed(exchange_name).get_account(symbol, extra_data=extra_data, **kwargs)

    def get_position(self, exchange_name, symbol=None, extra_data=None, **kwargs):
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

    def __getattr__(self, name):
        if name.startswith("async_"):

            def _async_proxy(exchange_name, *args, **kwargs):
                feed = self._get_feed(exchange_name)
                feed_method = getattr(feed, name, None)
                if feed_method is None:
                    raise AttributeError(f"Feed for {exchange_name} has no method {name!r}")
                return feed_method(*args, **kwargs)

            _async_proxy.__name__ = name
            _async_proxy.__doc__ = f"异步代理 → feed.{name}()，结果推送到 data_queue"
            return _async_proxy
        raise AttributeError(f"'BtApi' object has no attribute {name!r}")

    # ── 批量操作 ───────────────────────────────────────────────────

    def get_all_ticks(self, symbol, extra_data=None, **kwargs):
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

    def get_all_balances(self, symbol=None, extra_data=None, **kwargs):
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

    def get_all_positions(self, symbol=None, extra_data=None, **kwargs):
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

    def cancel_all_orders(self, symbol=None, extra_data=None, **kwargs):
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
