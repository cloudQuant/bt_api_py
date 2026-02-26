# -*- coding: utf-8 -*-
"""
OKX Account WebSocket base class.
Handles private account channels (orders, account, positions, balance_and_position).
"""
from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData


class OkxAccountWssData(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxAccountWssData, self).__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.account_wss_url)


class OkxMarketWssData(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxMarketWssData, self).__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.wss_url)


class OkxKlineWssData(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super(OkxKlineWssData, self).__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", self._params.kline_wss_url)
