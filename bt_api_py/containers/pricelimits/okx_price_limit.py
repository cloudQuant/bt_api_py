"""
OKX Price Limit Data Container.
"""

import json
import time
from typing import Any

from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxPriceLimitData:
    """OKX price limit data from price-limit channel.

    Represents the buy/sell limit prices for an instrument.

    Attributes:
        event: Event type identifier.
        price_limit_info: Raw price limit information.
        has_been_json_encoded: Whether data has been JSON decoded.
        exchange_name: Exchange identifier.
        local_update_time: Local timestamp of data update.
        asset_type: Asset type (spot, swap, etc.).
        symbol_name: Trading symbol name.
    """

    def __init__(
        self,
        price_limit_info: str | dict,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize OKX price limit data container.

        Args:
            price_limit_info: Raw price limit information (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (spot, swap, etc.).
            has_been_json_encoded: Whether data is already JSON decoded.
        """
        self.event = "PriceLimitEvent"
        self.price_limit_info = price_limit_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name = "OKX"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.price_limit_data = price_limit_info if has_been_json_encoded else None
        self.has_been_init_data = False

        self.server_time: float | None = None
        self.price_limit_symbol_name: str | None = None
        self.buy_limit: float | None = None
        self.sell_limit: float | None = None
        self.all_data: dict[str, Any] | None = None

    def init_data(self) -> "OkxPriceLimitData":
        """Initialize and parse price limit data.

        Returns:
            Self for method chaining.
        """
        if not self.has_been_json_encoded:
            if isinstance(self.price_limit_info, str):
                self.price_limit_data = json.loads(self.price_limit_info)
            else:
                self.price_limit_data = self.price_limit_info
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.server_time = from_dict_get_float(self.price_limit_data, "ts")
        self.price_limit_symbol_name = from_dict_get_string(self.price_limit_data, "instId")
        self.buy_limit = from_dict_get_float(self.price_limit_data, "buyLmt")
        self.sell_limit = from_dict_get_float(self.price_limit_data, "sellLmt")

        self.has_been_init_data = True
        return self

        self.server_time = from_dict_get_float(self.price_limit_data, "ts")
        self.price_limit_symbol_name = from_dict_get_string(self.price_limit_data, "instId")
        self.buy_limit = from_dict_get_float(self.price_limit_data, "buyLmt")
        self.sell_limit = from_dict_get_float(self.price_limit_data, "sellLmt")

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all price limit data as dictionary.

        Returns:
            Dictionary containing all price limit fields.
        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "price_limit_symbol_name": self.price_limit_symbol_name,
                "buy_limit": self.buy_limit,
                "sell_limit": self.sell_limit,
            }
        return self.all_data

    def get_event(self) -> str:
        """Get event type.

        Returns:
            Event type string.
        """
        return self.event

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name string.
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update time as timestamp.
        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Symbol name string.
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type string.
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """Get server timestamp.

        Returns:
            Server time as float, or None if not initialized.
        """
        return self.server_time

    def get_price_limit_symbol_name(self) -> str | None:
        """Get price limit symbol name.

        Returns:
            Price limit symbol name, or None if not initialized.
        """
        return self.price_limit_symbol_name

    def get_buy_limit(self) -> float | None:
        """Get buy limit price.

        Returns:
            Buy limit price, or None if not initialized.
        """
        return self.buy_limit

    def get_sell_limit(self) -> float | None:
        """Get sell limit price.

        Returns:
            Sell limit price, or None if not initialized.
        """
        return self.sell_limit

    def __str__(self) -> str:
        """String representation.

        Returns:
            JSON string of all data.
        """
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Official string representation.

        Returns:
            Same as __str__.
        """
        return self.__str__()
