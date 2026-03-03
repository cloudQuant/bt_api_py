"""
HitBTC REST API request base class.
Handles authentication, signing, and all REST API methods.
API methods are organized into Mixin classes under the mixins/ package.
"""

import base64
import hmac
import json
import time
from urllib import parse

from bt_api_py.containers.exchanges.hitbtc_exchange_data import HitBtcExchangeData, HitBtcSpotExchangeData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error_framework_hitbtc import HitBtcErrorTranslator
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class HitBtcRequestData(Feed):
    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_DEALS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key")
        self.private_key = kwargs.get("private_key")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "hitbtc_spot_feed.log")
        self._params = HitBtcExchangeData()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()
        self._error_translator = HitBtcErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter():
        rules = [
            RateLimitRule(
                name="hitbtc_market_data",
                limit=100,
                interval=1,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="hitbtc_trading",
                limit=300,
                interval=1,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
            RateLimitRule(
                name="hitbtc_wallet",
                limit=10,
                interval=1,
                type=RateLimitType.SLIDING_WINDOW,
                scope=RateLimitScope.GLOBAL,
            ),
        ]
        return RateLimiter(rules)

    def translate_error(self, raw_response):
        """将原始 HitBTC API 响应翻译为 UnifiedError（如有错误），否则返回 None"""
        if isinstance(raw_response, dict):
            code = raw_response.get("code")
            if code is not None:
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            assert 0, "队列未初始化"

    def sign(self, content):
        """签名
        Args:
            content (TYPE): 签名内容
        """
        sign = hmac.new(
            self.private_key.encode("utf-8"), content.encode("utf-8"), digestmod="sha256"
        ).hexdigest()

        return sign

    def request(self, method, path, params=None, body=None, extra_data=None, timeout=10, is_sign=True):
        """HTTP request function
        Args:
            method (str): HTTP method (GET, POST, DELETE)
            path (str): request path
            params (dict, optional): query parameters
            body (dict, optional): request body
            extra_data(dict, optional): extra data, generate by user
            timeout (int, optional): request timeout(s)
            is_sign (bool, optional): is need signature
        """
        if params is None:
            params = {}
        if body is None:
            body = {}

        url = f"{self._params.rest_url}{path}"

        if is_sign:
            # Add Basic Auth header
            auth_string = f"{self.public_key}:{self.private_key}"
            auth_bytes = auth_string.encode('ascii')
            auth_header = base64.b64encode(auth_bytes).decode('ascii')
            headers = {"Authorization": f"Basic {auth_header}"}
        else:
            headers = {}

        # Add query parameters
        if params:
            url = f"{url}?{parse.urlencode(params)}"

        # Make request
        response = self.http_request(method, url, headers=headers, body=body, timeout=timeout)

        # Check for errors
        if isinstance(response, dict) and "error" in response:
            error = self.translate_error(response)
            if error:
                raise error
            return response

        return RequestData(response, extra_data)

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time"""
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_server_time_normalize_function,
        )
        return self.request("GET", path, extra_data=extra_data, is_sign=False)

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        status = input_data is not None
        if status:
            data = input_data.get("server_time", 0)
        else:
            data = None
        return data, status

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info and symbols"""
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        extra_data = self._update_extra_data(
            extra_data,
            request_type=request_type,
            exchange_name=self.exchange_name,
            normalize_function=self._get_exchange_info_normalize_function,
        )
        return self.request("GET", path, extra_data=extra_data, is_sign=False)

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        status = input_data is not None
        if status and isinstance(input_data, dict):
            symbols = input_data.get("symbols", {})
            data = symbols
        else:
            data = {}
        return data, status

    def _update_extra_data(self, extra_data, **kwargs):
        """Update extra_data with default values"""
        if extra_data is None:
            extra_data = {}
        extra_data.update(kwargs)
        return extra_data

    def async_get_server_time(self, extra_data=None, **kwargs):
        """Get server time async"""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def _get_server_time(self, extra_data=None, **kwargs):
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = self._update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            },
        )
        return path, params, extra_data

    def async_get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info async"""
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        self.submit(
            self.async_request("GET", path, params=params, extra_data=extra_data, is_sign=False),
            callback=self.async_callback,
        )

    def _get_exchange_info(self, extra_data=None, **kwargs):
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = self._update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )
        return path, params, extra_data

    def async_callback(self, request_data):
        """Async callback function"""
        if request_data.status:
            self.push_data_to_queue(request_data)