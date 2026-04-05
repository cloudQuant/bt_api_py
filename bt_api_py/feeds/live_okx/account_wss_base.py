"""
OKX Account WebSocket base class.
Handles private account channels (orders, account, positions, balance_and_position).
"""

from __future__ import annotations

from typing import Any

from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData


class OkxAccountWssData(OkxWssData):
    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.account_wss_url)


class OkxMarketWssData(OkxWssData):
    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.wss_url)


class OkxKlineWssData(OkxWssData):
    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.kline_wss_url)
