"""
Independent Reserve REST API request base class.

API doc: https://www.independentreserve.com/API
Auth: HMAC-SHA256 signature in POST JSON body (apiKey, nonce, signature)
Public: GET /Public/...  Private: POST /Private/...
Symbol: primaryCurrencyCode + secondaryCurrencyCode (e.g. Xbt, Aud)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.independent_reserve_exchange_data import (
    IndependentReserveExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class IndependentReserveRequestData(Feed):
    """Independent Reserve REST API Feed base class."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_DEALS,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.QUERY_OPEN_ORDERS,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self._api_key = kwargs.get("public_key") or kwargs.get("api_key") or ""
        self._api_secret = kwargs.get("private_key") or kwargs.get("api_secret") or ""
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "INDEPENDENT_RESERVE___SPOT")
        self._params = IndependentReserveExchangeDataSpot()
        self.request_logger = get_logger("independent_reserve_feed")
        self.async_logger = get_logger("independent_reserve_feed")

    @property
    def api_key(self):
        return self._api_key

    # ── auth helpers ────────────────────────────────────────────

    def _generate_signature(self, url, nonce, params=None):
        """HMAC-SHA256: message = url,apiKey={key},nonce={nonce},k=v,..."""
        if not self._api_secret:
            return ""
        auth_parts = [url, f"apiKey={self._api_key}", f"nonce={nonce}"]
        if params:
            for k, v in params.items():
                if k not in ("apiKey", "nonce", "signature"):
                    auth_parts.append(f"{k}={v}")
        message = ",".join(auth_parts)
        return (
            hmac.new(
                self._api_secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            )
            .hexdigest()
            .upper()
        )

    def _get_headers(self):
        return {
            "Content-Type": "application/json",
            "User-Agent": "bt_api_py/1.0",
        }

    def _sign_body(self, url, body):
        """Add apiKey, nonce, signature to *body* dict for private endpoints."""
        nonce = int(time.time() * 1000)
        body["apiKey"] = self._api_key
        body["nonce"] = nonce
        sig = self._generate_signature(url, nonce, body)
        if sig:
            body["signature"] = sig
        return body

    # ── core request helpers ────────────────────────────────────

    def push_data_to_queue(self, data):
        if self.data_queue is not None:
            self.data_queue.put(data)

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """Synchronous HTTP request."""
        if params is None:
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers()

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else {}
            if self._api_key:
                json_body = self._sign_body(url, dict(json_body))

        res = self.http_request(method, url, headers, json_body, timeout)
        self.request_logger.info(f"{method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    async def async_request(self, path, params=None, body=None, extra_data=None, timeout=5):
        """Async HTTP request."""
        if params is None:
            params: dict[str, Any] = {}
        method, endpoint = path.split(" ", 1)
        headers = self._get_headers()

        if method == "GET":
            qs = urlencode(params) if params else ""
            url = f"{self._params.rest_url}{endpoint}{'?' + qs if qs else ''}"
            json_body = None
        else:
            url = f"{self._params.rest_url}{endpoint}"
            json_body = body if body is not None else {}
            if self._api_key:
                json_body = self._sign_body(url, dict(json_body))

        res = await self.async_http_request(method, url, headers, json_body, timeout)
        self.async_logger.info(f"async {method} {url} -> {type(res)}")
        return RequestData(res, extra_data)

    def async_callback(self, request_data):
        if request_data is not None:
            self.push_data_to_queue(request_data)

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_tick")
        primary, secondary = self._params.get_symbol(symbol)
        params = {"primaryCurrencyCode": primary, "secondaryCurrencyCode": secondary}
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
        return path, params, extra_data

    def _get_depth(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_depth")
        primary, secondary = self._params.get_symbol(symbol)
        params = {"primaryCurrencyCode": primary, "secondaryCurrencyCode": secondary}
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
        return path, params, extra_data

    def _get_exchange_info(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_exchange_info")
        params: dict[str, Any] = {}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, params, extra_data

    def _get_deals(self, symbol, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_deals")
        primary, secondary = self._params.get_symbol(symbol)
        params = {
            "primaryCurrencyCode": primary,
            "secondaryCurrencyCode": secondary,
            "numberOfRecentTradesToRetrieve": kwargs.get("count", 50),
        }
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_deals",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_deals_normalize_function,
            }
        )
        return path, params, extra_data

    def _make_order(self, symbol, side, order_type, amount, price=None, extra_data=None, **kwargs):
        if "market" in order_type.lower():
            path = self._params.get_rest_path("make_order_market")
        else:
            path = self._params.get_rest_path("make_order_limit")
        primary, secondary = self._params.get_symbol(symbol)
        ir_type = "LimitBid" if side.lower() == "buy" else "LimitOffer"
        if "market" in order_type.lower():
            ir_type = "MarketBid" if side.lower() == "buy" else "MarketOffer"
        body = {
            "primaryCurrencyCode": primary,
            "secondaryCurrencyCode": secondary,
            "orderType": ir_type,
            "volume": amount,
        }
        if price is not None and "limit" in order_type.lower():
            body["price"] = price
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
        return path, body, extra_data

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        path = self._params.get_rest_path("cancel_order")
        body = {"orderGuid": order_id}
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "cancel_order",
                "symbol_name": None,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            }
        )
        return path, body, extra_data

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_open_orders")
        body = {}
        if symbol:
            primary, secondary = self._params.get_symbol(symbol)
            body["primaryCurrencyCode"] = primary
            body["secondaryCurrencyCode"] = secondary
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_open_orders",
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._get_open_orders_normalize_function,
            }
        )
        return path, body, extra_data

    def _get_balance(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_balance")
        body = {}
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
        return path, body, extra_data

    def _get_account(self, extra_data=None, **kwargs):
        path = self._params.get_rest_path("get_account")
        body = {}
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
        return path, body, extra_data

    # ── normalize helpers ───────────────────────────────────────
    # IR returns direct JSON; errors have "Message" key

    @staticmethod
    def _is_error(input_data):
        if input_data is None:
            return True
        return bool(isinstance(input_data, dict) and "Message" in input_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return [input_data], True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and "Trades" in input_data:
            return input_data["Trades"], True
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [{}], True

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and "Data" in input_data:
            return input_data["Data"], True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False

    @staticmethod
    def _get_account_normalize_function(input_data, extra_data):
        if IndependentReserveRequestData._is_error(input_data):
            return [], False
        if isinstance(input_data, list):
            return input_data, True
        if isinstance(input_data, dict) and input_data:
            return [input_data], True
        return [], False
