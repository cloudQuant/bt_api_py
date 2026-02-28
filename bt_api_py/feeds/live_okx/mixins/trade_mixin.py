"""
OKX API - TradeMixin
Auto-generated from request_base.py
"""

from bt_api_py.containers.bars.okx_bar import OkxBarData
from bt_api_py.containers.orders.okx_order import OkxOrderData
from bt_api_py.containers.trades.okx_trade import OkxRequestTradeData
from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function
from bt_api_py.functions.utils import update_extra_data


class TradeMixin:
    """Mixin providing OKX API methods."""

    # ==================== Trade APIs ====================

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        try:
            vol = round(vol * self._params.symbol_leverage_dict[symbol])
        except Exception as e:
            self.request_logger.warn(f"_make_order:{e}")
        side, ord_type = order_type.split("-")
        if post_only:
            ord_type = "post_only"
        params = {
            "instId": request_symbol,
            "tdMode": "cross",
            "ccy": "USDT",
            "clOrdId": client_order_id,
            "side": side,
            "ordType": ord_type,
            "px": str(price),
            "sz": str(vol),
        }
        path = self._params.get_rest_path(request_type)
        path = path.replace("<instrument_id>", request_symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "offset": offset,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._make_order_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = [
            {
                "client_order_id": i["clOrdId"],
                "order_id": i["ordId"],
                "tag": i["tag"],
                "s_code": i["sCode"],
                "s_msg": i["sMsg"],
                "in_server_time": input_data.get("inTime"),
                "out_server_time": input_data.get("outTime"),
            }
            for i in input_data["data"]
        ]
        return data, status

    # noinspection PyBroadException
    def make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra_data = self._make_order(
            symbol, vol, price, order_type, offset, post_only, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    # noinspection PyBroadException
    def async_make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra_data = self._make_order(
            symbol, vol, price, order_type, offset, post_only, client_order_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _amend_order(
        self,
        symbol,
        order_id=None,
        client_order_id=None,
        new_sz=None,
        new_px=None,
        extra_data=None,
        **kwargs,
    ):
        """Amend an incomplete order"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "amend_order"
        params = {
            "instId": request_symbol,
        }
        if order_id:
            params["ordId"] = order_id
        if client_order_id:
            params["clOrdId"] = client_order_id
        if new_sz is not None:
            params["newSz"] = str(new_sz)
        if new_px is not None:
            params["newPx"] = str(new_px)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._amend_order_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _amend_order_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = [
            {
                "client_order_id": i.get("clOrdId", ""),
                "order_id": i.get("ordId", ""),
                "req_id": i.get("reqId", ""),
                "s_code": i.get("sCode", ""),
                "s_msg": i.get("sMsg", ""),
            }
            for i in input_data["data"]
        ]
        return data, status

    def amend_order(
        self,
        symbol,
        order_id=None,
        client_order_id=None,
        new_sz=None,
        new_px=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra_data = self._amend_order(
            symbol, order_id, client_order_id, new_sz, new_px, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_amend_order(
        self,
        symbol,
        order_id=None,
        client_order_id=None,
        new_sz=None,
        new_px=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra_data = self._amend_order(
            symbol, order_id, client_order_id, new_sz, new_px, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        params = {"instId": request_symbol}
        if order_id:
            params["ordId"] = order_id
        if "client_order_id" in kwargs:
            params["clOrdId"] = kwargs["client_order_id"]
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._cancel_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        if extra_data:
            pass
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                {
                    "client_order_id": i["clOrdId"],
                    "order_id": i["ordId"],
                    "s_code": i["sCode"],
                    "s_msg": i["sMsg"],
                }
                for i in data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # noinspection PyBroadException
    def _query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        request_symbol = self._params.get_symbol(symbol)
        request_type = "query_order"
        path = self._params.get_rest_path(request_type)
        params = {}
        if order_id is not None:
            params["ordId"] = order_id
        if "client_order_id" in kwargs:
            params["clOrdId"] = kwargs["client_order_id"]
        params["instId"] = request_symbol
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._query_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _query_order_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxOrderData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in data
            ]
            data = data_list
        else:
            data = []
        return data, status

    # noinspection PyBroadException
    def query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_query_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
        else:
            request_symbol = ""
        request_type = "get_open_orders"
        uly = kwargs.get("uly", "")
        inst_type = kwargs.get("instType", "")
        ord_type = kwargs.get("ordType", "")
        state = kwargs.get("state", "")
        after = kwargs.get("after", "")
        before = kwargs.get("before", "")
        limit = kwargs.get("limit", "")
        inst_family = kwargs.get("instFamily", "")
        params = {
            "instType": inst_type,
            "uly": uly,
            "instId": request_symbol,
            "ordType": ord_type,
            "state": state,
            "after": after,
            "before": before,
            "limit": limit,
            "instFamily": inst_family,
        }

        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._get_open_orders_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_open_orders_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if isinstance(data, list):
            data_list = [
                OkxOrderData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in data
            ]
            target_data = data_list
        elif isinstance(data, dict):
            data_list = [
                OkxOrderData(data, extra_data["symbol_name"], extra_data["asset_type"], True)
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    # noinspection PyBroadException
    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # noinspection PyBroadException
    def async_get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_order_history(
        self,
        inst_type="SWAP",
        symbol=None,
        ord_type=None,
        state=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get order history (last 7 days)"""
        request_type = "get_order_history"
        params = {"instType": inst_type}
        if symbol:
            params["instId"] = self._params.get_symbol(symbol)
        if ord_type:
            params["ordType"] = ord_type
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
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._get_open_orders_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_order_history(
        self,
        inst_type="SWAP",
        symbol=None,
        ord_type=None,
        state=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        path, params, extra_data = self._get_order_history(
            inst_type, symbol, ord_type, state, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def _get_deals(
        self, symbol=None, count=100, start_time="", end_time="", extra_data=None, **kwargs
    ):
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
        else:
            request_symbol = ""
            symbol = ""
        request_type = "get_deals"
        params = {
            "instType": self.asset_type,
            "instId": request_symbol,
            "limit": str(count),
            "uly": kwargs.get("underlying", ""),
            "ordId": kwargs.get("ordId", ""),
            "instFamily": kwargs.get("instFamily", ""),
            "before": "",
            "after": "",
            "start": start_time,
            "end": end_time,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": TradeMixin._get_deals_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_deals_normalize_function(input_data, extra_data):
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxRequestTradeData(
                    data[0], extra_data["symbol_name"], extra_data["asset_type"], True
                )
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    # noinspection PyBroadException
    def get_deals(
        self, symbol=None, count=100, start_time="", end_time="", extra_data=None, **kwargs
    ):
        path, params, extra_data = self._get_deals(
            symbol, count, start_time, end_time, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deals(
        self, symbol=None, count=100, start_time="", end_time="", extra_data=None, **kwargs
    ):
        path, params, extra_data = self._get_deals(
            symbol, count, start_time, end_time, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Algo Trading APIs ====================

    def _make_algo_order(self, symbol, side, ord_type, sz, extra_data=None, **kwargs):
        """Place algo order (trigger, conditional, oco, iceberg, twap, trailing)"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_algo_order"
        params = {
            "instId": request_symbol,
            "tdMode": kwargs.get("tdMode", "cross"),
            "side": side,
            "ordType": ord_type,
            "sz": str(sz),
        }
        # Add optional algo-specific parameters
        for key in [
            "triggerPx",
            "orderPx",
            "triggerPxType",
            "tpTriggerPx",
            "tpOrdPx",
            "slTriggerPx",
            "slOrdPx",
            "tpTriggerPxType",
            "slTriggerPxType",
            "ccy",
            "posSide",
            "clOrdId",
            "reduceOnly",
            "tgtCcy",
        ]:
            if key in kwargs:
                params[key] = str(kwargs[key])
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._make_order_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update({k: v for k, v in kwargs.items() if k not in params})
        return path, params, extra_data

    def make_algo_order(self, symbol, side, ord_type, sz, extra_data=None, **kwargs):
        path, params, extra_data = self._make_algo_order(
            symbol, side, ord_type, sz, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def cancel_algo_order(self, algo_id, inst_id, extra_data=None):
        """Cancel algo order"""
        path = self._params.get_rest_path("cancel_algo_order")
        params = [{"algoId": algo_id, "instId": inst_id}]
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "cancel_algo_order",
                "symbol_name": inst_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_get_clear_price(self, symbol, extra_data=None, **kwargs):
        data_type = "get_clear_price"
        path = self._params.get_rest_path(data_type)
        params = {"instId": self._params.get_symbol(symbol)}
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Option Instrument Family Trades ====================

    def _get_option_instrument_family_trades(
        self, inst_family, uly=None, limit=None, extra_data=None, **kwargs
    ):
        """
        Get option instrument family trades data
        :param inst_family: Instrument family, e.g. `BTC-USD`
        :param uly: Underlying index
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_instrument_family_trades"
        params = {"instFamily": inst_family}
        if uly:
            params["uly"] = uly
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_family,
                "asset_type": "OPTION",
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_option_instrument_family_trades(
        self, inst_family, uly=None, limit=None, extra_data=None, **kwargs
    ):
        """Get option instrument family trades data"""
        path, params, extra_data = self._get_option_instrument_family_trades(
            inst_family, uly, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_instrument_family_trades(
        self, inst_family, uly=None, limit=None, extra_data=None, **kwargs
    ):
        """Async get option instrument family trades data"""
        path, params, extra_data = self._get_option_instrument_family_trades(
            inst_family, uly, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Option Trades ====================

    def _get_option_trades(self, inst_id, limit=None, extra_data=None, **kwargs):
        """
        Get option trades data
        :param inst_id: Instrument ID, e.g. `BTC-USD-231229-40000-C`
        :param limit: Default 100, max 500
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_option_trades"
        params = {"instId": inst_id}
        if limit:
            params["limit"] = limit
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id,
                "asset_type": "OPTION",
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_option_trades(self, inst_id, limit=None, extra_data=None, **kwargs):
        """Get option trades data"""
        path, params, extra_data = self._get_option_trades(inst_id, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_option_trades(self, inst_id, limit=None, extra_data=None, **kwargs):
        """Async get option trades data"""
        path, params, extra_data = self._get_option_trades(inst_id, limit, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== 24h Volume ====================

    def _get_24h_volume(self, extra_data=None, **kwargs):
        """
        Get platform 24h total volume
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_24h_volume"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_24h_volume(self, extra_data=None, **kwargs):
        """Get platform 24h total volume"""
        path, params, extra_data = self._get_24h_volume(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_24h_volume(self, extra_data=None, **kwargs):
        """Async get platform 24h total volume"""
        path, params, extra_data = self._get_24h_volume(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Call Auction Details ====================

    def _get_call_auction_details(
        self, inst_type=None, uly=None, inst_id=None, extra_data=None, **kwargs
    ):
        """
        Get call auction details
        :param inst_type: Instrument type: `FUTURES`, `OPTION`
        :param uly: Underlying, required for `FUTURES`/`OPTION`
        :param inst_id: Instrument ID
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_call_auction_details"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
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

    def get_call_auction_details(
        self, inst_type=None, uly=None, inst_id=None, extra_data=None, **kwargs
    ):
        """Get call auction details"""
        path, params, extra_data = self._get_call_auction_details(
            inst_type, uly, inst_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_call_auction_details(
        self, inst_type=None, uly=None, inst_id=None, extra_data=None, **kwargs
    ):
        """Async get call auction details"""
        path, params, extra_data = self._get_call_auction_details(
            inst_type, uly, inst_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Index Price ====================

    def _get_index_price(self, index=None, extra_data=None, **kwargs):
        """
        Get index ticker data
        :param index: Index, e.g. `BTC-USD`
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_price"
        params = {}
        if index:
            params["instId"] = index
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": index or "ALL",
                "asset_type": "INDEX",
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_index_price(self, index=None, extra_data=None, **kwargs):
        """Get index ticker data"""
        path, params, extra_data = self._get_index_price(index, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_price(self, index=None, extra_data=None, **kwargs):
        """Async get index ticker data"""
        path, params, extra_data = self._get_index_price(index, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Index Candles ====================

    def _get_index_candles(
        self, index, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """
        Get index candlestick charts
        :param index: Index, e.g. `BTC-USD`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_candles"
        params = {
            "instId": index,
            "bar": self._params.get_period(bar),
        }
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
                "symbol_name": index,
                "asset_type": "INDEX",
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._get_index_candles_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_index_candles_normalize_function(input_data, extra_data):
        """Normalize index candles data - API returns 6 elements, OkxBarData expects 9"""
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = sorted(input_data["data"], key=lambda x: x[0])
        if len(data) > 0:
            # Pad data from 6-7 elements to 9 elements for OkxBarData
            # API format: [ts, o, h, l, c, vol] or [ts, o, h, l, c, vol, vol_ccy]
            # OkxBarData expects: [ts, o, h, l, c, vol, base_vol, quote_vol, confirm]
            padded_data = []
            for row in data:
                padded = list(row)
                # Ensure we have at least 6 elements
                while len(padded) < 6:
                    padded.append("0")
                # Add missing elements to reach 9
                # Index 6: base_asset_volume (use vol if not present)
                if len(padded) < 7:
                    padded.append(padded[5])  # vol as base_vol
                else:
                    padded.append(padded[5])  # vol as base_vol
                # Index 7: quote_asset_volume (use vol_ccy or '0')
                if len(padded) < 8:
                    padded.append("0")  # no quote_vol for index
                # Index 8: confirm status
                if len(padded) < 9:
                    padded.append("1")
                padded_data.append(padded)

            data_list = [
                OkxBarData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in padded_data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_index_candles(
        self, index, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Get index candlestick charts"""
        path, params, extra_data = self._get_index_candles(
            index, bar, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_candles(
        self, index, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Async get index candlestick charts"""
        path, params, extra_data = self._get_index_candles(
            index, bar, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Mark Price Candles ====================

    def _get_mark_price_candles(
        self, symbol, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """
        Get mark price candlestick charts
        :param symbol: Instrument ID, e.g. `BTC-USDT`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_mark_price_candles"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            "instId": request_symbol,
            "bar": self._params.get_period(bar),
        }
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
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": TradeMixin._get_mark_price_candles_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_mark_price_candles_normalize_function(input_data, extra_data):
        """Normalize mark price candles data - API returns 6-7 elements, OkxBarData expects 9"""
        status = True if input_data["code"] == "0" else False
        if "data" not in input_data:
            return [], status
        data = sorted(input_data["data"], key=lambda x: x[0])
        if len(data) > 0:
            # Pad data from 6-7 elements to 9 elements for OkxBarData
            # API format: [ts, o, h, l, c, vol] or [ts, o, h, l, c, vol, vol_ccy]
            # OkxBarData expects: [ts, o, h, l, c, vol, base_vol, quote_vol, confirm]
            padded_data = []
            for row in data:
                padded = list(row)
                # Ensure we have at least 6 elements
                while len(padded) < 6:
                    padded.append("0")
                # Add missing elements to reach 9
                # Index 6: base_asset_volume (use vol if not present)
                if len(padded) < 7:
                    padded.append(padded[5])  # vol as base_vol
                else:
                    padded.append(padded[5])  # vol as base_vol
                # Index 7: quote_asset_volume (use vol_ccy or '0')
                if len(padded) < 8:
                    padded.append("0")  # no quote_vol
                # Index 8: confirm status
                if len(padded) < 9:
                    padded.append("1")
                padded_data.append(padded)

            data_list = [
                OkxBarData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in padded_data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_mark_price_candles(
        self, symbol, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Get mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles(
            symbol, bar, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_mark_price_candles(
        self, symbol, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Async get mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles(
            symbol, bar, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Index Candles History ====================

    def _get_index_candles_history(
        self, index, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """
        Get historical index candlestick charts
        :param index: Index, e.g. `BTC-USD`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_index_candles_history"
        params = {
            "instId": index,
            "bar": self._params.get_period(bar),
        }
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
                "symbol_name": index,
                "asset_type": "INDEX",
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_index_candles_history(
        self, index, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Get historical index candlestick charts"""
        path, params, extra_data = self._get_index_candles_history(
            index, bar, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_index_candles_history(
        self, index, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Async get historical index candlestick charts"""
        path, params, extra_data = self._get_index_candles_history(
            index, bar, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Mark Price Candles History ====================

    def _get_mark_price_candles_history(
        self, symbol, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """
        Get historical mark price candlestick charts
        :param symbol: Instrument ID, e.g. `BTC-USD-SWAP`
        :param bar: Bar size, default `1m`. Options: `1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M`
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_mark_price_candles_history"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            "instId": request_symbol,
            "bar": self._params.get_period(bar),
        }
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
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_mark_price_candles_history(
        self, symbol, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Get historical mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles_history(
            symbol, bar, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_mark_price_candles_history(
        self, symbol, bar="1m", after="", before="", limit="100", extra_data=None, **kwargs
    ):
        """Async get historical mark price candlestick charts"""
        path, params, extra_data = self._get_mark_price_candles_history(
            symbol, bar, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Missing Trade APIs ====================

    def _make_orders(self, orders_data, extra_data=None, **kwargs):
        """Make multiple orders (batch)"""
        request_type = "make_orders"
        params = orders_data
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "BATCH",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def make_orders(self, orders_data, extra_data=None, **kwargs):
        """Make multiple orders (batch)"""
        path, params, extra_data = self._make_orders(orders_data, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_make_orders(self, orders_data, extra_data=None, **kwargs):
        """Async make multiple orders (batch)"""
        path, params, extra_data = self._make_orders(orders_data, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_orders(self, orders_data, extra_data=None, **kwargs):
        """Cancel multiple orders (batch)"""
        request_type = "cancel_orders"
        params = orders_data
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "BATCH",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_orders(self, orders_data, extra_data=None, **kwargs):
        """Cancel multiple orders (batch)"""
        path, params, extra_data = self._cancel_orders(orders_data, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_orders(self, orders_data, extra_data=None, **kwargs):
        """Async cancel multiple orders (batch)"""
        path, params, extra_data = self._cancel_orders(orders_data, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _amend_orders(self, orders_data, extra_data=None, **kwargs):
        """Amend multiple orders (batch)"""
        request_type = "amend_orders"
        params = orders_data
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "BATCH",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def amend_orders(self, orders_data, extra_data=None, **kwargs):
        """Amend multiple orders (batch)"""
        path, params, extra_data = self._amend_orders(orders_data, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_amend_orders(self, orders_data, extra_data=None, **kwargs):
        """Async amend multiple orders (batch)"""
        path, params, extra_data = self._amend_orders(orders_data, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_fills(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        order_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get fills"""
        request_type = "get_fills"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
        if order_id:
            params["ordId"] = order_id
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
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_fills(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        order_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get fills"""
        path, params, extra_data = self._get_fills(
            inst_type, uly, inst_id, order_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_fills(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        order_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Async get fills"""
        path, params, extra_data = self._get_fills(
            inst_type, uly, inst_id, order_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _close_position(
        self,
        symbol,
        pos_side=None,
        mgn_mode=None,
        ccy=None,
        auto_cxl=False,
        extra_data=None,
        **kwargs,
    ):
        """Close position"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "close_position"
        params = {
            "instId": request_symbol,
        }
        if pos_side:
            params["posSide"] = pos_side
        if mgn_mode:
            params["mgnMode"] = mgn_mode
        if ccy:
            params["ccy"] = ccy
        if auto_cxl:
            params["autoCxl"] = True
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def close_position(
        self,
        symbol,
        pos_side=None,
        mgn_mode=None,
        ccy=None,
        auto_cxl=False,
        extra_data=None,
        **kwargs,
    ):
        """Close position"""
        path, params, extra_data = self._close_position(
            symbol, pos_side, mgn_mode, ccy, auto_cxl, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_close_position(
        self,
        symbol,
        pos_side=None,
        mgn_mode=None,
        ccy=None,
        auto_cxl=False,
        extra_data=None,
        **kwargs,
    ):
        """Async close position"""
        path, params, extra_data = self._close_position(
            symbol, pos_side, mgn_mode, ccy, auto_cxl, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_fills_history(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        order_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get fills history"""
        request_type = "get_fills_history"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
        if order_id:
            params["ordId"] = order_id
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
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_fills_history(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        order_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get fills history"""
        path, params, extra_data = self._get_fills_history(
            inst_type, uly, inst_id, order_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_fills_history(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        order_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Async get fills history"""
        path, params, extra_data = self._get_fills_history(
            inst_type, uly, inst_id, order_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_order_history_archive(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get order history archive"""
        request_type = "get_order_history_archive"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
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
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_order_history_archive(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get order history archive"""
        path, params, extra_data = self._get_order_history_archive(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_order_history_archive(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Async get order history archive"""
        path, params, extra_data = self._get_order_history_archive(
            inst_type, uly, inst_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_all_after(self, time_slug, extra_data=None, **kwargs):
        """Cancel all orders after time"""
        request_type = "cancel_all_after"
        params = {"timeSlug": time_slug}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_all_after(self, time_slug, extra_data=None, **kwargs):
        """Cancel all orders after time"""
        path, params, extra_data = self._cancel_all_after(time_slug, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all_after(self, time_slug, extra_data=None, **kwargs):
        """Async cancel all orders after time"""
        path, params, extra_data = self._cancel_all_after(time_slug, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _amend_algo_order(
        self,
        algo_id,
        inst_id,
        ccy=None,
        amend_px_on_trigger_type=None,
        new_sz=None,
        new_px=None,
        new_tp_trigger_px=None,
        new_tp_ord_px=None,
        new_sl_trigger_px=None,
        new_sl_ord_px=None,
        trigger_px=None,
        order_type=None,
        extra_data=None,
        **kwargs,
    ):
        """Amend algo order"""
        request_symbol = self._params.get_symbol(inst_id)
        request_type = "amend_algo_order"
        params = {
            "algoId": algo_id,
            "instId": request_symbol,
        }
        if ccy:
            params["ccy"] = ccy
        if amend_px_on_trigger_type:
            params["amendPxOnTriggerType"] = amend_px_on_trigger_type
        if new_sz is not None:
            params["newSz"] = str(new_sz)
        if new_px is not None:
            params["newPx"] = str(new_px)
        if new_tp_trigger_px is not None:
            params["newTpTriggerPx"] = str(new_tp_trigger_px)
        if new_tp_ord_px is not None:
            params["newTpOrdPx"] = str(new_tp_ord_px)
        if new_sl_trigger_px is not None:
            params["newSlTriggerPx"] = str(new_sl_trigger_px)
        if new_sl_ord_px is not None:
            params["newSlOrdPx"] = str(new_sl_ord_px)
        if trigger_px is not None:
            params["triggerPx"] = str(trigger_px)
        if order_type:
            params["algoOrdType"] = order_type
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

    def amend_algo_order(
        self,
        algo_id,
        inst_id,
        ccy=None,
        amend_px_on_trigger_type=None,
        new_sz=None,
        new_px=None,
        new_tp_trigger_px=None,
        new_tp_ord_px=None,
        new_sl_trigger_px=None,
        new_sl_ord_px=None,
        trigger_px=None,
        order_type=None,
        extra_data=None,
        **kwargs,
    ):
        """Amend algo order"""
        path, params, extra_data = self._amend_algo_order(
            algo_id,
            inst_id,
            ccy,
            amend_px_on_trigger_type,
            new_sz,
            new_px,
            new_tp_trigger_px,
            new_tp_ord_px,
            new_sl_trigger_px,
            new_sl_ord_px,
            trigger_px,
            order_type,
            extra_data,
            **kwargs,
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_amend_algo_order(
        self,
        algo_id,
        inst_id,
        ccy=None,
        amend_px_on_trigger_type=None,
        new_sz=None,
        new_px=None,
        new_tp_trigger_px=None,
        new_tp_ord_px=None,
        new_sl_trigger_px=None,
        new_sl_ord_px=None,
        trigger_px=None,
        order_type=None,
        extra_data=None,
        **kwargs,
    ):
        """Async amend algo order"""
        path, params, extra_data = self._amend_algo_order(
            algo_id,
            inst_id,
            ccy,
            amend_px_on_trigger_type,
            new_sz,
            new_px,
            new_tp_trigger_px,
            new_tp_ord_px,
            new_sl_trigger_px,
            new_sl_ord_px,
            trigger_px,
            order_type,
            extra_data,
            **kwargs,
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_algo_orders_pending(
        self, inst_type=None, uly=None, inst_id=None, algo_id=None, extra_data=None, **kwargs
    ):
        """Get pending algo orders"""
        request_type = "get_algo_orders_pending"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params["instId"] = request_symbol
        if algo_id:
            params["algoId"] = algo_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_algo_orders_pending(
        self, inst_type=None, uly=None, inst_id=None, algo_id=None, extra_data=None, **kwargs
    ):
        """Get pending algo orders"""
        path, params, extra_data = self._get_algo_orders_pending(
            inst_type, uly, inst_id, algo_id, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_algo_orders_pending(
        self, inst_type=None, uly=None, inst_id=None, algo_id=None, extra_data=None, **kwargs
    ):
        """Async get pending algo orders"""
        path, params, extra_data = self._get_algo_orders_pending(
            inst_type, uly, inst_id, algo_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_algo_order_history(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        algo_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get algo order history"""
        request_type = "get_algo_order_history"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params["instId"] = request_symbol
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
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_algo_order_history(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        algo_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Get algo order history"""
        path, params, extra_data = self._get_algo_order_history(
            inst_type, uly, inst_id, algo_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_algo_order_history(
        self,
        inst_type=None,
        uly=None,
        inst_id=None,
        algo_id=None,
        after=None,
        before=None,
        limit=None,
        extra_data=None,
        **kwargs,
    ):
        """Async get algo order history"""
        path, params, extra_data = self._get_algo_order_history(
            inst_type, uly, inst_id, algo_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_algo_order(self, algo_id=None, symbol=None, inst_type=None, extra_data=None, **kwargs):
        """Get algo order details"""
        request_type = "get_algo_order"
        params = {}
        if algo_id:
            params["algoId"] = algo_id
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            params["instId"] = request_symbol
        if inst_type:
            params["instType"] = inst_type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_algo_order(self, algo_id=None, symbol=None, inst_type=None, extra_data=None, **kwargs):
        """Get algo order details"""
        path, params, extra_data = self._get_algo_order(
            algo_id, symbol, inst_type, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_algo_order(
        self, algo_id=None, symbol=None, inst_type=None, extra_data=None, **kwargs
    ):
        """Async get algo order details"""
        path, params, extra_data = self._get_algo_order(
            algo_id, symbol, inst_type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_all(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Cancel all orders"""
        request_type = "cancel_all"
        params = {"instType": inst_type}
        if uly:
            params["uly"] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params["instId"] = request_symbol
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": inst_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_all(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Cancel all orders"""
        path, params, extra_data = self._cancel_all(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_all(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async cancel all orders"""
        path, params, extra_data = self._cancel_all(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_account_rate_limit(self, extra_data=None, **kwargs):
        """Get account rate limit"""
        request_type = "get_account_rate_limit"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_account_rate_limit(self, extra_data=None, **kwargs):
        """Get account rate limit"""
        path, params, extra_data = self._get_account_rate_limit(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_account_rate_limit(self, extra_data=None, **kwargs):
        """Async get account rate limit"""
        path, params, extra_data = self._get_account_rate_limit(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_easy_convert_currency_list(self, extra_data=None, **kwargs):
        """Get easy convert currency list"""
        request_type = "get_easy_convert_currency_list"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_easy_convert_currency_list(self, extra_data=None, **kwargs):
        """Get easy convert currency list"""
        path, params, extra_data = self._get_easy_convert_currency_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_easy_convert_currency_list(self, extra_data=None, **kwargs):
        """Async get easy convert currency list"""
        path, params, extra_data = self._get_easy_convert_currency_list(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _easy_convert(self, from_ccy, to_ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """Easy convert"""
        request_type = "easy_convert"
        params = {
            "fromCcy": from_ccy,
            "toCcy": to_ccy,
            "amt": str(amt),
        }
        if client_order_id:
            params["clientOrderId"] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": from_ccy,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def easy_convert(self, from_ccy, to_ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """Easy convert"""
        path, params, extra_data = self._easy_convert(
            from_ccy, to_ccy, amt, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_easy_convert(
        self, from_ccy, to_ccy, amt, client_order_id=None, extra_data=None, **kwargs
    ):
        """Async easy convert"""
        path, params, extra_data = self._easy_convert(
            from_ccy, to_ccy, amt, client_order_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_easy_convert_history(
        self, after=None, before=None, limit=None, extra_data=None, **kwargs
    ):
        """Get easy convert history"""
        request_type = "get_easy_convert_history"
        params = {}
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
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_easy_convert_history(
        self, after=None, before=None, limit=None, extra_data=None, **kwargs
    ):
        """Get easy convert history"""
        path, params, extra_data = self._get_easy_convert_history(
            after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_easy_convert_history(
        self, after=None, before=None, limit=None, extra_data=None, **kwargs
    ):
        """Async get easy convert history"""
        path, params, extra_data = self._get_easy_convert_history(
            after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_one_click_repay_currency_list(self, extra_data=None, **kwargs):
        """Get one click repay currency list"""
        request_type = "get_one_click_repay_currency_list"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_one_click_repay_currency_list(self, extra_data=None, **kwargs):
        """Get one click repay currency list"""
        path, params, extra_data = self._get_one_click_repay_currency_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_one_click_repay_currency_list(self, extra_data=None, **kwargs):
        """Async get one click repay currency list"""
        path, params, extra_data = self._get_one_click_repay_currency_list(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _one_click_repay(self, ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """One click repay"""
        request_type = "one_click_repay"
        params = {
            "ccy": ccy,
            "amt": str(amt),
        }
        if client_order_id:
            params["clientOrderId"] = client_order_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def one_click_repay(self, ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """One click repay"""
        path, params, extra_data = self._one_click_repay(
            ccy, amt, client_order_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_one_click_repay(self, ccy, amt, client_order_id=None, extra_data=None, **kwargs):
        """Async one click repay"""
        path, params, extra_data = self._one_click_repay(
            ccy, amt, client_order_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_one_click_repay_history(
        self, after=None, before=None, limit=None, extra_data=None, **kwargs
    ):
        """Get one click repay history"""
        request_type = "get_one_click_repay_history"
        params = {}
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
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_one_click_repay_history(
        self, after=None, before=None, limit=None, extra_data=None, **kwargs
    ):
        """Get one click repay history"""
        path, params, extra_data = self._get_one_click_repay_history(
            after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_one_click_repay_history(
        self, after=None, before=None, limit=None, extra_data=None, **kwargs
    ):
        """Async get one click repay history"""
        path, params, extra_data = self._get_one_click_repay_history(
            after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _mass_cancel(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Mass cancel orders"""
        request_type = "mass_cancel"
        params = {"instType": inst_type}
        if uly:
            params["uly"] = uly
        if inst_id:
            request_symbol = self._params.get_symbol(inst_id)
            params["instId"] = request_symbol
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or "ALL",
                "asset_type": inst_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def mass_cancel(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Mass cancel orders"""
        path, params, extra_data = self._mass_cancel(inst_type, uly, inst_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_mass_cancel(self, inst_type, uly=None, inst_id=None, extra_data=None, **kwargs):
        """Async mass cancel orders"""
        path, params, extra_data = self._mass_cancel(inst_type, uly, inst_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _order_precheck(
        self,
        symbol,
        td_mode,
        ccy,
        side,
        order_type=None,
        sz=None,
        px=None,
        extra_data=None,
        **kwargs,
    ):
        """Order precheck"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "order_precheck"
        params = {
            "instId": request_symbol,
            "tdMode": td_mode,
            "ccy": ccy,
            "side": side,
        }
        if order_type:
            params["ordType"] = order_type
        if sz is not None:
            params["sz"] = str(sz)
        if px is not None:
            params["px"] = str(px)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def order_precheck(
        self,
        symbol,
        td_mode,
        ccy,
        side,
        order_type=None,
        sz=None,
        px=None,
        extra_data=None,
        **kwargs,
    ):
        """Order precheck"""
        path, params, extra_data = self._order_precheck(
            symbol, td_mode, ccy, side, order_type, sz, px, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_order_precheck(
        self,
        symbol,
        td_mode,
        ccy,
        side,
        order_type=None,
        sz=None,
        px=None,
        extra_data=None,
        **kwargs,
    ):
        """Async order precheck"""
        path, params, extra_data = self._order_precheck(
            symbol, td_mode, ccy, side, order_type, sz, px, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )
