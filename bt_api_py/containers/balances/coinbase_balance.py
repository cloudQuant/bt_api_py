"""Coinbase Balance Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class CoinbaseBalanceData(BalanceData):
    """Coinbase balance data container.

    This class handles balance data from Coinbase exchange.
    It parses and stores balance information including currency, available,
    hold, and total amounts.

    Attributes:
        exchange_name: Exchange identifier ("COINBASE").
        local_update_time: Local timestamp of last update.
        asset_type: Asset type for the balance.
        balance_data: Parsed balance data dictionary.
        currency: Currency symbol.
        available: Available balance amount.
        hold: Hold/frozen balance amount.
        total: Total balance amount.
        native_balance: Native balance information.
        all_data: Cached dictionary of all balance data.
        has_been_init_data: Flag indicating if data has been initialized.
    """

    def __init__(
        self,
        balance_info: dict[str, Any] | str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Coinbase balance data container.

        Args:
            balance_info: Balance information from Coinbase API (dict or JSON string).
            asset_type: Asset type for the balance.
            has_been_json_encoded: Whether balance_info is already JSON encoded.
        """
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        if has_been_json_encoded:
            self.balance_data: dict[str, Any] | None = (
                json.loads(balance_info) if isinstance(balance_info, str) else balance_info
            )
        else:
            self.balance_data = None
        self.currency: str | None = None
        self.available: float | None = None
        self.hold: float | None = None
        self.total: float | None = None
        self.native_balance: dict[str, Any] | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "CoinbaseBalanceData":
        """Initialize balance data from Coinbase response.

        Parses the balance data structure and extracts currency,
        available, hold, and total amounts.

        Returns:
            Self for method chaining.
        """
        if not self.has_been_json_encoded:
            self.balance_data = (
                json.loads(self.balance_info)
                if isinstance(self.balance_info, str)
                else self.balance_info
            )
            self.has_been_json_encoded = True
        if isinstance(self.balance_data, str):
            self.balance_data = json.loads(self.balance_data)
        if self.has_been_init_data:
            return self
        try:
            if isinstance(self.balance_data, dict):
                if "currency" in self.balance_data:
                    self.currency = from_dict_get_string(self.balance_data, "currency")
                    if "available_balance" in self.balance_data:
                        available_bal = self.balance_data.get("available_balance", {})
                        if isinstance(available_bal, dict):
                            self.available = from_dict_get_float(available_bal, "value")
                        else:
                            self.available = float(available_bal) if available_bal else None
                    else:
                        self.available = from_dict_get_float(self.balance_data, "available")

                    if "hold" in self.balance_data:
                        hold_val = self.balance_data.get("hold")
                        if isinstance(hold_val, dict):
                            self.hold = from_dict_get_float(hold_val, "value")
                        else:
                            self.hold = float(hold_val) if hold_val else None
                    else:
                        self.hold = from_dict_get_float(self.balance_data, "hold")

                    if "total" in self.balance_data:
                        total_val = self.balance_data.get("total")
                        if isinstance(total_val, dict):
                            self.total = from_dict_get_float(total_val, "value")
                        else:
                            self.total = float(total_val) if total_val else None
                    else:
                        self.total = from_dict_get_float(self.balance_data, "total")

                    if "native_balance" in self.balance_data:
                        self.native_balance = self.balance_data.get("native_balance")
        except Exception as e:
            print(f"Error parsing REST balance data: {e}")
            self.balance_data = {}
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all balance data as a dictionary.

        Returns:
            Dictionary containing all balance information including exchange name,
            asset type, local update time, currency, and balance amounts.
        """
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "currency": self.currency,
                "available": self.available,
                "hold": self.hold,
                "total": self.total,
                "native_balance": self.native_balance,
            }
        return self.all_data

    def __str__(self) -> str:
        """Return string representation of balance data.

        Returns:
            JSON string of all balance data.
        """
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Return representation of balance data.

        Returns:
            Same as __str__.
        """
        return self.__str__()

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange identifier "COINBASE".
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local timestamp when data was last updated.
        """
        return self.local_update_time

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type for the balance.
        """
        return self.asset_type

    def get_currency(self) -> str | None:
        """Get currency symbol.

        Returns:
            Currency symbol or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.currency

    def get_available(self) -> float | None:
        """Get available balance.

        Returns:
            Available balance amount or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.available

    def get_hold(self) -> float | None:
        """Get hold/frozen balance.

        Returns:
            Hold balance amount or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.hold

    def get_total(self) -> float | None:
        """Get total balance.

        Returns:
            Total balance amount or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.total

    def get_native_balance(self) -> dict[str, Any] | None:
        """Get native balance information.

        Returns:
            Native balance dictionary or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.native_balance


class CoinbaseWssBalanceData(CoinbaseBalanceData):
    """Coinbase WebSocket balance data container.

    Handles balance data received through Coinbase WebSocket connection.
    """

    def init_data(self) -> "CoinbaseWssBalanceData":
        """Initialize balance data from WebSocket response.

        Returns:
            Self for method chaining.
        """
        if self.has_been_init_data:
            return self
        if not self.has_been_json_encoded:
            self.balance_data = (
                json.loads(self.balance_info)
                if isinstance(self.balance_info, str)
                else self.balance_info
            )
            self.has_been_json_encoded = True
        if isinstance(self.balance_data, str):
            self.balance_data = json.loads(self.balance_data)

        if isinstance(self.balance_data, dict):
            self.currency = from_dict_get_string(self.balance_data, "currency")
            self.available = from_dict_get_float(self.balance_data, "available")
            self.hold = from_dict_get_float(self.balance_data, "hold")
            self.total = from_dict_get_float(self.balance_data, "total")

        self.has_been_init_data = True
        return self


class CoinbaseRequestBalanceData(CoinbaseBalanceData):
    """Coinbase REST API balance data container.

    Handles balance data received through Coinbase REST API.
    """

    def init_data(self) -> "CoinbaseRequestBalanceData":
        """Initialize balance data from REST API response.

        Returns:
            Self for method chaining.
        """
        if self.has_been_init_data:
            return self
        if not self.has_been_json_encoded:
            self.balance_data = (
                json.loads(self.balance_info)
                if isinstance(self.balance_info, str)
                else self.balance_info
            )
            self.has_been_json_encoded = True
        if isinstance(self.balance_data, str):
            self.balance_data = json.loads(self.balance_data)

        if isinstance(self.balance_data, dict):
            self.currency = from_dict_get_string(self.balance_data, "currency")
            self.available = from_dict_get_float(self.balance_data, "available")
            self.hold = from_dict_get_float(self.balance_data, "hold")
            self.total = from_dict_get_float(self.balance_data, "total")

        self.has_been_init_data = True
        return self
