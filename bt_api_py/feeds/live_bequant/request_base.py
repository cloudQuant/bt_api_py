"""
BeQuant REST API request base class.
"""

import time
import hmac
import hashlib
import base64
from urllib.parse import urlsplit

from bt_api_py.containers.exchanges.bequant_exchange_data import BeQuantExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class BeQuantRequestData(Feed):
    """BeQuant REST API Feed base class."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "BEQUANT___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BeQuantExchangeDataSpot()
        # API credentials from kwargs
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key", "")
        self.api_secret = kwargs.get("private_key") or kwargs.get("api_secret", "")
        self.request_logger = SpdLogManager(
            "./logs/bequant_feed.log", "request", 0, 0, False
        ).create_logger()

    def _generate_signature(self, timestamp, method, url, body=""):
        """Generate HS256 signature for BeQuant API."""
        # BeQuant signature: method + path + [?query] + [body] + timestamp
        api_secret = self.api_secret
        if api_secret:
            parsed = urlsplit(url)
            message = [method, parsed.path]
            if parsed.query:
                message.append('?')
                message.append(parsed.query)
            if body:
                message.append(body)
            message.append(str(timestamp))

            signature = hmac.new(
                api_secret.encode("utf-8"),
                ''.join(message).encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            # Base64 encode: apiKey:signature:timestamp
            auth_data = f"{self.api_key}:{signature}:{timestamp}"
            return base64.b64encode(auth_data.encode()).decode()
        return ""

    def _get_headers(self, method, request_path, params=None, body=""):
        """Generate request headers."""
        # For public endpoints, no auth required
        # For private endpoints, use HS256 auth
        headers = {
            "Content-Type": "application/json",
        }

        # Add auth if api_key is configured
        if self.api_key and request_path.startswith(("/spot", "/margin", "/futures", "/wallet")):
            timestamp = int(time.time() * 1000)
            full_url = self._params.rest_url + request_path
            signature = self._generate_signature(timestamp, method, full_url, body)
            if signature:
                headers["Authorization"] = f"HS256 {signature}"

        return headers

    def request(self, path, params=None, body=None, extra_data=None, timeout=10):
        """HTTP request for BeQuant API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            from bt_api_py.feeds.http_client import HttpClient

            http_client = HttpClient(venue=self.exchange_name, timeout=timeout)
            response = http_client.request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.request_logger.error(f"Request failed: {e}")
            raise

    def _process_response(self, response, extra_data=None):
        """Process API response."""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        return RequestData(response, extra_data)

    def push_data_to_queue(self, data):
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    # ==================== Async Methods ====================

    async def async_request(
        self, path, params=None, body=None, extra_data=None, timeout=10, is_sign=False
    ):
        """Async HTTP request for BeQuant API."""
        if params is None:
            params = {}
        method = path.split()[0] if " " in path else "GET"
        request_path = path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        # Build URL with params
        from urllib.parse import urlencode
        url = self._params.rest_url + request_path
        if params:
            url = f"{url}?{urlencode(params)}"

        try:
            res = await self.async_http_request(method, url, headers, body, timeout)
            from bt_api_py.containers.requestdatas.request_data import RequestData
            return RequestData(res, extra_data)
        except Exception as e:
            self.request_logger.error(f"Async request failed: {e}")
            raise

    def async_callback(self, future):
        """Callback function for async requests, pushes data to data_queue."""
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            import traceback
            self.request_logger.warning(f"async_callback::{e}\n{traceback.format_exc()}")

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker data."""
        from bt_api_py.feeds.live_bequant.spot import BeQuantRequestDataSpot
        # Use the spot implementation if available
        if hasattr(self, '_get_tick'):
            request_type = "get_tick"
            path = "GET /public/ticker"
            extra_data = extra_data or {}
            extra_data.update({
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_tick_normalize_function if hasattr(self, '_get_tick_normalize_function') else None,
            })
            self.submit(
                self.async_request(path, params={"symbols": symbol}, extra_data=extra_data),
                callback=self.async_callback,
            )
        else:
            raise NotImplementedError("async_get_tick not implemented")

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Async get orderbook depth."""
        from bt_api_py.feeds.live_bequant.spot import BeQuantRequestDataSpot
        if hasattr(self, '_get_depth'):
            request_type = "get_depth"
            path = "GET /public/orderbook"
            extra_data = extra_data or {}
            extra_data.update({
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_depth_normalize_function if hasattr(self, '_get_depth_normalize_function') else None,
            })
            self.submit(
                self.async_request(path, params={"symbols": symbol, "limit": count}, extra_data=extra_data),
                callback=self.async_callback,
            )
        else:
            raise NotImplementedError("async_get_depth not implemented")

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Async get kline/candlestick data."""
        from bt_api_py.feeds.live_bequant.spot import BeQuantRequestDataSpot
        if hasattr(self, '_get_kline'):
            request_type = "get_kline"
            path = "GET /public/candles"
            extra_data = extra_data or {}
            extra_data.update({
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_kline_normalize_function if hasattr(self, '_get_kline_normalize_function') else None,
            })
            bequant_period = self._params.kline_periods.get(period, period)
            self.submit(
                self.async_request(path, params={
                    "symbols": symbol,
                    "period": bequant_period,
                    "limit": count,
                    "sort": "DESC",
                }, extra_data=extra_data),
                callback=self.async_callback,
            )
        else:
            raise NotImplementedError("async_get_kline not implemented")
