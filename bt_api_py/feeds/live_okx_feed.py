# -*- coding: utf-8 -*-
"""
1. okx单币种模式下，现货下单的时候返回的订单种类并不是SPOT, 而是MARGIN, 订阅orders的时候需要制定为MARGIN
"""
# import requests
import hmac
import base64
import time
import json
from urllib import parse
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap, OkxExchangeDataSpot
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp
from bt_api_py.functions.log_message import SpdLogManager
# from bt_api_py.functions.calculate_time import get_string_tz_time
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData
from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData
from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData
from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData
from bt_api_py.containers.accounts.okx_account import OkxAccountData
from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.containers.trades.okx_trade import OkxRequestTradeData, OkxWssTradeData
from bt_api_py.containers.positions.okx_position import OkxPositionData

# session = requests.Session()
# session.keep_alive = False
# adapter = requests.adapters.HTTPAdapter(
#     max_retries=5, # 最大重试次数
#     pool_connections=100, # 连接池大小
#     pool_maxsize=100, # 连接池最大空闲连接数
#     pool_block=True, # 连接池是否阻塞)
# session.mount('http://', adapter)
# session.mount('https://', adapter)


class OkxRequestData(Feed):
    def __init__(self, data_queue, **kwargs):
        super(OkxRequestData, self).__init__(data_queue)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.passphrase = kwargs.get("passphrase", None)
        self.topics = kwargs.get("topics", {})
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "okx_swap_feed.log")
        # self.account_wss = OkxAccountWss("okx_account_wss",
        #                                  self._params.account_wss_url,
        #                                  data_queue, **kwargs)
        # self.data_wss = OkxDataWss("okx_data_wss",
        #                            self._params.wss_url,
        #                            data_queue, **kwargs)

        # self._params = kwargs.get("exchange_data", None)
        self._params = OkxExchangeDataSwap()
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
    def signature(self, timestamp, method, request_path, secret_key, body=None):
        if body is None:
            body = ''
        else:
            body = str(body)
        message = str(timestamp) + str.upper(method) + request_path + body
        mac = hmac.new(bytes(secret_key, encoding='utf8'), bytes(message, encoding='utf-8'), digestmod='sha256')
        d = mac.digest()
        return base64.b64encode(d).decode()

    # def sign(self, content):
    #     """签名
    #
    #     Args:
    #         content (TYPE): Description
    #     """
    #     sign = base64.b64encode(
    #         hmac.new(
    #             self.private_key.encode('utf-8'), content.encode('utf-8'), digestmod='sha256'
    #         ).digest()
    #     ).decode()
    #
    #     return sign

    # set request header
    # noinspection PyMethodMayBeStatic
    def get_header(self, api_key, sign, timestamp, passphrase):
        header = dict()
        header['Content-Type'] = 'application/json'
        header['OK-ACCESS-KEY'] = api_key
        header['OK-ACCESS-SIGN'] = sign
        header['OK-ACCESS-TIMESTAMP'] = str(timestamp)
        header['OK-ACCESS-PASSPHRASE'] = passphrase
        header['x-simulated-trading'] = "0"
        return header

    def request(self, path, params=None, body=None, extra_data=None, timeout=3):
        """http request function
        Args:
            path (TYPE): request url
            params (dict, optional): in url
            body (dict, optional): in request body
            extra_data(dict,None): extra_data, generate by user
            timeout (int, optional): request timeout(s)
        """
        if params is None:
            params = {}
        # if body is None:
        #     body = {}
        method, path = path.split(' ', 1)
        req = parse.urlencode(params)
        url = f"{self._params.rest_url}{path}?{req}"  # ?{req}
        if params:
            path = f"{path}?{req}"
        timestamp = round(time.time(), 3)  # datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        # now = datetime.datetime.utcnow()
        # t = now.isoformat("T", "milliseconds")
        # timestamp = t + "Z"
        signature_ = self.signature(timestamp, method, path, self.private_key,
                                    json.dumps(body) if body is not None else None)
        headers = self.get_header(self.public_key, signature_, timestamp, self.passphrase)
        # print("method = ", method)
        # print("url = ", url)
        # print("headers = ", headers)
        # print("body = ", body)
        # print("timeout = ", timeout)
        res = self.http_request(method, url, headers, body, timeout)
        # print(res)
        # request_type = extra_data.get('request_type')
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
        if symbol is None:
            params = {
                'ccy': ''
            }
            extra_data = update_extra_data(extra_data, **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": OkxRequestData._get_account_normalize_function,
            })
        else:
            params = {
                'ccy': symbol
            }
            extra_data = update_extra_data(extra_data, **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxAccountData(data,
                                        extra_data['symbol_name'],
                                        extra_data['asset_type'],
                                        True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)

        return data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        return self.get_account(symbol, extra_data, **kwargs)

    def _get_position(self, symbol, extra_data=None, **kwargs):
        """
        get position info from okx by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        _request_symbol = self._params.get_symbol(symbol)
        request_type = "get_position"
        path = self._params.get_rest_path(request_type)
        params = {
            "instType": "",
            "instId": symbol,
            "posId": ""
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_position_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_position_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxPositionData(data[0],
                                         extra_data['symbol_name'],
                                         extra_data['asset_type'],
                                         True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

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
        params = {
            'instId': self._params.get_symbol(symbol),
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_tick_normalize_function,

        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxTickerData(data,
                                       extra_data['symbol_name'],
                                       extra_data['asset_type'],
                                       True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
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
            'instId': request_symbol,
            "sz": size
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_depth_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxOrderBookData(data,
                                          extra_data['symbol_name'],
                                          extra_data['asset_type'],
                                          True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_kline(self, symbol, period, count=100, start_time=0, end_time=0, extra_data=None, **kwargs):
        """
        get kline from okx using request.
        :param symbol: instrument name.
        :param period: kline interval.
        :param count: kline number, default is 100.
        :param start_time: begin time, default is 0.
        :param end_time: end time, default is 0.
        :param extra_data: extra_data, generated by user and function
        :param args:  pass a variable number of arguments to a function.
        :param kwargs: pass a key-worded, variable-length argument list.
        :return: tuple of (str, dict, dict)
        """
        request_type = "get_kline"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'bar': self._params.get_period(period),
        }
        if count:
            params['limit'] = count
        if end_time:
            params.update({"after": end_time})
        if start_time:
            params.update({"before": start_time})
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_kline_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxBarData(data,
                                    extra_data['symbol_name'],
                                    extra_data['asset_type'],
                                    True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_kline(self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
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
            'instId': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_funding_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_funding_rate_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxFundingRateData(data,
                                            extra_data['symbol_name'],
                                            extra_data['asset_type'],
                                            True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
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
            'instId': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": "SPOT",
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_mark_price_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_mark_price_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxMarkPriceData(data,
                                          extra_data['symbol_name'],
                                          extra_data['asset_type'],
                                          True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

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
        path, params, extra_data = self._get_config(extra_data=extra_data)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_config(self, extra_data=None):
        path, params, extra_data = self._get_config(extra_data=extra_data)
        self.submit(self.async_request(path, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_config(self, extra_data=None):
        params = {}
        path = self._params.get_rest_path("get_config")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_config",
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_config_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_config_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        # print("self.input_data", self.input_data)
        if extra_data is None:
            pass
        data = input_data['data']
        if len(data) > 0:
            data = data
        else:
            data = []
        return data, status

    def set_lever(self, symbol):
        symbol = self._params.get_symbol(symbol)
        params = {
            "instId": symbol,
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
        try:
            vol = round(vol * self._params.symbol_leverage_dict[symbol])
        except Exception as e:
            self.request_logger.warn(f"_make_order:{e}")
        side, ord_type = order_type.split("-")
        # offset和clientOrderId是两个未使用参数，避免警告
        if post_only:
            ord_type = "post_only"
        params = {
            'instId': request_symbol,
            "tdMode": "cross",
            "ccy": "USDT",
            "clOrdId": client_order_id,
            'side': side,
            'ordType': ord_type,
            'px': str(price),
            'sz': str(vol),
        }
        # if "symbol_type" in kwargs:
        #     params['tdMode'] = kwargs['symbol_type']
        path = self._params.get_rest_path(request_type)
        path = path.replace("<instrument_id>", request_symbol)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "offset": offset,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._make_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = True if input_data["code"] == '0' else False
        data = [{
            "client_order_id": i["clOrdId"],
            "order_id": i["ordId"],
            "tag": i["tag"],
            "s_code": i["sCode"],
            "s_msg": i["sMsg"],
            "in_server_time": input_data['inTime'],
            "out_server_time": input_data['outTime'],
        }
            for i in input_data['data']]
        return data, status

    # noinspection PyBroadException
    def make_order(self, symbol, vol, price=None, order_type='buy-limit',
                   offset='open', post_only=False, client_order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                    post_only, client_order_id, extra_data,
                                                    **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
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
            'instId': request_symbol
        }
        if order_id:
            params['ordId'] = order_id
        if "client_order_id" in kwargs:
            params["clOrdId"] = kwargs["client_order_id"]
        # update params
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_order_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if extra_data:
            pass
        status = True if input_data["code"] == '0' else False
        data = input_data['data']
        if len(data) > 0:
            data_list = [{
                "client_order_id": i["clOrdId"],
                "order_id": i["ordId"],
                "s_code": i["sCode"],
                "s_msg": i["sMsg"]
            }
                for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
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
        }
        if order_id is not None:
            params["ordId"] = order_id
        if "client_order_id" in kwargs:
            params['clOrdId'] = kwargs['client_order_id']
        params['instId'] = request_symbol
        # update extra_data
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._query_order_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxOrderData(i,
                                      extra_data['symbol_name'],
                                      extra_data['asset_type'],
                                      True)
                         for i in data]
            data = data_list
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
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
        else:
            request_symbol = ''
        request_type = "get_open_orders"
        uly = kwargs.get("uly", "")
        inst_type = kwargs.get("instType", "")
        ord_type = kwargs.get("ordType", "")
        state = kwargs.get("state", "")
        after = kwargs.get("after", "")
        before = kwargs.get("before", "")
        limit = kwargs.get("limit", "")
        inst_family = kwargs.get("instFamily", "")
        params = {'instType': inst_type, 'uly': uly, 'instId': request_symbol,
                  'ordType': ord_type, 'state': state, 'after': after,
                  'before': before, 'limit': limit, 'instFamily': inst_family}

        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_open_orders_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxOrderData(data,
                                      extra_data['symbol_name'],
                                      extra_data['asset_type'],
                                      True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    # noinspection PyBroadException
    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
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
        # params = {'instType': instType, 'uly': uly, 'instId': instId,
        #           'ordId': ordId, 'after': after, 'before': before,
        #           'limit': limit, 'instFamily': instFamily}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
        else:
            request_symbol = ""
            symbol = ""
        request_type = "get_deals"
        params = {
            # "instrument_id	":symbol,
            "instType": self.asset_type,
            "instId": request_symbol,
            "limit": str(count),
            'uly': kwargs.get("underlying", ""),
            'ordId': kwargs.get("ordId", ""),
            'instFamily': kwargs.get("instFamily", ""),
            "before": "",
            "after": "",
            "start": start_time,
            "end": end_time
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "exchange_name": self.exchange_name,
            "asset_type": self.asset_type,
            "normalize_function": OkxRequestData._get_deals_normalize_function,
        })
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxRequestTradeData(data[0],
                                             extra_data['symbol_name'],
                                             extra_data['asset_type'],
                                             True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    # noinspection PyBroadException
    def get_deals(self, symbol=None, count=100,
                  start_time="", end_time="",
                  extra_data=None,
                  **kwargs):
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data,
                                                   **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5) -> RequestData:
        """http request function
        Args:
            path (TYPE): request url
            params (dict, optional): in url
            body (dict, optional): in request body
            timeout (int, optional): request timeout(s)
            extra_data(dict,None): extra_data, generate by user
        """
        if params is None:
            params = {}
        method, path = path.split(' ', 1)
        req = parse.urlencode(params)
        url = f"{self._params.rest_url}{path}?{req}"  # ?{req}
        if params:
            path = f"{path}?{req}"
        timestamp = round(time.time(), 3)
        signature_ = self.signature(timestamp, method, path, self.private_key,
                                    json.dumps(body) if body is not None else None)
        headers = self.get_header(self.public_key, signature_, timestamp, self.passphrase)
        res = await self.async_http_request(method, url, headers, body, timeout)
        # self.request_logger.info(f"""request:{get_string_tz_time()} {res}""")
        # request_type = extra_data.get('request_type')
        # data_factory = self._params.request_data_dict.get(request_type)
        return RequestData(res, extra_data)

    # noinspection PyBroadException
    def async_get_account(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance_assert")
        self.submit(self.async_request(path, extra_data=extra_data),
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
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
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
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # noinspection PyMethodMayBeStatic
    def async_get_kline(self, symbol, period, count=100, before=0, after=0, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, before, after, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_mark_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_set_lever(self, symbol, extra_data=None):
        symbol = self._params.get_symbol(symbol)
        params = {
            "instId": symbol,
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
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # noinspection PyBroadException
    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_deals(self, symbol=None, count=100, start_time="", end_time="",
                        extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def async_get_clear_price(self, symbol, extra_data=None, **kwargs):
        data_type = "get_clear_price"
        path = self._params.get_rest_path(data_type)
        params = {
            "instId": self._params.get_symbol(symbol)
        }
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)


class OkxRequestDataSwap(OkxRequestData):
    def __init__(self, data_queue, **kwargs):
        super(OkxRequestDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "okx_swap_feed.log")


class OkxRequestDataSpot(OkxRequestData):
    def __init__(self, data_queue, **kwargs):
        super(OkxRequestDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "okx_spot_feed.log")
        self._params = OkxExchangeDataSpot()

    def _get_index_price(self, symbol, extra_data=None, **kwargs):
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
        else:
            request_symbol = ""
        request_type = "get_index_price"
        params = {
            "instId": request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": request_symbol,
            "exchange_name": self.exchange_name,
            "asset_type": self.asset_type,
            "normalize_function": OkxRequestDataSpot._get_index_price_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_index_price_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        # print("input_data", input_data)
        status = True if input_data["code"] == '0' else False
        data = input_data["data"][0]
        timestamp = float(data["ts"])
        data = [timestamp, float(data["idxPx"])]
        # ret = transform.normalizeClearPrice(data=data)
        return data, status

    def get_index_price(self, symbol, extra_data=None, **kwargs):

        path, params, extra_data = self._get_index_price(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data


class OkxWssData(MyWebsocketApp):
    def __init__(self, data_queue, **kwargs):
        super(OkxWssData, self).__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.passphrase = kwargs.get("passphrase", None)
        self.wss_url = kwargs.get("wss_url", None)  # 必须传入特定的链接
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
        # timestamp = f"{datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z"
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
            if "ticker" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='tick', symbol=symbol)
            if "depth" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='depth', symbol=symbol, type='step0')
            if "books" in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='books', symbol=symbol, type='step0')
            if 'bidAsk' in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='bidAsk', symbol=symbol, type='step0')
            if 'funding_rate' in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='funding_rate', symbol=symbol)
            if 'mark_price' in topics['topic']:
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='mark_price', symbol=symbol)
            if "kline" in topics['topic']:
                period = topics.get("period", "1m")
                symbol = topics.get("symbol", "BTC—USDT")
                self.subscribe(topic='kline', symbol=symbol, period=period)

    def handle_data(self, content):
        # print(content)
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
            # if "trade" in arg["channel"]:
            #     self.push_trade(content)
            if "positions" in arg["channel"]:
                self.push_position(content)

    def push_mark_price(self, content):
        mark_price_info = content['data'][0]
        symbol = content['arg']['instId']
        mark_price_data = OkxMarkPriceData(mark_price_info, symbol, self.asset_type, True)
        self.data_queue.put(mark_price_data)
        # print("获取mark_price成功, mark_price is ", mark_price_data.get_mark_price())

    def push_funding_rate(self, content):
        # 资金费率推送
        funding_rate_info = content['data'][0]
        symbol = content['arg']['instId']
        funding_rate_data = OkxFundingRateData(funding_rate_info, symbol, self.asset_type, True)
        self.data_queue.put(funding_rate_data)
        # print("获取funding_rate成功，当前funding_rate = ", funding_rate_data.get_current_funding_rate())

    def push_ticker(self, content):
        # 推送ticker数据到添加事件中
        ticker_info = content['data'][0]
        symbol = content['arg']['instId']
        ticker_data = OkxTickerData(ticker_info, symbol, self.asset_type, True)
        self.data_queue.put(ticker_data)
        # print("获取ticker数据成功，ticker ask_price = ", ticker_data.get_ask_price())

    def push_order_book(self, content):
        # 推送order_book数据并添加到事件中
        order_book_info = content['data'][0]
        symbol = content['arg']['instId']
        order_book_data = OkxOrderBookData(order_book_info, symbol, self.asset_type, True)
        self.data_queue.put(order_book_data)
        # print("获取orderbook成功, 当前价格为：", order_book_data.get_ask_price_list())

    def push_bar(self, content):
        # 推送bar数据并添加到事件中
        bar_info = content['data'][0]
        symbol = content['arg']['instId']
        bar_data = OkxBarData(bar_info, symbol, self.asset_type, True)
        # bar_data.init_data()
        # bar_status = bar_data.get_bar_status()
        # if bar_status:
        #     self.data_queue.put(bar_data)
        self.data_queue.put(bar_data)
        # print("获取kline成功，close_price = ", bar_data.get_close_price())

    def push_account(self, content):
        # 推送account数据并添加到事件中
        account_info = content['data'][0]
        symbol = "ANY"
        account_data = OkxAccountData(account_info, symbol, self.asset_type, True)
        self.data_queue.put(account_data)
        # print("获取account数据成功，当前账户净值为：", account_data.get_total_margin())

    def push_order(self, content):
        # print("订阅到order数据")
        order_info = content['data'][0]
        symbol = content['arg']['instId']
        order_data = OkxOrderData(order_info, symbol, self.asset_type, True)
        self.data_queue.put(order_data)
        # print("获取order成功，当前order_status 为：", order_data.get_order_status())

    def push_trade(self, content):
        trade_info = content['data'][0]
        symbol = content['arg']['instId']
        trade_data = OkxWssTradeData(trade_info, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
        # print("获取trade成功，当前trade_id 为：", trade_data.get_trade_id())

    def push_position(self, content):
        data = content['data']
        if len(data) > 0:
            position_info = data[0]
            symbol = content['arg']['instId']
            position_data = OkxPositionData(position_info, symbol, self.asset_type, True)
            self.data_queue.put(position_data)
            # print("获取position数据成功，当前账户持仓为：", position_data.get_position_symbol_name(),
            #       position_data.get_position_qty())

    def message_rsp(self, message):
        rsp = json.loads(message)
        if 'event' in rsp:
            if rsp['event'] == 'login':  # 鉴权
                if rsp['code'] == "0":
                    self.wss_logger.info(f"===== {self._params.exchange_name} Data Websocket Connected =====")
                    # self._init()
                else:
                    self.ws.restart()
            elif rsp['event'] == 'subscribe':
                self.wss_logger.info(f"===== Data Websocket {rsp} =====")
                pass
        elif 'arg' in rsp:
            self.handle_data(rsp)
            return


class OkxAccountWssData(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxAccountWssData, self).__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.account_wss_url)


class OkxAccountWssDataSwap(OkxAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxAccountWssDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxAccountWssDataSpot(OkxAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxAccountWssDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class OkxMarketWssData(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxMarketWssData, self).__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.wss_url)


class OkxMarketWssDataSwap(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxMarketWssDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxMarketWssDataSpot(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxMarketWssDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class OkxKlineWssData(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxKlineWssData, self).__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.kline_wss_url)


class OkxKlineWssDataSwap(OkxKlineWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxKlineWssDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxKlineWssDataSpot(OkxKlineWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxKlineWssDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class OkxWssDataSwap(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxWssDataSwap, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SWAP")


class OkxWssDataSpot(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxWssDataSpot, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


if __name__ == "__main__":
    pass
