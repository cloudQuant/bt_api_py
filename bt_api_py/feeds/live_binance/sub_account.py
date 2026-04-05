"""Binance Sub-account API - 子账户管理接口请求类.

实现 Binance 子账户管理相关的所有 REST API 请求，包括：
- 子账户管理 (列表、状态、现货汇总)
- 子账户资金划转 (子账号转主账号、主账号转子账号、子账号互转)
- 子账户资产查询 (资产、保证金、期货账户)
- 子账户 API Key 管理 (创建、查询、删除、IP限制)
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSubAccount
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataSubAccount(BinanceRequestData):
    """Binance Sub-account API 请求类.

    处理所有子账户管理相关的请求。
    """

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", BinanceExchangeDataSubAccount())
        kwargs.setdefault("exchange_name", "binance_sub_account")
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SUB_ACCOUNT")
        self.logger_name = kwargs.get("logger_name", "binance_sub_account_feed.log")
        self._params = kwargs["exchange_data"]
        self.request_logger = get_logger("binance_sub_account_feed")
        self.async_logger = get_logger("binance_sub_account_feed")

    # ==================== 子账户管理接口 ====================

    def _get_sub_account_list(self, email=None, is_freeze=None, extra_data=None, **kwargs):
        """查询子账户列表.

        Args:
            email: 子账户邮箱 (模糊查询)
            is_freeze: 是否冻结
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_list"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if email is not None:
            params["email"] = email
        if is_freeze is not None:
            params["isFreeze"] = is_freeze
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

    def get_sub_account_list(self, email=None, is_freeze=None, extra_data=None, **kwargs):
        """查询子账户列表.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_list(
            email=email, is_freeze=is_freeze, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_account_status(self, extra_data=None, **kwargs):
        """查询子账户状态.

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_status"
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

    def get_sub_account_status(self, extra_data=None, **kwargs):
        """查询子账户状态.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_status(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_account_spot_summary(self, email=None, extra_data=None, **kwargs):
        """查询子账户现货汇总.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_spot_summary"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if email is not None:
            params["email"] = email
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

    def get_sub_account_spot_summary(self, email=None, extra_data=None, **kwargs):
        """查询子账户现货汇总.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_spot_summary(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 子账户资金划转接口 ====================

    def _sub_transfer_to_main(self, email, asset, amount, extra_data=None, **kwargs):
        """子账户转主账户.

        Args:
            email: 子账户邮箱
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "sub_transfer_to_main"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
            "asset": asset,
            "amount": amount,
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

    def sub_transfer_to_main(self, email, asset, amount, extra_data=None, **kwargs):
        """子账户转主账户.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._sub_transfer_to_main(
            email=email, asset=asset, amount=amount, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _main_transfer_to_sub(self, email, asset, amount, extra_data=None, **kwargs):
        """主账户转子账户.

        Args:
            email: 子账户邮箱
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "main_transfer_to_sub"
        path = self._params.get_rest_path(request_type)
        params = {
            "toEmail": email,
            "asset": asset,
            "amount": amount,
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

    def main_transfer_to_sub(self, email, asset, amount, extra_data=None, **kwargs):
        """主账户转子账户.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._main_transfer_to_sub(
            email=email, asset=asset, amount=amount, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _sub_transfer_to_sub(self, from_email, to_email, asset, amount, extra_data=None, **kwargs):
        """子账户互转.

        Args:
            from_email: 源子账户邮箱
            to_email: 目标子账户邮箱
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "sub_transfer_to_sub"
        path = self._params.get_rest_path(request_type)
        params = {
            "fromEmail": from_email,
            "toEmail": to_email,
            "asset": asset,
            "amount": amount,
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

    def sub_transfer_to_sub(self, from_email, to_email, asset, amount, extra_data=None, **kwargs):
        """子账户互转.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._sub_transfer_to_sub(
            from_email=from_email,
            to_email=to_email,
            asset=asset,
            amount=amount,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_transfer_history(
        self, startTime=None, endTime=None, limit=None, extra_data=None, **kwargs
    ):
        """查询子账户划转历史.

        Args:
            startTime: 开始时间戳
            endTime: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_transfer_history"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if startTime is not None:
            params["startTime"] = startTime
        if endTime is not None:
            params["endTime"] = endTime
        if limit is not None:
            params["limit"] = limit
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

    def get_sub_transfer_history(
        self, startTime=None, endTime=None, limit=None, extra_data=None, **kwargs
    ):
        """查询子账户划转历史.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_transfer_history(
            startTime=startTime, endTime=endTime, limit=limit, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_account_universal_transfer(
        self, from_type, to_type, asset, amount, extra_data=None, **kwargs
    ):
        """子账户通用划转.

        Args:
            from_type: 源账户类型
            to_type: 目标账户类型
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_universal_transfer"
        path = self._params.get_rest_path(request_type)
        params = {
            "fromType": from_type,
            "toType": to_type,
            "asset": asset,
            "amount": amount,
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

    def get_sub_account_universal_transfer(
        self, from_type, to_type, asset, amount, extra_data=None, **kwargs
    ):
        """子账户通用划转.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_universal_transfer(
            from_type=from_type,
            to_type=to_type,
            asset=asset,
            amount=amount,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 子账户资产查询接口 ====================

    def _get_sub_account_assets(self, email, extra_data=None, **kwargs):
        """查询子账户资产.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_assets"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
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

    def get_sub_account_assets(self, email, extra_data=None, **kwargs):
        """查询子账户资产.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_assets(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_account_margin_account(self, email, extra_data=None, **kwargs):
        """查询子账户杠杆账户.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_margin_account"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
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

    def get_sub_account_margin_account(self, email, extra_data=None, **kwargs):
        """查询子账户杠杆账户.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_margin_account(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_account_margin_summary(self, email, extra_data=None, **kwargs):
        """查询子账户杠杆账户汇总.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_margin_summary"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
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

    def get_sub_account_margin_summary(self, email, extra_data=None, **kwargs):
        """查询子账户杠杆账户汇总.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_margin_summary(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_account_futures_account(self, email, extra_data=None, **kwargs):
        """查询子账户期货账户.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_account_futures_account"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
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

    def get_sub_account_futures_account(self, email, extra_data=None, **kwargs):
        """查询子账户期货账户.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_account_futures_account(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 子账户 API Key 管理接口 ====================

    def _create_sub_api_key(self, email, extra_data=None, **kwargs):
        """创建子账户 API Key.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数 (canTrade, marginTrade, futuresType)

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "create_sub_api_key"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
        # 可选参数
        if "canTrade" in kwargs:
            params["canTrade"] = kwargs["canTrade"]
        if "marginTrade" in kwargs:
            params["marginTrade"] = kwargs["marginTrade"]
        if "futuresType" in kwargs:
            params["futuresType"] = kwargs["futuresType"]
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "API_KEY",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def create_sub_api_key(self, email, extra_data=None, **kwargs):
        """创建子账户 API Key.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._create_sub_api_key(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_api_key(self, email, extra_data=None, **kwargs):
        """查询子账户 API Key.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_api_key"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "API_KEY",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_sub_api_key(self, email, extra_data=None, **kwargs):
        """查询子账户 API Key.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_api_key(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _delete_sub_api_key(self, email, api_key, extra_data=None, **kwargs):
        """删除子账户 API Key.

        Args:
            email: 子账户邮箱
            api_key: API Key
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "delete_sub_api_key"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
            "publicKey": api_key,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "API_KEY",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def delete_sub_api_key(self, email, api_key, extra_data=None, **kwargs):
        """删除子账户 API Key.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._delete_sub_api_key(
            email=email, api_key=api_key, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_sub_api_ip_restriction(self, email, extra_data=None, **kwargs):
        """查询子账户 IP 限制.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_sub_api_ip_restriction"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "IP_RESTRICTION",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_sub_api_ip_restriction(self, email, extra_data=None, **kwargs):
        """查询子账户 IP 限制.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_sub_api_ip_restriction(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _delete_sub_ip_restriction(self, email, extra_data=None, **kwargs):
        """删除子账户 IP 限制.

        Args:
            email: 子账户邮箱
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "delete_sub_ip_restriction"
        path = self._params.get_rest_path(request_type)
        params = {
            "email": email,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "IP_RESTRICTION",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def delete_sub_ip_restriction(self, email, extra_data=None, **kwargs):
        """删除子账户 IP 限制.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._delete_sub_ip_restriction(
            email=email, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
