# -*- coding: utf-8 -*-
"""
Binance Staking API - 质押理财接口请求类

实现 Binance 质押理财相关的所有 REST API 请求，包括：
- Staking 产品查询
- Staking 产品购买
- Staking 产品赎回
- Staking 持仓查询
- Staking 历史记录查询
"""

from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataStaking
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BinanceRequestDataStaking(BinanceRequestData):
    """Binance Staking API 请求类

    处理所有质押理财相关的请求。
    """

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault('exchange_data', BinanceExchangeDataStaking())
        kwargs.setdefault('exchange_name', 'binance_staking')
        super(BinanceRequestDataStaking, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "STAKING")
        self.logger_name = kwargs.get("logger_name", "binance_staking_feed.log")
        self._params = kwargs['exchange_data']
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

    # ==================== Staking 产品接口 ====================

    def _get_staking_products(self, product_type, asset=None, size=None, current=None,
                              extra_data=None, **kwargs):
        """查询 Staking 产品

        Args:
            product_type: 产品类型 (STAKING, F_DEFI, L_DEFI)
            asset: 资产名称
            size: 每页数量
            current: 当前页
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_staking_products'
        path = self._params.get_rest_path(request_type)
        params = {
            'type': product_type,
        }
        if asset is not None:
            params['asset'] = asset
        if size is not None:
            params['size'] = size
        if current is not None:
            params['current'] = current
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": asset or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_staking_products(self, product_type, asset=None, size=None, current=None,
                             extra_data=None, **kwargs):
        """查询 Staking 产品

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_staking_products(
            product_type=product_type, asset=asset, size=size, current=current,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _staking_purchase(self, product_id, amount, auto_renew=None, extra_data=None, **kwargs):
        """购买 Staking 产品

        Args:
            product_id: 产品ID
            amount: 购买数量
            auto_renew: 是否自动续期
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'staking_purchase'
        path = self._params.get_rest_path(request_type)
        params = {
            'productId': product_id,
            'amount': amount,
        }
        if auto_renew is not None:
            params['renew'] = auto_renew
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": str(product_id),
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def staking_purchase(self, product_id, amount, auto_renew=None, extra_data=None, **kwargs):
        """购买 Staking 产品

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._staking_purchase(
            product_id=product_id, amount=amount, auto_renew=auto_renew,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _staking_redeem(self, product_id, amount, position_id=None, extra_data=None, **kwargs):
        """赎回 Staking 产品

        Args:
            product_id: 产品ID
            amount: 赎回数量
            position_id: 持仓ID (部分赎回时必填)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'staking_redeem'
        path = self._params.get_rest_path(request_type)
        params = {
            'productId': product_id,
            'amount': amount,
        }
        if position_id is not None:
            params['positionId'] = position_id
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": str(product_id),
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def staking_redeem(self, product_id, amount, position_id=None, extra_data=None, **kwargs):
        """赎回 Staking 产品

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._staking_redeem(
            product_id=product_id, amount=amount, position_id=position_id,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_staking_position(self, product_type=None, asset=None, size=None, current=None,
                              extra_data=None, **kwargs):
        """查询 Staking 持仓

        Args:
            product_type: 产品类型 (STAKING, F_DEFI, L_DEFI)
            asset: 资产名称
            size: 每页数量
            current: 当前页
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_staking_position'
        path = self._params.get_rest_path(request_type)
        params = {}
        if product_type is not None:
            params['type'] = product_type
        if asset is not None:
            params['asset'] = asset
        if size is not None:
            params['size'] = size
        if current is not None:
            params['current'] = current
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": asset or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_staking_position(self, product_type=None, asset=None, size=None, current=None,
                             extra_data=None, **kwargs):
        """查询 Staking 持仓

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_staking_position(
            product_type=product_type, asset=asset, size=size, current=current,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_staking_history(self, product_type=None, asset=None, start_time=None, end_time=None,
                             size=None, current=None, extra_data=None, **kwargs):
        """查询 Staking 历史记录

        Args:
            product_type: 产品类型 (STAKING, F_DEFI, L_DEFI)
            asset: 资产名称
            start_time: 开始时间戳
            end_time: 结束时间戳
            size: 每页数量
            current: 当前页
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_staking_history'
        path = self._params.get_rest_path(request_type)
        params = {}
        if product_type is not None:
            params['type'] = product_type
        if asset is not None:
            params['asset'] = asset
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time
        if size is not None:
            params['size'] = size
        if current is not None:
            params['current'] = current
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": asset or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_staking_history(self, product_type=None, asset=None, start_time=None, end_time=None,
                            size=None, current=None, extra_data=None, **kwargs):
        """查询 Staking 历史记录

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_staking_history(
            product_type=product_type, asset=asset, start_time=start_time,
            end_time=end_time, size=size, current=current,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
