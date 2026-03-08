"""
Coinbase Account Data Container
"""

import json
import time

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class CoinbaseAccountData(AccountData):
    """保存Coinbase账户信息"""

    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.account_id = None
        self.currency = None
        self.balance = None
        self.available = None
        self.hold = None
        self.last_activity = None
        self.native_balance = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        try:
            # Parse account data
            if isinstance(self.account_data, dict):
                # Single account structure
                if "uuid" in self.account_data:
                    self.account_id = from_dict_get_string(self.account_data, "uuid")
                    self.currency = from_dict_get_string(self.account_data, "currency")

                    # Balance information
                    balance_info = self.account_data.get("available_balance", {})
                    self.available = from_dict_get_float(balance_info, "value", 0)

                    hold_info = self.account_data.get("hold", {})
                    self.hold = from_dict_get_float(hold_info, "value", 0)

                    self.balance = self.available + self.hold
                    self.native_balance = from_dict_get_float(
                        self.account_data, "native_balance", 0
                    )

                    self.last_activity = from_dict_get_string(self.account_data, "updated_at")

                # Portfolio structure
                elif "accounts" in self.account_data:
                    # Handle multiple accounts
                    for account in self.account_data["accounts"]:
                        if account.get("currency") == self.symbol_name.split("-")[0]:
                            self.account_id = from_dict_get_string(account, "uuid")
                            self.currency = from_dict_get_string(account, "currency")

                            balance_info = account.get("available_balance", {})
                            self.available = from_dict_get_float(balance_info, "value", 0)

                            hold_info = account.get("hold", {})
                            self.hold = from_dict_get_float(hold_info, "value", 0)

                            self.balance = self.available + self.hold
                            self.last_activity = from_dict_get_string(account, "updated_at")
                            break
        except Exception as e:
            print(f"Error parsing account data: {e}")
            self.account_data = {}
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "account_id": self.account_id,
                "currency": self.currency,
                "balance": self.balance,
                "available": self.available,
                "hold": self.hold,
                "last_activity": self.last_activity,
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

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_account_id(self):
        self.init_data()
        return self.account_id

    def get_currency(self):
        self.init_data()
        return self.currency

    def get_balance(self):
        self.init_data()
        return self.balance

    def get_available(self):
        self.init_data()
        return self.available

    def get_hold(self):
        self.init_data()
        return self.hold

    def get_last_activity(self):
        self.init_data()
        return self.last_activity

    def get_native_balance(self):
        self.init_data()
        return self.native_balance


class CoinbaseSpotWssAccountData(CoinbaseAccountData):
    """保存WebSocket现货账户信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        try:
            # WebSocket account data (outboundAccountPosition format)
            if isinstance(self.account_data, dict):
                if "accounts" in self.account_data:
                    for account in self.account_data["accounts"]:
                        self.currency = from_dict_get_string(account, "currency")
                        self.available = from_dict_get_float(account, "available", {})
                        self.hold = from_dict_get_float(account, "hold", {})
                        self.balance = self.available + self.hold
                        self.last_activity = from_dict_get_string(account, "updated_at")
                        break
        except Exception as e:
            print(f"Error parsing WebSocket account data: {e}")
            self.account_data = {}
        self.has_been_init_data = True
        return self


class CoinbaseRequestAccountData(CoinbaseAccountData):
    """保存REST API账户信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        try:
            # REST API account data
            if isinstance(self.account_data, dict):
                if "account" in self.account_data:
                    account_info = self.account_data["account"]
                    self.account_id = from_dict_get_string(account_info, "uuid")
                    self.currency = from_dict_get_string(account_info, "currency")

                    balance_info = account_info.get("available_balance", {})
                    self.available = from_dict_get_float(balance_info, "value", 0)

                    hold_info = account_info.get("hold", {})
                    self.hold = from_dict_get_float(hold_info, "value", 0)

                    self.balance = self.available + self.hold
                    self.last_activity = from_dict_get_string(account_info, "updated_at")
                elif "accounts" in self.account_data:
                    # Handle multiple accounts
                    for account in self.account_data["accounts"]:
                        if account.get("currency") == self.symbol_name.split("-")[0]:
                            self.account_id = from_dict_get_string(account, "uuid")
                            self.currency = from_dict_get_string(account, "currency")

                            balance_info = account.get("available_balance", {})
                            self.available = from_dict_get_float(balance_info, "value", 0)

                            hold_info = account.get("hold", {})
                            self.hold = from_dict_get_float(hold_info, "value", 0)

                            self.balance = self.available + self.hold
                            self.last_activity = from_dict_get_string(account, "updated_at")
                            break
        except Exception as e:
            print(f"Error parsing REST account data: {e}")
            self.account_data = {}
        self.has_been_init_data = True
        return self
