# -*- coding: utf-8 -*-
"""
OKX Market WebSocket base class.
Handles public market data channels (tickers, orderbook, kline, funding rate, mark price).
"""
import hmac
import base64
import time
import json
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData
from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData
from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData
from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData
from bt_api_py.containers.accounts.okx_account import OkxAccountData
from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.containers.trades.okx_trade import OkxWssTradeData
from bt_api_py.containers.positions.okx_position import OkxPositionData


class OkxWssData(MyWebsocketApp):
    count = 0

    def __init__(self, data_queue, **kwargs):
        super(OkxWssData, self).__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.passphrase = kwargs.get("passphrase", None)
        self.wss_url = kwargs.get("wss_url", None)
        self.asset_type = kwargs.get("asset_type", "SWAP")

    def sign(self, content):
        """签名
        Args:
            content (TYPE): Description
        """
        sign = base64.b64encode(
            hmac.new(
                self.private_key.encode('utf-8'), content.encode('utf-8'), digestmod='sha256'
            ).digest()
        ).decode()

        return sign

    def author(self):
        timestamp = str(round(time.time()))
        sign_content = f"{timestamp}GET/users/self/verify"
        sign = self.sign(sign_content)
        auth = {
            'op': 'login',
            'args': [
                {"apiKey": self.public_key, "passphrase": self.passphrase, "timestamp": timestamp, "sign": sign}]
        }
        self.ws.send(json.dumps(auth))

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected =====")
        self.author()
        time.sleep(0.3)
        self._init()

    def _init(self):
        for topics in self.topics:
            self.count += 1
            if "orders" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='orders', symbol=symbol)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, orders")

            if "account" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                currency = topics.get("currency", "USDT")
                self.subscribe(topic='account', symbol=symbol, currency=currency)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, account")

            if "positions" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='positions', symbol=symbol)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, positions")

            if "balance_position" in self.topics:
                self.subscribe(topic='balance_position')
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, balance_position")

            if "ticker" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='tick', symbol=symbol)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, ticker")

            if "depth" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='depth', symbol=symbol, type='step0')
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, depth")

            if "books" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='books', symbol=symbol, type='step0')
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, orderbook")

            if 'bidAsk' in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='bidAsk', symbol=symbol, type='step0')
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, bidAsk")

            if 'funding_rate' in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='funding_rate', symbol=symbol)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, funding_rate")

            if 'mark_price' in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='mark_price', symbol=symbol)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, mark_price")

            if "kline" in topics['topic']:
                period = topics.get("period", "1m")
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='kline', symbol=symbol, period=period)
                print(f"subscribe {self.count} data, OKX, {self.asset_type}, {symbol}, kline")

    def handle_data(self, content):
        arg = content.get("arg", None)
        if arg is not None:
            if "tickers" in arg['channel']:
                self.push_ticker(content)
            if "books5" in arg['channel']:
                self.push_order_book(content)
            if "books" in arg['channel']:
                self.push_order_book(content)
            if 'candle' in arg['channel']:
                self.push_bar(content)
            if 'funding-rate' in arg['channel']:
                self.push_funding_rate(content)
            if "mark-price" in arg['channel']:
                self.push_mark_price(content)
            if "account" in arg["channel"]:
                self.push_account(content)
            if "order" in arg["channel"]:
                self.push_order(content)
            if "order" in arg["channel"] and content['data'][0].get("tradeId") != "":
                self.push_trade(content)
            if "positions" in arg["channel"]:
                self.push_position(content)

    def push_mark_price(self, content):
        mark_price_info = content['data'][0]
        symbol = content['arg']['instId']
        mark_price_data = OkxMarkPriceData(mark_price_info, symbol, self.asset_type, True)
        self.data_queue.put(mark_price_data)

    def push_funding_rate(self, content):
        funding_rate_info = content['data'][0]
        symbol = content['arg']['instId']
        funding_rate_data = OkxFundingRateData(funding_rate_info, symbol, self.asset_type, True)
        self.data_queue.put(funding_rate_data)

    def push_ticker(self, content):
        ticker_info = content['data'][0]
        symbol = content['arg']['instId']
        ticker_data = OkxTickerData(ticker_info, symbol, self.asset_type, True)
        self.data_queue.put(ticker_data)

    def push_order_book(self, content):
        order_book_info = content['data'][0]
        symbol = content['arg']['instId']
        order_book_data = OkxOrderBookData(order_book_info, symbol, self.asset_type, True)
        self.data_queue.put(order_book_data)

    def push_bar(self, content):
        bar_info = content['data'][0]
        symbol = content['arg']['instId']
        bar_data = OkxBarData(bar_info, symbol, self.asset_type, True)
        self.data_queue.put(bar_data)

    def push_account(self, content):
        account_info = content['data'][0]
        symbol = "ANY"
        account_data = OkxAccountData(account_info, symbol, self.asset_type, True)
        self.data_queue.put(account_data)

    def push_order(self, content):
        print("订阅到order数据")
        order_info = content['data'][0]
        symbol = content['arg']['instId']
        order_data = OkxOrderData(order_info, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        trade_info = content['data'][0]
        symbol = content['arg']['instId']
        trade_data = OkxWssTradeData(trade_info, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)

    def push_position(self, content):
        data = content['data']
        if len(data) > 0:
            position_info = data[0]
            symbol = content['arg']['instId']
            position_data = OkxPositionData(position_info, symbol, self.asset_type, True)
            self.data_queue.put(position_data)

    def message_rsp(self, message):
        rsp = json.loads(message)
        if 'event' in rsp:
            if rsp['event'] == 'login':
                if rsp['code'] == "0":
                    self.wss_logger.info(f"===== {self._params.exchange_name} Data Websocket Connected =====")
                else:
                    self.ws.restart()
            elif rsp['event'] == 'subscribe':
                self.wss_logger.info(f"===== Data Websocket {rsp} =====")
                pass
        elif 'arg' in rsp:
            self.handle_data(rsp)
            return
