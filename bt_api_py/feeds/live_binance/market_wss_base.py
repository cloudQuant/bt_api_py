import json
import time

from bt_api_py.containers.accounts.binance_account import BinanceSwapWssAccountData
from bt_api_py.containers.bars.binance_bar import BinanceWssBarData
from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceWssFundingRateData
from bt_api_py.containers.markprices.binance_mark_price import BinanceWssMarkPriceData
from bt_api_py.containers.orderbooks.binance_orderbook import BinanceWssOrderBookData
from bt_api_py.containers.orders.binance_order import BinanceForceOrderData, BinanceSwapWssOrderData
from bt_api_py.containers.positions.binance_position import BinanceWssPositionData
from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData
from bt_api_py.containers.trades.binance_trade import BinanceAggTradeData, BinanceSwapWssTradeData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp
from bt_api_py.logging_factory import get_logger


class BinanceMarketWssData(MyWebsocketApp, BinanceRequestData):
    count = 0

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        # Explicitly init BinanceRequestData/Feed chain to create _http_client
        # (MyWebsocketApp.__init__ does not call super().__init__, so MRO chain stops)
        BinanceRequestData.__init__(self, data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.wss_url = kwargs.get("wss_url")  # 必须传入特定的链接
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.listen_key = kwargs.get("listen_key")
        self.proxies = kwargs.get("proxies")
        self.async_proxy = kwargs.get("async_proxy")
        self.exchange_name = kwargs.get("exchange_name", "BINANCE")
        self.logger = get_logger("binance_market_wss")
        # ping = threading.Thread(target=self.ping)
        # ping.start()
        # print("初始化成功")

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected ====="
        )
        self._init()
        time.sleep(0.1)

    def _init(self):
        for topics in self.topics:
            # time.sleep(0.2)
            self.count += 1
            if topics["topic"] == "ticker":
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="tick", symbol=symbol)
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, ticker")

            if topics["topic"] == "depth":
                # print(topics)
                if "symbol" in topics:
                    symbol = topics.get("symbol")
                    self.subscribe(topic="depth", symbol=symbol, type="step0")
                    print(
                        f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, depth"
                    )
                elif "symbol_list" in topics:
                    symbol_list = topics.get("symbol_list")
                    self.subscribe(topic="depth", symbol_list=symbol_list, type="step0")
                    print(
                        f"subscribe {self.count} data, BINANCE, {self.asset_type}, depth, symbol_list"
                    )
                else:
                    print("depth need symbol to subscribe")

            if topics["topic"] == "funding_rate":
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="funding_rate", symbol=symbol)
                print(
                    f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, funding_rate"
                )

            if topics["topic"] == "mark_price":
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="mark_price", symbol=symbol)
                print(
                    f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, mark_price"
                )

            if topics["topic"] == "kline":
                period = topics.get("period", "1m")
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="kline", symbol=symbol, period=period)
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, kline")

            if topics["topic"] == "all_mark_price":
                self.subscribe(topic="all_mark_price")
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, all_mark_price")

            if topics["topic"] == "all_ticker":
                self.subscribe(topic="all_ticker")
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type},all_ticker")

            if topics["topic"] == "all_force_order":
                self.subscribe(topic="all_force_order")
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, all_force_order")

            if topics["topic"] == "agg_trade":
                if "symbol" in topics:
                    symbol = topics.get("symbol")
                    self.subscribe(topic="agg_trade", symbol=symbol)
                    print(
                        f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, agg_trade"
                    )
                if "symbol_list" in topics:
                    symbol_list = topics.get("symbol_list")
                    self.subscribe(topic="agg_trade", symbol_list=symbol_list)
                    print(
                        f"subscribe {self.count} data, BINANCE, {self.asset_type}, agg_trade, symbol_list"
                    )

            if topics["topic"] == "force_order":
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="force_order", symbol=symbol)
                print(
                    f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, force_order"
                )

            if topics["topic"] == "liquidation":
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="liquidation", symbol=symbol)
                print(
                    f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, liquidation"
                )

            if topics["topic"] == "mini_ticker":
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic="mini_ticker", symbol=symbol)
                print(
                    f"subscribe {self.count} data, BINANCE, {self.asset_type}, {symbol}, mini_ticker"
                )

            if topics["topic"] == "all_mini_ticker":
                self.subscribe(topic="all_mini_ticker")
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, all_mini_ticker")

            if topics["topic"] == "all_book_ticker":
                self.subscribe(topic="all_book_ticker")
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, all_book_ticker")

            if topics["topic"] == "continuous_kline":
                period = topics.get("period", "1m")
                pair = topics.get("pair", topics.get("symbol", "BTC—USDT"))
                self.subscribe(topic="continuous_kline", pair=pair, period=period)
                print(
                    f"subscribe {self.count} data, BINANCE, {self.asset_type}, {pair}, continuous_kline"
                )

            if topics["topic"] == "contract_info":
                self.subscribe(topic="contract_info")
                print(f"subscribe {self.count} data, BINANCE, {self.asset_type}, contract_info")

    # def handle_all_data(self, content):
    #     if isinstance(content, list):
    #         for c in content:
    #             self.handle_all_data(c)
    #     elif isinstance(content, dict):
    #         self.handle_data(content)

    def handle_data(self, content):
        # print(content)
        event = content.get("e", None)
        if event is not None:
            if event == "bookTicker":
                self.push_ticker(content)
            if event == "depthUpdate":
                self.push_order_book(content)
            if event == "kline":
                self.push_bar(content)
                # print(content)
            if event == "markPriceUpdate":
                self.push_funding_rate(content)
            if event == "markPriceUpdate":
                self.push_mark_price(content)
            if event == "ACCOUNT_UPDATE":
                self.push_account(content)
            if event == "aggTrade":
                self.push_agg_trade(content)
            if event == "forceOrder":
                self.push_force_order(content)
            if event == "24hrTicker":
                self.push_ticker(content)
            if event == "24hrMiniTicker":
                self.push_ticker(content)
            if event == "continuous_kline":
                self.push_continuous_kline(content)
            # if "bookTicker" == event:
            #     self.push_order(content)
            # if "bookTicker" == event and content['data'][0].get("tradeId") != "":
            #     self.push_trade(content)
            # # if "trade" in arg["channel"]:
            # #     self.push_trade(content)
            # if "bookTicker" == event:
            #     self.push_position(content)

    def push_continuous_kline(self, content):
        symbol = content.get("ps", content.get("s", "UNKNOWN"))
        # Add 's' key for compatibility with BinanceWssBarData
        if "s" not in content:
            content["s"] = symbol
        bar_data = BinanceWssBarData(content, symbol, self.asset_type, True)
        bar_data.init_data()
        self.data_queue.put(bar_data)

    def push_force_order(self, content):
        # print("接收到force_order: ", content)
        symbol = content["o"]["s"]
        force_order_data = BinanceForceOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(force_order_data)

    def push_agg_trade(self, content):
        symbol = content["s"]
        agg_trade_data = BinanceAggTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(agg_trade_data)

    def push_mark_price(self, content):
        symbol = content["s"]
        mark_price_data = BinanceWssMarkPriceData(content, symbol, self.asset_type, True)
        self.data_queue.put(mark_price_data)
        # print("获取mark_price成功, mark_price is ", mark_price_data.get_mark_price())

    def push_funding_rate(self, content):
        # 资金费率推送
        symbol = content["s"]
        funding_rate_data = BinanceWssFundingRateData(content, symbol, self.asset_type, True)
        self.data_queue.put(funding_rate_data)
        # print("获取funding_rate成功，当前funding_rate = ", funding_rate_data.get_current_funding_rate())

    def push_ticker(self, content):
        # 推送ticker数据到添加事件中
        symbol = content["s"]
        ticker_data = BinanceWssTickerData(content, symbol, self.asset_type, True)
        self.data_queue.put(ticker_data)
        # print("获取ticker数据成功，ticker ask_price = ", ticker_data.get_ask_price())

    def push_order_book(self, content):
        # 推送order_book数据并添加到事件中
        symbol = content["s"]
        order_book_data = BinanceWssOrderBookData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_book_data)
        # print("获取orderbook成功, 当前价格为：", order_book_data.get_ask_price_list())

    def push_bar(self, content):
        # 推送bar数据并添加到事件中
        symbol = content["s"]
        bar_data = BinanceWssBarData(content, symbol, self.asset_type, True)
        bar_data.init_data()
        bar_data.get_bar_status()
        # if bar_status:
        #     self.data_queue.put(bar_data)
        self.data_queue.put(bar_data)
        # all_data = bar_data.get_all_data()
        # timestamp = all_data["open_time"]
        # # dtime_utc = datetime.fromtimestamp(timestamp // 1000, tz=UTC)
        # # 将时间戳转换为 UTC 时间（确保它是 UTC 时间）
        # dtime_utc = datetime.fromtimestamp(timestamp // 1000, tz=pytz.UTC)
        # if bar_status:
        #     print(f"获取binance {dtime_utc} kline成功,"
        #           f"close_price = {bar_data.get_close_price()},"
        #           f"bar_status = {bar_status}")

    def push_account(self, content):
        # 推送account数据并添加到事件中
        account_info = content["data"][0]
        symbol = "ANY"
        account_data = BinanceSwapWssAccountData(account_info, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_total_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        order_info = content["data"][0]
        symbol = content["arg"]["symbol"]
        order_data = BinanceSwapWssOrderData(order_info, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        trade_info = content["data"][0]
        symbol = content["arg"]["symbol"]
        trade_data = BinanceSwapWssTradeData(trade_info, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())

    def push_position(self, content):
        data = content["data"]
        if len(data) > 0:
            position_info = data[0]
            symbol = content["arg"]["symbol"]
            position_data = BinanceWssPositionData(position_info, symbol, self.asset_type, True)
            self.data_queue.put(position_data)
            # print("获取position数据成功，当前账户持仓为：", position_data.get_position_symbol_name(),
            #       position_data.get_position_qty())

    def message_rsp(self, message):
        rsp = json.loads(message)
        if isinstance(rsp, dict):
            if "result" in rsp:
                if rsp["id"] == 1:
                    self.wss_logger.info(
                        f"===== {self._params.exchange_name} Data Websocket Connected ====="
                    )
                else:
                    print("restart操作")
                    self.ws.restart()
            elif "e" in rsp:
                self.handle_data(rsp)
                return
        elif isinstance(rsp, list):
            for data in rsp:
                self.handle_data(data)
