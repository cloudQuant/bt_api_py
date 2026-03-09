"""Bitget Balance Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitgetBalanceData(BalanceData):
    """Bitget account balance data container."""

    def __init__(
        self,
        balance_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitget balance data.

        Args:
            balance_info: Raw balance data from Bitget API
            symbol_name: Trading symbol name
            asset_type: Asset type (e.g., 'SPOT', 'SWAP')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.coin: str | None = None
        self.coin_type: str | None = None
        self.available: float | None = None
        self.frozen: float | None = None
        self.stored: float | None = None
        self.value_usd: float | None = None
        self.equity: float | None = None
        self.margin_ratio: float | None = None
        self.pnl: float | None = None
        self.pnl_ratio: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BitgetBalanceData":
        """Initialize and parse the balance data.

        Returns:
            Self for method chaining
        """
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.coin = from_dict_get_string(self.balance_data, "coin")
        self.coin_type = from_dict_get_string(self.balance_data, "coinType")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.frozen = from_dict_get_float(self.balance_data, "frozen")
        self.stored = from_dict_get_float(self.balance_data, "stored")
        self.value_usd = from_dict_get_float(self.balance_data, "usdValue")
        self.equity = from_dict_get_float(self.balance_data, "eq")
        self.margin_ratio = from_dict_get_float(self.balance_data, "mr")
        self.pnl = from_dict_get_float(self.balance_data, "pnl")
        self.pnl_ratio = from_dict_get_float(self.balance_data, "pnlRatio")
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all balance data as a dictionary.

        Returns:
            Dictionary containing all balance data fields
        """
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "coin": self.coin,
                "coin_type": self.coin_type,
                "available": self.available,
                "frozen": self.frozen,
                "stored": self.stored,
                "value_usd": self.value_usd,
                "equity": self.equity,
                "margin_ratio": self.margin_ratio,
                "pnl": self.pnl,
                "pnl_ratio": self.pnl_ratio,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name string
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update time as Unix timestamp
        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Trading symbol name
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type string
        """
        return self.asset_type

    def get_coin(self) -> str | None:
        """Get coin symbol.

        Returns:
            Coin symbol or None
        """
        return self.coin

    def get_coin_type(self) -> str | None:
        """Get coin type.

        Returns:
            Coin type or None
        """
        return self.coin_type

    def get_available(self) -> float | None:
        """Get available balance.

        Returns:
            Available balance
        """
        self.init_data()
        return self.available

    def get_frozen(self) -> float | None:
        """Get frozen balance.

        Returns:
            Frozen balance
        """
        self.init_data()
        return self.frozen

    def get_stored(self) -> float | None:
        """Get stored balance.

        Returns:
            Stored balance
        """
        self.init_data()
        return self.stored

    def get_total(self) -> float:
        """Get total balance (available + frozen + stored).

        Returns:
            Total balance
        """
        self.init_data()
        return (self.available or 0) + (self.frozen or 0) + (self.stored or 0)

    def get_value_usd(self) -> float | None:
        """Get USD value.

        Returns:
            USD value
        """
        self.init_data()
        return self.value_usd

    def get_equity(self) -> float | None:
        """Get equity.

        Returns:
            Equity value
        """
        self.init_data()
        return self.equity

    def get_margin_ratio(self) -> float | None:
        """Get margin ratio.

        Returns:
            Margin ratio
        """
        self.init_data()
        return self.margin_ratio

    def get_pnl(self) -> float | None:
        """Get profit and loss.

        Returns:
            PNL value
        """
        self.init_data()
        return self.pnl

    def get_pnl_ratio(self) -> float | None:
        """Get PNL ratio.

        Returns:
            PNL ratio
        """
        self.init_data()
        return self.pnl_ratio

    def is_empty(self) -> bool:
        """Check if balance is empty.

        Returns:
            True if all balances are zero
        """
        self.init_data()
        total = self.get_total()
        return total == 0.0


class BitgetWssBalanceData(BitgetBalanceData):
    """Bitget WebSocket Balance Data."""

    def init_data(self) -> "BitgetWssBalanceData":
        """Initialize WebSocket balance data.

        Returns:
            Self for method chaining
        """
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.coin = from_dict_get_string(self.balance_data, "a")
        self.coin_type = from_dict_get_string(self.balance_data, "t")
        self.available = from_dict_get_float(self.balance_data, "b")
        self.frozen = from_dict_get_float(self.balance_data, "f")
        self.stored = from_dict_get_float(self.balance_data, "s")
        self.value_usd = from_dict_get_float(self.balance_data, "v")
        self.equity = from_dict_get_float(self.balance_data, "e")
        self.margin_ratio = from_dict_get_float(self.balance_data, "mr")
        self.pnl = from_dict_get_float(self.balance_data, "pnl")
        self.pnl_ratio = from_dict_get_float(self.balance_data, "pnlRatio")
        self.has_been_init_data = True
        return self


class BitgetRequestBalanceData(BitgetBalanceData):
    """Bitget REST API Balance Data."""

    def init_data(self) -> "BitgetRequestBalanceData":
        """Initialize REST API balance data.

        Returns:
            Self for method chaining
        """
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.coin = from_dict_get_string(self.balance_data, "coin")
        self.coin_type = from_dict_get_string(self.balance_data, "coinType")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.frozen = from_dict_get_float(self.balance_data, "frozen")
        self.stored = from_dict_get_float(self.balance_data, "stored")
        self.value_usd = from_dict_get_float(self.balance_data, "usdValue")
        self.equity = from_dict_get_float(self.balance_data, "equity")
        self.margin_ratio = from_dict_get_float(self.balance_data, "marginRatio")
        self.pnl = from_dict_get_float(self.balance_data, "pnl")
        self.pnl_ratio = from_dict_get_float(self.balance_data, "pnlRatio")
        self.has_been_init_data = True
        return self


class BitgetSpotWssAccountData:
    """Bitget Spot WebSocket Account Data."""

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitget WebSocket account data.

        Args:
            account_info: Raw account data from Bitget WebSocket
            symbol_name: Trading symbol name
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self._raw_account_info: dict[str, Any] | str = account_info
        self._account_data: dict[str, Any] | None = (
            account_info if has_been_json_encoded and isinstance(account_info, dict) else None
        )
        self.balances: list[BitgetWssBalanceData] = []
        self.has_been_init_data = False
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self) -> "BitgetSpotWssAccountData":
        """Initialize and parse account data.

        Returns:
            Self for method chaining
        """
        if not self.has_been_json_encoded:
            if isinstance(self._raw_account_info, (str, bytes)):
                self._account_data = json.loads(self._raw_account_info)
            elif isinstance(self._raw_account_info, dict):
                self._account_data = self._raw_account_info
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if (
            self._account_data
            and isinstance(self._account_data, dict)
            and "data" in self._account_data
            and isinstance(self._account_data.get("data"), dict)
            and "balances" in self._account_data["data"]
        ):
            for balance in self._account_data["data"]["balances"]:
                balance_data = BitgetWssBalanceData(
                    balance, self.symbol_name, self.asset_type, True
                )
                self.balances.append(balance_data)

        self.has_been_init_data = True
        return self

    def get_balances(self) -> list[BitgetWssBalanceData]:
        """Get all balances.

        Returns:
            List of BitgetBalanceData objects
        """
        self.init_data()
        return self.balances

    def get_balance(self, coin: str) -> BitgetWssBalanceData | None:
        """Get balance for specific coin.

        Args:
            coin: Coin symbol (e.g., "USDT", "BTC")

        Returns:
            Balance data or None if not found
        """
        self.init_data()
        for balance in self.balances:
            if balance.get_coin() == coin:
                return balance
        return None

    def get_total_equity(self) -> float:
        """Get total equity.

        Returns:
            Total equity across all balances
        """
        self.init_data()
        total = 0.0
        for balance in self.balances:
            equity = balance.get_equity()
            if equity is not None:
                total += equity
        return total
