"""
Swyftx Spot Feed – three-layer sync / async wrappers + WSS stubs.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_swyftx.request_base import SwyftxRequestData
from bt_api_py.logging_factory import get_logger


class SwyftxRequestDataSpot(SwyftxRequestData):
    """Swyftx Spot REST Feed."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)

    # ── server time ─────────────────────────────────────────────

    def get_server_time(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, extra = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_server_time(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, extra = self._get_server_time(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── market data ─────────────────────────────────────────────

    def get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_tick(
        self, symbol: Any, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_ticker(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> RequestData:
        return self.get_tick(symbol, extra_data, **kwargs)

    async def async_get_ticker(
        self, symbol: Any, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        return await self.async_get_tick(symbol, extra_data, **kwargs)

    def get_depth(
        self, symbol: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_depth(
        self, symbol: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_depth(symbol, count, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_kline(
        self, symbol: Any, period: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_kline(
        self, symbol: Any, period: Any, count: Any = 20, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── account ─────────────────────────────────────────────────

    def get_balance(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> RequestData:
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_balance(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_account(
        self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_account(
        self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        path, params, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)


# ── WebSocket stubs ──────────────────────────────────────────


class SwyftxMarketWssDataSpot:
    """Swyftx Spot Market WebSocket Data Handler (stub)."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self._params = SwyftxExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "swyftx_spot_market_wss.log")
        self.request_logger = get_logger("swyftx_spot_market_wss")
        self.wss_url = kwargs.get("wss_url", "")
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class SwyftxAccountWssDataSpot:
    """Swyftx Spot Account WebSocket Data Handler (stub)."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self._params = SwyftxExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "swyftx_spot_account_wss.log")
        self.request_logger = get_logger("swyftx_spot_account_wss")
        self.wss_url = kwargs.get("wss_url", "")
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
