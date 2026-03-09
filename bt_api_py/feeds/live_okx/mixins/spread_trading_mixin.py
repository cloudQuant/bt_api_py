"""
OKX API - SpreadTradingMixin
Auto-generated from request_base.py
"""

from typing import Any

from bt_api_py.functions.utils import update_extra_data


class SpreadTradingMixin:
    """Mixin providing OKX API methods."""

    # ==================== Spread Trading APIs ====================

    def _sprd_order(
        self,
        sprd_id: Any,
        side: Any,
        sz: Any,
        px: Any = None,
        reduce_only: Any = False,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        pos_side: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Place spread order
        :param sprd_id: Spread instrument ID
        :param side: Order side: buy, sell
        :param sz: Order size
        :param px: Order price, required for limit orders
        :param reduce_only: Whether to reduce position only
        :param ccy: Currency
        :param cl_ord_id: Client order ID
        :param tag: Order tag
        :param pos_side: Position side
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_order"
        params = {
            "sprdId": sprd_id,
            "side": side,
            "sz": str(sz),
        }
        if px is not None:
            params["px"] = str(px)
        if reduce_only:
            params["reduceOnly"] = "true"
        if ccy:
            params["ccy"] = ccy
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        if tag:
            params["tag"] = tag
        if pos_side:
            params["posSide"] = pos_side
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sprd_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": SpreadTradingMixin._sprd_order_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_order_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize spread order response"""
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"][0]
        result = {
            "order_id": data.get("ordId"),
            "client_order_id": data.get("clOrdId"),
            "sprd_id": data.get("sprdId"),
            "side": data.get("side"),
            "size": data.get("sz"),
            "price": data.get("px"),
            "pos_side": data.get("posSide"),
            "state": data.get("state"),
            "avg_px": data.get("avgPx"),
            "filled_sz": data.get("fillSz"),
            "fee": data.get("fee"),
            "s_code": data.get("sCode"),
            "s_msg": data.get("sMsg"),
        }
        return [result], status

    def sprd_order(
        self,
        sprd_id: Any,
        side: Any,
        sz: Any,
        px: Any = None,
        reduce_only: Any = False,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        pos_side: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Place spread order"""
        path, params, extra_data = self._sprd_order(
            sprd_id, side, sz, px, reduce_only, ccy, cl_ord_id, tag, pos_side, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_sprd_order(
        self,
        sprd_id: Any,
        side: Any,
        sz: Any,
        px: Any = None,
        reduce_only: Any = False,
        ccy: Any = None,
        cl_ord_id: Any = None,
        tag: Any = None,
        pos_side: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async place spread order"""
        path, params, extra_data = self._sprd_order(
            sprd_id, side, sz, px, reduce_only, ccy, cl_ord_id, tag, pos_side, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _sprd_cancel_order(
        self,
        sprd_id: Any,
        order_id: Any = None,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Cancel spread order
        :param sprd_id: Spread instrument ID
        :param order_id: Order ID
        :param client_order_id: Client order ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_cancel_order"
        params = {"sprdId": sprd_id}
        if order_id:
            params["ordId"] = order_id
        if client_order_id:
            params["clOrdId"] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sprd_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": SpreadTradingMixin._sprd_cancel_order_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_cancel_order_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize spread cancel order response"""
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"][0]
        result = {
            "order_id": data.get("ordId"),
            "client_order_id": data.get("clOrdId"),
            "s_code": data.get("sCode"),
            "s_msg": data.get("sMsg"),
        }
        return [result], status

    def sprd_cancel_order(
        self,
        sprd_id: Any,
        order_id: Any = None,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Cancel spread order"""
        path, params, extra_data = self._sprd_cancel_order(
            sprd_id, order_id, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_sprd_cancel_order(
        self,
        sprd_id: Any,
        order_id: Any = None,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async cancel spread order"""
        path, params, extra_data = self._sprd_cancel_order(
            sprd_id, order_id, client_order_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _sprd_get_order(
        self,
        sprd_id: Any,
        order_id: Any = None,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get spread order details
        :param sprd_id: Spread instrument ID
        :param order_id: Order ID
        :param client_order_id: Client order ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_order"
        params = {"sprdId": sprd_id}
        if order_id:
            params["ordId"] = order_id
        if client_order_id:
            params["clOrdId"] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sprd_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": SpreadTradingMixin._sprd_get_order_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_order_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize spread get order response"""
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"]
        results = []
        for item in data:
            result = {
                "sprd_id": item.get("sprdId"),
                "order_id": item.get("ordId"),
                "client_order_id": item.get("clOrdId"),
                "side": item.get("side"),
                "pos_side": item.get("posSide"),
                "size": item.get("sz"),
                "price": item.get("px"),
                "state": item.get("state"),
                "avg_px": item.get("avgPx"),
                "filled_sz": item.get("fillSz"),
                "fee": item.get("fee"),
                "fee_ccy": item.get("feeCcy"),
                "source": item.get("source"),
                "create_time": item.get("cTime"),
                "update_time": item.get("uTime"),
            }
            results.append(result)
        return results, status

    def sprd_get_order(
        self,
        sprd_id: Any,
        order_id: Any = None,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get spread order details"""
        path, params, extra_data = self._sprd_get_order(
            sprd_id, order_id, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_order(
        self,
        sprd_id: Any,
        order_id: Any = None,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get spread order details"""
        path, params, extra_data = self._sprd_get_order(
            sprd_id, order_id, client_order_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _sprd_get_orders_pending(
        self,
        sprd_id: Any = None,
        inst_type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get pending spread orders
        :param sprd_id: Spread instrument ID
        :param inst_type: Instrument type
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_orders_pending"
        params = {}
        if sprd_id:
            params["sprdId"] = sprd_id
        if inst_type:
            params["instType"] = inst_type
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
                "symbol_name": sprd_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": SpreadTradingMixin._sprd_get_orders_pending_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_orders_pending_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize spread pending orders response"""
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"]
        results = []
        for item in data:
            result = {
                "sprd_id": item.get("sprdId"),
                "order_id": item.get("ordId"),
                "client_order_id": item.get("clOrdId"),
                "side": item.get("side"),
                "pos_side": item.get("posSide"),
                "size": item.get("sz"),
                "price": item.get("px"),
                "reduce_only": item.get("reduceOnly"),
                "state": item.get("state"),
                "avg_px": item.get("avgPx"),
                "filled_sz": item.get("fillSz"),
                "fee": item.get("fee"),
                "fee_ccy": item.get("feeCcy"),
                "source": item.get("source"),
                "create_time": item.get("cTime"),
                "update_time": item.get("uTime"),
            }
            results.append(result)
        return results, status

    def sprd_get_orders_pending(
        self,
        sprd_id: Any = None,
        inst_type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get pending spread orders"""
        path, params, extra_data = self._sprd_get_orders_pending(
            sprd_id, inst_type, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_orders_pending(
        self,
        sprd_id: Any = None,
        inst_type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get pending spread orders"""
        path, params, extra_data = self._sprd_get_orders_pending(
            sprd_id, inst_type, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _sprd_get_orders_history(
        self,
        sprd_id: Any = None,
        inst_type: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get spread order history
        :param sprd_id: Spread instrument ID
        :param inst_type: Instrument type
        :param state: Order state
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_orders_history"
        params = {}
        if sprd_id:
            params["sprdId"] = sprd_id
        if inst_type:
            params["instType"] = inst_type
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
                "symbol_name": sprd_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": SpreadTradingMixin._sprd_get_orders_history_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_orders_history_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize spread order history response"""
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"]
        results = []
        for item in data:
            result = {
                "sprd_id": item.get("sprdId"),
                "order_id": item.get("ordId"),
                "client_order_id": item.get("clOrdId"),
                "side": item.get("side"),
                "pos_side": item.get("posSide"),
                "size": item.get("sz"),
                "price": item.get("px"),
                "reduce_only": item.get("reduceOnly"),
                "state": item.get("state"),
                "avg_px": item.get("avgPx"),
                "filled_sz": item.get("fillSz"),
                "fee": item.get("fee"),
                "fee_ccy": item.get("feeCcy"),
                "source": item.get("source"),
                "acc_fill_sz_yday": item.get("accFillSzYday"),
                "create_time": item.get("cTime"),
                "update_time": item.get("uTime"),
            }
            results.append(result)
        return results, status

    def sprd_get_orders_history(
        self,
        sprd_id: Any = None,
        inst_type: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get spread order history"""
        path, params, extra_data = self._sprd_get_orders_history(
            sprd_id, inst_type, state, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_orders_history(
        self,
        sprd_id: Any = None,
        inst_type: Any = None,
        state: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get spread order history"""
        path, params, extra_data = self._sprd_get_orders_history(
            sprd_id, inst_type, state, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _sprd_get_trades(
        self,
        sprd_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get spread trade history
        :param sprd_id: Spread instrument ID
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "sprd_get_trades"
        params = {}
        if sprd_id:
            params["sprdId"] = sprd_id
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
                "symbol_name": sprd_id or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": SpreadTradingMixin._sprd_get_trades_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _sprd_get_trades_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize spread trades response"""
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"]
        results = []
        for item in data:
            result = {
                "sprd_id": item.get("sprdId"),
                "trade_id": item.get("tradeId"),
                "order_id": item.get("ordId"),
                "client_order_id": item.get("clOrdId"),
                "side": item.get("side"),
                "pos_side": item.get("posSide"),
                "size": item.get("sz"),
                "price": item.get("px"),
                "fee": item.get("fee"),
                "fee_ccy": item.get("feeCcy"),
                "source": item.get("source"),
                "timestamp": item.get("ts"),
            }
            results.append(result)
        return results, status

    def sprd_get_trades(
        self,
        sprd_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get spread trade history"""
        path, params, extra_data = self._sprd_get_trades(
            sprd_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_sprd_get_trades(
        self,
        sprd_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get spread trade history"""
        path, params, extra_data = self._sprd_get_trades(
            sprd_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
