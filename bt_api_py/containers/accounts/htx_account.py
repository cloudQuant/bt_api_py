"""HTX Account Data Container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.functions.utils import from_dict_get_float


class HtxSpotRequestAccountData(AccountData):
    """HTX REST API account data.

    This class handles account data from HTX (Huobi) exchange's REST API.
    It parses and stores account information including account ID, margins, etc.

    Attributes:
        exchange_name: Exchange identifier ("HTX").
        account_type: Account type ("SPOT").
        symbol_name: Trading symbol name.
        local_update_time: Local timestamp of last update.
        asset_type: Asset type for the account.
        account_data: Parsed account data dictionary.
        account_id: Account identifier.
        available_margin: Available margin amount.
        used_margin: Used margin amount.
        all_data: Cached dictionary of all account data.
        has_been_init_data: Flag indicating if data has been initialized.
    """

    def __init__(
        self,
        account_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize HTX account data container.

        Args:
            account_info: Account information from HTX API (dict or JSON string).
            symbol_name: Trading symbol name.
            asset_type: Asset type for the account.
            has_been_json_encoded: Whether account_info is already JSON encoded.
        """
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.account_type = "SPOT"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data: dict[str, Any] | None = (
            account_info if has_been_json_encoded and isinstance(account_info, dict) else None
        )
        self.account_id: str | None = None
        self.available_margin: float | None = None
        self.used_margin: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> None:
        """Initialize account data from HTX response.

        Parses the HTX account response format:
        {
            "status": "ok",
            "data": [
                {
                    "id": 123456,
                    "type": "spot",
                    "subtype": "",
                    "state": "working"
                }
            ]
        }
        """
        if self.has_been_init_data:
            return

        if not self.has_been_json_encoded:
            account_info_parsed = (
                json.loads(self.account_info)
                if isinstance(self.account_info, str)
                else self.account_info
            )
            self.account_data = account_info_parsed

        # Extract account data
        if self.account_data is None:
            self.has_been_init_data = True
            return

        data = self.account_data.get("data", [])
        if isinstance(data, list) and len(data) > 0:
            account = data[0]
            self.account_id = str(from_dict_get_float(account, "id"))

        self.has_been_init_data = True

    def get_all_data(self) -> dict[str, Any]:
        """Get all account data as a dictionary.

        Returns:
            Dictionary containing all account information including exchange name,
            account type, symbol name, timestamps, account ID, and margin data.
        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "account_type": self.account_type,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "account_id": self.account_id,
                "available_margin": self.available_margin,
                "used_margin": self.used_margin,
            }
        return self.all_data

    def __str__(self) -> str:
        """Return string representation of account data.

        Returns:
            JSON string of all account data.
        """
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Return representation of account data.

        Returns:
            Same as __str__.
        """
        return self.__str__()

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name "HTX".
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Trading symbol name.
        """
        return self.symbol_name

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

    def get_account_id(self) -> str | None:
        """Get account ID.

        Returns:
            Account identifier or None if not initialized.
        """
        self.init_data()
        return self.account_id

    def get_account_type(self) -> str:
        """Get account type.

        Returns:
            Account type "SPOT".
        """
        return self.account_type

    def get_available_margin(self) -> float | None:
        """Get available margin.

        Returns:
            Available margin amount or None.
        """
        return self.available_margin

    def get_used_margin(self) -> float | None:
        """Get used margin.

        Returns:
            Used margin amount or None.
        """
        return self.used_margin
