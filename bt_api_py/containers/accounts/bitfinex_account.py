import json
import time
from typing import Any

from bt_api_py.containers.accounts.account import AccountData


class BitfinexSpotRequestAccountData(AccountData):
    """Bitfinex Spot Request Account Data"""

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitfinex account data.

        Args:
            account_info: Raw account data from Bitfinex API
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.account_id: str | None = None
        self.account_type: str | None = None
        self.currency: str | None = None
        self.balance: float | None = None
        self.available: float | None = None
        self.timestamp: str | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BitfinexSpotRequestAccountData":
        """Initialize and parse the account data.

        Returns:
            Self for method chaining
        """
        if not self.has_been_json_encoded:
            if isinstance(self.account_info, str):
                self.account_data = json.loads(self.account_info)
            else:
                self.account_data = self.account_info
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.account_data, dict):
            self.account_id = self.account_data.get("id")
            self.account_type = self.account_data.get("type")
            self.currency = self.account_data.get("currency")
            self.balance = self.account_data.get("balance")
            self.available = self.account_data.get("available")
            self.timestamp = self.account_data.get("timestamp")

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all account data as a dictionary.

        Returns:
            Dictionary containing all account data fields
        """
        if self.all_data is None:
            self.init_data()
            all_data: dict[str, Any] = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "account_id": self.account_id,
                "account_type": self.account_type,
                "currency": self.currency,
                "balance": self.balance,
                "available": self.available,
                "timestamp": self.timestamp,
            }
            self.all_data = all_data
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()


class BitfinexSpotWssAccountData(BitfinexSpotRequestAccountData):
    """Bitfinex Spot WebSocket Account Data"""
