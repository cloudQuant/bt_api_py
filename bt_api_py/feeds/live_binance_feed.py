# -*- coding: utf-8 -*-
import hmac
# import urllib
import time
import json
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
#     max_retries=5,  # 最大重试次数
#     pool_connections=100,  # 连接池大小
#     pool_maxsize=100,  # 连接池最大空闲连接数
#     pool_block=True,  # 连接池是否阻塞
# )
# session.mount('http://', adapter)
# session.mount('https://', adapter)

class BinanceRequestData(Feed):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestData, self).__init__(data_queue)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "binance_swap_feed.log")
        self._params = BinanceExchangeDataSwap()
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
    def request(self, path, params=None, body=None, extra_data=None, timeout=3, is_sign=True):
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """
        get tick price by symbol
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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
        get depth data from okx using requests
        :param symbol: instrument name
        :param size: the size of the orderbook level
        :param args:  pass a variable number of arguments to a function.
        :param extra_data: extra_data, generate by user and extends by function
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
        :param extra_data: extra_data, generate by user and function
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
            params.update({"startTime": start_time})
        if end_time is not None:
            params.update({"endTime": end_time})
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
        :param extra_data: extra_data, generate by user and function
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
        get funding rate from okx
        :param symbol: symbol name, eg: BTC-USDT.
        :param args:  pass a variable number of arguments to a function.
        :param extra_data: extra_data, generate by user and function
        :param kwargs: pass a key-worded, variable-length argument list.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_funding_rate"
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
        :param extra_data: extra_data, generate by user and function
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
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, order_type = order_type.split('-')
        time_in_force = kwargs.get('time_in_force', "GTC")
        params = {
            'symbol': request_symbol,
            'side': side.upper(),
            'quantity': vol,
            'price': price,
            'type': order_type.upper(),
            'timeInForce': time_in_force,
        }
        if self.asset_type == "SWAP":
            params['reduceOnly'] = 'false' if offset == 'open' else 'true' if offset == 'close' else None,
        if client_order_id is not None:
            params['newClientOrderId'] = client_order_id
        if order_type == 'market':
            params.pop("timeInForce", None)
            params.pop("price", None)
        if "position_side" in kwargs:
            params["positionSide"] = kwargs["position_side"]
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param order_id: order_id ,default is None, can be a string passed by user
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5, is_sign=False):
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
        :param symbol: default None, get all the currency, can be string, eg "BTC-USDT".
        :param extra_data: extra_data ,default is None, can be a dict passed by user
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
            self.async_logger.warn(f"async_callback::{e}")

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


class BinanceRequestDataSwap(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "binance_swap_feed.log")
        self._params = BinanceExchangeDataSwap()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()


class BinanceRequestDataSpot(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "binance_spot_feed.log")
        self._params = BinanceExchangeDataSpot()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

    def _make_order(self, symbol, vol, price=None, order_type='buy-limit',
                    offset='open', post_only=False, client_order_id=None,
                    extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        side, order_type = order_type.split('-')
        time_in_force = kwargs.get('time_in_force', "GTC")
        params = {
            'symbol': request_symbol,
            'side': side.upper(),
            'quantity': vol,
            'price': price,
            'type': order_type.upper(),
            'timeInForce': time_in_force,
        }
        if client_order_id is not None:
            params['newClientOrderId'] = client_order_id
        if order_type == 'market':
            params.pop("timeInForce", None)
            params.pop("price", None)
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
        print("run spot make_order")
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                    post_only, client_order_id, extra_data,
                                                    **kwargs)
        # print("params = ", params)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

class BinanceMarketWssData(MyWebsocketApp, BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssData, self).__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.wss_url = kwargs.get("wss_url", None)  # 必须传入特定的链接
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.listen_key = kwargs.get("listen_key", None)
        # ping = threading.Thread(target=self.ping)
        # ping.start()
        # print("初始化成功")

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected =====")
        self._init()
        time.sleep(0.1)

    def _init(self):
        count = 0
        for topics in self.topics:
            # time.sleep(0.2)
            count += 1
            if "ticker" == topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='tick', symbol=symbol)
                print(f"订阅{count}个数据,数据类型---ticker---{symbol}")

            if "depth" == topics['topic']:
                # print(topics)
                if "symbol" in topics:
                    symbol = topics.get("symbol")
                    self.subscribe(topic='depth', symbol=symbol, type='step0')
                    print(f"订阅{count}个数据,数据类型---depth---{symbol}")
                elif "symbol_list" in topics:
                    symbol_list = topics.get("symbol_list")
                    self.subscribe(topic='depth', symbol_list=symbol_list, type='step0')
                    print(f"订阅{count}个数据,数据类型---depth---symbol_list")
                else:
                    print("depth need symbol to subscribe")

            if 'funding_rate' == topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='funding_rate', symbol=symbol)
                print(f"订阅{count}个数据,数据类型---funding_rate---{symbol}")

            if 'mark_price' == topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='mark_price', symbol=symbol)
                print(f"订阅{count}个数据,数据类型---mark_price---{symbol}")

            if "kline" == topics['topic']:
                period = topics.get("period", "1m")
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='kline', symbol=symbol, period=period)
                print(f"订阅{count}个数据,数据类型---kline---{symbol}---{period}")

            if "all_mark_price" == topics['topic']:
                self.subscribe(topic='all_mark_price')
                print(f"订阅{count}个数据,数据类型---all_mark_price")

            if "all_ticker" == topics['topic']:
                self.subscribe(topic='all_ticker')
                print(f"订阅{count}个数据,数据类型---all_mark_price")

            if "all_force_order" == topics['topic']:
                self.subscribe(topic='all_force_order')
                print(f"订阅{count}个数据,数据类型---all_force_order")

            if "agg_trade" == topics['topic']:
                if "symbol" in topics:
                    symbol = topics.get("symbol")
                    self.subscribe(topic='agg_trade', symbol=symbol)
                    print(f"订阅{count}个数据,数据类型---agg_trade---{symbol}")
                if "symbol_list" in topics:
                    symbol_list = topics.get("symbol_list")
                    self.subscribe(topic='agg_trade', symbol_list=symbol_list)
                    print(f"订阅{count}个数据,数据类型---agg_trade---symbol_list")

            if "force_order" == topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='force_order', symbol=symbol)
                print(f"订阅{count}个数据,数据类型---force_order---{symbol}")

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
            if "bookTicker" == event:
                self.push_ticker(content)
            if "depthUpdate" == event:
                self.push_order_book(content)
            if "kline" == event:
                self.push_bar(content)
            if "markPriceUpdate" == event:
                self.push_funding_rate(content)
            if "markPriceUpdate" == event:
                self.push_mark_price(content)
            if "ACCOUNT_UPDATE" == event:
                self.push_account(content)
            if "aggTrade" == event:
                self.push_agg_trade(content)
            if "forceOrder" == event:
                self.push_force_order(content)
            # if "bookTicker" == event:
            #     self.push_order(content)
            # if "bookTicker" == event and content['data'][0].get("tradeId") != "":
            #     self.push_trade(content)
            # # if "trade" in arg["channel"]:
            # #     self.push_trade(content)
            # if "bookTicker" == event:
            #     self.push_position(content)

    def push_force_order(self, content):
        # print("接收到force_order: ", content)
        symbol = content['o']['s']
        force_order_data = BinanceForceOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(force_order_data)

    def push_agg_trade(self, content):
        symbol = content['s']
        agg_trade_data = BinanceAggTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(agg_trade_data)

    def push_mark_price(self, content):
        symbol = content['s']
        mark_price_data = BinanceWssMarkPriceData(content, symbol, self.asset_type, True)
        self.data_queue.put(mark_price_data)
        # print("获取mark_price成功, mark_price is ", mark_price_data.get_mark_price())

    def push_funding_rate(self, content):
        # 资金费率推送
        symbol = content['s']
        funding_rate_data = BinanceWssFundingRateData(content, symbol, self.asset_type, True)
        self.data_queue.put(funding_rate_data)
        # print("获取funding_rate成功，当前funding_rate = ", funding_rate_data.get_current_funding_rate())

    def push_ticker(self, content):
        # 推送ticker数据到添加事件中
        symbol = content['s']
        ticker_data = BinanceWssTickerData(content, symbol, self.asset_type, True)
        self.data_queue.put(ticker_data)
        # print("获取ticker数据成功，ticker ask_price = ", ticker_data.get_ask_price())

    def push_order_book(self, content):
        # 推送order_book数据并添加到事件中
        symbol = content['s']
        order_book_data = BinanceWssOrderBookData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_book_data)
        # print("获取orderbook成功, 当前价格为：", order_book_data.get_ask_price_list())

    def push_bar(self, content):
        # 推送bar数据并添加到事件中
        symbol = content['s']
        bar_data = BinanceWssBarData(content, symbol, self.asset_type, True)
        self.data_queue.put(bar_data)
        bar_data.init_data()
        # print("获取kline成功，close_price = ", bar_data.get_close_price())

    def push_account(self, content):
        # 推送account数据并添加到事件中
        account_info = content['data'][0]
        symbol = "ANY"
        account_data = BinanceSwapWssAccountData(account_info, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_total_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        order_info = content['data'][0]
        symbol = content['arg']['symbol']
        order_data = BinanceSwapWssOrderData(order_info, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        trade_info = content['data'][0]
        symbol = content['arg']['symbol']
        trade_data = BinanceSwapWssTradeData(trade_info, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())

    def push_position(self, content):
        data = content['data']
        if len(data) > 0:
            position_info = data[0]
            symbol = content['arg']['symbol']
            position_data = BinanceWssPositionData(position_info, symbol, self.asset_type, True)
            self.data_queue.put(position_data)
            # print("获取position数据成功，当前账户持仓为：", position_data.get_position_symbol_name(),
            #       position_data.get_position_qty())

    def message_rsp(self, message):
        rsp = json.loads(message)
        if isinstance(rsp, dict):
            if 'result' in rsp:
                if rsp['id'] == 1:
                    self.wss_logger.info(f"===== {self._params.exchange_name} Data Websocket Connected =====")
                else:
                    print("restart操作")
                    self.ws.restart()
            elif 'e' in rsp:
                self.handle_data(rsp)
                return
        elif isinstance(rsp, list):
            for data in rsp:
                self.handle_data(data)


class BinanceMarketWssDataSwap(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class BinanceMarketWssDataSpot(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class BinanceAccountWssData(MyWebsocketApp, BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceAccountWssData, self).__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.wss_url = kwargs.get("wss_url", None)  # 必须传入特定的链接
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.exchange_name = kwargs.get("exchange_name", None)
        self.symbol_name = kwargs.get("symbol_name", None)
        self.listen_key = kwargs.get("listen_key", None)
        self.wss_author()
        # ping = threading.Thread(target=self.ping)
        # ping.start()
        # print("初始化成功")

    def get_listen_key(self):
        path = self._params.get_rest_path("get_listen_key")
        extra_data = {
            "asset_type": self.asset_type,
            "symbol_name": None,
            "request_type": "get_listen_key",
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        }
        data = self.request(path, extra_data=extra_data, is_sign=False)
        return data.get_data()

    def refresh_listen_key(self):
        params = {
            'listenKey': self.listen_key,
        }
        extra_data = {
            "asset_type": "get_listen_key",
            "symbol_name": None,
            "request_type": "get_listen_key"
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
        self.listen_key = self.get_listen_key()['listenKey']
        self.wss_url = f"{self._params.wss_url}/{self.listen_key}"
        # print("wss_author", self.wss_url)

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self._params.exchange_name} Websocket Connected =====")

    def _init(self):
        for topics in self.topics:
            if "orders" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='orders', symbol=symbol)
            if "account" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                currency = topics.get("currency", "USDT")
                self.subscribe(topic='account', symbol=symbol, currency=currency)
            if "positions" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='positions', symbol=symbol)
            if "balance_position" in self.topics:
                self.subscribe(topic='balance_position')

    def handle_data(self, content):
        event = content.get("e", None)
        if event is not None:
            if "ACCOUNT_UPDATE" == event:
                self.push_account(content)
            if 'ORDER_TRADE_UPDATE' == event:
                self.push_order(content)
            if 'ORDER_TRADE_UPDATE' == event and content['o'].get("t") != 0:
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
        symbol = content['o']['s']
        order_data = BinanceSwapWssOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        symbol = content['o']['s']
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


class BinanceAccountWssDataSwap(BinanceAccountWssData):
    pass


class BinanceAccountWssDataSpot(BinanceAccountWssData):
    def handle_data(self, content):
        event = content.get("e", None)
        if event is not None:
            # 现货账户事件类型
            if "executionReport" == event and content.get("x", None) != "TRADE":
                self.push_order(content)
            if "outboundAccountPosition" == event:
                self.push_account(content)
            if "executionReport" == event and content.get("x", None) == "TRADE":
                self.push_trade(content)
            # if "balanceUpdate" == event:
            #     self.push_balance(content)

    def push_account(self, content):
        # 推送account数据并添加到事件中
        # print("订阅到账户数据")
        symbol = "ALL"
        account_data = BinanceSpotWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_balances()[0].get_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        symbol = content['s']
        order_data = BinanceSpotWssOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        # print("订阅到trade数据")
        symbol = content['s']
        trade_data = BinanceSpotWssTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())


if __name__ == "__main__":
    import queue
    import random
    from bt_api_py.functions.utils import read_yaml_file

    data_queue_ = queue.Queue()
    data_ = read_yaml_file("account_config.yaml")
    kwargs_ = {
        "public_key": data_['binance']['public_key'],
        "private_key": data_['binance']['private_key'],
        "exchange_data": BinanceExchangeDataSpot(),
        "topics": {"tick": {"symbol": "BTC-USDT"}}
    }
    # 尝试获取资金费率
    # live_binance_swap_feed = BinanceRequestDataSwap(data_queue_, **kwargs_)
    # funding_rate_data = live_binance_swap_feed.get_history_funding_rate(
    #     "BTC-USDT", start_time="2024-10-10 00:00:00.000", end_time="2024-12-10 00:00:00.000")
    #
    # funding_rate_list = funding_rate_data.get_data()
    # result = []
    # for item in funding_rate_list:
    #     item.init_data()
    #     result.append([item.get_symbol_name(), item.get_current_funding_rate(), item.get_server_time()])
    # import pandas as pd
    # df = pd.DataFrame(result, columns=['symbol', 'current_funding_rate', 'funding_rate_time'])
    # print(df)

    # live_binance_spot_feed = BinanceRequestDataSpot(data_queue_, **kwargs_)
    # price_data = live_binance_spot_feed.get_tick("OP-USDT")
    # price_data = price_data.get_data()[0].init_data()
    # bid_price = round(price_data.get_bid_price() * 0.9, 2)
    # ask_price = round(price_data.get_ask_price() * 1.1, 2)
    # random_number = random.randint(10 ** 17, 10 ** 18 - 1)
    # buy_client_order_id = str(random_number)
    # buy_data = live_binance_spot_feed.make_order("OP-USDT", 4,
    #                                              bid_price, "buy-limit",
    #                                              # client_order_id=buy_client_order_id,
    #                                              # **{"position_side": "LONG"}
    #                                              )

    # buy_data = live_binance_spot_feed.make_order("MOVR-USDT", 1,
    #                                              8.7, "buy-limit",
    #                                              # client_order_id=buy_client_order_id,
    #                                              )

    # 测试买单和卖单
    # buy_info = buy_data.get_data()[0]
    # assert buy_data.get_status()
    # assert isinstance(buy_data, RequestData)
    # buy_order_id = buy_info.init_data().get_order_id()
    # assert buy_order_id is not None
    #
    # # 用order_id和client_order_id进行撤单
    # data_ = live_binance_spot_feed.cancel_order("OP-USDT", order_id=int(buy_order_id))
    # print(data_.get_data())
    # data = live_binance_spot_feed.cancel_order("MOVR-USDT", order_id=int(buy_order_id))
    # print(data.get_data())
