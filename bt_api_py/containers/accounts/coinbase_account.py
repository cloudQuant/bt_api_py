"""
Coinbase Account Data Container
"""

import json
import time
from typing import Any

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class CoinbaseAccountData(AccountData):
    """保存Coinbase账户信息"""

    def __init__(
        self,
        account_info: dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Coinbase账户数据。

        Args:
            account_info: 账户信息字典。
            symbol_name: 交易对名称。
            asset_type: 资产类型（如 SPOT、FUTURE 等）。
            has_been_json_encoded: 是否已经 JSON 编码，默认为 False。
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.account_id: str | None = None
        self.currency: str | None = None
        self.balance: float | None = None
        self.available: float | None = None
        self.hold: float | None = None
        self.last_activity: str | None = None
        self.native_balance: dict[str, Any] | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "CoinbaseAccountData":
        """初始化并解析账户数据。

        Returns:
            初始化后的 CoinbaseAccountData 实例。
        """
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
                    if "available_balance" in self.account_data:
                        balance_info = self.account_data["available_balance"]
                        self.available = from_dict_get_float(balance_info, "value", 0)

                    if "hold" in self.account_data:
                        hold_info = self.account_data["hold"]
                        self.hold = from_dict_get_float(hold_info, "value", 0)

                    if self.available is not None and self.hold is not None:
                        self.balance = self.available + self.hold
                    self.last_activity = from_dict_get_string(self.account_data, "updated_at")
        except Exception as e:
            print(f"Error parsing account data: {e}")
            self.account_data = {}
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """获取所有账户数据。

        Returns:
            包含所有账户信息的字典。
        """
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

    def __str__(self) -> str:
        """返回账户数据的字符串表示。

        Returns:
            JSON 格式的账户数据字符串。
        """
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """返回账户数据的字符串表示。

        Returns:
            账户数据的字符串表示。
        """
        return self.__str__()

    def get_exchange_name(self) -> str:
        """获取交易所名称。

        Returns:
            交易所名称 "COINBASE"。
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """获取本地更新时间戳。

        Returns:
            本地更新时间戳。
        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """获取交易对名称。

        Returns:
            交易对名称。
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型。

        Returns:
            资产类型（如 SPOT、FUTURE 等）。
        """
        return self.asset_type

    def get_account_id(self) -> str | None:
        """获取账户ID。

        Returns:
            账户ID。
        """
        self.init_data()
        return self.account_id

    def get_currency(self) -> str | None:
        """获取货币类型。

        Returns:
            货币类型。
        """
        self.init_data()
        return self.currency

    def get_balance(self) -> float | None:
        """获取账户余额。

        Returns:
            账户余额。
        """
        self.init_data()
        return self.balance

    def get_available(self) -> float | None:
        """获取可用余额。

        Returns:
            可用余额。
        """
        self.init_data()
        return self.available

    def get_hold(self) -> float | None:
        """获取冻结余额。

        Returns:
            冻结余额。
        """
        self.init_data()
        return self.hold

    def get_last_activity(self) -> str | None:
        """获取最后活动时间。

        Returns:
            最后活动时间。
        """
        self.init_data()
        return self.last_activity

    def get_native_balance(self) -> dict[str, Any] | None:
        """获取原生余额信息。

        Returns:
            原生余额信息字典。
        """
        self.init_data()
        return self.native_balance


class CoinbaseSpotWssAccountData(CoinbaseAccountData):
    """保存WebSocket账户信息"""

    def init_data(self) -> "CoinbaseSpotWssAccountData":
        """初始化并解析WebSocket账户数据。

        Returns:
            初始化后的 CoinbaseSpotWssAccountData 实例。
        """
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        try:
            # WebSocket account data
            if isinstance(self.account_data, dict):
                if "accounts" in self.account_data:
                    # Handle multiple accounts
                    for account in self.account_data["accounts"]:
                        if account.get("currency") == self.symbol_name.split("-")[0]:
                            self.account_id = from_dict_get_string(account, "uuid")
                            self.currency = from_dict_get_string(account, "currency")

                            if "available_balance" in account:
                                balance_info = account["available_balance"]
                                self.available = from_dict_get_float(balance_info, "value", 0)

                            if "hold" in account:
                                hold_info = account["hold"]
                                self.hold = from_dict_get_float(hold_info, "value", 0)

                            if self.available is not None and self.hold is not None:
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

    def init_data(self) -> "CoinbaseRequestAccountData":
        """初始化并解析REST API账户数据。

        Returns:
            初始化后的 CoinbaseRequestAccountData 实例。
        """
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

                    if self.available is not None and self.hold is not None:
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

                            if self.available is not None and self.hold is not None:
                                self.balance = self.available + self.hold
                            self.last_activity = from_dict_get_string(account, "updated_at")
                            break
        except Exception as e:
            print(f"Error parsing REST account data: {e}")
            self.account_data = {}
        self.has_been_init_data = True
        return self
