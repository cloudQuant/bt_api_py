"""
Integrate all exchange APIs using this BtApi class
通过 ExchangeRegistry 实现交易所的即插即用，新增交易所无需修改此文件
"""
import queue
import time
from datetime import datetime, timedelta, timezone
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.event_bus import EventBus
from bt_api_py.exceptions import ExchangeNotFoundError

# 导入注册模块，确保交易所在使用前完成注册
# 使用 try/except 以便在缺少依赖时不阻塞其他交易所的使用
import logging as _logging
_reg_logger = _logging.getLogger(__name__)

try:
    import bt_api_py.feeds.register_binance  # noqa: F401
except ImportError as e:
    _reg_logger.debug(f"Binance register skipped: {e}")

try:
    import bt_api_py.feeds.register_okx  # noqa: F401
except ImportError as e:
    _reg_logger.debug(f"OKX register skipped: {e}")

try:
    import bt_api_py.feeds.register_ctp  # noqa: F401
except ImportError as e:
    _reg_logger.debug(f"CTP register skipped (install ctp-python to enable): {e}")

try:
    import bt_api_py.feeds.register_ib  # noqa: F401
except ImportError as e:
    _reg_logger.debug(f"IB register skipped (install ib_insync to enable): {e}")

try:
    import bt_api_py.feeds.register_ib_web  # noqa: F401
except ImportError as e:
    _reg_logger.debug(f"IB Web API register skipped: {e}")


class BtApi(object):
    def __init__(self, exchange_kwargs, debug=True, event_bus=None):
        self.exchange_kwargs = exchange_kwargs
        self.debug = debug                          # 是否是debug模式，默认是
        self.data_queues = {}                       # 保存各个交易所的数据队列
        self.exchange_feeds = {}                    # 保存各个交易所的feed接口
        self.logger = self.init_logger()            # 初始化日志
        self._value_dict = {}                       # 保存各个交易所账户的净值
        self._cash_dict = {}                        # 保存各个交易所账户的现金
        self.subscribe_bar_num = 0                  # 记录订阅了多少个品种的K线
        self.event_bus = event_bus or EventBus()    # 事件总线，支持回调模式
        self._subscription_flags = {}               # 跟踪各交易所账户订阅状态
        self.init_exchange(exchange_kwargs)         # 根据提供的交易所列表进行相应的初始化


    def init_exchange(self, exchange_kwargs):
        if exchange_kwargs is None:
            return
        for exchange_name in exchange_kwargs:
            exchange_params = exchange_kwargs[exchange_name]
            self.add_exchange(exchange_name, exchange_params)


    def init_logger(self):
        if self.debug:
            print_info = True
        else:
            print_info = False
        logger = SpdLogManager(file_name='bt_api.log',
                               logger_name="api",
                               print_info=print_info).create_logger()
        return logger

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
            pass

    def add_exchange(self, exchange_name, exchange_params):
        """通过 ExchangeRegistry 创建 feed，无需硬编码交易所类型"""
        if exchange_name not in self.exchange_feeds:
            if exchange_name in self.data_queues:
                raise ExchangeNotFoundError(exchange_name, "data_queue exists but feed does not — inconsistent state")
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
        exchange, asset_type, symbol = dataname.split('___')
        exchange_name = exchange + "___" + asset_type
        exchange_params = self.exchange_kwargs[exchange_name]
        for topic in topics:
            if topic['topic'] == "kline":
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

    def download_history_bars(self, exchange_name, symbol, period, count=100, start_time=None, end_time=None, extra_data=None):
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
                return local_time.astimezone(timezone.utc)
            elif isinstance(input_time, datetime):
                if input_time.tzinfo is None:
                    local_time = input_time.replace(tzinfo=timezone.utc).astimezone()
                else:
                    local_time = input_time
                return local_time.astimezone(timezone.utc)
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
                now = datetime.now(timezone.utc)
                period_seconds = int(period[:-1]) * 60 if "m" in period else int(period[:-1]) * 3600
                stop_time = now - timedelta(seconds=now.timestamp() % period_seconds)

            while begin_time < stop_time:
                try:
                    time_delta = calculate_time_delta(period)
                    current_end_time = min(begin_time + time_delta, stop_time)

                    begin_stamp = int(1000.0 * begin_time.timestamp())
                    end_stamp = int(1000.0 * current_end_time.timestamp())

                    data = feed.get_kline(
                        symbol, period, start_time=begin_stamp, end_time=end_stamp, extra_data=extra_data
                    )
                    self.push_bar_data_to_queue(exchange_name, data)
                    self.log(f"download successfully: {symbol}, period: {period}, "
                             f"begin: {begin_time}, end: {current_end_time}")

                    begin_time = current_end_time

                    if begin_time >= stop_time:
                        break
                except Exception as e:
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
                    self._value_dict[exchange_name][currency]["value"] = account.get_margin() + account.get_unrealized_profit()
                    self._cash_dict[exchange_name][currency]["cash"] = account.get_available_margin()
            elif currency is None:
                self._value_dict[exchange_name][account.get_account_type()]["value"] = account.get_margin() + account.get_unrealized_profit()
                self._cash_dict[exchange_name][account.get_account_type()]["cash"] = account.get_available_margin()


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

    def list_exchanges(self):
        """列出所有已添加的交易所"""
        return list(self.exchange_feeds.keys())

    @staticmethod
    def list_available_exchanges():
        """列出所有已注册可用的交易所"""
        return ExchangeRegistry.list_exchanges()




