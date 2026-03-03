"""HTX Coin-M Inverse Swap Trading Feed

Uses api.hbdm.com with /swap-api/ and /swap-ex/ prefixes.
Symbol format: BTC-USD (uppercase, dash-separated).
Market data uses 'contract_code' parameter instead of 'symbol'.
"""

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataCoinSwap
from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot, HtxMarketWssDataSpot
from bt_api_py.feeds.live_htx.usdt_swap import HtxRequestDataUsdtSwap
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import get_project_log_path


class HtxRequestDataCoinSwap(HtxRequestDataUsdtSwap):
    """HTX Coin-M Inverse Swap REST API feed.

    Shares the same method structure as USDT swap (contract_code param),
    just with different exchange data (paths, base URL, symbol format).
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "COIN_SWAP")
        self.logger_name = kwargs.get("logger_name", "htx_coin_swap_feed.log")
        self._params = HtxExchangeDataCoinSwap()
        self.request_logger = SpdLogManager(
            get_project_log_path(self.logger_name), "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            get_project_log_path(self.logger_name), "async_request", 0, 0, False
        ).create_logger()


class HtxMarketWssDataCoinSwap(HtxMarketWssDataSpot):
    """HTX Coin Swap Market WebSocket data feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataCoinSwap())
        kwargs.setdefault("asset_type", "COIN_SWAP")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataCoinSwap(HtxAccountWssDataSpot):
    """HTX Coin Swap Account WebSocket data feed."""

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataCoinSwap())
        kwargs.setdefault("asset_type", "COIN_SWAP")
        super().__init__(data_queue, **kwargs)
