"""
OKX API - SubAccountMixin
Auto-generated from request_base.py
"""

from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function
from bt_api_py.functions.utils import update_extra_data


class SubAccountMixin:
    """Mixin providing OKX API methods."""

    # ==================== Missing Sub-account APIs ====================

    def _get_sub_account_list(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get sub account list"""
        request_type = "get_sub_account_list"
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

    def get_sub_account_list(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get sub account list"""
        path, params, extra_data = self._get_sub_account_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_list(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get sub account list"""
        path, params, extra_data = self._get_sub_account_list(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _create_sub_account(
        self, sub_acct: Any, tag: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Create sub account"""
        request_type = "create_sub_account"
        params = {"subAcct": sub_acct}
        if tag:
            params["tag"] = tag
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def create_sub_account(
        self, sub_acct: Any, tag: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Create sub account"""
        path, params, extra_data = self._create_sub_account(sub_acct, tag, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_sub_account(
        self, sub_acct: Any, tag: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async create sub account"""
        path, params, extra_data = self._create_sub_account(sub_acct, tag, extra_data, **kwargs)
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _create_sub_account_api_key(
        self,
        sub_acct: Any,
        label: Any = None,
        ip: Any = None,
        perm: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Create sub account API key"""
        request_type = "create_sub_account_api_key"
        params = {
            "subAcct": sub_acct,
        }
        if label:
            params["label"] = label
        if ip:
            params["ip"] = ip
        if perm:
            params["perm"] = perm
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def create_sub_account_api_key(
        self,
        sub_acct: Any,
        label: Any = None,
        ip: Any = None,
        perm: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Create sub account API key"""
        path, params, extra_data = self._create_sub_account_api_key(
            sub_acct, label, ip, perm, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_sub_account_api_key(
        self,
        sub_acct: Any,
        label: Any = None,
        ip: Any = None,
        perm: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async create sub account API key"""
        path, params, extra_data = self._create_sub_account_api_key(
            sub_acct, label, ip, perm, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_sub_account_api_key(
        self, sub_acct: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get sub account API key"""
        request_type = "get_sub_account_api_key"
        params = {"subAcct": sub_acct}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_api_key(self, sub_acct: Any, extra_data: Any = None, **kwargs: Any) -> None:
        """Get sub account API key"""
        path, params, extra_data = self._get_sub_account_api_key(sub_acct, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_api_key(
        self, sub_acct: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get sub account API key"""
        path, params, extra_data = self._get_sub_account_api_key(sub_acct, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _reset_sub_account_api_key(
        self,
        sub_acct: Any,
        api_key: Any,
        label: Any = None,
        ip: Any = None,
        perm: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Reset sub account API key"""
        request_type = "reset_sub_account_api_key"
        params = {
            "subAcct": sub_acct,
            "apiKey": api_key,
        }
        if label:
            params["label"] = label
        if ip:
            params["ip"] = ip
        if perm:
            params["perm"] = perm
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def reset_sub_account_api_key(
        self,
        sub_acct: Any,
        api_key: Any,
        label: Any = None,
        ip: Any = None,
        perm: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Reset sub account API key"""
        path, params, extra_data = self._reset_sub_account_api_key(
            sub_acct, api_key, label, ip, perm, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_reset_sub_account_api_key(
        self,
        sub_acct: Any,
        api_key: Any,
        label: Any = None,
        ip: Any = None,
        perm: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async reset sub account API key"""
        path, params, extra_data = self._reset_sub_account_api_key(
            sub_acct, api_key, label, ip, perm, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _delete_sub_account_api_key(
        self, sub_acct: Any, api_key: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Delete sub account API key"""
        request_type = "delete_sub_account_api_key"
        params = {
            "subAcct": sub_acct,
            "apiKey": api_key,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def delete_sub_account_api_key(
        self, sub_acct: Any, api_key: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Delete sub account API key"""
        path, params, extra_data = self._delete_sub_account_api_key(
            sub_acct, api_key, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_delete_sub_account_api_key(
        self, sub_acct: Any, api_key: Any, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async delete sub account API key"""
        path, params, extra_data = self._delete_sub_account_api_key(
            sub_acct, api_key, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_sub_account_funding_balance(
        self, sub_acct: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get sub account funding balance"""
        request_type = "get_sub_account_funding_balance"
        params = {"subAcct": sub_acct}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_funding_balance(
        self, sub_acct: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get sub account funding balance"""
        path, params, extra_data = self._get_sub_account_funding_balance(
            sub_acct, ccy, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_funding_balance(
        self, sub_acct: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get sub account funding balance"""
        path, params, extra_data = self._get_sub_account_funding_balance(
            sub_acct, ccy, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_sub_account_max_withdrawal(
        self, sub_acct: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get sub account max withdrawal"""
        request_type = "get_sub_account_max_withdrawal"
        params = {"subAcct": sub_acct}
        if ccy:
            params["ccy"] = ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_max_withdrawal(
        self, sub_acct: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Get sub account max withdrawal"""
        path, params, extra_data = self._get_sub_account_max_withdrawal(
            sub_acct, ccy, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_max_withdrawal(
        self, sub_acct: Any, ccy: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> None:
        """Async get sub account max withdrawal"""
        path, params, extra_data = self._get_sub_account_max_withdrawal(
            sub_acct, ccy, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    # ==================== Sub-account (P2) - Remaining Interfaces ====================

    def _get_sub_account_transfer_history(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get sub-account transfer history"""
        request_type = "get_sub_account_transfer_history"
        params = {}
        if sub_acct:
            params["subAcct"] = sub_acct
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
                "symbol_name": sub_acct or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_sub_account_transfer_history(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get sub-account transfer history"""
        path, params, extra_data = self._get_sub_account_transfer_history(
            sub_acct, ccy, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_sub_account_transfer_history(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get sub-account transfer history"""
        path, params, extra_data = self._get_sub_account_transfer_history(
            sub_acct, ccy, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_managed_sub_account_bills(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get managed sub-account bills"""
        request_type = "get_managed_sub_account_bills"
        params = {}
        if sub_acct:
            params["subAcct"] = sub_acct
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
                "symbol_name": sub_acct or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_managed_sub_account_bills(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Get managed sub-account bills"""
        path, params, extra_data = self._get_managed_sub_account_bills(
            sub_acct, ccy, after, before, limit, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_managed_sub_account_bills(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        after: Any = None,
        before: Any = None,
        limit: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async get managed sub-account bills"""
        path, params, extra_data = self._get_managed_sub_account_bills(
            sub_acct, ccy, after, before, limit, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _sub_account_transfer(
        self,
        ccy: Any,
        amount: Any,
        from_acct: Any,
        to_acct: Any,
        from_acc_type: Any = 6,
        to_acc_type: Any = 6,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Sub-account transfer"""
        request_type = "sub_account_transfer"
        params = {
            "ccy": ccy,
            "amt": str(amount),
            "fromAcct": from_acct,
            "toAcct": to_acct,
            "type": "1",  # 1: master account transfer to sub-account, 2: sub-account transfer to master account
            "fromAccType": str(from_acc_type),  # 6: master account
            "toAccType": str(to_acc_type),  # 6: master account
        }
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

    def sub_account_transfer(
        self,
        ccy: Any,
        amount: Any,
        from_acct: Any,
        to_acct: Any,
        from_acc_type: Any = 6,
        to_acc_type: Any = 6,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Sub-account transfer"""
        path, params, extra_data = self._sub_account_transfer(
            ccy, amount, from_acct, to_acct, from_acc_type, to_acc_type, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_sub_account_transfer(
        self,
        ccy: Any,
        amount: Any,
        from_acct: Any,
        to_acct: Any,
        from_acc_type: Any = 6,
        to_acc_type: Any = 6,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async sub-account transfer"""
        path, params, extra_data = self._sub_account_transfer(
            ccy, amount, from_acct, to_acct, from_acc_type, to_acc_type, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _set_sub_account_transfer_out(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        allow: Any = False,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Set sub-account transfer out permission"""
        request_type = "set_sub_account_transfer_out"
        params = {
            "subAcct": sub_acct or "",
            "ccy": ccy or "",
            "allow": "true" if allow else "false",
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": sub_acct or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": generic_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def set_sub_account_transfer_out(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        allow: Any = False,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Set sub-account transfer out permission"""
        path, params, extra_data = self._set_sub_account_transfer_out(
            sub_acct, ccy, allow, extra_data, **kwargs
        )
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_set_sub_account_transfer_out(
        self,
        sub_acct: Any = None,
        ccy: Any = None,
        allow: Any = False,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Async set sub-account transfer out permission"""
        path, params, extra_data = self._set_sub_account_transfer_out(
            sub_acct, ccy, allow, extra_data, **kwargs
        )
        self.submit(
            self.async_request(path, body=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def _get_custody_sub_account_list(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get custody sub-account list"""
        request_type = "get_custody_sub_account_list"
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

    def get_custody_sub_account_list(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Get custody sub-account list"""
        path, params, extra_data = self._get_custody_sub_account_list(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_custody_sub_account_list(self, extra_data: Any = None, **kwargs: Any) -> None:
        """Async get custody sub-account list"""
        path, params, extra_data = self._get_custody_sub_account_list(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )
