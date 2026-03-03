"""HTX Balance Data Container"""

import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HtxRequestBalanceData(BalanceData):
    """HTX REST API balance data."""

    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.account_type = "SPOT"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.available_margin = None
        self.used_margin = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize balance data from HTX response.

        HTX balance response format:
        {
            "status": "ok",
            "data": {
                "id": 123456,
                "type": "spot",
                "state": "working",
                "list": [
                    {
                        "currency": "btc",
                        "type": "trade",
                        "balance": "0.80000000"
                    },
                    {
                        "currency": "btc",
                        "type": "frozen",
                        "balance": "0.20000000"
                    },
                    {
                        "currency": "usdt",
                        "type": "trade",
                        "balance": "9000.00"
                    },
                    {
                        "currency": "usdt",
                        "type": "frozen",
                        "balance": "1000.00"
                    }
                ]
            }
        }
        """
        if self.has_been_init_data:
            return

        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)

        # Extract data list
        data = self.balance_data.get("data", {})
        balance_list = data.get("list", [])

        # Aggregate by currency
        currency_balances = {}
        for item in balance_list:
            currency = from_dict_get_string(item, "currency").upper()
            balance_type = from_dict_get_string(item, "type")
            balance = from_dict_get_float(item, "balance")

            if currency not in currency_balances:
                currency_balances[currency] = {"trade": 0.0, "frozen": 0.0}

            if balance_type == "trade":
                currency_balances[currency]["trade"] = balance
            elif balance_type == "frozen":
                currency_balances[currency]["frozen"] = balance

        # Set available and used margin for the requested symbol
        if self.symbol_name and self.symbol_name.upper() in currency_balances:
            balances = currency_balances[self.symbol_name.upper()]
            self.available_margin = balances["trade"]
            self.used_margin = balances["frozen"]
        elif self.symbol_name:
            # Symbol not found in balance
            self.available_margin = 0.0
            self.used_margin = 0.0
        else:
            # Return total USDT balance if no symbol specified
            if "USDT" in currency_balances:
                self.available_margin = currency_balances["USDT"]["trade"]
                self.used_margin = currency_balances["USDT"]["frozen"]
            else:
                self.available_margin = 0.0
                self.used_margin = 0.0

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "account_type": self.account_type,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "available_margin": self.available_margin,
                "used_margin": self.used_margin,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return None

    def get_local_update_time(self):
        return self.local_update_time

    def get_account_id(self):
        return None

    def get_account_type(self):
        return self.account_type

    def get_available_margin(self):
        self.init_data()
        return self.available_margin

    def get_used_margin(self):
        self.init_data()
        return self.used_margin
