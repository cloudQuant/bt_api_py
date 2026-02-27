# -*- coding: utf-8 -*-
"""
OKX API - CopyTradingMixin
Auto-generated from request_base.py
"""
from bt_api_py.functions.utils import update_extra_data


class CopyTradingMixin:
    """Mixin providing OKX API methods."""
    # ==================== Copy Trading APIs ====================

    def _copytrading_get_current_subpositions(self, inst_type=None, inst_id=None, after=None,
                                              before=None, limit=None, extra_data=None, **kwargs):
        """
        Get existing lead positions
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param inst_id: Instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_current_subpositions"
        params = {}
        if inst_type:
            params['instType'] = inst_type
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
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_current_subpositions_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_current_subpositions_normalize_function(input_data, extra_data):
        """Normalize copy trading current subpositions data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_current_subpositions(self, inst_type=None, inst_id=None, after=None,
                                             before=None, limit=None, extra_data=None, **kwargs):
        """Get existing lead positions"""
        path, params, extra_data = self._copytrading_get_current_subpositions(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_current_subpositions(self, inst_type=None, inst_id=None, after=None,
                                                   before=None, limit=None, extra_data=None, **kwargs):
        """Async get existing lead positions"""
        path, params, extra_data = self._copytrading_get_current_subpositions(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_subpositions_history(self, inst_type=None, inst_id=None, after=None,
                                              before=None, limit=None, extra_data=None, **kwargs):
        """
        Lead position history
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param inst_id: Instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_subpositions_history"
        params = {}
        if inst_type:
            params['instType'] = inst_type
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
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_subpositions_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_subpositions_history_normalize_function(input_data, extra_data):
        """Normalize copy trading subpositions history data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_subpositions_history(self, inst_type=None, inst_id=None, after=None,
                                             before=None, limit=None, extra_data=None, **kwargs):
        """Lead position history"""
        path, params, extra_data = self._copytrading_get_subpositions_history(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_subpositions_history(self, inst_type=None, inst_id=None, after=None,
                                                   before=None, limit=None, extra_data=None, **kwargs):
        """Async lead position history"""
        path, params, extra_data = self._copytrading_get_subpositions_history(
            inst_type, inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_algo_order(self, sub_pos_id, tp_trigger_px=None, tp_trigger_px_type=None,
                                sl_trigger_px=None, sl_trigger_px_type=None, tp_ord_px=None,
                                sl_ord_px=None, extra_data=None, **kwargs):
        """
        Place lead stop order
        :param sub_pos_id: Sub position ID
        :param tp_trigger_px: Take profit trigger price
        :param tp_trigger_px_type: Take profit trigger price type: last, index, mark
        :param sl_trigger_px: Stop loss trigger price
        :param sl_trigger_px_type: Stop loss trigger price type: last, index, mark
        :param tp_ord_px: Take profit order price
        :param sl_ord_px: Stop loss order price
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_algo_order"
        body = {
            'subPosId': sub_pos_id,
        }
        if tp_trigger_px is not None:
            body['tpTriggerPx'] = str(tp_trigger_px)
        if tp_trigger_px_type:
            body['tpTriggerPxType'] = tp_trigger_px_type
        if sl_trigger_px is not None:
            body['slTriggerPx'] = str(sl_trigger_px)
        if sl_trigger_px_type:
            body['slTriggerPxType'] = sl_trigger_px_type
        if tp_ord_px is not None:
            body['tpOrdPx'] = str(tp_ord_px)
        if sl_ord_px is not None:
            body['slOrdPx'] = str(sl_ord_px)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_pos_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_algo_order_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_algo_order_normalize_function(input_data, extra_data):
        """Normalize copy trading algo order response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_algo_order(self, sub_pos_id, tp_trigger_px=None, tp_trigger_px_type=None,
                               sl_trigger_px=None, sl_trigger_px_type=None, tp_ord_px=None,
                               sl_ord_px=None, extra_data=None, **kwargs):
        """Place lead stop order"""
        path, body, extra_data = self._copytrading_algo_order(
            sub_pos_id, tp_trigger_px, tp_trigger_px_type, sl_trigger_px,
            sl_trigger_px_type, tp_ord_px, sl_ord_px, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_algo_order(self, sub_pos_id, tp_trigger_px=None, tp_trigger_px_type=None,
                                     sl_trigger_px=None, sl_trigger_px_type=None, tp_ord_px=None,
                                     sl_ord_px=None, extra_data=None, **kwargs):
        """Async place lead stop order"""
        path, body, extra_data = self._copytrading_algo_order(
            sub_pos_id, tp_trigger_px, tp_trigger_px_type, sl_trigger_px,
            sl_trigger_px_type, tp_ord_px, sl_ord_px, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_close_subposition(self, sub_pos_id, extra_data=None, **kwargs):
        """
        Close lead position
        :param sub_pos_id: Sub position ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_close_subposition"
        body = {
            'subPosId': sub_pos_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": sub_pos_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_close_subposition_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_close_subposition_normalize_function(input_data, extra_data):
        """Normalize copy trading close subposition response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_close_subposition(self, sub_pos_id, extra_data=None, **kwargs):
        """Close lead position"""
        path, body, extra_data = self._copytrading_close_subposition(sub_pos_id, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_close_subposition(self, sub_pos_id, extra_data=None, **kwargs):
        """Async close lead position"""
        path, body, extra_data = self._copytrading_close_subposition(sub_pos_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_instruments(self, extra_data=None, **kwargs):
        """
        Leading instruments
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_instruments"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_instruments_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_instruments_normalize_function(input_data, extra_data):
        """Normalize copy trading instruments data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_instruments(self, extra_data=None, **kwargs):
        """Leading instruments"""
        path, params, extra_data = self._copytrading_get_instruments(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_instruments(self, extra_data=None, **kwargs):
        """Async leading instruments"""
        path, params, extra_data = self._copytrading_get_instruments(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_set_instruments(self, inst_type, inst_ids=None, extra_data=None, **kwargs):
        """
        Amend leading instruments
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param inst_ids: Instrument ID list
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_set_instruments"
        body = {
            'instType': inst_type,
        }
        if inst_ids:
            body['instId'] = ','.join(inst_ids) if isinstance(inst_ids, list) else inst_ids
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": inst_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_set_instruments_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_set_instruments_normalize_function(input_data, extra_data):
        """Normalize copy trading set instruments response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_set_instruments(self, inst_type, inst_ids=None, extra_data=None, **kwargs):
        """Amend leading instruments"""
        path, body, extra_data = self._copytrading_set_instruments(inst_type, inst_ids, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_set_instruments(self, inst_type, inst_ids=None, extra_data=None, **kwargs):
        """Async amend leading instruments"""
        path, body, extra_data = self._copytrading_set_instruments(inst_type, inst_ids, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_profit_sharing_details(self, after=None, before=None, limit=None,
                                                 extra_data=None, **kwargs):
        """
        Profit sharing details
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_profit_sharing_details"
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
            "normalize_function": CopyTradingMixin._copytrading_get_profit_sharing_details_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_profit_sharing_details_normalize_function(input_data, extra_data):
        """Normalize copy trading profit sharing details data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_profit_sharing_details(self, after=None, before=None, limit=None,
                                               extra_data=None, **kwargs):
        """Profit sharing details"""
        path, params, extra_data = self._copytrading_get_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_profit_sharing_details(self, after=None, before=None, limit=None,
                                                     extra_data=None, **kwargs):
        """Async profit sharing details"""
        path, params, extra_data = self._copytrading_get_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_total_profit_sharing(self, extra_data=None, **kwargs):
        """
        Total profit sharing
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_total_profit_sharing"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_total_profit_sharing_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_total_profit_sharing_normalize_function(input_data, extra_data):
        """Normalize copy trading total profit sharing data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_total_profit_sharing(self, extra_data=None, **kwargs):
        """Total profit sharing"""
        path, params, extra_data = self._copytrading_get_total_profit_sharing(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_total_profit_sharing(self, extra_data=None, **kwargs):
        """Async total profit sharing"""
        path, params, extra_data = self._copytrading_get_total_profit_sharing(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_unrealized_profit_sharing_details(self, after=None, before=None,
                                                             limit=None, extra_data=None, **kwargs):
        """
        Unrealized profit sharing details
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_unrealized_profit_sharing_details"
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
            "normalize_function": CopyTradingMixin._copytrading_get_unrealized_profit_sharing_details_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_unrealized_profit_sharing_details_normalize_function(input_data, extra_data):
        """Normalize copy trading unrealized profit sharing details data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_unrealized_profit_sharing_details(self, after=None, before=None,
                                                           limit=None, extra_data=None, **kwargs):
        """Unrealized profit sharing details"""
        path, params, extra_data = self._copytrading_get_unrealized_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_unrealized_profit_sharing_details(self, after=None, before=None,
                                                                 limit=None, extra_data=None, **kwargs):
        """Async unrealized profit sharing details"""
        path, params, extra_data = self._copytrading_get_unrealized_profit_sharing_details(
            after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_total_unrealized_profit_sharing(self, extra_data=None, **kwargs):
        """
        Total unrealized profit sharing
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_total_unrealized_profit_sharing"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_total_unrealized_profit_sharing_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_total_unrealized_profit_sharing_normalize_function(input_data, extra_data):
        """Normalize copy trading total unrealized profit sharing data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_total_unrealized_profit_sharing(self, extra_data=None, **kwargs):
        """Total unrealized profit sharing"""
        path, params, extra_data = self._copytrading_get_total_unrealized_profit_sharing(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_total_unrealized_profit_sharing(self, extra_data=None, **kwargs):
        """Async total unrealized profit sharing"""
        path, params, extra_data = self._copytrading_get_total_unrealized_profit_sharing(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_set_profit_sharing_ratio(self, profit_sharing_ratio, extra_data=None, **kwargs):
        """
        Amend profit sharing ratio
        :param profit_sharing_ratio: Profit sharing ratio, e.g. 10 means 10%
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_set_profit_sharing_ratio"
        body = {
            'profitSharingRatio': str(profit_sharing_ratio),
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_set_profit_sharing_ratio_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_set_profit_sharing_ratio_normalize_function(input_data, extra_data):
        """Normalize copy trading set profit sharing ratio response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_set_profit_sharing_ratio(self, profit_sharing_ratio, extra_data=None, **kwargs):
        """Amend profit sharing ratio"""
        path, body, extra_data = self._copytrading_set_profit_sharing_ratio(profit_sharing_ratio, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_set_profit_sharing_ratio(self, profit_sharing_ratio, extra_data=None, **kwargs):
        """Async amend profit sharing ratio"""
        path, body, extra_data = self._copytrading_set_profit_sharing_ratio(profit_sharing_ratio, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_config(self, extra_data=None, **kwargs):
        """
        Account configuration
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_config"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_config_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_config_normalize_function(input_data, extra_data):
        """Normalize copy trading config data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_config(self, extra_data=None, **kwargs):
        """Account configuration"""
        path, params, extra_data = self._copytrading_get_config(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_config(self, extra_data=None, **kwargs):
        """Async account configuration"""
        path, params, extra_data = self._copytrading_get_config(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_first_copy_settings(self, copy_inst_id, lever=None,
                                          extra_data=None, **kwargs):
        """
        First copy settings
        :param copy_inst_id: Copy instrument ID, unique identifier for the lead trader
        :param lever: Leverage
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_first_copy_settings"
        body = {
            'copyInstId': copy_inst_id,
        }
        if lever is not None:
            body['lever'] = str(lever)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_first_copy_settings_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_first_copy_settings_normalize_function(input_data, extra_data):
        """Normalize copy trading first copy settings response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_first_copy_settings(self, copy_inst_id, lever=None,
                                         extra_data=None, **kwargs):
        """First copy settings"""
        path, body, extra_data = self._copytrading_first_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_first_copy_settings(self, copy_inst_id, lever=None,
                                              extra_data=None, **kwargs):
        """Async first copy settings"""
        path, body, extra_data = self._copytrading_first_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_amend_copy_settings(self, copy_inst_id, lever=None,
                                          extra_data=None, **kwargs):
        """
        Amend copy settings
        :param copy_inst_id: Copy instrument ID, unique identifier for the lead trader
        :param lever: Leverage
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_amend_copy_settings"
        body = {
            'copyInstId': copy_inst_id,
        }
        if lever is not None:
            body['lever'] = str(lever)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_amend_copy_settings_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_amend_copy_settings_normalize_function(input_data, extra_data):
        """Normalize copy trading amend copy settings response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_amend_copy_settings(self, copy_inst_id, lever=None,
                                         extra_data=None, **kwargs):
        """Amend copy settings"""
        path, body, extra_data = self._copytrading_amend_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_amend_copy_settings(self, copy_inst_id, lever=None,
                                              extra_data=None, **kwargs):
        """Async amend copy settings"""
        path, body, extra_data = self._copytrading_amend_copy_settings(
            copy_inst_id, lever, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_stop_copy_trading(self, copy_inst_id, extra_data=None, **kwargs):
        """
        Stop copying
        :param copy_inst_id: Copy instrument ID, unique identifier for the lead trader
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "copytrading_stop_copy_trading"
        body = {
            'copyInstId': copy_inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_stop_copy_trading_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    @staticmethod
    def _copytrading_stop_copy_trading_normalize_function(input_data, extra_data):
        """Normalize copy trading stop copy trading response"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = [data[0]]
        else:
            target_data = []
        return target_data, status

    def copytrading_stop_copy_trading(self, copy_inst_id, extra_data=None, **kwargs):
        """Stop copying"""
        path, body, extra_data = self._copytrading_stop_copy_trading(copy_inst_id, extra_data, **kwargs)
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_copytrading_stop_copy_trading(self, copy_inst_id, extra_data=None, **kwargs):
        """Async stop copying"""
        path, body, extra_data = self._copytrading_stop_copy_trading(copy_inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=body, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_copy_settings(self, extra_data=None, **kwargs):
        """
        Get copy settings
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_copy_settings"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_copy_settings_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_copy_settings_normalize_function(input_data, extra_data):
        """Normalize copy trading copy settings data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_copy_settings(self, extra_data=None, **kwargs):
        """Get copy settings"""
        path, params, extra_data = self._copytrading_get_copy_settings(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_copy_settings(self, extra_data=None, **kwargs):
        """Async get copy settings"""
        path, params, extra_data = self._copytrading_get_copy_settings(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_batch_leverage_info(self, extra_data=None, **kwargs):
        """
        My lead traders
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_batch_leverage_info"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_batch_leverage_info_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_batch_leverage_info_normalize_function(input_data, extra_data):
        """Normalize copy trading batch leverage info data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_batch_leverage_info(self, extra_data=None, **kwargs):
        """My lead traders"""
        path, params, extra_data = self._copytrading_get_batch_leverage_info(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_batch_leverage_info(self, extra_data=None, **kwargs):
        """Async my lead traders"""
        path, params, extra_data = self._copytrading_get_batch_leverage_info(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_get_copy_trading_configuration(self, extra_data=None, **kwargs):
        """
        Copy trading configuration
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_get_copy_trading_configuration"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_get_copy_trading_configuration_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_get_copy_trading_configuration_normalize_function(input_data, extra_data):
        """Normalize copy trading configuration data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_get_copy_trading_configuration(self, extra_data=None, **kwargs):
        """Copy trading configuration"""
        path, params, extra_data = self._copytrading_get_copy_trading_configuration(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_get_copy_trading_configuration(self, extra_data=None, **kwargs):
        """Async copy trading configuration"""
        path, params, extra_data = self._copytrading_get_copy_trading_configuration(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Copy Trading Public APIs ====================

    def _copytrading_public_lead_traders(self, inst_type=None, sort_by=None, uly=None,
                                         after=None, before=None, limit=None,
                                         extra_data=None, **kwargs):
        """
        Lead trader ranks (public)
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param sort_by: Sort by, e.g. totalProfitSharing
        :param uly: Underlying
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_lead_traders"
        params = {}
        if inst_type:
            params['instType'] = inst_type
        if sort_by:
            params['sortBy'] = sort_by
        if uly:
            params['uly'] = uly
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "PUBLIC",
            "asset_type": inst_type or self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_lead_traders_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_lead_traders_normalize_function(input_data, extra_data):
        """Normalize copy trading public lead traders data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_lead_traders(self, inst_type=None, sort_by=None, uly=None,
                                        after=None, before=None, limit=None,
                                        extra_data=None, **kwargs):
        """Lead trader ranks (public)"""
        path, params, extra_data = self._copytrading_public_lead_traders(
            inst_type, sort_by, uly, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_lead_traders(self, inst_type=None, sort_by=None, uly=None,
                                              after=None, before=None, limit=None,
                                              extra_data=None, **kwargs):
        """Async lead trader ranks (public)"""
        path, params, extra_data = self._copytrading_public_lead_traders(
            inst_type, sort_by, uly, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_weekly_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                       extra_data=None, **kwargs):
        """
        Lead trader weekly PnL (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_weekly_pnl"
        params = {
            'copyInstId': copy_inst_id,
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
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_weekly_pnl_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_weekly_pnl_normalize_function(input_data, extra_data):
        """Normalize copy trading public weekly PnL data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_weekly_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Lead trader weekly PnL (public)"""
        path, params, extra_data = self._copytrading_public_weekly_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_weekly_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                            extra_data=None, **kwargs):
        """Async lead trader weekly PnL (public)"""
        path, params, extra_data = self._copytrading_public_weekly_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """
        Lead trader daily PnL (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_pnl"
        params = {
            'copyInstId': copy_inst_id,
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
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_pnl_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_pnl_normalize_function(input_data, extra_data):
        """Normalize copy trading public PnL data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """Lead trader daily PnL (public)"""
        path, params, extra_data = self._copytrading_public_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_pnl(self, copy_inst_id, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Async lead trader daily PnL (public)"""
        path, params, extra_data = self._copytrading_public_pnl(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_stats(self, copy_inst_id, extra_data=None, **kwargs):
        """
        Lead trader stats (public)
        :param copy_inst_id: Copy instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_stats"
        params = {
            'copyInstId': copy_inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_stats_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_stats_normalize_function(input_data, extra_data):
        """Normalize copy trading public stats data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_stats(self, copy_inst_id, extra_data=None, **kwargs):
        """Lead trader stats (public)"""
        path, params, extra_data = self._copytrading_public_stats(copy_inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_stats(self, copy_inst_id, extra_data=None, **kwargs):
        """Async lead trader stats (public)"""
        path, params, extra_data = self._copytrading_public_stats(copy_inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_preference_currency(self, copy_inst_id, extra_data=None, **kwargs):
        """
        Lead trader currency preferences (public)
        :param copy_inst_id: Copy instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_preference_currency"
        params = {
            'copyInstId': copy_inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_preference_currency_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_preference_currency_normalize_function(input_data, extra_data):
        """Normalize copy trading public preference currency data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_preference_currency(self, copy_inst_id, extra_data=None, **kwargs):
        """Lead trader currency preferences (public)"""
        path, params, extra_data = self._copytrading_public_preference_currency(
            copy_inst_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_preference_currency(self, copy_inst_id, extra_data=None, **kwargs):
        """Async lead trader currency preferences (public)"""
        path, params, extra_data = self._copytrading_public_preference_currency(
            copy_inst_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_current_subpositions(self, copy_inst_id, after=None, before=None,
                                                 limit=None, extra_data=None, **kwargs):
        """
        Lead trader current positions (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_current_subpositions"
        params = {
            'copyInstId': copy_inst_id,
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
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_current_subpositions_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_current_subpositions_normalize_function(input_data, extra_data):
        """Normalize copy trading public current subpositions data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_current_subpositions(self, copy_inst_id, after=None, before=None,
                                                limit=None, extra_data=None, **kwargs):
        """Lead trader current positions (public)"""
        path, params, extra_data = self._copytrading_public_current_subpositions(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_current_subpositions(self, copy_inst_id, after=None, before=None,
                                                     limit=None, extra_data=None, **kwargs):
        """Async lead trader current positions (public)"""
        path, params, extra_data = self._copytrading_public_current_subpositions(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_subpositions_history(self, copy_inst_id, after=None, before=None,
                                                 limit=None, extra_data=None, **kwargs):
        """
        Lead trader position history (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_subpositions_history"
        params = {
            'copyInstId': copy_inst_id,
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
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_subpositions_history_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_subpositions_history_normalize_function(input_data, extra_data):
        """Normalize copy trading public subpositions history data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_subpositions_history(self, copy_inst_id, after=None, before=None,
                                                limit=None, extra_data=None, **kwargs):
        """Lead trader position history (public)"""
        path, params, extra_data = self._copytrading_public_subpositions_history(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_subpositions_history(self, copy_inst_id, after=None, before=None,
                                                     limit=None, extra_data=None, **kwargs):
        """Async lead trader position history (public)"""
        path, params, extra_data = self._copytrading_public_subpositions_history(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _copytrading_public_copy_traders(self, copy_inst_id, after=None, before=None,
                                         limit=None, extra_data=None, **kwargs):
        """
        Copy traders (public)
        :param copy_inst_id: Copy instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "copytrading_public_copy_traders"
        params = {
            'copyInstId': copy_inst_id,
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
            "symbol_name": copy_inst_id,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": CopyTradingMixin._copytrading_public_copy_traders_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _copytrading_public_copy_traders_normalize_function(input_data, extra_data):
        """Normalize copy trading public copy traders data"""
        status = True if input_data.get("code") == '0' else False
        if 'data' not in input_data:
            return [], status
        data = input_data['data']
        if len(data) > 0:
            target_data = data
        else:
            target_data = []
        return target_data, status

    def copytrading_public_copy_traders(self, copy_inst_id, after=None, before=None,
                                        limit=None, extra_data=None, **kwargs):
        """Copy traders (public)"""
        path, params, extra_data = self._copytrading_public_copy_traders(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_copytrading_public_copy_traders(self, copy_inst_id, after=None, before=None,
                                              limit=None, extra_data=None, **kwargs):
        """Async copy traders (public)"""
        path, params, extra_data = self._copytrading_public_copy_traders(
            copy_inst_id, after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

