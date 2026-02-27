# -*- coding: utf-8 -*-
"""
Binance Grid Trading API - 网格交易接口请求类

实现 Binance 网格交易相关的所有 REST API 请求，包括：
- 合约网格订单创建和撤销
- 合约网格订单查询
- 合约网格持仓查询
- 合约网格收益查询
"""

from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataGrid
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BinanceRequestDataGrid(BinanceRequestData):
    """Binance Grid Trading API 请求类

    处理所有网格交易相关的请求。
    """

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault('exchange_data', BinanceExchangeDataGrid())
        kwargs.setdefault('exchange_name', 'binance_grid')
        super(BinanceRequestDataGrid, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "GRID")
        self.logger_name = kwargs.get("logger_name", "binance_grid_feed.log")
        self._params = kwargs['exchange_data']
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

    # ==================== 合约网格交易接口 ====================

    def _futures_grid_new_order(self, symbol, upper_price, lower_price, grid_quantity,
                                base_quantity, interval_type=None, extra_data=None, **kwargs):
        """创建合约网格订单

        Args:
            symbol: 交易对
            upper_price: 上限价格
            lower_price: 下限价格
            grid_quantity: 网格数量
            base_quantity: 每格数量
            interval_type: 间隔类型 (如: GEOMETRIC, ARITHMETIC)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'futures_grid_new_order'
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'pair': request_symbol,
            'upperPrice': upper_price,
            'lowerPrice': lower_price,
            'gridNumber': grid_quantity,
            'investAmount': base_quantity,
        }
        if interval_type is not None:
            params['intervalType'] = interval_type
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def futures_grid_new_order(self, symbol, upper_price, lower_price, grid_quantity,
                               base_quantity, interval_type=None, extra_data=None, **kwargs):
        """创建合约网格订单

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._futures_grid_new_order(
            symbol=symbol, upper_price=upper_price, lower_price=lower_price,
            grid_quantity=grid_quantity, base_quantity=base_quantity,
            interval_type=interval_type, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _futures_grid_cancel_order(self, symbol, order_id=None, client_order_id=None,
                                   extra_data=None, **kwargs):
        """撤销合约网格订单

        Args:
            symbol: 交易对
            order_id: 订单ID
            client_order_id: 客户端订单ID
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'futures_grid_cancel_order'
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'pair': request_symbol,
        }
        if order_id is not None:
            params['orderId'] = order_id
        if client_order_id is not None:
            params['clientOrderId'] = client_order_id
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def futures_grid_cancel_order(self, symbol, order_id=None, client_order_id=None,
                                  extra_data=None, **kwargs):
        """撤销合约网格订单

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._futures_grid_cancel_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_grid_orders(self, symbol=None, order_id=None, status=None,
                                  extra_data=None, **kwargs):
        """查询合约网格订单

        Args:
            symbol: 交易对
            order_id: 订单ID
            status: 订单状态
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_futures_grid_orders'
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params['pair'] = request_symbol
        if order_id is not None:
            params['orderId'] = order_id
        if status is not None:
            params['status'] = status
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_futures_grid_orders(self, symbol=None, order_id=None, status=None,
                                 extra_data=None, **kwargs):
        """查询合约网格订单

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_futures_grid_orders(
            symbol=symbol, order_id=order_id, status=status,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_grid_position(self, symbol=None, extra_data=None, **kwargs):
        """查询合约网格持仓

        Args:
            symbol: 交易对
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_futures_grid_position'
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params['pair'] = request_symbol
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_futures_grid_position(self, symbol=None, extra_data=None, **kwargs):
        """查询合约网格持仓

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_futures_grid_position(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_grid_income(self, symbol=None, start_time=None, end_time=None,
                                  limit=None, extra_data=None, **kwargs):
        """查询合约网格收益

        Args:
            symbol: 交易对
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_futures_grid_income'
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params['pair'] = request_symbol
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time
        if limit is not None:
            params['limit'] = limit
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_futures_grid_income(self, symbol=None, start_time=None, end_time=None,
                                limit=None, extra_data=None, **kwargs):
        """查询合约网格收益

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_futures_grid_income(
            symbol=symbol, start_time=start_time, end_time=end_time, limit=limit,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
