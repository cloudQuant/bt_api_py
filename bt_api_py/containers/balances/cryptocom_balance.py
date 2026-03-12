"""
Crypto.com Balance Data Container
"""

import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData


class CryptoComBalance(BalanceData):
    """Crypto.com specific balance implementation."""

    def __init__(
        self,
        balance_info: dict[str, Any] | None = None,
        symbol_name: str | None = None,
        asset_type: str = "SPOT",
        has_been_json_encoded: bool = False,
    ) -> None:
        info = balance_info or {}
        super().__init__(info, has_been_json_encoded)
        self.exchange_name = "CRYPTOCOM"
        self.account_type = "SPOT"
        self.symbol_name = symbol_name or info.get("instrument_name", "")
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data = info if has_been_json_encoded else None
        self.available_margin: float | None = None
        self.used_margin: float | None = None
        self.position_initial_margin: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False
        self.currency: str | None = None
        self.total_balance: float | None = None
        self.available_balance: float | None = None
        self.frozen_balance: float | None = None

    def init_data(self) -> None:
        """Parse balance_info into currency and balance amounts."""
        if self.has_been_init_data:
            return
        data = self.balance_info or {}
        self.currency = data.get("instrument_name", "") or None
        self.total_balance = float(data.get("total_balance", 0))
        self.available_balance = float(data.get("total_available", 0))
        self.frozen_balance = float(data.get("total_locked", 0))
        self.has_been_init_data = True

    @classmethod
    def from_api_response(cls, data: dict) -> "CryptoComBalance":
        """Create balance from API response."""
        data = data or {}
        balance_info = {
            "instrument_name": data.get("instrument_name", ""),
            "total_balance": float(data.get("total_balance", 0)),
            "total_available": float(data.get("total_available", 0)),
            "total_locked": float(data.get("total_locked", 0)),
        }
        return cls(balance_info=balance_info)

    def to_dict(self) -> dict[str, Any]:
        """Convert balance to dictionary format."""
        if not self.has_been_init_data:
            self.init_data()
        return {
            "currency": self.currency,
            "total_balance": self.total_balance,
            "available_balance": self.available_balance,
            "frozen_balance": self.frozen_balance,
        }
