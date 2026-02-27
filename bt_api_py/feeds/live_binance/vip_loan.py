# -*- coding: utf-8 -*-
"""
Binance VIP Loan API - VIP借贷接口请求类

实现 Binance VIP借贷相关的所有 REST API 请求，包括：
- VIP借贷进行中订单查询
- VIP借贷借入
- VIP借贷还款
- VIP借贷历史记录查询
- VIP还款历史记录查询
"""

from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataVipLoan
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BinanceRequestDataVipLoan(BinanceRequestData):
    """Binance VIP Loan API 请求类

    处理所有VIP借贷相关的请求。
    """

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault('exchange_data', BinanceExchangeDataVipLoan())
        kwargs.setdefault('exchange_name', 'binance_vip_loan')
        super(BinanceRequestDataVipLoan, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "VIP_LOAN")
        self.logger_name = kwargs.get("logger_name", "binance_vip_loan_feed.log")
        self._params = kwargs['exchange_data']
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

    # ==================== VIP Loan 接口 ====================

    def _get_vip_loan_ongoing_orders(self, loan_coin=None, collateral_coin=None,
                                     current=None, size=None, extra_data=None, **kwargs):
        """查询VIP借贷进行中订单

        Args:
            loan_coin: 借贷币种
            collateral_coin: 抵押币种
            current: 当前页
            size: 每页数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_vip_loan_ongoing_orders'
        path = self._params.get_rest_path(request_type)
        params = {}
        if loan_coin is not None:
            params['loanCoin'] = loan_coin
        if collateral_coin is not None:
            params['collateralCoin'] = collateral_coin
        if current is not None:
            params['current'] = current
        if size is not None:
            params['size'] = size
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": loan_coin or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_vip_loan_ongoing_orders(self, loan_coin=None, collateral_coin=None,
                                    current=None, size=None, extra_data=None, **kwargs):
        """查询VIP借贷进行中订单

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_vip_loan_ongoing_orders(
            loan_coin=loan_coin, collateral_coin=collateral_coin,
            current=current, size=size, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _vip_loan_borrow(self, loan_coin, collateral_coin, loan_amount, collateral_amount,
                         extra_data=None, **kwargs):
        """VIP借贷借入

        Args:
            loan_coin: 借贷币种
            collateral_coin: 抵押币种
            loan_amount: 借贷数量
            collateral_amount: 抵押数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'vip_loan_borrow'
        path = self._params.get_rest_path(request_type)
        params = {
            'loanCoin': loan_coin,
            'collateralCoin': collateral_coin,
            'loanAmount': loan_amount,
            'collateralAmount': collateral_amount,
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": loan_coin,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def vip_loan_borrow(self, loan_coin, collateral_coin, loan_amount, collateral_amount,
                        extra_data=None, **kwargs):
        """VIP借贷借入

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._vip_loan_borrow(
            loan_coin=loan_coin, collateral_coin=collateral_coin,
            loan_amount=loan_amount, collateral_amount=collateral_amount,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _vip_loan_repay(self, loan_coin, collateral_coin, repay_amount=None,
                        collateral_amount=None, extra_data=None, **kwargs):
        """VIP借贷还款

        Args:
            loan_coin: 借贷币种
            collateral_coin: 抵押币种
            repay_amount: 还款数量
            collateral_amount: 抵押数量 (用于部分还款)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'vip_loan_repay'
        path = self._params.get_rest_path(request_type)
        params = {
            'loanCoin': loan_coin,
            'collateralCoin': collateral_coin,
        }
        if repay_amount is not None:
            params['repayAmount'] = repay_amount
        if collateral_amount is not None:
            params['collateralAmount'] = collateral_amount
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": loan_coin,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def vip_loan_repay(self, loan_coin, collateral_coin, repay_amount=None,
                       collateral_amount=None, extra_data=None, **kwargs):
        """VIP借贷还款

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._vip_loan_repay(
            loan_coin=loan_coin, collateral_coin=collateral_coin,
            repay_amount=repay_amount, collateral_amount=collateral_amount,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_vip_loan_history(self, loan_coin=None, collateral_coin=None, start_time=None,
                              end_time=None, current=None, size=None, extra_data=None, **kwargs):
        """查询VIP借贷历史记录

        Args:
            loan_coin: 借贷币种
            collateral_coin: 抵押币种
            start_time: 开始时间戳
            end_time: 结束时间戳
            current: 当前页
            size: 每页数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_vip_loan_history'
        path = self._params.get_rest_path(request_type)
        params = {}
        if loan_coin is not None:
            params['loanCoin'] = loan_coin
        if collateral_coin is not None:
            params['collateralCoin'] = collateral_coin
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time
        if current is not None:
            params['current'] = current
        if size is not None:
            params['size'] = size
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": loan_coin or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_vip_loan_history(self, loan_coin=None, collateral_coin=None, start_time=None,
                             end_time=None, current=None, size=None, extra_data=None, **kwargs):
        """查询VIP借贷历史记录

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_vip_loan_history(
            loan_coin=loan_coin, collateral_coin=collateral_coin,
            start_time=start_time, end_time=end_time,
            current=current, size=size, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_vip_repayment_history(self, loan_coin=None, collateral_coin=None, start_time=None,
                                   end_time=None, current=None, size=None, extra_data=None, **kwargs):
        """查询VIP还款历史记录

        Args:
            loan_coin: 借贷币种
            collateral_coin: 抵押币种
            start_time: 开始时间戳
            end_time: 结束时间戳
            current: 当前页
            size: 每页数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_vip_repayment_history'
        path = self._params.get_rest_path(request_type)
        params = {}
        if loan_coin is not None:
            params['loanCoin'] = loan_coin
        if collateral_coin is not None:
            params['collateralCoin'] = collateral_coin
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time
        if current is not None:
            params['current'] = current
        if size is not None:
            params['size'] = size
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": loan_coin or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_vip_repayment_history(self, loan_coin=None, collateral_coin=None, start_time=None,
                                  end_time=None, current=None, size=None, extra_data=None, **kwargs):
        """查询VIP还款历史记录

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_vip_repayment_history(
            loan_coin=loan_coin, collateral_coin=collateral_coin,
            start_time=start_time, end_time=end_time,
            current=current, size=size, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
