import json
import time

from bt_api_py.containers.accounts.binance_account import BinanceSwapWssAccountData
from bt_api_py.containers.orders.binance_order import BinanceSwapWssOrderData
from bt_api_py.containers.trades.binance_trade import BinanceSwapWssTradeData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp
from bt_api_py.logging_factory import get_logger


class BinanceAccountWssData(MyWebsocketApp, BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.wss_url = kwargs.get("wss_url")  # 必须传入特定的链接
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.exchange_name = kwargs.get("exchange_name", "BINANCE")
        self.symbol_name = kwargs.get("symbol_name")
        self.listen_key = kwargs.get("listen_key")
        self.proxies = kwargs.get("proxies")
        self.async_proxy = kwargs.get("async_proxy")
        self.logger = get_logger("binance_account_wss")
        self.wss_author()
        # ping = threading.Thread(target=self.ping)
        # ping.start()
        # print("初始化成功")

    def get_listen_key(self, max_retries=3):
        path = self._params.get_rest_path("get_listen_key")
        extra_data = {
            "asset_type": self.asset_type,
            "symbol_name": None,
            "request_type": "get_listen_key",
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        }
        last_err = None
        for attempt in range(max_retries):
            try:
                data = self.request(path, extra_data=extra_data, is_sign=False)
                result = data.get_data()
                if isinstance(result, dict) and "listenKey" in result:
                    return result
                self.logger.warn(
                    f"get_listen_key attempt {attempt + 1}/{max_retries} "
                    f"unexpected response: {result}"
                )
            except Exception as e:
                last_err = e
                self.logger.warn(f"get_listen_key attempt {attempt + 1}/{max_retries} error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
        raise RuntimeError(f"Failed to get listen key after {max_retries} attempts: {last_err}")

    def refresh_listen_key(self):
        params = {
            "listenKey": self.listen_key,
        }
        extra_data = {
            "asset_type": "get_listen_key",
            "symbol_name": None,
            "request_type": "get_listen_key",
        }
        path = self._params.get_rest_path("refresh_listen_key")
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data.get_data()

    def ping(self):
        while True:
            time.sleep(60)
            try:
                self.refresh_listen_key()
            except Exception as e:
                print(e)

    def wss_author(self):
        self.listen_key = self.get_listen_key()["listenKey"]
        self.wss_url = f"{self._params.wss_url}/{self.listen_key}"
        # print("wss_author", self.wss_url)

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected ====="
        )

    def _init(self):
        for topics in self.topics:
            if "orders" in topics["topic"]:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="orders", symbol=symbol)
            if "account" in topics["topic"]:
                symbol = topics.get("symbol", "BTC—USDT")
                currency = topics.get("currency", "USDT")
                self.subscribe(topic="account", symbol=symbol, currency=currency)
            if "positions" in topics["topic"]:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="positions", symbol=symbol)
            if "balance_position" in self.topics:
                self.subscribe(topic="balance_position")

    def handle_data(self, content):
        event = content.get("e", None)
        if event is not None:
            if event == "ACCOUNT_UPDATE":
                self.push_account(content)
            if event == "ORDER_TRADE_UPDATE":
                self.push_order(content)
            if event == "ORDER_TRADE_UPDATE" and content["o"].get("t") != 0:
                self.push_trade(content)
            # # 现货账户事件类型
            # if "executionReport" == event:
            #     self.push_order(content)
            # if "outboundAccountPosition" == event:
            #     self.push_account(content)
            # if "balanceUpdate" == event:
            #     self.push_balance(content)

    def push_account(self, content):
        # 推送account数据并添加到事件中
        # print("订阅到账户数据")
        symbol = "ALL"
        account_data = BinanceSwapWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_balances()[0].get_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        symbol = content["o"]["s"]
        order_data = BinanceSwapWssOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        symbol = content["o"]["s"]
        trade_data = BinanceSwapWssTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())

    def message_rsp(self, message):
        rsp = json.loads(message)
        # print("message received:", rsp)
        if "e" in rsp:
            self.handle_data(rsp)
        else:
            self.wss_logger.info(f"{self.logger_name}, error, {rsp}")
