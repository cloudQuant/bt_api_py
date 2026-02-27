# -*- coding: utf-8 -*-
"""
OKX API - StatisticsMixin
Auto-generated from request_base.py
"""
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function


class StatisticsMixin:
    """Mixin providing OKX API methods."""
    # ==================== Trading Statistics APIs ====================

    def _get_taker_volume_contract(self, ccy=None, inst_type=None, begin=None, end=None,
                                   period=None, limit=None, extra_data=None, **kwargs):
        """
        Get contract active buy/sell volume (taker volume)
        :param ccy: Currency, e.g. "BTC"
        :param inst_type: Instrument type: `SWAP`, `FUTURES`, `OPTION`
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `5m`, `1H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_taker_volume_contract"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if inst_type:
            params['instType'] = inst_type
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_taker_volume_contract_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_taker_volume_contract_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_taker_volume_contract(self, ccy=None, inst_type=None, begin=None, end=None,
                                   period=None, limit=None, extra_data=None, **kwargs):
        """Get contract active buy/sell volume (taker volume)"""
        path, params, extra_data = self._get_taker_volume_contract(
            ccy, inst_type, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_taker_volume_contract(self, ccy=None, inst_type=None, begin=None, end=None,
                                        period=None, limit=None, extra_data=None, **kwargs):
        """Async get contract active buy/sell volume (taker volume)"""
        path, params, extra_data = self._get_taker_volume_contract(
            ccy, inst_type, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_margin_loan_ratio(self, ccy=None, begin=None, end=None, period=None,
                               limit=None, extra_data=None, **kwargs):
        """
        Get margin loan ratio (spot long/short ratio)
        :param ccy: Currency, e.g. "BTC"
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `5m`, `1H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_margin_loan_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_margin_loan_ratio_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_margin_loan_ratio_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_margin_loan_ratio(self, ccy=None, begin=None, end=None, period=None,
                              limit=None, extra_data=None, **kwargs):
        """Get margin loan ratio (spot long/short ratio)"""
        path, params, extra_data = self._get_margin_loan_ratio(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_margin_loan_ratio(self, ccy=None, begin=None, end=None, period=None,
                                    limit=None, extra_data=None, **kwargs):
        """Async get margin loan ratio (spot long/short ratio)"""
        path, params, extra_data = self._get_margin_loan_ratio(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_long_short_ratio(self, ccy=None, currency=None, begin=None, end=None,
                                      period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option long/short ratio
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_long_short_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_option_long_short_ratio_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_long_short_ratio_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_long_short_ratio(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """Get option long/short ratio"""
        path, params, extra_data = self._get_option_long_short_ratio(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_long_short_ratio(self, ccy=None, currency=None, begin=None, end=None,
                                          period=None, limit=None, extra_data=None, **kwargs):
        """Async get option long/short ratio"""
        path, params, extra_data = self._get_option_long_short_ratio(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_contracts_oi_volume(self, ccy=None, begin=None, end=None, period=None,
                                  limit=None, extra_data=None, **kwargs):
        """
        Get contract open interest and volume
        :param ccy: Currency, e.g. "BTC"
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `5m`, `1H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_contracts_oi_volume"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_contracts_oi_volume_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_contracts_oi_volume_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_contracts_oi_volume(self, ccy=None, begin=None, end=None, period=None,
                                 limit=None, extra_data=None, **kwargs):
        """Get contract open interest and volume"""
        path, params, extra_data = self._get_contracts_oi_volume(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_contracts_oi_volume(self, ccy=None, begin=None, end=None, period=None,
                                      limit=None, extra_data=None, **kwargs):
        """Async get contract open interest and volume"""
        path, params, extra_data = self._get_contracts_oi_volume(
            ccy, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_oi_volume(self, ccy=None, currency=None, begin=None, end=None,
                               period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option open interest and volume
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_oi_volume"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_option_oi_volume_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_oi_volume_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_oi_volume(self, ccy=None, currency=None, begin=None, end=None,
                              period=None, limit=None, extra_data=None, **kwargs):
        """Get option open interest and volume"""
        path, params, extra_data = self._get_option_oi_volume(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_oi_volume(self, ccy=None, currency=None, begin=None, end=None,
                                    period=None, limit=None, extra_data=None, **kwargs):
        """Async get option open interest and volume"""
        path, params, extra_data = self._get_option_oi_volume(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_oi_volume_expiry(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option open interest and volume by expiry
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_oi_volume_expiry"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_option_oi_volume_expiry_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_oi_volume_expiry_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_oi_volume_expiry(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """Get option open interest and volume by expiry"""
        path, params, extra_data = self._get_option_oi_volume_expiry(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_oi_volume_expiry(self, ccy=None, currency=None, begin=None, end=None,
                                          period=None, limit=None, extra_data=None, **kwargs):
        """Async get option open interest and volume by expiry"""
        path, params, extra_data = self._get_option_oi_volume_expiry(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_oi_volume_strike(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option open interest and volume by strike price
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_oi_volume_strike"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_option_oi_volume_strike_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_oi_volume_strike_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_oi_volume_strike(self, ccy=None, currency=None, begin=None, end=None,
                                     period=None, limit=None, extra_data=None, **kwargs):
        """Get option open interest and volume by strike price"""
        path, params, extra_data = self._get_option_oi_volume_strike(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_oi_volume_strike(self, ccy=None, currency=None, begin=None, end=None,
                                          period=None, limit=None, extra_data=None, **kwargs):
        """Async get option open interest and volume by strike price"""
        path, params, extra_data = self._get_option_oi_volume_strike(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_option_taker_flow(self, ccy=None, currency=None, begin=None, end=None,
                               period=None, limit=None, extra_data=None, **kwargs):
        """
        Get option taker block volume (large trades)
        :param ccy: Underlying index, e.g. "BTC-USD"
        :param currency: Margin currency, only support USD
        :param begin: Begin timestamp (ms)
        :param end: End timestamp (ms)
        :param period: Time period: `8H`, `1D`
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_taker_flow"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if currency:
            params['currency'] = currency
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_option_taker_flow_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_option_taker_flow_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_option_taker_flow(self, ccy=None, currency=None, begin=None, end=None,
                              period=None, limit=None, extra_data=None, **kwargs):
        """Get option taker block volume (large trades)"""
        path, params, extra_data = self._get_option_taker_flow(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_taker_flow(self, ccy=None, currency=None, begin=None, end=None,
                                    period=None, limit=None, extra_data=None, **kwargs):
        """Async get option taker block volume (large trades)"""
        path, params, extra_data = self._get_option_taker_flow(
            ccy, currency, begin, end, period, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Position Builder APIs ====================

    def _position_builder(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                          margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """
        Position builder - Calculate the maximum open size
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID, e.g. BTC-USDT-SWAP
        :param ccy: Currency, e.g. BTC
        :param max_sz: Maximum open size
        :param margin_mode: Margin mode: cross, isolated, cash
        :param pos_side: Position side: long, short, net
        :param auto_sz: Whether to automatically calculate the size: true, false
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "position_builder"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if max_sz is not None:
            params['maxSz'] = str(max_sz)
        if margin_mode:
            params['mgnMode'] = margin_mode
        if pos_side:
            params['posSide'] = pos_side
        if auto_sz is not None:
            params['autoSz'] = str(auto_sz).lower()
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._position_builder_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _position_builder_normalize_function(input_data, extra_data):
        """Normalize position builder response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def position_builder(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                         margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Position builder - Calculate the maximum open size"""
        path, params, extra_data = self._position_builder(inst_type, uly, inst_id, ccy, max_sz,
                                                          margin_mode, pos_side, auto_sz,
                                                          extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_position_builder(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                               margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Async position builder - Calculate the maximum open size"""
        path, params, extra_data = self._position_builder(inst_type, uly, inst_id, ccy, max_sz,
                                                          margin_mode, pos_side, auto_sz,
                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _position_builder_trend(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                                margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """
        Position builder trend - Get position builder trend data
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID, e.g. BTC-USDT-SWAP
        :param ccy: Currency, e.g. BTC
        :param max_sz: Maximum open size
        :param margin_mode: Margin mode: cross, isolated, cash
        :param pos_side: Position side: long, short, net
        :param auto_sz: Whether to automatically calculate the size: true, false
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "position_builder_trend"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if ccy:
            params['ccy'] = ccy
        if max_sz is not None:
            params['maxSz'] = str(max_sz)
        if margin_mode:
            params['mgnMode'] = margin_mode
        if pos_side:
            params['posSide'] = pos_side
        if auto_sz is not None:
            params['autoSz'] = str(auto_sz).lower()
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._position_builder_trend_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _position_builder_trend_normalize_function(input_data, extra_data):
        """Normalize position builder trend response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def position_builder_trend(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                               margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Position builder trend - Get position builder trend data"""
        path, params, extra_data = self._position_builder_trend(inst_type, uly, inst_id, ccy, max_sz,
                                                                margin_mode, pos_side, auto_sz,
                                                                extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_position_builder_trend(self, inst_type, uly=None, inst_id=None, ccy=None, max_sz=None,
                                     margin_mode=None, pos_side=None, auto_sz=None, extra_data=None, **kwargs):
        """Async position builder trend - Get position builder trend data"""
        path, params, extra_data = self._position_builder_trend(inst_type, uly, inst_id, ccy, max_sz,
                                                                margin_mode, pos_side, auto_sz,
                                                                extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Trading Statistics APIs ====================

    @staticmethod
    def _get_support_coin_normalize_function(input_data, extra_data):
        """Normalize get_support_coin response.
        API returns data with different coin types grouped by category.
        Response format: {"code": "0", "data": {"contract": [...], "option": [...], "spot": [...]}}"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return {}, status
        # Data is already a dict with keys: contract, option, spot
        data = input_data['data']
        # Return dict as-is with keys: contract, option, spot
        return data, status

    def _get_support_coin(self, extra_data=None, **kwargs):
        """Get support coin"""
        request_type = "get_support_coin"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": StatisticsMixin._get_support_coin_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_support_coin(self, extra_data=None, **kwargs):
        """Get support coin"""
        path, params, extra_data = self._get_support_coin(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_support_coin(self, extra_data=None, **kwargs):
        """Async get support coin"""
        path, params, extra_data = self._get_support_coin(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_contract_oi_history(self, ccy=None, uly=None, inst_id=None, after=None, before=None,
                                  limit=None, period=None, extra_data=None, **kwargs):
        """Get contract open interest history"""
        request_type = "get_contract_oi_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_contract_oi_history(self, ccy=None, uly=None, inst_id=None, after=None, before=None,
                                limit=None, period=None, extra_data=None, **kwargs):
        """Get contract open interest history"""
        path, params, extra_data = self._get_contract_oi_history(ccy, uly, inst_id, after,
                                                                   before, limit, period,
                                                                   extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_contract_oi_history(self, ccy=None, uly=None, inst_id=None, after=None,
                                       before=None, limit=None, period=None,
                                       extra_data=None, **kwargs):
        """Async get contract open interest history"""
        path, params, extra_data = self._get_contract_oi_history(ccy, uly, inst_id, after,
                                                                   before, limit, period,
                                                                   extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_taker_volume(self, ccy=None, uly=None, inst_id=None, begin=None, end=None,
                          period=None, extra_data=None, **kwargs):
        """Get taker volume"""
        request_type = "get_taker_volume"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_taker_volume(self, ccy=None, uly=None, inst_id=None, begin=None, end=None,
                         period=None, extra_data=None, **kwargs):
        """Get taker volume"""
        path, params, extra_data = self._get_taker_volume(ccy, uly, inst_id, begin, end,
                                                            period, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_taker_volume(self, ccy=None, uly=None, inst_id=None, begin=None, end=None,
                               period=None, extra_data=None, **kwargs):
        """Async get taker volume"""
        path, params, extra_data = self._get_taker_volume(ccy, uly, inst_id, begin, end,
                                                            period, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_long_short_ratio(self, ccy=None, begin=None, end=None, period=None,
                               extra_data=None, **kwargs):
        """Get long short ratio"""
        request_type = "get_long_short_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_long_short_ratio(self, ccy=None, begin=None, end=None, period=None,
                             extra_data=None, **kwargs):
        """Get long short ratio"""
        path, params, extra_data = self._get_long_short_ratio(ccy, begin, end, period,
                                                                extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_long_short_ratio(self, ccy=None, begin=None, end=None, period=None,
                                   extra_data=None, **kwargs):
        """Async get long short ratio"""
        path, params, extra_data = self._get_long_short_ratio(ccy, begin, end, period,
                                                                extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_long_short_ratio_top_trader(self, ccy=None, begin=None, end=None, period=None,
                                          extra_data=None, **kwargs):
        """Get long short ratio (top trader)"""
        request_type = "get_long_short_ratio_top_trader"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_long_short_ratio_top_trader(self, ccy=None, begin=None, end=None, period=None,
                                         extra_data=None, **kwargs):
        """Get long short ratio (top trader)"""
        path, params, extra_data = self._get_long_short_ratio_top_trader(ccy, begin, end,
                                                                          period, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_long_short_ratio_top_trader(self, ccy=None, begin=None, end=None,
                                               period=None, extra_data=None, **kwargs):
        """Async get long short ratio (top trader)"""
        path, params, extra_data = self._get_long_short_ratio_top_trader(ccy, begin, end,
                                                                          period, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_contract_long_short_ratio(self, ccy=None, uly=None, inst_id=None, begin=None,
                                        end=None, period=None, limit=None, extra_data=None, **kwargs):
        """Get contract long short ratio"""
        request_type = "get_contract_long_short_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_contract_long_short_ratio(self, ccy=None, uly=None, inst_id=None, begin=None,
                                       end=None, period=None, limit=None, extra_data=None, **kwargs):
        """Get contract long short ratio"""
        path, params, extra_data = self._get_contract_long_short_ratio(ccy, uly, inst_id, begin,
                                                                       end, period, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_contract_long_short_ratio(self, ccy=None, uly=None, inst_id=None, begin=None,
                                             end=None, period=None, limit=None, extra_data=None, **kwargs):
        """Async get contract long short ratio"""
        path, params, extra_data = self._get_contract_long_short_ratio(ccy, uly, inst_id, begin,
                                                                       end, period, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_put_call_ratio(self, ccy=None, uly=None, begin=None, end=None, period=None,
                            extra_data=None, **kwargs):
        """Get put call ratio"""
        request_type = "get_put_call_ratio"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if uly:
            params['uly'] = uly
        if begin:
            params['begin'] = begin
        if end:
            params['end'] = end
        if period:
            params['period'] = period
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly or ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_put_call_ratio(self, ccy=None, uly=None, begin=None, end=None, period=None,
                           extra_data=None, **kwargs):
        """Get put call ratio"""
        path, params, extra_data = self._get_put_call_ratio(ccy, uly, begin, end, period,
                                                              extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_put_call_ratio(self, ccy=None, uly=None, begin=None, end=None, period=None,
                                  extra_data=None, **kwargs):
        """Async get put call ratio"""
        path, params, extra_data = self._get_put_call_ratio(ccy, uly, begin, end, period,
                                                              extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

