"""Binance Wallet API - 钱包接口请求类.

实现 Binance 钱包相关的所有 REST API 请求，包括：
- 资产查询 (余额、详情、账本、分红)
- 资产划转 (通用划转、期货划转、合约划转等)
- 充值相关 (充值地址、充值历史)
- 提现相关 (提现申请、提现历史、提现地址)
- 小额资产转换 (Dust)
"""

from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataWallet
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataWallet(BinanceRequestData):
    """Binance Wallet API 请求类.

    处理所有钱包相关的请求，包括资产查询、划转、充值、提现等。
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", BinanceExchangeDataWallet())
        kwargs.setdefault("exchange_name", "binance_wallet")
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "WALLET")
        self.logger_name = kwargs.get("logger_name", "binance_wallet_feed.log")
        self._params = kwargs["exchange_data"]
        self.request_logger = get_logger("binance_wallet_feed")
        self.async_logger = get_logger("binance_wallet_feed")

    # ==================== 资产查询接口 ====================

    def _get_wallet_balance(self, extra_data=None, **kwargs):
        """查询钱包余额.

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_wallet_balance"
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

    def get_wallet_balance(self, extra_data=None, **kwargs):
        """查询钱包余额.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_wallet_balance(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_asset_detail(self, extra_data=None, **kwargs):
        """查询资产详情.

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_asset_detail"
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

    def get_asset_detail(self, extra_data=None, **kwargs):
        """查询资产详情.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_asset_detail(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_asset_ledger(
        self, asset=None, startTime=None, endTime=None, limit=None, extra_data=None, **kwargs
    ):
        """查询资产账本.

        Args:
            asset: 资产名称
            startTime: 开始时间戳
            endTime: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_asset_ledger"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if asset is not None:
            params["asset"] = asset
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
                "symbol_name": asset or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_asset_ledger(
        self, asset=None, startTime=None, endTime=None, limit=None, extra_data=None, **kwargs
    ):
        """查询资产账本.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_asset_ledger(
            asset=asset,
            startTime=startTime,
            endTime=endTime,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_asset_dividend(
        self, asset=None, startTime=None, endTime=None, limit=None, extra_data=None, **kwargs
    ):
        """查询资产分红记录.

        Args:
            asset: 资产名称
            startTime: 开始时间戳
            endTime: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_asset_dividend"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if asset is not None:
            params["asset"] = asset
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
                "symbol_name": asset or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_asset_dividend(
        self, asset=None, startTime=None, endTime=None, limit=None, extra_data=None, **kwargs
    ):
        """查询资产分红记录.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_asset_dividend(
            asset=asset,
            startTime=startTime,
            endTime=endTime,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 资产划转接口 ====================

    def _asset_transfer(
        self,
        transfer_type,
        asset,
        amount,
        from_symbol=None,
        to_symbol=None,
        extra_data=None,
        **kwargs,
    ):
        """通用资产划转.

        Args:
            transfer_type: 划转类型 (SPOT, UM, CM, MARGIN, ISOLATED_MARGIN, etc.)
            asset: 资产名称
            amount: 划转数量
            from_symbol: 源交易对 (仅用于 ISOLATED_MARGIN)
            to_symbol: 目标交易对 (仅用于 ISOLATED_MARGIN)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "asset_transfer"
        path = self._params.get_rest_path(request_type)
        params = {
            "type": transfer_type,
            "asset": asset,
            "amount": amount,
        }
        if from_symbol is not None:
            params["fromSymbol"] = from_symbol
        if to_symbol is not None:
            params["toSymbol"] = to_symbol
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

    def asset_transfer(
        self,
        transfer_type,
        asset,
        amount,
        from_symbol=None,
        to_symbol=None,
        extra_data=None,
        **kwargs,
    ):
        """通用资产划转.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._asset_transfer(
            transfer_type=transfer_type,
            asset=asset,
            amount=amount,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_asset_transfer(
        self,
        transfer_type=None,
        startTime=None,
        endTime=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """查询资产划转历史.

        Args:
            transfer_type: 划转类型
            startTime: 开始时间戳
            endTime: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_asset_transfer"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if transfer_type is not None:
            params["type"] = transfer_type
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

    def get_asset_transfer(
        self,
        transfer_type=None,
        startTime=None,
        endTime=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """查询资产划转历史.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_asset_transfer(
            transfer_type=transfer_type,
            startTime=startTime,
            endTime=endTime,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _transfer_to_futures_main(self, asset, amount, extra_data=None, **kwargs):
        """划转到主账户期货账户.

        Args:
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "transfer_to_futures_main"
        path = self._params.get_rest_path(request_type)
        params = {
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

    def transfer_to_futures_main(self, asset, amount, extra_data=None, **kwargs):
        """划转到主账户期货账户.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._transfer_to_futures_main(
            asset=asset, amount=amount, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _transfer_to_futures_sub(self, email, asset, amount, extra_data=None, **kwargs):
        """划转到子账户期货账户.

        Args:
            email: 子账户邮箱
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "transfer_to_futures_sub"
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

    def transfer_to_futures_sub(self, email, asset, amount, extra_data=None, **kwargs):
        """划转到子账户期货账户.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._transfer_to_futures_sub(
            email=email, asset=asset, amount=amount, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _transfer_to_um(self, asset, amount, extra_data=None, **kwargs):
        """划转到U本位合约.

        Args:
            asset: 资产名称
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "transfer_to_um"
        path = self._params.get_rest_path(request_type)
        params = {
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

    def transfer_to_um(self, asset, amount, extra_data=None, **kwargs):
        """划转到U本位合约.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._transfer_to_um(
            asset=asset, amount=amount, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _transfer_to_isolated_margin(self, asset, symbol, amount, extra_data=None, **kwargs):
        """划转到逐仓杠杆.

        Args:
            asset: 资产名称
            symbol: 交易对
            amount: 划转数量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "transfer_to_isolated_margin"
        path = self._params.get_rest_path(request_type)
        params = {
            "asset": asset,
            "symbol": symbol,
            "amount": amount,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def transfer_to_isolated_margin(self, asset, symbol, amount, extra_data=None, **kwargs):
        """划转到逐仓杠杆.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._transfer_to_isolated_margin(
            asset=asset, symbol=symbol, amount=amount, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 充值相关接口 ====================

    def _get_deposit_address(self, coin, network=None, extra_data=None, **kwargs):
        """查询充值地址.

        Args:
            coin: 币种
            network: 网络
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_deposit_address"
        path = self._params.get_rest_path(request_type)
        params = {
            "coin": coin,
        }
        if network is not None:
            params["network"] = network
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": coin,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_deposit_address(self, coin, network=None, extra_data=None, **kwargs):
        """查询充值地址.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_deposit_address(
            coin=coin, network=network, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_deposit_history(
        self,
        coin=None,
        startTime=None,
        endTime=None,
        limit=None,
        offset=None,
        extra_data=None,
        **kwargs,
    ):
        """查询充值历史.

        Args:
            coin: 币种
            startTime: 开始时间戳
            endTime: 结束时间戳
            limit: 数量限制
            offset: 偏移量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_deposit_history"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin
        if startTime is not None:
            params["startTime"] = startTime
        if endTime is not None:
            params["endTime"] = endTime
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": coin or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_deposit_history(
        self,
        coin=None,
        startTime=None,
        endTime=None,
        limit=None,
        offset=None,
        extra_data=None,
        **kwargs,
    ):
        """查询充值历史.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_deposit_history(
            coin=coin,
            startTime=startTime,
            endTime=endTime,
            limit=limit,
            offset=offset,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 提现相关接口 ====================

    def _withdraw(
        self,
        coin,
        address,
        amount,
        network=None,
        addressTag=None,
        name=None,
        extra_data=None,
        **kwargs,
    ):
        """提现申请.

        Args:
            coin: 币种
            address: 提现地址
            amount: 提现数量
            network: 网络
            addressTag: 地址标签
            name: 提现备注名
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "withdraw"
        path = self._params.get_rest_path(request_type)
        params = {
            "coin": coin,
            "address": address,
            "amount": amount,
        }
        if network is not None:
            params["network"] = network
        if addressTag is not None:
            params["addressTag"] = addressTag
        if name is not None:
            params["name"] = name
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": coin,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def withdraw(
        self,
        coin,
        address,
        amount,
        network=None,
        addressTag=None,
        name=None,
        extra_data=None,
        **kwargs,
    ):
        """提现申请.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._withdraw(
            coin=coin,
            address=address,
            amount=amount,
            network=network,
            addressTag=addressTag,
            name=name,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_withdraw_history(
        self,
        coin=None,
        startTime=None,
        endTime=None,
        limit=None,
        offset=None,
        extra_data=None,
        **kwargs,
    ):
        """查询提现历史.

        Args:
            coin: 币种
            startTime: 开始时间戳
            endTime: 结束时间戳
            limit: 数量限制
            offset: 偏移量
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_withdraw_history"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin
        if startTime is not None:
            params["startTime"] = startTime
        if endTime is not None:
            params["endTime"] = endTime
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": coin or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_withdraw_history(
        self,
        coin=None,
        startTime=None,
        endTime=None,
        limit=None,
        offset=None,
        extra_data=None,
        **kwargs,
    ):
        """查询提现历史.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_withdraw_history(
            coin=coin,
            startTime=startTime,
            endTime=endTime,
            limit=limit,
            offset=offset,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_withdraw_address(self, coin=None, extra_data=None, **kwargs):
        """查询提现地址.

        Args:
            coin: 币种
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_withdraw_address"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": coin or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_withdraw_address(self, coin=None, extra_data=None, **kwargs):
        """查询提现地址.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_withdraw_address(
            coin=coin, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 小额资产转换接口 ====================

    def _get_dust(self, extra_data=None, **kwargs):
        """查询小额资产.

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "get_dust"
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

    def get_dust(self, extra_data=None, **kwargs):
        """查询小额资产.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._get_dust(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _dust_transfer(self, assets, extra_data=None, **kwargs):
        """小额资产转换BTC.

        Args:
            assets: 资产列表 (用逗号分隔的数组)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)

        """
        request_type = "dust_transfer"
        path = self._params.get_rest_path(request_type)
        params = {
            "asset": assets if isinstance(assets, str) else ",".join(assets),
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "DUST",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def dust_transfer(self, assets, extra_data=None, **kwargs):
        """小额资产转换BTC.

        Returns:
            RequestData: 请求结果

        """
        path, params, extra_data = self._dust_transfer(
            assets=assets, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
