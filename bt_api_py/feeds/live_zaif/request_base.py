"""
Zaif REST API request base class – Feed pattern.
"""

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.zaif_exchange_data import ZaifExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class ZaifRequestData(Feed):
    """Zaif REST API Feed base class."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "ZAIF___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key")
        self.api_secret = kwargs.get("secret_key") or kwargs.get("api_secret")
        self._params = ZaifExchangeDataSpot()
        self.request_logger = get_logger("zaif_feed")
        self.async_logger = get_logger("zaif_feed")

    # ── auth ────────────────────────────────────────────────────

    def _generate_signature(self, body_string):
        if self.api_secret:
            return hmac.new(
                self.api_secret.encode("utf-8"),
                body_string.encode("utf-8"),
                hashlib.sha512,
            ).hexdigest()
        return ""

    def _get_headers(self, body=""):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.api_key:
            headers["Key"] = self.api_key
        if self.api_secret and body:
            headers["Sign"] = self._generate_signature(body)
        return headers

    # ── request / async_request ─────────────────────────────────

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers(body or "")
        response = self.http_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        return self._process_response(response, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=10):
        if extra_data is None:
            extra_data = {}
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers(body or "")
        response = await self._http_client.async_request(
            method=method,
            url=url,
            headers=headers,
            json_data=body,
            timeout=timeout,
        )
        return self._process_response(response, extra_data)

    def _process_response(self, response, extra_data=None):
        return RequestData(response, extra_data)

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def async_callback(self, future):
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    def connect(self):
        pass

    def disconnect(self):
        super().disconnect()

    def is_connected(self):
        return True

    # ── error detection ─────────────────────────────────────────

    @staticmethod
    def _is_error(data):
        if data is None:
            return True
        return bool(isinstance(data, dict) and ("error" in data or data.get("success") == 0))

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(self, extra_data=None, **kwargs):
        pair = kwargs.get("pair", "btc_jpy")
        path = self._params.get_rest_path("get_server_time", pair=pair)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_server_time",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        pair = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_tick", pair=pair)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_tick",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_tick_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        pair = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_depth", pair=pair)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_depth",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        pair = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_kline", pair=pair)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_balance",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_account",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, None, extra_data

    # ── trading internal methods ─────────────────────────────────

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        pair = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("make_order")
        side, otype = order_type.split("-") if "-" in order_type else (order_type, "limit")
        nonce = int(time.time())
        params = {
            "method": "trade",
            "nonce": nonce,
            "currency_pair": pair,
            "action": side.lower(),
            "price": price,
            "amount": vol,
        }
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "make_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._make_order_normalize_function,
            }
        )
        return path, params, extra_data

    def _cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("cancel_order")
        nonce = int(time.time())
        params = {
            "method": "cancel_order",
            "nonce": nonce,
            "order_id": order_id,
        }
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "cancel_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            }
        )
        return path, params, extra_data

    def _query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("query_order")
        nonce = int(time.time())
        params = {
            "method": "active_orders",
            "nonce": nonce,
            "currency_pair": self._params.get_symbol(symbol),
        }
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "query_order",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._query_order_normalize_function,
            }
        )
        return path, params, extra_data

    # ── normalization functions ──────────────────────────────────

    @staticmethod
    def _get_server_time_normalize_function(data, extra_data):
        if data is None:
            return [], False
        return [data] if isinstance(data, dict) else [{"last_price": data}], True

    @staticmethod
    def _get_tick_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(data, extra_data):
        if data is None:
            return [], False
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, list):
            return data, bool(data)
        return [data], True

    @staticmethod
    def _get_exchange_info_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, list):
            return data, bool(data)
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_account_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _make_order_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _cancel_order_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _query_order_normalize_function(data, extra_data):
        if ZaifRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False
