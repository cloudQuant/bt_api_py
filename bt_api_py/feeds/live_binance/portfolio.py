"""Binance Portfolio Margin API - 组合保证金接口请求类.

实现 Binance 组合保证金相关的所有 REST API 请求，包括：
- 组合保证金账户查询
- 抵押率查询
- 组合保证金资产划转
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataPortfolio
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataPortfolio(BinanceRequestData):
    """Binance Portfolio Margin API 请求类.

    处理所有组合保证金相关的请求。
    """

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", BinanceExchangeDataPortfolio())
        kwargs.setdefault("exchange_name", "binance_portfolio")
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "PORTFOLIO")
        self.logger_name = kwargs.get("logger_name", "binance_portfolio_feed.log")
        self._params = kwargs["exchange_data"]
        self.request_logger = get_logger("binance_portfolio_feed")
        self.async_logger = get_logger("binance_portfolio_feed")

    # ==================== 组合保证金接口 ====================

    def _get_portfolio_account(self, extra_data=None, **kwargs):
        """查询组合保证金账户信息.

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_portfolio_account"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
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

    def get_portfolio_account(self, extra_data=None, **kwargs) -> Any:
        """查询组合保证金账户信息.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_portfolio_account(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_portfolio_collateral_rate(self, asset_type=None, extra_data=None, **kwargs):
        """查询抵押率.

        Args:
            asset_type: 资产类型 (如: USDT)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_portfolio_collateral_rate"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if asset_type is not None:
            params["assetType"] = asset_type
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": asset_type or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_portfolio_collateral_rate(self, asset_type=None, extra_data=None, **kwargs) -> Any:
        """查询抵押率.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_portfolio_collateral_rate(
            asset_type=asset_type, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _portfolio_transfer(self, asset, amount, transfer_type, extra_data=None, **kwargs):
        """组合保证金资产划转.

        Args:
            asset: 资产名称
            amount: 划转数量
            transfer_type: 划转类型 (SPOT_TO_PORTFOLIO, PORTFOLIO_TO_SPOT)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "portfolio_transfer"
        path = self._params.get_rest_path(request_type)
        params = {
            "asset": asset,
            "amount": amount,
            "type": transfer_type,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": asset,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def portfolio_transfer(self, asset, amount, transfer_type, extra_data=None, **kwargs):
        """组合保证金资产划转.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._portfolio_transfer(
            asset=asset, amount=amount, transfer_type=transfer_type, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
