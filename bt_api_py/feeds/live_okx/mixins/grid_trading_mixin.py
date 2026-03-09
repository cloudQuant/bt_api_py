"""
OKX API - GridTradingMixin
Auto-generated from request_base.py
"""

from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function
from bt_api_py.functions.utils import update_extra_data


class GridTradingMixin:
    """Mixin providing OKX API methods."""

    # ==================== Grid Trading APIs ====================

    def _grid_order_algo(
        self,
        inst_id: Any,
        td_mode: Any,
        ccy: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        run_type: Any = None,
        sz: Any = None,
        base_sz: Any = None,
        trigger_px: Any = None,
        trigger_time: Any = None,
        attach_algo_cl_or: Any = None,
        attach_algo_om_trigger_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        fast_callback_speed: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Create grid strategy order"""
        request_type = "grid_order_algo"
        params = {
            "instId": inst_id,
            "tdMode": td_mode,
            "algoAlgoType": algo_algo_type,  # "grid_regular" or "grid_contract"
            "maxPx": max_px,
            "minPx": min_px,
            "gridNum": grid_num,
            "runType": run_type or "1",  # 1: single, 2: neutral
        }
        if ccy:
            params["ccy"] = ccy
        if sz is not None:
            params["sz"] = sz
        if base_sz is not None:
            params["baseSz"] = base_sz
        if trigger_px is not None:
            params["triggerPx"] = trigger_px
        if trigger_time is not None:
            params["triggerTime"] = trigger_time
        if attach_algo_cl_or is not None:
            params["attachAlgoClOrd"] = attach_algo_cl_or
        if attach_algo_om_trigger_px is not None:
            params["attachAlgoOmTriggerPx"] = attach_algo_om_trigger_px
        if tp_px is not None:
            params["tpPx"] = tp_px
        if tp_trigger_px is not None:
            params["tpTriggerPx"] = tp_trigger_px
        if sl_px is not None:
            params["slPx"] = sl_px
        if sl_trigger_px is not None:
            params["slTriggerPx"] = sl_trigger_px
        if fast_callback_speed is not None:
            params["fastCallbackSpeed"] = fast_callback_speed
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_order_algo(
        self,
        inst_id: Any,
        td_mode: Any,
        ccy: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        run_type: Any = None,
        sz: Any = None,
        base_sz: Any = None,
        trigger_px: Any = None,
        trigger_time: Any = None,
        attach_algo_cl_or: Any = None,
        attach_algo_om_trigger_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        fast_callback_speed: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Create grid strategy order"""
        path, params, extra_data = self._grid_order_algo(
            inst_id,
            td_mode,
            ccy,
            algo_algo_type,
            max_px,
            min_px,
            grid_num,
            run_type,
            sz,
            base_sz,
            trigger_px,
            trigger_time,
            attach_algo_cl_or,
            attach_algo_om_trigger_px,
            tp_px,
            tp_trigger_px,
            sl_px,
            sl_trigger_px,
            fast_callback_speed,
            extra_data,
            **kwargs,
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_order_algo(
        self,
        inst_id: Any,
        td_mode: Any,
        ccy: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        run_type: Any = None,
        sz: Any = None,
        base_sz: Any = None,
        trigger_px: Any = None,
        trigger_time: Any = None,
        attach_algo_cl_or: Any = None,
        attach_algo_om_trigger_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        fast_callback_speed: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async create grid strategy order"""
        path, params, extra_data = self._grid_order_algo(
            inst_id,
            td_mode,
            ccy,
            algo_algo_type,
            max_px,
            min_px,
            grid_num,
            run_type,
            sz,
            base_sz,
            trigger_px,
            trigger_time,
            attach_algo_cl_or,
            attach_algo_om_trigger_px,
            tp_px,
            tp_trigger_px,
            sl_px,
            sl_trigger_px,
            fast_callback_speed,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_amend_order_algo(
        self,
        algo_id: Any,
        inst_id: Any,
        trigger_px: Any = None,
        max_px: Any = None,
        min_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Amend grid strategy order"""
        request_type = "grid_amend_order_algo"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        if trigger_px is not None:
            params["triggerPx"] = trigger_px
        if max_px is not None:
            params["maxPx"] = max_px
        if min_px is not None:
            params["minPx"] = min_px
        if tp_px is not None:
            params["tpPx"] = tp_px
        if tp_trigger_px is not None:
            params["tpTriggerPx"] = tp_trigger_px
        if sl_px is not None:
            params["slPx"] = sl_px
        if sl_trigger_px is not None:
            params["slTriggerPx"] = sl_trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_amend_order_algo(
        self,
        algo_id: Any,
        inst_id: Any,
        trigger_px: Any = None,
        max_px: Any = None,
        min_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Amend grid strategy order"""
        path, params, extra_data = self._grid_amend_order_algo(
            algo_id,
            inst_id,
            trigger_px,
            max_px,
            min_px,
            tp_px,
            tp_trigger_px,
            sl_px,
            sl_trigger_px,
            extra_data,
            **kwargs,
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_amend_order_algo(
        self,
        algo_id: Any,
        inst_id: Any,
        trigger_px: Any = None,
        max_px: Any = None,
        min_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async amend grid strategy order"""
        path, params, extra_data = self._grid_amend_order_algo(
            algo_id,
            inst_id,
            trigger_px,
            max_px,
            min_px,
            tp_px,
            tp_trigger_px,
            sl_px,
            sl_trigger_px,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_stop_order_algo(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Stop grid strategy order"""
        request_type = "grid_stop_order_algo"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_stop_order_algo(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Stop grid strategy order"""
        path, params, extra_data = self._grid_stop_order_algo(
            algo_id, inst_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_stop_order_algo(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async stop grid strategy order"""
        path, params, extra_data = self._grid_stop_order_algo(
            algo_id, inst_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_orders_algo_pending(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid strategy pending orders"""
        request_type = "grid_orders_algo_pending"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if inst_id:
            params["instId"] = inst_id
        if algo_id:
            params["algoId"] = algo_id
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_orders_algo_pending(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid strategy pending orders"""
        path, params, extra_data = self._grid_orders_algo_pending(
            inst_type, inst_id, algo_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_orders_algo_pending(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get grid strategy pending orders"""
        path, params, extra_data = self._grid_orders_algo_pending(
            inst_type, inst_id, algo_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_orders_algo_history(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid strategy order history"""
        request_type = "grid_orders_algo_history"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if inst_id:
            params["instId"] = inst_id
        if algo_id:
            params["algoId"] = algo_id
        if state:
            params["state"] = state
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_orders_algo_history(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid strategy order history"""
        path, params, extra_data = self._grid_orders_algo_history(
            inst_type, inst_id, algo_id, state, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_orders_algo_history(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get grid strategy order history"""
        path, params, extra_data = self._grid_orders_algo_history(
            inst_type, inst_id, algo_id, state, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_amend_order_algo_basic(
        self,
        algo_id: Any,
        inst_id: Any,
        max_px: Any = None,
        min_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Amend grid order (basic parameters) - 修改网格委托(基础参数)"""
        request_type = "grid_amend_order_algo_basic"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        if max_px is not None:
            params["maxPx"] = max_px
        if min_px is not None:
            params["minPx"] = min_px
        if tp_px is not None:
            params["tpPx"] = tp_px
        if tp_trigger_px is not None:
            params["tpTriggerPx"] = tp_trigger_px
        if sl_px is not None:
            params["slPx"] = sl_px
        if sl_trigger_px is not None:
            params["slTriggerPx"] = sl_trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_amend_order_algo_basic(
        self,
        algo_id: Any,
        inst_id: Any,
        max_px: Any = None,
        min_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Amend grid order (basic parameters) - 修改网格委托(基础参数)"""
        path, params, extra_data = self._grid_amend_order_algo_basic(
            algo_id,
            inst_id,
            max_px,
            min_px,
            tp_px,
            tp_trigger_px,
            sl_px,
            sl_trigger_px,
            extra_data,
            **kwargs,
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_amend_order_algo_basic(
        self,
        algo_id: Any,
        inst_id: Any,
        max_px: Any = None,
        min_px: Any = None,
        tp_px: Any = None,
        tp_trigger_px: Any = None,
        sl_px: Any = None,
        sl_trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async amend grid order (basic parameters)"""
        path, params, extra_data = self._grid_amend_order_algo_basic(
            algo_id,
            inst_id,
            max_px,
            min_px,
            tp_px,
            tp_trigger_px,
            sl_px,
            sl_trigger_px,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_close_position(
        self,
        algo_id: Any,
        inst_id: Any,
        ccy: Any = None,
        margin: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Close futures grid position - 合约网格平仓"""
        request_type = "grid_close_position"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        if ccy:
            params["ccy"] = ccy
        if margin is not None:
            params["margin"] = margin
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_close_position(
        self,
        algo_id: Any,
        inst_id: Any,
        ccy: Any = None,
        margin: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Close futures grid position - 合约网格平仓"""
        path, params, extra_data = self._grid_close_position(
            algo_id, inst_id, ccy, margin, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_close_position(
        self,
        algo_id: Any,
        inst_id: Any,
        ccy: Any = None,
        margin: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async close futures grid position"""
        path, params, extra_data = self._grid_close_position(
            algo_id, inst_id, ccy, margin, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_cancel_close_order(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Cancel futures grid close order - 撤销合约网格平仓单"""
        request_type = "grid_cancel_close_order"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_cancel_close_order(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Cancel futures grid close order - 撤销合约网格平仓单"""
        path, params, extra_data = self._grid_cancel_close_order(
            algo_id, inst_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_cancel_close_order(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async cancel futures grid close order"""
        path, params, extra_data = self._grid_cancel_close_order(
            algo_id, inst_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_order_instant_trigger(
        self,
        algo_id: Any,
        inst_id: Any,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Grid order instant trigger - 网格委托立即触发"""
        request_type = "grid_order_instant_trigger"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        if trigger_px is not None:
            params["triggerPx"] = trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_order_instant_trigger(
        self,
        algo_id: Any,
        inst_id: Any,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Grid order instant trigger - 网格委托立即触发"""
        path, params, extra_data = self._grid_order_instant_trigger(
            algo_id, inst_id, trigger_px, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_order_instant_trigger(
        self,
        algo_id: Any,
        inst_id: Any,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async grid order instant trigger"""
        path, params, extra_data = self._grid_order_instant_trigger(
            algo_id, inst_id, trigger_px, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_orders_algo_details(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get grid order details - 获取网格委托详情"""
        request_type = "grid_orders_algo_details"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_orders_algo_details(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get grid order details - 获取网格委托详情"""
        path, params, extra_data = self._grid_orders_algo_details(
            algo_id, inst_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_orders_algo_details(
        self, algo_id: Any, inst_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get grid order details"""
        path, params, extra_data = self._grid_orders_algo_details(
            algo_id, inst_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_sub_orders(
        self,
        algo_id: Any,
        inst_id: Any,
        type: Any = None,
        ord_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid sub orders - 获取网格委托子订单"""
        request_type = "grid_sub_orders"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
        }
        if type is not None:
            params["type"] = type
        if ord_id:
            params["ordId"] = ord_id
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_sub_orders(
        self,
        algo_id: Any,
        inst_id: Any,
        type: Any = None,
        ord_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid sub orders - 获取网格委托子订单"""
        path, params, extra_data = self._grid_sub_orders(
            algo_id, inst_id, type, ord_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_sub_orders(
        self,
        algo_id: Any,
        inst_id: Any,
        type: Any = None,
        ord_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get grid sub orders"""
        path, params, extra_data = self._grid_sub_orders(
            algo_id, inst_id, type, ord_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_positions(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid positions - 获取网格委托持仓"""
        request_type = "grid_positions"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if inst_id:
            params["instId"] = inst_id
        if algo_id:
            params["algoId"] = algo_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_positions(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid positions - 获取网格委托持仓"""
        path, params, extra_data = self._grid_positions(
            inst_type, inst_id, algo_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_positions(
        self,
        inst_type: Any = None,
        inst_id: Any = None,
        algo_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get grid positions"""
        path, params, extra_data = self._grid_positions(
            inst_type, inst_id, algo_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_withdraw_income(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Spot grid withdraw income - 现货网格提取利润"""
        request_type = "grid_withdraw_income"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
            "amt": amt,
        }
        if ccy:
            params["ccy"] = ccy
        if type is not None:
            params["type"] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_withdraw_income(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Spot grid withdraw income - 现货网格提取利润"""
        path, params, extra_data = self._grid_withdraw_income(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_withdraw_income(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async spot grid withdraw income"""
        path, params, extra_data = self._grid_withdraw_income(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_compute_margin_balance(
        self,
        inst_id: Any,
        td_mode: Any,
        ccy: Any,
        algo_ords_type: Any,
        sz: Any,
        max_px: Any = None,
        min_px: Any = None,
        grid_num: Any = None,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Compute margin balance - 计算保证金余额"""
        request_type = "grid_compute_margin_balance"
        params = {
            "instId": inst_id,
            "tdMode": td_mode,
            "ccy": ccy,
            "algoOrdsType": algo_ords_type,
            "sz": sz,
        }
        if max_px is not None:
            params["maxPx"] = max_px
        if min_px is not None:
            params["minPx"] = min_px
        if grid_num is not None:
            params["gridNum"] = grid_num
        if trigger_px is not None:
            params["triggerPx"] = trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_compute_margin_balance(
        self,
        inst_id: Any,
        td_mode: Any,
        ccy: Any,
        algo_ords_type: Any,
        sz: Any,
        max_px: Any = None,
        min_px: Any = None,
        grid_num: Any = None,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Compute margin balance - 计算保证金余额"""
        path, params, extra_data = self._grid_compute_margin_balance(
            inst_id,
            td_mode,
            ccy,
            algo_ords_type,
            sz,
            max_px,
            min_px,
            grid_num,
            trigger_px,
            extra_data,
            **kwargs,
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_compute_margin_balance(
        self,
        inst_id: Any,
        td_mode: Any,
        ccy: Any,
        algo_ords_type: Any,
        sz: Any,
        max_px: Any = None,
        min_px: Any = None,
        grid_num: Any = None,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async compute margin balance"""
        path, params, extra_data = self._grid_compute_margin_balance(
            inst_id,
            td_mode,
            ccy,
            algo_ords_type,
            sz,
            max_px,
            min_px,
            grid_num,
            trigger_px,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_margin_balance(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Adjust margin - 调整保证金"""
        request_type = "grid_margin_balance"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
            "amt": amt,
        }
        if ccy:
            params["ccy"] = ccy
        if type is not None:
            params["type"] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_margin_balance(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Adjust margin - 调整保证金"""
        path, params, extra_data = self._grid_margin_balance(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_margin_balance(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async adjust margin"""
        path, params, extra_data = self._grid_margin_balance(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_add_investment(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Add investment - 增加投入币数量"""
        request_type = "grid_add_investment"
        params = {
            "algoId": algo_id,
            "instId": inst_id,
            "amt": amt,
        }
        if ccy:
            params["ccy"] = ccy
        if type is not None:
            params["type"] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_add_investment(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Add investment - 增加投入币数量"""
        path, params, extra_data = self._grid_add_investment(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_add_investment(
        self,
        algo_id: Any,
        inst_id: Any,
        amt: Any,
        ccy: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async add investment"""
        path, params, extra_data = self._grid_add_investment(
            algo_id, inst_id, amt, ccy, type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_get_ai_param(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any = None,
        min_px: Any = None,
        grid_num: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid AI parameters - 获取网格AI参数"""
        request_type = "grid_get_ai_param"
        params = {
            "instId": inst_id,
            "algoAlgoType": algo_algo_type,
        }
        if max_px is not None:
            params["maxPx"] = max_px
        if min_px is not None:
            params["minPx"] = min_px
        if grid_num is not None:
            params["gridNum"] = grid_num
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_get_ai_param(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any = None,
        min_px: Any = None,
        grid_num: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get grid AI parameters - 获取网格AI参数"""
        path, params, extra_data = self._grid_get_ai_param(
            inst_id, algo_algo_type, max_px, min_px, grid_num, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_get_ai_param(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any = None,
        min_px: Any = None,
        grid_num: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get grid AI parameters"""
        path, params, extra_data = self._grid_get_ai_param(
            inst_id, algo_algo_type, max_px, min_px, grid_num, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_compute_min_investment(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        run_type: Any = None,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Compute minimum investment - 计算最小投入金额"""
        request_type = "grid_compute_min_investment"
        params = {
            "instId": inst_id,
            "algoAlgoType": algo_algo_type,
            "maxPx": max_px,
            "minPx": min_px,
            "gridNum": grid_num,
        }
        if run_type is not None:
            params["runType"] = run_type
        if trigger_px is not None:
            params["triggerPx"] = trigger_px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_compute_min_investment(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        run_type: Any = None,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Compute minimum investment - 计算最小投入金额"""
        path, params, extra_data = self._grid_compute_min_investment(
            inst_id,
            algo_algo_type,
            max_px,
            min_px,
            grid_num,
            run_type,
            trigger_px,
            extra_data,
            **kwargs,
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_grid_compute_min_investment(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        run_type: Any = None,
        trigger_px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async compute minimum investment"""
        path, params, extra_data = self._grid_compute_min_investment(
            inst_id,
            algo_algo_type,
            max_px,
            min_px,
            grid_num,
            run_type,
            trigger_px,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_rsi_back_testing(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        time_type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """RSI back testing - RSI回测"""
        request_type = "grid_rsi_back_testing"
        params = {
            "instId": inst_id,
            "algoAlgoType": algo_algo_type,
            "maxPx": max_px,
            "minPx": min_px,
            "gridNum": grid_num,
        }
        if time_type is not None:
            params["timeType"] = time_type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_rsi_back_testing(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        time_type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """RSI back testing - RSI回测"""
        path, params, extra_data = self._grid_rsi_back_testing(
            inst_id, algo_algo_type, max_px, min_px, grid_num, time_type, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_rsi_back_testing(
        self,
        inst_id: Any,
        algo_algo_type: Any,
        max_px: Any,
        min_px: Any,
        grid_num: Any,
        time_type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async RSI back testing"""
        path, params, extra_data = self._grid_rsi_back_testing(
            inst_id, algo_algo_type, max_px, min_px, grid_num, time_type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _grid_max_grid_quantity(
        self, inst_id: Any, algo_algo_type: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get max grid quantity - 最大网格数量"""
        request_type = "grid_max_grid_quantity"
        params = {
            "instId": inst_id,
            "algoAlgoType": algo_algo_type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def grid_max_grid_quantity(
        self, inst_id: Any, algo_algo_type: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get max grid quantity - 最大网格数量"""
        path, params, extra_data = self._grid_max_grid_quantity(
            inst_id, algo_algo_type, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_grid_max_grid_quantity(
        self, inst_id: Any, algo_algo_type: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get max grid quantity"""
        path, params, extra_data = self._grid_max_grid_quantity(
            inst_id, algo_algo_type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
