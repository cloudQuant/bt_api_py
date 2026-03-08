"""
Crypto.com Balance Data Container
"""

import time

from bt_api_py.containers.balances.balance import BalanceData


class CryptoComBalance(BalanceData):
    """Crypto.com specific balance implementation."""

    def __init__(
        self, balance_info, symbol_name=None, asset_type="SPOT", has_been_json_encoded=False
    ):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "CRYPTOCOM"
        self.account_type = "SPOT"
        self.symbol_name = symbol_name or balance_info.get("instrument_name", "")
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.available_margin = None
        self.used_margin = None
        self.position_initial_margin = None
        self.all_data = None
        self.has_been_init_data = False

    @classmethod
    def from_api_response(cls, data):
        """Create balance from API response."""
        instrument_name = data.get("instrument_name", "")
        return cls(
            currency=instrument_name,
            total_balance=float(data.get("total_balance", 0)),
            available_balance=float(data.get("total_available", 0)),
            frozen_balance=float(data.get("total_locked", 0)),
        )

    def to_dict(self):
        """Convert balance to dictionary format."""
        return {
            "currency": self.currency,
            "total_balance": self.total_balance,
            "available_balance": self.available_balance,
            "frozen_balance": self.frozen_balance,
        }
