# -*- coding: utf-8 -*-
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData
from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataMargin
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.containers.orders.binance_order import BinanceSpotWssOrderData
from bt_api_py.containers.accounts.binance_account import BinanceSpotWssAccountData
from bt_api_py.containers.trades.binance_trade import BinanceSpotWssTradeData


class BinanceRequestDataMargin(BinanceRequestData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceRequestDataMargin, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "MARGIN")
        self.logger_name = kwargs.get("logger_name", "binance_margin_feed.log")
        self._params = BinanceExchangeDataMargin()
        self.request_logger = SpdLogManager("./logs/" + self.logger_name, "request",
                                            0, 0, False).create_logger()
        self.async_logger = SpdLogManager("./logs/" + self.logger_name, "async_request",
                                          0, 0, False).create_logger()

    # ==================== 保证金数据接口 ====================

    def _get_cross_margin_data(self, extra_data=None, **kwargs):
        """查询全仓保证金数据

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_cross_margin_data'
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_cross_margin_data(self, extra_data=None, **kwargs):
        """查询全仓保证金数据

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_cross_margin_data(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_isolated_margin_data(self, symbols=None, extra_data=None, **kwargs):
        """查询逐仓保证金数据

        Args:
            symbols: 交易对列表 (逗号分隔)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_isolated_margin_data'
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbols is not None:
            params['symbols'] = symbols
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbols or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_isolated_margin_data(self, symbols=None, extra_data=None, **kwargs):
        """查询逐仓保证金数据

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_isolated_margin_data(
            symbols=symbols, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_capital_flow(self, asset=None, start_time=None, end_time=None, limit=None,
                          extra_data=None, **kwargs):
        """查询资金流水

        Args:
            asset: 资产名称
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_capital_flow'
        path = self._params.get_rest_path(request_type)
        params = {}
        if asset is not None:
            params['asset'] = asset
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time
        if limit is not None:
            params['limit'] = limit
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": asset or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_capital_flow(self, asset=None, start_time=None, end_time=None, limit=None,
                         extra_data=None, **kwargs):
        """查询资金流水

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_capital_flow(
            asset=asset, start_time=start_time, end_time=end_time, limit=limit,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== BNB抵扣接口 ====================

    def _get_bnb_burn(self, extra_data=None, **kwargs):
        """获取BNB抵扣状态

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_bnb_burn'
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "BNB",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_bnb_burn(self, extra_data=None, **kwargs):
        """获取BNB抵扣状态

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_bnb_burn(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _toggle_bnb_burn(self, extra_data=None, **kwargs):
        """开关BNB抵扣

        Args:
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'toggle_bnb_burn'
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "BNB",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def toggle_bnb_burn(self, extra_data=None, **kwargs):
        """开关BNB抵扣

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._toggle_bnb_burn(extra_data=extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    # ==================== 杠杆清算接口 ====================

    def _manual_liquidation(self, symbol=None, extra_data=None, **kwargs):
        """手动清算

        Args:
            symbol: 交易对
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'manual_liquidation'
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params['symbol'] = request_symbol
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def manual_liquidation(self, symbol=None, extra_data=None, **kwargs):
        """手动清算

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._manual_liquidation(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _exchange_small_liability(self, asset_names=None, extra_data=None, **kwargs):
        """小额负债兑换

        Args:
            asset_names: 资产名称列表 (逗号分隔的字符串)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'exchange_small_liability'
        path = self._params.get_rest_path(request_type)
        params = {}
        if asset_names is not None:
            params['assetNames'] = asset_names
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": asset_names or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def exchange_small_liability(self, asset_names=None, extra_data=None, **kwargs):
        """小额负债兑换

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._exchange_small_liability(
            asset_names=asset_names, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_small_liability_history(self, asset=None, start_time=None, end_time=None, limit=None,
                                    extra_data=None, **kwargs):
        """查询小额负债兑换历史

        Args:
            asset: 资产名称
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 数量限制
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'get_small_liability_history'
        path = self._params.get_rest_path(request_type)
        params = {}
        if asset is not None:
            params['asset'] = asset
        if start_time is not None:
            params['startTime'] = start_time
        if end_time is not None:
            params['endTime'] = end_time
        if limit is not None:
            params['limit'] = limit
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": asset or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def get_small_liability_history(self, asset=None, start_time=None, end_time=None, limit=None,
                                    extra_data=None, **kwargs):
        """查询小额负债兑换历史

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._get_small_liability_history(
            asset=asset, start_time=start_time, end_time=end_time, limit=limit,
            extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _set_max_leverage(self, max_leverage, extra_data=None, **kwargs):
        """设置最大杠杆

        Args:
            max_leverage: 最大杠杆倍数 (1-125)
            extra_data: 额外数据
            **kwargs: 其他参数

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = 'set_max_leverage'
        path = self._params.get_rest_path(request_type)
        params = {
            'maxLeverage': max_leverage,
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })
        return path, params, extra_data

    def set_max_leverage(self, max_leverage, extra_data=None, **kwargs):
        """设置最大杠杆

        Args:
            max_leverage: 最大杠杆倍数 (1-125)

        Returns:
            RequestData: 请求结果
        """
        path, params, extra_data = self._set_max_leverage(
            max_leverage=max_leverage, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data


class BinanceMarketWssDataMargin(BinanceMarketWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceMarketWssDataMargin, self).__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "MARGIN")
        self._params = BinanceExchangeDataMargin()


class BinanceAccountWssDataMargin(BinanceAccountWssData):
    def __init__(self, data_queue, **kwargs):
        super(BinanceAccountWssDataMargin, self).__init__(data_queue, **kwargs)
        self._params = BinanceExchangeDataMargin()

    def handle_data(self, content):
        """处理杠杆账户 WebSocket 数据

        杠杆账户使用与现货相同的事件结构:
        - executionReport: 订单执行报告
        - outboundAccountPosition: 账户余额变动
        - balanceUpdate: 余额更新 (如分红)
        """
        event = content.get("e", None)
        if event is not None:
            # 订单执行报告 (非成交事件)
            if "executionReport" == event and content.get("x", None) != "TRADE":
                self.push_order(content)
            # 账户余额变动
            if "outboundAccountPosition" == event:
                self.push_account(content)
            # 成交事件
            if "executionReport" == event and content.get("x", None) == "TRADE":
                self.push_trade(content)
            # 余额更新 (分红等)
            if "balanceUpdate" == event:
                self.push_balance(content)

    def push_account(self, content):
        """推送账户数据"""
        symbol = "ALL"
        account_data = BinanceSpotWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(account_data)

    def push_order(self, content):
        """推送订单数据"""
        symbol = content['s']
        order_data = BinanceSpotWssOrderData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_data)

    def push_trade(self, content):
        """推送成交数据"""
        symbol = content['s']
        trade_data = BinanceSpotWssTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)

    def push_balance(self, content):
        """推送余额更新数据 (分红等)"""
        # balanceUpdate 事件包含: {e: "balanceUpdate", E: 1573200697114, s: "BTC", u: "15896533547050558808", B: "500.00000000"}
        # 可以使用 Spot 的账户数据容器，或者创建专门的余额更新容器
        symbol = content.get('s', 'ALL')
        balance_data = BinanceSpotWssAccountData(content, symbol, self.asset_type, True)
        self.data_queue.put(balance_data)
