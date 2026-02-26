# -*- coding: utf-8 -*-
"""
OKX REST API request base class.
Handles authentication, signing, and all REST API methods.
"""
import hmac
import base64
import time
import json
from urllib import parse
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
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
from bt_api_py.containers.symbols.okx_symbol import OkxSymbolData


class OkxRequestData(Feed):
    def __init__(self, data_queue, **kwargs):
        super(OkxRequestData, self).__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key", None)
        self.private_key = kwargs.get("private_key", None)
        self.passphrase = kwargs.get("passphrase", None)
        self.topics = kwargs.get("topics", {})
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "okx_swap_feed.log")
        self._params = OkxExchangeDataSwap()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

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
        method, path = path.split(' ', 1)
        req = parse.urlencode(params)
        url = f"{self._params.rest_url}{path}?{req}"  # ?{req}
        if params:
            path = f"{path}?{req}"
        timestamp = round(time.time(), 3)
        signature_ = self.signature(timestamp, method, path, self.private_key,
                                    json.dumps(body) if body is not None else None)
        headers = self.get_header(self.public_key, signature_, timestamp, self.passphrase)
        res = self.http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

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
        return RequestData(res, extra_data)

    def async_callback(self, future):
        """
        callback function for async requests, push result to data_queue
        :param future: asyncio future object
        :return: None
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warn(f"async_callback::{e}")

    # ==================== Account APIs ====================

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
        if 'data' not in input_data or not input_data['data']:
            return [], status
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

    # ==================== Position APIs ====================

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
        if 'data' not in input_data:
            return [], status
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
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_position(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Config APIs ====================

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
        if 'data' not in input_data:
            return [], status
        if extra_data is None:
            pass
        data = input_data['data']
        if len(data) > 0:
            data = data
        else:
            data = []
        return data, status

    def get_config(self, extra_data=None):
        path, params, extra_data = self._get_config(extra_data=extra_data)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_config(self, extra_data=None):
        path, params, extra_data = self._get_config(extra_data=extra_data)
        self.submit(self.async_request(path, extra_data=extra_data),
                    callback=self.async_callback)

    def set_mode(self):
        params = {
            "posMode": "long_short_mode"
        }
        path = self._params.get_rest_path("set_mode")
        data = self.request(path, body=params)
        return data

    def set_lever(self, symbol, lever=10, mgn_mode="cross"):
        symbol = self._params.get_symbol(symbol)
        params = {
            "instId": symbol,
            "lever": lever,
            "mgnMode": mgn_mode
        }
        path = self._params.get_rest_path("set_lever")
        data = self.request(path, body=params)
        return data

    def async_set_lever(self, symbol, lever=10, mgn_mode="cross", extra_data=None):
        symbol = self._params.get_symbol(symbol)
        params = {
            "instId": symbol,
            "lever": lever,
            "mgnMode": mgn_mode
        }
        path = self._params.get_rest_path("set_lever")
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Market Data APIs ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
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
        if 'data' not in input_data or not input_data['data']:
            return [], status
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

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_depth(self, symbol, size=20, extra_data=None, **kwargs):
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
        if 'data' not in input_data or not input_data['data']:
            return [], status
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

    def async_get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_kline(self, symbol, period, count=100, start_time=0, end_time=0, extra_data=None, **kwargs):
        request_type = "get_kline"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'bar': self._params.get_period(period),
        }
        if count and count!=100:
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
        if 'data' not in input_data:
            return [], status
        data = sorted(input_data['data'],key=lambda x: x[0])
        if len(data) > 0:
            data_list = [OkxBarData(i,
                                    extra_data['symbol_name'],
                                    extra_data['asset_type'],
                                    True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_kline(self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # noinspection PyMethodMayBeStatic
    def async_get_kline(self, symbol, period, count=100, before=0, after=0, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, before, after, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Public Data APIs ====================

    def _get_funding_rate(self, symbol, extra_data=None, **kwargs):
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
        if 'data' not in input_data or not input_data['data']:
            return [], status
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

    def async_get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_funding_rate_history(self, symbol, before="", after="", limit="100", extra_data=None, **kwargs):
        """Get funding rate history"""
        request_type = "get_funding_rate_history"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
        }
        if before:
            params['before'] = before
        if after:
            params['after'] = after
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_funding_rate_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_funding_rate_history_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxFundingRateData(i,
                                            extra_data['symbol_name'],
                                            extra_data['asset_type'],
                                            True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_funding_rate_history(self, symbol, before="", after="", limit="100", extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate_history(symbol, before, after, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_instruments(self, asset_type=None, underlying=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        request_type = "get_instruments"
        params = {}
        if asset_type:
            params['instType']=asset_type
        if underlying:
            params['uly'] = underlying
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_instruments_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_instruments_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if isinstance(data, list):
            target_data = [OkxSymbolData(i,True) for i in data]
        elif isinstance(data, dict):
            target_data = [OkxSymbolData(data,True)]
        else:
            target_data = []
        return target_data, status

    def get_instruments(self, asset_type=None, underlying=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_instruments(asset_type, underlying, inst_family, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_mark_price(self, symbol, extra_data=None, **kwargs):
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
        if 'data' not in input_data or not input_data['data']:
            return [], status
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

    def async_get_mark_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        """Get open interest data"""
        request_type = "get_open_interest"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_interest(inst_type, uly, inst_family, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # ==================== Trade APIs ====================

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
        if 'data' not in input_data:
            return [], status
        data = [{
            "client_order_id": i["clOrdId"],
            "order_id": i["ordId"],
            "tag": i["tag"],
            "s_code": i["sCode"],
            "s_msg": i["sMsg"],
            "in_server_time": input_data.get('inTime'),
            "out_server_time": input_data.get('outTime'),
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

    # noinspection PyBroadException
    def async_make_order(self, symbol, vol, price=None, order_type='buy-limit',
                         offset='open', post_only=False, client_order_id=None,
                         extra_data=None, **kwargs):
        path, params, extra_data = self._make_order(symbol, vol, price, order_type, offset,
                                                    post_only, client_order_id, extra_data,
                                                    **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _amend_order(self, symbol, order_id=None, client_order_id=None,
                     new_sz=None, new_px=None, extra_data=None, **kwargs):
        """Amend an incomplete order"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "amend_order"
        params = {
            'instId': request_symbol,
        }
        if order_id:
            params['ordId'] = order_id
        if client_order_id:
            params['clOrdId'] = client_order_id
        if new_sz is not None:
            params['newSz'] = str(new_sz)
        if new_px is not None:
            params['newPx'] = str(new_px)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._amend_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _amend_order_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = [{
            "client_order_id": i.get("clOrdId", ""),
            "order_id": i.get("ordId", ""),
            "req_id": i.get("reqId", ""),
            "s_code": i.get("sCode", ""),
            "s_msg": i.get("sMsg", ""),
        } for i in input_data['data']]
        return data, status

    def amend_order(self, symbol, order_id=None, client_order_id=None,
                    new_sz=None, new_px=None, extra_data=None, **kwargs):
        path, params, extra_data = self._amend_order(symbol, order_id, client_order_id,
                                                     new_sz, new_px, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_amend_order(self, symbol, order_id=None, client_order_id=None,
                          new_sz=None, new_px=None, extra_data=None, **kwargs):
        path, params, extra_data = self._amend_order(symbol, order_id, client_order_id,
                                                     new_sz, new_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params = {
            'instId': request_symbol
        }
        if order_id:
            params['ordId'] = order_id
        if "client_order_id" in kwargs:
            params["clOrdId"] = kwargs["client_order_id"]
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
        if 'data' not in input_data:
            return [], status
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

    def async_cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # noinspection PyBroadException
    def _query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        params = {}
        if order_id is not None:
            params["ordId"] = order_id
        if "client_order_id" in kwargs:
            params['clOrdId'] = kwargs['client_order_id']
        params['instId'] = request_symbol
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
        if 'data' not in input_data:
            return [], status
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

    def async_query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
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
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if isinstance(data, list):
            data_list = [OkxOrderData(i,
                                      extra_data['symbol_name'],
                                      extra_data['asset_type'],
                                      True)
                         for i in data]
            target_data = data_list
        elif isinstance(data, dict):
            data_list = [OkxOrderData(data,
                                      extra_data['symbol_name'],
                                      extra_data['asset_type'],
                                      True)
                         ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    # noinspection PyBroadException
    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # noinspection PyBroadException
    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_order_history(self, inst_type="SWAP", symbol=None, ord_type=None,
                           state=None, after=None, before=None, limit=None,
                           extra_data=None, **kwargs):
        """Get order history (last 7 days)"""
        request_type = "get_order_history"
        params = {'instType': inst_type}
        if symbol:
            params['instId'] = self._params.get_symbol(symbol)
        if ord_type:
            params['ordType'] = ord_type
        if state:
            params['state'] = state
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_open_orders_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_order_history(self, inst_type="SWAP", symbol=None, ord_type=None,
                          state=None, after=None, before=None, limit=None,
                          extra_data=None, **kwargs):
        path, params, extra_data = self._get_order_history(inst_type, symbol, ord_type,
                                                           state, after, before, limit,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_deals(self, symbol=None, count=100, start_time="", end_time="",
                   extra_data=None, **kwargs):
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
        else:
            request_symbol = ""
            symbol = ""
        request_type = "get_deals"
        params = {
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
        if 'data' not in input_data:
            return [], status
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

    def async_get_deals(self, symbol=None, count=100, start_time="", end_time="",
                        extra_data=None, **kwargs):
        path, params, extra_data = self._get_deals(symbol, count, start_time, end_time, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Algo Trading APIs ====================

    def _make_algo_order(self, symbol, side, ord_type, sz, extra_data=None, **kwargs):
        """Place algo order (trigger, conditional, oco, iceberg, twap, trailing)"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_algo_order"
        params = {
            'instId': request_symbol,
            'tdMode': kwargs.get('tdMode', 'cross'),
            'side': side,
            'ordType': ord_type,
            'sz': str(sz),
        }
        # Add optional algo-specific parameters
        for key in ['triggerPx', 'orderPx', 'triggerPxType',
                    'tpTriggerPx', 'tpOrdPx', 'slTriggerPx', 'slOrdPx',
                    'tpTriggerPxType', 'slTriggerPxType',
                    'ccy', 'posSide', 'clOrdId', 'reduceOnly', 'tgtCcy']:
            if key in kwargs:
                params[key] = str(kwargs[key])
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._make_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in params})
        return path, params, extra_data

    def make_algo_order(self, symbol, side, ord_type, sz, extra_data=None, **kwargs):
        path, params, extra_data = self._make_algo_order(symbol, side, ord_type, sz, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def cancel_algo_order(self, algo_id, inst_id, extra_data=None):
        """Cancel algo order"""
        path = self._params.get_rest_path("cancel_algo_order")
        params = [{"algoId": algo_id, "instId": inst_id}]
        extra_data = update_extra_data(extra_data, **{
            "request_type": "cancel_algo_order",
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_get_clear_price(self, symbol, extra_data=None, **kwargs):
        data_type = "get_clear_price"
        path = self._params.get_rest_path(data_type)
        params = {
            "instId": self._params.get_symbol(symbol)
        }
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)
