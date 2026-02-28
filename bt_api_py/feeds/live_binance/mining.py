"""
Binance Mining API - 矿池接口请求类

实现 Binance 矿池相关的所有 REST API 请求，包括：
- 矿工算法列表查询
- 矿工列表查询
- 矿工收益统计查询
"""

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataMining
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BinanceRequestDataMining(BinanceRequestData):
    """Binance Mining API 请求类

    处理所有矿池相关的请求。
    """

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", BinanceExchangeDataMining())
        kwargs.setdefault("exchange_name", "binance_mining")
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "MINING")
        self.logger_name = kwargs.get("logger_name", "binance_mining_feed.log")
        self._params = kwargs["exchange_data"]
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    # ==================== 矿池接口 ====================

    def _get_mining_algo_list(self, extra_data=None, **kwargs):
        """查询矿工算法列表

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_mining_algo_list"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_mining_algo_list(self, extra_data=None, **kwargs):
        """查询矿工算法列表

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_mining_algo_list(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=False)
        return data

    def _get_mining_worker_list(self, algo, user_name, extra_data=None, **kwargs):
        """查询矿工列表

        Args:
            algo: 算法类型 (如: sha256)
            user_name: 矿工账号
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_mining_worker_list"
        path = self._params.get_rest_path(request_type)
        params = {
            "algo": algo,
            "userName": user_name,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": algo,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_mining_worker_list(self, algo, user_name, extra_data=None, **kwargs):
        """查询矿工列表

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_mining_worker_list(
            algo=algo, user_name=user_name, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_mining_statistics(self, algo, user_name, extra_data=None, **kwargs):
        """查询矿工收益统计

        Args:
            algo: 算法类型 (如: sha256)
            user_name: 矿工账号
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_mining_statistics"
        path = self._params.get_rest_path(request_type)
        params = {
            "algo": algo,
            "userName": user_name,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": algo,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_mining_statistics(self, algo, user_name, extra_data=None, **kwargs):
        """查询矿工收益统计

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_mining_statistics(
            algo=algo, user_name=user_name, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
