"""
Integrate all exchange APIs using this BtApi class
"""
import queue
import time
from datetime import datetime, timedelta, timezone
from bt_api_py.functions.log_message import SpdLogManager


class BtApi(object):
    def __init__(self, exchange_kwargs, debug=True):
        self.exchange_kwargs = exchange_kwargs
        self.debug = debug                          # 是否是debug模式，默认是
        self.data_queues = {}                       # 保存各个交易所的数据队列
        self.exchange_feeds = {}                    # 保存各个交易所的feed接口
        self.logger = self.init_logger()            # 初始化日志
        self._value_dict = {}                       # 保存各个交易所账户的净值
        self._cash_dict = {}                        # 保存各个交易所账户的现金
        self.subscribe_bar_num = 0                  # 记录订阅了多少个品种的K线
        self.init_exchange(exchange_kwargs)         # 根据提供的交易所列表进行相应的初始化
        self.binance_swap_account_subscribed = 0    # binance的合约账户是否订阅了
        self.binance_spot_account_subscribed = 0    # binance的现货账户是否订阅了


    def init_exchange(self, exchange_kwargs):
        # print("exchange_kwargs: {}".format(exchange_kwargs))
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
        if exchange_name not in self.exchange_feeds:
            assert exchange_name not in self.data_queues
            self.data_queues[exchange_name] = queue.Queue()
            print("exchange_name", exchange_name)
            exchange, asset_type = exchange_name.split('___')
            data_queue = self.get_data_queue(exchange_name)
            if exchange == "BINANCE" and asset_type == "SWAP":
                from bt_api_py.feeds.live_binance_feed import BinanceRequestDataSwap
                self.exchange_feeds[exchange_name] = BinanceRequestDataSwap(data_queue, **exchange_params)

            if exchange == "BINANCE" and asset_type == "SPOT":
                from bt_api_py.feeds.live_binance_feed import BinanceRequestDataSpot
                self.exchange_feeds[exchange_name] =  BinanceRequestDataSpot(data_queue, **exchange_params)

            if exchange == "OKX" and asset_type == "SPOT":
                from bt_api_py.feeds.live_okx_feed import OkxRequestDataSpot
                self.exchange_feeds[exchange_name] = OkxRequestDataSpot(data_queue, **exchange_params)

            if exchange == "OKX" and asset_type == "SWAP":
                from bt_api_py.feeds.live_okx_feed import OkxRequestDataSwap
                self.exchange_feeds[exchange_name] = OkxRequestDataSwap(data_queue, **exchange_params)
        else:
            self.log(f"exchange_name: {exchange_name} already exists")


    def get_request_api(self, exchange_name):
        api = self.exchange_feeds.get(exchange_name, None)
        if api is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return api

    def get_async_request_api(self, exchange_name):
        feed = self.exchange_feeds.get(exchange_name, None)
        if feed is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return feed

    def get_data_queue(self, exchange_name):
        data_queue = self.data_queues.get(exchange_name, None)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return data_queue

    def subscribe(self, dataname, topics):
        exchange, asset_type, symbol = dataname.split('___')
        exchange_name = exchange+"___"+asset_type
        exchange_params = self.exchange_kwargs[exchange_name]
        for topic in topics:
            if topic['topic'] == "kline":
                self.subscribe_bar_num += 1
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        if exchange == "BINANCE" and asset_type == "SWAP":
            self.wss_start_binance_swap(data_queue, exchange_params, topics)
        if exchange == "BINANCE" and asset_type == "SPOT":
            self.wss_start_binance_spot(data_queue, exchange_params, topics)

        if exchange == "OKX" and asset_type == "SPOT":
            self.wss_start_okx_spot(data_queue, exchange_params, topics)

        if exchange == "OKX" and asset_type == "SWAP":
            self.wss_start_okx_swap(data_queue, exchange_params, topics)

    def wss_start_binance_swap(self, data_queue, exchange_params, topics):
        from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
        from bt_api_py.feeds.live_binance_feed import BinanceMarketWssDataSwap
        from bt_api_py.feeds.live_binance_feed import BinanceAccountWssDataSwap
        kwargs = {key:v for key, v in exchange_params.items()}
        kwargs['wss_name'] = 'binance_market_data'
        kwargs["wss_url"] = 'wss://fstream.binance.com/ws'
        kwargs["exchange_data"] = BinanceExchangeDataSwap()
        kwargs['topics'] = topics
        # self.log(f"wss_start_binance_swap kwargs: {kwargs}")
        BinanceMarketWssDataSwap(data_queue, **kwargs).start()
        if self.binance_swap_account_subscribed == 0:
            account_kwargs = {k:v for k, v in kwargs.items()}
            account_kwargs['topics'] =  [
                {"topic": "account"},
                {"topic": "order"},
                {"topic": "trade"},
            ]
            BinanceAccountWssDataSwap(data_queue, **account_kwargs).start()
            # self.log(f"wss_start_binance_swap kwargs: {account_kwargs}")
            self.binance_swap_account_subscribed = 1


    def wss_start_binance_spot(self, data_queue, exchange_params, topics):
        from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSpot
        from bt_api_py.feeds.live_binance_feed import BinanceMarketWssDataSpot
        from bt_api_py.feeds.live_binance_feed import BinanceAccountWssDataSpot
        kwargs = {key: v for key, v in exchange_params.items()}
        kwargs['wss_name'] = 'binance_market_data'
        kwargs["wss_url"] = 'wss://fstream.binance.com/ws'
        kwargs["exchange_data"] = BinanceExchangeDataSpot()
        kwargs['topics'] = topics
        BinanceMarketWssDataSpot(data_queue, **kwargs).start()
        if self.binance_spot_account_subscribed == 0:
            account_kwargs = {k: v for k, v in kwargs.items()}
            account_kwargs['topics'] = [
                {"topic": "account"},
                {"topic": "order"},
                {"topic": "trade"},
            ]
            BinanceAccountWssDataSpot(data_queue, **account_kwargs).start()
            self.binance_spot_account_subscribed = 1


    def wss_start_okx_spot(self, data_queue, exchange_params, topics):
        from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSpot
        from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSpot
        from bt_api_py.feeds.live_okx_feed import OkxAccountWssDataSpot
        from bt_api_py.feeds.live_okx_feed import OkxKlineWssDataSpot
        topic_list = [i['topic'] for i in topics]
        if "kline" in topic_list:
            kline_kwargs = {key: v for key, v in exchange_params.items()}
            kline_kwargs['wss_name'] = 'okx_spot_kline_data'
            kline_kwargs["wss_url"] = 'wss://ws.okx.com:8443/ws/v5/business'
            kline_kwargs["exchange_data"] = OkxExchangeDataSpot()
            kline_topics = [i for i in exchange_params['topics'] if i['topic']=="kline"]
            kline_kwargs['topics'] = kline_topics
            OkxKlineWssDataSpot(data_queue, **kline_kwargs).start()
            self.log(f"kline spot start")

        ticker_true = "ticker" in topic_list
        depth_true = "depth" in topic_list
        funding_rate_true = "funding_rate" in topic_list
        mark_price_true = "mark_price" in topic_list
        if ticker_true or depth_true or funding_rate_true or mark_price_true:
            market_kwargs = {key: v for key, v in exchange_params.items()}
            market_kwargs['wss_name'] = 'okx_spot_market_data'
            market_kwargs["wss_url"] = 'wss://ws.okx.com:8443/ws/v5/public'
            market_kwargs["exchange_data"] = OkxExchangeDataSpot()
            market_topics = [i for i in exchange_params['topics'] if i['topic'] != "kline"]
            market_kwargs['topics'] = market_topics
            OkxMarketWssDataSpot(data_queue, **market_kwargs).start()
            self.log(f"market spot start")

        account_kwargs = {key: v for key, v in exchange_params.items()}
        account_topics = [i for i in topics if
                          (i['topic'] == "account" or i['topic'] == "orders" or i['topic'] == "positions")]
        account_kwargs['topics'] = account_topics
        OkxAccountWssDataSpot(data_queue, **account_kwargs).start()
        self.log(f" account spot start")


    def wss_start_okx_swap(self, data_queue, exchange_params, topics):
        self.log("begin wss_start_okx_swap")
        from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
        from bt_api_py.feeds.live_okx_feed import OkxMarketWssDataSwap
        from bt_api_py.feeds.live_okx_feed import OkxAccountWssDataSwap
        from bt_api_py.feeds.live_okx_feed import OkxKlineWssDataSwap
        topic_list = [i['topic'] for i in topics]
        if "kline" in topic_list:
            self.log("begin to subscribe okx swap kline")
            kline_kwargs = {key: v for key, v in exchange_params.items()}
            kline_kwargs['wss_name'] = 'okx_spot_kline_data'
            kline_kwargs["wss_url"] = 'wss://ws.okx.com:8443/ws/v5/business'
            kline_kwargs["exchange_data"] = OkxExchangeDataSwap()
            kline_topics = [i for i in topics if i['topic'] == "kline"]
            kline_kwargs['topics'] = kline_topics
            # self.log(f"{kline_kwargs} begin to start")
            OkxKlineWssDataSwap(data_queue, **kline_kwargs).start()
            self.log(f"okx_swap kline swap started. ")

        ticker_true = "ticker" in topic_list
        depth_true = "depth" in topic_list
        funding_rate_true = "funding_rate" in topic_list
        mark_price_true = "mark_price" in topic_list
        if ticker_true or depth_true or funding_rate_true or mark_price_true:
            market_kwargs = {key: v for key, v in exchange_params.items()}
            market_kwargs['wss_name'] = 'okx_spot_market_data'
            market_kwargs["wss_url"] = 'wss://ws.okx.com:8443/ws/v5/public'
            market_kwargs["exchange_data"] = OkxExchangeDataSwap()
            market_topics = [i for i in topics if i['topic'] != "kline"]
            market_kwargs['topics'] = market_topics
            OkxMarketWssDataSwap(data_queue, **market_kwargs).start()
            self.log(f"okx_swap market swap started. ")

        account_kwargs = {key: v for key, v in exchange_params.items()}
        account_topics = [i for i in topics if
                          (i['topic'] == "account" or i['topic'] == "orders" or i['topic'] == "positions")]
        account_kwargs['topics'] = account_topics
        account_kwargs['exchange_data'] = OkxExchangeDataSwap()
        OkxAccountWssDataSwap(data_queue, **account_kwargs).start()
        self.log(f"okx swap account swap started. ")



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
                # 假设输入的字符串时间是本地时间
                local_time = datetime.fromisoformat(input_time)
                return local_time.astimezone(timezone.utc)
            elif isinstance(input_time, datetime):
                # 如果是 datetime 类型，确保转换为 UTC
                if input_time.tzinfo is None:
                    local_time = input_time.replace(tzinfo=timezone.utc).astimezone()  # 假设为本地时间
                else:
                    local_time = input_time
                return local_time.astimezone(timezone.utc)
            elif input_time is None:
                return None
            else:
                raise TypeError(f"Unsupported time format: {type(input_time)}")

        # 解析开始时间和结束时间为 UTC
        begin_time = parse_time(start_time)
        stop_time = parse_time(end_time)

        feed = self.exchange_feeds[exchange_name]
        if begin_time is None and count is not None:
            # 如果没有开始时间，只传入 count，获取最近 count 条数据
            data = feed.get_kline(symbol, period, count, extra_data=extra_data)
            self.push_bar_data_to_queue(exchange_name, data)
            self.log(f"download completely: {symbol}, new {count} bar")
            return

        if begin_time is not None:
            # 如果未提供结束时间，则默认为当前时间并对齐到 period
            if stop_time is None:
                now = datetime.now(timezone.utc)  # 当前时间为 UTC
                period_seconds = int(period[:-1]) * 60 if "m" in period else int(period[:-1]) * 3600
                stop_time = now - timedelta(seconds=now.timestamp() % period_seconds)

            # 循环下载数据
            while begin_time < stop_time:
                try:
                    # 计算当前时间段的结束时间
                    time_delta = calculate_time_delta(period)
                    current_end_time = min(begin_time + time_delta, stop_time)

                    # 转换时间戳为毫秒
                    begin_stamp = int(1000.0 * begin_time.timestamp())
                    end_stamp = int(1000.0 * current_end_time.timestamp())

                    # 下载数据
                    data = feed.get_kline(
                        symbol, period, start_time=begin_stamp, end_time=end_stamp, extra_data=extra_data
                    )
                    self.push_bar_data_to_queue(exchange_name, data)
                    print(f"download successfully: {symbol}, period: {period}, "
                          f"begin: {begin_time}, end: {current_end_time}")

                    # 更新开始时间
                    begin_time = current_end_time

                    # 如果数据已经下载完成，跳出循环
                    if begin_time >= stop_time:
                        break
                except Exception as e:
                    print(f"download fail, retry: {e}")
                    time.sleep(3)  # 暂停 3 秒后重试
            print(f"download all data completely: {symbol}, period: {period}")

    def update_total_balance(self):
        for exchange_name in self.exchange_feeds:
            feed = self.exchange_feeds[exchange_name]
            balance_data = feed.get_balance()
            balance_data.init_data()
            account_list = balance_data.get_data()
            if exchange_name == "BINANCE___SWAP":
                self.update_binance_swap_balance_data(exchange_name, account_list)
            elif exchange_name == "BINANCE___SPOT":
                self.update_binance_spot_balance_data(exchange_name, account_list)
            elif exchange_name == "OKX___SWAP":
                self.update_okx_swap_balance_data(exchange_name, account_list)
            elif exchange_name == "OKX___SPOT":
                self.update_okx_spot_balance_data(exchange_name, account_list)


    def update_okx_swap_balance_data(self, exchange_name, account_list):
        value_result = {}
        cash_result = {}
        for account in account_list:
            account.init_data()
            for balance in account.get_balances():
                balance.init_data()
                currency = balance.get_symbol_name()
                cash_result[currency] = {}
                cash_result[currency]["cash"] = balance.get_available_margin()
                value_result[currency] = {}
                value_result[currency]["value"] = balance.get_margin() + balance.get_unrealized_profit()
        self._value_dict[exchange_name] = value_result
        self._cash_dict[exchange_name] = cash_result

    def update_okx_spot_balance_data(self, exchange_name, account_list):
        value_result = {}
        cash_result = {}
        for account in account_list:
            account.init_data()
            for balance in account.get_balances():
                balance.init_data()
                currency = balance.get_symbol_name()
                cash_result[currency] = {}
                cash_result[currency]["cash"] = balance.get_available_margin()
                value_result[currency] = {}
                value_result[currency]["value"] = balance.get_margin() + balance.get_unrealized_profit()
        self._value_dict[exchange_name] = value_result
        self._cash_dict[exchange_name] = cash_result


    def update_binance_swap_balance_data(self, exchange_name, account_list):
        value_result = {}
        cash_result = {}
        for account in account_list:
            account.init_data()
            currency = account.get_account_type()
            cash_result[currency] = {}
            cash_result[currency]["cash"] = account.get_available_margin()
            value_result[currency] = {}
            value_result[currency]["value"] = account.get_margin() + account.get_unrealized_profit()
        self._value_dict[exchange_name] = value_result
        self._cash_dict[exchange_name] = cash_result

    def update_binance_spot_balance_data(self, exchange_name, account_list):
        value_result = {}
        cash_result = {}
        for account in account_list:
            account.init_data()
            currency = account.get_account_type()
            cash_result[currency] = {}
            cash_result[currency]["cash"] = account.get_available_margin()
            value_result[currency] = {}
            value_result[currency]["value"] = account.get_margin() + account.get_unrealized_profit()
        self._value_dict[exchange_name] = value_result
        self._cash_dict[exchange_name] = cash_result


    def update_balance(self, exchange_name, currency=None):
        feed = self.exchange_feeds[exchange_name]
        balance_data = feed.get_balance()
        balance_data.init_data()
        account_list = balance_data.get_data()
        for account in account_list:
            account.init_data()
            if currency is not None:
                if account.get_account_type() == currency:
                    self._value_dict[exchange_name][currency]["cash"] = account.get_margin() + account.get_unrealized_profit()
                    self._cash_dict[exchange_name][currency]["value"] = account.get_available_margin()
            if currency is None:
                self._value_dict[exchange_name][account.get_account_type()]["cash"] = account.get_margin() + account.get_unrealized_profit()
                self._cash_dict[exchange_name][account.get_account_type()]["value"] = account.get_available_margin()


    def get_cash(self, exchange_name, currency):
        return self._value_dict[exchange_name][currency]["value"]

    def get_value(self, exchange_name, currency):
        return self._cash_dict[exchange_name][currency]["cash"]

    def get_total_cash(self):
        return self._cash_dict

    def get_total_value(self):
        return self._value_dict


