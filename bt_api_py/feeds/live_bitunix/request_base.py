"""
Bitunix REST API request base class.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Optional

from bt_api_py.containers.exchanges.bitunix_exchange_data import BitunixExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger

RequestParams = dict[str, Any]
RequestExtraData = dict[str, Any]
RequestSpec = tuple[str, Optional[RequestParams], RequestExtraData]


class BitunixRequestData(Feed):
    """Bitunix REST API Feed base class."""

    @classmethod
    def _capabilities(cls) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
        }

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "BITUNIX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = BitunixExchangeDataSpot()
        self._params.api_key = kwargs.get("public_key") or kwargs.get("api_key")
        self._params.api_secret = (
            kwargs.get("private_key") or kwargs.get("secret_key") or kwargs.get("api_secret")
        )
        self.request_logger = get_logger("bitunix_feed")
        self.async_logger = get_logger("bitunix_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _generate_signature(
        self, nonce: str, timestamp: str, query_params: str = "", body: str = ""
    ) -> str:
        """Generate dual SHA256 signature for Bitunix API.

        Bitunix uses dual SHA256 signature (not HMAC):
        1. First round: SHA256(nonce + timestamp + apiKey + queryParams + body)
        2. Second round: SHA256(digest + secretKey)
        """
        api_key = self._params.api_key
        secret = self._params.api_secret
        if not secret:
            return ""

        # First SHA256
        first_input = nonce + timestamp + api_key + query_params + body
        digest = hashlib.sha256(first_input.encode("utf-8")).hexdigest()

        # Second SHA256
        second_input = digest + secret
        return hashlib.sha256(second_input.encode("utf-8")).hexdigest()

    def _get_headers(
        self,
        method: str,
        request_path: str,
        params: RequestParams | None = None,
        body: Any = "",
    ) -> dict[str, str]:
        """Generate request headers."""
        nonce = str(uuid.uuid4()).replace("-", "")[:32]
        timestamp = str(int(time.time() * 1000))

        # Build query string for GET requests
        query_params = ""
        if params and method == "GET":
            sorted_params = sorted(params.items())
            query_params = "&".join(f"{k}={v}" for k, v in sorted_params)

        headers = {
            "Content-Type": "application/json",
            "api-key": self._params.api_key if self._params.api_key else "",
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": self._generate_signature(nonce, timestamp, query_params, body),
        }
        return headers

    def request(
        self,
        path: str,
        params: RequestParams | None = None,
        body: Any | None = None,
        extra_data: RequestExtraData | None = None,
        timeout: int = 10,
    ) -> RequestData:
        """HTTP request for Bitunix API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            response = self._http_client.request(
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

    async def async_request(
        self,
        path: str,
        params: RequestParams | None = None,
        body: Any | None = None,
        extra_data: RequestExtraData | None = None,
        timeout: int = 5,
    ) -> RequestData:
        """Async HTTP request for Bitunix API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path

        headers = self._get_headers(method, request_path, params, body)

        try:
            response = await self._http_client.async_request(
                method=method,
                url=self._params.rest_url + request_path,
                headers=headers,
                json_data=body if method == "POST" else None,
                params=params,
            )
            return self._process_response(response, extra_data)
        except Exception as e:
            self.async_logger.error(f"Async request failed: {e}")
            raise

    def async_callback(self, future: Any) -> None:
        """Callback for async requests, push result to data_queue."""
        try:
            result = future.result()
            if result is not None:
                self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.error(f"Async callback error: {e}")

    def _process_response(
        self, response: dict[str, Any] | list[Any], extra_data: RequestExtraData | None = None
    ) -> RequestData:
        """Process API response."""
        if extra_data is None:
            extra_data = {}
        return RequestData(response, extra_data)

    def _get_server_time(
        self, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestSpec:
        """Prepare server time request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
            }
        )
        return "GET /api/v1/futures/market/serverTime", {}, extra_data

    def get_server_time(
        self, extra_data: RequestExtraData | None = None, **kwargs: Any
    ) -> RequestData:
        """Get server time. Returns RequestData."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def push_data_to_queue(self, data: Any) -> None:
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        super().disconnect()

    def is_connected(self) -> bool:
        return True
