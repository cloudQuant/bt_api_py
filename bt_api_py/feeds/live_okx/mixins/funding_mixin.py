# -*- coding: utf-8 -*-
"""
OKX API - FundingMixin
Auto-generated from request_base.py
"""
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function


class FundingMixin:
    """Mixin providing OKX API methods."""
    # ==================== Missing Funding Account APIs ====================

    def _get_currencies(self, ccy=None, extra_data=None, **kwargs):
        """Get currencies"""
        request_type = "get_currencies"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_currencies(self, ccy=None, extra_data=None, **kwargs):
        """Get currencies"""
        path, params, extra_data = self._get_currencies(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_currencies(self, ccy=None, extra_data=None, **kwargs):
        """Async get currencies"""
        path, params, extra_data = self._get_currencies(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_balances(self, ccy=None, extra_data=None, **kwargs):
        """Get asset balances"""
        request_type = "get_asset_balances"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_asset_balances(self, ccy=None, extra_data=None, **kwargs):
        """Get asset balances"""
        path, params, extra_data = self._get_asset_balances(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_balances(self, ccy=None, extra_data=None, **kwargs):
        """Async get asset balances"""
        path, params, extra_data = self._get_asset_balances(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_non_tradable_assets(self, ccy=None, extra_data=None, **kwargs):
        """Get non-tradable assets"""
        request_type = "get_non_tradable_assets"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_non_tradable_assets(self, ccy=None, extra_data=None, **kwargs):
        """Get non-tradable assets"""
        path, params, extra_data = self._get_non_tradable_assets(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_non_tradable_assets(self, ccy=None, extra_data=None, **kwargs):
        """Async get non-tradable assets"""
        path, params, extra_data = self._get_non_tradable_assets(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_valuation(self, ccy=None, extra_data=None, **kwargs):
        """Get asset valuation"""
        request_type = "get_asset_valuation"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_asset_valuation(self, ccy=None, extra_data=None, **kwargs):
        """Get asset valuation"""
        path, params, extra_data = self._get_asset_valuation(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_valuation(self, ccy=None, extra_data=None, **kwargs):
        """Async get asset valuation"""
        path, params, extra_data = self._get_asset_valuation(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _transfer(self, ccy, amt, from_acct=None, to_acct=None, type=None, client_bill_id=None,
                  extra_data=None, **kwargs):
        """Asset transfer"""
        request_type = "transfer"
        params = {
            'ccy': ccy,
            'amt': str(amt),
        }
        if from_acct:
            params['from'] = from_acct
        if to_acct:
            params['to'] = to_acct
        if type:
            params['type'] = type
        if client_bill_id:
            params['clientBillId'] = client_bill_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def transfer(self, ccy, amt, from_acct=None, to_acct=None, type=None, client_bill_id=None,
                 extra_data=None, **kwargs):
        """Asset transfer"""
        path, params, extra_data = self._transfer(ccy, amt, from_acct, to_acct, type,
                                                    client_bill_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_transfer(self, ccy, amt, from_acct=None, to_acct=None, type=None, client_bill_id=None,
                       extra_data=None, **kwargs):
        """Async asset transfer"""
        path, params, extra_data = self._transfer(ccy, amt, from_acct, to_acct, type,
                                                    client_bill_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_transfer_state(self, transfer_id=None, client_bill_id=None, type=None,
                             extra_data=None, **kwargs):
        """Get transfer state"""
        request_type = "get_transfer_state"
        params = {}
        if transfer_id:
            params['transId'] = transfer_id
        if client_bill_id:
            params['clientBillId'] = client_bill_id
        if type:
            params['type'] = type
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_transfer_state(self, transfer_id=None, client_bill_id=None, type=None,
                           extra_data=None, **kwargs):
        """Get transfer state"""
        path, params, extra_data = self._get_transfer_state(transfer_id, client_bill_id,
                                                              type, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_transfer_state(self, transfer_id=None, client_bill_id=None, type=None,
                                  extra_data=None, **kwargs):
        """Async get transfer state"""
        path, params, extra_data = self._get_transfer_state(transfer_id, client_bill_id,
                                                              type, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_bills(self, ccy=None, type=None, after=None, before=None, limit=None,
                         extra_data=None, **kwargs):
        """Get asset bills"""
        request_type = "get_asset_bills"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if type:
            params['type'] = type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_asset_bills(self, ccy=None, type=None, after=None, before=None, limit=None,
                        extra_data=None, **kwargs):
        """Get asset bills"""
        path, params, extra_data = self._get_asset_bills(ccy, type, after, before, limit,
                                                           extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_bills(self, ccy=None, type=None, after=None, before=None, limit=None,
                              extra_data=None, **kwargs):
        """Async get asset bills"""
        path, params, extra_data = self._get_asset_bills(ccy, type, after, before, limit,
                                                           extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_asset_bills_history(self, ccy=None, type=None, after=None, before=None, limit=None,
                                  extra_data=None, **kwargs):
        """Get asset bills history"""
        request_type = "get_asset_bills_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if type:
            params['type'] = type
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_asset_bills_history(self, ccy=None, type=None, after=None, before=None, limit=None,
                                extra_data=None, **kwargs):
        """Get asset bills history"""
        path, params, extra_data = self._get_asset_bills_history(ccy, type, after, before, limit,
                                                                   extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_asset_bills_history(self, ccy=None, type=None, after=None, before=None,
                                       limit=None, extra_data=None, **kwargs):
        """Async get asset bills history"""
        path, params, extra_data = self._get_asset_bills_history(ccy, type, after, before, limit,
                                                                   extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_address(self, ccy, to=None, chain=None, extra_data=None, **kwargs):
        """Get deposit address"""
        request_type = "get_deposit_address"
        params = {'ccy': ccy}
        if to:
            params['to'] = to
        if chain:
            params['chain'] = chain
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_address(self, ccy, to=None, chain=None, extra_data=None, **kwargs):
        """Get deposit address"""
        path, params, extra_data = self._get_deposit_address(ccy, to, chain, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_address(self, ccy, to=None, chain=None, extra_data=None, **kwargs):
        """Async get deposit address"""
        path, params, extra_data = self._get_deposit_address(ccy, to, chain, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_history(self, ccy=None, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get deposit history"""
        request_type = "get_deposit_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_deposit_history(self, ccy=None, after=None, before=None, limit=None,
                            extra_data=None, **kwargs):
        """Get deposit history"""
        path, params, extra_data = self._get_deposit_history(ccy, after, before, limit,
                                                               extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_history(self, ccy=None, after=None, before=None, limit=None,
                                  extra_data=None, **kwargs):
        """Async get deposit history"""
        path, params, extra_data = self._get_deposit_history(ccy, after, before, limit,
                                                               extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_withdraw_status(self, ccy=None, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Get deposit withdraw status"""
        request_type = "get_deposit_withdraw_status"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_deposit_withdraw_status(self, ccy=None, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Get deposit withdraw status"""
        path, params, extra_data = self._get_deposit_withdraw_status(ccy, after, before, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_withdraw_status(self, ccy=None, after=None, before=None, limit=None,
                                           extra_data=None, **kwargs):
        """Async get deposit withdraw status"""
        path, params, extra_data = self._get_deposit_withdraw_status(ccy, after, before, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _withdrawal(self, ccy, amt, dest, to_addr, fee=None, chain=None, area_code=None,
                     client_chain_id=None, extra_data=None, **kwargs):
        """Withdrawal"""
        request_type = "withdrawal"
        params = {
            'ccy': ccy,
            'amt': str(amt),
            'dest': dest,
            'toAddr': to_addr,
        }
        if fee is not None:
            params['fee'] = str(fee)
        if chain:
            params['chain'] = chain
        if area_code:
            params['areaCode'] = area_code
        if client_chain_id:
            params['clientChainId'] = client_chain_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def withdrawal(self, ccy, amt, dest, to_addr, fee=None, chain=None, area_code=None,
                   client_chain_id=None, extra_data=None, **kwargs):
        """Withdrawal"""
        path, params, extra_data = self._withdrawal(ccy, amt, dest, to_addr, fee, chain,
                                                      area_code, client_chain_id, extra_data,
                                                      **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_withdrawal(self, ccy, amt, dest, to_addr, fee=None, chain=None, area_code=None,
                         client_chain_id=None, extra_data=None, **kwargs):
        """Async withdrawal"""
        path, params, extra_data = self._withdrawal(ccy, amt, dest, to_addr, fee, chain,
                                                      area_code, client_chain_id, extra_data,
                                                      **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_withdrawal(self, wd_id, ccy=None, extra_data=None, **kwargs):
        """Cancel withdrawal"""
        request_type = "cancel_withdrawal"
        params = {'wdId': wd_id}
        if ccy:
            params['ccy'] = ccy
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

    def cancel_withdrawal(self, wd_id, ccy=None, extra_data=None, **kwargs):
        """Cancel withdrawal"""
        path, params, extra_data = self._cancel_withdrawal(wd_id, ccy, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_withdrawal(self, wd_id, ccy=None, extra_data=None, **kwargs):
        """Async cancel withdrawal"""
        path, params, extra_data = self._cancel_withdrawal(wd_id, ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_history(self, ccy=None, after=None, before=None, limit=None,
                                 extra_data=None, **kwargs):
        """Get withdrawal history"""
        request_type = "get_withdrawal_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_withdrawal_history(self, ccy=None, after=None, before=None, limit=None,
                               extra_data=None, **kwargs):
        """Get withdrawal history"""
        path, params, extra_data = self._get_withdrawal_history(ccy, after, before, limit,
                                                                  extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_history(self, ccy=None, after=None, before=None, limit=None,
                                     extra_data=None, **kwargs):
        """Async get withdrawal history"""
        path, params, extra_data = self._get_withdrawal_history(ccy, after, before, limit,
                                                                  extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    # ==================== Funding Account (P2) - Remaining Interfaces ====================

    def _get_exchange_list(self, ccy=None, extra_data=None, **kwargs):
        """Get exchange list"""
        request_type = "get_exchange_list"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_exchange_list(self, ccy=None, extra_data=None, **kwargs):
        """Get exchange list"""
        path, params, extra_data = self._get_exchange_list(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_exchange_list(self, ccy=None, extra_data=None, **kwargs):
        """Async get exchange list"""
        path, params, extra_data = self._get_exchange_list(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _apply_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Apply for monthly statement (last year)"""
        request_type = "apply_monthly_statement"
        params = {}
        if month:
            params['month'] = month
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def apply_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Apply for monthly statement (last year)"""
        path, params, extra_data = self._apply_monthly_statement(month, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_apply_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Async apply for monthly statement (last year)"""
        path, params, extra_data = self._apply_monthly_statement(month, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Get monthly statement (last year)"""
        request_type = "get_monthly_statement"
        params = {}
        if month:
            params['month'] = month
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Get monthly statement (last year)"""
        path, params, extra_data = self._get_monthly_statement(month, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_monthly_statement(self, month=None, extra_data=None, **kwargs):
        """Async get monthly statement (last year)"""
        path, params, extra_data = self._get_monthly_statement(month, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_convert_currencies(self, extra_data=None, **kwargs):
        """Get convert currencies list"""
        request_type = "get_convert_currencies"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_currencies(self, extra_data=None, **kwargs):
        """Get convert currencies list"""
        path, params, extra_data = self._get_convert_currencies(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_currencies(self, extra_data=None, **kwargs):
        """Async get convert currencies list"""
        path, params, extra_data = self._get_convert_currencies(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_convert_currency_pair(self, from_ccy=None, to_ccy=None, extra_data=None, **kwargs):
        """Get convert currency pair"""
        request_type = "get_convert_currency_pair"
        params = {}
        if from_ccy:
            params['fromCcy'] = from_ccy
        if to_ccy:
            params['toCcy'] = to_ccy
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{from_ccy or 'ALL'}-{to_ccy or 'ALL'}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_currency_pair(self, from_ccy=None, to_ccy=None, extra_data=None, **kwargs):
        """Get convert currency pair"""
        path, params, extra_data = self._get_convert_currency_pair(from_ccy, to_ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_currency_pair(self, from_ccy=None, to_ccy=None, extra_data=None, **kwargs):
        """Async get convert currency pair"""
        path, params, extra_data = self._get_convert_currency_pair(from_ccy, to_ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _convert_estimate_quote(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert estimate quote"""
        request_type = "convert_estimate_quote"
        params = {
            'fromCcy': from_ccy,
            'toCcy': to_ccy,
            'amount': str(amount),
            'type': type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{from_ccy}-{to_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def convert_estimate_quote(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert estimate quote"""
        path, params, extra_data = self._convert_estimate_quote(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_convert_estimate_quote(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Async convert estimate quote"""
        path, params, extra_data = self._convert_estimate_quote(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _convert_trade(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert trade"""
        request_type = "convert_trade"
        params = {
            'fromCcy': from_ccy,
            'toCcy': to_ccy,
            'amount': str(amount),
            'type': type,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{from_ccy}-{to_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def convert_trade(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Convert trade"""
        path, params, extra_data = self._convert_trade(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_convert_trade(self, from_ccy, to_ccy, amount, type='buy', extra_data=None, **kwargs):
        """Async convert trade"""
        path, params, extra_data = self._convert_trade(from_ccy, to_ccy, amount, type, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get convert history"""
        request_type = "get_convert_history"
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
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get convert history"""
        path, params, extra_data = self._get_convert_history(after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_convert_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get convert history"""
        path, params, extra_data = self._get_convert_history(after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get deposit payment methods"""
        request_type = "get_deposit_payment_methods"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_deposit_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get deposit payment methods"""
        path, params, extra_data = self._get_deposit_payment_methods(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Async get deposit payment methods"""
        path, params, extra_data = self._get_deposit_payment_methods(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get withdrawal payment methods"""
        request_type = "get_withdrawal_payment_methods"
        params = {}
        if ccy:
            params['ccy'] = ccy
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

    def get_withdrawal_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Get withdrawal payment methods"""
        path, params, extra_data = self._get_withdrawal_payment_methods(ccy, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_payment_methods(self, ccy=None, extra_data=None, **kwargs):
        """Async get withdrawal payment methods"""
        path, params, extra_data = self._get_withdrawal_payment_methods(ccy, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _create_withdrawal_order(self, ccy, amt, dest, to_addr=None, pwd=None, fee=None, chain=None,
                                  area_code=None, extra_data=None, **kwargs):
        """Create withdrawal order"""
        request_type = "create_withdrawal_order"
        params = {
            'ccy': ccy,
            'amt': str(amt),
            'dest': dest,
        }
        if to_addr:
            params['toAddr'] = to_addr
        if pwd:
            params['pwd'] = pwd
        if fee is not None:
            params['fee'] = str(fee)
        if chain:
            params['chain'] = chain
        if area_code:
            params['areaCode'] = area_code
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": ccy,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def create_withdrawal_order(self, ccy, amt, dest, to_addr=None, pwd=None, fee=None, chain=None,
                                 area_code=None, extra_data=None, **kwargs):
        """Create withdrawal order"""
        path, params, extra_data = self._create_withdrawal_order(ccy, amt, dest, to_addr, pwd, fee, chain,
                                                                  area_code, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_create_withdrawal_order(self, ccy, amt, dest, to_addr=None, pwd=None, fee=None, chain=None,
                                       area_code=None, extra_data=None, **kwargs):
        """Async create withdrawal order"""
        path, params, extra_data = self._create_withdrawal_order(ccy, amt, dest, to_addr, pwd, fee, chain,
                                                                  area_code, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _cancel_withdrawal_order(self, wd_id, extra_data=None, **kwargs):
        """Cancel withdrawal order"""
        request_type = "cancel_withdrawal_order"
        params = {
            'wdId': wd_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def cancel_withdrawal_order(self, wd_id, extra_data=None, **kwargs):
        """Cancel withdrawal order"""
        path, params, extra_data = self._cancel_withdrawal_order(wd_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_cancel_withdrawal_order(self, wd_id, extra_data=None, **kwargs):
        """Async cancel withdrawal order"""
        path, params, extra_data = self._cancel_withdrawal_order(wd_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_order_history(self, ccy=None, wd_id=None, after=None, before=None, limit=None,
                                       extra_data=None, **kwargs):
        """Get withdrawal order history"""
        request_type = "get_withdrawal_order_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if wd_id:
            params['wdId'] = wd_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_withdrawal_order_history(self, ccy=None, wd_id=None, after=None, before=None, limit=None,
                                      extra_data=None, **kwargs):
        """Get withdrawal order history"""
        path, params, extra_data = self._get_withdrawal_order_history(ccy, wd_id, after, before, limit,
                                                                       extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_order_history(self, ccy=None, wd_id=None, after=None, before=None, limit=None,
                                            extra_data=None, **kwargs):
        """Async get withdrawal order history"""
        path, params, extra_data = self._get_withdrawal_order_history(ccy, wd_id, after, before, limit,
                                                                       extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_withdrawal_order_detail(self, wd_id, extra_data=None, **kwargs):
        """Get withdrawal order detail"""
        request_type = "get_withdrawal_order_detail"
        params = {
            'wdId': wd_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_withdrawal_order_detail(self, wd_id, extra_data=None, **kwargs):
        """Get withdrawal order detail"""
        path, params, extra_data = self._get_withdrawal_order_detail(wd_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_withdrawal_order_detail(self, wd_id, extra_data=None, **kwargs):
        """Async get withdrawal order detail"""
        path, params, extra_data = self._get_withdrawal_order_detail(wd_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_order_history(self, ccy=None, dep_id=None, after=None, before=None, limit=None,
                                    extra_data=None, **kwargs):
        """Get deposit order history"""
        request_type = "get_deposit_order_history"
        params = {}
        if ccy:
            params['ccy'] = ccy
        if dep_id:
            params['depId'] = dep_id
        if after:
            params['after'] = after
        if before:
            params['before'] = before
        if limit:
            params['limit'] = limit
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

    def get_deposit_order_history(self, ccy=None, dep_id=None, after=None, before=None, limit=None,
                                   extra_data=None, **kwargs):
        """Get deposit order history"""
        path, params, extra_data = self._get_deposit_order_history(ccy, dep_id, after, before, limit,
                                                                    extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_order_history(self, ccy=None, dep_id=None, after=None, before=None, limit=None,
                                         extra_data=None, **kwargs):
        """Async get deposit order history"""
        path, params, extra_data = self._get_deposit_order_history(ccy, dep_id, after, before, limit,
                                                                    extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_deposit_order_detail(self, dep_id, extra_data=None, **kwargs):
        """Get deposit order detail"""
        request_type = "get_deposit_order_detail"
        params = {
            'depId': dep_id,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_deposit_order_detail(self, dep_id, extra_data=None, **kwargs):
        """Get deposit order detail"""
        path, params, extra_data = self._get_deposit_order_detail(dep_id, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_deposit_order_detail(self, dep_id, extra_data=None, **kwargs):
        """Async get deposit order detail"""
        path, params, extra_data = self._get_deposit_order_detail(dep_id, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_currencies(self, extra_data=None, **kwargs):
        """Get buy/sell currencies list"""
        request_type = "get_buy_sell_currencies"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_currencies(self, extra_data=None, **kwargs):
        """Get buy/sell currencies list"""
        path, params, extra_data = self._get_buy_sell_currencies(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_currencies(self, extra_data=None, **kwargs):
        """Async get buy/sell currencies list"""
        path, params, extra_data = self._get_buy_sell_currencies(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_currency_pair(self, extra_data=None, **kwargs):
        """Get buy/sell currency pair"""
        request_type = "get_buy_sell_currency_pair"
        params = {}
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_currency_pair(self, extra_data=None, **kwargs):
        """Get buy/sell currency pair"""
        path, params, extra_data = self._get_buy_sell_currency_pair(extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_currency_pair(self, extra_data=None, **kwargs):
        """Async get buy/sell currency pair"""
        path, params, extra_data = self._get_buy_sell_currency_pair(extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_quote(self, side, quote_ccy, base_ccy, amount=None, extra_data=None, **kwargs):
        """Get buy/sell quote"""
        request_type = "get_buy_sell_quote"
        params = {
            'side': side,
            'quoteCcy': quote_ccy,
            'baseCcy': base_ccy,
        }
        if amount is not None:
            params['amount'] = str(amount)
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{base_ccy}-{quote_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_quote(self, side, quote_ccy, base_ccy, amount=None, extra_data=None, **kwargs):
        """Get buy/sell quote"""
        path, params, extra_data = self._get_buy_sell_quote(side, quote_ccy, base_ccy, amount, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_quote(self, side, quote_ccy, base_ccy, amount=None, extra_data=None, **kwargs):
        """Async get buy/sell quote"""
        path, params, extra_data = self._get_buy_sell_quote(side, quote_ccy, base_ccy, amount, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _buy_sell_trade(self, side, quote_ccy, base_ccy, amount, quote_id=None, extra_data=None, **kwargs):
        """Buy/sell trade"""
        request_type = "buy_sell_trade"
        params = {
            'side': side,
            'quoteCcy': quote_ccy,
            'baseCcy': base_ccy,
            'amount': str(amount),
        }
        if quote_id:
            params['quoteId'] = quote_id
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(extra_data, **{
            "request_type": request_type,
            "symbol_name": f"{base_ccy}-{quote_ccy}",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def buy_sell_trade(self, side, quote_ccy, base_ccy, amount, quote_id=None, extra_data=None, **kwargs):
        """Buy/sell trade"""
        path, params, extra_data = self._buy_sell_trade(side, quote_ccy, base_ccy, amount, quote_id, extra_data, **kwargs)
        data = self.request(path, body=params, extra_data=extra_data)
        return data

    def async_buy_sell_trade(self, side, quote_ccy, base_ccy, amount, quote_id=None, extra_data=None, **kwargs):
        """Async buy/sell trade"""
        path, params, extra_data = self._buy_sell_trade(side, quote_ccy, base_ccy, amount, quote_id, extra_data, **kwargs)
        self.submit(self.async_request(path, body=params, extra_data=extra_data),
                    callback=self.async_callback)

    def _get_buy_sell_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get buy/sell history"""
        request_type = "get_buy_sell_history"
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
            "normalize_function": generic_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    def get_buy_sell_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Get buy/sell history"""
        path, params, extra_data = self._get_buy_sell_history(after, before, limit, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def async_get_buy_sell_history(self, after=None, before=None, limit=None, extra_data=None, **kwargs):
        """Async get buy/sell history"""
        path, params, extra_data = self._get_buy_sell_history(after, before, limit, extra_data, **kwargs)
        self.submit(self.async_request(path, params=params, extra_data=extra_data),
                    callback=self.async_callback)

