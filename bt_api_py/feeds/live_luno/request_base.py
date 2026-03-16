"""
Luno REST API request base class.
"""

import base64
from typing import Any

from bt_api_py.containers.exchanges.luno_exchange_data import LunoExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.http_client import HttpClient
from bt_api_py.logging_factory import get_logger


class LunoRequestData(Feed):
    """Luno REST API Feed base class."""

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
        self.exchange_name = kwargs.get("exchange_name", "LUNO___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self._params = LunoExchangeDataSpot()
        self.request_logger = get_logger("luno_feed")
        self.async_logger = get_logger("luno_feed")
        self._http_client = HttpClient(venue=self.exchange_name, timeout=10)

    def _get_auth_headers(self) -> dict[str, str]:
        """Generate HTTP Basic Auth headers for Luno API."""
        headers = {
            "Content-Type": "application/json",
        }
        if self._params.api_key and self._params.api_secret:
            credentials = base64.b64encode(
                f"{self._params.api_key}:{self._params.api_secret}".encode()
            ).decode("utf-8")
            headers["Authorization"] = f"Basic {credentials}"
        return headers

    def _resolve_url(self, request_path: str) -> str:
        """Resolve base URL based on request path."""
        base_url = self._params.rest_url
        if "candles" in request_path or "markets" in request_path:
            base_url = getattr(self._params, "rest_exchange_url", self._params.rest_url)
        return base_url

    def request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_data: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> RequestData:
        """HTTP request for Luno API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path
        base_url = self._resolve_url(request_path)
        headers = self._get_auth_headers()

        try:
            response = self._http_client.request(
                method=method,
                url=base_url + request_path,
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
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_data: dict[str, Any] | None = None,
        timeout: float = 5,
    ) -> RequestData:
        """Async HTTP request for Luno API."""
        method = path.split()[0] if " " in path else "GET"
        request_path = "/" + path.split()[1] if " " in path else path
        base_url = self._resolve_url(request_path)
        headers = self._get_auth_headers()
        try:
            response = await self._http_client.async_request(
                method=method,
                url=base_url + request_path,
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
        self, response: dict[str, Any], extra_data: dict[str, Any] | None = None
    ) -> RequestData:
        """Process API response."""
        if extra_data is None:
            extra_data = {}
        return RequestData(response, extra_data)

    def _get_server_time(
        self, extra_data: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Prepare server time request. Returns (path, params, extra_data)."""
        path = "GET /ticker"
        if extra_data is None:
            extra_data = {}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": "",
                "asset_type": self.asset_type,
                "request_type": "get_server_time",
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, {"pair": "XBTZAR"}, extra_data

    def get_server_time(
        self, extra_data: dict[str, Any] | None = None, **kwargs: Any
    ) -> RequestData:
        """Get server time (uses ticker timestamp)."""
        path, params, extra_data = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_server_time_normalize_function(
        input_data: Any, extra_data: dict[str, Any]
    ) -> tuple[Any, bool]:
        if not input_data:
            return None, False
        if isinstance(input_data, dict):
            ts = input_data.get("timestamp")
            return ts, True
        return input_data, True

    def push_data_to_queue(self, data: Any) -> None:
        """Push data to the queue."""
        if self.data_queue is not None:
            self.data_queue.put(data)

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True
