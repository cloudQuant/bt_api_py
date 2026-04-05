"""KuCoin account data container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class KuCoinAccountData(AccountData):
    """Base class for KuCoin account data."""

    def __init__(
        self,
        account_info: Any,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize KuCoin account data.

        Args:
            account_info: Raw account information (dict or JSON string).
            symbol_name: Trading symbol or currency name.
            asset_type: Asset type (e.g., "SPOT", "MARGIN").
            has_been_json_encoded: Whether account_info is already JSON encoded.

        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "KUCOIN"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data: Any = account_info if has_been_json_encoded else None
        self.server_time: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

        # Account type (main, trade, margin)
        self.account_type: str | None = None

        # Balance fields
        self.total_balance: float | None = None
        self.available_balance: float | None = None
        self.frozen_balance: float | None = None

        # Currency
        self.currency: str | None = None

    def init_data(self) -> KuCoinAccountData:
        """Initialize and parse account data.

        Returns:
            Self for method chaining.

        Raises:
            NotImplementedError: Subclasses must implement this method.

        """
        raise NotImplementedError("Subclasses must implement init_data")

    def get_all_data(self) -> dict[str, Any]:
        """Get all account data as dictionary.

        Returns:
            Dictionary containing all account fields.

        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "account_type": self.account_type,
                "currency": self.currency,
                "total_balance": self.total_balance,
                "available_balance": self.available_balance,
                "frozen_balance": self.frozen_balance,
            }
        return self.all_data

    def __str__(self) -> str:
        """Return string representation of account data."""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Return representation of account data."""
        return self.__str__()

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name "KUCOIN".

        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update time as Unix timestamp.

        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """Get symbol or currency name.

        Returns:
            Symbol or currency name.

        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type (e.g., "SPOT", "MARGIN").

        """
        return self.asset_type

    def get_server_time(self) -> int | float:
        """Get server timestamp.

        Returns:
            Server time as Unix timestamp in milliseconds.

        """
        return self.server_time if self.server_time is not None else 0

    def get_account_type(self) -> str:
        """Get account type.

        Returns:
            Account type (e.g., "main", "trade", "margin").

        """
        return self.account_type if self.account_type is not None else ""

    def get_currency(self) -> str | None:
        """Get currency name.

        Returns:
            Currency name (e.g., "BTC", "USDT"), or None if not available.

        """
        return self.currency


class KuCoinRequestAccountData(KuCoinAccountData):
    """KuCoin REST API account data.

    API response format for GET /api/v1/accounts:
    {
        "code": "200000",
        "data": [
            {
                "id": "5bd6e9286d99522a52e458de",
                "currency": "BTC",
                "type": "trade",
                "balance": "1.00000000",
                "available": "0.80000000",
                "holds": "0.20000000"
            }
        ]
    }
    """

    def init_data(self) -> None:
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data field
        if isinstance(self.account_data, dict) and "data" in self.account_data:
            data = self.account_data["data"]
        else:
            data = self.account_data

        # Handle both single account and list of accounts
        if isinstance(data, list) and len(data) > 0:
            # Use first account
            data = data[0]

        self.currency = from_dict_get_string(data, "currency")
        self.account_type = from_dict_get_string(data, "type")
        self.total_balance = from_dict_get_float(data, "balance")
        self.available_balance = from_dict_get_float(data, "available")
        self.frozen_balance = from_dict_get_float(data, "holds")
        self.server_time = time.time() * 1000

        # Update symbol_name with currency if not set
        if self.symbol_name == "ALL" and self.currency:
            self.symbol_name = self.currency

        self.has_been_init_data = True
        return self

    def get_balances(self) -> Any:
        """Return list of balance objects.

        For single account response, returns list with one balance.
        For multi-account response, returns all balances.
        """
        from bt_api_py.containers.balances.kucoin_balance import KuCoinRequestBalanceData

        if isinstance(self.account_data, dict) and "data" in self.account_data:
            data = self.account_data["data"]
        else:
            data = self.account_data

        if isinstance(data, list):
            return [
                KuCoinRequestBalanceData(
                    json.dumps({"data": acc}),
                    acc.get("currency", self.symbol_name),
                    self.asset_type,
                    True,
                )
                for acc in data
            ]
        else:
            return [
                KuCoinRequestBalanceData(self.account_info, self.symbol_name, self.asset_type, True)
            ]


class KuCoinWssAccountData(KuCoinAccountData):
    """KuCoin WebSocket account data.

    WebSocket account message format:
    {
        "topic": "/account/balance",
        "type": "message",
        "subject": "balance",
        "data": {
            "available": "9000.00",
            "holds": "1000.00",
            "currency": "USDT",
            "balance": "10000.00"
        }
    }
    """

    def init_data(self) -> None:
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data from WebSocket message
        if isinstance(self.account_data, dict) and "data" in self.account_data:
            data = self.account_data["data"]
        else:
            data = self.account_data

        self.currency = from_dict_get_string(data, "currency")
        self.account_type = "trade"  # WebSocket doesn't specify type, assume trade
        self.total_balance = from_dict_get_float(data, "balance")
        self.available_balance = from_dict_get_float(data, "available")
        self.frozen_balance = from_dict_get_float(data, "holds")
        self.server_time = time.time() * 1000

        if self.symbol_name == "ALL" and self.currency:
            self.symbol_name = self.currency

        self.has_been_init_data = True
        return self

    def get_balances(self) -> Any:
        """Return list of balance objects."""
        from bt_api_py.containers.balances.kucoin_balance import KuCoinWssBalanceData

        return [KuCoinWssBalanceData(self.account_info, self.symbol_name, self.asset_type, True)]
