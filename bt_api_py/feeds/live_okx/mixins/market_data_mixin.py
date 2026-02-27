# -*- coding: utf-8 -*-
"""
OKX API - MarketDataMixin
Auto-generated from request_base.py
"""
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData
from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData
from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData
from bt_api_py.containers.symbols.okx_symbol import OkxSymbolData
from bt_api_py.containers.tickers.okx_ticker import OkxTickerData
from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function


class MarketDataMixin:
    """Mixin providing OKX API methods."""
    # ==================== Market Data APIs ====================

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        request_type = "get_tick"
        path = self._params.get_rest_path(request_type)
        params = {
            'instId': self._params.get_symbol(symbol),
        }
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_tick_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxTickerData(data,
                                       extra_data['symbol_name'],
                                       extra_data['asset_type'],
                                       True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        request_type = "get_depth"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            "sz": size
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_depth_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxOrderBookData(data,
                                          extra_data['symbol_name'],
                                          extra_data['asset_type'],
                                          True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_depth(self, symbol, size=20, extra_data=None, **kwargs):
        path, params, extra_data = self._get_depth(symbol, size, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_kline(self, symbol, period, count=100, start_time=0, end_time=0, extra_data=None, **kwargs):
        request_type = "get_kline"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
            'bar': self._params.get_period(period),
        }
        if count and count!=100:
            params['limit'] = count
        if end_time:
            params.update({"after": end_time})
        if start_time:
            params.update({"before": start_time})
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_kline_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = sorted(input_data['data'],key=lambda x: x[0])
        if len(data) > 0:
            data_list = [OkxBarData(i,
                                    extra_data['symbol_name'],
                                    extra_data['asset_type'],
                                    True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_kline(self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, start_time, end_time, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # noinspection PyMethodMayBeStatic
    def async_get_kline(self, symbol, period, count=100, before=0, after=0, extra_data=None, **kwargs):
        path, params, extra_data = self._get_kline(symbol, period, count, before, after, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Public Data APIs ====================

    def _get_funding_rate(self, symbol, extra_data=None, **kwargs):
        request_type = "get_funding_rate"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_funding_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_funding_rate_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxFundingRateData(data,
                                            extra_data['symbol_name'],
                                            extra_data['asset_type'],
                                            True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_funding_rate(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_funding_rate_history(self, symbol, before="", after="", limit="100", extra_data=None, **kwargs):
        """Get funding rate history"""
        request_type = "get_funding_rate_history"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
        }
        if before:
            params['before'] = before
        if after:
            params['after'] = after
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_funding_rate_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_funding_rate_history_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            data_list = [OkxFundingRateData(i,
                                            extra_data['symbol_name'],
                                            extra_data['asset_type'],
                                            True)
                         for i in data]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_funding_rate_history(self, symbol, before="", after="", limit="100", extra_data=None, **kwargs):
        path, params, extra_data = self._get_funding_rate_history(symbol, before, after, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_instruments(self, asset_type=None, underlying=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        request_type = "get_instruments"
        params = {}
        if asset_type:
            params['instType']=asset_type
        if underlying:
            params['uly'] = underlying
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_instruments_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_instruments_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if isinstance(data, list):
            target_data = [OkxSymbolData(i,True) for i in data]
        elif isinstance(data, dict):
            target_data = [OkxSymbolData(data,True)]
        else:
            target_data = []
        return target_data, status

    def get_instruments(self, asset_type=None, underlying=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_instruments(asset_type, underlying, inst_family, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_mark_price(self, symbol, extra_data=None, **kwargs):
        request_type = "get_mark_price"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            'instId': request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": "SPOT",
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_mark_price_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_mark_price_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data or not input_data['data']:
            return [], status
        data = input_data['data'][0]
        if len(data) > 0:
            data_list = [OkxMarkPriceData(data,
                                          extra_data['symbol_name'],
                                          extra_data['asset_type'],
                                          True)]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_mark_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_mark_price(self, symbol, extra_data=None, **kwargs):
        path, params, extra_data = self._get_mark_price(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        """Get open interest data"""
        request_type = "get_open_interest"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_family:
            params['instFamily'] = inst_family
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_interest(inst_type, uly, inst_family, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_open_interest(self, inst_type="SWAP", uly=None, inst_family=None, inst_id=None,
                                extra_data=None, **kwargs):
        """Async get open interest data"""
        path, params, extra_data = self._get_open_interest(inst_type, uly, inst_family, inst_id,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_premium_history(self, inst_type, uly=None, inst_id=None, after=None,
                            before=None, limit=None, extra_data=None, **kwargs):
        """
        Get premium history
        :param inst_type: Instrument type: `FUTURES`, `SWAP` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param after: Pagination (older data), request before this timestamp
        :param before: Pagination (newer data), request after this timestamp
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_premium_history"
        params = {'instType': inst_type}
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
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_premium_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_premium_history_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_premium_history(self, inst_type, uly=None, inst_id=None, after=None,
                           before=None, limit=None, extra_data=None, **kwargs):
        """Get premium history"""
        path, params, extra_data = self._get_premium_history(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_premium_history(self, inst_type, uly=None, inst_id=None, after=None,
                                 before=None, limit=None, extra_data=None, **kwargs):
        """Async get premium history"""
        path, params, extra_data = self._get_premium_history(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_economic_calendar(self, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """
        Get economic calendar
        :param after: Pagination (older data), request before this timestamp
        :param before: Pagination (newer data), request after this timestamp
        :param limit: Number of results, default 20, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_economic_calendar"
        params = {}
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_economic_calendar_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_economic_calendar_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_economic_calendar(self, after=None, before=None, limit=None,
                             extra_data=None, **kwargs):
        """Get economic calendar"""
        path, params, extra_data = self._get_economic_calendar(
            after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_economic_calendar(self, after=None, before=None, limit=None,
                                   extra_data=None, **kwargs):
        """Async get economic calendar"""
        path, params, extra_data = self._get_economic_calendar(
            after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Market Data APIs (continued) ====================

    def _get_exchange_rate(self, extra_data=None, **kwargs):
        """
        Get exchange rate
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_exchange_rate"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_exchange_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_exchange_rate_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_exchange_rate(self, extra_data=None, **kwargs):
        """Get exchange rate"""
        path, params, extra_data = self._get_exchange_rate(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_exchange_rate(self, extra_data=None, **kwargs):
        """Async get exchange rate"""
        path, params, extra_data = self._get_exchange_rate(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_index_components(self, index, extra_data=None, **kwargs):
        """
        Get index components
        :param index: Index name, e.g. "BTC-USD"
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_components"
        params = {'index': index}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": index,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_index_components_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_index_components_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return {}, status
        data = input_data['data']
        if len(data) > 0:
            # The API returns a dict with 'components', 'index', 'last', 'ts' keys
            target_data = data[0] if isinstance(data, list) else data
        else:
            target_data = {}
        return target_data, status

    def get_index_components(self, index, extra_data=None, **kwargs):
        """Get index components"""
        path, params, extra_data = self._get_index_components(index, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_components(self, index, extra_data=None, **kwargs):
        """Async get index components"""
        path, params, extra_data = self._get_index_components(index, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Public Data APIs (Additional) ====================

    def _get_estimated_price(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """
        Get estimated delivery/exercise price
        :param inst_type: Instrument type: `FUTURES`, `OPTION` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_estimated_price"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_estimated_price_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_estimated_price_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_estimated_price(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get estimated delivery/exercise price"""
        path, params, extra_data = self._get_estimated_price(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_estimated_price(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get estimated delivery/exercise price"""
        path, params, extra_data = self._get_estimated_price(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_discount_rate(self, ccy=None, discount_level=None, extra_data=None, **kwargs):
        """
        Get discount rate and interest-free quota
        :param ccy: Currency, e.g. `BTC`
        :param discount_level: Discount level, default is `lv1`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_discount_rate"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if discount_level:
            params['discountLevel'] = discount_level
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_discount_rate_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_discount_rate_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_discount_rate(self, ccy=None, discount_level=None, extra_data=None, **kwargs):
        """Get discount rate and interest-free quota"""
        path, params, extra_data = self._get_discount_rate(ccy, discount_level, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_discount_rate(self, ccy=None, discount_level=None, extra_data=None, **kwargs):
        """Async get discount rate and interest-free quota"""
        path, params, extra_data = self._get_discount_rate(ccy, discount_level, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_interest_rate_loan_quota(self, ccy=None, extra_data=None, **kwargs):
        """
        Get interest rate and loan quota
        :param ccy: Currency, e.g. `BTC`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_interest_rate_loan_quota"
        params = {}
        if ccy:
            params['ccy'] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_interest_rate_loan_quota_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_interest_rate_loan_quota_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_interest_rate_loan_quota(self, ccy=None, extra_data=None, **kwargs):
        """Get interest rate and loan quota"""
        path, params, extra_data = self._get_interest_rate_loan_quota(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_interest_rate_loan_quota(self, ccy=None, extra_data=None, **kwargs):
        """Async get interest rate and loan quota"""
        path, params, extra_data = self._get_interest_rate_loan_quota(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_interest_rate(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                           extra_data=None, **kwargs):
        """
        Get interest rate for borrowing
        :param ccy: Currency, e.g. `BTC`
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param mgn_mode: Margin mode, cross or isolated
        :param uly: Underlying, e.g. BTC-USD
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_interest_rate"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if inst_type:
            params['instType'] = inst_type
        if mgn_mode:
            params['mgnMode'] = mgn_mode
        if uly:
            params['uly'] = uly
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy or uly or "ALL",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_interest_rate(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                          extra_data=None, **kwargs):
        """Get interest rate for borrowing"""
        path, params, extra_data = self._get_interest_rate(ccy, inst_type, mgn_mode, uly,
                                                             extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_interest_rate(self, ccy=None, inst_type=None, mgn_mode=None, uly=None,
                                extra_data=None, **kwargs):
        """Async get interest rate for borrowing"""
        path, params, extra_data = self._get_interest_rate(ccy, inst_type, mgn_mode, uly,
                                                             extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_underlying(self, inst_type, uly=None, extra_data=None, **kwargs):
        """
        Get underlying index
        :param inst_type: Instrument type: `FUTURES`, `SWAP`, `OPTION` (required)
        :param uly: Underlying
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_underlying"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_underlying_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_underlying_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_underlying(self, inst_type, uly=None, extra_data=None, **kwargs):
        """Get underlying index"""
        path, params, extra_data = self._get_underlying(inst_type, uly, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_underlying(self, inst_type, uly=None, extra_data=None, **kwargs):
        """Async get underlying index"""
        path, params, extra_data = self._get_underlying(inst_type, uly, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_insurance_fund(self, inst_type, uly=None, inst_id=None, after=None,
                           before=None, limit=None, extra_data=None, **kwargs):
        """
        Get insurance fund balance
        :param inst_type: Instrument type: `MARGIN`, `FUTURES`, `SWAP`, `OPTION` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_insurance_fund"
        params = {'instType': inst_type}
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
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_insurance_fund_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_insurance_fund_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_insurance_fund(self, inst_type, uly=None, inst_id=None, after=None,
                          before=None, limit=None, extra_data=None, **kwargs):
        """Get insurance fund balance"""
        path, params, extra_data = self._get_insurance_fund(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_insurance_fund(self, inst_type, uly=None, inst_id=None, after=None,
                                before=None, limit=None, extra_data=None, **kwargs):
        """Async get insurance fund balance"""
        path, params, extra_data = self._get_insurance_fund(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _convert_contract_coin(self, inst_type, uly, inst_id, amount, unit, extra_data=None, **kwargs):
        """
        Convert contract unit
        :param inst_type: Instrument type: `FUTURES`, `SWAP` (required)
        :param uly: Underlying (required)
        :param inst_id: Instrument ID (required)
        :param amount: Quantity to be converted (required)
        :param unit: Unit of amount to be converted: `ccy`, `ct` (required)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "convert_contract_coin"
        params = {
            'instType': inst_type,
            'uly': uly,
            'instId': inst_id,
            'amount': amount,
            'unit': unit,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id,
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._convert_contract_coin_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _convert_contract_coin_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def convert_contract_coin(self, inst_type, uly, inst_id, amount, unit, extra_data=None, **kwargs):
        """Convert contract unit"""
        path, params, extra_data = self._convert_contract_coin(
            inst_type, uly, inst_id, amount, unit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_convert_contract_coin(self, inst_type, uly, inst_id, amount, unit, extra_data=None, **kwargs):
        """Async convert contract unit"""
        path, params, extra_data = self._convert_contract_coin(
            inst_type, uly, inst_id, amount, unit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_instrument_tick_bands(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """
        Get instrument minimum tick size
        :param inst_type: Instrument type: `SPOT`, `MARGIN`, `FUTURES`, `SWAP`, `OPTION` (required)
        :param uly: Underlying
        :param inst_id: Instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_instrument_tick_bands"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_instrument_tick_bands_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_instrument_tick_bands_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def get_instrument_tick_bands(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get instrument minimum tick size"""
        path, params, extra_data = self._get_instrument_tick_bands(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_instrument_tick_bands(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get instrument minimum tick size"""
        path, params, extra_data = self._get_instrument_tick_bands(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Missing Critical Public APIs ====================

    def _get_system_time(self, extra_data=None, **kwargs):
        """Get system time"""
        request_type = "get_system_time"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "SYSTEM",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_system_time(self, extra_data=None, **kwargs):
        """Get system time"""
        path, params, extra_data = self._get_system_time(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_system_time(self, extra_data=None, **kwargs):
        """Async get system time"""
        path, params, extra_data = self._get_system_time(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_tickers(self, inst_type="SWAP", uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get tickers for all instruments"""
        request_type = "get_tickers"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_tickers(self, inst_type="SWAP", uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get tickers for all instruments"""
        path, params, extra_data = self._get_tickers(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_tickers(self, inst_type="SWAP", uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async get tickers for all instruments"""
        path, params, extra_data = self._get_tickers(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_depth_full(self, symbol, sz=100, extra_data=None, **kwargs):
        """Get full depth order book"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth_full"
        params = {
            'instId': request_symbol,
            'sz': sz
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_depth_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_depth_full(self, symbol, sz=100, extra_data=None, **kwargs):
        """Get full depth order book"""
        path, params, extra_data = self._get_depth_full(symbol, sz, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_depth_full(self, symbol, sz=100, extra_data=None, **kwargs):
        """Async get full depth order book"""
        path, params, extra_data = self._get_depth_full(symbol, sz, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_kline_his(self, symbol, bar='1m', after='', before='', limit='100',
                       extra_data=None, **kwargs):
        """Get historical kline data"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_kline_his"
        params = {
            'instId': request_symbol,
            'bar': bar,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": MarketDataMixin._get_kline_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_kline_his(self, symbol, bar='1m', after='', before='', limit='100',
                      extra_data=None, **kwargs):
        """Get historical kline data"""
        path, params, extra_data = self._get_kline_his(symbol, bar, after, before,
                                                        limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_kline_his(self, symbol, bar='1m', after='', before='', limit='100',
                            extra_data=None, **kwargs):
        """Async get historical kline data"""
        path, params, extra_data = self._get_kline_his(symbol, bar, after, before,
                                                        limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_trades(self, symbol, limit='100', extra_data=None, **kwargs):
        """Get recent trades"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        params = {
            'instId': request_symbol,
            'limit': limit
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_trades(self, symbol, limit='100', extra_data=None, **kwargs):
        """Get recent trades"""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_trades(self, symbol, limit='100', extra_data=None, **kwargs):
        """Async get recent trades"""
        path, params, extra_data = self._get_trades(symbol, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_trades_history(self, symbol, after='', before='', limit='100',
                            extra_data=None, **kwargs):
        """Get historical trades data"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades_history"
        params = {
            'instId': request_symbol,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_trades_history(self, symbol, after='', before='', limit='100',
                           extra_data=None, **kwargs):
        """Get historical trades data"""
        path, params, extra_data = self._get_trades_history(symbol, after, before,
                                                             limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_trades_history(self, symbol, after='', before='', limit='100',
                                 extra_data=None, **kwargs):
        """Async get historical trades data"""
        path, params, extra_data = self._get_trades_history(symbol, after, before,
                                                             limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_public_instruments(self, inst_type, uly=None, inst_id=None, uly_multi=None,
                                 extra_data=None, **kwargs):
        """Get public instruments"""
        request_type = "get_public_instruments"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if uly_multi:
            params['ulyMulti'] = uly_multi
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_public_instruments(self, inst_type, uly=None, inst_id=None, uly_multi=None,
                               extra_data=None, **kwargs):
        """Get public instruments"""
        path, params, extra_data = self._get_public_instruments(inst_type, uly, inst_id,
                                                                 uly_multi, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_public_instruments(self, inst_type, uly=None, inst_id=None, uly_multi=None,
                                     extra_data=None, **kwargs):
        """Async get public instruments"""
        path, params, extra_data = self._get_public_instruments(inst_type, uly, inst_id,
                                                                 uly_multi, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_delivery_exercise_history(self, inst_type, uly, after='', before='', limit='100',
                                        extra_data=None, **kwargs):
        """Get delivery exercise history"""
        request_type = "get_delivery_exercise_history"
        params = {
            'instType': inst_type,
            'uly': uly,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly,
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_delivery_exercise_history(self, inst_type, uly, after='', before='', limit='100',
                                       extra_data=None, **kwargs):
        """Get delivery exercise history"""
        path, params, extra_data = self._get_delivery_exercise_history(inst_type, uly, after,
                                                                         before, limit, extra_data,
                                                                         **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_delivery_exercise_history(self, inst_type, uly, after='', before='',
                                            limit='100', extra_data=None, **kwargs):
        """Async get delivery exercise history"""
        path, params, extra_data = self._get_delivery_exercise_history(inst_type, uly, after,
                                                                         before, limit, extra_data,
                                                                         **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_estimated_settlement_price(self, inst_type, uly=None, inst_id=None,
                                         extra_data=None, **kwargs):
        """Get estimated settlement price"""
        request_type = "get_estimated_settlement_price"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_estimated_settlement_price(self, inst_type, uly=None, inst_id=None,
                                        extra_data=None, **kwargs):
        """Get estimated settlement price"""
        path, params, extra_data = self._get_estimated_settlement_price(inst_type, uly, inst_id,
                                                                          extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_estimated_settlement_price(self, inst_type, uly=None, inst_id=None,
                                              extra_data=None, **kwargs):
        """Async get estimated settlement price"""
        path, params, extra_data = self._get_estimated_settlement_price(inst_type, uly, inst_id,
                                                                          extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_settlement_history(self, inst_type, uly, after='', before='', limit='100',
                                 extra_data=None, **kwargs):
        """Get settlement history"""
        request_type = "get_settlement_history"
        params = {
            'instType': inst_type,
            'uly': uly,
        }
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly,
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_settlement_history(self, inst_type, uly, after='', before='', limit='100',
                                extra_data=None, **kwargs):
        """Get settlement history"""
        path, params, extra_data = self._get_settlement_history(inst_type, uly, after,
                                                                  before, limit, extra_data,
                                                                  **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_settlement_history(self, inst_type, uly, after='', before='', limit='100',
                                      extra_data=None, **kwargs):
        """Async get settlement history"""
        path, params, extra_data = self._get_settlement_history(inst_type, uly, after,
                                                                  before, limit, extra_data,
                                                                  **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_price_limit(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get price limit"""
        request_type = "get_price_limit"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_price_limit(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Get price limit"""
        path, params, extra_data = self._get_price_limit(inst_type, uly, inst_id,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_price_limit(self, inst_type, uly=None, inst_id=None,
                              extra_data=None, **kwargs):
        """Async get price limit"""
        path, params, extra_data = self._get_price_limit(inst_type, uly, inst_id,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_opt_summary(self, inst_type="OPTION", uly=None, exp_time=None, extra_data=None, **kwargs):
        """Get option summary"""
        request_type = "get_opt_summary"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if exp_time:
            params['expTime'] = exp_time
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_opt_summary(self, inst_type="OPTION", uly=None, exp_time=None,
                        extra_data=None, **kwargs):
        """Get option summary"""
        path, params, extra_data = self._get_opt_summary(inst_type, uly, exp_time,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_opt_summary(self, inst_type="OPTION", uly=None, exp_time=None,
                              extra_data=None, **kwargs):
        """Async get option summary"""
        path, params, extra_data = self._get_opt_summary(inst_type, uly, exp_time,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_position_tiers_public(self, inst_type, uly=None, inst_id=None, tier=None,
                                    extra_data=None, **kwargs):
        """Get position tiers (public)"""
        request_type = "get_position_tiers_public"
        params = {'instType': inst_type}
        if uly:
            params['uly'] = uly
        if inst_id:
            params['instId'] = inst_id
        if tier:
            params['tier'] = tier
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": inst_id or uly or "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_position_tiers_public(self, inst_type, uly=None, inst_id=None, tier=None,
                                  extra_data=None, **kwargs):
        """Get position tiers (public)"""
        path, params, extra_data = self._get_position_tiers_public(inst_type, uly, inst_id,
                                                                    tier, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_position_tiers_public(self, inst_type, uly=None, inst_id=None, tier=None,
                                        extra_data=None, **kwargs):
        """Async get position tiers (public)"""
        path, params, extra_data = self._get_position_tiers_public(inst_type, uly, inst_id,
                                                                    tier, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

