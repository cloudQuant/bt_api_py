"""LocalBitcoins REST Feed base – HMAC-SHA256 auth, _get_xxx pattern."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class LocalBitcoinsRequestData(Feed):
    """LocalBitcoins REST Feed base – P2P exchange, HMAC-SHA256 auth."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "LOCALBITCOINS___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_feed.log")
        self._params = kwargs.get("exchange_data", LocalBitcoinsExchangeDataSpot())

        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        self.api_key = kwargs.get("public_key", None) or kwargs.get("api_key", None)
        self.api_secret = kwargs.get("private_key", None) or kwargs.get("api_secret", None)
        self.rest_url = self._params.rest_url

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Synchronous HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers(method=method, path=endpoint)

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else params
            if not json_body:
                json_body = None

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request."""
        if params is None:
            params = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers(method=method, path=endpoint)

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else params
            if not json_body:
                json_body = None

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── capabilities ────────────────────────────────────────────

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_EXCHANGE_INFO,
        }

    # ── auth ────────────────────────────────────────────────────

    def _generate_signature(self, method, path, params_str="", body_str=""):
        """HMAC-SHA256 → hex digest.  nonce+key+path+query+body"""
        nonce = str(int(time.time() * 1000))
        msg = f"{nonce}{self.api_key}{path}{params_str}{body_str}"
        sig = hmac.new(
            self.api_secret.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()
        return nonce, sig

    def _get_headers(self, method="GET", path="", params=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key and self.api_secret and "/api/" in path:
            qs = urlencode(params) if params else ""
            nonce, sig = self._generate_signature(method, path, qs)
            headers["Apiauth-Key"] = self.api_key
            headers["Apiauth-Nonce"] = nonce
            headers["Apiauth-Signature"] = sig
        return headers

    # ── error detection ─────────────────────────────────────────

    @staticmethod
    def _is_error(data):
        if data is None:
            return True
        if isinstance(data, dict) and "error" in data:
            return True
        return False

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_tick",
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_tick_normalize_function,
        })
        return path, {}, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_exchange_info",
            "symbol_name": None,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_exchange_info_normalize_function,
        })
        return path, {}, extra_data

    def _get_server_time(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_server_time")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_server_time",
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_server_time_normalize_function,
        })
        return path, {}, extra_data

    def _get_ads(self, ad_id, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_ads", id=ad_id)
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_ads",
            "ad_id": ad_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_ads_normalize_function,
        })
        return path, {}, extra_data

    def _get_online_ads(self, currency="USD", country_code="all", extra_data=None, **kwargs):
        path = self._params.get_rest_path(
            "get_online_ads", currency=currency.lower(), country_code=country_code.lower()
        )
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_online_ads",
            "currency": currency,
            "country_code": country_code,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_ads_normalize_function,
        })
        return path, {}, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_balance",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_balance_normalize_function,
        })
        return path, {}, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        extra_data = update_extra_data(extra_data, **{
            "request_type": "get_account",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._get_account_normalize_function,
        })
        return path, {}, extra_data

    # ── normalization functions ──────────────────────────────────

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if LocalBitcoinsRequestData._is_error(input_data):
            return input_data, False
        if isinstance(input_data, dict):
            return [input_data], True
        return input_data, False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if LocalBitcoinsRequestData._is_error(input_data):
            return input_data, False
        if isinstance(input_data, dict):
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return input_data, False

    @staticmethod
    def _get_server_time_normalize_function(input_data, extra_data):
        if input_data is None:
            return None, False
        return [input_data], True

    @staticmethod
    def _get_ads_normalize_function(input_data, extra_data):
        if LocalBitcoinsRequestData._is_error(input_data):
            return input_data, False
        if isinstance(input_data, dict):
            return [input_data], True
        if isinstance(input_data, list):
            return input_data, True
        return input_data, False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if LocalBitcoinsRequestData._is_error(input_data):
            return input_data, False
        if isinstance(input_data, dict):
            return [input_data], True
        return input_data, False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if LocalBitcoinsRequestData._is_error(input_data):
            return input_data, False
        if isinstance(input_data, dict):
            return [input_data], True
        return input_data, False
