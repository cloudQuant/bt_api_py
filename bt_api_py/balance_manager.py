"""账户余额与资金管理。"""

from __future__ import annotations

from typing import Any, Protocol

from bt_api_base.exceptions import CurrencyNotFoundError, ExchangeNotFoundError
from bt_api_base.registry import ExchangeRegistry


class _BalanceManagerHost(Protocol):
    """Host attributes required from ``BtApi`` by :class:`BalanceManagerMixin`."""

    exchange_feeds: dict[str, Any]
    _value_dict: dict[str, Any]
    _cash_dict: dict[str, Any]

    def log(self, txt: str, level: str = "info") -> None: ...
    def _get_feed(self, exchange_name: str) -> Any: ...


class BalanceManagerMixin:
    """余额与资金管理方法（供 BtApi 混入）。"""

    def update_total_balance(self: _BalanceManagerHost) -> None:
        """通过 ExchangeRegistry 查找余额解析函数，无需硬编码交易所类型"""
        for exchange_name in self.exchange_feeds:
            feed = self.exchange_feeds[exchange_name]
            balance_data = feed.get_balance()
            balance_data.init_data()
            account_list = balance_data.get_data()

            balance_handler = ExchangeRegistry.get_balance_handler(exchange_name)
            if balance_handler is not None:
                value_result, cash_result = balance_handler(account_list)
                self._value_dict[exchange_name] = value_result
                self._cash_dict[exchange_name] = cash_result
            else:
                self.log(f"No balance handler registered for {exchange_name}", level="warning")

    def update_balance(
        self: _BalanceManagerHost, exchange_name: str, currency: str | None = None
    ) -> None:
        feed = self._get_feed(exchange_name)
        balance_data = feed.get_balance()
        balance_data.init_data()
        account_list = balance_data.get_data()
        for account in account_list:
            account.init_data()
            if currency is not None:
                if account.get_account_type() == currency:
                    self._value_dict[exchange_name][currency]["value"] = (
                        account.get_margin() + account.get_unrealized_profit()
                    )
                    self._cash_dict[exchange_name][currency]["cash"] = (
                        account.get_available_margin()
                    )
            else:
                self._value_dict[exchange_name][account.get_account_type()]["value"] = (
                    account.get_margin() + account.get_unrealized_profit()
                )
                self._cash_dict[exchange_name][account.get_account_type()]["cash"] = (
                    account.get_available_margin()
                )

    def get_cash(self: _BalanceManagerHost, exchange_name: str, currency: str) -> float:
        if exchange_name not in self._cash_dict:
            raise ExchangeNotFoundError(exchange_name, list(self._cash_dict.keys()))
        if currency not in self._cash_dict[exchange_name]:
            raise CurrencyNotFoundError(exchange_name, currency)
        return self._cash_dict[exchange_name][currency]["cash"]

    def get_value(self: _BalanceManagerHost, exchange_name: str, currency: str) -> float:
        if exchange_name not in self._value_dict:
            raise ExchangeNotFoundError(exchange_name, list(self._value_dict.keys()))
        if currency not in self._value_dict[exchange_name]:
            raise CurrencyNotFoundError(exchange_name, currency)
        return self._value_dict[exchange_name][currency]["value"]

    def get_total_cash(self: _BalanceManagerHost) -> dict[str, Any]:
        return self._cash_dict

    def get_total_value(self: _BalanceManagerHost) -> dict[str, Any]:
        return self._value_dict
