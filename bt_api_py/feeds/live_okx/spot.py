from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSpot
from bt_api_py.feeds.live_okx.account_wss_base import (
    OkxAccountWssData,
    OkxKlineWssData,
    OkxMarketWssData,
)
from bt_api_py.feeds.live_okx.market_wss_base import OkxWssData
from bt_api_py.feeds.live_okx.request_base import OkxRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class OkxRequestDataSpot(OkxRequestData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "okx_spot_feed.log")
        self._params = OkxExchangeDataSpot()
        self.request_logger = get_logger("okx_spot_feed")
        self.async_logger = get_logger("okx_spot_feed")

    def _get_index_price(self, symbol, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol) if symbol is not None else ""
        request_type = "get_index_price"
        params = {
            "instId": request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": request_symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": OkxRequestDataSpot._get_index_price_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_index_price_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = input_data["code"] == "0"
        data = input_data["data"][0]
        timestamp = float(data["ts"])
        data = [timestamp, float(data["idxPx"])]
        return data, status

    def get_index_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_index_price(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data


class OkxAccountWssDataSpot(OkxAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class OkxMarketWssDataSpot(OkxMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class OkxKlineWssDataSpot(OkxKlineWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")


class OkxWssDataSpot(OkxWssData):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
