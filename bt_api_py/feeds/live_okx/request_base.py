"""
OKX REST API request base class.
Handles authentication, signing, and all REST API methods.
API methods are organized into Mixin classes under the mixins/ package.
"""

import base64
import hmac
import json
import time
from typing import Any
from urllib import parse

from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import OKXErrorTranslator
from bt_api_py.exceptions import QueueNotInitializedError
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.live_okx.mixins.account_mixin import AccountMixin
from bt_api_py.feeds.live_okx.mixins.copy_trading_mixin import CopyTradingMixin
from bt_api_py.feeds.live_okx.mixins.funding_mixin import FundingMixin
from bt_api_py.feeds.live_okx.mixins.grid_trading_mixin import GridTradingMixin
from bt_api_py.feeds.live_okx.mixins.market_data_mixin import MarketDataMixin
from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function
from bt_api_py.feeds.live_okx.mixins.rfq_mixin import RfqMixin
from bt_api_py.feeds.live_okx.mixins.spread_trading_mixin import SpreadTradingMixin
from bt_api_py.feeds.live_okx.mixins.statistics_mixin import StatisticsMixin
from bt_api_py.feeds.live_okx.mixins.status_mixin import StatusMixin
from bt_api_py.feeds.live_okx.mixins.sub_account_mixin import SubAccountMixin
from bt_api_py.feeds.live_okx.mixins.trade_mixin import TradeMixin
from bt_api_py.feeds.live_okx.mixins.trading_account_mixin import TradingAccountMixin
from bt_api_py.logging_factory import get_logger
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType


class OkxRequestData(
    AccountMixin,
    CopyTradingMixin,
    FundingMixin,
    GridTradingMixin,
    MarketDataMixin,
    RfqMixin,
    SpreadTradingMixin,
    StatisticsMixin,
    StatusMixin,
    SubAccountMixin,
    TradeMixin,
    TradingAccountMixin,
    Feed,
):
    @classmethod
    def _capabilities(cls: Any) -> set[Capability]:
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

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.public_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.private_key = (
            kwargs.get("private_key") or kwargs.get("secret_key") or kwargs.get("api_secret")
        )
        self.passphrase = kwargs.get("passphrase")
        self.topics = kwargs.get("topics", {})
        self.exchange_name = kwargs.get("exchange_name", "OKX___SWAP")
        self.asset_type = kwargs.get("asset_type", "SWAP")
        self.logger_name = kwargs.get("logger_name", "okx_swap_feed.log")
        self._params = OkxExchangeDataSwap()
        self.request_logger = get_logger("okx_swap_feed")
        self.async_logger = get_logger("okx_swap_feed")
        self._error_translator = OKXErrorTranslator()
        self._rate_limiter = kwargs.get("rate_limiter", self._create_default_rate_limiter())

    @staticmethod
    def _create_default_rate_limiter() -> RateLimiter:
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

    def translate_error(self, raw_response: Any) -> None:
        """将原始 OKX API 响应翻译为 UnifiedError（如有错误），否则返回 None"""
        if isinstance(raw_response, dict):
            code = raw_response.get("code", raw_response.get("sCode", "0"))
            if str(code) != "0":
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def push_data_to_queue(self, data: Any) -> None:
        if self.data_queue is not None:
            self.data_queue.put(data)
        else:
            raise QueueNotInitializedError("data_queue not initialized")

    # noinspection PyMethodMayBeStatic
    def signature(
        self, timestamp: Any, method: Any, request_path: Any, secret_key: Any, body: Any = None
    ) -> str:
        body = "" if body is None else str(body)
        message = str(timestamp) + str.upper(method) + request_path + body
        mac = hmac.new(
            bytes(secret_key, encoding="utf8"), bytes(message, encoding="utf-8"), digestmod="sha256"
        )
        d = mac.digest()
        return base64.b64encode(d).decode()

    # noinspection PyMethodMayBeStatic
    def get_header(self, api_key: Any, sign: Any, timestamp: Any, passphrase: Any) -> dict[str, Any]:
        header = {}
        header["Content-Type"] = "application/json"
        header["OK-ACCESS-KEY"] = api_key
        header["OK-ACCESS-SIGN"] = sign
        header["OK-ACCESS-TIMESTAMP"] = str(timestamp)
        header["OK-ACCESS-PASSPHRASE"] = passphrase
        header["x-simulated-trading"] = "0"
        return header

    def request(
        self,
        path: Any,
        params: Any = None,
        body: Any = None,
        extra_data: Any = None,
        timeout: Any = 10,
    ) -> RequestData:
        """http request function
        Args:
            path (TYPE): request url
            params (dict, optional): in url
            body (dict, optional): in request body
            extra_data(dict,None): extra_data, generate by user
            timeout (int, optional): request timeout(s)
        """
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}
        method, path = path.split(" ", 1)
        req = parse.urlencode(params)
        url = f"{self._params.rest_url}{path}?{req}"  # ?{req}
        if params:
            path = f"{path}?{req}"
        timestamp = round(time.time(), 3)
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else None
        signature_ = self.signature(
            timestamp,
            method,
            path,
            self.private_key,
            body_str,
        )
        headers = self.get_header(self.public_key, signature_, timestamp, self.passphrase)
        res = self.http_request(method, url, headers, body, timeout)
        return RequestData(res, extra_data)

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=5
    ) -> RequestData:
        """http request function
        Args:
            path (TYPE): request url
            params (dict, optional): in url
            body (dict, optional): in request body
            timeout (int, optional): request timeout(s)
            extra_data(dict,None): extra_data, generate by user
        """
        if params is None:
            params: dict[str, Any] = {}
        if extra_data is None:
            extra_data = {}
        method, path = path.split(" ", 1)
        req = parse.urlencode(params)
        url = f"{self._params.rest_url}{path}?{req}"  # ?{req}
        if params:
            path = f"{path}?{req}"
        timestamp = round(time.time(), 3)
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else None
        signature_ = self.signature(
            timestamp,
            method,
            path,
            self.private_key,
            body_str,
        )
        headers = self.get_header(self.public_key, signature_, timestamp, self.passphrase)
        res = await self._http_client.async_request(
            method=method,
            url=url,
            headers=headers,
            json_data=body_str,
            timeout=timeout,
        )
        return RequestData(res, extra_data)

    def async_callback(self, future: Any) -> None:
        """
        callback function for async requests, push result to data_queue
        :param future: asyncio future object
        :return: None
        """
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    @staticmethod
    def _generic_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Generic normalize function for OKX API responses.
        Extracts 'data' list and checks 'code' for status.
        Delegates to the shared normalizers module."""
        return generic_normalize_function(input_data, extra_data)
