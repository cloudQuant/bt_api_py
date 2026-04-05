"""HTX Balance Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HtxRequestBalanceData(BalanceData):
    """HTX REST API balance data container.

    Args:
        balance_info: Balance information data
        symbol_name: Symbol name (e.g., "BTC")
        asset_type: Asset type (e.g., "SPOT")
        has_been_json_encoded: Whether data has been JSON encoded
    """

    def __init__(
        self,
        balance_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize HTX balance data.

        Args:
            balance_info: Balance information (dict or JSON string)
            symbol_name: Symbol name to query
            asset_type: Asset type
            has_been_json_encoded: Whether balance_info is already JSON encoded
        """
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.account_type = "SPOT"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data: dict[str, Any] | None = balance_info if has_been_json_encoded else None
        self.available_margin: float = 0.0
        self.used_margin: float = 0.0
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> None:
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
                    }
                ]
            }
        }

        Parses the balance list and aggregates by currency, separating trade and
        frozen amounts.
        """
        if self.has_been_init_data:
            return

        if not self.has_been_json_encoded and isinstance(self.balance_info, str):
            self.balance_data = json.loads(self.balance_info)

        if self.balance_data is None:
            self.has_been_init_data = True
            return

        data = self.balance_data.get("data", {})
        balance_list = data.get("list", [])

        currency_balances: dict[str, dict[str, float]] = {}
        for item in balance_list:
            currency_str = from_dict_get_string(item, "currency")
            balance_type = from_dict_get_string(item, "type")
            balance = from_dict_get_float(item, "balance")

            if currency_str:
                currency = currency_str.upper()
                if currency not in currency_balances:
                    currency_balances[currency] = {"trade": 0.0, "frozen": 0.0}

                if balance_type == "trade":
                    currency_balances[currency]["trade"] = balance
                elif balance_type == "frozen":
                    currency_balances[currency]["frozen"] = balance

        if self.symbol_name and self.symbol_name.upper() in currency_balances:
            balances = currency_balances[self.symbol_name.upper()]
            self.available_margin = balances["trade"]
            self.used_margin = balances["frozen"]
        elif self.symbol_name:
            self.available_margin = 0.0
            self.used_margin = 0.0
        else:
            if "USDT" in currency_balances:
                self.available_margin = currency_balances["USDT"]["trade"]
                self.used_margin = currency_balances["USDT"]["frozen"]
            else:
                self.available_margin = 0.0
                self.used_margin = 0.0

        self.has_been_init_data = True

    def get_all_data(self) -> dict[str, Any]:
        """Get all balance data as a dictionary.

        Returns:
            Dictionary containing all balance information
        """
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

    def __str__(self) -> str:
        """Return string representation of balance data.

        Returns:
            JSON string of all balance data
        """
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Return formal string representation.

        Returns:
            Same as __str__
        """
        return self.__str__()

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name string
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Symbol name string
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type string
        """
        return self.asset_type

    def get_server_time(self) -> float:
        """Get server time (not available for HTX).

        Returns:
            0.0 as server time is not available
        """
        return 0.0

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update time as Unix timestamp
        """
        return self.local_update_time

    def get_account_id(self) -> str:
        """Get account ID (not available for HTX).

        Returns:
            Empty string as account ID is not available
        """
        return ""

    def get_account_type(self) -> str:
        """Get account type.

        Returns:
            Account type string
        """
        return self.account_type

    def get_available_margin(self) -> float:
        """Get available margin balance.

        Returns:
            Available margin amount
        """
        self.init_data()
        return self.available_margin

    def get_used_margin(self) -> float:
        """Get used/frozen margin balance.

        Returns:
            Used margin amount
        """
        self.init_data()
        return self.used_margin
