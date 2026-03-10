"""
OKX API - AccountMixin
Auto-generated from request_base.py
"""

from typing import Any

from bt_api_py.containers.accounts.okx_account import OkxAccountData
from bt_api_py.containers.positions.okx_position import OkxPositionData
from bt_api_py.functions.utils import update_extra_data


class AccountMixin:
    """Mixin providing OKX API methods."""

    # ==================== Account APIs ====================

    def _get_account(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        """
        get account info using async
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        request_type = "get_account"
        path = self._params.get_rest_path(request_type)
        if symbol is None:
            params = {"ccy": ""}
            extra_data = update_extra_data(
                extra_data,
                **{
                    "request_type": request_type,
                    "symbol_name": "ALL",
                    "asset_type": self.asset_type,
                    "exchange_name": self.exchange_name,
                    "normalize_function": AccountMixin._get_account_normalize_function,
                },
            )
        else:
            params = {"ccy": symbol}
            extra_data = update_extra_data(
                extra_data,
                **{
                    "request_type": request_type,
                    "symbol_name": symbol,
                    "asset_type": self.asset_type,
                    "exchange_name": self.exchange_name,
                    "normalize_function": self._get_account_normalize_function,
                },
            )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_account_normalize_function(input_data: Any, extra_data: Any) -> None:
        status = input_data["code"] == "0"
        if "data" not in input_data or not input_data["data"]:
            return [], status
        data = input_data["data"][0]
        if len(data) > 0:
            data_list = [
                OkxAccountData(data, extra_data["symbol_name"], extra_data["asset_type"], True)
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_account(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def get_balance(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        return self.get_account(symbol, extra_data, **kwargs)

    def async_get_account(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        self.submit(self.async_request(path, extra_data=extra_data), callback=self.async_callback)

    def async_get_balance(self, extra_data: Any = None, **kwargs: Any) -> None:
        path = self._params.get_rest_path("get_balance_assert")
        self.submit(self.async_request(path, extra_data=extra_data), callback=self.async_callback)

    def async_sub_account(self, extra_data: Any = None) -> None:
        path = self._params.get_rest_path("sub_account")
        params = {"subAcct": "xxx"}
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Position APIs ====================

    def _get_position(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """
        get position info from okx by symbol
        :param symbol: default None, get all the currency, can be string, e.g. "BTC-USDT".
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: RequestData
        """
        _request_symbol = self._params.get_symbol(symbol)
        request_type = "get_position"
        path = self._params.get_rest_path(request_type)
        params = {"instType": "", "instId": symbol, "posId": ""}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._get_position_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_position_normalize_function(input_data: Any, extra_data: Any) -> None:
        status = input_data["code"] == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxPositionData(data[0], extra_data["symbol_name"], extra_data["asset_type"], True)
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_position(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_position(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> None:
        path, params, extra_data = self._get_position(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_positions_history(
        self,
        inst_type: Any = None,
        uly: Any = None,
        inst_id: Any = None,
        mgn_mode: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get positions history
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID
        :param mgn_mode: Margin mode, cross or isolated
        :param ccy: Currency
        :param after: Pagination (older data)
        :param before: Pagination (newer data)
        :param limit: Number of results, default 100, max 100
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_positions_history"
        params = {}
        if inst_type:
            params["instType"] = inst_type
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
        if mgn_mode:
            params["mgnMode"] = mgn_mode
        if ccy:
            params["ccy"] = ccy
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
                "symbol_name": inst_id or uly or "ALL",
                "asset_type": inst_type or self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._get_positions_history_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_positions_history_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize positions history data"""
        status = input_data["code"] == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxPositionData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_positions_history(
        self,
        inst_type: Any = None,
        uly: Any = None,
        inst_id: Any = None,
        mgn_mode: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get positions history"""
        path, params, extra_data = self._get_positions_history(
            inst_type, uly, inst_id, mgn_mode, ccy, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_positions_history(
        self,
        inst_type: Any = None,
        uly: Any = None,
        inst_id: Any = None,
        mgn_mode: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get positions history"""
        path, params, extra_data = self._get_positions_history(
            inst_type, uly, inst_id, mgn_mode, ccy, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Config APIs ====================

    def _get_config(self, extra_data: Any = None) -> None:
        params = {}
        path = self._params.get_rest_path("get_config")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_config",
                "symbol_name": "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._get_config_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _generic_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Generic normalize function for OKX API responses.
        Extracts 'data' list and checks 'code' for status."""
        status = input_data.get("code") == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if isinstance(data, list):
            return data, status
        return [data] if data else [], status

    @staticmethod
    def _get_config_normalize_function(input_data: Any, extra_data: Any) -> None:
        status = input_data["code"] == "0"
        if "data" not in input_data:
            return [], status
        if extra_data is None:
            pass
        data = input_data["data"]
        data = data if len(data) > 0 else []
        return data, status

    def get_config(self, extra_data: Any = None) -> None:
        path, params, extra_data = self._get_config(extra_data=extra_data)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_config(self, extra_data: Any = None) -> None:
        path, params, extra_data = self._get_config(extra_data=extra_data)
        self.submit(self.async_request(path, extra_data=extra_data), callback=self.async_callback)

    def set_mode(self) -> None:
        params = {"posMode": "long_short_mode"}
        path = self._params.get_rest_path("set_mode")
        data = self.request(path, body=params)
        return data

    def set_lever(self, symbol: Any, lever: Any = 10, mgn_mode: Any = "cross") -> None:
        symbol = self._params.get_symbol(symbol)
        params = {"instId": symbol, "lever": lever, "mgnMode": mgn_mode}
        path = self._params.get_rest_path("set_lever")
        data = self.request(path, body=params)
        return data

    def async_set_lever(
        self, symbol: Any, lever: Any = 10, mgn_mode: Any = "cross", extra_data: Any = None
    ) -> None:
        symbol = self._params.get_symbol(symbol)
        params = {"instId": symbol, "lever": lever, "mgnMode": mgn_mode}
        path = self._params.get_rest_path("set_lever")
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_fee(
        self,
        inst_type: Any,
        uly: Any = None,
        inst_id: Any = None,
        ccy: Any = None,
        qty: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get fee rate
        :param inst_type: Instrument type, e.g. SPOT, MARGIN, SWAP, FUTURES, OPTION
        :param uly: Underlying, e.g. BTC-USD
        :param inst_id: Instrument ID
        :param ccy: Currency
        :param qty: Order size
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_fee"
        params = {"instType": inst_type}
        if uly:
            params["uly"] = uly
        if inst_id:
            params["instId"] = inst_id
        if ccy:
            params["ccy"] = ccy
        if qty:
            params["qty"] = qty
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": inst_id or uly or "ALL",
                "asset_type": inst_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._get_fee_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_fee_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize fee data"""
        status = input_data["code"] == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxPositionData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_fee(
        self,
        inst_type: Any,
        uly: Any = None,
        inst_id: Any = None,
        ccy: Any = None,
        qty: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get fee rate"""
        path, params, extra_data = self._get_fee(
            inst_type, uly, inst_id, ccy, qty, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_fee(
        self,
        inst_type: Any,
        uly: Any = None,
        inst_id: Any = None,
        ccy: Any = None,
        qty: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get fee rate"""
        path, params, extra_data = self._get_fee(
            inst_type, uly, inst_id, ccy, qty, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_max_size(
        self,
        symbol: Any,
        td_mode: Any,
        ccy: Any = None,
        px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get maximum open position size
        :param symbol: Instrument ID, e.g. BTC-USDT
        :param td_mode: Trade mode, cross or isolated
        :param ccy: Currency (for isolated margin mode)
        :param px: Order price
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_max_size"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            "instId": request_symbol,
            "tdMode": td_mode,
        }
        if ccy:
            params["ccy"] = ccy
        if px:
            params["px"] = px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._get_max_size_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_max_size_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize max size data"""
        status = input_data["code"] == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxPositionData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_max_size(
        self,
        symbol: Any,
        td_mode: Any,
        ccy: Any = None,
        px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get maximum open position size"""
        path, params, extra_data = self._get_max_size(
            symbol, td_mode, ccy, px, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_max_size(
        self,
        symbol: Any,
        td_mode: Any,
        ccy: Any = None,
        px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get maximum open position size"""
        path, params, extra_data = self._get_max_size(
            symbol, td_mode, ccy, px, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_max_avail_size(
        self,
        symbol: Any,
        td_mode: Any,
        ccy: Any = None,
        px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Get maximum available open position size
        :param symbol: Instrument ID, e.g. BTC-USDT
        :param td_mode: Trade mode, cross or isolated
        :param ccy: Currency (for isolated margin mode)
        :param px: Order price
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, params, extra_data
        """
        request_type = "get_max_avail_size"
        request_symbol = self._params.get_symbol(symbol)
        params = {
            "instId": request_symbol,
            "tdMode": td_mode,
        }
        if ccy:
            params["ccy"] = ccy
        if px:
            params["px"] = px
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._get_max_avail_size_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_max_avail_size_normalize_function(input_data: Any, extra_data: Any) -> None:
        """Normalize max avail size data"""
        status = input_data["code"] == "0"
        if "data" not in input_data:
            return [], status
        data = input_data["data"]
        if len(data) > 0:
            data_list = [
                OkxPositionData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in data
            ]
            target_data = data_list
        else:
            target_data = []
        return target_data, status

    def get_max_avail_size(
        self,
        symbol: Any,
        td_mode: Any,
        ccy: Any = None,
        px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get maximum available open position size"""
        path, params, extra_data = self._get_max_avail_size(
            symbol, td_mode, ccy, px, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_max_avail_size(
        self,
        symbol: Any,
        td_mode: Any,
        ccy: Any = None,
        px: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get maximum available open position size"""
        path, params, extra_data = self._get_max_avail_size(
            symbol, td_mode, ccy, px, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _set_margin_balance(
        self,
        symbol: Any,
        pos_id: Any,
        amt: Any,
        mgn_mode: Any,
        action_type: Any = "add",
        pos_side: Any = "net",
        ccy: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Set margin balance (add/reduce margin)
        :param symbol: Instrument ID, e.g. BTC-USDT
        :param pos_id: Position ID
        :param amt: Amount to add/reduce
        :param mgn_mode: Margin mode, cross or isolated
        :param action_type: add or reduce, default is add
        :param pos_side: Position side, long/short/net, default is net
        :param ccy: Currency (for isolated margin mode)
        :param extra_data: extra_data, default is None, can be a dict passed by user
        :param kwargs: pass key-worded, variable-length arguments.
        :return: path, body, extra_data
        """
        request_type = "set_margin_balance"
        request_symbol = self._params.get_symbol(symbol)
        body = {
            "instId": request_symbol,
            "posSide": pos_side,
            "type": action_type,
            "posId": pos_id,
            "amt": str(amt),
            "mgnMode": mgn_mode,
        }
        if ccy:
            body["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": AccountMixin._generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, body, extra_data

    def set_margin_balance(
        self,
        symbol: Any,
        pos_id: Any,
        amt: Any,
        mgn_mode: Any,
        action_type: Any = "add",
        pos_side: Any = "net",
        ccy: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Set margin balance (add/reduce margin)"""
        path, body, extra_data = self._set_margin_balance(
            symbol, pos_id, amt, mgn_mode, action_type, pos_side, ccy, extra_data, **kwargs
        )
        data = self.request(path, body=body, extra_data=extra_data)
        return data

    def async_set_margin_balance(
        self,
        symbol: Any,
        pos_id: Any,
        amt: Any,
        mgn_mode: Any,
        action_type: Any = "add",
        pos_side: Any = "net",
        ccy: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async set margin balance (add/reduce margin)"""
        path, body, extra_data = self._set_margin_balance(
            symbol, pos_id, amt, mgn_mode, action_type, pos_side, ccy, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=body, extra_data=extra_data), callback=self.async_callback
        )
