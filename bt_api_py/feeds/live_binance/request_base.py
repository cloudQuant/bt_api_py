# -*- coding: utf-8 -*-
import hmac
# import urllib
import time
import json
from datetime import datetime
import pytz
# import threading
# from urllib import parse
from urllib.parse import urlencode
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.calculate_time import datetime2timestamp
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.bars.binance_bar import (BinanceWssBarData,
                                                   BinanceRequestBarData)
from bt_api_py.containers.markprices.binance_mark_price import (BinanceWssMarkPriceData,
                                                                BinanceRequestMarkPriceData)
from bt_api_py.containers.tickers.binance_ticker import (BinanceWssTickerData,
                                                         BinanceRequestTickerData)
from bt_api_py.containers.orderbooks.binance_orderbook import (BinanceWssOrderBookData,
                                                               BinanceRequestOrderBookData)
from bt_api_py.containers.fundingrates.binance_funding_rate import (BinanceWssFundingRateData,
                                                                    BinanceRequestFundingRateData,
                                                                    BinanceRequestHistoryFundingRateData)
from bt_api_py.containers.accounts.binance_account import (BinanceSwapWssAccountData,
                                                           BinanceSpotWssAccountData,
                                                           BinanceSwapRequestAccountData,
                                                           BinanceSpotRequestAccountData)
from bt_api_py.containers.orders.binance_order import (BinanceSwapWssOrderData,
                                                       BinanceSpotWssOrderData,
                                                       BinanceRequestOrderData,
                                                       BinanceForceOrderData)
from bt_api_py.containers.trades.binance_trade import (BinanceSwapWssTradeData,
                                                       BinanceSpotWssTradeData,
                                                       BinanceRequestTradeData,
                                                       BinanceAggTradeData)
from bt_api_py.containers.positions.binance_position import (BinanceWssPositionData,
                                                             BinanceRequestPositionData)
from bt_api_py.containers.exchanges.binance_exchange_data import (BinanceExchangeDataSwap,
                                                                  BinanceExchangeDataSpot)
from bt_api_py.containers.balances.binance_balance import BinanceSwapRequestBalanceData  # , BinanceSpotRequestBalanceData


# session = requests.Session()
# session.keep_alive = False
# adapter = requests.adapters.HTTPAdapter(
#     max_retries=5, # 最大重试次数
#     pool_connections=100, # 连接池大小
#     pool_maxsize=100, # 连接池最大空闲连接数
#     pool_block=True, # 连接池是否阻塞)
# session.mount('http://', adapter)
# session.mount('https://', adapter)

class BinanceRequestData(Feed):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestData, self).__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "binance_swap_feed.log")
        self._params = kwargs.get("exchange_data", BinanceExchangeDataSwap())
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()
        # self.start_loop()  # 在开始订阅数据的时候启动

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "队列未初始化"

    # noinspection PyMethodMayBeStatic
    # def signature(self, timestamp, method, request_path, secret_key, body=None):
    #     if body is None:
    #         body = ''
    #     else:
    #         body = str(body)
    #     message = str(timestamp) + str.upper(method) + request_path + body
    #     mac = hmac.new(bytes(secret_key, encoding='utf8'), bytes(message, encoding='utf-8'), digestmod='sha256')
    #     d = mac.digest()
    #     return base64.b64encode(d).decode()

    def sign(self, content):
        """签名

        Args:
            content (TYPE): Description
        """
        sign = hmac.new(
            self.private_key.encode('utf-8'), content.encode('utf-8'), digestmod='sha256'
        ).hexdigest()

        return sign

    # set request header
    # noinspection PyMethodMayBeStatic
    def request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=True):
        """http request function
        Args:
            path (TYPE): request url
            params (dict, optional): in url
            body (dict, optional): in request body
            extra_data(dict,None): extra_data, generate by user
            timeout (int, optional): request timeout(s)
            is_sign (bool, optional): is need signature
        """
        if params is None:
            params = {}
        # if body is None:
        #     body = {}
        method, path = path.split(' ', 1)
        if is_sign is False:
            req = params
        else:
            req = {
                'recvWindow': 60000,
                'timestamp': int(time.time() * 1000),
            }
            req.update(params)
            sign = urlencode(req)
            req['signature'] = self.sign(sign)
            # req['signature'] = self.sign(str(req))
        req = urlencode(req)
        url = f"{self._params.rest_url}{path}?{req}"
        headers = {
            "X-MBX-APIKEY": self.public_key
        }
        request_type = extra_data.get('request_type')
        # print("url ", url)
        # print("headers ", headers)
        # print("method ", method)
        # print("body ", body)
        # print("request_type", request_type)
        # print(f"self.public_key:{self.public_key}")
        # print(f"self.private_key:{self.private_key}")
        res = self.http_request(method, url, headers, body, timeout)
        # print("res", res)
        # data_factory = self._params.request_data_dict.get(request_type)
        return RequestData(res, extra_data)

    def _get_account(self, symbol=None, extra_data=None, **kwargs):
        """
        get account info using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        request_type = 'get_account'
        path = self._params.get_rest_path(request_type)
        params = {
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_account_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if len(input_data) > 0:
            if asset_type == "SPOT":
                data_list = [BinanceSpotRequestAccountData(input_data,
                                                           symbol_name,
                                                           asset_type,
                                                           True)]
            else:
                data_list = [BinanceSwapRequestAccountData(input_data,
                                                           symbol_name,
                                                           asset_type,
                                                           True)]
            data = data_list
        else:
            data = []
        return data, status

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """
        get balance info using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        request_type = 'get_balance'
        # request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path(request_type)
        params = {
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_balance_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        print("symbol_name", symbol_name, "asset_type", asset_type)
        if isinstance(input_data, list) and asset_type == "SWAP":
            data = [BinanceSwapRequestBalanceData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict) and asset_type == "SWAP":
            data = [BinanceSwapRequestBalanceData(input_data, symbol_name, asset_type, True)
                    ]
        elif isinstance(input_data, list) and asset_type == "SPOT":
            data = [BinanceSpotRequestAccountData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict) and asset_type == "SPOT":
            data = [BinanceSpotRequestAccountData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_position(self, symbol, extra_data=None, **kwargs):
        """
        get position info from okx by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_position"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_position_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_position_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list) and isinstance(input_data[0], dict):
            data = [BinanceRequestPositionData(i, symbol_name, asset_type, True)
                    for i in input_data]
        else:
            data = []
        return data, status

    def get_position(self, symbol, extra_data=None, **kwargs):
        """
        get position info from okx by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """
        get tick price by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params = {
                'symbol': request_symbol,
            }
        else:
            params = {}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_tick_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestTickerData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestTickerData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        """
        get depth data from okx using requests package
        :param symbol: instrument name
        :param size: the size of the orderbook level
        :param args:  pass a variable number of arguments to a function.
        :param extra_data: extra_data, generated by user and extended by function
        :param kwargs: pass key-worded, variable-length arguments.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_depth"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            "limit": size
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_depth_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderBookData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderBookData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_kline(self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        """
        get kline from okx using request.
        :param symbol: instrument name.
        :param period: kline interval.
        :param count: kline number, default is 100.
        :param start_time: start_time
        :param end_time: end_time
        :param extra_data: extra_data, generated by user and function
        :param kwargs: pass a key-worded, variable-length argument list.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_kline"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'interval': self._params.get_period(period),
            'limit': count,
        }
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
                params.update({"startTime": start_time})
            elif isinstance(start_time, int):
                params.update({"startTime": start_time})
            else:
                self.logger.error("start_time is not str or int")
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
                params.update({"endTime": end_time})
            elif isinstance(end_time, int):
                params.update({"endTime": end_time})
            else:
                self.logger.error("end_time is not str or int")
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_kline_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestBarData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestBarData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_kline(self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_funding_rate(self, symbol, extra_data=None, **kwargs):
        """
        get funding rate from okx
        :param symbol: symbol name, eg: BTC-USDT.
        :param args:  pass a variable number of arguments to a function.
        :param extra_data: extra_data, generated by user and function
        :param kwargs: pass a key-worded, variable-length argument list.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_funding_rate"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_funding_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_funding_rate_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        # print('input_data', input_data)
        if isinstance(input_data, list):
            data = [BinanceRequestFundingRateData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestFundingRateData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        print("funding rate data = ", data)
        return data

    def _get_history_funding_rate(self, symbol, start_time, end_time, count=1000, extra_data=None, **kwargs):
        """
        get funding rate from binance
        :param symbol: symbol name, eg: BTC-USDT.
        :param start_time: start time
        :param end_time: end time
        :param count: count
        :param args:  passes a variable number of arguments to a function.
        :param extra_data: extra_data, generated by user and function
        :param kwargs: pass a key-worded, variable-length argument list.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_history_funding_rate"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'limit': count,
        }
        if isinstance(start_time, str):
            start_time = int(datetime2timestamp(start_time)*1000)
            # print("start_time = ", start_time)
            params.update({"startTime": start_time})
        elif isinstance(start_time, int):
            params.update({"startTime": start_time})
        else:
            pass
        if isinstance(end_time, str):
            end_time = int(datetime2timestamp(end_time)*1000)
            # print("end_time = ", end_time)
            params.update({"endTime": end_time})
        elif isinstance(start_time, int):
            params.update({"endTime": end_time})
        else:
            pass

        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_history_funding_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_history_funding_rate_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        # print('input_data', input_data)
        if isinstance(input_data, list):
            data = [BinanceRequestHistoryFundingRateData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestHistoryFundingRateData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_history_funding_rate(self, symbol, start_time=None, end_time=None, count=1000, extra_data=None, **kwargs):
        path, params, extra_data = self._get_history_funding_rate(symbol, start_time, end_time, count, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_mark_price(self, symbol, extra_data=None, **kwargs):
        """
        get mark_price from okx
        :param symbol: symbol name, eg: BTC-USDT.
        :param extra_data: extra_data, generated by user and function
        :param args:  pass a variable number of arguments to a function.
        :param kwargs: pass a key-worded, variable-length argument list.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_mark_price"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": "SPOT",
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_mark_price_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_mark_price_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestMarkPriceData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestMarkPriceData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def get_mark_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def set_mode(self):
        params = {
            "posMode": "long_short_mode"
        }
        path = self._params.get_rest_path("set_mode")
        data = self.request(path, body=params)
        return data

    def get_config(self, extra_data=None):
        params = {}
        path = self._params.get_rest_path("get_config")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_config",
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def set_lever(self, symbol):
        symbol = self._params.get_symbol(symbol)
        params = {
            "symbol": symbol,
            "lever": 10,
            "mgnMode": "cross"
        }
        path = self._params.get_rest_path("set_lever")
        data = self.request(path, body=params)
        return data

    def _make_order(self, symbol, vol, price=None, order_type='buy-limit',
                    offset='open', post_only=False, client_order_id=None,
                    extra_data=None, **kwargs):
        # todo 双向账户下下单
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, order_type = order_type.split('-')
        side = side.upper()
        time_in_force = kwargs.get('time_in_force', "GTC")
        params = {
            'symbol': request_symbol,
            'side': side,
            'quantity': vol,
            'price': price,
            'type': order_type.upper(),
            'timeInForce': time_in_force,
        }
        print(f"offset = {offset}")
        if self.asset_type == "SWAP":
            params['reduceOnly'] = 'false' if offset == 'open' else 'true' if offset == 'close' else None
        if client_order_id is not None:
            params['newClientOrderId'] = client_order_id
        if order_type == 'market':
            params.pop("timeInForce", None)
            params.pop("price", None)
        if "position_side" in kwargs:
            params["positionSide"] = kwargs["position_side"]
            params.pop("reduceOnly", None)
        elif self.asset_type == "SWAP":
            params.pop("reduceOnly", None)
            if side=="BUY":
                params["positionSide"] = "LONG"
            elif side=="SELL":
                params["positionSide"] = "SHORT"
        else:
            params.pop("reduceOnly", None)


        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "post_only": post_only,
            "normalize_function": BinanceRequestData._make_order_normalize_function,
        })
        # if kwargs is not None:
        #     extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def make_order(self, symbol, vol, price=None, order_type='buy-limit',
                   offset='open', post_only=False, client_order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                    post_only, client_order_id, extra_data,
                                                    **kwargs)
        # print("params = ", params)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        """
        cancel order by order_id using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param order_id: order_id, default is None, can be a string passed by user
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        :return:
        """
        request_symbol = self._params.get_symbol(symbol)
        # request_symbol = symbol
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        # update params
        params = {
            'symbol': request_symbol,
        }
        if order_id:
            params['orderId'] = order_id
        if "client_order_id" in kwargs:
            params["origClientOrderId"] = kwargs["client_order_id"]
        # update params
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": request_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._cancel_order_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    def _get_server_time(self, extra_data=None, **kwargs):
        request_symbol = "ALL"
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        if extra_data is None:
            extra_data = kwargs
        else:
            extra_data.update(kwargs)
        params = {}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": request_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def async_get_server_time(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        self.submit(self.async_request(path, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # noinspection PyBroadException
    def _query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        # path = path.replace("<instrument_id>", symbol)
        # path = path.replace("<order_id>", str(order_id))
        # update params
        params = {
            "symbol": request_symbol,
        }
        if order_id is not None:
            params["orderId"] = order_id
        if "client_order_id" in kwargs:
            params['origClientOrderId'] = kwargs['client_order_id']
        # update extra_data
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._query_order_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """
        get open orders by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params = {"symbol": request_symbol}
        else:
            request_symbol = ''
            params = {}
        request_type = "get_open_orders"
        if 'recv_window' in kwargs:
            params["recvWindow"] = kwargs["recv_window"]
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": request_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_open_orders_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestOrderData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestOrderData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_deals(self, symbol=None, count=100, start_time="", end_time="",
                   extra_data=None, **kwargs):
        """
        get history trade records from okx
        :param symbol: 交易对, btc/usdt
        :param count: 分页数量, 默认100, 最大100
        :param start_time: 筛选开始时间戳, 毫秒
        :param end_time: 筛选结束时间戳, 毫秒
        :param extra_data: 策略请求数据的时候添加的额外数据
        :return:
        """
        # params = {'instType': instType, 'uly': uly, 'symbol': symbol,
        #           'ordId': ordId, 'after': after, 'before': before,
        #           'limit': limit, 'instFamily': instFamily}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params = {"symbol": request_symbol}
        else:
            request_symbol = ""
            params = {}
        request_type = "get_deals"
        if count:
            params["limit"] = count
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        if "from_id" in kwargs:
            params["fromId"] = kwargs["from_id"]
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": request_symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_deals_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        status = True if input_data is not None else False
        symbol_name = extra_data["symbol_name"]
        asset_type = extra_data["asset_type"]
        if isinstance(input_data, list):
            data = [BinanceRequestTradeData(i, symbol_name, asset_type, True)
                    for i in input_data]
        elif isinstance(input_data, dict):
            data = [BinanceRequestTradeData(input_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def get_deals(self, symbol=None, count=100,
                  start_time="", end_time="",
                  extra_data=None,
                  **kwargs):
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data,
                                                   **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def get_clear_price(self, symbol, extra_data=None, **kwargs):
        pass

    # ===== New market data methods =====

    def _get_agg_trades(self, symbol, from_id=None, start_time=None, end_time=None,
                        count=500, extra_data=None, **kwargs):
        request_type = "get_agg_trades"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'limit': count,
        }
        if from_id is not None:
            params['fromId'] = from_id
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_agg_trades(self, symbol, from_id=None, start_time=None, end_time=None,
                       count=500, extra_data=None, **kwargs):
        path, params, extra_data = self._get_agg_trades(
            symbol, from_id, start_time, end_time, count, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_open_interest(self, symbol, extra_data=None, **kwargs):
        request_type = "get_open_interest"
        request_symbol = self._params.get_symbol(symbol)
        params = {'symbol': request_symbol}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_open_interest(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_interest(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_continuous_kline(self, pair, period, contract_type="PERPETUAL",
                              count=100, start_time=None, end_time=None,
                              extra_data=None, **kwargs):
        request_type = "get_continuous_kline"
        params = {
            'pair': self._params.get_symbol(pair),
            'contractType': contract_type,
            'interval': self._params.get_period(period),
            'limit': count,
        }
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": pair,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._get_kline_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_continuous_kline(self, pair, period, contract_type="PERPETUAL",
                             count=100, start_time=None, end_time=None,
                             extra_data=None, **kwargs):
        path, params, extra_data = self._get_continuous_kline(
            pair, period, contract_type, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_index_price_kline(self, pair, period, count=100, start_time=None,
                               end_time=None, extra_data=None, **kwargs):
        request_type = "get_index_price_kline"
        params = {
            'pair': self._params.get_symbol(pair),
            'interval': self._params.get_period(period),
            'limit': count,
        }
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": pair,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_index_price_kline(self, pair, period, count=100, start_time=None,
                              end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_index_price_kline(
            pair, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_mark_price_kline(self, symbol, period, count=100, start_time=None,
                              end_time=None, extra_data=None, **kwargs):
        request_type = "get_mark_price_kline"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'interval': self._params.get_period(period),
            'limit': count,
        }
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_mark_price_kline(self, symbol, period, count=100, start_time=None,
                             end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price_kline(
            symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_funding_info(self, extra_data=None, **kwargs):
        request_type = "get_funding_info"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_funding_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_info(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_long_short_ratio(self, symbol, period="5m", count=30, start_time=None,
                              end_time=None, extra_data=None, **kwargs):
        request_type = "get_long_short_ratio"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'period': period,
            'limit': count,
        }
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_long_short_ratio(self, symbol, period="5m", count=30, start_time=None,
                             end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_long_short_ratio(
            symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_taker_buy_sell_volume(self, symbol, period="5m", count=30, start_time=None,
                                   end_time=None, extra_data=None, **kwargs):
        request_type = "get_taker_buy_sell_volume"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'period': period,
            'limit': count,
        }
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_taker_buy_sell_volume(self, symbol, period="5m", count=30, start_time=None,
                                  end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_taker_buy_sell_volume(
            symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    # ===== New trade methods =====

    def _get_all_orders(self, symbol, order_id=None, start_time=None, end_time=None,
                        count=500, extra_data=None, **kwargs):
        request_type = "get_all_orders"
        request_symbol = self._params.get_symbol(symbol)
        params = {'symbol': request_symbol, 'limit': count}
        if order_id is not None:
            params['orderId'] = order_id
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._query_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_all_orders(self, symbol, order_id=None, start_time=None, end_time=None,
                       count=500, extra_data=None, **kwargs):
        path, params, extra_data = self._get_all_orders(
            symbol, order_id, start_time, end_time, count, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _modify_order(self, symbol, order_id=None, orig_client_order_id=None,
                      side=None, quantity=None, price=None,
                      extra_data=None, **kwargs):
        request_type = "modify_order"
        request_symbol = self._params.get_symbol(symbol)
        params = {'symbol': request_symbol}
        if order_id is not None:
            params['orderId'] = order_id
        if orig_client_order_id is not None:
            params['origClientOrderId'] = orig_client_order_id
        if side is not None:
            params['side'] = side.upper()
        if quantity is not None:
            params['quantity'] = quantity
        if price is not None:
            params['price'] = price
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._make_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def modify_order(self, symbol, order_id=None, orig_client_order_id=None,
                     side=None, quantity=None, price=None,
                     extra_data=None, **kwargs):
        path, params, extra_data = self._modify_order(
            symbol, order_id, orig_client_order_id, side, quantity, price,
            extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _cancel_orders(self, symbol, order_id_list=None, client_order_id_list=None,
                       extra_data=None, **kwargs):
        request_type = "cancel_orders"
        request_symbol = self._params.get_symbol(symbol)
        params = {'symbol': request_symbol}
        if order_id_list is not None:
            params['orderIdList'] = str(order_id_list)
        if client_order_id_list is not None:
            params['origClientOrderIdList'] = str(client_order_id_list)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": BinanceRequestData._cancel_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_orders(self, symbol, order_id_list=None, client_order_id_list=None,
                      extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_orders(
            symbol, order_id_list, client_order_id_list, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _cancel_all_orders(self, symbol, extra_data=None, **kwargs):
        request_type = "cancel_all"
        request_symbol = self._params.get_symbol(symbol)
        params = {'symbol': request_symbol}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_all_orders(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_all_orders(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ===== New account query methods =====

    def _get_leverage_bracket(self, symbol=None, extra_data=None, **kwargs):
        request_type = "get_leverage_bracket"
        params = {}
        if symbol is not None:
            params['symbol'] = self._params.get_symbol(symbol)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_leverage_bracket(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_leverage_bracket(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_position_mode(self, extra_data=None, **kwargs):
        request_type = "get_position_mode"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_position_mode(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_position_mode(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_income(self, symbol=None, income_type=None, start_time=None, end_time=None,
                    count=100, extra_data=None, **kwargs):
        request_type = "get_income"
        params = {'limit': count}
        if symbol is not None:
            params['symbol'] = self._params.get_symbol(symbol)
        if income_type is not None:
            params['incomeType'] = income_type
        if start_time is not None:
            if isinstance(start_time, str):
                start_time = int(datetime2timestamp(start_time) * 1000)
            params['startTime'] = start_time
        if end_time is not None:
            if isinstance(end_time, str):
                end_time = int(datetime2timestamp(end_time) * 1000)
            params['endTime'] = end_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_income(self, symbol=None, income_type=None, start_time=None, end_time=None,
                   count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_income(
            symbol, income_type, start_time, end_time, count, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _change_leverage(self, symbol, leverage, extra_data=None, **kwargs):
        request_type = "change_leverage"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'leverage': leverage,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def change_leverage(self, symbol, leverage, extra_data=None, **kwargs):
        path, params, extra_data = self._change_leverage(symbol, leverage, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _change_margin_type(self, symbol, margin_type, extra_data=None, **kwargs):
        request_type = "change_margin_type"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'symbol': request_symbol,
            'marginType': margin_type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def change_margin_type(self, symbol, margin_type, extra_data=None, **kwargs):
        path, params, extra_data = self._change_margin_type(
            symbol, margin_type, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_fee(self, symbol, extra_data=None, **kwargs):
        request_type = "get_fee"
        request_symbol = self._params.get_symbol(symbol)
        params = {'symbol': request_symbol}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_fee(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_fee(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False):
        """http request function
        Args:
            path (TYPE): request url
            params (dict, optional): in url
            body (dict, optional): in request body
            timeout (int, optional): request timeout(s)
            extra_data(dict,None): extra_data, generate by user
            is_sign (bool, optional): whether to signature
        """
        if params is None:
            params = {}
        # if body is None:
        #     body = {}
        method, path = path.split(' ', 1)
        if is_sign is False:
            req = params
        else:
            req = {
                'recvWindow': 3000,
                'timestamp': int(time.time() * 1000),
            }
            req.update(params)
            sign = urlencode(req)
            req['signature'] = self.sign(sign)
        req = urlencode(req)
        url = f"{self._params.rest_url}{path}?{req}"
        headers = {
            'X-MBX-APIKEY': self.public_key,
        }
        res = await self.async_http_request(method, url, headers, body, timeout)
        # self.request_logger.info(f"""request:{get_string_tz_time()} {res}""")
        # request_type = extra_data.get('request_type')
        # data_factory = self._params.request_data_dict.get(request_type)
        return RequestData(res, extra_data)

    # noinspection PyBroadException
    def async_get_account(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_get_balance(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_sub_account(self, extra_data=None):
        path = self._params.get_rest_path("sub_account")
        params = {
            "subAcct": "xxx"
        }
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_position(self, symbol, extra_data=None, **kwargs):
        """
        get position info from okx by symbol using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_callback(self, future):
        """
        callback function for async_get_tick, push tickerData to data_queue
        :param future: asyncio future object
        :return: None
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            import traceback
            self.async_logger.warn(f"async_callback::{e}\n{traceback.format_exc()}")

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    # noinspection PyMethodMayBeStatic
    def async_get_kline(self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_mark_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_config(self, extra_data=None):
        params = {
            # "posMode":"long_short_mode"
        }
        data_type = "get_config"
        path = self._params.get_rest_path(data_type)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)
        # data = self.request(path, body=params)

    def async_set_lever(self, symbol, extra_data=None):
        symbol = self._params.get_symbol(symbol)
        params = {
            "symbol": symbol,
            "lever": 10,
            "mgnMode": "cross"
        }
        data_type = "set_lever"
        path = self._params.get_rest_path(data_type)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # noinspection PyBroadException
    def async_make_order(self, symbol, vol, price=None, order_type='buy-limit',
                         offset='open', post_only=False, client_order_id=None,
                         extra_data=None, **kwargs):
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                    post_only, client_order_id, extra_data,
                                                    **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    # noinspection PyBroadException
    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_get_deals(self, symbol=None, count=100, start_time="", end_time="",
                        extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_get_clear_price(self, symbol, extra_data=None, **kwargs):
        data_type = "get_clear_price"
        path = self._params.get_rest_path(data_type)
        params = {
            "symbol": self._params.get_symbol(symbol)
        }
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ===== Async methods for new market data endpoints =====

    def async_get_agg_trades(self, symbol, from_id=None, start_time=None, end_time=None,
                             count=500, extra_data=None, **kwargs):
        path, params, extra_data = self._get_agg_trades(
            symbol, from_id, start_time, end_time, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_open_interest(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_interest(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_continuous_kline(self, pair, period, contract_type="PERPETUAL",
                                   count=100, start_time=None, end_time=None,
                                   extra_data=None, **kwargs):
        path, params, extra_data = self._get_continuous_kline(
            pair, period, contract_type, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_index_price_kline(self, pair, period, count=100, start_time=None,
                                    end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_index_price_kline(
            pair, period, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_mark_price_kline(self, symbol, period, count=100, start_time=None,
                                   end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price_kline(
            symbol, period, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_funding_info(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_info(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_long_short_ratio(self, symbol, period="5m", count=30, start_time=None,
                                   end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_long_short_ratio(
            symbol, period, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    def async_get_taker_buy_sell_volume(self, symbol, period="5m", count=30, start_time=None,
                                        end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_taker_buy_sell_volume(
            symbol, period, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=False),
                    callback=self.async_callback)

    # ===== Async methods for new trade endpoints =====

    def async_get_all_orders(self, symbol, order_id=None, start_time=None, end_time=None,
                             count=500, extra_data=None, **kwargs):
        path, params, extra_data = self._get_all_orders(
            symbol, order_id, start_time, end_time, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_modify_order(self, symbol, order_id=None, orig_client_order_id=None,
                           side=None, quantity=None, price=None,
                           extra_data=None, **kwargs):
        path, params, extra_data = self._modify_order(
            symbol, order_id, orig_client_order_id, side, quantity, price,
            extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_cancel_orders(self, symbol, order_id_list=None, client_order_id_list=None,
                            extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_orders(
            symbol, order_id_list, client_order_id_list, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_cancel_all_orders(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_all_orders(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    # ===== Async methods for new account endpoints =====

    def async_get_leverage_bracket(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_leverage_bracket(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_get_position_mode(self, extra_data=None, **kwargs):
        path, params, extra_data = self._get_position_mode(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_get_income(self, symbol=None, income_type=None, start_time=None, end_time=None,
                         count=100, extra_data=None, **kwargs):
        path, params, extra_data = self._get_income(
            symbol, income_type, start_time, end_time, count, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_change_leverage(self, symbol, leverage, extra_data=None, **kwargs):
        path, params, extra_data = self._change_leverage(symbol, leverage, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_change_margin_type(self, symbol, margin_type, extra_data=None, **kwargs):
        path, params, extra_data = self._change_margin_type(symbol, margin_type, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

    def async_get_fee(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_fee(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data, is_sign=True),
                    callback=self.async_callback)

