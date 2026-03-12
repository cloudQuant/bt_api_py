"""
OKX API - FundingMixin
Auto-generated from request_base.py
"""

from collections.abc import Callable
from typing import Any

from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function
from bt_api_py.functions.utils import update_extra_data


class FundingMixin:
    """Mixin providing OKX API methods."""

    _params: Any
    asset_type: str
    exchange_name: str
    request: Callable[..., Any]
    submit: Callable[..., Any]
    async_request: Callable[..., Any]
    async_callback: Callable[..., Any]

    # ==================== Missing Funding Account APIs ====================

    def _get_currencies(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get currencies"""
        request_type = "get_currencies"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_currencies(self, ccy: Any = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get currencies"""
        path, params, extra_data = self._get_currencies(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_currencies(self, ccy: Any = None, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get currencies"""
        path, params, extra_data = self._get_currencies(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_asset_balances(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get asset balances"""
        request_type = "get_asset_balances"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_balances(self, ccy: Any = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get asset balances"""
        path, params, extra_data = self._get_asset_balances(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_balances(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get asset balances"""
        path, params, extra_data = self._get_asset_balances(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_non_tradable_assets(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get non-tradable assets"""
        request_type = "get_non_tradable_assets"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_non_tradable_assets(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get non-tradable assets"""
        path, params, extra_data = self._get_non_tradable_assets(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_non_tradable_assets(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get non-tradable assets"""
        path, params, extra_data = self._get_non_tradable_assets(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_asset_valuation(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get asset valuation"""
        request_type = "get_asset_valuation"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_valuation(self, ccy: Any = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get asset valuation"""
        path, params, extra_data = self._get_asset_valuation(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_valuation(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get asset valuation"""
        path, params, extra_data = self._get_asset_valuation(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _transfer(
        self,
        ccy: Any,
        amt: Any,
        from_acct: Any = None,
        to_acct: Any = None,
        type: Any = None,
        client_bill_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Asset transfer"""
        request_type = "transfer"
        params = {
            "ccy": ccy,
            "amt": str(amt),
        }
        if from_acct:
            params["from"] = from_acct
        if to_acct:
            params["to"] = to_acct
        if type:
            params["type"] = type
        if client_bill_id:
            params["clientBillId"] = client_bill_id
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

    def transfer(
        self,
        ccy: Any,
        amt: Any,
        from_acct: Any = None,
        to_acct: Any = None,
        type: Any = None,
        client_bill_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Asset transfer"""
        path, params, extra_data = self._transfer(
            ccy, amt, from_acct, to_acct, type, client_bill_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_transfer(
        self,
        ccy: Any,
        amt: Any,
        from_acct: Any = None,
        to_acct: Any = None,
        type: Any = None,
        client_bill_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async asset transfer"""
        path, params, extra_data = self._transfer(
            ccy, amt, from_acct, to_acct, type, client_bill_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_transfer_state(
        self,
        transfer_id: Any = None,
        client_bill_id: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get transfer state"""
        request_type = "get_transfer_state"
        params: dict[str, Any] = {}
        if transfer_id:
            params["transId"] = transfer_id
        if client_bill_id:
            params["clientBillId"] = client_bill_id
        if type:
            params["type"] = type
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

    def get_transfer_state(
        self,
        transfer_id: Any = None,
        client_bill_id: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get transfer state"""
        path, params, extra_data = self._get_transfer_state(
            transfer_id, client_bill_id, type, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_transfer_state(
        self,
        transfer_id: Any = None,
        client_bill_id: Any = None,
        type: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get transfer state"""
        path, params, extra_data = self._get_transfer_state(
            transfer_id, client_bill_id, type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_asset_bills(
        self,
        ccy: Any = None,
        type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get asset bills"""
        request_type = "get_asset_bills"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        if type:
            params["type"] = type
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_bills(
        self,
        ccy: Any = None,
        type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get asset bills"""
        path, params, extra_data = self._get_asset_bills(
            ccy, type, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_bills(
        self,
        ccy: Any = None,
        type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get asset bills"""
        path, params, extra_data = self._get_asset_bills(
            ccy, type, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_asset_bills_history(
        self,
        ccy: Any = None,
        type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get asset bills history"""
        request_type = "get_asset_bills_history"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        if type:
            params["type"] = type
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_asset_bills_history(
        self,
        ccy: Any = None,
        type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get asset bills history"""
        path, params, extra_data = self._get_asset_bills_history(
            ccy, type, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_bills_history(
        self,
        ccy: Any = None,
        type: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get asset bills history"""
        path, params, extra_data = self._get_asset_bills_history(
            ccy, type, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_deposit_address(
        self, ccy: Any, to: Any = None, chain: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get deposit address"""
        request_type = "get_deposit_address"
        params = {"ccy": ccy}
        if to:
            params["to"] = to
        if chain:
            params["chain"] = chain
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

    def get_deposit_address(
        self, ccy: Any, to: Any = None, chain: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get deposit address"""
        path, params, extra_data = self._get_deposit_address(ccy, to, chain, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_address(
        self, ccy: Any, to: Any = None, chain: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get deposit address"""
        path, params, extra_data = self._get_deposit_address(ccy, to, chain, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_deposit_history(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get deposit history"""
        request_type = "get_deposit_history"
        params: dict[str, Any] = {}
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_history(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get deposit history"""
        path, params, extra_data = self._get_deposit_history(
            ccy, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_history(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get deposit history"""
        path, params, extra_data = self._get_deposit_history(
            ccy, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_deposit_withdraw_status(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get deposit withdraw status"""
        request_type = "get_deposit_withdraw_status"
        params: dict[str, Any] = {}
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_withdraw_status(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get deposit withdraw status"""
        path, params, extra_data = self._get_deposit_withdraw_status(
            ccy, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_withdraw_status(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get deposit withdraw status"""
        path, params, extra_data = self._get_deposit_withdraw_status(
            ccy, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _withdrawal(
        self,
        ccy: Any,
        amt: Any,
        dest: Any,
        to_addr: Any,
        fee: Any = None,
        chain: Any = None,
        area_code: Any = None,
        client_chain_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Withdrawal"""
        request_type = "withdrawal"
        params = {
            "ccy": ccy,
            "amt": str(amt),
            "dest": dest,
            "toAddr": to_addr,
        }
        if fee is not None:
            params["fee"] = str(fee)
        if chain:
            params["chain"] = chain
        if area_code:
            params["areaCode"] = area_code
        if client_chain_id:
            params["clientChainId"] = client_chain_id
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

    def withdrawal(
        self,
        ccy: Any,
        amt: Any,
        dest: Any,
        to_addr: Any,
        fee: Any = None,
        chain: Any = None,
        area_code: Any = None,
        client_chain_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Withdrawal"""
        path, params, extra_data = self._withdrawal(
            ccy, amt, dest, to_addr, fee, chain, area_code, client_chain_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_withdrawal(
        self,
        ccy: Any,
        amt: Any,
        dest: Any,
        to_addr: Any,
        fee: Any = None,
        chain: Any = None,
        area_code: Any = None,
        client_chain_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async withdrawal"""
        path, params, extra_data = self._withdrawal(
            ccy, amt, dest, to_addr, fee, chain, area_code, client_chain_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_withdrawal(
        self, wd_id: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel withdrawal"""
        request_type = "cancel_withdrawal"
        params = {"wdId": wd_id}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_withdrawal(
        self, wd_id: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Cancel withdrawal"""
        path, params, extra_data = self._cancel_withdrawal(wd_id, ccy, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_withdrawal(
        self, wd_id: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async cancel withdrawal"""
        path, params, extra_data = self._cancel_withdrawal(wd_id, ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_withdrawal_history(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get withdrawal history"""
        request_type = "get_withdrawal_history"
        params: dict[str, Any] = {}
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_history(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get withdrawal history"""
        path, params, extra_data = self._get_withdrawal_history(
            ccy, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_history(
        self,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get withdrawal history"""
        path, params, extra_data = self._get_withdrawal_history(
            ccy, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Funding Account (P2) - Remaining Interfaces ====================

    def _get_exchange_list(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get exchange list"""
        request_type = "get_exchange_list"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_exchange_list(self, ccy: Any = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get exchange list"""
        path, params, extra_data = self._get_exchange_list(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_exchange_list(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get exchange list"""
        path, params, extra_data = self._get_exchange_list(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _apply_monthly_statement(
        self, month: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Apply for monthly statement (last year)"""
        request_type = "apply_monthly_statement"
        params: dict[str, Any] = {}
        if month:
            params["month"] = month
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

    def apply_monthly_statement(
        self, month: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Apply for monthly statement (last year)"""
        path, params, extra_data = self._apply_monthly_statement(month, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_apply_monthly_statement(
        self, month: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async apply for monthly statement (last year)"""
        path, params, extra_data = self._apply_monthly_statement(month, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_monthly_statement(
        self, month: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get monthly statement (last year)"""
        request_type = "get_monthly_statement"
        params: dict[str, Any] = {}
        if month:
            params["month"] = month
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

    def get_monthly_statement(
        self, month: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get monthly statement (last year)"""
        path, params, extra_data = self._get_monthly_statement(month, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_monthly_statement(
        self, month: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get monthly statement (last year)"""
        path, params, extra_data = self._get_monthly_statement(month, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_convert_currencies(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get convert currencies list"""
        request_type = "get_convert_currencies"
        params: dict[str, Any] = {}
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

    def get_convert_currencies(self, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get convert currencies list"""
        path, params, extra_data = self._get_convert_currencies(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_currencies(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get convert currencies list"""
        path, params, extra_data = self._get_convert_currencies(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_convert_currency_pair(
        self, from_ccy: Any = None, to_ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get convert currency pair"""
        request_type = "get_convert_currency_pair"
        params: dict[str, Any] = {}
        if from_ccy:
            params["fromCcy"] = from_ccy
        if to_ccy:
            params["toCcy"] = to_ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": f"{from_ccy or 'ALL'}-{to_ccy or 'ALL'}",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_currency_pair(
        self, from_ccy: Any = None, to_ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get convert currency pair"""
        path, params, extra_data = self._get_convert_currency_pair(
            from_ccy, to_ccy, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_currency_pair(
        self, from_ccy: Any = None, to_ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get convert currency pair"""
        path, params, extra_data = self._get_convert_currency_pair(
            from_ccy, to_ccy, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _convert_estimate_quote(
        self,
        from_ccy: Any,
        to_ccy: Any,
        amount: Any,
        type: Any = "buy",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Convert estimate quote"""
        request_type = "convert_estimate_quote"
        params = {
            "fromCcy": from_ccy,
            "toCcy": to_ccy,
            "amount": str(amount),
            "type": type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": f"{from_ccy}-{to_ccy}",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def convert_estimate_quote(
        self,
        from_ccy: Any,
        to_ccy: Any,
        amount: Any,
        type: Any = "buy",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Convert estimate quote"""
        path, params, extra_data = self._convert_estimate_quote(
            from_ccy, to_ccy, amount, type, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_convert_estimate_quote(
        self,
        from_ccy: Any,
        to_ccy: Any,
        amount: Any,
        type: Any = "buy",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async convert estimate quote"""
        path, params, extra_data = self._convert_estimate_quote(
            from_ccy, to_ccy, amount, type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _convert_trade(
        self,
        from_ccy: Any,
        to_ccy: Any,
        amount: Any,
        type: Any = "buy",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Convert trade"""
        request_type = "convert_trade"
        params = {
            "fromCcy": from_ccy,
            "toCcy": to_ccy,
            "amount": str(amount),
            "type": type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": f"{from_ccy}-{to_ccy}",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def convert_trade(
        self,
        from_ccy: Any,
        to_ccy: Any,
        amount: Any,
        type: Any = "buy",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Convert trade"""
        path, params, extra_data = self._convert_trade(
            from_ccy, to_ccy, amount, type, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_convert_trade(
        self,
        from_ccy: Any,
        to_ccy: Any,
        amount: Any,
        type: Any = "buy",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async convert trade"""
        path, params, extra_data = self._convert_trade(
            from_ccy, to_ccy, amount, type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_convert_history(
        self,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get convert history"""
        request_type = "get_convert_history"
        params: dict[str, Any] = {}
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

    def get_convert_history(
        self,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get convert history"""
        path, params, extra_data = self._get_convert_history(
            after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_history(
        self,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get convert history"""
        path, params, extra_data = self._get_convert_history(
            after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_deposit_payment_methods(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get deposit payment methods"""
        request_type = "get_deposit_payment_methods"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_payment_methods(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get deposit payment methods"""
        path, params, extra_data = self._get_deposit_payment_methods(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_payment_methods(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get deposit payment methods"""
        path, params, extra_data = self._get_deposit_payment_methods(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_withdrawal_payment_methods(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get withdrawal payment methods"""
        request_type = "get_withdrawal_payment_methods"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_payment_methods(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Get withdrawal payment methods"""
        path, params, extra_data = self._get_withdrawal_payment_methods(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_payment_methods(
        self, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get withdrawal payment methods"""
        path, params, extra_data = self._get_withdrawal_payment_methods(ccy, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _create_withdrawal_order(
        self,
        ccy: Any,
        amt: Any,
        dest: Any,
        to_addr: Any = None,
        pwd: Any = None,
        fee: Any = None,
        chain: Any = None,
        area_code: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Create withdrawal order"""
        request_type = "create_withdrawal_order"
        params = {
            "ccy": ccy,
            "amt": str(amt),
            "dest": dest,
        }
        if to_addr:
            params["toAddr"] = to_addr
        if pwd:
            params["pwd"] = pwd
        if fee is not None:
            params["fee"] = str(fee)
        if chain:
            params["chain"] = chain
        if area_code:
            params["areaCode"] = area_code
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

    def create_withdrawal_order(
        self,
        ccy: Any,
        amt: Any,
        dest: Any,
        to_addr: Any = None,
        pwd: Any = None,
        fee: Any = None,
        chain: Any = None,
        area_code: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Create withdrawal order"""
        path, params, extra_data = self._create_withdrawal_order(
            ccy, amt, dest, to_addr, pwd, fee, chain, area_code, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_withdrawal_order(
        self,
        ccy: Any,
        amt: Any,
        dest: Any,
        to_addr: Any = None,
        pwd: Any = None,
        fee: Any = None,
        chain: Any = None,
        area_code: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async create withdrawal order"""
        path, params, extra_data = self._create_withdrawal_order(
            ccy, amt, dest, to_addr, pwd, fee, chain, area_code, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _cancel_withdrawal_order(
        self, wd_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Cancel withdrawal order"""
        request_type = "cancel_withdrawal_order"
        params = {
            "wdId": wd_id,
        }
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

    def cancel_withdrawal_order(self, wd_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Cancel withdrawal order"""
        path, params, extra_data = self._cancel_withdrawal_order(wd_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_withdrawal_order(
        self, wd_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async cancel withdrawal order"""
        path, params, extra_data = self._cancel_withdrawal_order(wd_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_withdrawal_order_history(
        self,
        ccy: Any = None,
        wd_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get withdrawal order history"""
        request_type = "get_withdrawal_order_history"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        if wd_id:
            params["wdId"] = wd_id
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_order_history(
        self,
        ccy: Any = None,
        wd_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get withdrawal order history"""
        path, params, extra_data = self._get_withdrawal_order_history(
            ccy, wd_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_order_history(
        self,
        ccy: Any = None,
        wd_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get withdrawal order history"""
        path, params, extra_data = self._get_withdrawal_order_history(
            ccy, wd_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_withdrawal_order_detail(
        self, wd_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get withdrawal order detail"""
        request_type = "get_withdrawal_order_detail"
        params = {
            "wdId": wd_id,
        }
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

    def get_withdrawal_order_detail(self, wd_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get withdrawal order detail"""
        path, params, extra_data = self._get_withdrawal_order_detail(wd_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_order_detail(
        self, wd_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get withdrawal order detail"""
        path, params, extra_data = self._get_withdrawal_order_detail(wd_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_deposit_order_history(
        self,
        ccy: Any = None,
        dep_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get deposit order history"""
        request_type = "get_deposit_order_history"
        params: dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy
        if dep_id:
            params["depId"] = dep_id
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
                "symbol_name": ccy or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_order_history(
        self,
        ccy: Any = None,
        dep_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get deposit order history"""
        path, params, extra_data = self._get_deposit_order_history(
            ccy, dep_id, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_order_history(
        self,
        ccy: Any = None,
        dep_id: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get deposit order history"""
        path, params, extra_data = self._get_deposit_order_history(
            ccy, dep_id, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_deposit_order_detail(
        self, dep_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get deposit order detail"""
        request_type = "get_deposit_order_detail"
        params = {
            "depId": dep_id,
        }
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

    def get_deposit_order_detail(self, dep_id: Any, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get deposit order detail"""
        path, params, extra_data = self._get_deposit_order_detail(dep_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_order_detail(
        self, dep_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get deposit order detail"""
        path, params, extra_data = self._get_deposit_order_detail(dep_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_buy_sell_currencies(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get buy/sell currencies list"""
        request_type = "get_buy_sell_currencies"
        params: dict[str, Any] = {}
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

    def get_buy_sell_currencies(self, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get buy/sell currencies list"""
        path, params, extra_data = self._get_buy_sell_currencies(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_currencies(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get buy/sell currencies list"""
        path, params, extra_data = self._get_buy_sell_currencies(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_buy_sell_currency_pair(
        self, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get buy/sell currency pair"""
        request_type = "get_buy_sell_currency_pair"
        params: dict[str, Any] = {}
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

    def get_buy_sell_currency_pair(self, extra_data: Any = None, **kwargs: Any) -> Any:
        """Get buy/sell currency pair"""
        path, params, extra_data = self._get_buy_sell_currency_pair(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_currency_pair(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get buy/sell currency pair"""
        path, params, extra_data = self._get_buy_sell_currency_pair(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_buy_sell_quote(
        self,
        side: Any,
        quote_ccy: Any,
        base_ccy: Any,
        amount: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get buy/sell quote"""
        request_type = "get_buy_sell_quote"
        params = {
            "side": side,
            "quoteCcy": quote_ccy,
            "baseCcy": base_ccy,
        }
        if amount is not None:
            params["amount"] = str(amount)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": f"{base_ccy}-{quote_ccy}",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_quote(
        self,
        side: Any,
        quote_ccy: Any,
        base_ccy: Any,
        amount: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get buy/sell quote"""
        path, params, extra_data = self._get_buy_sell_quote(
            side, quote_ccy, base_ccy, amount, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_quote(
        self,
        side: Any,
        quote_ccy: Any,
        base_ccy: Any,
        amount: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get buy/sell quote"""
        path, params, extra_data = self._get_buy_sell_quote(
            side, quote_ccy, base_ccy, amount, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _buy_sell_trade(
        self,
        side: Any,
        quote_ccy: Any,
        base_ccy: Any,
        amount: Any,
        quote_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Buy/sell trade"""
        request_type = "buy_sell_trade"
        params = {
            "side": side,
            "quoteCcy": quote_ccy,
            "baseCcy": base_ccy,
            "amount": str(amount),
        }
        if quote_id:
            params["quoteId"] = quote_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": f"{base_ccy}-{quote_ccy}",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def buy_sell_trade(
        self,
        side: Any,
        quote_ccy: Any,
        base_ccy: Any,
        amount: Any,
        quote_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Buy/sell trade"""
        path, params, extra_data = self._buy_sell_trade(
            side, quote_ccy, base_ccy, amount, quote_id, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_buy_sell_trade(
        self,
        side: Any,
        quote_ccy: Any,
        base_ccy: Any,
        amount: Any,
        quote_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async buy/sell trade"""
        path, params, extra_data = self._buy_sell_trade(
            side, quote_ccy, base_ccy, amount, quote_id, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_buy_sell_history(
        self,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get buy/sell history"""
        request_type = "get_buy_sell_history"
        params: dict[str, Any] = {}
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

    def get_buy_sell_history(
        self,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Get buy/sell history"""
        path, params, extra_data = self._get_buy_sell_history(
            after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_history(
        self,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get buy/sell history"""
        path, params, extra_data = self._get_buy_sell_history(
            after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
