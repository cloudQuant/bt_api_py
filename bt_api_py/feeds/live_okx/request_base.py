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
from bt_api_py.feeds.capability import Capability
from bt_api_py.error_framework import OKXErrorTranslator
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitType, RateLimitScope
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
    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_FUNDING_RATE,
            Capability.GET_MARK_PRICE,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_DEALS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_POSITION,
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
            Capability.CROSS_MARGIN,
            Capability.ISOLATED_MARGIN,
            Capability.HEDGE_MODE,
            Capability.BATCH_ORDER,
            Capability.CONDITIONAL_ORDER,
            Capability.TRAILING_STOP,
            Capability.OCO_ORDER,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

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
        self._error_translator = OKXErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter():
        rules = [
            RateLimitRule(
                name="okx_general",
                limit=20,
                interval=2,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/api/v5/market/*",
            ),
            RateLimitRule(
                name="okx_trade",
                limit=60,
                interval=2,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/api/v5/trade/order",
            ),
            RateLimitRule(
                name="okx_account",
                limit=10,
                interval=2,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.ENDPOINT,
                endpoint="/api/v5/account/*",
            ),
        ]
        return RateLimiter(rules)

    def translate_error(self, raw_response):
        """将原始 OKX API 响应翻译为 UnifiedError（如有错误），否则返回 None"""
        if isinstance(raw_response, dict):
            code = raw_response.get("code", raw_response.get("sCode", "0"))
            if str(code) != "0":
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

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

    def _get_positions_history(self, inst_type=None, uly=None, inst_id=None, mgn_mode=None,
                                ccy=None, after=None, before=None, limit=None,
                                extra_data=None, **kwargs):
        """
        Get positions history
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID
        :param mgn_mode: Margin mode, cross or isolated
        :param ccy: Currency
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_positions_history"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_positions_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_positions_history_normalize_function(input_data, extra_data):
        """Normalize positions history data"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxPositionData(i,
                                         extra_data['symbol_name'],
                                         extra_data['asset_type'],
                                         True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_positions_history(self, inst_type=None, uly=None, inst_id=None, mgn_mode=None,
                              ccy=None, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """Get positions history"""
        path, params, extra_data = self._get_positions_history(inst_type, uly, inst_id,
                                                                mgn_mode, ccy, after, before,
                                                                limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_positions_history(self, inst_type=None, uly=None, inst_id=None, mgn_mode=None,
                                    ccy=None, after=None, before=None, limit=None,
                                    extra_data=None, **kwargs):
        """Async get positions history"""
        path, params, extra_data = self._get_positions_history(inst_type, uly, inst_id,
                                                                mgn_mode, ccy, after, before,
                                                                limit, extra_data, **kwargs)
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
    def _generic_normalize_function(input_data, extra_data):
        """Generic normalize function for OKX API responses.
        Extracts 'data' list and checks 'code' for status."""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if isinstance(data, list):
            return data, status
        return [data] if data else [], status

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

    def _get_fee(self, inst_type, uly=None, inst_id=None, ccy=None, qty=None,
                 extra_data=None, **kwargs):
        """
        Get fee rate
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID
        :param ccy: Currency
        :param qty: Order size
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_fee"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if qty:
            params['qty'] = qty
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_fee_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_fee_normalize_function(input_data, extra_data):
        """Normalize fee data"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxPositionData(i,
                                         extra_data['symbol_name'],
                                         extra_data['asset_type'],
                                         True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_fee(self, inst_type, uly=None, inst_id=None, ccy=None, qty=None,
                extra_data=None, **kwargs):
        """Get fee rate"""
        path, params, extra_data = self._get_fee(inst_type, uly, inst_id, ccy,
                                                  qty, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_fee(self, inst_type, uly=None, inst_id=None, ccy=None, qty=None,
                      extra_data=None, **kwargs):
        """Async get fee rate"""
        path, params, extra_data = self._get_fee(inst_type, uly, inst_id, ccy,
                                                  qty, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_max_size(self, symbol, td_mode, ccy=None, px=None,
                      extra_data=None, **kwargs):
        """
        Get maximum open position size
        :param symbol: Instrument ID, e.g. BTC-USDT
        :param td_mode: Trade mode, cross or isolated
        :param ccy: Currency (for isolated margin mode)
        :param px: Order price
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_max_size"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'tdMode': td_mode,
        }
        if ccy:
            params['ccy'] = ccy
        if px:
            params['px'] = px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_max_size_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_max_size_normalize_function(input_data, extra_data):
        """Normalize max size data"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxPositionData(i,
                                         extra_data['symbol_name'],
                                         extra_data['asset_type'],
                                         True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_max_size(self, symbol, td_mode, ccy=None, px=None,
                     extra_data=None, **kwargs):
        """Get maximum open position size"""
        path, params, extra_data = self._get_max_size(symbol, td_mode, ccy,
                                                       px, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_max_size(self, symbol, td_mode, ccy=None, px=None,
                           extra_data=None, **kwargs):
        """Async get maximum open position size"""
        path, params, extra_data = self._get_max_size(symbol, td_mode, ccy,
                                                       px, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_max_avail_size(self, symbol, td_mode, ccy=None, px=None,
                            extra_data=None, **kwargs):
        """
        Get maximum available open position size
        :param symbol: Instrument ID, e.g. BTC-USDT
        :param td_mode: Trade mode, cross or isolated
        :param ccy: Currency (for isolated margin mode)
        :param px: Order price
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_max_avail_size"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'tdMode': td_mode,
        }
        if ccy:
            params['ccy'] = ccy
        if px:
            params['px'] = px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_max_avail_size_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_max_avail_size_normalize_function(input_data, extra_data):
        """Normalize max avail size data"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxPositionData(i,
                                         extra_data['symbol_name'],
                                         extra_data['asset_type'],
                                         True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_max_avail_size(self, symbol, td_mode, ccy=None, px=None,
                           extra_data=None, **kwargs):
        """Get maximum available open position size"""
        path, params, extra_data = self._get_max_avail_size(symbol, td_mode, ccy,
                                                             px, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_max_avail_size(self, symbol, td_mode, ccy=None, px=None,
                                 extra_data=None, **kwargs):
        """Async get maximum available open position size"""
        path, params, extra_data = self._get_max_avail_size(symbol, td_mode, ccy,
                                                             px, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_margin_balance(self, symbol, pos_id, amt, mgn_mode, ccy=None,
                            extra_data=None, **kwargs):
        """
        Set margin balance (add/reduce margin)
        :param symbol: Instrument ID, e.g. BTC-USDT
        :param pos_id: Position ID
        :param amt: Amount to add/reduce
        :param mgn_mode: Margin mode, cross or isolated
        :param ccy: Currency (for isolated margin mode)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "set_margin_balance"
        request_symbol = self._params.get_symbol(symbol)
        body = {
            'instId': request_symbol,
            'posId': pos_id,
            'amt': str(amt),
            'mgnMode': mgn_mode,
        }
        if ccy:
            body['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    def set_margin_balance(self, symbol, pos_id, amt, mgn_mode, ccy=None,
                           extra_data=None, **kwargs):
        """Set margin balance (add/reduce margin)"""
        path, body, extra_data = self._set_margin_balance(symbol, pos_id, amt,
                                                          mgn_mode, ccy, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_margin_balance(self, symbol, pos_id, amt, mgn_mode, ccy=None,
                                 extra_data=None, **kwargs):
        """Async set margin balance (add/reduce margin)"""
        path, body, extra_data = self._set_margin_balance(symbol, pos_id, amt,
                                                          mgn_mode, ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
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
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_interest(inst_type, uly, inst_family, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None,
                                extra_data=None, **kwargs):
        """Async get open interest data"""
        path, params, extra_data = self._get_open_interest(inst_type, uly, inst_family, inst_id,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_premium_history(self, inst_type, uly=None, inst_id=None, after=None,
                            before=None, limit=None, extra_data=None, **kwargs):
        """
        Get premium history
        :param inst_type: Instrument type: `FUTURES`, `SWAP` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param after: Pagination (older data), request before this timestamp
        :param before: Pagination (newer data), request after this timestamp
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_premium_history"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_premium_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_premium_history_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_premium_history(self, inst_type, uly=None, inst_id=None, after=None,
                           before=None, limit=None, extra_data=None, **kwargs):
        """Get premium history"""
        path, params, extra_data = self._get_premium_history(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_premium_history(self, inst_type, uly=None, inst_id=None, after=None,
                                 before=None, limit=None, extra_data=None, **kwargs):
        """Async get premium history"""
        path, params, extra_data = self._get_premium_history(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_economic_calendar(self, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """
        Get economic calendar
        :param after: Pagination (older data), request before this timestamp
        :param before: Pagination (newer data), request after this timestamp
        :param limit: Number of results, default 20, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_economic_calendar"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_economic_calendar_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_economic_calendar_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_economic_calendar(self, after=None, before=None, limit=None,
                             extra_data=None, **kwargs):
        """Get economic calendar"""
        path, params, extra_data = self._get_economic_calendar(
            after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_economic_calendar(self, after=None, before=None, limit=None,
                                   extra_data=None, **kwargs):
        """Async get economic calendar"""
        path, params, extra_data = self._get_economic_calendar(
            after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Market Data APIs (continued) ====================

    def _get_exchange_rate(self, extra_data=None, **kwargs):
        """
        Get exchange rate
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_exchange_rate"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_exchange_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_exchange_rate_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_exchange_rate(self, extra_data=None, **kwargs):
        """Get exchange rate"""
        path, params, extra_data = self._get_exchange_rate(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_exchange_rate(self, extra_data=None, **kwargs):
        """Async get exchange rate"""
        path, params, extra_data = self._get_exchange_rate(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_index_components(self, index, extra_data=None, **kwargs):
        """
        Get index components
        :param index: Index name, e.g. "BTC-USD"
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_components"
        params = {'index': index}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": index,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_index_components_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_index_components_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return {}, status
        data = input_data['data']
        if len(data) > 0:
            # The API returns a dict with 'components', 'index', 'last', 'ts' keys
            target_data = data[0] if isinstance(data, list) else data
        else:
            target_data = {}
        return target_data, status

    def get_index_components(self, index, extra_data=None, **kwargs):
        """Get index components"""
        path, params, extra_data = self._get_index_components(index, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_components(self, index, extra_data=None, **kwargs):
        """Async get index components"""
        path, params, extra_data = self._get_index_components(index, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

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
            "normalize_function": OkxRequestData._generic_normalize_function,
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

    # ==================== Option Instrument Family Trades ====================

    def _get_option_instrument_family_trades(self, inst_family, uly=None, limit=None,
                                              extra_data=None, **kwargs):
        """
        Get option instrument family trades data
        :param inst_family: Instrument family, e.g. `BTC-USD`
        :param uly: Underlying index
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_instrument_family_trades"
        params = {'instFamily': inst_family}
        if uly:
            params['uly'] = uly
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_family,
            "asset_type": "OPTION",
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_option_instrument_family_trades(self, inst_family, uly=None, limit=None,
                                              extra_data=None, **kwargs):
        """Get option instrument family trades data"""
        path, params, extra_data = self._get_option_instrument_family_trades(
            inst_family, uly, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_instrument_family_trades(self, inst_family, uly=None, limit=None,
                                                    extra_data=None, **kwargs):
        """Async get option instrument family trades data"""
        path, params, extra_data = self._get_option_instrument_family_trades(
            inst_family, uly, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Option Trades ====================

    def _get_option_trades(self, inst_id, limit=None, extra_data=None, **kwargs):
        """
        Get option trades data
        :param inst_id: Instrument ID, e.g. `BTC-USD-231229-40000-C`
        :param limit: Default 100, max 500
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_trades"
        params = {'instId': inst_id}
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": "OPTION",
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_option_trades(self, inst_id, limit=None, extra_data=None, **kwargs):
        """Get option trades data"""
        path, params, extra_data = self._get_option_trades(inst_id, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_trades(self, inst_id, limit=None, extra_data=None, **kwargs):
        """Async get option trades data"""
        path, params, extra_data = self._get_option_trades(inst_id, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== 24h Volume ====================

    def _get_24h_volume(self, extra_data=None, **kwargs):
        """
        Get platform 24h total volume
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_24h_volume"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_24h_volume(self, extra_data=None, **kwargs):
        """Get platform 24h total volume"""
        path, params, extra_data = self._get_24h_volume(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_24h_volume(self, extra_data=None, **kwargs):
        """Async get platform 24h total volume"""
        path, params, extra_data = self._get_24h_volume(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Call Auction Details ====================

    def _get_call_auction_details(self, inst_type=None, uly=None, inst_id=None,
                                   extra_data=None, **kwargs):
        """
        Get call auction details
        :param inst_type: Instrument type: `FUTURES`, `OPTION`
        :param uly: Underlying, required for `FUTURES`/`OPTION`
        :param inst_id: Instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_call_auction_details"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_call_auction_details(self, inst_type=None, uly=None, inst_id=None,
                                   extra_data=None, **kwargs):
        """Get call auction details"""
        path, params, extra_data = self._get_call_auction_details(
            inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_call_auction_details(self, inst_type=None, uly=None, inst_id=None,
                                        extra_data=None, **kwargs):
        """Async get call auction details"""
        path, params, extra_data = self._get_call_auction_details(
            inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Index Price ====================

    def _get_index_price(self, index=None, extra_data=None, **kwargs):
        """
        Get index ticker data
        :param index: Index, e.g. `BTC-USD`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_price"
        params = {}
        if index:
            params['instId'] = index
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": index or "ALL",
            "asset_type": "INDEX",
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_index_price(self, index=None, extra_data=None, **kwargs):
        """Get index ticker data"""
        path, params, extra_data = self._get_index_price(index, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_price(self, index=None, extra_data=None, **kwargs):
        """Async get index ticker data"""
        path, params, extra_data = self._get_index_price(index, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Index Candles ====================

    def _get_index_candles(self, index, bar='1m', after='', before='', limit='100',
                           extra_data=None, **kwargs):
        """
        Get index candlestick charts
        :param index: Index, e.g. `BTC-USD`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_candles"
        params = {
            'instId': index,
            'bar': self._params.get_period(bar),
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": index,
            "asset_type": "INDEX",
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_index_candles_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_index_candles_normalize_function(input_data, extra_data):
        """Normalize index candles data - API returns 6 elements, OkxBarData expects 9"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = sorted(input_data['data'], key=lambda x: x[0])
        if len(data) > 0:
            # Pad data from 6-7 elements to 9 elements for OkxBarData
            # API format: [ts, o, h, l, c, vol] or [ts, o, h, l, c, vol, vol_ccy]
            # OkxBarData expects: [ts, o, h, l, c, vol, base_vol, quote_vol, confirm]
            padded_data = []
            for row in data:
                padded = list(row)
                # Ensure we have at least 6 elements
                while len(padded) < 6:
                    padded.append('0')
                # Add missing elements to reach 9
                # Index 6: base_asset_volume (use vol if not present)
                if len(padded) < 7:
                    padded.append(padded[5])  # vol as base_vol
                else:
                    padded.append(padded[5])  # vol as base_vol
                # Index 7: quote_asset_volume (use vol_ccy or '0')
                if len(padded) < 8:
                    padded.append('0')  # no quote_vol for index
                # Index 8: confirm status
                if len(padded) < 9:
                    padded.append('1')
                padded_data.append(padded)

            data_list = [OkxBarData(i,
                                    extra_data['symbol_name'],
                                    extra_data['asset_type'],
                                    True)
                         for i in padded_data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_index_candles(self, index, bar='1m', after='', before='', limit='100',
                          extra_data=None, **kwargs):
        """Get index candlestick charts"""
        path, params, extra_data = self._get_index_candles(
            index, bar, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_candles(self, index, bar='1m', after='', before='', limit='100',
                                extra_data=None, **kwargs):
        """Async get index candlestick charts"""
        path, params, extra_data = self._get_index_candles(
            index, bar, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Mark Price Candles ====================

    def _get_mark_price_candles(self, symbol, bar='1m', after='', before='', limit='100',
                                 extra_data=None, **kwargs):
        """
        Get mark price candlestick charts
        :param symbol: Instrument ID, e.g. `BTC-USDT`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_mark_price_candles"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'bar': self._params.get_period(bar),
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_mark_price_candles_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_mark_price_candles_normalize_function(input_data, extra_data):
        """Normalize mark price candles data - API returns 6-7 elements, OkxBarData expects 9"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = sorted(input_data['data'], key=lambda x: x[0])
        if len(data) > 0:
            # Pad data from 6-7 elements to 9 elements for OkxBarData
            # API format: [ts, o, h, l, c, vol] or [ts, o, h, l, c, vol, vol_ccy]
            # OkxBarData expects: [ts, o, h, l, c, vol, base_vol, quote_vol, confirm]
            padded_data = []
            for row in data:
                padded = list(row)
                # Ensure we have at least 6 elements
                while len(padded) < 6:
                    padded.append('0')
                # Add missing elements to reach 9
                # Index 6: base_asset_volume (use vol if not present)
                if len(padded) < 7:
                    padded.append(padded[5])  # vol as base_vol
                else:
                    padded.append(padded[5])  # vol as base_vol
                # Index 7: quote_asset_volume (use vol_ccy or '0')
                if len(padded) < 8:
                    padded.append('0')  # no quote_vol
                # Index 8: confirm status
                if len(padded) < 9:
                    padded.append('1')
                padded_data.append(padded)

            data_list = [OkxBarData(i,
                                    extra_data['symbol_name'],
                                    extra_data['asset_type'],
                                    True)
                         for i in padded_data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_mark_price_candles(self, symbol, bar='1m', after='', before='', limit='100',
                               extra_data=None, **kwargs):
        """Get mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles(
            symbol, bar, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_mark_price_candles(self, symbol, bar='1m', after='', before='', limit='100',
                                     extra_data=None, **kwargs):
        """Async get mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles(
            symbol, bar, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Index Candles History ====================

    def _get_index_candles_history(self, index, bar='1m', after='', before='', limit='100',
                                    extra_data=None, **kwargs):
        """
        Get historical index candlestick charts
        :param index: Index, e.g. `BTC-USD`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_candles_history"
        params = {
            'instId': index,
            'bar': self._params.get_period(bar),
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": index,
            "asset_type": "INDEX",
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_index_candles_history(self, index, bar='1m', after='', before='', limit='100',
                                   extra_data=None, **kwargs):
        """Get historical index candlestick charts"""
        path, params, extra_data = self._get_index_candles_history(
            index, bar, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_candles_history(self, index, bar='1m', after='', before='', limit='100',
                                         extra_data=None, **kwargs):
        """Async get historical index candlestick charts"""
        path, params, extra_data = self._get_index_candles_history(
            index, bar, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Mark Price Candles History ====================

    def _get_mark_price_candles_history(self, symbol, bar='1m', after='', before='', limit='100',
                                         extra_data=None, **kwargs):
        """
        Get historical mark price candlestick charts
        :param symbol: Instrument ID, e.g. `BTC-USD-SWAP`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_mark_price_candles_history"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'bar': self._params.get_period(bar),
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_mark_price_candles_history(self, symbol, bar='1m', after='', before='', limit='100',
                                        extra_data=None, **kwargs):
        """Get historical mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles_history(
            symbol, bar, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_mark_price_candles_history(self, symbol, bar='1m', after='', before='', limit='100',
                                              extra_data=None, **kwargs):
        """Async get historical mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles_history(
            symbol, bar, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)


    # ==================== Trading Statistics APIs ====================

    def _get_taker_volume_contract(self, ccy=None, inst_type=None, begin=None, end=None,
                                   period=None, limit=None, extra_data=None, **kwargs):
        """
        Get contract active buy/sell volume (taker volume)
        :param ccy: Currency, e.g. "BTC"
        :param inst_type: Instrument type: `SWAP`, `FUTURES`, `OPTION`
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `5m`, `1H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_taker_volume_contract"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if inst_type:
            params['instType'] = inst_type
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_taker_volume_contract_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_taker_volume_contract_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_taker_volume_contract(self, ccy=None, inst_type=None, begin=None, end=None,
                                   period=None, limit=None, extra_data=None, **kwargs):
        """Get contract active buy/sell volume (taker volume)"""
        path, params, extra_data = self._get_taker_volume_contract(
            ccy, inst_type, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_taker_volume_contract(self, ccy=None, inst_type=None, begin=None, end=None,
                                        period=None, limit=None, extra_data=None, **kwargs):
        """Async get contract active buy/sell volume (taker volume)"""
        path, params, extra_data = self._get_taker_volume_contract(
            ccy, inst_type, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_margin_loan_ratio(self, ccy=None, begin=None, end=None, period=None,
                               limit=None, extra_data=None, **kwargs):
        """
        Get margin loan ratio (spot long/short ratio)
        :param ccy: Currency, e.g. "BTC"
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `5m`, `1H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_margin_loan_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_margin_loan_ratio_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_margin_loan_ratio_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_margin_loan_ratio(self, ccy=None, begin=None, end=None, period=None,
                              limit=None, extra_data=None, **kwargs):
        """Get margin loan ratio (spot long/short ratio)"""
        path, params, extra_data = self._get_margin_loan_ratio(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_margin_loan_ratio(self, ccy=None, begin=None, end=None, period=None,
                                    limit=None, extra_data=None, **kwargs):
        """Async get margin loan ratio (spot long/short ratio)"""
        path, params, extra_data = self._get_margin_loan_ratio(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_long_short_ratio(self, ccy=None, currency=None, begin=None, end=None,
                                      period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option long/short ratio
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_long_short_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_option_long_short_ratio_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_long_short_ratio_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_long_short_ratio(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """Get option long/short ratio"""
        path, params, extra_data = self._get_option_long_short_ratio(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_long_short_ratio(self, ccy=None, currency=None, begin=None, end=None,
                                          period=None, limit=None, extra_data=None, **kwargs):
        """Async get option long/short ratio"""
        path, params, extra_data = self._get_option_long_short_ratio(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_contracts_oi_volume(self, ccy=None, begin=None, end=None, period=None,
                                  limit=None, extra_data=None, **kwargs):
        """
        Get contract open interest and volume
        :param ccy: Currency, e.g. "BTC"
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `5m`, `1H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_contracts_oi_volume"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_contracts_oi_volume_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_contracts_oi_volume_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_contracts_oi_volume(self, ccy=None, begin=None, end=None, period=None,
                                 limit=None, extra_data=None, **kwargs):
        """Get contract open interest and volume"""
        path, params, extra_data = self._get_contracts_oi_volume(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_contracts_oi_volume(self, ccy=None, begin=None, end=None, period=None,
                                      limit=None, extra_data=None, **kwargs):
        """Async get contract open interest and volume"""
        path, params, extra_data = self._get_contracts_oi_volume(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_oi_volume(self, ccy=None, currency=None, begin=None, end=None,
                               period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option open interest and volume
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_oi_volume"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_option_oi_volume_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_oi_volume_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_oi_volume(self, ccy=None, currency=None, begin=None, end=None,
                              period=None, limit=None, extra_data=None, **kwargs):
        """Get option open interest and volume"""
        path, params, extra_data = self._get_option_oi_volume(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_oi_volume(self, ccy=None, currency=None, begin=None, end=None,
                                    period=None, limit=None, extra_data=None, **kwargs):
        """Async get option open interest and volume"""
        path, params, extra_data = self._get_option_oi_volume(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_oi_volume_expiry(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option open interest and volume by expiry
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_oi_volume_expiry"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_option_oi_volume_expiry_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_oi_volume_expiry_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_oi_volume_expiry(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """Get option open interest and volume by expiry"""
        path, params, extra_data = self._get_option_oi_volume_expiry(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_oi_volume_expiry(self, ccy=None, currency=None, begin=None, end=None,
                                          period=None, limit=None, extra_data=None, **kwargs):
        """Async get option open interest and volume by expiry"""
        path, params, extra_data = self._get_option_oi_volume_expiry(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_oi_volume_strike(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option open interest and volume by strike price
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_oi_volume_strike"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_option_oi_volume_strike_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_oi_volume_strike_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_oi_volume_strike(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """Get option open interest and volume by strike price"""
        path, params, extra_data = self._get_option_oi_volume_strike(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_oi_volume_strike(self, ccy=None, currency=None, begin=None, end=None,
                                          period=None, limit=None, extra_data=None, **kwargs):
        """Async get option open interest and volume by strike price"""
        path, params, extra_data = self._get_option_oi_volume_strike(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_taker_flow(self, ccy=None, currency=None, begin=None, end=None,
                               period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option taker block volume (large trades)
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_taker_flow"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_option_taker_flow_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_taker_flow_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_taker_flow(self, ccy=None, currency=None, begin=None, end=None,
                              period=None, limit=None, extra_data=None, **kwargs):
        """Get option taker block volume (large trades)"""
        path, params, extra_data = self._get_option_taker_flow(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_taker_flow(self, ccy=None, currency=None, begin=None, end=None,
                                    period=None, limit=None, extra_data=None, **kwargs):
        """Async get option taker block volume (large trades)"""
        path, params, extra_data = self._get_option_taker_flow(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Trading Account APIs ====================

    def _get_interest_limits(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                             inst_family=None, extra_data=None, **kwargs):
        """
        Get interest limit and interest rate
        :param ccy: Currency
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param mgn_mode: Margin mode: `cross`, `isolated`
        :param uly: Underlying
        :param inst_family: Instrument family
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_interest_limits"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if inst_type:
            params['instType'] = inst_type
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_interest_limits_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_interest_limits_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_interest_limits(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                           inst_family=None, extra_data=None, **kwargs):
        """Get interest limit and interest rate"""
        path, params, extra_data = self._get_interest_limits(ccy, inst_type, mgn_mode, uly,
                                                             inst_family, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_interest_limits(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                                 inst_family=None, extra_data=None, **kwargs):
        """Async get interest limit and interest rate"""
        path, params, extra_data = self._get_interest_limits(ccy, inst_type, mgn_mode, uly,
                                                             inst_family, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_fee_type(self, fee_type, extra_data=None, **kwargs):
        """
        Set fee rate tier
        :param fee_type: Fee rate tier, default is 1, 2, 3, 4, 5
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "set_fee_type"
        body = {
            'feeType': str(fee_type),
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_fee_type_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _set_fee_type_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_fee_type(self, fee_type, extra_data=None, **kwargs):
        """Set fee rate tier"""
        path, body, extra_data = self._set_fee_type(fee_type, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_fee_type(self, fee_type, extra_data=None, **kwargs):
        """Async set fee rate tier"""
        path, body, extra_data = self._set_fee_type(fee_type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_greeks(self, greeks_type, extra_data=None, **kwargs):
        """
        Set Greeks display type
        :param greeks_type: Greeks display type: `PA` PA price, `IV` IV
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "set_greeks"
        body = {
            'greeksType': greeks_type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_greeks_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _set_greeks_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_greeks(self, greeks_type, extra_data=None, **kwargs):
        """Set Greeks display type"""
        path, body, extra_data = self._set_greeks(greeks_type, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_greeks(self, greeks_type, extra_data=None, **kwargs):
        """Async set Greeks display type"""
        path, body, extra_data = self._set_greeks(greeks_type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_isolated_mode(self, symbol, iso_mode, extra_data=None, **kwargs):
        """
        Set isolated margin mode
        :param symbol: Instrument ID, e.g. "BTC-USDT"
        :param iso_mode: Isolated margin mode: `automatic`, `non-automatic`, `autonomy`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "set_isolated_mode"
        request_symbol = self._params.get_symbol(symbol)
        body = {
            'instId': request_symbol,
            'isoMode': iso_mode,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_isolated_mode_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _set_isolated_mode_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_isolated_mode(self, symbol, iso_mode, extra_data=None, **kwargs):
        """Set isolated margin mode"""
        path, body, extra_data = self._set_isolated_mode(symbol, iso_mode, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_isolated_mode(self, symbol, iso_mode, extra_data=None, **kwargs):
        """Async set isolated margin mode"""
        path, body, extra_data = self._set_isolated_mode(symbol, iso_mode, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _borrow_repay(self, ccy, side, amt, mgn_mode=None, symbol=None, auto=None,
                     extra_data=None, **kwargs):
        """
        Manual borrow or repay for cross/isolated margin
        :param ccy: Currency, e.g. `BTC`
        :param side: Side: `borrow`, `repay`
        :param amt: The amount to borrow or repay
        :param mgn_mode: Margin mode: `cross`, `isolated`
        :param symbol: Instrument ID, required for isolated margin
        :param auto: Auto loan repayment: `true`, `false`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "borrow_repay"
        body = {
            'ccy': ccy,
            'side': side,
            'amt': str(amt),
        }
        if mgn_mode:
            body['mgnMode'] = mgn_mode
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            body['instId'] = request_symbol
        if auto is not None:
            body['auto'] = auto
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._borrow_repay_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _borrow_repay_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def borrow_repay(self, ccy, side, amt, mgn_mode=None, symbol=None, auto=None,
                    extra_data=None, **kwargs):
        """Manual borrow or repay for cross/isolated margin"""
        path, body, extra_data = self._borrow_repay(ccy, side, amt, mgn_mode, symbol,
                                                    auto, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_borrow_repay(self, ccy, side, amt, mgn_mode=None, symbol=None, auto=None,
                          extra_data=None, **kwargs):
        """Async manual borrow or repay for cross/isolated margin"""
        path, body, extra_data = self._borrow_repay(ccy, side, amt, mgn_mode, symbol,
                                                    auto, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_auto_repay(self, auto_repay, extra_data=None, **kwargs):
        """
        Set auto loan repayment
        :param auto_repay: Auto loan repayment: `true`, `false`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "set_auto_repay"
        body = {
            'autoRepay': auto_repay,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_auto_repay_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _set_auto_repay_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_auto_repay(self, auto_repay, extra_data=None, **kwargs):
        """Set auto loan repayment"""
        path, body, extra_data = self._set_auto_repay(auto_repay, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_auto_repay(self, auto_repay, extra_data=None, **kwargs):
        """Async set auto loan repayment"""
        path, body, extra_data = self._set_auto_repay(auto_repay, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_borrow_repay_history(self, ccy=None, mgn_mode=None, after=None, before=None,
                                  limit=None, extra_data=None, **kwargs):
        """
        Get borrowing and repayment history (last 3 months)
        :param ccy: Currency, e.g. `BTC`
        :param mgn_mode: Margin mode: `cross`, `isolated`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_borrow_repay_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_borrow_repay_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_borrow_repay_history_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_borrow_repay_history(self, ccy=None, mgn_mode=None, after=None, before=None,
                                limit=None, extra_data=None, **kwargs):
        """Get borrowing and repayment history (last 3 months)"""
        path, params, extra_data = self._get_borrow_repay_history(ccy, mgn_mode, after, before,
                                                                   limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_borrow_repay_history(self, ccy=None, mgn_mode=None, after=None, before=None,
                                      limit=None, extra_data=None, **kwargs):
        """Async get borrowing and repayment history (last 3 months)"""
        path, params, extra_data = self._get_borrow_repay_history(ccy, mgn_mode, after, before,
                                                                   limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== MMP (Market Maker Protection) APIs ====================

    def _mmp_reset(self, inst_type, symbol=None, extra_data=None, **kwargs):
        """
        Reset MMP (Market Maker Protection) status
        :param inst_type: Instrument type, e.g. `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param symbol: Instrument ID (optional)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "mmp_reset"
        params = {
            'instType': inst_type,
        }
        if symbol:
            params['instId'] = symbol
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._mmp_reset_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _mmp_reset_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def mmp_reset(self, inst_type, symbol=None, extra_data=None, **kwargs):
        """Reset MMP (Market Maker Protection) status"""
        path, params, extra_data = self._mmp_reset(inst_type, symbol, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_mmp_reset(self, inst_type, symbol=None, extra_data=None, **kwargs):
        """Async reset MMP (Market Maker Protection) status"""
        path, params, extra_data = self._mmp_reset(inst_type, symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_mmp_config(self, inst_type, symbol=None, time_interval_frozen=None,
                        algo_orders_frozen=None, extra_data=None, **kwargs):
        """
        Set MMP (Market Maker Protection) configuration
        :param inst_type: Instrument type, e.g. `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param symbol: Instrument ID (optional)
        :param time_interval_frozen: Frozen period in milliseconds after triggered
        :param algo_orders_frozen: Whether to freeze algo orders: `true` or `false`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "set_mmp_config"
        params = {
            'instType': inst_type,
        }
        if symbol:
            params['instId'] = symbol
        if time_interval_frozen is not None:
            params['timeIntervalFrozen'] = time_interval_frozen
        if algo_orders_frozen is not None:
            params['algoOrdersFrozen'] = str(algo_orders_frozen).lower()
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_mmp_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_mmp_config_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def set_mmp_config(self, inst_type, symbol=None, time_interval_frozen=None,
                       algo_orders_frozen=None, extra_data=None, **kwargs):
        """Set MMP (Market Maker Protection) configuration"""
        path, params, extra_data = self._set_mmp_config(inst_type, symbol, time_interval_frozen,
                                                         algo_orders_frozen, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_mmp_config(self, inst_type, symbol=None, time_interval_frozen=None,
                             algo_orders_frozen=None, extra_data=None, **kwargs):
        """Async set MMP (Market Maker Protection) configuration"""
        path, params, extra_data = self._set_mmp_config(inst_type, symbol, time_interval_frozen,
                                                         algo_orders_frozen, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_mmp_config(self, inst_type, extra_data=None, **kwargs):
        """
        Get MMP (Market Maker Protection) configuration
        :param inst_type: Instrument type, e.g. `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_mmp_config"
        params = {
            'instType': inst_type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_mmp_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_mmp_config_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_mmp_config(self, inst_type, extra_data=None, **kwargs):
        """Get MMP (Market Maker Protection) configuration"""
        path, params, extra_data = self._get_mmp_config(inst_type, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_mmp_config(self, inst_type, extra_data=None, **kwargs):
        """Async get MMP (Market Maker Protection) configuration"""
        path, params, extra_data = self._get_mmp_config(inst_type, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Bills History Archive APIs ====================

    def _apply_bills_history_archive(self, year, ccy=None, after=None, before=None,
                                     extra_data=None, **kwargs):
        """
        Apply for historical bills archive (from 2021)
        :param year: Year, e.g. `2023`, `2024`
        :param ccy: Currency (optional)
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "apply_bills_history_archive"
        params = {
            'year': str(year),
        }
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._apply_bills_history_archive_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _apply_bills_history_archive_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def apply_bills_history_archive(self, year, ccy=None, after=None, before=None,
                                     extra_data=None, **kwargs):
        """Apply for historical bills archive (from 2021)"""
        path, params, extra_data = self._apply_bills_history_archive(year, ccy, after, before,
                                                                      extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_apply_bills_history_archive(self, year, ccy=None, after=None, before=None,
                                          extra_data=None, **kwargs):
        """Async apply for historical bills archive (from 2021)"""
        path, params, extra_data = self._apply_bills_history_archive(year, ccy, after, before,
                                                                      extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_bills_history_archive(self, year, ccy=None, after=None, before=None,
                                    extra_data=None, **kwargs):
        """
        Get historical bills archive (from 2021)
        :param year: Year, e.g. `2023`, `2024`
        :param ccy: Currency (optional)
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_bills_history_archive"
        params = {
            'year': str(year),
        }
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_bills_history_archive_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_bills_history_archive_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_bills_history_archive(self, year, ccy=None, after=None, before=None,
                                   extra_data=None, **kwargs):
        """Get historical bills archive (from 2021)"""
        path, params, extra_data = self._get_bills_history_archive(year, ccy, after, before,
                                                                    extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_bills_history_archive(self, year, ccy=None, after=None, before=None,
                                        extra_data=None, **kwargs):
        """Async get historical bills archive (from 2021)"""
        path, params, extra_data = self._get_bills_history_archive(year, ccy, after, before,
                                                                    extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Trading Account Configuration APIs ====================

    def _set_auto_loan(self, auto_loan, ccy=None, iso_mode=None, mgn_mode=None,
                       extra_data=None, **kwargs):
        """
        Set auto loan status
        :param auto_loan: Auto loan status: `true` for on, `false` for off
        :param ccy: Currency, required for isolated margin
        :param iso_mode: Isolated margin mode: `automatic`, `autonomy`, `manual`
        :param mgn_mode: Margin mode: `cross`, `isolated`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body (params), extra_data
        """
        request_type = "set_auto_loan"
        body = {
            'autoLoan': str(auto_loan).lower()
        }
        if ccy:
            body['ccy'] = ccy
        if iso_mode:
            body['isoMode'] = iso_mode
        if mgn_mode:
            body['mgnMode'] = mgn_mode
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_auto_loan_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in body})
        return path, body, extra_data

    @staticmethod
    def _set_auto_loan_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_auto_loan(self, auto_loan, ccy=None, iso_mode=None, mgn_mode=None,
                      extra_data=None, **kwargs):
        """Set auto loan status"""
        path, body, extra_data = self._set_auto_loan(auto_loan, ccy, iso_mode, mgn_mode,
                                                      extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_auto_loan(self, auto_loan, ccy=None, iso_mode=None, mgn_mode=None,
                            extra_data=None, **kwargs):
        """Async set auto loan status"""
        path, body, extra_data = self._set_auto_loan(auto_loan, ccy, iso_mode, mgn_mode,
                                                      extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_account_level(self, acct_lv, inst_type=None, inst_id=None, ccy=None,
                          td_mode=None, pos_side=None, uly=None,
                          extra_data=None, **kwargs):
        """
        Set account level
        :param acct_lv: Account level: `1` Simple mode, `2` Single-currency margin, `3` Multi-currency margin, `4` Portfolio margin
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param inst_id: Instrument ID
        :param ccy: Currency
        :param td_mode: Trade mode: `cross`, `isolated`, `cash`
        :param pos_side: Position side: `long`, `short`, `net`
        :param uly: Underlying
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body (params), extra_data
        """
        request_type = "set_account_level"
        body = {
            'acctLv': str(acct_lv)
        }
        if inst_type:
            body['instType'] = inst_type
        if inst_id:
            request_inst_id = self._params.get_symbol(inst_id)
            body['instId'] = request_inst_id
        if ccy:
            body['ccy'] = ccy
        if td_mode:
            body['tdMode'] = td_mode
        if pos_side:
            body['posSide'] = pos_side
        if uly:
            body['uly'] = uly
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_account_level_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in body})
        return path, body, extra_data

    @staticmethod
    def _set_account_level_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_account_level(self, acct_lv, inst_type=None, inst_id=None, ccy=None,
                          td_mode=None, pos_side=None, uly=None,
                          extra_data=None, **kwargs):
        """Set account level"""
        path, body, extra_data = self._set_account_level(acct_lv, inst_type, inst_id, ccy,
                                                          td_mode, pos_side, uly,
                                                          extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_account_level(self, acct_lv, inst_type=None, inst_id=None, ccy=None,
                                td_mode=None, pos_side=None, uly=None,
                                extra_data=None, **kwargs):
        """Async set account level"""
        path, body, extra_data = self._set_account_level(acct_lv, inst_type, inst_id, ccy,
                                                          td_mode, pos_side, uly,
                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _account_level_switch_preset(self, acct_lv, pos_side=None, ccy_list=None,
                                     uly=None, inst_type=None, extra_data=None, **kwargs):
        """
        Account level switch preset
        :param acct_lv: Target account level: `2` Single-currency margin, `3` Multi-currency margin, `4` Portfolio margin
        :param pos_side: Position side: `long`, `short`, `net`
        :param ccy_list: Currency list, comma-separated, e.g. "BTC,USDT"
        :param uly: Underlying
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body (params), extra_data
        """
        request_type = "account_level_switch_preset"
        body = {
            'acctLv': str(acct_lv)
        }
        if pos_side:
            body['posSide'] = pos_side
        if ccy_list:
            body['ccyList'] = ccy_list
        if uly:
            body['uly'] = uly
        if inst_type:
            body['instType'] = inst_type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._account_level_switch_preset_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in body})
        return path, body, extra_data

    @staticmethod
    def _account_level_switch_preset_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def account_level_switch_preset(self, acct_lv, pos_side=None, ccy_list=None,
                                    uly=None, inst_type=None, extra_data=None, **kwargs):
        """Account level switch preset"""
        path, body, extra_data = self._account_level_switch_preset(acct_lv, pos_side, ccy_list,
                                                                    uly, inst_type,
                                                                    extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_account_level_switch_preset(self, acct_lv, pos_side=None, ccy_list=None,
                                          uly=None, inst_type=None, extra_data=None, **kwargs):
        """Async account level switch preset"""
        path, body, extra_data = self._account_level_switch_preset(acct_lv, pos_side, ccy_list,
                                                                    uly, inst_type,
                                                                    extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _account_level_switch_precheck(self, acct_lv, inst_type=None, uly=None,
                                       extra_data=None, **kwargs):
        """
        Account level switch precheck
        :param acct_lv: Target account level: `2` Single-currency margin, `3` Multi-currency margin, `4` Portfolio margin
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param uly: Underlying
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "account_level_switch_precheck"
        params = {
            'acctLv': str(acct_lv)
        }
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._account_level_switch_precheck_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in params})
        return path, params, extra_data

    @staticmethod
    def _account_level_switch_precheck_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def account_level_switch_precheck(self, acct_lv, inst_type=None, uly=None,
                                      extra_data=None, **kwargs):
        """Account level switch precheck"""
        path, params, extra_data = self._account_level_switch_precheck(acct_lv, inst_type, uly,
                                                                        extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_account_level_switch_precheck(self, acct_lv, inst_type=None, uly=None,
                                            extra_data=None, **kwargs):
        """Async account level switch precheck"""
        path, params, extra_data = self._account_level_switch_precheck(acct_lv, inst_type, uly,
                                                                        extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_collateral_assets(self, ccy_list, auto_loan=None,
                                extra_data=None, **kwargs):
        """
        Set collateral assets
        :param ccy_list: Currency list, comma-separated, e.g. "BTC,USDT,ETH"
        :param auto_loan: Auto loan status: `true` for on, `false` for off
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body (params), extra_data
        """
        request_type = "set_collateral_assets"
        body = {}
        if ccy_list:
            body['ccy'] = ccy_list
        if auto_loan is not None:
            body['autoLoan'] = str(auto_loan).lower()
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy_list or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_collateral_assets_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in body})
        return path, body, extra_data

    @staticmethod
    def _set_collateral_assets_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_collateral_assets(self, ccy_list, auto_loan=None,
                              extra_data=None, **kwargs):
        """Set collateral assets"""
        path, body, extra_data = self._set_collateral_assets(ccy_list, auto_loan,
                                                              extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_collateral_assets(self, ccy_list, auto_loan=None,
                                    extra_data=None, **kwargs):
        """Async set collateral assets"""
        path, body, extra_data = self._set_collateral_assets(ccy_list, auto_loan,
                                                              extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_collateral_assets(self, ccy=None, extra_data=None, **kwargs):
        """
        Get collateral assets
        :param ccy: Currency, e.g. `BTC`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_collateral_assets"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_collateral_assets_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in params})
        return path, params, extra_data

    @staticmethod
    def _get_collateral_assets_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_collateral_assets(self, ccy=None, extra_data=None, **kwargs):
        """Get collateral assets"""
        path, params, extra_data = self._get_collateral_assets(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_collateral_assets(self, ccy=None, extra_data=None, **kwargs):
        """Async get collateral assets"""
        path, params, extra_data = self._get_collateral_assets(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_risk_offset_amt(self, amt_type, uly=None, ccy=None, inst_type=None,
                             offset_amt=None, inst_id=None, td_mode=None, pos_side=None,
                             extra_data=None, **kwargs):
        """
        Set risk offset amount
        :param amt_type: Offset amount type: `1` Add, `2` Reduce
        :param uly: Underlying
        :param ccy: Currency
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
        :param offset_amt: Offset amount
        :param inst_id: Instrument ID
        :param td_mode: Trade mode: `cross`, `isolated`, `cash`
        :param pos_side: Position side: `long`, `short`, `net`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body (params), extra_data
        """
        request_type = "set_risk_offset_amt"
        body = {
            'amtType': str(amt_type)
        }
        if uly:
            body['uly'] = uly
        if ccy:
            body['ccy'] = ccy
        if inst_type:
            body['instType'] = inst_type
        if offset_amt:
            body['offsetAmt'] = str(offset_amt)
        if inst_id:
            request_inst_id = self._params.get_symbol(inst_id)
            body['instId'] = request_inst_id
        if td_mode:
            body['tdMode'] = td_mode
        if pos_side:
            body['posSide'] = pos_side
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_risk_offset_amt_normalize_function,
        })
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in body})
        return path, body, extra_data

    @staticmethod
    def _set_risk_offset_amt_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def set_risk_offset_amt(self, amt_type, uly=None, ccy=None, inst_type=None,
                            offset_amt=None, inst_id=None, td_mode=None, pos_side=None,
                            extra_data=None, **kwargs):
        """Set risk offset amount"""
        path, body, extra_data = self._set_risk_offset_amt(amt_type, uly, ccy, inst_type,
                                                            offset_amt, inst_id, td_mode, pos_side,
                                                            extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_risk_offset_amt(self, amt_type, uly=None, ccy=None, inst_type=None,
                                  offset_amt=None, inst_id=None, td_mode=None, pos_side=None,
                                  extra_data=None, **kwargs):
        """Async set risk offset amount"""
        path, body, extra_data = self._set_risk_offset_amt(amt_type, uly, ccy, inst_type,
                                                            offset_amt, inst_id, td_mode, pos_side,
                                                            extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Additional Trading Account APIs ====================

    def _activate_option(self, uly, inst_id=None, cnt=None, amend_px_on=None,
                         extra_data=None, **kwargs):
        """
        Activate option trading
        :param uly: Underlying, e.g. `BTC-USD`
        :param inst_id: Instrument ID
        :param cnt: Exercise quantity
        :param amend_px_on: Whether to modify exercise price: `true` or `false`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "activate_option"
        params = {
            'uly': uly,
        }
        if inst_id:
            params['instId'] = inst_id
        if cnt:
            params['cnt'] = cnt
        if amend_px_on:
            params['amendPxOn'] = amend_px_on
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._activate_option_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _activate_option_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def activate_option(self, uly, inst_id=None, cnt=None, amend_px_on=None,
                        extra_data=None, **kwargs):
        """Activate option trading"""
        path, params, extra_data = self._activate_option(uly, inst_id, cnt, amend_px_on,
                                                          extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_activate_option(self, uly, inst_id=None, cnt=None, amend_px_on=None,
                              extra_data=None, **kwargs):
        """Async activate option trading"""
        path, params, extra_data = self._activate_option(uly, inst_id, cnt, amend_px_on,
                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _move_positions(self, symbol, pos_id, ccy, algo_id=None,
                        extra_data=None, **kwargs):
        """
        Move positions between currencies
        :param symbol: Instrument ID
        :param pos_id: Position ID
        :param ccy: Currency to move to
        :param algo_id: Algo order ID (for pending algo orders)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "move_positions"
        request_symbol = self._params.get_symbol(symbol)
        params = [
            {
                'instId': request_symbol,
                'posId': pos_id,
                'ccy': ccy,
            }
        ]
        if algo_id:
            params[0]['algoId'] = algo_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._move_positions_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _move_positions_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def move_positions(self, symbol, pos_id, ccy, algo_id=None,
                       extra_data=None, **kwargs):
        """Move positions between currencies"""
        path, params, extra_data = self._move_positions(symbol, pos_id, ccy, algo_id,
                                                         extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_move_positions(self, symbol, pos_id, ccy, algo_id=None,
                             extra_data=None, **kwargs):
        """Async move positions between currencies"""
        path, params, extra_data = self._move_positions(symbol, pos_id, ccy, algo_id,
                                                         extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_move_positions_history(self, symbol=None, ccy=None, after=None,
                                     before=None, limit=None,
                                     extra_data=None, **kwargs):
        """
        Get move positions history
        :param symbol: Instrument ID
        :param ccy: Currency
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_move_positions_history"
        params = {}
        if symbol:
            params['instId'] = symbol
        if ccy:
            params['ccy'] = ccy
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
            "normalize_function": OkxRequestData._get_move_positions_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_move_positions_history_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_move_positions_history(self, symbol=None, ccy=None, after=None,
                                    before=None, limit=None,
                                    extra_data=None, **kwargs):
        """Get move positions history"""
        path, params, extra_data = self._get_move_positions_history(symbol, ccy, after,
                                                                     before, limit,
                                                                     extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_move_positions_history(self, symbol=None, ccy=None, after=None,
                                         before=None, limit=None,
                                         extra_data=None, **kwargs):
        """Async get move positions history"""
        path, params, extra_data = self._get_move_positions_history(symbol, ccy, after,
                                                                     before, limit,
                                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_auto_earn(self, ccy, auto_earn, auto_earn_type=None,
                       extra_data=None, **kwargs):
        """
        Set auto earn (automatic savings)
        :param ccy: Currency, e.g. `USDT`
        :param auto_earn: Auto earn status: `true` or `false`
        :param auto_earn_type: Auto earn type: `1` manual, `2` fast
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "set_auto_earn"
        params = {
            'ccy': ccy,
            'autoEarn': auto_earn,
        }
        if auto_earn_type:
            params['autoEarnType'] = auto_earn_type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_auto_earn_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_auto_earn_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def set_auto_earn(self, ccy, auto_earn, auto_earn_type=None,
                      extra_data=None, **kwargs):
        """Set auto earn (automatic savings)"""
        path, params, extra_data = self._set_auto_earn(ccy, auto_earn, auto_earn_type,
                                                        extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_auto_earn(self, ccy, auto_earn, auto_earn_type=None,
                            extra_data=None, **kwargs):
        """Async set auto earn (automatic savings)"""
        path, params, extra_data = self._set_auto_earn(ccy, auto_earn, auto_earn_type,
                                                        extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_settle_currency(self, symbol, ccy, extra_data=None, **kwargs):
        """
        Set settlement currency
        :param symbol: Instrument ID
        :param ccy: Settlement currency
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "set_settle_currency"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'ccy': ccy,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_settle_currency_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_settle_currency_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def set_settle_currency(self, symbol, ccy, extra_data=None, **kwargs):
        """Set settlement currency"""
        path, params, extra_data = self._set_settle_currency(symbol, ccy,
                                                              extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_settle_currency(self, symbol, ccy, extra_data=None, **kwargs):
        """Async set settlement currency"""
        path, params, extra_data = self._set_settle_currency(symbol, ccy,
                                                              extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_trading_config(self, symbol, pos_mode=None, auto_loan=None,
                            auto_margin=None, auto_mul=None,
                            extra_data=None, **kwargs):
        """
        Set trading config
        :param symbol: Instrument ID
        :param pos_mode: Position mode: `net_mode` or `dual_side`
        :param auto_loan: Auto loan: `true` or `false`
        :param auto_margin: Auto margin: `true` or `false`
        :param auto_mul: Auto multiplier
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "set_trading_config"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
        }
        if pos_mode:
            params['posMode'] = pos_mode
        if auto_loan is not None:
            params['autoLoan'] = auto_loan
        if auto_margin is not None:
            params['autoMargin'] = auto_margin
        if auto_mul is not None:
            params['autoMul'] = auto_mul
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_trading_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_trading_config_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def set_trading_config(self, symbol, pos_mode=None, auto_loan=None,
                           auto_margin=None, auto_mul=None,
                           extra_data=None, **kwargs):
        """Set trading config"""
        path, params, extra_data = self._set_trading_config(symbol, pos_mode, auto_loan,
                                                              auto_margin, auto_mul,
                                                              extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_trading_config(self, symbol, pos_mode=None, auto_loan=None,
                                 auto_margin=None, auto_mul=None,
                                 extra_data=None, **kwargs):
        """Async set trading config"""
        path, params, extra_data = self._set_trading_config(symbol, pos_mode, auto_loan,
                                                              auto_margin, auto_mul,
                                                              extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_delta_neutral_precheck(self, symbol, delta_neutral_precheck,
                                     extra_data=None, **kwargs):
        """
        Set delta neutral precheck
        :param symbol: Instrument ID
        :param delta_neutral_precheck: Delta neutral precheck: `true` or `false`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "set_delta_neutral_precheck"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'deltaNeutralPrecheck': delta_neutral_precheck,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_delta_neutral_precheck_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_delta_neutral_precheck_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def set_delta_neutral_precheck(self, symbol, delta_neutral_precheck,
                                    extra_data=None, **kwargs):
        """Set delta neutral precheck"""
        path, params, extra_data = self._set_delta_neutral_precheck(symbol,
                                                                     delta_neutral_precheck,
                                                                     extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_delta_neutral_precheck(self, symbol, delta_neutral_precheck,
                                         extra_data=None, **kwargs):
        """Async set delta neutral precheck"""
        path, params, extra_data = self._set_delta_neutral_precheck(symbol,
                                                                     delta_neutral_precheck,
                                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Public Data APIs (Additional) ====================

    def _get_estimated_price(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """
        Get estimated delivery/exercise price
        :param inst_type: Instrument type: `FUTURES`, `OPTION` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_estimated_price"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_estimated_price_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_estimated_price_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_estimated_price(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get estimated delivery/exercise price"""
        path, params, extra_data = self._get_estimated_price(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_estimated_price(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get estimated delivery/exercise price"""
        path, params, extra_data = self._get_estimated_price(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_discount_rate(self, ccy=None, discount_level=None, extra_data=None, **kwargs):
        """
        Get discount rate and interest-free quota
        :param ccy: Currency, e.g. `BTC`
        :param discount_level: Discount level, default is `lv1`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_discount_rate"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if discount_level:
            params['discountLevel'] = discount_level
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_discount_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_discount_rate_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_discount_rate(self, ccy=None, discount_level=None, extra_data=None, **kwargs):
        """Get discount rate and interest-free quota"""
        path, params, extra_data = self._get_discount_rate(ccy, discount_level, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_discount_rate(self, ccy=None, discount_level=None, extra_data=None, **kwargs):
        """Async get discount rate and interest-free quota"""
        path, params, extra_data = self._get_discount_rate(ccy, discount_level, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_interest_rate_loan_quota(self, ccy=None, extra_data=None, **kwargs):
        """
        Get interest rate and loan quota
        :param ccy: Currency, e.g. `BTC`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_interest_rate_loan_quota"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_interest_rate_loan_quota_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_interest_rate_loan_quota_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_interest_rate_loan_quota(self, ccy=None, extra_data=None, **kwargs):
        """Get interest rate and loan quota"""
        path, params, extra_data = self._get_interest_rate_loan_quota(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_interest_rate_loan_quota(self, ccy=None, extra_data=None, **kwargs):
        """Async get interest rate and loan quota"""
        path, params, extra_data = self._get_interest_rate_loan_quota(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_interest_rate(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                           extra_data=None, **kwargs):
        """
        Get interest rate for borrowing
        :param ccy: Currency, e.g. `BTC`
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param mgn_mode: Margin mode, cross or isolated
        :param uly: Underlying, e.g. BTC-USD
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_interest_rate"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if inst_type:
            params['instType'] = inst_type
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if uly:
            params['uly'] = uly
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or uly or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_interest_rate(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                          extra_data=None, **kwargs):
        """Get interest rate for borrowing"""
        path, params, extra_data = self._get_interest_rate(ccy, inst_type, mgn_mode, uly,
                                                             extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_interest_rate(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                                extra_data=None, **kwargs):
        """Async get interest rate for borrowing"""
        path, params, extra_data = self._get_interest_rate(ccy, inst_type, mgn_mode, uly,
                                                             extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_underlying(self, inst_type, uly=None, extra_data=None, **kwargs):
        """
        Get underlying index
        :param inst_type: Instrument type: `FUTURES`, `SWAP`, `OPTION` (required)
        :param uly: Underlying
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_underlying"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_underlying_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_underlying_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_underlying(self, inst_type, uly=None, extra_data=None, **kwargs):
        """Get underlying index"""
        path, params, extra_data = self._get_underlying(inst_type, uly, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_underlying(self, inst_type, uly=None, extra_data=None, **kwargs):
        """Async get underlying index"""
        path, params, extra_data = self._get_underlying(inst_type, uly, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_insurance_fund(self, inst_type, uly=None, inst_id=None, after=None,
                           before=None, limit=None, extra_data=None, **kwargs):
        """
        Get insurance fund balance
        :param inst_type: Instrument type: `MARGIN`, `FUTURES`, `SWAP`, `OPTION` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_insurance_fund"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_insurance_fund_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_insurance_fund_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_insurance_fund(self, inst_type, uly=None, inst_id=None, after=None,
                          before=None, limit=None, extra_data=None, **kwargs):
        """Get insurance fund balance"""
        path, params, extra_data = self._get_insurance_fund(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_insurance_fund(self, inst_type, uly=None, inst_id=None, after=None,
                                before=None, limit=None, extra_data=None, **kwargs):
        """Async get insurance fund balance"""
        path, params, extra_data = self._get_insurance_fund(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _convert_contract_coin(self, inst_type, uly, inst_id, amount, unit, extra_data=None, **kwargs):
        """
        Convert contract unit
        :param inst_type: Instrument type: `FUTURES`, `SWAP` (required)
        :param uly: Underlying (required)
        :param inst_id: Instrument ID (required)
        :param amount: Quantity to be converted (required)
        :param unit: Unit of amount to be converted: `ccy`, `ct` (required)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "convert_contract_coin"
        params = {
            'instType': inst_type,
            'uly': uly,
            'instId': inst_id,
            'amount': amount,
            'unit': unit,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._convert_contract_coin_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _convert_contract_coin_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def convert_contract_coin(self, inst_type, uly, inst_id, amount, unit, extra_data=None, **kwargs):
        """Convert contract unit"""
        path, params, extra_data = self._convert_contract_coin(
            inst_type, uly, inst_id, amount, unit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_convert_contract_coin(self, inst_type, uly, inst_id, amount, unit, extra_data=None, **kwargs):
        """Async convert contract unit"""
        path, params, extra_data = self._convert_contract_coin(
            inst_type, uly, inst_id, amount, unit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_instrument_tick_bands(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """
        Get instrument minimum tick size
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `FUTURES`, `SWAP`, `OPTION` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_instrument_tick_bands"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_instrument_tick_bands_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_instrument_tick_bands_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_instrument_tick_bands(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get instrument minimum tick size"""
        path, params, extra_data = self._get_instrument_tick_bands(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_instrument_tick_bands(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get instrument minimum tick size"""
        path, params, extra_data = self._get_instrument_tick_bands(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Critical Public APIs ====================

    def _get_system_time(self, extra_data=None, **kwargs):
        """Get system time"""
        request_type = "get_system_time"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "SYSTEM",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_system_time(self, extra_data=None, **kwargs):
        """Get system time"""
        path, params, extra_data = self._get_system_time(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_system_time(self, extra_data=None, **kwargs):
        """Async get system time"""
        path, params, extra_data = self._get_system_time(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_tickers(self, inst_type="SWAP", uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get tickers for all instruments"""
        request_type = "get_tickers"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_tickers(self, inst_type="SWAP", uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get tickers for all instruments"""
        path, params, extra_data = self._get_tickers(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_tickers(self, inst_type="SWAP", uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get tickers for all instruments"""
        path, params, extra_data = self._get_tickers(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_depth_full(self, symbol, sz=100, extra_data=None, **kwargs):
        """Get full depth order book"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth_full"
        params = {
            'instId': request_symbol,
            'sz': sz
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_depth_full(self, symbol, sz=100, extra_data=None, **kwargs):
        """Get full depth order book"""
        path, params, extra_data = self._get_depth_full(symbol, sz, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_depth_full(self, symbol, sz=100, extra_data=None, **kwargs):
        """Async get full depth order book"""
        path, params, extra_data = self._get_depth_full(symbol, sz, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_kline_his(self, symbol, bar='1m', after='', before='', limit='100',
                       extra_data=None, **kwargs):
        """Get historical kline data"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_kline_his"
        params = {
            'instId': request_symbol,
            'bar': bar,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_kline_his(self, symbol, bar='1m', after='', before='', limit='100',
                      extra_data=None, **kwargs):
        """Get historical kline data"""
        path, params, extra_data = self._get_kline_his(symbol, bar, after, before,
                                                        limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_kline_his(self, symbol, bar='1m', after='', before='', limit='100',
                            extra_data=None, **kwargs):
        """Async get historical kline data"""
        path, params, extra_data = self._get_kline_his(symbol, bar, after, before,
                                                        limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_trades(self, symbol, limit='100', extra_data=None, **kwargs):
        """Get recent trades"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        params = {
            'instId': request_symbol,
            'limit': limit
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_trades(self, symbol, limit='100', extra_data=None, **kwargs):
        """Get recent trades"""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_trades(self, symbol, limit='100', extra_data=None, **kwargs):
        """Async get recent trades"""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_trades_history(self, symbol, after='', before='', limit='100',
                            extra_data=None, **kwargs):
        """Get historical trades data"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades_history"
        params = {
            'instId': request_symbol,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_trades_history(self, symbol, after='', before='', limit='100',
                           extra_data=None, **kwargs):
        """Get historical trades data"""
        path, params, extra_data = self._get_trades_history(symbol, after, before,
                                                             limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_trades_history(self, symbol, after='', before='', limit='100',
                                 extra_data=None, **kwargs):
        """Async get historical trades data"""
        path, params, extra_data = self._get_trades_history(symbol, after, before,
                                                             limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_public_instruments(self, inst_type, uly=None, inst_id=None, uly_multi=None,
                                 extra_data=None, **kwargs):
        """Get public instruments"""
        request_type = "get_public_instruments"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if uly_multi:
            params['ulyMulti'] = uly_multi
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_public_instruments(self, inst_type, uly=None, inst_id=None, uly_multi=None,
                               extra_data=None, **kwargs):
        """Get public instruments"""
        path, params, extra_data = self._get_public_instruments(inst_type, uly, inst_id,
                                                                 uly_multi, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_public_instruments(self, inst_type, uly=None, inst_id=None, uly_multi=None,
                                     extra_data=None, **kwargs):
        """Async get public instruments"""
        path, params, extra_data = self._get_public_instruments(inst_type, uly, inst_id,
                                                                 uly_multi, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_delivery_exercise_history(self, inst_type, uly, after='', before='', limit='100',
                                        extra_data=None, **kwargs):
        """Get delivery exercise history"""
        request_type = "get_delivery_exercise_history"
        params = {
            'instType': inst_type,
            'uly': uly,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly,
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_delivery_exercise_history(self, inst_type, uly, after='', before='', limit='100',
                                       extra_data=None, **kwargs):
        """Get delivery exercise history"""
        path, params, extra_data = self._get_delivery_exercise_history(inst_type, uly, after,
                                                                         before, limit, extra_data,
                                                                         **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_delivery_exercise_history(self, inst_type, uly, after='', before='',
                                            limit='100', extra_data=None, **kwargs):
        """Async get delivery exercise history"""
        path, params, extra_data = self._get_delivery_exercise_history(inst_type, uly, after,
                                                                         before, limit, extra_data,
                                                                         **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_estimated_settlement_price(self, inst_type, uly=None, inst_id=None,
                                         extra_data=None, **kwargs):
        """Get estimated settlement price"""
        request_type = "get_estimated_settlement_price"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_estimated_settlement_price(self, inst_type, uly=None, inst_id=None,
                                        extra_data=None, **kwargs):
        """Get estimated settlement price"""
        path, params, extra_data = self._get_estimated_settlement_price(inst_type, uly, inst_id,
                                                                          extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_estimated_settlement_price(self, inst_type, uly=None, inst_id=None,
                                              extra_data=None, **kwargs):
        """Async get estimated settlement price"""
        path, params, extra_data = self._get_estimated_settlement_price(inst_type, uly, inst_id,
                                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_settlement_history(self, inst_type, uly, after='', before='', limit='100',
                                 extra_data=None, **kwargs):
        """Get settlement history"""
        request_type = "get_settlement_history"
        params = {
            'instType': inst_type,
            'uly': uly,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly,
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_settlement_history(self, inst_type, uly, after='', before='', limit='100',
                                extra_data=None, **kwargs):
        """Get settlement history"""
        path, params, extra_data = self._get_settlement_history(inst_type, uly, after,
                                                                  before, limit, extra_data,
                                                                  **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_settlement_history(self, inst_type, uly, after='', before='', limit='100',
                                      extra_data=None, **kwargs):
        """Async get settlement history"""
        path, params, extra_data = self._get_settlement_history(inst_type, uly, after,
                                                                  before, limit, extra_data,
                                                                  **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_price_limit(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get price limit"""
        request_type = "get_price_limit"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_price_limit(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get price limit"""
        path, params, extra_data = self._get_price_limit(inst_type, uly, inst_id,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_price_limit(self, inst_type, uly=None, inst_id=None,
                              extra_data=None, **kwargs):
        """Async get price limit"""
        path, params, extra_data = self._get_price_limit(inst_type, uly, inst_id,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_opt_summary(self, inst_type="OPTION", uly=None, exp_time=None, extra_data=None, **kwargs):
        """Get option summary"""
        request_type = "get_opt_summary"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if exp_time:
            params['expTime'] = exp_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_opt_summary(self, inst_type="OPTION", uly=None, exp_time=None,
                        extra_data=None, **kwargs):
        """Get option summary"""
        path, params, extra_data = self._get_opt_summary(inst_type, uly, exp_time,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_opt_summary(self, inst_type="OPTION", uly=None, exp_time=None,
                              extra_data=None, **kwargs):
        """Async get option summary"""
        path, params, extra_data = self._get_opt_summary(inst_type, uly, exp_time,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_position_tiers_public(self, inst_type, uly=None, inst_id=None, tier=None,
                                    extra_data=None, **kwargs):
        """Get position tiers (public)"""
        request_type = "get_position_tiers_public"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if tier:
            params['tier'] = tier
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_position_tiers_public(self, inst_type, uly=None, inst_id=None, tier=None,
                                  extra_data=None, **kwargs):
        """Get position tiers (public)"""
        path, params, extra_data = self._get_position_tiers_public(inst_type, uly, inst_id,
                                                                    tier, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_position_tiers_public(self, inst_type, uly=None, inst_id=None, tier=None,
                                        extra_data=None, **kwargs):
        """Async get position tiers (public)"""
        path, params, extra_data = self._get_position_tiers_public(inst_type, uly, inst_id,
                                                                    tier, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Trading Account APIs ====================

    def _get_account_position_risk(self, extra_data=None, **kwargs):
        """Get account position risk"""
        request_type = "get_account_position_risk"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_account_position_risk(self, extra_data=None, **kwargs):
        """Get account position risk"""
        path, params, extra_data = self._get_account_position_risk(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_account_position_risk(self, extra_data=None, **kwargs):
        """Async get account position risk"""
        path, params, extra_data = self._get_account_position_risk(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_bills_archive(self, year, ccy=None, after=None, before=None, limit=None,
                           extra_data=None, **kwargs):
        """Get bills archive"""
        request_type = "get_bills_archive"
        params = {'year': str(year)}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_bills_archive(self, year, ccy=None, after=None, before=None, limit=None,
                          extra_data=None, **kwargs):
        """Get bills archive"""
        path, params, extra_data = self._get_bills_archive(year, ccy, after, before,
                                                            limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_bills_archive(self, year, ccy=None, after=None, before=None, limit=None,
                                extra_data=None, **kwargs):
        """Async get bills archive"""
        path, params, extra_data = self._get_bills_archive(year, ccy, after, before,
                                                            limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_adjust_leverage_info(self, inst_type, uly=None, inst_id=None, mgn_mode=None,
                                   extra_data=None, **kwargs):
        """Get adjust leverage info"""
        request_type = "get_adjust_leverage_info"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_adjust_leverage_info(self, inst_type, uly=None, inst_id=None, mgn_mode=None,
                                  extra_data=None, **kwargs):
        """Get adjust leverage info"""
        path, params, extra_data = self._get_adjust_leverage_info(inst_type, uly, inst_id,
                                                                    mgn_mode, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_adjust_leverage_info(self, inst_type, uly=None, inst_id=None, mgn_mode=None,
                                       extra_data=None, **kwargs):
        """Async get adjust leverage info"""
        path, params, extra_data = self._get_adjust_leverage_info(inst_type, uly, inst_id,
                                                                    mgn_mode, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_max_loan(self, inst_type, uly=None, inst_id=None, mgn_mode=None, ccy=None,
                      extra_data=None, **kwargs):
        """Get max loan"""
        request_type = "get_max_loan"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_max_loan(self, inst_type, uly=None, inst_id=None, mgn_mode=None, ccy=None,
                     extra_data=None, **kwargs):
        """Get max loan"""
        path, params, extra_data = self._get_max_loan(inst_type, uly, inst_id, mgn_mode,
                                                        ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_max_loan(self, inst_type, uly=None, inst_id=None, mgn_mode=None, ccy=None,
                           extra_data=None, **kwargs):
        """Async get max loan"""
        path, params, extra_data = self._get_max_loan(inst_type, uly, inst_id, mgn_mode,
                                                        ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_interest_accrued(self, inst_type, uly=None, inst_id=None, mgn_mode=None, ccy=None,
                              extra_data=None, **kwargs):
        """Get interest accrued"""
        request_type = "get_interest_accrued"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_interest_accrued(self, inst_type, uly=None, inst_id=None, mgn_mode=None, ccy=None,
                             extra_data=None, **kwargs):
        """Get interest accrued"""
        path, params, extra_data = self._get_interest_accrued(inst_type, uly, inst_id,
                                                               mgn_mode, ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_interest_accrued(self, inst_type, uly=None, inst_id=None, mgn_mode=None,
                                   ccy=None, extra_data=None, **kwargs):
        """Async get interest accrued"""
        path, params, extra_data = self._get_interest_accrued(inst_type, uly, inst_id,
                                                               mgn_mode, ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_greeks(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get greeks"""
        request_type = "get_greeks"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_greeks(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get greeks"""
        path, params, extra_data = self._get_greeks(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_greeks(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get greeks"""
        path, params, extra_data = self._get_greeks(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_position_tiers(self, inst_type, uly=None, inst_id=None, tier=None,
                            extra_data=None, **kwargs):
        """Get position tiers"""
        request_type = "get_position_tiers"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if tier:
            params['tier'] = tier
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_position_tiers(self, inst_type, uly=None, inst_id=None, tier=None,
                           extra_data=None, **kwargs):
        """Get position tiers"""
        path, params, extra_data = self._get_position_tiers(inst_type, uly, inst_id, tier,
                                                              extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_position_tiers(self, inst_type, uly=None, inst_id=None, tier=None,
                                 extra_data=None, **kwargs):
        """Async get position tiers"""
        path, params, extra_data = self._get_position_tiers(inst_type, uly, inst_id, tier,
                                                              extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_max_withdrawal(self, ccy=None, extra_data=None, **kwargs):
        """Get max withdrawal"""
        request_type = "get_max_withdrawal"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_max_withdrawal(self, ccy=None, extra_data=None, **kwargs):
        """Get max withdrawal"""
        path, params, extra_data = self._get_max_withdrawal(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_max_withdrawal(self, ccy=None, extra_data=None, **kwargs):
        """Async get max withdrawal"""
        path, params, extra_data = self._get_max_withdrawal(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_risk_state(self, extra_data=None, **kwargs):
        """Get risk state"""
        request_type = "get_risk_state"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_risk_state(self, extra_data=None, **kwargs):
        """Get risk state"""
        path, params, extra_data = self._get_risk_state(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_risk_state(self, extra_data=None, **kwargs):
        """Async get risk state"""
        path, params, extra_data = self._get_risk_state(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_bills(self, inst_type=None, uly=None, inst_id=None, ccy=None, mgn_mode=None,
                   after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get bills"""
        request_type = "get_bills"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_bills(self, inst_type=None, uly=None, inst_id=None, ccy=None, mgn_mode=None,
                  after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get bills"""
        path, params, extra_data = self._get_bills(inst_type, uly, inst_id, ccy, mgn_mode,
                                                     after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_bills(self, inst_type=None, uly=None, inst_id=None, ccy=None, mgn_mode=None,
                        after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get bills"""
        path, params, extra_data = self._get_bills(inst_type, uly, inst_id, ccy, mgn_mode,
                                                     after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_lever(self, inst_type, uly=None, inst_id=None, mgn_mode=None, extra_data=None, **kwargs):
        """Get leverage info"""
        request_type = "get_lever"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_lever(self, inst_type, uly=None, inst_id=None, mgn_mode=None, extra_data=None, **kwargs):
        """Get leverage info"""
        path, params, extra_data = self._get_lever(inst_type, uly, inst_id, mgn_mode,
                                                     extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_lever(self, inst_type, uly=None, inst_id=None, mgn_mode=None,
                        extra_data=None, **kwargs):
        """Async get leverage info"""
        path, params, extra_data = self._get_lever(inst_type, uly, inst_id, mgn_mode,
                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Funding Account APIs ====================

    def _get_currencies(self, ccy=None, extra_data=None, **kwargs):
        """Get currencies"""
        request_type = "get_currencies"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_currencies(self, ccy=None, extra_data=None, **kwargs):
        """Get currencies"""
        path, params, extra_data = self._get_currencies(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_currencies(self, ccy=None, extra_data=None, **kwargs):
        """Async get currencies"""
        path, params, extra_data = self._get_currencies(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_balances(self, ccy=None, extra_data=None, **kwargs):
        """Get asset balances"""
        request_type = "get_asset_balances"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_balances(self, ccy=None, extra_data=None, **kwargs):
        """Get asset balances"""
        path, params, extra_data = self._get_asset_balances(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_balances(self, ccy=None, extra_data=None, **kwargs):
        """Async get asset balances"""
        path, params, extra_data = self._get_asset_balances(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_non_tradable_assets(self, ccy=None, extra_data=None, **kwargs):
        """Get non-tradable assets"""
        request_type = "get_non_tradable_assets"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_non_tradable_assets(self, ccy=None, extra_data=None, **kwargs):
        """Get non-tradable assets"""
        path, params, extra_data = self._get_non_tradable_assets(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_non_tradable_assets(self, ccy=None, extra_data=None, **kwargs):
        """Async get non-tradable assets"""
        path, params, extra_data = self._get_non_tradable_assets(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_valuation(self, ccy=None, extra_data=None, **kwargs):
        """Get asset valuation"""
        request_type = "get_asset_valuation"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_valuation(self, ccy=None, extra_data=None, **kwargs):
        """Get asset valuation"""
        path, params, extra_data = self._get_asset_valuation(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_valuation(self, ccy=None, extra_data=None, **kwargs):
        """Async get asset valuation"""
        path, params, extra_data = self._get_asset_valuation(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _transfer(self, ccy, amt, from_account, to_account, type=None, client_bill_id=None,
                  extra_data=None, **kwargs):
        """Asset transfer"""
        request_type = "transfer"
        params = {
            'ccy': ccy,
            'amt': str(amt),
            'from': from_account,
            'to': to_account,
        }
        if type:
            params['type'] = type
        if client_bill_id:
            params['clientBillId'] = client_bill_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def transfer(self, ccy, amt, from_account, to_account, type=None, client_bill_id=None,
                 extra_data=None, **kwargs):
        """Asset transfer"""
        path, params, extra_data = self._transfer(ccy, amt, from_account, to_account, type,
                                                    client_bill_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_transfer(self, ccy, amt, from_account, to_account, type=None, client_bill_id=None,
                       extra_data=None, **kwargs):
        """Async asset transfer"""
        path, params, extra_data = self._transfer(ccy, amt, from_account, to_account, type,
                                                    client_bill_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_transfer_state(self, transfer_id=None, client_bill_id=None, type=None,
                             extra_data=None, **kwargs):
        """Get transfer state"""
        request_type = "get_transfer_state"
        params = {}
        if transfer_id:
            params['transId'] = transfer_id
        if client_bill_id:
            params['clientBillId'] = client_bill_id
        if type:
            params['type'] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_transfer_state(self, transfer_id=None, client_bill_id=None, type=None,
                           extra_data=None, **kwargs):
        """Get transfer state"""
        path, params, extra_data = self._get_transfer_state(transfer_id, client_bill_id,
                                                              type, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_transfer_state(self, transfer_id=None, client_bill_id=None, type=None,
                                  extra_data=None, **kwargs):
        """Async get transfer state"""
        path, params, extra_data = self._get_transfer_state(transfer_id, client_bill_id,
                                                              type, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_bills(self, ccy=None, type=None, after=None, before=None, limit=None,
                         extra_data=None, **kwargs):
        """Get asset bills"""
        request_type = "get_asset_bills"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if type:
            params['type'] = type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_bills(self, ccy=None, type=None, after=None, before=None, limit=None,
                        extra_data=None, **kwargs):
        """Get asset bills"""
        path, params, extra_data = self._get_asset_bills(ccy, type, after, before, limit,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_bills(self, ccy=None, type=None, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """Async get asset bills"""
        path, params, extra_data = self._get_asset_bills(ccy, type, after, before, limit,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_bills_history(self, ccy=None, type=None, after=None, before=None, limit=None,
                                  extra_data=None, **kwargs):
        """Get asset bills history"""
        request_type = "get_asset_bills_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if type:
            params['type'] = type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_bills_history(self, ccy=None, type=None, after=None, before=None, limit=None,
                                extra_data=None, **kwargs):
        """Get asset bills history"""
        path, params, extra_data = self._get_asset_bills_history(ccy, type, after, before, limit,
                                                                   extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_bills_history(self, ccy=None, type=None, after=None, before=None,
                                       limit=None, extra_data=None, **kwargs):
        """Async get asset bills history"""
        path, params, extra_data = self._get_asset_bills_history(ccy, type, after, before, limit,
                                                                   extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_address(self, ccy, to=None, chain=None, extra_data=None, **kwargs):
        """Get deposit address"""
        request_type = "get_deposit_address"
        params = {'ccy': ccy}
        if to:
            params['to'] = to
        if chain:
            params['chain'] = chain
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_address(self, ccy, to=None, chain=None, extra_data=None, **kwargs):
        """Get deposit address"""
        path, params, extra_data = self._get_deposit_address(ccy, to, chain, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_address(self, ccy, to=None, chain=None, extra_data=None, **kwargs):
        """Async get deposit address"""
        path, params, extra_data = self._get_deposit_address(ccy, to, chain, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_history(self, ccy=None, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get deposit history"""
        request_type = "get_deposit_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_history(self, ccy=None, after=None, before=None, limit=None,
                            extra_data=None, **kwargs):
        """Get deposit history"""
        path, params, extra_data = self._get_deposit_history(ccy, after, before, limit,
                                                               extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_history(self, ccy=None, after=None, before=None, limit=None,
                                  extra_data=None, **kwargs):
        """Async get deposit history"""
        path, params, extra_data = self._get_deposit_history(ccy, after, before, limit,
                                                               extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_withdraw_status(self, ccy=None, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Get deposit withdraw status"""
        request_type = "get_deposit_withdraw_status"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_withdraw_status(self, ccy=None, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Get deposit withdraw status"""
        path, params, extra_data = self._get_deposit_withdraw_status(ccy, after, before, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_withdraw_status(self, ccy=None, after=None, before=None, limit=None,
                                           extra_data=None, **kwargs):
        """Async get deposit withdraw status"""
        path, params, extra_data = self._get_deposit_withdraw_status(ccy, after, before, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _withdrawal(self, ccy, amt, dest, to_addr, fee=None, chain=None, area_code=None,
                     client_chain_id=None, extra_data=None, **kwargs):
        """Withdrawal"""
        request_type = "withdrawal"
        params = {
            'ccy': ccy,
            'amt': str(amt),
            'dest': dest,
            'toAddr': to_addr,
        }
        if fee is not None:
            params['fee'] = str(fee)
        if chain:
            params['chain'] = chain
        if area_code:
            params['areaCode'] = area_code
        if client_chain_id:
            params['clientChainId'] = client_chain_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def withdrawal(self, ccy, amt, dest, to_addr, fee=None, chain=None, area_code=None,
                   client_chain_id=None, extra_data=None, **kwargs):
        """Withdrawal"""
        path, params, extra_data = self._withdrawal(ccy, amt, dest, to_addr, fee, chain,
                                                      area_code, client_chain_id, extra_data,
                                                      **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_withdrawal(self, ccy, amt, dest, to_addr, fee=None, chain=None, area_code=None,
                         client_chain_id=None, extra_data=None, **kwargs):
        """Async withdrawal"""
        path, params, extra_data = self._withdrawal(ccy, amt, dest, to_addr, fee, chain,
                                                      area_code, client_chain_id, extra_data,
                                                      **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_withdrawal(self, wd_id, ccy=None, extra_data=None, **kwargs):
        """Cancel withdrawal"""
        request_type = "cancel_withdrawal"
        params = {'wdId': wd_id}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_withdrawal(self, wd_id, ccy=None, extra_data=None, **kwargs):
        """Cancel withdrawal"""
        path, params, extra_data = self._cancel_withdrawal(wd_id, ccy, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_withdrawal(self, wd_id, ccy=None, extra_data=None, **kwargs):
        """Async cancel withdrawal"""
        path, params, extra_data = self._cancel_withdrawal(wd_id, ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_history(self, ccy=None, after=None, before=None, limit=None,
                                 extra_data=None, **kwargs):
        """Get withdrawal history"""
        request_type = "get_withdrawal_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_history(self, ccy=None, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """Get withdrawal history"""
        path, params, extra_data = self._get_withdrawal_history(ccy, after, before, limit,
                                                                  extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_history(self, ccy=None, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Async get withdrawal history"""
        path, params, extra_data = self._get_withdrawal_history(ccy, after, before, limit,
                                                                  extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Sub-account APIs ====================

    def _get_sub_account_list(self, extra_data=None, **kwargs):
        """Get sub account list"""
        request_type = "get_sub_account_list"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_list(self, extra_data=None, **kwargs):
        """Get sub account list"""
        path, params, extra_data = self._get_sub_account_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_list(self, extra_data=None, **kwargs):
        """Async get sub account list"""
        path, params, extra_data = self._get_sub_account_list(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _create_sub_account(self, sub_acct, tag=None, extra_data=None, **kwargs):
        """Create sub account"""
        request_type = "create_sub_account"
        params = {'subAcct': sub_acct}
        if tag:
            params['tag'] = tag
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def create_sub_account(self, sub_acct, tag=None, extra_data=None, **kwargs):
        """Create sub account"""
        path, params, extra_data = self._create_sub_account(sub_acct, tag, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_sub_account(self, sub_acct, tag=None, extra_data=None, **kwargs):
        """Async create sub account"""
        path, params, extra_data = self._create_sub_account(sub_acct, tag, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _create_sub_account_api_key(self, sub_acct, label, ip=None, perm=None, extra_data=None, **kwargs):
        """Create sub account API key"""
        request_type = "create_sub_account_api_key"
        params = {
            'subAcct': sub_acct,
            'label': label,
        }
        if ip:
            params['ip'] = ip
        if perm:
            params['perm'] = perm
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def create_sub_account_api_key(self, sub_acct, label, ip=None, perm=None,
                                   extra_data=None, **kwargs):
        """Create sub account API key"""
        path, params, extra_data = self._create_sub_account_api_key(sub_acct, label, ip, perm,
                                                                     extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_sub_account_api_key(self, sub_acct, label, ip=None, perm=None,
                                         extra_data=None, **kwargs):
        """Async create sub account API key"""
        path, params, extra_data = self._create_sub_account_api_key(sub_acct, label, ip, perm,
                                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_sub_account_api_key(self, sub_acct, extra_data=None, **kwargs):
        """Get sub account API key"""
        request_type = "get_sub_account_api_key"
        params = {'subAcct': sub_acct}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_api_key(self, sub_acct, extra_data=None, **kwargs):
        """Get sub account API key"""
        path, params, extra_data = self._get_sub_account_api_key(sub_acct, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_api_key(self, sub_acct, extra_data=None, **kwargs):
        """Async get sub account API key"""
        path, params, extra_data = self._get_sub_account_api_key(sub_acct, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _reset_sub_account_api_key(self, sub_acct, api_key, label=None, ip=None, perm=None,
                                    extra_data=None, **kwargs):
        """Reset sub account API key"""
        request_type = "reset_sub_account_api_key"
        params = {
            'subAcct': sub_acct,
            'apiKey': api_key,
        }
        if label:
            params['label'] = label
        if ip:
            params['ip'] = ip
        if perm:
            params['perm'] = perm
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def reset_sub_account_api_key(self, sub_acct, api_key, label=None, ip=None, perm=None,
                                  extra_data=None, **kwargs):
        """Reset sub account API key"""
        path, params, extra_data = self._reset_sub_account_api_key(sub_acct, api_key, label, ip,
                                                                     perm, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_reset_sub_account_api_key(self, sub_acct, api_key, label=None, ip=None, perm=None,
                                        extra_data=None, **kwargs):
        """Async reset sub account API key"""
        path, params, extra_data = self._reset_sub_account_api_key(sub_acct, api_key, label, ip,
                                                                     perm, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _delete_sub_account_api_key(self, sub_acct, api_key, extra_data=None, **kwargs):
        """Delete sub account API key"""
        request_type = "delete_sub_account_api_key"
        params = {
            'subAcct': sub_acct,
            'apiKey': api_key,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def delete_sub_account_api_key(self, sub_acct, api_key, extra_data=None, **kwargs):
        """Delete sub account API key"""
        path, params, extra_data = self._delete_sub_account_api_key(sub_acct, api_key,
                                                                      extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_delete_sub_account_api_key(self, sub_acct, api_key, extra_data=None, **kwargs):
        """Async delete sub account API key"""
        path, params, extra_data = self._delete_sub_account_api_key(sub_acct, api_key,
                                                                      extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_sub_account_funding_balance(self, sub_acct, ccy=None, extra_data=None, **kwargs):
        """Get sub account funding balance"""
        request_type = "get_sub_account_funding_balance"
        params = {'subAcct': sub_acct}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_funding_balance(self, sub_acct, ccy=None, extra_data=None, **kwargs):
        """Get sub account funding balance"""
        path, params, extra_data = self._get_sub_account_funding_balance(sub_acct, ccy,
                                                                          extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_funding_balance(self, sub_acct, ccy=None, extra_data=None, **kwargs):
        """Async get sub account funding balance"""
        path, params, extra_data = self._get_sub_account_funding_balance(sub_acct, ccy,
                                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_sub_account_max_withdrawal(self, sub_acct, ccy=None, extra_data=None, **kwargs):
        """Get sub account max withdrawal"""
        request_type = "get_sub_account_max_withdrawal"
        params = {'subAcct': sub_acct}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_max_withdrawal(self, sub_acct, ccy=None, extra_data=None, **kwargs):
        """Get sub account max withdrawal"""
        path, params, extra_data = self._get_sub_account_max_withdrawal(sub_acct, ccy,
                                                                         extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_max_withdrawal(self, sub_acct, ccy=None, extra_data=None, **kwargs):
        """Async get sub account max withdrawal"""
        path, params, extra_data = self._get_sub_account_max_withdrawal(sub_acct, ccy,
                                                                         extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Trade APIs ====================

    def _make_orders(self, orders_data, extra_data=None, **kwargs):
        """Make multiple orders (batch)"""
        request_type = "make_orders"
        params = orders_data
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "BATCH",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def make_orders(self, orders_data, extra_data=None, **kwargs):
        """Make multiple orders (batch)"""
        path, params, extra_data = self._make_orders(orders_data, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_make_orders(self, orders_data, extra_data=None, **kwargs):
        """Async make multiple orders (batch)"""
        path, params, extra_data = self._make_orders(orders_data, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_orders(self, orders_data, extra_data=None, **kwargs):
        """Cancel multiple orders (batch)"""
        request_type = "cancel_orders"
        params = orders_data
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "BATCH",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_orders(self, orders_data, extra_data=None, **kwargs):
        """Cancel multiple orders (batch)"""
        path, params, extra_data = self._cancel_orders(orders_data, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_orders(self, orders_data, extra_data=None, **kwargs):
        """Async cancel multiple orders (batch)"""
        path, params, extra_data = self._cancel_orders(orders_data, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _amend_orders(self, orders_data, extra_data=None, **kwargs):
        """Amend multiple orders (batch)"""
        request_type = "amend_orders"
        params = orders_data
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "BATCH",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def amend_orders(self, orders_data, extra_data=None, **kwargs):
        """Amend multiple orders (batch)"""
        path, params, extra_data = self._amend_orders(orders_data, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_amend_orders(self, orders_data, extra_data=None, **kwargs):
        """Async amend multiple orders (batch)"""
        path, params, extra_data = self._amend_orders(orders_data, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_fills(self, inst_type=None, uly=None, inst_id=None, order_id=None, after=None,
                   before=None, limit=None, extra_data=None, **kwargs):
        """Get fills"""
        request_type = "get_fills"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if order_id:
            params['ordId'] = order_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_fills(self, inst_type=None, uly=None, inst_id=None, order_id=None, after=None,
                  before=None, limit=None, extra_data=None, **kwargs):
        """Get fills"""
        path, params, extra_data = self._get_fills(inst_type, uly, inst_id, order_id, after,
                                                     before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_fills(self, inst_type=None, uly=None, inst_id=None, order_id=None, after=None,
                        before=None, limit=None, extra_data=None, **kwargs):
        """Async get fills"""
        path, params, extra_data = self._get_fills(inst_type, uly, inst_id, order_id, after,
                                                     before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _close_position(self, symbol, pos_side=None, mgn_mode=None, ccy=None, auto_cxl=False,
                        extra_data=None, **kwargs):
        """Close position"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "close_position"
        params = {
            'instId': request_symbol,
        }
        if pos_side:
            params['posSide'] = pos_side
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if ccy:
            params['ccy'] = ccy
        if auto_cxl:
            params['autoCxl'] = True
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def close_position(self, symbol, pos_side=None, mgn_mode=None, ccy=None, auto_cxl=False,
                       extra_data=None, **kwargs):
        """Close position"""
        path, params, extra_data = self._close_position(symbol, pos_side, mgn_mode, ccy,
                                                         auto_cxl, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_close_position(self, symbol, pos_side=None, mgn_mode=None, ccy=None, auto_cxl=False,
                             extra_data=None, **kwargs):
        """Async close position"""
        path, params, extra_data = self._close_position(symbol, pos_side, mgn_mode, ccy,
                                                         auto_cxl, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_fills_history(self, inst_type=None, uly=None, inst_id=None, order_id=None,
                           after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get fills history"""
        request_type = "get_fills_history"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if order_id:
            params['ordId'] = order_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_fills_history(self, inst_type=None, uly=None, inst_id=None, order_id=None,
                          after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get fills history"""
        path, params, extra_data = self._get_fills_history(inst_type, uly, inst_id, order_id,
                                                             after, before, limit, extra_data,
                                                             **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_fills_history(self, inst_type=None, uly=None, inst_id=None, order_id=None,
                                after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get fills history"""
        path, params, extra_data = self._get_fills_history(inst_type, uly, inst_id, order_id,
                                                             after, before, limit, extra_data,
                                                             **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_order_history_archive(self, inst_type=None, uly=None, inst_id=None, after=None,
                                    before=None, limit=None, extra_data=None, **kwargs):
        """Get order history archive"""
        request_type = "get_order_history_archive"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_order_history_archive(self, inst_type=None, uly=None, inst_id=None, after=None,
                                  before=None, limit=None, extra_data=None, **kwargs):
        """Get order history archive"""
        path, params, extra_data = self._get_order_history_archive(inst_type, uly, inst_id,
                                                                     after, before, limit,
                                                                     extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_order_history_archive(self, inst_type=None, uly=None, inst_id=None,
                                        after=None, before=None, limit=None,
                                        extra_data=None, **kwargs):
        """Async get order history archive"""
        path, params, extra_data = self._get_order_history_archive(inst_type, uly, inst_id,
                                                                     after, before, limit,
                                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_all_after(self, time_slug, extra_data=None, **kwargs):
        """Cancel all orders after time"""
        request_type = "cancel_all_after"
        params = {'timeSlug': time_slug}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_all_after(self, time_slug, extra_data=None, **kwargs):
        """Cancel all orders after time"""
        path, params, extra_data = self._cancel_all_after(time_slug, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all_after(self, time_slug, extra_data=None, **kwargs):
        """Async cancel all orders after time"""
        path, params, extra_data = self._cancel_all_after(time_slug, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _amend_algo_order(self, algo_id, inst_id, ccy=None, amend_px_on_trigger_type=None,
                          new_sz=None, new_px=None, new_tp_trigger_px=None, new_tp_ord_px=None,
                          new_sl_trigger_px=None, new_sl_ord_px=None, trigger_px=None,
                          order_type=None, extra_data=None, **kwargs):
        """Amend algo order"""
        request_symbol = self._params.get_symbol(inst_id)
        request_type = "amend_algo_order"
        params = {
            'algoId': algo_id,
            'instId': request_symbol,
        }
        if ccy:
            params['ccy'] = ccy
        if amend_px_on_trigger_type:
            params['amendPxOnTriggerType'] = amend_px_on_trigger_type
        if new_sz is not None:
            params['newSz'] = str(new_sz)
        if new_px is not None:
            params['newPx'] = str(new_px)
        if new_tp_trigger_px is not None:
            params['newTpTriggerPx'] = str(new_tp_trigger_px)
        if new_tp_ord_px is not None:
            params['newTpOrdPx'] = str(new_tp_ord_px)
        if new_sl_trigger_px is not None:
            params['newSlTriggerPx'] = str(new_sl_trigger_px)
        if new_sl_ord_px is not None:
            params['newSlOrdPx'] = str(new_sl_ord_px)
        if trigger_px is not None:
            params['triggerPx'] = str(trigger_px)
        if order_type:
            params['algoOrdType'] = order_type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def amend_algo_order(self, algo_id, inst_id, ccy=None, amend_px_on_trigger_type=None,
                         new_sz=None, new_px=None, new_tp_trigger_px=None, new_tp_ord_px=None,
                         new_sl_trigger_px=None, new_sl_ord_px=None, trigger_px=None,
                         order_type=None, extra_data=None, **kwargs):
        """Amend algo order"""
        path, params, extra_data = self._amend_algo_order(algo_id, inst_id, ccy,
                                                            amend_px_on_trigger_type, new_sz,
                                                            new_px, new_tp_trigger_px,
                                                            new_tp_ord_px, new_sl_trigger_px,
                                                            new_sl_ord_px, trigger_px,
                                                            order_type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_amend_algo_order(self, algo_id, inst_id, ccy=None, amend_px_on_trigger_type=None,
                               new_sz=None, new_px=None, new_tp_trigger_px=None,
                               new_tp_ord_px=None, new_sl_trigger_px=None, new_sl_ord_px=None,
                               trigger_px=None, order_type=None, extra_data=None, **kwargs):
        """Async amend algo order"""
        path, params, extra_data = self._amend_algo_order(algo_id, inst_id, ccy,
                                                            amend_px_on_trigger_type, new_sz,
                                                            new_px, new_tp_trigger_px,
                                                            new_tp_ord_px, new_sl_trigger_px,
                                                            new_sl_ord_px, trigger_px,
                                                            order_type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_algo_orders_pending(self, inst_type=None, uly=None, inst_id=None, algo_id=None,
                                  extra_data=None, **kwargs):
        """Get pending algo orders"""
        request_type = "get_algo_orders_pending"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params['instId'] = request_symbol
        if algo_id:
            params['algoId'] = algo_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_algo_orders_pending(self, inst_type=None, uly=None, inst_id=None, algo_id=None,
                                extra_data=None, **kwargs):
        """Get pending algo orders"""
        path, params, extra_data = self._get_algo_orders_pending(inst_type, uly, inst_id,
                                                                   algo_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_algo_orders_pending(self, inst_type=None, uly=None, inst_id=None,
                                      algo_id=None, extra_data=None, **kwargs):
        """Async get pending algo orders"""
        path, params, extra_data = self._get_algo_orders_pending(inst_type, uly, inst_id,
                                                                   algo_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_algo_order_history(self, inst_type=None, uly=None, inst_id=None, algo_id=None,
                                 after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get algo order history"""
        request_type = "get_algo_order_history"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params['instId'] = request_symbol
        if algo_id:
            params['algoId'] = algo_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_algo_order_history(self, inst_type=None, uly=None, inst_id=None, algo_id=None,
                               after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get algo order history"""
        path, params, extra_data = self._get_algo_order_history(inst_type, uly, inst_id,
                                                                  algo_id, after, before, limit,
                                                                  extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_algo_order_history(self, inst_type=None, uly=None, inst_id=None,
                                     algo_id=None, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Async get algo order history"""
        path, params, extra_data = self._get_algo_order_history(inst_type, uly, inst_id,
                                                                  algo_id, after, before, limit,
                                                                  extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_algo_order(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Get algo order details"""
        request_symbol = self._params.get_symbol(inst_id)
        request_type = "get_algo_order"
        params = {
            'algoId': algo_id,
            'instId': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_algo_order(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Get algo order details"""
        path, params, extra_data = self._get_algo_order(algo_id, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_algo_order(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Async get algo order details"""
        path, params, extra_data = self._get_algo_order(algo_id, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_all(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Cancel all orders"""
        request_type = "cancel_all"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params['instId'] = request_symbol
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_all(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Cancel all orders"""
        path, params, extra_data = self._cancel_all(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async cancel all orders"""
        path, params, extra_data = self._cancel_all(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_account_rate_limit(self, extra_data=None, **kwargs):
        """Get account rate limit"""
        request_type = "get_account_rate_limit"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_account_rate_limit(self, extra_data=None, **kwargs):
        """Get account rate limit"""
        path, params, extra_data = self._get_account_rate_limit(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_account_rate_limit(self, extra_data=None, **kwargs):
        """Async get account rate limit"""
        path, params, extra_data = self._get_account_rate_limit(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_easy_convert_currency_list(self, extra_data=None, **kwargs):
        """Get easy convert currency list"""
        request_type = "get_easy_convert_currency_list"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_easy_convert_currency_list(self, extra_data=None, **kwargs):
        """Get easy convert currency list"""
        path, params, extra_data = self._get_easy_convert_currency_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_easy_convert_currency_list(self, extra_data=None, **kwargs):
        """Async get easy convert currency list"""
        path, params, extra_data = self._get_easy_convert_currency_list(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _easy_convert(self, from_ccy, to_ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """Easy convert"""
        request_type = "easy_convert"
        params = {
            'fromCcy': from_ccy,
            'toCcy': to_ccy,
            'amt': str(amt),
        }
        if client_order_id:
            params['clientOrderId'] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": from_ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def easy_convert(self, from_ccy, to_ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """Easy convert"""
        path, params, extra_data = self._easy_convert(from_ccy, to_ccy, amt, client_order_id,
                                                       extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_easy_convert(self, from_ccy, to_ccy, amt, client_order_id=None,
                           extra_data=None, **kwargs):
        """Async easy convert"""
        path, params, extra_data = self._easy_convert(from_ccy, to_ccy, amt, client_order_id,
                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_easy_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get easy convert history"""
        request_type = "get_easy_convert_history"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_easy_convert_history(self, after=None, before=None, limit=None,
                                  extra_data=None, **kwargs):
        """Get easy convert history"""
        path, params, extra_data = self._get_easy_convert_history(after, before, limit,
                                                                    extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_easy_convert_history(self, after=None, before=None, limit=None,
                                       extra_data=None, **kwargs):
        """Async get easy convert history"""
        path, params, extra_data = self._get_easy_convert_history(after, before, limit,
                                                                    extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_one_click_repay_currency_list(self, extra_data=None, **kwargs):
        """Get one click repay currency list"""
        request_type = "get_one_click_repay_currency_list"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_one_click_repay_currency_list(self, extra_data=None, **kwargs):
        """Get one click repay currency list"""
        path, params, extra_data = self._get_one_click_repay_currency_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_one_click_repay_currency_list(self, extra_data=None, **kwargs):
        """Async get one click repay currency list"""
        path, params, extra_data = self._get_one_click_repay_currency_list(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _one_click_repay(self, ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """One click repay"""
        request_type = "one_click_repay"
        params = {
            'ccy': ccy,
            'amt': str(amt),
        }
        if client_order_id:
            params['clientOrderId'] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def one_click_repay(self, ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """One click repay"""
        path, params, extra_data = self._one_click_repay(ccy, amt, client_order_id,
                                                           extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_one_click_repay(self, ccy, amt, client_order_id=None,
                              extra_data=None, **kwargs):
        """Async one click repay"""
        path, params, extra_data = self._one_click_repay(ccy, amt, client_order_id,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_one_click_repay_history(self, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Get one click repay history"""
        request_type = "get_one_click_repay_history"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_one_click_repay_history(self, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Get one click repay history"""
        path, params, extra_data = self._get_one_click_repay_history(after, before, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_one_click_repay_history(self, after=None, before=None, limit=None,
                                          extra_data=None, **kwargs):
        """Async get one click repay history"""
        path, params, extra_data = self._get_one_click_repay_history(after, before, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _mass_cancel(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Mass cancel orders"""
        request_type = "mass_cancel"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params['instId'] = request_symbol
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def mass_cancel(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Mass cancel orders"""
        path, params, extra_data = self._mass_cancel(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_mass_cancel(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async mass cancel orders"""
        path, params, extra_data = self._mass_cancel(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _order_precheck(self, symbol, td_mode, ccy, side, order_type=None, sz=None, px=None,
                        extra_data=None, **kwargs):
        """Order precheck"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "order_precheck"
        params = {
            'instId': request_symbol,
            'tdMode': td_mode,
            'ccy': ccy,
            'side': side,
        }
        if order_type:
            params['ordType'] = order_type
        if sz is not None:
            params['sz'] = str(sz)
        if px is not None:
            params['px'] = str(px)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def order_precheck(self, symbol, td_mode, ccy, side, order_type=None, sz=None, px=None,
                       extra_data=None, **kwargs):
        """Order precheck"""
        path, params, extra_data = self._order_precheck(symbol, td_mode, ccy, side, order_type,
                                                         sz, px, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_order_precheck(self, symbol, td_mode, ccy, side, order_type=None, sz=None, px=None,
                             extra_data=None, **kwargs):
        """Async order precheck"""
        path, params, extra_data = self._order_precheck(symbol, td_mode, ccy, side, order_type,
                                                         sz, px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Position Builder APIs ====================

    def _position_builder(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                          margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """
        Position builder - Calculate the maximum open size
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID, e.g. BTC-USDT-SWAP
        :param ccy: Currency, e.g. BTC
        :param max_sz: Maximum open size
        :param margin_mode: Margin mode: cross, isolated, cash
        :param pos_side: Position side: long, short, net
        :param auto_sz: Whether to automatically calculate the size: true, false
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "position_builder"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if max_sz is not None:
            params['maxSz'] = str(max_sz)
        if margin_mode:
            params['mgnMode'] = margin_mode
        if pos_side:
            params['posSide'] = pos_side
        if auto_sz is not None:
            params['autoSz'] = str(auto_sz).lower()
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._position_builder_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _position_builder_normalize_function(input_data, extra_data):
        """Normalize position builder response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def position_builder(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                         margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Position builder - Calculate the maximum open size"""
        path, params, extra_data = self._position_builder(inst_type, uly, inst_id, ccy, max_sz,
                                                          margin_mode, pos_side, auto_sz,
                                                          extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_position_builder(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                               margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Async position builder - Calculate the maximum open size"""
        path, params, extra_data = self._position_builder(inst_type, uly, inst_id, ccy, max_sz,
                                                          margin_mode, pos_side, auto_sz,
                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _position_builder_trend(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                                margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """
        Position builder trend - Get position builder trend data
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID, e.g. BTC-USDT-SWAP
        :param ccy: Currency, e.g. BTC
        :param max_sz: Maximum open size
        :param margin_mode: Margin mode: cross, isolated, cash
        :param pos_side: Position side: long, short, net
        :param auto_sz: Whether to automatically calculate the size: true, false
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "position_builder_trend"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if max_sz is not None:
            params['maxSz'] = str(max_sz)
        if margin_mode:
            params['mgnMode'] = margin_mode
        if pos_side:
            params['posSide'] = pos_side
        if auto_sz is not None:
            params['autoSz'] = str(auto_sz).lower()
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._position_builder_trend_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _position_builder_trend_normalize_function(input_data, extra_data):
        """Normalize position builder trend response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def position_builder_trend(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                               margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Position builder trend - Get position builder trend data"""
        path, params, extra_data = self._position_builder_trend(inst_type, uly, inst_id, ccy, max_sz,
                                                                margin_mode, pos_side, auto_sz,
                                                                extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_position_builder_trend(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                                     margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Async position builder trend - Get position builder trend data"""
        path, params, extra_data = self._position_builder_trend(inst_type, uly, inst_id, ccy, max_sz,
                                                                margin_mode, pos_side, auto_sz,
                                                                extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Trading Statistics APIs ====================

    @staticmethod
    def _get_support_coin_normalize_function(input_data, extra_data):
        """Normalize get_support_coin response.
        API returns data with different coin types grouped by category.
        Response format: {"code": "0", "data": {"contract": [...], "option": [...], "spot": [...]}}"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return {}, status
        # Data is already a dict with keys: contract, option, spot
        data = input_data['data']
        # Return dict as-is with keys: contract, option, spot
        return data, status

    def _get_support_coin(self, extra_data=None, **kwargs):
        """Get support coin"""
        request_type = "get_support_coin"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_support_coin_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_support_coin(self, extra_data=None, **kwargs):
        """Get support coin"""
        path, params, extra_data = self._get_support_coin(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_support_coin(self, extra_data=None, **kwargs):
        """Async get support coin"""
        path, params, extra_data = self._get_support_coin(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_contract_oi_history(self, ccy=None, uly=None, inst_id=None, after=None, before=None,
                                  limit=None, period=None, extra_data=None, **kwargs):
        """Get contract open interest history"""
        request_type = "get_contract_oi_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_contract_oi_history(self, ccy=None, uly=None, inst_id=None, after=None, before=None,
                                limit=None, period=None, extra_data=None, **kwargs):
        """Get contract open interest history"""
        path, params, extra_data = self._get_contract_oi_history(ccy, uly, inst_id, after,
                                                                   before, limit, period,
                                                                   extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_contract_oi_history(self, ccy=None, uly=None, inst_id=None, after=None,
                                       before=None, limit=None, period=None,
                                       extra_data=None, **kwargs):
        """Async get contract open interest history"""
        path, params, extra_data = self._get_contract_oi_history(ccy, uly, inst_id, after,
                                                                   before, limit, period,
                                                                   extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_taker_volume(self, ccy=None, uly=None, inst_id=None, begin=None, end=None,
                          period=None, extra_data=None, **kwargs):
        """Get taker volume"""
        request_type = "get_taker_volume"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_taker_volume(self, ccy=None, uly=None, inst_id=None, begin=None, end=None,
                         period=None, extra_data=None, **kwargs):
        """Get taker volume"""
        path, params, extra_data = self._get_taker_volume(ccy, uly, inst_id, begin, end,
                                                            period, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_taker_volume(self, ccy=None, uly=None, inst_id=None, begin=None, end=None,
                               period=None, extra_data=None, **kwargs):
        """Async get taker volume"""
        path, params, extra_data = self._get_taker_volume(ccy, uly, inst_id, begin, end,
                                                            period, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_long_short_ratio(self, ccy=None, begin=None, end=None, period=None,
                               extra_data=None, **kwargs):
        """Get long short ratio"""
        request_type = "get_long_short_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_long_short_ratio(self, ccy=None, begin=None, end=None, period=None,
                             extra_data=None, **kwargs):
        """Get long short ratio"""
        path, params, extra_data = self._get_long_short_ratio(ccy, begin, end, period,
                                                                extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_long_short_ratio(self, ccy=None, begin=None, end=None, period=None,
                                   extra_data=None, **kwargs):
        """Async get long short ratio"""
        path, params, extra_data = self._get_long_short_ratio(ccy, begin, end, period,
                                                                extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_long_short_ratio_top_trader(self, ccy=None, begin=None, end=None, period=None,
                                          extra_data=None, **kwargs):
        """Get long short ratio (top trader)"""
        request_type = "get_long_short_ratio_top_trader"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_long_short_ratio_top_trader(self, ccy=None, begin=None, end=None, period=None,
                                         extra_data=None, **kwargs):
        """Get long short ratio (top trader)"""
        path, params, extra_data = self._get_long_short_ratio_top_trader(ccy, begin, end,
                                                                          period, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_long_short_ratio_top_trader(self, ccy=None, begin=None, end=None,
                                               period=None, extra_data=None, **kwargs):
        """Async get long short ratio (top trader)"""
        path, params, extra_data = self._get_long_short_ratio_top_trader(ccy, begin, end,
                                                                          period, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_contract_long_short_ratio(self, ccy=None, uly=None, inst_id=None, begin=None,
                                        end=None, period=None, limit=None, extra_data=None, **kwargs):
        """Get contract long short ratio"""
        request_type = "get_contract_long_short_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_contract_long_short_ratio(self, ccy=None, uly=None, inst_id=None, begin=None,
                                       end=None, period=None, limit=None, extra_data=None, **kwargs):
        """Get contract long short ratio"""
        path, params, extra_data = self._get_contract_long_short_ratio(ccy, uly, inst_id, begin,
                                                                       end, period, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_contract_long_short_ratio(self, ccy=None, uly=None, inst_id=None, begin=None,
                                             end=None, period=None, limit=None, extra_data=None, **kwargs):
        """Async get contract long short ratio"""
        path, params, extra_data = self._get_contract_long_short_ratio(ccy, uly, inst_id, begin,
                                                                       end, period, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_put_call_ratio(self, ccy=None, uly=None, begin=None, end=None, period=None,
                            extra_data=None, **kwargs):
        """Get put call ratio"""
        request_type = "get_put_call_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_put_call_ratio(self, ccy=None, uly=None, begin=None, end=None, period=None,
                           extra_data=None, **kwargs):
        """Get put call ratio"""
        path, params, extra_data = self._get_put_call_ratio(ccy, uly, begin, end, period,
                                                              extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_put_call_ratio(self, ccy=None, uly=None, begin=None, end=None, period=None,
                                  extra_data=None, **kwargs):
        """Async get put call ratio"""
        path, params, extra_data = self._get_put_call_ratio(ccy, uly, begin, end, period,
                                                              extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Grid Trading APIs ====================

    def _grid_order_algo(self, inst_id, td_mode, ccy, algo_algo_type, max_px, min_px, grid_num,
                         run_type=None, sz=None, base_sz=None, trigger_px=None, trigger_time=None,
                         attach_algo_cl_or=None, attach_algo_om_trigger_px=None, tp_px=None,
                         tp_trigger_px=None, sl_px=None, sl_trigger_px=None, fast_callback_speed=None,
                         extra_data=None, **kwargs):
        """Create grid strategy order"""
        request_type = "grid_order_algo"
        params = {
            'instId': inst_id,
            'tdMode': td_mode,
            'algoAlgoType': algo_algo_type,  # "grid_regular" or "grid_contract"
            'maxPx': max_px,
            'minPx': min_px,
            'gridNum': grid_num,
            'runType': run_type or '1',  # 1: single, 2: neutral
        }
        if ccy:
            params['ccy'] = ccy
        if sz is not None:
            params['sz'] = sz
        if base_sz is not None:
            params['baseSz'] = base_sz
        if trigger_px is not None:
            params['triggerPx'] = trigger_px
        if trigger_time is not None:
            params['triggerTime'] = trigger_time
        if attach_algo_cl_or is not None:
            params['attachAlgoClOrd'] = attach_algo_cl_or
        if attach_algo_om_trigger_px is not None:
            params['attachAlgoOmTriggerPx'] = attach_algo_om_trigger_px
        if tp_px is not None:
            params['tpPx'] = tp_px
        if tp_trigger_px is not None:
            params['tpTriggerPx'] = tp_trigger_px
        if sl_px is not None:
            params['slPx'] = sl_px
        if sl_trigger_px is not None:
            params['slTriggerPx'] = sl_trigger_px
        if fast_callback_speed is not None:
            params['fastCallbackSpeed'] = fast_callback_speed
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_order_algo(self, inst_id, td_mode, ccy, algo_algo_type, max_px, min_px, grid_num,
                        run_type=None, sz=None, base_sz=None, trigger_px=None, trigger_time=None,
                        attach_algo_cl_or=None, attach_algo_om_trigger_px=None, tp_px=None,
                        tp_trigger_px=None, sl_px=None, sl_trigger_px=None, fast_callback_speed=None,
                        extra_data=None, **kwargs):
        """Create grid strategy order"""
        path, params, extra_data = self._grid_order_algo(
            inst_id, td_mode, ccy, algo_algo_type, max_px, min_px, grid_num,
            run_type, sz, base_sz, trigger_px, trigger_time,
            attach_algo_cl_or, attach_algo_om_trigger_px, tp_px,
            tp_trigger_px, sl_px, sl_trigger_px, fast_callback_speed,
            extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_order_algo(self, inst_id, td_mode, ccy, algo_algo_type, max_px, min_px, grid_num,
                              run_type=None, sz=None, base_sz=None, trigger_px=None, trigger_time=None,
                              attach_algo_cl_or=None, attach_algo_om_trigger_px=None, tp_px=None,
                              tp_trigger_px=None, sl_px=None, sl_trigger_px=None, fast_callback_speed=None,
                              extra_data=None, **kwargs):
        """Async create grid strategy order"""
        path, params, extra_data = self._grid_order_algo(
            inst_id, td_mode, ccy, algo_algo_type, max_px, min_px, grid_num,
            run_type, sz, base_sz, trigger_px, trigger_time,
            attach_algo_cl_or, attach_algo_om_trigger_px, tp_px,
            tp_trigger_px, sl_px, sl_trigger_px, fast_callback_speed,
            extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_amend_order_algo(self, algo_id, inst_id, trigger_px=None, max_px=None, min_px=None,
                                tp_px=None, tp_trigger_px=None, sl_px=None, sl_trigger_px=None,
                                extra_data=None, **kwargs):
        """Amend grid strategy order"""
        request_type = "grid_amend_order_algo"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        if trigger_px is not None:
            params['triggerPx'] = trigger_px
        if max_px is not None:
            params['maxPx'] = max_px
        if min_px is not None:
            params['minPx'] = min_px
        if tp_px is not None:
            params['tpPx'] = tp_px
        if tp_trigger_px is not None:
            params['tpTriggerPx'] = tp_trigger_px
        if sl_px is not None:
            params['slPx'] = sl_px
        if sl_trigger_px is not None:
            params['slTriggerPx'] = sl_trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_amend_order_algo(self, algo_id, inst_id, trigger_px=None, max_px=None, min_px=None,
                               tp_px=None, tp_trigger_px=None, sl_px=None, sl_trigger_px=None,
                               extra_data=None, **kwargs):
        """Amend grid strategy order"""
        path, params, extra_data = self._grid_amend_order_algo(
            algo_id, inst_id, trigger_px, max_px, min_px,
            tp_px, tp_trigger_px, sl_px, sl_trigger_px,
            extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_amend_order_algo(self, algo_id, inst_id, trigger_px=None, max_px=None, min_px=None,
                                     tp_px=None, tp_trigger_px=None, sl_px=None, sl_trigger_px=None,
                                     extra_data=None, **kwargs):
        """Async amend grid strategy order"""
        path, params, extra_data = self._grid_amend_order_algo(
            algo_id, inst_id, trigger_px, max_px, min_px,
            tp_px, tp_trigger_px, sl_px, sl_trigger_px,
            extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_stop_order_algo(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Stop grid strategy order"""
        request_type = "grid_stop_order_algo"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_stop_order_algo(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Stop grid strategy order"""
        path, params, extra_data = self._grid_stop_order_algo(algo_id, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_stop_order_algo(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Async stop grid strategy order"""
        path, params, extra_data = self._grid_stop_order_algo(algo_id, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_orders_algo_pending(self, inst_type=None, inst_id=None, algo_id=None, after=None,
                                   before=None, limit=None, extra_data=None, **kwargs):
        """Get grid strategy pending orders"""
        request_type = "grid_orders_algo_pending"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if algo_id:
            params['algoId'] = algo_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_orders_algo_pending(self, inst_type=None, inst_id=None, algo_id=None, after=None,
                                  before=None, limit=None, extra_data=None, **kwargs):
        """Get grid strategy pending orders"""
        path, params, extra_data = self._grid_orders_algo_pending(
            inst_type, inst_id, algo_id, after, before, limit,
            extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_orders_algo_pending(self, inst_type=None, inst_id=None, algo_id=None, after=None,
                                        before=None, limit=None, extra_data=None, **kwargs):
        """Async get grid strategy pending orders"""
        path, params, extra_data = self._grid_orders_algo_pending(
            inst_type, inst_id, algo_id, after, before, limit,
            extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_orders_algo_history(self, inst_type=None, inst_id=None, algo_id=None, state=None,
                                   after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get grid strategy order history"""
        request_type = "grid_orders_algo_history"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if algo_id:
            params['algoId'] = algo_id
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
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_orders_algo_history(self, inst_type=None, inst_id=None, algo_id=None, state=None,
                                 after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get grid strategy order history"""
        path, params, extra_data = self._grid_orders_algo_history(
            inst_type, inst_id, algo_id, state, after, before, limit,
            extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_orders_algo_history(self, inst_type=None, inst_id=None, algo_id=None, state=None,
                                       after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get grid strategy order history"""
        path, params, extra_data = self._grid_orders_algo_history(
            inst_type, inst_id, algo_id, state, after, before, limit,
            extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_amend_order_algo_basic(self, algo_id, inst_id, max_px=None, min_px=None,
                                      tp_px=None, tp_trigger_px=None, sl_px=None,
                                      sl_trigger_px=None, extra_data=None, **kwargs):
        """Amend grid order (basic parameters) - 修改网格委托(基础参数)"""
        request_type = "grid_amend_order_algo_basic"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        if max_px is not None:
            params['maxPx'] = max_px
        if min_px is not None:
            params['minPx'] = min_px
        if tp_px is not None:
            params['tpPx'] = tp_px
        if tp_trigger_px is not None:
            params['tpTriggerPx'] = tp_trigger_px
        if sl_px is not None:
            params['slPx'] = sl_px
        if sl_trigger_px is not None:
            params['slTriggerPx'] = sl_trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_amend_order_algo_basic(self, algo_id, inst_id, max_px=None, min_px=None,
                                     tp_px=None, tp_trigger_px=None, sl_px=None,
                                     sl_trigger_px=None, extra_data=None, **kwargs):
        """Amend grid order (basic parameters) - 修改网格委托(基础参数)"""
        path, params, extra_data = self._grid_amend_order_algo_basic(
            algo_id, inst_id, max_px, min_px, tp_px, tp_trigger_px,
            sl_px, sl_trigger_px, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_amend_order_algo_basic(self, algo_id, inst_id, max_px=None, min_px=None,
                                          tp_px=None, tp_trigger_px=None, sl_px=None,
                                          sl_trigger_px=None, extra_data=None, **kwargs):
        """Async amend grid order (basic parameters)"""
        path, params, extra_data = self._grid_amend_order_algo_basic(
            algo_id, inst_id, max_px, min_px, tp_px, tp_trigger_px,
            sl_px, sl_trigger_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_close_position(self, algo_id, inst_id, ccy=None, margin=None,
                             extra_data=None, **kwargs):
        """Close futures grid position - 合约网格平仓"""
        request_type = "grid_close_position"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        if ccy:
            params['ccy'] = ccy
        if margin is not None:
            params['margin'] = margin
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_close_position(self, algo_id, inst_id, ccy=None, margin=None,
                            extra_data=None, **kwargs):
        """Close futures grid position - 合约网格平仓"""
        path, params, extra_data = self._grid_close_position(
            algo_id, inst_id, ccy, margin, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_close_position(self, algo_id, inst_id, ccy=None, margin=None,
                                  extra_data=None, **kwargs):
        """Async close futures grid position"""
        path, params, extra_data = self._grid_close_position(
            algo_id, inst_id, ccy, margin, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_cancel_close_order(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Cancel futures grid close order - 撤销合约网格平仓单"""
        request_type = "grid_cancel_close_order"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_cancel_close_order(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Cancel futures grid close order - 撤销合约网格平仓单"""
        path, params, extra_data = self._grid_cancel_close_order(
            algo_id, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_cancel_close_order(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Async cancel futures grid close order"""
        path, params, extra_data = self._grid_cancel_close_order(
            algo_id, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_order_instant_trigger(self, algo_id, inst_id, trigger_px=None,
                                     extra_data=None, **kwargs):
        """Grid order instant trigger - 网格委托立即触发"""
        request_type = "grid_order_instant_trigger"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        if trigger_px is not None:
            params['triggerPx'] = trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_order_instant_trigger(self, algo_id, inst_id, trigger_px=None,
                                   extra_data=None, **kwargs):
        """Grid order instant trigger - 网格委托立即触发"""
        path, params, extra_data = self._grid_order_instant_trigger(
            algo_id, inst_id, trigger_px, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_order_instant_trigger(self, algo_id, inst_id, trigger_px=None,
                                         extra_data=None, **kwargs):
        """Async grid order instant trigger"""
        path, params, extra_data = self._grid_order_instant_trigger(
            algo_id, inst_id, trigger_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_orders_algo_details(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Get grid order details - 获取网格委托详情"""
        request_type = "grid_orders_algo_details"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_orders_algo_details(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Get grid order details - 获取网格委托详情"""
        path, params, extra_data = self._grid_orders_algo_details(
            algo_id, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_orders_algo_details(self, algo_id, inst_id, extra_data=None, **kwargs):
        """Async get grid order details"""
        path, params, extra_data = self._grid_orders_algo_details(
            algo_id, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_sub_orders(self, algo_id, inst_id, type=None, ord_id=None, after=None,
                         before=None, limit=None, extra_data=None, **kwargs):
        """Get grid sub orders - 获取网格委托子订单"""
        request_type = "grid_sub_orders"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
        }
        if type is not None:
            params['type'] = type
        if ord_id:
            params['ordId'] = ord_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_sub_orders(self, algo_id, inst_id, type=None, ord_id=None, after=None,
                        before=None, limit=None, extra_data=None, **kwargs):
        """Get grid sub orders - 获取网格委托子订单"""
        path, params, extra_data = self._grid_sub_orders(
            algo_id, inst_id, type, ord_id, after, before, limit,
            extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_sub_orders(self, algo_id, inst_id, type=None, ord_id=None, after=None,
                              before=None, limit=None, extra_data=None, **kwargs):
        """Async get grid sub orders"""
        path, params, extra_data = self._grid_sub_orders(
            algo_id, inst_id, type, ord_id, after, before, limit,
            extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_positions(self, inst_type=None, inst_id=None, algo_id=None, extra_data=None, **kwargs):
        """Get grid positions - 获取网格委托持仓"""
        request_type = "grid_positions"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if algo_id:
            params['algoId'] = algo_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_positions(self, inst_type=None, inst_id=None, algo_id=None, extra_data=None, **kwargs):
        """Get grid positions - 获取网格委托持仓"""
        path, params, extra_data = self._grid_positions(
            inst_type, inst_id, algo_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_positions(self, inst_type=None, inst_id=None, algo_id=None, extra_data=None, **kwargs):
        """Async get grid positions"""
        path, params, extra_data = self._grid_positions(
            inst_type, inst_id, algo_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_withdraw_income(self, algo_id, inst_id, amt, ccy=None, type=None,
                              extra_data=None, **kwargs):
        """Spot grid withdraw income - 现货网格提取利润"""
        request_type = "grid_withdraw_income"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
            'amt': amt,
        }
        if ccy:
            params['ccy'] = ccy
        if type is not None:
            params['type'] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_withdraw_income(self, algo_id, inst_id, amt, ccy=None, type=None,
                             extra_data=None, **kwargs):
        """Spot grid withdraw income - 现货网格提取利润"""
        path, params, extra_data = self._grid_withdraw_income(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_withdraw_income(self, algo_id, inst_id, amt, ccy=None, type=None,
                                   extra_data=None, **kwargs):
        """Async spot grid withdraw income"""
        path, params, extra_data = self._grid_withdraw_income(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_compute_margin_balance(self, inst_id, td_mode, ccy, algo_ords_type, sz, max_px=None,
                                     min_px=None, grid_num=None, trigger_px=None,
                                     extra_data=None, **kwargs):
        """Compute margin balance - 计算保证金余额"""
        request_type = "grid_compute_margin_balance"
        params = {
            'instId': inst_id,
            'tdMode': td_mode,
            'ccy': ccy,
            'algoOrdsType': algo_ords_type,
            'sz': sz,
        }
        if max_px is not None:
            params['maxPx'] = max_px
        if min_px is not None:
            params['minPx'] = min_px
        if grid_num is not None:
            params['gridNum'] = grid_num
        if trigger_px is not None:
            params['triggerPx'] = trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_compute_margin_balance(self, inst_id, td_mode, ccy, algo_ords_type, sz, max_px=None,
                                    min_px=None, grid_num=None, trigger_px=None,
                                    extra_data=None, **kwargs):
        """Compute margin balance - 计算保证金余额"""
        path, params, extra_data = self._grid_compute_margin_balance(
            inst_id, td_mode, ccy, algo_ords_type, sz, max_px,
            min_px, grid_num, trigger_px, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_compute_margin_balance(self, inst_id, td_mode, ccy, algo_ords_type, sz,
                                          max_px=None, min_px=None, grid_num=None,
                                          trigger_px=None, extra_data=None, **kwargs):
        """Async compute margin balance"""
        path, params, extra_data = self._grid_compute_margin_balance(
            inst_id, td_mode, ccy, algo_ords_type, sz, max_px,
            min_px, grid_num, trigger_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_margin_balance(self, algo_id, inst_id, amt, ccy=None, type=None,
                            extra_data=None, **kwargs):
        """Adjust margin - 调整保证金"""
        request_type = "grid_margin_balance"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
            'amt': amt,
        }
        if ccy:
            params['ccy'] = ccy
        if type is not None:
            params['type'] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_margin_balance(self, algo_id, inst_id, amt, ccy=None, type=None,
                            extra_data=None, **kwargs):
        """Adjust margin - 调整保证金"""
        path, params, extra_data = self._grid_margin_balance(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_margin_balance(self, algo_id, inst_id, amt, ccy=None, type=None,
                                  extra_data=None, **kwargs):
        """Async adjust margin"""
        path, params, extra_data = self._grid_margin_balance(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_add_investment(self, algo_id, inst_id, amt, ccy=None, type=None,
                            extra_data=None, **kwargs):
        """Add investment - 增加投入币数量"""
        request_type = "grid_add_investment"
        params = {
            'algoId': algo_id,
            'instId': inst_id,
            'amt': amt,
        }
        if ccy:
            params['ccy'] = ccy
        if type is not None:
            params['type'] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_add_investment(self, algo_id, inst_id, amt, ccy=None, type=None,
                            extra_data=None, **kwargs):
        """Add investment - 增加投入币数量"""
        path, params, extra_data = self._grid_add_investment(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_add_investment(self, algo_id, inst_id, amt, ccy=None, type=None,
                                  extra_data=None, **kwargs):
        """Async add investment"""
        path, params, extra_data = self._grid_add_investment(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_get_ai_param(self, inst_id, algo_algo_type, max_px=None, min_px=None,
                          grid_num=None, extra_data=None, **kwargs):
        """Get grid AI parameters - 获取网格AI参数"""
        request_type = "grid_get_ai_param"
        params = {
            'instId': inst_id,
            'algoAlgoType': algo_algo_type,
        }
        if max_px is not None:
            params['maxPx'] = max_px
        if min_px is not None:
            params['minPx'] = min_px
        if grid_num is not None:
            params['gridNum'] = grid_num
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_get_ai_param(self, inst_id, algo_algo_type, max_px=None, min_px=None,
                          grid_num=None, extra_data=None, **kwargs):
        """Get grid AI parameters - 获取网格AI参数"""
        path, params, extra_data = self._grid_get_ai_param(
            inst_id, algo_algo_type, max_px, min_px, grid_num,
            extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_get_ai_param(self, inst_id, algo_algo_type, max_px=None, min_px=None,
                                grid_num=None, extra_data=None, **kwargs):
        """Async get grid AI parameters"""
        path, params, extra_data = self._grid_get_ai_param(
            inst_id, algo_algo_type, max_px, min_px, grid_num,
            extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_compute_min_investment(self, inst_id, algo_algo_type, max_px, min_px, grid_num,
                                     run_type=None, trigger_px=None, extra_data=None, **kwargs):
        """Compute minimum investment - 计算最小投入金额"""
        request_type = "grid_compute_min_investment"
        params = {
            'instId': inst_id,
            'algoAlgoType': algo_algo_type,
            'maxPx': max_px,
            'minPx': min_px,
            'gridNum': grid_num,
        }
        if run_type is not None:
            params['runType'] = run_type
        if trigger_px is not None:
            params['triggerPx'] = trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_compute_min_investment(self, inst_id, algo_algo_type, max_px, min_px, grid_num,
                                    run_type=None, trigger_px=None, extra_data=None, **kwargs):
        """Compute minimum investment - 计算最小投入金额"""
        path, params, extra_data = self._grid_compute_min_investment(
            inst_id, algo_algo_type, max_px, min_px, grid_num,
            run_type, trigger_px, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_compute_min_investment(self, inst_id, algo_algo_type, max_px, min_px,
                                          grid_num, run_type=None, trigger_px=None,
                                          extra_data=None, **kwargs):
        """Async compute minimum investment"""
        path, params, extra_data = self._grid_compute_min_investment(
            inst_id, algo_algo_type, max_px, min_px, grid_num,
            run_type, trigger_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_rsi_back_testing(self, inst_id, algo_algo_type, max_px, min_px, grid_num,
                               time_type=None, extra_data=None, **kwargs):
        """RSI back testing - RSI回测"""
        request_type = "grid_rsi_back_testing"
        params = {
            'instId': inst_id,
            'algoAlgoType': algo_algo_type,
            'maxPx': max_px,
            'minPx': min_px,
            'gridNum': grid_num,
        }
        if time_type is not None:
            params['timeType'] = time_type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_rsi_back_testing(self, inst_id, algo_algo_type, max_px, min_px, grid_num,
                              time_type=None, extra_data=None, **kwargs):
        """RSI back testing - RSI回测"""
        path, params, extra_data = self._grid_rsi_back_testing(
            inst_id, algo_algo_type, max_px, min_px, grid_num,
            time_type, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_rsi_back_testing(self, inst_id, algo_algo_type, max_px, min_px, grid_num,
                                    time_type=None, extra_data=None, **kwargs):
        """Async RSI back testing"""
        path, params, extra_data = self._grid_rsi_back_testing(
            inst_id, algo_algo_type, max_px, min_px, grid_num,
            time_type, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _grid_max_grid_quantity(self, inst_id, algo_algo_type, extra_data=None, **kwargs):
        """Get max grid quantity - 最大网格数量"""
        request_type = "grid_max_grid_quantity"
        params = {
            'instId': inst_id,
            'algoAlgoType': algo_algo_type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_max_grid_quantity(self, inst_id, algo_algo_type, extra_data=None, **kwargs):
        """Get max grid quantity - 最大网格数量"""
        path, params, extra_data = self._grid_max_grid_quantity(
            inst_id, algo_algo_type, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_max_grid_quantity(self, inst_id, algo_algo_type, extra_data=None, **kwargs):
        """Async get max grid quantity"""
        path, params, extra_data = self._grid_max_grid_quantity(
            inst_id, algo_algo_type, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Funding Account (P2) - Remaining Interfaces ====================

    def _get_exchange_list(self, ccy=None, extra_data=None, **kwargs):
        """Get exchange list"""
        request_type = "get_exchange_list"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_exchange_list(self, ccy=None, extra_data=None, **kwargs):
        """Get exchange list"""
        path, params, extra_data = self._get_exchange_list(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_exchange_list(self, ccy=None, extra_data=None, **kwargs):
        """Async get exchange list"""
        path, params, extra_data = self._get_exchange_list(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _apply_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Apply for monthly statement (last year)"""
        request_type = "apply_monthly_statement"
        params = {}
        if month:
            params['month'] = month
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def apply_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Apply for monthly statement (last year)"""
        path, params, extra_data = self._apply_monthly_statement(month, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_apply_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Async apply for monthly statement (last year)"""
        path, params, extra_data = self._apply_monthly_statement(month, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Get monthly statement (last year)"""
        request_type = "get_monthly_statement"
        params = {}
        if month:
            params['month'] = month
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Get monthly statement (last year)"""
        path, params, extra_data = self._get_monthly_statement(month, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Async get monthly statement (last year)"""
        path, params, extra_data = self._get_monthly_statement(month, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_convert_currencies(self, extra_data=None, **kwargs):
        """Get convert currencies list"""
        request_type = "get_convert_currencies"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_currencies(self, extra_data=None, **kwargs):
        """Get convert currencies list"""
        path, params, extra_data = self._get_convert_currencies(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_currencies(self, extra_data=None, **kwargs):
        """Async get convert currencies list"""
        path, params, extra_data = self._get_convert_currencies(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_convert_currency_pair(self, from_ccy=None, to_ccy=None, extra_data=None, **kwargs):
        """Get convert currency pair"""
        request_type = "get_convert_currency_pair"
        params = {}
        if from_ccy:
            params['fromCcy'] = from_ccy
        if to_ccy:
            params['toCcy'] = to_ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{from_ccy or 'ALL'}-{to_ccy or 'ALL'}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_currency_pair(self, from_ccy=None, to_ccy=None, extra_data=None, **kwargs):
        """Get convert currency pair"""
        path, params, extra_data = self._get_convert_currency_pair(from_ccy, to_ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_currency_pair(self, from_ccy=None, to_ccy=None, extra_data=None, **kwargs):
        """Async get convert currency pair"""
        path, params, extra_data = self._get_convert_currency_pair(from_ccy, to_ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _convert_estimate_quote(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert estimate quote"""
        request_type = "convert_estimate_quote"
        params = {
            'fromCcy': from_ccy,
            'toCcy': to_ccy,
            'amount': str(amount),
            'type': type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{from_ccy}-{to_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def convert_estimate_quote(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert estimate quote"""
        path, params, extra_data = self._convert_estimate_quote(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_convert_estimate_quote(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Async convert estimate quote"""
        path, params, extra_data = self._convert_estimate_quote(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _convert_trade(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert trade"""
        request_type = "convert_trade"
        params = {
            'fromCcy': from_ccy,
            'toCcy': to_ccy,
            'amount': str(amount),
            'type': type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{from_ccy}-{to_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def convert_trade(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert trade"""
        path, params, extra_data = self._convert_trade(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_convert_trade(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Async convert trade"""
        path, params, extra_data = self._convert_trade(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get convert history"""
        request_type = "get_convert_history"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get convert history"""
        path, params, extra_data = self._get_convert_history(after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get convert history"""
        path, params, extra_data = self._get_convert_history(after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get deposit payment methods"""
        request_type = "get_deposit_payment_methods"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get deposit payment methods"""
        path, params, extra_data = self._get_deposit_payment_methods(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Async get deposit payment methods"""
        path, params, extra_data = self._get_deposit_payment_methods(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get withdrawal payment methods"""
        request_type = "get_withdrawal_payment_methods"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get withdrawal payment methods"""
        path, params, extra_data = self._get_withdrawal_payment_methods(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Async get withdrawal payment methods"""
        path, params, extra_data = self._get_withdrawal_payment_methods(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _create_withdrawal_order(self, ccy, amt, dest, to_addr=None, pwd=None, fee=None, chain=None,
                                  area_code=None, extra_data=None, **kwargs):
        """Create withdrawal order"""
        request_type = "create_withdrawal_order"
        params = {
            'ccy': ccy,
            'amt': str(amt),
            'dest': dest,
        }
        if to_addr:
            params['toAddr'] = to_addr
        if pwd:
            params['pwd'] = pwd
        if fee is not None:
            params['fee'] = str(fee)
        if chain:
            params['chain'] = chain
        if area_code:
            params['areaCode'] = area_code
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def create_withdrawal_order(self, ccy, amt, dest, to_addr=None, pwd=None, fee=None, chain=None,
                                 area_code=None, extra_data=None, **kwargs):
        """Create withdrawal order"""
        path, params, extra_data = self._create_withdrawal_order(ccy, amt, dest, to_addr, pwd, fee, chain,
                                                                  area_code, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_withdrawal_order(self, ccy, amt, dest, to_addr=None, pwd=None, fee=None, chain=None,
                                       area_code=None, extra_data=None, **kwargs):
        """Async create withdrawal order"""
        path, params, extra_data = self._create_withdrawal_order(ccy, amt, dest, to_addr, pwd, fee, chain,
                                                                  area_code, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_withdrawal_order(self, wd_id, extra_data=None, **kwargs):
        """Cancel withdrawal order"""
        request_type = "cancel_withdrawal_order"
        params = {
            'wdId': wd_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_withdrawal_order(self, wd_id, extra_data=None, **kwargs):
        """Cancel withdrawal order"""
        path, params, extra_data = self._cancel_withdrawal_order(wd_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_withdrawal_order(self, wd_id, extra_data=None, **kwargs):
        """Async cancel withdrawal order"""
        path, params, extra_data = self._cancel_withdrawal_order(wd_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_order_history(self, ccy=None, wd_id=None, after=None, before=None, limit=None,
                                       extra_data=None, **kwargs):
        """Get withdrawal order history"""
        request_type = "get_withdrawal_order_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if wd_id:
            params['wdId'] = wd_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_order_history(self, ccy=None, wd_id=None, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Get withdrawal order history"""
        path, params, extra_data = self._get_withdrawal_order_history(ccy, wd_id, after, before, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_order_history(self, ccy=None, wd_id=None, after=None, before=None, limit=None,
                                            extra_data=None, **kwargs):
        """Async get withdrawal order history"""
        path, params, extra_data = self._get_withdrawal_order_history(ccy, wd_id, after, before, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_order_detail(self, wd_id, extra_data=None, **kwargs):
        """Get withdrawal order detail"""
        request_type = "get_withdrawal_order_detail"
        params = {
            'wdId': wd_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_order_detail(self, wd_id, extra_data=None, **kwargs):
        """Get withdrawal order detail"""
        path, params, extra_data = self._get_withdrawal_order_detail(wd_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_order_detail(self, wd_id, extra_data=None, **kwargs):
        """Async get withdrawal order detail"""
        path, params, extra_data = self._get_withdrawal_order_detail(wd_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_order_history(self, ccy=None, dep_id=None, after=None, before=None, limit=None,
                                    extra_data=None, **kwargs):
        """Get deposit order history"""
        request_type = "get_deposit_order_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if dep_id:
            params['depId'] = dep_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_order_history(self, ccy=None, dep_id=None, after=None, before=None, limit=None,
                                   extra_data=None, **kwargs):
        """Get deposit order history"""
        path, params, extra_data = self._get_deposit_order_history(ccy, dep_id, after, before, limit,
                                                                    extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_order_history(self, ccy=None, dep_id=None, after=None, before=None, limit=None,
                                         extra_data=None, **kwargs):
        """Async get deposit order history"""
        path, params, extra_data = self._get_deposit_order_history(ccy, dep_id, after, before, limit,
                                                                    extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_order_detail(self, dep_id, extra_data=None, **kwargs):
        """Get deposit order detail"""
        request_type = "get_deposit_order_detail"
        params = {
            'depId': dep_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_order_detail(self, dep_id, extra_data=None, **kwargs):
        """Get deposit order detail"""
        path, params, extra_data = self._get_deposit_order_detail(dep_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_order_detail(self, dep_id, extra_data=None, **kwargs):
        """Async get deposit order detail"""
        path, params, extra_data = self._get_deposit_order_detail(dep_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_currencies(self, extra_data=None, **kwargs):
        """Get buy/sell currencies list"""
        request_type = "get_buy_sell_currencies"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_currencies(self, extra_data=None, **kwargs):
        """Get buy/sell currencies list"""
        path, params, extra_data = self._get_buy_sell_currencies(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_currencies(self, extra_data=None, **kwargs):
        """Async get buy/sell currencies list"""
        path, params, extra_data = self._get_buy_sell_currencies(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_currency_pair(self, extra_data=None, **kwargs):
        """Get buy/sell currency pair"""
        request_type = "get_buy_sell_currency_pair"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_currency_pair(self, extra_data=None, **kwargs):
        """Get buy/sell currency pair"""
        path, params, extra_data = self._get_buy_sell_currency_pair(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_currency_pair(self, extra_data=None, **kwargs):
        """Async get buy/sell currency pair"""
        path, params, extra_data = self._get_buy_sell_currency_pair(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_quote(self, side, quote_ccy, base_ccy, amount=None, extra_data=None, **kwargs):
        """Get buy/sell quote"""
        request_type = "get_buy_sell_quote"
        params = {
            'side': side,
            'quoteCcy': quote_ccy,
            'baseCcy': base_ccy,
        }
        if amount is not None:
            params['amount'] = str(amount)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{base_ccy}-{quote_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_quote(self, side, quote_ccy, base_ccy, amount=None, extra_data=None, **kwargs):
        """Get buy/sell quote"""
        path, params, extra_data = self._get_buy_sell_quote(side, quote_ccy, base_ccy, amount, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_quote(self, side, quote_ccy, base_ccy, amount=None, extra_data=None, **kwargs):
        """Async get buy/sell quote"""
        path, params, extra_data = self._get_buy_sell_quote(side, quote_ccy, base_ccy, amount, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _buy_sell_trade(self, side, quote_ccy, base_ccy, amount, quote_id=None, extra_data=None, **kwargs):
        """Buy/sell trade"""
        request_type = "buy_sell_trade"
        params = {
            'side': side,
            'quoteCcy': quote_ccy,
            'baseCcy': base_ccy,
            'amount': str(amount),
        }
        if quote_id:
            params['quoteId'] = quote_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{base_ccy}-{quote_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def buy_sell_trade(self, side, quote_ccy, base_ccy, amount, quote_id=None, extra_data=None, **kwargs):
        """Buy/sell trade"""
        path, params, extra_data = self._buy_sell_trade(side, quote_ccy, base_ccy, amount, quote_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_buy_sell_trade(self, side, quote_ccy, base_ccy, amount, quote_id=None, extra_data=None, **kwargs):
        """Async buy/sell trade"""
        path, params, extra_data = self._buy_sell_trade(side, quote_ccy, base_ccy, amount, quote_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get buy/sell history"""
        request_type = "get_buy_sell_history"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get buy/sell history"""
        path, params, extra_data = self._get_buy_sell_history(after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get buy/sell history"""
        path, params, extra_data = self._get_buy_sell_history(after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Sub-account (P2) - Remaining Interfaces ====================

    def _get_sub_account_transfer_history(self, sub_acct=None, ccy=None, after=None, before=None, limit=None,
                                           extra_data=None, **kwargs):
        """Get sub-account transfer history"""
        request_type = "get_sub_account_transfer_history"
        params = {}
        if sub_acct:
            params['subAcct'] = sub_acct
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_transfer_history(self, sub_acct=None, ccy=None, after=None, before=None, limit=None,
                                          extra_data=None, **kwargs):
        """Get sub-account transfer history"""
        path, params, extra_data = self._get_sub_account_transfer_history(sub_acct, ccy, after, before, limit,
                                                                          extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_transfer_history(self, sub_acct=None, ccy=None, after=None, before=None, limit=None,
                                                extra_data=None, **kwargs):
        """Async get sub-account transfer history"""
        path, params, extra_data = self._get_sub_account_transfer_history(sub_acct, ccy, after, before, limit,
                                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_managed_sub_account_bills(self, sub_acct=None, ccy=None, after=None, before=None, limit=None,
                                        extra_data=None, **kwargs):
        """Get managed sub-account bills"""
        request_type = "get_managed_sub_account_bills"
        params = {}
        if sub_acct:
            params['subAcct'] = sub_acct
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_managed_sub_account_bills(self, sub_acct=None, ccy=None, after=None, before=None, limit=None,
                                       extra_data=None, **kwargs):
        """Get managed sub-account bills"""
        path, params, extra_data = self._get_managed_sub_account_bills(sub_acct, ccy, after, before, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_managed_sub_account_bills(self, sub_acct=None, ccy=None, after=None, before=None, limit=None,
                                            extra_data=None, **kwargs):
        """Async get managed sub-account bills"""
        path, params, extra_data = self._get_managed_sub_account_bills(sub_acct, ccy, after, before, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _sub_account_transfer(self, ccy, amount, from_acct, to_acct, from_acc_type=6, to_acc_type=6,
                              extra_data=None, **kwargs):
        """Sub-account transfer"""
        request_type = "sub_account_transfer"
        params = {
            'ccy': ccy,
            'amt': str(amount),
            'fromAcct': from_acct,
            'toAcct': to_acct,
            'type': '1',  # 1: master account transfer to sub-account, 2: sub-account transfer to master account
            'fromAccType': str(from_acc_type),  # 6: master account
            'toAccType': str(to_acc_type),  # 6: master account
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def sub_account_transfer(self, ccy, amount, from_acct, to_acct, from_acc_type=6, to_acc_type=6,
                              extra_data=None, **kwargs):
        """Sub-account transfer"""
        path, params, extra_data = self._sub_account_transfer(ccy, amount, from_acct, to_acct, from_acc_type,
                                                              to_acc_type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_sub_account_transfer(self, ccy, amount, from_acct, to_acct, from_acc_type=6, to_acc_type=6,
                                    extra_data=None, **kwargs):
        """Async sub-account transfer"""
        path, params, extra_data = self._sub_account_transfer(ccy, amount, from_acct, to_acct, from_acc_type,
                                                              to_acc_type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_sub_account_transfer_out(self, sub_acct=None, ccy=None, allow=False, extra_data=None, **kwargs):
        """Set sub-account transfer out permission"""
        request_type = "set_sub_account_transfer_out"
        params = {
            'subAcct': sub_acct or '',
            'ccy': ccy or '',
            'allow': 'true' if allow else 'false',
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_acct or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def set_sub_account_transfer_out(self, sub_acct=None, ccy=None, allow=False, extra_data=None, **kwargs):
        """Set sub-account transfer out permission"""
        path, params, extra_data = self._set_sub_account_transfer_out(sub_acct, ccy, allow, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_sub_account_transfer_out(self, sub_acct=None, ccy=None, allow=False, extra_data=None, **kwargs):
        """Async set sub-account transfer out permission"""
        path, params, extra_data = self._set_sub_account_transfer_out(sub_acct, ccy, allow, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_custody_sub_account_list(self, extra_data=None, **kwargs):
        """Get custody sub-account list"""
        request_type = "get_custody_sub_account_list"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_custody_sub_account_list(self, extra_data=None, **kwargs):
        """Get custody sub-account list"""
        path, params, extra_data = self._get_custody_sub_account_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_custody_sub_account_list(self, extra_data=None, **kwargs):
        """Async get custody sub-account list"""
        path, params, extra_data = self._get_custody_sub_account_list(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Status/Announcement APIs ====================

    def _get_system_status(self, state=None, extra_data=None, **kwargs):
        """Get system status (maintenance, degraded, etc.)
        Args:
            state: Status type. "scheduled" for maintenance announcements. Default is empty for current system status.
            extra_data: extra_data, default is None, can be a dict passed by user
            kwargs: pass key-worded, variable-length arguments.
        """
        request_type = "get_system_status"
        params = {}
        if state is not None:
            params['state'] = state
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "SYSTEM",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_system_status_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_system_status_normalize_function(input_data, extra_data):
        """Normalize system status response
        Returns: (status_list, status_bool) where status_list contains system status data
        """
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        return input_data['data'], status

    def get_system_status(self, state=None, extra_data=None, **kwargs):
        """Get system status
        Args:
            state: Status type. "scheduled" for maintenance announcements. Default is empty for current system status.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_system_status(state, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_system_status(self, state=None, extra_data=None, **kwargs):
        """Async get system status
        Args:
            state: Status type. "scheduled" for maintenance announcements. Default is empty for current system status.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_system_status(state, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_announcements(self, announcement_type=None, page=None, limit=None, extra_data=None, **kwargs):
        """Get announcements
        Args:
            announcement_type: Announcement type. Default is empty for all types.
            page: Page number. Default is 1.
            limit: Number of results per page. Default is 10. Maximum is 100.
            extra_data: extra_data, default is None, can be a dict passed by user
            kwargs: pass key-worded, variable-length arguments.
        """
        request_type = "get_announcements"
        params = {}
        if announcement_type is not None:
            params['announcementType'] = announcement_type
        if page is not None:
            params['page'] = str(page)
        if limit is not None:
            params['limit'] = str(limit)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "SYSTEM",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_announcements_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_announcements_normalize_function(input_data, extra_data):
        """Normalize announcements response
        Returns: (announcement_list, status_bool) where announcement_list contains announcement data
        """
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        # Return the announcement details directly
        if 'details' in input_data['data'][0]:
            return input_data['data'][0]['details'], status
        return input_data['data'], status

    def get_announcements(self, announcement_type=None, page=None, limit=None, extra_data=None, **kwargs):
        """Get announcements
        Args:
            announcement_type: Announcement type. Default is empty for all types.
            page: Page number. Default is 1.
            limit: Number of results per page. Default is 10. Maximum is 100.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcements(announcement_type, page, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_announcements(self, announcement_type=None, page=None, limit=None, extra_data=None, **kwargs):
        """Async get announcements
        Args:
            announcement_type: Announcement type. Default is empty for all types.
            page: Page number. Default is 1.
            limit: Number of results per page. Default is 10. Maximum is 100.
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcements(announcement_type, page, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_announcement_types(self, extra_data=None, **kwargs):
        """Get announcement types
        Args:
            extra_data: extra_data, default is None, can be a dict passed by user
            kwargs: pass key-worded, variable-length arguments.
        """
        request_type = "get_announcement_types"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "SYSTEM",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_announcement_types_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_announcement_types_normalize_function(input_data, extra_data):
        """Normalize announcement types response
        Returns: (type_list, status_bool) where type_list contains announcement type data
        """
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        # Return the announcement types directly
        if 'announcementType' in input_data['data'][0]:
            return input_data['data'][0]['announcementType'], status
        return input_data['data'], status

    def get_announcement_types(self, extra_data=None, **kwargs):
        """Get announcement types
        Args:
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcement_types(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_announcement_types(self, extra_data=None, **kwargs):
        """Async get announcement types
        Args:
            extra_data: extra_data, default is None, can be a dict passed by user
        """
        path, params, extra_data = self._get_announcement_types(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)
    # ==================== Spread Trading APIs ====================

    def _sprd_order(self, sprd_id, side, sz, px=None, reduce_only=False, ccy=None,
                    cl_ord_id=None, tag=None, pos_side=None, extra_data=None, **kwargs):
        """
        Place spread order
        :param sprd_id: Spread instrument ID
        :param side: Order side: buy, sell
        :param sz: Order size
        :param px: Order price, required for limit orders
        :param reduce_only: Whether to reduce position only
        :param ccy: Currency
        :param cl_ord_id: Client order ID
        :param tag: Order tag
        :param pos_side: Position side
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_order"
        params = {
            'sprdId': sprd_id,
            'side': side,
            'sz': str(sz),
        }
        if px is not None:
            params['px'] = str(px)
        if reduce_only:
            params['reduceOnly'] = 'true'
        if ccy:
            params['ccy'] = ccy
        if cl_ord_id:
            params['clOrdId'] = cl_ord_id
        if tag:
            params['tag'] = tag
        if pos_side:
            params['posSide'] = pos_side
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sprd_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._sprd_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_order_normalize_function(input_data, extra_data):
        """Normalize spread order response"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data'][0]
        result = {
            'order_id': data.get('ordId'),
            'client_order_id': data.get('clOrdId'),
            'sprd_id': data.get('sprdId'),
            'side': data.get('side'),
            'size': data.get('sz'),
            'price': data.get('px'),
            'pos_side': data.get('posSide'),
            'state': data.get('state'),
            'avg_px': data.get('avgPx'),
            'filled_sz': data.get('fillSz'),
            'fee': data.get('fee'),
            's_code': data.get('sCode'),
            's_msg': data.get('sMsg'),
        }
        return [result], status

    def sprd_order(self, sprd_id, side, sz, px=None, reduce_only=False, ccy=None,
                   cl_ord_id=None, tag=None, pos_side=None, extra_data=None, **kwargs):
        """Place spread order"""
        path, params, extra_data = self._sprd_order(sprd_id, side, sz, px, reduce_only,
                                                     ccy, cl_ord_id, tag, pos_side,
                                                     extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_sprd_order(self, sprd_id, side, sz, px=None, reduce_only=False, ccy=None,
                         cl_ord_id=None, tag=None, pos_side=None, extra_data=None, **kwargs):
        """Async place spread order"""
        path, params, extra_data = self._sprd_order(sprd_id, side, sz, px, reduce_only,
                                                     ccy, cl_ord_id, tag, pos_side,
                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _sprd_cancel_order(self, sprd_id, order_id=None, client_order_id=None,
                           extra_data=None, **kwargs):
        """
        Cancel spread order
        :param sprd_id: Spread instrument ID
        :param order_id: Order ID
        :param client_order_id: Client order ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_cancel_order"
        params = {'sprdId': sprd_id}
        if order_id:
            params['ordId'] = order_id
        if client_order_id:
            params['clOrdId'] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sprd_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._sprd_cancel_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_cancel_order_normalize_function(input_data, extra_data):
        """Normalize spread cancel order response"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data'][0]
        result = {
            'order_id': data.get('ordId'),
            'client_order_id': data.get('clOrdId'),
            's_code': data.get('sCode'),
            's_msg': data.get('sMsg'),
        }
        return [result], status

    def sprd_cancel_order(self, sprd_id, order_id=None, client_order_id=None,
                          extra_data=None, **kwargs):
        """Cancel spread order"""
        path, params, extra_data = self._sprd_cancel_order(sprd_id, order_id, client_order_id,
                                                            extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_sprd_cancel_order(self, sprd_id, order_id=None, client_order_id=None,
                                extra_data=None, **kwargs):
        """Async cancel spread order"""
        path, params, extra_data = self._sprd_cancel_order(sprd_id, order_id, client_order_id,
                                                            extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _sprd_get_order(self, sprd_id, order_id=None, client_order_id=None,
                        extra_data=None, **kwargs):
        """
        Get spread order details
        :param sprd_id: Spread instrument ID
        :param order_id: Order ID
        :param client_order_id: Client order ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_order"
        params = {'sprdId': sprd_id}
        if order_id:
            params['ordId'] = order_id
        if client_order_id:
            params['clOrdId'] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sprd_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._sprd_get_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_order_normalize_function(input_data, extra_data):
        """Normalize spread get order response"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data']
        results = []
        for item in data:
            result = {
                'sprd_id': item.get('sprdId'),
                'order_id': item.get('ordId'),
                'client_order_id': item.get('clOrdId'),
                'side': item.get('side'),
                'pos_side': item.get('posSide'),
                'size': item.get('sz'),
                'price': item.get('px'),
                'state': item.get('state'),
                'avg_px': item.get('avgPx'),
                'filled_sz': item.get('fillSz'),
                'fee': item.get('fee'),
                'fee_ccy': item.get('feeCcy'),
                'source': item.get('source'),
                'create_time': item.get('cTime'),
                'update_time': item.get('uTime'),
            }
            results.append(result)
        return results, status

    def sprd_get_order(self, sprd_id, order_id=None, client_order_id=None,
                       extra_data=None, **kwargs):
        """Get spread order details"""
        path, params, extra_data = self._sprd_get_order(sprd_id, order_id, client_order_id,
                                                         extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_order(self, sprd_id, order_id=None, client_order_id=None,
                             extra_data=None, **kwargs):
        """Async get spread order details"""
        path, params, extra_data = self._sprd_get_order(sprd_id, order_id, client_order_id,
                                                         extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _sprd_get_orders_pending(self, sprd_id=None, inst_type=None, after=None, before=None,
                                  limit=None, extra_data=None, **kwargs):
        """
        Get pending spread orders
        :param sprd_id: Spread instrument ID
        :param inst_type: Instrument type
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_orders_pending"
        params = {}
        if sprd_id:
            params['sprdId'] = sprd_id
        if inst_type:
            params['instType'] = inst_type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sprd_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._sprd_get_orders_pending_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_orders_pending_normalize_function(input_data, extra_data):
        """Normalize spread pending orders response"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data']
        results = []
        for item in data:
            result = {
                'sprd_id': item.get('sprdId'),
                'order_id': item.get('ordId'),
                'client_order_id': item.get('clOrdId'),
                'side': item.get('side'),
                'pos_side': item.get('posSide'),
                'size': item.get('sz'),
                'price': item.get('px'),
                'reduce_only': item.get('reduceOnly'),
                'state': item.get('state'),
                'avg_px': item.get('avgPx'),
                'filled_sz': item.get('fillSz'),
                'fee': item.get('fee'),
                'fee_ccy': item.get('feeCcy'),
                'source': item.get('source'),
                'create_time': item.get('cTime'),
                'update_time': item.get('uTime'),
            }
            results.append(result)
        return results, status

    def sprd_get_orders_pending(self, sprd_id=None, inst_type=None, after=None, before=None,
                                 limit=None, extra_data=None, **kwargs):
        """Get pending spread orders"""
        path, params, extra_data = self._sprd_get_orders_pending(sprd_id, inst_type, after,
                                                                  before, limit, extra_data,
                                                                  **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_orders_pending(self, sprd_id=None, inst_type=None, after=None, before=None,
                                       limit=None, extra_data=None, **kwargs):
        """Async get pending spread orders"""
        path, params, extra_data = self._sprd_get_orders_pending(sprd_id, inst_type, after,
                                                                  before, limit, extra_data,
                                                                  **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _sprd_get_orders_history(self, sprd_id=None, inst_type=None, state=None, after=None,
                                  before=None, limit=None, extra_data=None, **kwargs):
        """
        Get spread order history
        :param sprd_id: Spread instrument ID
        :param inst_type: Instrument type
        :param state: Order state
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_orders_history"
        params = {}
        if sprd_id:
            params['sprdId'] = sprd_id
        if inst_type:
            params['instType'] = inst_type
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
            "symbol_name": sprd_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._sprd_get_orders_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_orders_history_normalize_function(input_data, extra_data):
        """Normalize spread order history response"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data']
        results = []
        for item in data:
            result = {
                'sprd_id': item.get('sprdId'),
                'order_id': item.get('ordId'),
                'client_order_id': item.get('clOrdId'),
                'side': item.get('side'),
                'pos_side': item.get('posSide'),
                'size': item.get('sz'),
                'price': item.get('px'),
                'reduce_only': item.get('reduceOnly'),
                'state': item.get('state'),
                'avg_px': item.get('avgPx'),
                'filled_sz': item.get('fillSz'),
                'fee': item.get('fee'),
                'fee_ccy': item.get('feeCcy'),
                'source': item.get('source'),
                'acc_fill_sz_yday': item.get('accFillSzYday'),
                'create_time': item.get('cTime'),
                'update_time': item.get('uTime'),
            }
            results.append(result)
        return results, status

    def sprd_get_orders_history(self, sprd_id=None, inst_type=None, state=None, after=None,
                                 before=None, limit=None, extra_data=None, **kwargs):
        """Get spread order history"""
        path, params, extra_data = self._sprd_get_orders_history(sprd_id, inst_type, state,
                                                                  after, before, limit,
                                                                  extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_orders_history(self, sprd_id=None, inst_type=None, state=None, after=None,
                                       before=None, limit=None, extra_data=None, **kwargs):
        """Async get spread order history"""
        path, params, extra_data = self._sprd_get_orders_history(sprd_id, inst_type, state,
                                                                  after, before, limit,
                                                                  extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _sprd_get_trades(self, sprd_id=None, after=None, before=None, limit=None,
                         extra_data=None, **kwargs):
        """
        Get spread trade history
        :param sprd_id: Spread instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_trades"
        params = {}
        if sprd_id:
            params['sprdId'] = sprd_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sprd_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._sprd_get_trades_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_trades_normalize_function(input_data, extra_data):
        """Normalize spread trades response"""
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data']
        results = []
        for item in data:
            result = {
                'sprd_id': item.get('sprdId'),
                'trade_id': item.get('tradeId'),
                'order_id': item.get('ordId'),
                'client_order_id': item.get('clOrdId'),
                'side': item.get('side'),
                'pos_side': item.get('posSide'),
                'size': item.get('sz'),
                'price': item.get('px'),
                'fee': item.get('fee'),
                'fee_ccy': item.get('feeCcy'),
                'source': item.get('source'),
                'timestamp': item.get('ts'),
            }
            results.append(result)
        return results, status

    def sprd_get_trades(self, sprd_id=None, after=None, before=None, limit=None,
                        extra_data=None, **kwargs):
        """Get spread trade history"""
        path, params, extra_data = self._sprd_get_trades(sprd_id, after, before, limit,
                                                          extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_trades(self, sprd_id=None, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """Async get spread trade history"""
        path, params, extra_data = self._sprd_get_trades(sprd_id, after, before, limit,
                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Copy Trading APIs ====================

    def _copytrading_get_current_subpositions(self, inst_type=None, inst_id=None, after=None,
                                              before=None, limit=None, extra_data=None, **kwargs):
        """
        Get existing lead positions
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param inst_id: Instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_current_subpositions"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_current_subpositions_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_current_subpositions_normalize_function(input_data, extra_data):
        """Normalize copy trading current subpositions data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_current_subpositions(self, inst_type=None, inst_id=None, after=None,
                                             before=None, limit=None, extra_data=None, **kwargs):
        """Get existing lead positions"""
        path, params, extra_data = self._copytrading_get_current_subpositions(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_current_subpositions(self, inst_type=None, inst_id=None, after=None,
                                                   before=None, limit=None, extra_data=None, **kwargs):
        """Async get existing lead positions"""
        path, params, extra_data = self._copytrading_get_current_subpositions(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_subpositions_history(self, inst_type=None, inst_id=None, after=None,
                                              before=None, limit=None, extra_data=None, **kwargs):
        """
        Lead position history
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param inst_id: Instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_subpositions_history"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_subpositions_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_subpositions_history_normalize_function(input_data, extra_data):
        """Normalize copy trading subpositions history data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_subpositions_history(self, inst_type=None, inst_id=None, after=None,
                                             before=None, limit=None, extra_data=None, **kwargs):
        """Lead position history"""
        path, params, extra_data = self._copytrading_get_subpositions_history(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_subpositions_history(self, inst_type=None, inst_id=None, after=None,
                                                   before=None, limit=None, extra_data=None, **kwargs):
        """Async lead position history"""
        path, params, extra_data = self._copytrading_get_subpositions_history(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_algo_order(self, sub_pos_id, tp_trigger_px=None, tp_trigger_px_type=None,
                                sl_trigger_px=None, sl_trigger_px_type=None, tp_ord_px=None,
                                sl_ord_px=None, extra_data=None, **kwargs):
        """
        Place lead stop order
        :param sub_pos_id: Sub position ID
        :param tp_trigger_px: Take profit trigger price
        :param tp_trigger_px_type: Take profit trigger price type: last, index, mark
        :param sl_trigger_px: Stop loss trigger price
        :param sl_trigger_px_type: Stop loss trigger price type: last, index, mark
        :param tp_ord_px: Take profit order price
        :param sl_ord_px: Stop loss order price
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_algo_order"
        body = {
            'subPosId': sub_pos_id,
        }
        if tp_trigger_px is not None:
            body['tpTriggerPx'] = str(tp_trigger_px)
        if tp_trigger_px_type:
            body['tpTriggerPxType'] = tp_trigger_px_type
        if sl_trigger_px is not None:
            body['slTriggerPx'] = str(sl_trigger_px)
        if sl_trigger_px_type:
            body['slTriggerPxType'] = sl_trigger_px_type
        if tp_ord_px is not None:
            body['tpOrdPx'] = str(tp_ord_px)
        if sl_ord_px is not None:
            body['slOrdPx'] = str(sl_ord_px)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_pos_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_algo_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_algo_order_normalize_function(input_data, extra_data):
        """Normalize copy trading algo order response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_algo_order(self, sub_pos_id, tp_trigger_px=None, tp_trigger_px_type=None,
                               sl_trigger_px=None, sl_trigger_px_type=None, tp_ord_px=None,
                               sl_ord_px=None, extra_data=None, **kwargs):
        """Place lead stop order"""
        path, body, extra_data = self._copytrading_algo_order(
            sub_pos_id, tp_trigger_px, tp_trigger_px_type, sl_trigger_px,
            sl_trigger_px_type, tp_ord_px, sl_ord_px, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_algo_order(self, sub_pos_id, tp_trigger_px=None, tp_trigger_px_type=None,
                                     sl_trigger_px=None, sl_trigger_px_type=None, tp_ord_px=None,
                                     sl_ord_px=None, extra_data=None, **kwargs):
        """Async place lead stop order"""
        path, body, extra_data = self._copytrading_algo_order(
            sub_pos_id, tp_trigger_px, tp_trigger_px_type, sl_trigger_px,
            sl_trigger_px_type, tp_ord_px, sl_ord_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_close_subposition(self, sub_pos_id, extra_data=None, **kwargs):
        """
        Close lead position
        :param sub_pos_id: Sub position ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_close_subposition"
        body = {
            'subPosId': sub_pos_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_pos_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_close_subposition_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_close_subposition_normalize_function(input_data, extra_data):
        """Normalize copy trading close subposition response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_close_subposition(self, sub_pos_id, extra_data=None, **kwargs):
        """Close lead position"""
        path, body, extra_data = self._copytrading_close_subposition(sub_pos_id, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_close_subposition(self, sub_pos_id, extra_data=None, **kwargs):
        """Async close lead position"""
        path, body, extra_data = self._copytrading_close_subposition(sub_pos_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_instruments(self, extra_data=None, **kwargs):
        """
        Leading instruments
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_instruments"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_instruments_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_instruments_normalize_function(input_data, extra_data):
        """Normalize copy trading instruments data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_instruments(self, extra_data=None, **kwargs):
        """Leading instruments"""
        path, params, extra_data = self._copytrading_get_instruments(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_instruments(self, extra_data=None, **kwargs):
        """Async leading instruments"""
        path, params, extra_data = self._copytrading_get_instruments(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_set_instruments(self, inst_type, inst_ids=None, extra_data=None, **kwargs):
        """
        Amend leading instruments
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param inst_ids: Instrument ID list
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_set_instruments"
        body = {
            'instType': inst_type,
        }
        if inst_ids:
            body['instId'] = ','.join(inst_ids) if isinstance(inst_ids, list) else inst_ids
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_set_instruments_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_set_instruments_normalize_function(input_data, extra_data):
        """Normalize copy trading set instruments response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_set_instruments(self, inst_type, inst_ids=None, extra_data=None, **kwargs):
        """Amend leading instruments"""
        path, body, extra_data = self._copytrading_set_instruments(inst_type, inst_ids, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_set_instruments(self, inst_type, inst_ids=None, extra_data=None, **kwargs):
        """Async amend leading instruments"""
        path, body, extra_data = self._copytrading_set_instruments(inst_type, inst_ids, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_profit_sharing_details(self, after=None, before=None, limit=None,
                                                 extra_data=None, **kwargs):
        """
        Profit sharing details
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_profit_sharing_details"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_profit_sharing_details_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_profit_sharing_details_normalize_function(input_data, extra_data):
        """Normalize copy trading profit sharing details data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_profit_sharing_details(self, after=None, before=None, limit=None,
                                               extra_data=None, **kwargs):
        """Profit sharing details"""
        path, params, extra_data = self._copytrading_get_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_profit_sharing_details(self, after=None, before=None, limit=None,
                                                     extra_data=None, **kwargs):
        """Async profit sharing details"""
        path, params, extra_data = self._copytrading_get_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_total_profit_sharing(self, extra_data=None, **kwargs):
        """
        Total profit sharing
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_total_profit_sharing"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_total_profit_sharing_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_total_profit_sharing_normalize_function(input_data, extra_data):
        """Normalize copy trading total profit sharing data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_total_profit_sharing(self, extra_data=None, **kwargs):
        """Total profit sharing"""
        path, params, extra_data = self._copytrading_get_total_profit_sharing(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_total_profit_sharing(self, extra_data=None, **kwargs):
        """Async total profit sharing"""
        path, params, extra_data = self._copytrading_get_total_profit_sharing(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_unrealized_profit_sharing_details(self, after=None, before=None,
                                                             limit=None, extra_data=None, **kwargs):
        """
        Unrealized profit sharing details
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_unrealized_profit_sharing_details"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_unrealized_profit_sharing_details_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_unrealized_profit_sharing_details_normalize_function(input_data, extra_data):
        """Normalize copy trading unrealized profit sharing details data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_unrealized_profit_sharing_details(self, after=None, before=None,
                                                           limit=None, extra_data=None, **kwargs):
        """Unrealized profit sharing details"""
        path, params, extra_data = self._copytrading_get_unrealized_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_unrealized_profit_sharing_details(self, after=None, before=None,
                                                                 limit=None, extra_data=None, **kwargs):
        """Async unrealized profit sharing details"""
        path, params, extra_data = self._copytrading_get_unrealized_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_total_unrealized_profit_sharing(self, extra_data=None, **kwargs):
        """
        Total unrealized profit sharing
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_total_unrealized_profit_sharing"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_total_unrealized_profit_sharing_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_total_unrealized_profit_sharing_normalize_function(input_data, extra_data):
        """Normalize copy trading total unrealized profit sharing data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_total_unrealized_profit_sharing(self, extra_data=None, **kwargs):
        """Total unrealized profit sharing"""
        path, params, extra_data = self._copytrading_get_total_unrealized_profit_sharing(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_total_unrealized_profit_sharing(self, extra_data=None, **kwargs):
        """Async total unrealized profit sharing"""
        path, params, extra_data = self._copytrading_get_total_unrealized_profit_sharing(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_set_profit_sharing_ratio(self, profit_sharing_ratio, extra_data=None, **kwargs):
        """
        Amend profit sharing ratio
        :param profit_sharing_ratio: Profit sharing ratio, e.g. 10 means 10%
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_set_profit_sharing_ratio"
        body = {
            'profitSharingRatio': str(profit_sharing_ratio),
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_set_profit_sharing_ratio_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_set_profit_sharing_ratio_normalize_function(input_data, extra_data):
        """Normalize copy trading set profit sharing ratio response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_set_profit_sharing_ratio(self, profit_sharing_ratio, extra_data=None, **kwargs):
        """Amend profit sharing ratio"""
        path, body, extra_data = self._copytrading_set_profit_sharing_ratio(profit_sharing_ratio, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_set_profit_sharing_ratio(self, profit_sharing_ratio, extra_data=None, **kwargs):
        """Async amend profit sharing ratio"""
        path, body, extra_data = self._copytrading_set_profit_sharing_ratio(profit_sharing_ratio, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_config(self, extra_data=None, **kwargs):
        """
        Account configuration
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_config"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_config_normalize_function(input_data, extra_data):
        """Normalize copy trading config data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_config(self, extra_data=None, **kwargs):
        """Account configuration"""
        path, params, extra_data = self._copytrading_get_config(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_config(self, extra_data=None, **kwargs):
        """Async account configuration"""
        path, params, extra_data = self._copytrading_get_config(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_first_copy_settings(self, copy_inst_id, lever=None,
                                          extra_data=None, **kwargs):
        """
        First copy settings
        :param copy_inst_id: Copy instrument ID, unique identifier for the lead trader
        :param lever: Leverage
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_first_copy_settings"
        body = {
            'copyInstId': copy_inst_id,
        }
        if lever is not None:
            body['lever'] = str(lever)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_first_copy_settings_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_first_copy_settings_normalize_function(input_data, extra_data):
        """Normalize copy trading first copy settings response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_first_copy_settings(self, copy_inst_id, lever=None,
                                         extra_data=None, **kwargs):
        """First copy settings"""
        path, body, extra_data = self._copytrading_first_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_first_copy_settings(self, copy_inst_id, lever=None,
                                              extra_data=None, **kwargs):
        """Async first copy settings"""
        path, body, extra_data = self._copytrading_first_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_amend_copy_settings(self, copy_inst_id, lever=None,
                                          extra_data=None, **kwargs):
        """
        Amend copy settings
        :param copy_inst_id: Copy instrument ID, unique identifier for the lead trader
        :param lever: Leverage
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_amend_copy_settings"
        body = {
            'copyInstId': copy_inst_id,
        }
        if lever is not None:
            body['lever'] = str(lever)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_amend_copy_settings_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_amend_copy_settings_normalize_function(input_data, extra_data):
        """Normalize copy trading amend copy settings response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_amend_copy_settings(self, copy_inst_id, lever=None,
                                         extra_data=None, **kwargs):
        """Amend copy settings"""
        path, body, extra_data = self._copytrading_amend_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_amend_copy_settings(self, copy_inst_id, lever=None,
                                              extra_data=None, **kwargs):
        """Async amend copy settings"""
        path, body, extra_data = self._copytrading_amend_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_stop_copy_trading(self, copy_inst_id, extra_data=None, **kwargs):
        """
        Stop copying
        :param copy_inst_id: Copy instrument ID, unique identifier for the lead trader
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_stop_copy_trading"
        body = {
            'copyInstId': copy_inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_stop_copy_trading_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_stop_copy_trading_normalize_function(input_data, extra_data):
        """Normalize copy trading stop copy trading response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_stop_copy_trading(self, copy_inst_id, extra_data=None, **kwargs):
        """Stop copying"""
        path, body, extra_data = self._copytrading_stop_copy_trading(copy_inst_id, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_stop_copy_trading(self, copy_inst_id, extra_data=None, **kwargs):
        """Async stop copying"""
        path, body, extra_data = self._copytrading_stop_copy_trading(copy_inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_copy_settings(self, extra_data=None, **kwargs):
        """
        Get copy settings
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_copy_settings"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_copy_settings_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_copy_settings_normalize_function(input_data, extra_data):
        """Normalize copy trading copy settings data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_copy_settings(self, extra_data=None, **kwargs):
        """Get copy settings"""
        path, params, extra_data = self._copytrading_get_copy_settings(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_copy_settings(self, extra_data=None, **kwargs):
        """Async get copy settings"""
        path, params, extra_data = self._copytrading_get_copy_settings(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_batch_leverage_info(self, extra_data=None, **kwargs):
        """
        My lead traders
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_batch_leverage_info"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_batch_leverage_info_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_batch_leverage_info_normalize_function(input_data, extra_data):
        """Normalize copy trading batch leverage info data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_batch_leverage_info(self, extra_data=None, **kwargs):
        """My lead traders"""
        path, params, extra_data = self._copytrading_get_batch_leverage_info(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_batch_leverage_info(self, extra_data=None, **kwargs):
        """Async my lead traders"""
        path, params, extra_data = self._copytrading_get_batch_leverage_info(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_copy_trading_configuration(self, extra_data=None, **kwargs):
        """
        Copy trading configuration
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_copy_trading_configuration"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_get_copy_trading_configuration_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_copy_trading_configuration_normalize_function(input_data, extra_data):
        """Normalize copy trading configuration data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_copy_trading_configuration(self, extra_data=None, **kwargs):
        """Copy trading configuration"""
        path, params, extra_data = self._copytrading_get_copy_trading_configuration(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_copy_trading_configuration(self, extra_data=None, **kwargs):
        """Async copy trading configuration"""
        path, params, extra_data = self._copytrading_get_copy_trading_configuration(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Copy Trading Public APIs ====================

    def _copytrading_public_lead_traders(self, inst_type=None, sort_by=None, uly=None,
                                         after=None, before=None, limit=None,
                                         extra_data=None, **kwargs):
        """
        Lead trader ranks (public)
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param sort_by: Sort by, e.g. totalProfitSharing
        :param uly: Underlying
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_lead_traders"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if sort_by:
            params['sortBy'] = sort_by
        if uly:
            params['uly'] = uly
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "PUBLIC",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_lead_traders_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_lead_traders_normalize_function(input_data, extra_data):
        """Normalize copy trading public lead traders data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_lead_traders(self, inst_type=None, sort_by=None, uly=None,
                                        after=None, before=None, limit=None,
                                        extra_data=None, **kwargs):
        """Lead trader ranks (public)"""
        path, params, extra_data = self._copytrading_public_lead_traders(
            inst_type, sort_by, uly, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_lead_traders(self, inst_type=None, sort_by=None, uly=None,
                                              after=None, before=None, limit=None,
                                              extra_data=None, **kwargs):
        """Async lead trader ranks (public)"""
        path, params, extra_data = self._copytrading_public_lead_traders(
            inst_type, sort_by, uly, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_weekly_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                       extra_data=None, **kwargs):
        """
        Lead trader weekly PnL (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_weekly_pnl"
        params = {
            'copyInstId': copy_inst_id,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_weekly_pnl_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_weekly_pnl_normalize_function(input_data, extra_data):
        """Normalize copy trading public weekly PnL data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_weekly_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Lead trader weekly PnL (public)"""
        path, params, extra_data = self._copytrading_public_weekly_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_weekly_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                            extra_data=None, **kwargs):
        """Async lead trader weekly PnL (public)"""
        path, params, extra_data = self._copytrading_public_weekly_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """
        Lead trader daily PnL (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_pnl"
        params = {
            'copyInstId': copy_inst_id,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_pnl_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_pnl_normalize_function(input_data, extra_data):
        """Normalize copy trading public PnL data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """Lead trader daily PnL (public)"""
        path, params, extra_data = self._copytrading_public_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Async lead trader daily PnL (public)"""
        path, params, extra_data = self._copytrading_public_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_stats(self, copy_inst_id, extra_data=None, **kwargs):
        """
        Lead trader stats (public)
        :param copy_inst_id: Copy instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_stats"
        params = {
            'copyInstId': copy_inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_stats_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_stats_normalize_function(input_data, extra_data):
        """Normalize copy trading public stats data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_stats(self, copy_inst_id, extra_data=None, **kwargs):
        """Lead trader stats (public)"""
        path, params, extra_data = self._copytrading_public_stats(copy_inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_stats(self, copy_inst_id, extra_data=None, **kwargs):
        """Async lead trader stats (public)"""
        path, params, extra_data = self._copytrading_public_stats(copy_inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_preference_currency(self, copy_inst_id, extra_data=None, **kwargs):
        """
        Lead trader currency preferences (public)
        :param copy_inst_id: Copy instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_preference_currency"
        params = {
            'copyInstId': copy_inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_preference_currency_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_preference_currency_normalize_function(input_data, extra_data):
        """Normalize copy trading public preference currency data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_preference_currency(self, copy_inst_id, extra_data=None, **kwargs):
        """Lead trader currency preferences (public)"""
        path, params, extra_data = self._copytrading_public_preference_currency(
            copy_inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_preference_currency(self, copy_inst_id, extra_data=None, **kwargs):
        """Async lead trader currency preferences (public)"""
        path, params, extra_data = self._copytrading_public_preference_currency(
            copy_inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_current_subpositions(self, copy_inst_id, after=None, before=None,
                                                 limit=None, extra_data=None, **kwargs):
        """
        Lead trader current positions (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_current_subpositions"
        params = {
            'copyInstId': copy_inst_id,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_current_subpositions_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_current_subpositions_normalize_function(input_data, extra_data):
        """Normalize copy trading public current subpositions data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_current_subpositions(self, copy_inst_id, after=None, before=None,
                                                limit=None, extra_data=None, **kwargs):
        """Lead trader current positions (public)"""
        path, params, extra_data = self._copytrading_public_current_subpositions(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_current_subpositions(self, copy_inst_id, after=None, before=None,
                                                     limit=None, extra_data=None, **kwargs):
        """Async lead trader current positions (public)"""
        path, params, extra_data = self._copytrading_public_current_subpositions(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_subpositions_history(self, copy_inst_id, after=None, before=None,
                                                 limit=None, extra_data=None, **kwargs):
        """
        Lead trader position history (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_subpositions_history"
        params = {
            'copyInstId': copy_inst_id,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_subpositions_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_subpositions_history_normalize_function(input_data, extra_data):
        """Normalize copy trading public subpositions history data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_subpositions_history(self, copy_inst_id, after=None, before=None,
                                                limit=None, extra_data=None, **kwargs):
        """Lead trader position history (public)"""
        path, params, extra_data = self._copytrading_public_subpositions_history(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_subpositions_history(self, copy_inst_id, after=None, before=None,
                                                     limit=None, extra_data=None, **kwargs):
        """Async lead trader position history (public)"""
        path, params, extra_data = self._copytrading_public_subpositions_history(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_copy_traders(self, copy_inst_id, after=None, before=None,
                                         limit=None, extra_data=None, **kwargs):
        """
        Copy traders (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_copy_traders"
        params = {
            'copyInstId': copy_inst_id,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._copytrading_public_copy_traders_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_copy_traders_normalize_function(input_data, extra_data):
        """Normalize copy trading public copy traders data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_copy_traders(self, copy_inst_id, after=None, before=None,
                                        limit=None, extra_data=None, **kwargs):
        """Copy traders (public)"""
        path, params, extra_data = self._copytrading_public_copy_traders(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_copy_traders(self, copy_inst_id, after=None, before=None,
                                              limit=None, extra_data=None, **kwargs):
        """Async copy traders (public)"""
        path, params, extra_data = self._copytrading_public_copy_traders(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== RFQ (Request for Quote) / Block Trading ====================

    def _get_counterparties(self, extra_data=None, **kwargs):
        """Get RFQ counterparties list"""
        request_type = "get_counterparties"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_counterparties_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_counterparties_normalize_function(input_data, extra_data):
        """Normalize RFQ counterparties response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_counterparties(self, extra_data=None, **kwargs):
        """Get RFQ counterparties list"""
        path, params, extra_data = self._get_counterparties(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_counterparties(self, extra_data=None, **kwargs):
        """Async get RFQ counterparties list"""
        path, params, extra_data = self._get_counterparties(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _create_rfq(self, inst_id, side, sz, ccy=None, cl_ord_id=None, tag=None,
                    extra_data=None, **kwargs):
        """Create RFQ"""
        request_type = "create_rfq"
        path = self._params.get_rest_path(request_type)
        params = {
            "instId": inst_id,
            "side": side,
            "sz": sz,
        }
        if ccy:
            params["ccy"] = ccy
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        if tag:
            params["tag"] = tag
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._create_rfq_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _create_rfq_normalize_function(input_data, extra_data):
        """Normalize create RFQ response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def create_rfq(self, inst_id, side, sz, ccy=None, cl_ord_id=None, tag=None,
                   extra_data=None, **kwargs):
        """Create RFQ"""
        path, params, extra_data = self._create_rfq(inst_id, side, sz, ccy, cl_ord_id, tag,
                                                     extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_rfq(self, inst_id, side, sz, ccy=None, cl_ord_id=None, tag=None,
                         extra_data=None, **kwargs):
        """Async create RFQ"""
        path, params, extra_data = self._create_rfq(inst_id, side, sz, ccy, cl_ord_id, tag,
                                                     extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_rfq(self, rfq_id, inst_id, extra_data=None, **kwargs):
        """Cancel RFQ"""
        request_type = "cancel_rfq"
        path = self._params.get_rest_path(request_type)
        params = {
            "rfqId": rfq_id,
            "instId": inst_id,
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_rfq_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_rfq_normalize_function(input_data, extra_data):
        """Normalize cancel RFQ response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def cancel_rfq(self, rfq_id, inst_id, extra_data=None, **kwargs):
        """Cancel RFQ"""
        path, params, extra_data = self._cancel_rfq(rfq_id, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_rfq(self, rfq_id, inst_id, extra_data=None, **kwargs):
        """Async cancel RFQ"""
        path, params, extra_data = self._cancel_rfq(rfq_id, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_multiple_rfqs(self, rfq_ids, inst_id, extra_data=None, **kwargs):
        """Cancel multiple RFQs"""
        request_type = "cancel_multiple_rfqs"
        path = self._params.get_rest_path(request_type)
        params = {
            "rfqIds": rfq_ids if isinstance(rfq_ids, str) else ','.join(rfq_ids),
            "instId": inst_id,
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_multiple_rfqs_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_multiple_rfqs_normalize_function(input_data, extra_data):
        """Normalize cancel multiple RFQs response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def cancel_multiple_rfqs(self, rfq_ids, inst_id, extra_data=None, **kwargs):
        """Cancel multiple RFQs"""
        path, params, extra_data = self._cancel_multiple_rfqs(rfq_ids, inst_id,
                                                                extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_multiple_rfqs(self, rfq_ids, inst_id, extra_data=None, **kwargs):
        """Async cancel multiple RFQs"""
        path, params, extra_data = self._cancel_multiple_rfqs(rfq_ids, inst_id,
                                                                extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_all_rfqs(self, inst_id, extra_data=None, **kwargs):
        """Cancel all RFQs"""
        request_type = "cancel_all_rfqs"
        path = self._params.get_rest_path(request_type)
        params = {"instId": inst_id}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_all_rfqs_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_all_rfqs_normalize_function(input_data, extra_data):
        """Normalize cancel all RFQs response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def cancel_all_rfqs(self, inst_id, extra_data=None, **kwargs):
        """Cancel all RFQs"""
        path, params, extra_data = self._cancel_all_rfqs(inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all_rfqs(self, inst_id, extra_data=None, **kwargs):
        """Async cancel all RFQs"""
        path, params, extra_data = self._cancel_all_rfqs(inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _execute_quote(self, quote_id, inst_id, side, sz, px, ccy=None, cl_ord_id=None,
                       tag=None, extra_data=None, **kwargs):
        """Execute quote"""
        request_type = "execute_quote"
        path = self._params.get_rest_path(request_type)
        params = {
            "quoteId": quote_id,
            "instId": inst_id,
            "side": side,
            "sz": sz,
            "px": px,
        }
        if ccy:
            params["ccy"] = ccy
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        if tag:
            params["tag"] = tag
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._execute_quote_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _execute_quote_normalize_function(input_data, extra_data):
        """Normalize execute quote response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def execute_quote(self, quote_id, inst_id, side, sz, px, ccy=None, cl_ord_id=None,
                      tag=None, extra_data=None, **kwargs):
        """Execute quote"""
        path, params, extra_data = self._execute_quote(quote_id, inst_id, side, sz, px,
                                                       ccy, cl_ord_id, tag, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_execute_quote(self, quote_id, inst_id, side, sz, px, ccy=None, cl_ord_id=None,
                           tag=None, extra_data=None, **kwargs):
        """Async execute quote"""
        path, params, extra_data = self._execute_quote(quote_id, inst_id, side, sz, px,
                                                       ccy, cl_ord_id, tag, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_quote_products(self, extra_data=None, **kwargs):
        """Get quote products list"""
        request_type = "get_quote_products"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_quote_products_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_quote_products_normalize_function(input_data, extra_data):
        """Normalize quote products response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_quote_products(self, extra_data=None, **kwargs):
        """Get quote products list"""
        path, params, extra_data = self._get_quote_products(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_quote_products(self, extra_data=None, **kwargs):
        """Async get quote products list"""
        path, params, extra_data = self._get_quote_products(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _set_quote_products(self, products, extra_data=None, **kwargs):
        """Set quote products"""
        request_type = "set_quote_products"
        path = self._params.get_rest_path(request_type)
        params = {
            'products': products if isinstance(products, str) else json.dumps(products)
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._set_quote_products_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _set_quote_products_normalize_function(input_data, extra_data):
        """Normalize set quote products response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def set_quote_products(self, products, extra_data=None, **kwargs):
        """Set quote products"""
        path, params, extra_data = self._set_quote_products(products, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_quote_products(self, products, extra_data=None, **kwargs):
        """Async set quote products"""
        path, params, extra_data = self._set_quote_products(products, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _rfq_mmp_reset(self, inst_id, extra_data=None, **kwargs):
        """Reset MMP status for RFQ"""
        request_type = "rfq_mmp_reset"
        path = self._params.get_rest_path(request_type)
        params = {'instId': inst_id}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._rfq_mmp_reset_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _rfq_mmp_reset_normalize_function(input_data, extra_data):
        """Normalize RFQ MMP reset response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def rfq_mmp_reset(self, inst_id, extra_data=None, **kwargs):
        """Reset MMP status for RFQ"""
        path, params, extra_data = self._rfq_mmp_reset(inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_rfq_mmp_reset(self, inst_id, extra_data=None, **kwargs):
        """Async reset MMP status for RFQ"""
        path, params, extra_data = self._rfq_mmp_reset(inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _rfq_mmp_config(self, inst_id, mode, tier, quote_limit, extra_data=None, **kwargs):
        """Set MMP for RFQ"""
        request_type = "rfq_mmp_config"
        path = self._params.get_rest_path(request_type)
        params = {
            'instId': inst_id,
            'mode': mode,
            'tier': str(tier),
            'quoteLimit': str(quote_limit)
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._rfq_mmp_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _rfq_mmp_config_normalize_function(input_data, extra_data):
        """Normalize RFQ MMP config response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def rfq_mmp_config(self, inst_id, mode, tier, quote_limit, extra_data=None, **kwargs):
        """Set MMP for RFQ"""
        path, params, extra_data = self._rfq_mmp_config(inst_id, mode, tier, quote_limit,
                                                          extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_rfq_mmp_config(self, inst_id, mode, tier, quote_limit, extra_data=None, **kwargs):
        """Async set MMP for RFQ"""
        path, params, extra_data = self._rfq_mmp_config(inst_id, mode, tier, quote_limit,
                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_rfq_mmp_config(self, inst_id, extra_data=None, **kwargs):
        """Get MMP configuration for RFQ"""
        request_type = "get_rfq_mmp_config"
        path = self._params.get_rest_path(request_type)
        params = {'instId': inst_id}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_rfq_mmp_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfq_mmp_config_normalize_function(input_data, extra_data):
        """Normalize get RFQ MMP config response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_rfq_mmp_config(self, inst_id, extra_data=None, **kwargs):
        """Get MMP configuration for RFQ"""
        path, params, extra_data = self._get_rfq_mmp_config(inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfq_mmp_config(self, inst_id, extra_data=None, **kwargs):
        """Async get MMP configuration for RFQ"""
        path, params, extra_data = self._get_rfq_mmp_config(inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _create_quote(self, inst_id, side, px, sz, cl_ord_id, tif,
                      extra_data=None, **kwargs):
        """Create RFQ quote"""
        request_type = "create_quote"
        path = self._params.get_rest_path(request_type)
        params = {
            'instId': inst_id,
            'side': side,
            'px': str(px),
            'sz': str(sz),
            'clOrdId': cl_ord_id,
            'tif': tif
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._create_quote_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _create_quote_normalize_function(input_data, extra_data):
        """Normalize create quote response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def create_quote(self, inst_id, side, px, sz, cl_ord_id, tif,
                     extra_data=None, **kwargs):
        """Create RFQ quote"""
        path, params, extra_data = self._create_quote(inst_id, side, px, sz, cl_ord_id,
                                                       tif, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_quote(self, inst_id, side, px, sz, cl_ord_id, tif,
                           extra_data=None, **kwargs):
        """Async create RFQ quote"""
        path, params, extra_data = self._create_quote(inst_id, side, px, sz, cl_ord_id,
                                                       tif, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_quote(self, inst_id, quote_id, cl_ord_id=None, extra_data=None, **kwargs):
        """Cancel RFQ quote"""
        request_type = "cancel_quote"
        path = self._params.get_rest_path(request_type)
        params = {
            'instId': inst_id,
            'quoteId': quote_id
        }
        if cl_ord_id:
            params['clOrdId'] = cl_ord_id
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_quote_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_quote_normalize_function(input_data, extra_data):
        """Normalize cancel quote response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def cancel_quote(self, inst_id, quote_id, cl_ord_id=None, extra_data=None, **kwargs):
        """Cancel RFQ quote"""
        path, params, extra_data = self._cancel_quote(inst_id, quote_id, cl_ord_id,
                                                       extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_quote(self, inst_id, quote_id, cl_ord_id=None, extra_data=None, **kwargs):
        """Async cancel RFQ quote"""
        path, params, extra_data = self._cancel_quote(inst_id, quote_id, cl_ord_id,
                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_multiple_quotes(self, inst_id, quote_ids, cl_ord_id=None,
                                extra_data=None, **kwargs):
        """Cancel multiple RFQ quotes"""
        request_type = "cancel_multiple_quotes"
        path = self._params.get_rest_path(request_type)
        params = {'instId': inst_id}
        if isinstance(quote_ids, list):
            params['quoteIds'] = ','.join(quote_ids)
        else:
            params['quoteIds'] = quote_ids
        if cl_ord_id:
            params['clOrdId'] = cl_ord_id
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_multiple_quotes_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_multiple_quotes_normalize_function(input_data, extra_data):
        """Normalize cancel multiple quotes response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def cancel_multiple_quotes(self, inst_id, quote_ids, cl_ord_id=None,
                               extra_data=None, **kwargs):
        """Cancel multiple RFQ quotes"""
        path, params, extra_data = self._cancel_multiple_quotes(inst_id, quote_ids,
                                                                cl_ord_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_multiple_quotes(self, inst_id, quote_ids, cl_ord_id=None,
                                     extra_data=None, **kwargs):
        """Async cancel multiple RFQ quotes"""
        path, params, extra_data = self._cancel_multiple_quotes(inst_id, quote_ids,
                                                                cl_ord_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_all_quotes(self, inst_id, extra_data=None, **kwargs):
        """Cancel all RFQ quotes"""
        request_type = "cancel_all_quotes"
        path = self._params.get_rest_path(request_type)
        params = {'instId': inst_id}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._cancel_all_quotes_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _cancel_all_quotes_normalize_function(input_data, extra_data):
        """Normalize cancel all quotes response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def cancel_all_quotes(self, inst_id, extra_data=None, **kwargs):
        """Cancel all RFQ quotes"""
        path, params, extra_data = self._cancel_all_quotes(inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all_quotes(self, inst_id, extra_data=None, **kwargs):
        """Async cancel all RFQ quotes"""
        path, params, extra_data = self._cancel_all_quotes(inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _rfq_cancel_all_after(self, inst_id, cancel_after, cl_ord_id=None,
                               extra_data=None, **kwargs):
        """Set timer to cancel all RFQ quotes"""
        request_type = "rfq_cancel_all_after"
        path = self._params.get_rest_path(request_type)
        params = {
            'instId': inst_id,
            'cancelAfter': str(cancel_after)
        }
        if cl_ord_id:
            params['clOrdId'] = cl_ord_id
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._rfq_cancel_all_after_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _rfq_cancel_all_after_normalize_function(input_data, extra_data):
        """Normalize RFQ cancel all after response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def rfq_cancel_all_after(self, inst_id, cancel_after, cl_ord_id=None,
                              extra_data=None, **kwargs):
        """Set timer to cancel all RFQ quotes"""
        path, params, extra_data = self._rfq_cancel_all_after(inst_id, cancel_after,
                                                                cl_ord_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_rfq_cancel_all_after(self, inst_id, cancel_after, cl_ord_id=None,
                                     extra_data=None, **kwargs):
        """Async set timer to cancel all RFQ quotes"""
        path, params, extra_data = self._rfq_cancel_all_after(inst_id, cancel_after,
                                                                cl_ord_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_rfqs(self, inst_type=None, state=None, after=None, before=None, limit=None,
                   extra_data=None, **kwargs):
        """Get RFQs list"""
        request_type = "get_rfqs"
        params = {}
        if inst_type:
            params['instType'] = inst_type
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
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_rfqs_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfqs_normalize_function(input_data, extra_data):
        """Normalize get RFQs response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_rfqs(self, inst_type=None, state=None, after=None, before=None, limit=None,
                  extra_data=None, **kwargs):
        """Get RFQs list"""
        path, params, extra_data = self._get_rfqs(inst_type, state, after, before,
                                                     limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfqs(self, inst_type=None, state=None, after=None, before=None, limit=None,
                        extra_data=None, **kwargs):
        """Async get RFQs list"""
        path, params, extra_data = self._get_rfqs(inst_type, state, after, before,
                                                     limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_rfq_quotes(self, rfq_id=None, inst_id=None, state=None, after=None, before=None,
                         limit=None, extra_data=None, **kwargs):
        """Get RFQ quotes list"""
        request_type = "get_rfq_quotes"
        params = {}
        if rfq_id:
            params['rfqId'] = rfq_id
        if inst_id:
            params['instId'] = inst_id
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
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_rfq_quotes_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfq_quotes_normalize_function(input_data, extra_data):
        """Normalize get RFQ quotes response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_rfq_quotes(self, rfq_id=None, inst_id=None, state=None, after=None, before=None,
                        limit=None, extra_data=None, **kwargs):
        """Get RFQ quotes list"""
        path, params, extra_data = self._get_rfq_quotes(rfq_id, inst_id, state, after,
                                                          before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfq_quotes(self, rfq_id=None, inst_id=None, state=None, after=None,
                              before=None, limit=None, extra_data=None, **kwargs):
        """Async get RFQ quotes list"""
        path, params, extra_data = self._get_rfq_quotes(rfq_id, inst_id, state, after,
                                                          before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_rfq_trades(self, inst_id=None, after=None, before=None, limit=None,
                         extra_data=None, **kwargs):
        """Get RFQ trades"""
        request_type = "get_rfq_trades"
        params = {}
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_rfq_trades_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_rfq_trades_normalize_function(input_data, extra_data):
        """Normalize get RFQ trades response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_rfq_trades(self, inst_id=None, after=None, before=None, limit=None,
                        extra_data=None, **kwargs):
        """Get RFQ trades"""
        path, params, extra_data = self._get_rfq_trades(inst_id, after, before,
                                                         limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_rfq_trades(self, inst_id=None, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """Async get RFQ trades"""
        path, params, extra_data = self._get_rfq_trades(inst_id, after, before,
                                                         limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_public_rfq_trades(self, inst_type=None, after=None, before=None, limit=None,
                                extra_data=None, **kwargs):
        """Get public RFQ trades (multi-leg)"""
        request_type = "get_public_rfq_trades"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_public_rfq_trades_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_public_rfq_trades_normalize_function(input_data, extra_data):
        """Normalize get public RFQ trades response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_public_rfq_trades(self, inst_type=None, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """Get public RFQ trades (multi-leg)"""
        path, params, extra_data = self._get_public_rfq_trades(inst_type, after, before,
                                                                 limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_public_rfq_trades(self, inst_type=None, after=None, before=None,
                                     limit=None, extra_data=None, **kwargs):
        """Async get public RFQ trades (multi-leg)"""
        path, params, extra_data = self._get_public_rfq_trades(inst_type, after, before,
                                                                 limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_block_tickers(self, inst_type=None, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get block tickers"""
        request_type = "get_block_tickers"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_block_tickers_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_block_tickers_normalize_function(input_data, extra_data):
        """Normalize get block tickers response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_block_tickers(self, inst_type=None, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get block tickers"""
        path, params, extra_data = self._get_block_tickers(inst_type, uly, inst_id,
                                                            extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_block_tickers(self, inst_type=None, uly=None, inst_id=None,
                                 extra_data=None, **kwargs):
        """Async get block tickers"""
        path, params, extra_data = self._get_block_tickers(inst_type, uly, inst_id,
                                                            extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_block_ticker(self, inst_id, extra_data=None, **kwargs):
        """Get single block ticker"""
        request_type = "get_block_ticker"
        params = {'instId': inst_id}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_block_ticker_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_block_ticker_normalize_function(input_data, extra_data):
        """Normalize get block ticker response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_block_ticker(self, inst_id, extra_data=None, **kwargs):
        """Get single block ticker"""
        path, params, extra_data = self._get_block_ticker(inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_block_ticker(self, inst_id, extra_data=None, **kwargs):
        """Async get single block ticker"""
        path, params, extra_data = self._get_block_ticker(inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_public_block_trades(self, inst_id=None, after=None, before=None, limit=None,
                                   extra_data=None, **kwargs):
        """Get public block trades (single-leg)"""
        request_type = "get_public_block_trades"
        params = {}
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": OkxRequestData._get_public_block_trades_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_public_block_trades_normalize_function(input_data, extra_data):
        """Normalize get public block trades response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_public_block_trades(self, inst_id=None, after=None, before=None, limit=None,
                                 extra_data=None, **kwargs):
        """Get public block trades (single-leg)"""
        path, params, extra_data = self._get_public_block_trades(inst_id, after, before,
                                                                 limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_public_block_trades(self, inst_id=None, after=None, before=None,
                                       limit=None, extra_data=None, **kwargs):
        """Async get public block trades (single-leg)"""
        path, params, extra_data = self._get_public_block_trades(inst_id, after, before,
                                                                 limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)
