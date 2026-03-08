"""
Coinbase Balance Data Container
"""

import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class CoinbaseBalanceData(BalanceData):
    """保存Coinbase余额信息"""

    def __init__(self, balance_info, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        # If already JSON encoded, parse it to dict; otherwise store raw
        if has_been_json_encoded:
            self.balance_data = (
                json.loads(balance_info) if isinstance(balance_info, str) else balance_info
            )
        else:
            self.balance_data = None
        self.currency = None
        self.available = None
        self.hold = None
        self.total = None
        self.native_balance = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        # Ensure balance_data is a dict
        if isinstance(self.balance_data, str):
            self.balance_data = json.loads(self.balance_data)
        if self.has_been_init_data:
            return self
        try:
            # Parse balance data
            if isinstance(self.balance_data, dict):
                # Single balance structure with nested available_balance/hold
                if "currency" in self.balance_data:
                    self.currency = from_dict_get_string(self.balance_data, "currency")
                    # Try nested available_balance structure
                    if "available_balance" in self.balance_data:
                        available_bal = self.balance_data.get("available_balance", {})
                        if isinstance(available_bal, dict):
                            self.available = from_dict_get_float(available_bal, "value")
                        else:
                            self.available = float(available_bal) if available_bal else None
                    else:
                        self.available = from_dict_get_float(self.balance_data, "available")

                    # Try nested hold structure
                    if "hold" in self.balance_data:
                        hold_val = self.balance_data.get("hold")
                        if isinstance(hold_val, dict):
                            self.hold = from_dict_get_float(hold_val, "value")
                        else:
                            self.hold = float(hold_val) if hold_val else None
                    else:
                        self.hold = from_dict_get_float(self.balance_data, "hold")

                    # Calculate total
                    if self.available is not None:
                        self.available = float(self.available)
                    if self.hold is not None:
                        self.hold = float(self.hold)
                    if self.available is not None and self.hold is not None:
                        self.total = self.available + self.hold
                    elif self.available is not None:
                        self.total = self.available

                    self.native_balance = from_dict_get_float(self.balance_data, "native_balance")
                # Account structure
                elif "accounts" in self.balance_data:
                    # Handle account structure
                    for account in self.balance_data["accounts"]:
                        self.currency = from_dict_get_string(account, "currency")
                        # Try nested available_balance structure
                        if "available_balance" in account:
                            available_bal = account.get("available_balance", {})
                            if isinstance(available_bal, dict):
                                self.available = from_dict_get_float(available_bal, "value")
                            else:
                                self.available = float(available_bal) if available_bal else None
                        else:
                            self.available = from_dict_get_float(account, "available")

                        # Try nested hold structure
                        if "hold" in account:
                            hold_val = account.get("hold")
                            if isinstance(hold_val, dict):
                                self.hold = from_dict_get_float(hold_val, "value")
                            else:
                                self.hold = float(hold_val) if hold_val else None

                        # Calculate total
                        if self.available is not None:
                            self.available = float(self.available)
                        if self.hold is not None:
                            self.hold = float(self.hold)
                        if self.available is not None and self.hold is not None:
                            self.total = self.available + self.hold
                        elif self.available is not None:
                            self.total = self.available
                        break
        except Exception as e:
            print(f"Error parsing balance data: {e}")
            self.balance_data = {}
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "currency": self.currency,
                "available": self.available,
                "hold": self.hold,
                "total": self.total,
                "native_balance": self.native_balance,
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
        self.init_data()
        return self.currency

    def get_available(self):
        self.init_data()
        return self.available

    def get_hold(self):
        self.init_data()
        return self.hold

    def get_total(self):
        self.init_data()
        return self.total

    def get_native_balance(self):
        self.init_data()
        return self.native_balance


class CoinbaseWssBalanceData(CoinbaseBalanceData):
    """保存WebSocket余额信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        # Ensure balance_data is a dict
        if isinstance(self.balance_data, str):
            self.balance_data = json.loads(self.balance_data)
        if self.has_been_init_data:
            return self
        try:
            # WebSocket balance data (balanceUpdate format)
            if isinstance(self.balance_data, dict):
                if "currency" in self.balance_data:
                    self.currency = from_dict_get_string(self.balance_data, "currency")
                    self.available = from_dict_get_float(self.balance_data, "available")
                    self.hold = from_dict_get_float(self.balance_data, "hold")
                    self.total = self.available + self.hold
        except Exception as e:
            print(f"Error parsing WebSocket balance data: {e}")
            self.balance_data = {}
        self.has_been_init_data = True
        return self


class CoinbaseRequestBalanceData(CoinbaseBalanceData):
    """保存REST API余额信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        # Ensure balance_data is a dict
        if isinstance(self.balance_data, str):
            self.balance_data = json.loads(self.balance_data)
        if self.has_been_init_data:
            return self
        try:
            # REST API balance data
            if isinstance(self.balance_data, dict):
                if "balances" in self.balance_data:
                    # Handle account balances structure
                    for balance in self.balance_data["balances"]:
                        self.currency = from_dict_get_string(balance, "currency")
                        self.available = from_dict_get_float(balance, "available")
                        self.hold = from_dict_get_float(balance, "hold")
                        self.total = from_dict_get_float(balance, "total")
                        break
        except Exception as e:
            print(f"Error parsing REST balance data: {e}")
            self.balance_data = {}
        self.has_been_init_data = True
        return self
