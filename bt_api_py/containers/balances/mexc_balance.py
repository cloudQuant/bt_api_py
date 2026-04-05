"""MEXC Balance Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class MexcBalanceData(BalanceData):
    """MEXC balance data container.

    This class handles balance data from MEXC exchange.
    It stores balance information including asset, free, and locked amounts.

    Attributes:
        exchange_name: Exchange identifier ("MEXC").
        local_update_time: Local timestamp of last update.
        symbol_name: Trading symbol name.
        asset_type: Asset type for the balance.
        balance_data: Parsed balance data dictionary.
        asset: Asset symbol.
        free: Available balance amount.
        locked: Frozen balance amount.
        all_data: Cached dictionary of all balance data.
        has_been_init_data: Flag indicating if data has been initialized.
    """

    def __init__(
        self,
        balance_info: dict[str, Any] | str,
        symbol_name: str | None = None,
        asset_type: str | None = None,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize MEXC balance data container.

        Args:
            balance_info: Balance information from MEXC API (dict or JSON string).
            symbol_name: Trading symbol name.
            asset_type: Asset type for the balance.
            has_been_json_encoded: Whether balance_info is already JSON encoded.
        """
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "MEXC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.balance_data: dict[str, Any] | None = (
            balance_info if has_been_json_encoded and isinstance(balance_info, dict) else None
        )
        self.asset: str | None = None
        self.free: float | None = None
        self.locked: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> MexcBalanceData:  # type: ignore[override]
        """Initialize balance data from MEXC response.

        Parses the balance data and extracts asset, free, and locked amounts.

        Returns:
            Self for method chaining.
        """
        if not self.has_been_json_encoded:
            if isinstance(self.balance_info, str):
                self.balance_data = json.loads(self.balance_info)
            else:
                self.balance_data = self.balance_info

        if self.balance_data:
            self.asset = from_dict_get_string(self.balance_data, "asset")
            self.free = from_dict_get_float(self.balance_data, "free", 0.0)
            self.locked = from_dict_get_float(self.balance_data, "locked", 0.0)

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all balance data as a dictionary.

        Returns:
            Dictionary containing all balance information including exchange name,
            symbol name, asset type, local update time, asset, and balance amounts.
        """
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "asset": self.asset,
                "free": self.free,
                "locked": self.locked,
                "total": self.free + self.locked if self.free and self.locked else 0.0,
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
            Exchange identifier "MEXC".
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local timestamp when data was last updated.
        """
        return self.local_update_time

    def get_symbol_name(self) -> str | None:
        """Get symbol name.

        Returns:
            Trading symbol name or None.
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type for the balance or empty string if None.
        """
        return self.asset_type if self.asset_type is not None else ""

    def get_asset(self) -> str | None:
        """Get asset symbol.

        Returns:
            Asset symbol or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.asset

    def get_free(self) -> float | None:
        """Get free/available balance.

        Returns:
            Free balance amount or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.free

    def get_locked(self) -> float | None:
        """Get locked/frozen balance.

        Returns:
            Locked balance amount or None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.locked

    def get_total(self) -> float:
        """Get total balance (free + locked).

        Returns:
            Total balance amount. Returns 0.0 if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        if self.free is not None and self.locked is not None:
            return self.free + self.locked
        return 0.0

    def get_available(self) -> float:
        """Get available balance (same as free).

        Returns:
            Available balance amount. Returns 0.0 if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.free if self.free is not None else 0.0

    def get_frozen(self) -> float:
        """Get frozen balance (same as locked).

        Returns:
            Frozen balance amount. Returns 0.0 if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.locked if self.locked is not None else 0.0

    def is_zero(self) -> bool:
        """Check if balance is zero.

        Returns:
            True if total balance is zero, False otherwise.
        """
        return self.get_total() == 0.0

    def has_available(self) -> bool:
        """Check if there is available balance.

        Returns:
            True if available balance > 0, False otherwise.
        """
        return self.get_available() > 0.0

    def has_frozen(self) -> bool:
        """Check if there is frozen balance.

        Returns:
            True if frozen balance > 0, False otherwise.
        """
        return self.get_frozen() > 0.0


class MexcRequestBalanceData(MexcBalanceData):
    """MEXC REST API balance data container.

    Handles balance data received through MEXC REST API.
    """

    def __init__(
        self,
        balance_info: dict[str, Any] | str,
        symbol_name: str | None = None,
        asset_type: str | None = None,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize MEXC REST API balance data container.

        Args:
            balance_info: Balance information from MEXC API (dict or JSON string).
            symbol_name: Trading symbol name.
            asset_type: Asset type for the balance.
            has_been_json_encoded: Whether balance_info is already JSON encoded.
        """
        super().__init__(balance_info, symbol_name, asset_type, has_been_json_encoded)

    def init_data(self) -> MexcRequestBalanceData:  # type: ignore[override]
        """Initialize balance data from REST API response.

        Returns:
            Self for method chaining.
        """
        super().init_data()
        return self


class MexcAccountData(BalanceData):
    """MEXC account data container.

    This class handles account data from MEXC exchange including
    commissions, permissions, and multiple balances.

    Attributes:
        exchange_name: Exchange identifier ("MEXC").
        local_update_time: Local timestamp of last update.
        asset_type: Asset type for the account.
        account_data: Parsed account data dictionary.
        maker_commission: Maker commission rate.
        taker_commission: Taker commission rate.
        buyer_commission: Buyer commission rate.
        seller_commission: Seller commission rate.
        can_trade: Whether account can trade.
        can_withdraw: Whether account can withdraw.
        can_deposit: Whether account can deposit.
        account_type: Account type.
        balances: List of balance data.
        all_data: Cached dictionary of all account data.
        has_been_init_data: Flag indicating if data has been initialized.
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize MEXC account data container.

        Args:
            account_info: Account information from MEXC API (dict or JSON string).
            asset_type: Asset type for the account.
            has_been_json_encoded: Whether account_info is already JSON encoded.
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "MEXC"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data: dict[str, Any] | None = (
            account_info if has_been_json_encoded and isinstance(account_info, dict) else None
        )
        self.maker_commission: int | None = None
        self.taker_commission: int | None = None
        self.buyer_commission: int | None = None
        self.seller_commission: int | None = None
        self.can_trade: bool | None = None
        self.can_withdraw: bool | None = None
        self.can_deposit: bool | None = None
        self.account_type: str | None = None
        self.balances: list[MexcBalanceData] = []
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> MexcAccountData:  # type: ignore[override]
        """Initialize account data from MEXC response.

        Parses the MEXC account response and extracts commissions,
        permissions, and balances.

        Returns:
            Self for method chaining.
        """
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            if isinstance(self.account_info, str):
                self.account_data = json.loads(self.account_info)
            else:
                self.account_data = self.account_info
            self.has_been_json_encoded = True

        if self.account_data and isinstance(self.account_data, dict):
            self.maker_commission = int(
                from_dict_get_float(self.account_data, "makerCommission", 0)
            )
            self.taker_commission = int(
                from_dict_get_float(self.account_data, "takerCommission", 0)
            )
            self.buyer_commission = int(
                from_dict_get_float(self.account_data, "buyerCommission", 0)
            )
            self.seller_commission = int(
                from_dict_get_float(self.account_data, "sellerCommission", 0)
            )
            self.can_trade = self.account_data.get("canTrade", False)
            self.can_withdraw = self.account_data.get("canWithdraw", False)
            self.can_deposit = self.account_data.get("canDeposit", False)
            self.account_type = from_dict_get_string(self.account_data, "accountType")

            balances_data = self.account_data.get("balances", [])
            if isinstance(balances_data, list):
                for balance_dict in balances_data:
                    if isinstance(balance_dict, dict):
                        balance = MexcBalanceData(
                            balance_dict,
                            symbol_name=None,
                            asset_type=self.asset_type,
                            has_been_json_encoded=True,
                        )
                        balance.init_data()
                        self.balances.append(balance)

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all account data as a dictionary.

        Returns:
            Dictionary containing all account information including exchange name,
            asset type, local update time, commissions, permissions, and balances.
        """
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "maker_commission": self.maker_commission,
                "taker_commission": self.taker_commission,
                "buyer_commission": self.buyer_commission,
                "seller_commission": self.seller_commission,
                "can_trade": self.can_trade,
                "can_withdraw": self.can_withdraw,
                "can_deposit": self.can_deposit,
                "account_type": self.account_type,
                "balances": [b.get_all_data() for b in self.balances],
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange identifier "MEXC".
        """
        return self.exchange_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type for the account.
        """
        return self.asset_type

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local timestamp when data was last updated.
        """
        return self.local_update_time

    def get_maker_commission(self) -> int | None:
        """Get maker commission rate.

        Returns:
            Maker commission rate or None if not initialized.
        """
        self.init_data()
        return self.maker_commission

    def get_taker_commission(self) -> int | None:
        """Get taker commission rate.

        Returns:
            Taker commission rate or None if not initialized.
        """
        self.init_data()
        return self.taker_commission

    def get_buyer_commission(self) -> int | None:
        """Get buyer commission rate.

        Returns:
            Buyer commission rate or None if not initialized.
        """
        self.init_data()
        return self.buyer_commission

    def get_seller_commission(self) -> int | None:
        """Get seller commission rate.

        Returns:
            Seller commission rate or None if not initialized.
        """
        self.init_data()
        return self.seller_commission

    def get_can_trade(self) -> bool | None:
        """Check if account can trade.

        Returns:
            True if account can trade, False or None otherwise.
        """
        self.init_data()
        return self.can_trade

    def get_can_withdraw(self) -> bool | None:
        """Check if account can withdraw.

        Returns:
            True if account can withdraw, False or None otherwise.
        """
        self.init_data()
        return self.can_withdraw

    def get_can_deposit(self) -> bool | None:
        """Check if account can deposit.

        Returns:
            True if account can deposit, False or None otherwise.
        """
        self.init_data()
        return self.can_deposit

    def get_account_type(self) -> str:
        """Get account type.

        Returns:
            Account type or empty string if not initialized.
        """
        self.init_data()
        return self.account_type if self.account_type is not None else ""

    def get_balances(self) -> list[MexcBalanceData]:
        """Get all balances.

        Returns:
            List of MexcBalanceData objects.
        """
        self.init_data()
        return self.balances

    def get_balance_by_asset(self, asset: str) -> MexcBalanceData | None:
        """Get balance for a specific asset.

        Args:
            asset: Asset symbol to search for.

        Returns:
            MexcBalanceData for the asset or None if not found.
        """
        for balance in self.balances:
            if balance.get_asset() == asset:
                return balance
        return None

    def get_total_balance_by_asset(self, asset: str) -> float:
        """Get total balance for a specific asset.

        Args:
            asset: Asset symbol to search for.

        Returns:
            Total balance amount. Returns 0.0 if not found.
        """
        balance = self.get_balance_by_asset(asset)
        return balance.get_total() if balance else 0.0

    def get_available_balance_by_asset(self, asset: str) -> float:
        """Get available balance for a specific asset.

        Args:
            asset: Asset symbol to search for.

        Returns:
            Available balance amount. Returns 0.0 if not found.
        """
        balance = self.get_balance_by_asset(asset)
        return balance.get_available() if balance else 0.0

    def get_frozen_balance_by_asset(self, asset: str) -> float:
        """Get frozen balance for a specific asset.

        Args:
            asset: Asset symbol to search for.

        Returns:
            Frozen balance amount. Returns 0.0 if not found.
        """
        balance = self.get_balance_by_asset(asset)
        return balance.get_frozen() if balance else 0.0

    def get_usd_value(self, asset_price_map: dict[str, float]) -> float:
        """Calculate total USD value of all balances.

        Args:
            asset_price_map: Dictionary mapping asset symbols to their USD prices.

        Returns:
            Total USD value of all balances.
        """
        total_value = 0.0
        for balance in self.balances:
            asset = balance.get_asset()
            amount = balance.get_total()

            if asset == "USDT":
                total_value += amount
            elif asset in asset_price_map:
                total_value += amount * asset_price_map[asset]

        return total_value

    def has_sufficient_balance(self, asset: str, amount: float) -> bool:
        """Check if account has sufficient balance for an asset.

        Args:
            asset: Asset symbol to check.
            amount: Required amount.

        Returns:
            True if available balance >= required amount, False otherwise.
        """
        available_balance = self.get_available_balance_by_asset(asset)
        return available_balance >= amount
