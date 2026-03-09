import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HitBtcRequestBalanceData(BalanceData):
    """保存HitBTC账户余额信息."""

    def __init__(
        self,
        balance_info: Any,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """
        Initialize HitBTC balance data.

        Args:
            balance_info: Balance information dictionary.
            symbol_name: Symbol name.
            asset_type: Asset type.
            has_been_json_encoded: Whether data has been JSON encoded.

        Returns:
            None
        """
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        # Always store balance_info for init_data() to parse
        self.balance_data = balance_info
        self.currency: str | None = None
        self.available: float | None = None
        self.reserved: float | None = None
        self.timestamp: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> None:
        """
        Initialize data from balance_info.

        Returns:
            None
        """
        if self.has_been_init_data:
            return

        if self.balance_data is None:
            return

        # 提取数据
        self.currency = from_dict_get_string(self.balance_data, "currency")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.reserved = from_dict_get_float(self.balance_data, "reserved")
        self.timestamp = from_dict_get_float(self.balance_data, "timestamp")

        self.has_been_init_data = True

    def get_all_data(self) -> dict[str, Any]:
        """
        Get all balance data as dictionary.

        Returns:
            Dictionary containing all balance data.
        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "currency": self.currency,
                "available": self.available,
                "reserved": self.reserved,
                "timestamp": self.timestamp,
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        """
        Get exchange name.

        Returns:
            Exchange name string.
        """
        return self.exchange_name

    def get_asset_type(self) -> str:
        """
        Get asset type.

        Returns:
            Asset type string.
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """
        Get server time.

        Returns:
            Server timestamp or None.
        """
        return self.timestamp

    def get_local_update_time(self) -> float:
        """
        Get local update time.

        Returns:
            Local update timestamp.
        """
        return self.local_update_time

    def get_currency(self) -> str | None:
        """
        Get currency.

        Returns:
            Currency string or None.
        """
        return self.currency

    def get_total(self) -> float:
        """
        Get total balance.

        Returns:
            Total balance amount.
        """
        return (self.available or 0) + (self.reserved or 0)

    def get_free(self) -> float:
        """
        Get available balance.

        Returns:
            Available balance amount.
        """
        return self.available or 0

    def get_used(self) -> float:
        """
        Get used balance.

        Returns:
            Used balance amount.
        """
        return self.reserved or 0

    def is_zero(self) -> bool:
        """
        Check if balance is zero.

        Returns:
            True if balance is zero, False otherwise.
        """
        total = self.get_total()
        return abs(total) < 1e-8

    def __str__(self) -> str:
        return (
            f"HITBTC Balance {self.currency}: Available={self.available}, Reserved={self.reserved}"
        )

    def __repr__(self) -> str:
        return f"<HitBtcBalanceData {self.currency} {self.get_total()}>"
