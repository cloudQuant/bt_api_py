"""
Gate.io Balance Data Container
"""

import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class GateioBalanceData(BalanceData):
    """Gate.io balance data container"""

    def __init__(self, balance_info, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "GATEIO"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.currency = None
        self.available = None
        self.locked = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize and parse balance data"""
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse balance data
        self.currency = from_dict_get_string(self.balance_data, "currency")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.locked = from_dict_get_float(self.balance_data, "locked")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all balance data as dictionary"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "currency": self.currency,
                "available": self.available,
                "locked": self.locked,
                "total": self.available + self.locked if self.available and self.locked else 0,
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

    def get_asset_type(self):
        return self.asset_type

    def get_currency(self):
        return self.currency

    def get_available(self):
        return self.available

    def get_locked(self):
        return self.locked

    def get_total(self):
        """Get total balance (available + locked)"""
        if self.available and self.locked:
            return self.available + self.locked
        return 0.0

    def is_zero_balance(self):
        """Check if balance is zero"""
        return not (self.available and self.available > 0) and not (self.locked and self.locked > 0)


class GateioRequestBalanceData(GateioBalanceData):
    """Gate.io REST API balance data"""

    def init_data(self):
        """Initialize and parse REST API balance data"""
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse REST API balance data
        self.currency = from_dict_get_string(self.balance_data, "currency")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.locked = from_dict_get_float(self.balance_data, "locked")

        self.has_been_init_data = True
        return self


class GateioWssBalanceData(GateioBalanceData):
    """Gate.io WebSocket balance data (placeholder for future implementation)"""

    def init_data(self):
        """Initialize and parse WebSocket balance data"""
        # TODO: Implement WebSocket balance data parsing
        return self


class GateioAccountBalance:
    """Gate.io account balance container for multiple currencies"""

    def __init__(self, balance_list, asset_type="SPOT"):
        self.exchange_name = "GATEIO"
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.balances = []
        self._parse_balances(balance_list)

    def _parse_balances(self, balance_list):
        """Parse balance list"""
        for balance_info in balance_list:
            balance = GateioRequestBalanceData(balance_info, self.asset_type, True)
            self.balances.append(balance)

    def get_all_balances(self):
        """Get all balances"""
        return [balance.get_all_data() for balance in self.balances]

    def get_balance(self, currency):
        """Get balance for specific currency"""
        for balance in self.balances:
            if balance.get_currency() == currency:
                return balance.get_all_data()
        return None

    def get_nonzero_balances(self):
        """Get all non-zero balances"""
        return [balance for balance in self.balances if not balance.is_zero_balance()]

    def get_total_value(self, prices=None):
        """
        Get total account value in quote currency
        prices: Dictionary of currency to price conversion
        """
        total_value = 0.0
        for balance in self.get_nonzero_balances():
            available = balance.get_available()
            locked = balance.get_locked()
            total = available + locked
            currency = balance.get_currency()

            if currency in prices:
                total_value += total * prices[currency]

        return total_value

    def __str__(self):
        return json.dumps(self.get_all_balances())

    def __repr__(self):
        return self.__str__()