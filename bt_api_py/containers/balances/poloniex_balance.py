"""
Poloniex Balance Data Container
"""

import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class PoloniexBalanceData(BalanceData):
    """Poloniex Balance Data Container"""

    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "POLONIEX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name  # Currency code
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.currency = None
        self.available_balance = None
        self.locked_balance = None
        self.total_balance = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "currency": self.currency,
                "available_balance": self.available_balance,
                "locked_balance": self.locked_balance,
                "total_balance": self.total_balance,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_currency(self):
        return self.currency

    def get_available_balance(self):
        return self.available_balance

    def get_locked_balance(self):
        return self.locked_balance

    def get_total_balance(self):
        return self.total_balance


class PoloniexRequestBalanceData(PoloniexBalanceData):
    """Poloniex REST API Balance Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            # Handle both single balance and list of balances
            if isinstance(self.balance_info, list) and len(self.balance_info) > 0:
                self.balance_data = self.balance_info[0]
            else:
                self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.currency = from_dict_get_string(self.balance_data, "currency")
        self.available_balance = from_dict_get_float(self.balance_data, "available")
        self.locked_balance = from_dict_get_float(self.balance_data, "hold")
        self.total_balance = (
            self.available_balance + self.locked_balance
            if self.available_balance and self.locked_balance
            else None
        )

        self.has_been_init_data = True
        return self


class PoloniexWssBalanceData(PoloniexBalanceData):
    """Poloniex WebSocket Balance Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.currency = from_dict_get_string(self.balance_data, "currency")
        self.available_balance = from_dict_get_float(self.balance_data, "available")
        self.locked_balance = from_dict_get_float(self.balance_data, "locked") or from_dict_get_float(self.balance_data, "hold")
        self.total_balance = (
            self.available_balance + self.locked_balance
            if self.available_balance and self.locked_balance
            else None
        )

        self.has_been_init_data = True
        return self
