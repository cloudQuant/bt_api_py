"""HTX Coin-M Inverse Swap Trading Feed

Uses api.hbdm.com with /swap-api/ and /swap-ex/ prefixes.
Symbol format: BTC-USD (uppercase, dash-separated).
Market data uses 'contract_code' parameter instead of 'symbol'.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataCoinSwap
from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot, HtxMarketWssDataSpot
from bt_api_py.feeds.live_htx.usdt_swap import HtxRequestDataUsdtSwap
from bt_api_py.logging_factory import get_logger


class HtxRequestDataCoinSwap(HtxRequestDataUsdtSwap):
    """HTX Coin-M Inverse Swap REST API feed.

    Shares the same method structure as USDT swap (contract_code param),
    just with different exchange data (paths, base URL, symbol format).
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "COIN_SWAP")
        self.logger_name = kwargs.get("logger_name", "htx_coin_swap_feed.log")
        self._params = HtxExchangeDataCoinSwap()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")


class HtxMarketWssDataCoinSwap(HtxMarketWssDataSpot):
    """HTX Coin Swap Market WebSocket data feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", HtxExchangeDataCoinSwap())
        kwargs.setdefault("asset_type", "COIN_SWAP")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataCoinSwap(HtxAccountWssDataSpot):
    """HTX Coin Swap Account WebSocket data feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", HtxExchangeDataCoinSwap())
        kwargs.setdefault("asset_type", "COIN_SWAP")
        super().__init__(data_queue, **kwargs)
