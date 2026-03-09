"""
LocalBitcoins Spot Feed – three-layer sync / async wrappers + WSS stubs.
"""

from typing import Any
from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.feeds.live_localbitcoins.request_base import LocalBitcoinsRequestData
from bt_api_py.logging_factory import get_logger


class LocalBitcoinsRequestDataSpot(LocalBitcoinsRequestData):
    """LocalBitcoins Spot REST Feed."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)

    # ── server time ─────────────────────────────────────────────

    def get_server_time(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_server_time(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_server_time(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_server_time(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── market data ─────────────────────────────────────────────

    def get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_tick(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_tick(symbol, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_ticker(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        return self.get_tick(symbol, extra_data, **kwargs)

    async def async_get_ticker(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        return await self.async_get_tick(symbol, extra_data, **kwargs)

    def get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_exchange_info(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_exchange_info(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── P2P-specific ────────────────────────────────────────────

    def get_ads(self, ad_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_ads(ad_id, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_ads(self, ad_id: Any, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_ads(ad_id, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_online_ads(
        self,
        currency: Any = "USD",
        country_code: Any = "all",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        path, params, extra = self._get_online_ads(currency, country_code, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_online_ads(
        self,
        currency: Any = "USD",
        country_code: Any = "all",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        path, params, extra = self._get_online_ads(currency, country_code, extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    # ── account ─────────────────────────────────────────────────

    def get_balance(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_balance(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_balance(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)

    def get_account(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_account(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra)

    async def async_get_account(self, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra = self._get_account(extra_data, **kwargs)
        return await self.async_request(path, params, extra_data=extra)


# ── WebSocket stubs (kept for backward compatibility) ────────


class LocalBitcoinsMarketWssDataSpot:
    """LocalBitcoins Spot Market WebSocket Data Handler (stub)."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self._params = LocalBitcoinsExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_market_wss.log")
        self.request_logger = get_logger("localbitcoins_spot_market_wss")
        self.wss_url = kwargs.get("wss_url", "")
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class LocalBitcoinsAccountWssDataSpot:
    """LocalBitcoins Spot Account WebSocket Data Handler (stub)."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        self.data_queue = data_queue
        self._params = LocalBitcoinsExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_account_wss.log")
        self.request_logger = get_logger("localbitcoins_spot_account_wss")
        self.wss_url = kwargs.get("wss_url", "")
        self.topics = kwargs.get("topics", [])
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
