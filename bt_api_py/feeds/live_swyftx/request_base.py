"""
Swyftx REST API request base class – Feed pattern.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger


class SwyftxRequestData(Feed):
    """Swyftx REST API Feed base class."""

    @classmethod
    def _capabilities(cls: Any) -> set[Capability]:
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.data_queue = data_queue
        self.exchange_name = kwargs.get("exchange_name", "SWYFTX___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.api_key = kwargs.get("public_key") or kwargs.get("api_key")
        self._params = SwyftxExchangeDataSpot()
        self.request_logger = get_logger("swyftx_feed")
        self.async_logger = get_logger("swyftx_feed")

    # ── auth ────────────────────────────────────────────────────

    def _get_headers(self, **kwargs: Any) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ── request / async_request ─────────────────────────────────

    def request(
        self,
        path: Any,
        params: Any = None,
        body: Any = None,
        extra_data: Any = None,
        timeout: Any = 10,
    ) -> RequestData:
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers()
        response = self.http_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        return self._process_response(response, extra_data)

    async def async_request(
        self,
        path: Any,
        params: Any = None,
        body: Any = None,
        extra_data: Any = None,
        timeout: Any = 10,
    ) -> RequestData:
        method = path.split()[0] if " " in path else "GET"
        endpoint = path.split()[1] if " " in path else path
        url = self._params.rest_url + endpoint
        if params:
            url = url + "?" + urlencode(params)
        headers = self._get_headers()
        response = await self.async_http_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        return self._process_response(response, extra_data)

    def _process_response(self, response: Any, extra_data: Any = None) -> RequestData:
        return RequestData(response, extra_data)

    def push_data_to_queue(self, data: Any) -> None:
        if self.data_queue is not None:
            self.data_queue.put(data)

    def async_callback(self, future: Any) -> None:
        try:
            result = future.result()
            self.push_data_to_queue(result)
        except Exception as e:
            self.async_logger.warning(f"async_callback::{e}")

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        super().disconnect()

    def is_connected(self) -> bool:
        return True

    # ── error detection ─────────────────────────────────────────

    @staticmethod
    def _is_error(data: Any) -> bool:
        if data is None:
            return True
        return bool(isinstance(data, dict) and ("error" in data or "errors" in data))

    # ── _get_xxx internal methods ───────────────────────────────

    def _get_server_time(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        path = self._params.get_rest_path("get_server_time")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_server_time",
                "normalize_function": self._get_server_time_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_tick(
        self, symbol: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        path = self._params.get_rest_path("get_tick", symbol=symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_tick",
                "symbol_name": symbol,
                "normalize_function": self._get_tick_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_depth(
        self, symbol: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        path = self._params.get_rest_path("get_depth", symbol=symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_depth",
                "symbol_name": symbol,
                "normalize_function": self._get_depth_normalize_function,
            }
        )
        return path, {"depth": count}, extra_data

    def _get_kline(
        self, symbol: Any, period: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        path = self._params.get_rest_path("get_kline", symbol=symbol)
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_kline",
                "symbol_name": symbol,
                "normalize_function": self._get_kline_normalize_function,
            }
        )
        return (
            path,
            {
                "interval": self._params.get_period(period),
                "limit": count,
            },
            extra_data,
        )

    def _get_exchange_info(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        path = self._params.get_rest_path("get_exchange_info")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_exchange_info",
                "normalize_function": self._get_exchange_info_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_balance(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        path = self._params.get_rest_path("get_balance")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_balance",
                "normalize_function": self._get_balance_normalize_function,
            }
        )
        return path, None, extra_data

    def _get_account(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        path = self._params.get_rest_path("get_account")
        extra_data = extra_data or {}
        extra_data.update(
            {
                "request_type": "get_account",
                "normalize_function": self._get_account_normalize_function,
            }
        )
        return path, None, extra_data

    # ── normalization functions ──────────────────────────────────

    @staticmethod
    def _get_server_time_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if data is None:
            return [], False
        return [data] if isinstance(data, dict) else [{"serverTime": data}], True

    @staticmethod
    def _get_tick_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if SwyftxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_depth_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if SwyftxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False

    @staticmethod
    def _get_kline_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if SwyftxRequestData._is_error(data):
            return [], False
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_exchange_info_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if SwyftxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_balance_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if SwyftxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        if isinstance(data, list):
            return data, bool(data)
        return [], False

    @staticmethod
    def _get_account_normalize_function(data: Any, extra_data: Any) -> tuple[list[Any], bool]:
        if SwyftxRequestData._is_error(data):
            return [], False
        if isinstance(data, dict):
            return [data], True
        return [], False
