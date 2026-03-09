import json
import time
from typing import Any

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class BitfinexOrderData(OrderData):
    """Bitfinex order data container.

    This class holds order information from Bitfinex exchange.
    """

    def __init__(
        self,
        order_info: dict[str, Any] | str | list[Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitfinex order data.

        Args:
            order_info: Order information from exchange (dict, list, or JSON string)
            symbol_name: Symbol name for the order
            asset_type: Asset type (SPOT, FUTURE, etc.)
            has_been_json_encoded: Whether order_info is already JSON encoded
        """
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data: dict[str, Any] | str | list[Any] | None = (
            order_info if has_been_json_encoded else None
        )
        self.order_id: int | None = None
        self.group_id: int | None = None
        self.client_order_id: int | None = None
        self.symbol: str | None = None
        self.mts_create: int | None = None
        self.mts_update: int | None = None
        self.amount: float | None = None
        self.amount_orig: float | None = None
        self.type: str | None = None
        self.type_prev: str | None = None
        self.flags: int | None = None
        self.status: str | None = None
        self.price: float | None = None
        self.price_avg: float | None = None
        self.price_trail: float | None = None
        self.price_aux_limit: float | None = None
        self.notify: int | None = None
        self.hidden: int | None = None
        self.placed_id: int | None = None
        self.routing: str | None = None
        self.meta: Any = None
        self.has_been_init_data = False

    def init_data(self) -> "BitfinexOrderData":
        """Initialize order data by parsing order_info.

        Returns:
            Self for method chaining
        """
        if not self.has_been_json_encoded:
            if isinstance(self.order_info, str):
                self.order_data = json.loads(self.order_info)
            else:
                self.order_data = self.order_info
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        if isinstance(self.order_data, list) and len(self.order_data) >= 16:
            self.order_id = from_dict_get_int(self.order_data[0], None)
            self.group_id = from_dict_get_int(self.order_data[1], None)
            self.client_order_id = from_dict_get_int(self.order_data[2], None)
            self.symbol = from_dict_get_string(self.order_data[3], "")
            self.mts_create = from_dict_get_int(self.order_data[4], None)
            self.mts_update = from_dict_get_int(self.order_data[5], None)
            self.amount = from_dict_get_float(self.order_data[6], 0.0)
            self.amount_orig = from_dict_get_float(self.order_data[7], 0.0)
            self.type = from_dict_get_string(self.order_data[8], "")
            self.type_prev = from_dict_get_string(self.order_data[9], "")
            self.flags = from_dict_get_int(self.order_data[10], 0)
            self.status = from_dict_get_string(self.order_data[11], "")
            self.price = from_dict_get_float(self.order_data[12], 0.0)
            self.price_avg = from_dict_get_float(self.order_data[13], 0.0)
            self.price_trail = from_dict_get_float(self.order_data[14], 0.0)
            self.price_aux_limit = from_dict_get_float(self.order_data[15], 0.0)

            if len(self.order_data) > 16:
                self.notify = from_dict_get_int(self.order_data[16], 0)
            if len(self.order_data) > 17:
                self.hidden = from_dict_get_int(self.order_data[17], 0)
            if len(self.order_data) > 18:
                self.placed_id = from_dict_get_int(self.order_data[18], None)
            if len(self.order_data) > 19:
                self.routing = from_dict_get_string(self.order_data[19], "")
            if len(self.order_data) > 20:
                self.meta = self.order_data[20]

        elif isinstance(self.order_data, dict):
            self.order_id = from_dict_get_int(self.order_data, "id", None)
            self.group_id = from_dict_get_int(self.order_data, "gid", None)
            self.client_order_id = from_dict_get_int(self.order_data, "cid", None)
            self.symbol = from_dict_get_string(self.order_data, "symbol", "")
            self.mts_create = from_dict_get_int(self.order_data, "mts_create", None)
            self.mts_update = from_dict_get_int(self.order_data, "mts_update", None)
            self.amount = from_dict_get_float(self.order_data, "amount", 0.0)
            self.amount_orig = from_dict_get_float(self.order_data, "amount_orig", 0.0)
            self.type = from_dict_get_string(self.order_data, "type", "")
            self.type_prev = from_dict_get_string(self.order_data, "type_prev", "")
            self.flags = from_dict_get_int(self.order_data, "flags", 0)
            self.status = from_dict_get_string(self.order_data, "status", "")
            self.price = from_dict_get_float(self.order_data, "price", 0.0)
            self.price_avg = from_dict_get_float(self.order_data, "price_avg", 0.0)
            self.price_trail = from_dict_get_float(self.order_data, "price_trail", 0.0)
            self.price_aux_limit = from_dict_get_float(self.order_data, "aux_price", 0.0)
            self.notify = from_dict_get_int(self.order_data, "notify", 0)
            self.hidden = from_dict_get_int(self.order_data, "hidden", 0)
            self.placed_id = from_dict_get_int(self.order_data, "placed_id", None)
            self.routing = from_dict_get_string(self.order_data, "routing", "")
            self.meta = self.order_data.get("meta")

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all order data as a dictionary.

        Returns:
            Dictionary containing all order data fields
        """
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "group_id": self.group_id,
                "client_order_id": self.client_order_id,
                "symbol": self.symbol,
                "mts_create": self.mts_create,
                "mts_update": self.mts_update,
                "amount": self.amount,
                "amount_orig": self.amount_orig,
                "type": self.type,
                "type_prev": self.type_prev,
                "flags": self.flags,
                "status": self.status,
                "price": self.price,
                "price_avg": self.price_avg,
                "price_trail": self.price_trail,
                "price_aux_limit": self.price_aux_limit,
                "notify": self.notify,
                "hidden": self.hidden,
                "placed_id": self.placed_id,
                "routing": self.routing,
                "meta": self.meta,
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
            Exchange name (BITFINEX)
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update timestamp
        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Symbol name
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type (SPOT, FUTURE, etc.)
        """
        return self.asset_type

    def get_order_id(self) -> int | None:
        """Get order ID.

        Returns:
            Order ID
        """
        return self.order_id

    def get_group_id(self) -> int | None:
        """Get group ID.

        Returns:
            Group ID
        """
        return self.group_id

    def get_client_order_id(self) -> int | None:
        """Get client order ID.

        Returns:
            Client order ID
        """
        return self.client_order_id

    def get_symbol(self) -> str | None:
        """Get symbol.

        Returns:
            Symbol
        """
        return self.symbol

    def get_mts_create(self) -> int | None:
        """Get creation timestamp.

        Returns:
            Creation timestamp in milliseconds
        """
        return self.mts_create

    def get_mts_update(self) -> int | None:
        """Get update timestamp.

        Returns:
            Update timestamp in milliseconds
        """
        return self.mts_update

    def get_amount(self) -> float | None:
        """Get current amount.

        Returns:
            Current amount (negative for sell)
        """
        return self.amount

    def get_amount_orig(self) -> float | None:
        """Get original amount.

        Returns:
            Original order amount
        """
        return self.amount_orig

    def get_type(self) -> str | None:
        """Get order type.

        Returns:
            Order type
        """
        return self.type

    def get_type_prev(self) -> str | None:
        """Get previous order type.

        Returns:
            Previous order type
        """
        return self.type_prev

    def get_flags(self) -> int | None:
        """Get order flags.

        Returns:
            Order flags
        """
        return self.flags

    def get_status(self) -> str | None:
        """Get order status.

        Returns:
            Order status
        """
        return self.status

    def get_price(self) -> float | None:
        """Get order price.

        Returns:
            Order price
        """
        return self.price

    def get_price_avg(self) -> float | None:
        """Get average price.

        Returns:
            Average execution price
        """
        return self.price_avg

    def get_price_trail(self) -> float | None:
        """Get trailing price.

        Returns:
            Trailing price for trailing stop orders
        """
        return self.price_trail

    def get_price_aux_limit(self) -> float | None:
        """Get auxiliary limit price.

        Returns:
            Auxiliary limit price
        """
        return self.price_aux_limit

    def get_notify(self) -> int | None:
        """Get notify flag.

        Returns:
            Notify flag
        """
        return self.notify

    def get_hidden(self) -> int | None:
        """Get hidden flag.

        Returns:
            Hidden flag (1 if order is hidden)
        """
        return self.hidden

    def get_placed_id(self) -> int | None:
        """Get placed ID.

        Returns:
            Placed ID
        """
        return self.placed_id

    def get_routing(self) -> str | None:
        """Get routing information.

        Returns:
            Routing information
        """
        return self.routing

    def get_meta(self) -> Any:
        """Get metadata.

        Returns:
            Metadata
        """
        return self.meta

    def get_side(self) -> str | None:
        """Get order side.

        Returns:
            Order side (BUY/SELL)
        """
        if self.type:
            if self.amount is not None and self.amount > 0:
                return "BUY"
            if self.type.upper().startswith("BUY"):
                return "BUY"
            return "SELL"
        return None

    def get_order_state(self) -> str | None:
        """Get order state.

        Returns:
            Order state
        """
        return self.status

    def is_active(self) -> bool:
        """Check if order is active.

        Returns:
            True if order is active
        """
        return self.status in ["ACTIVE", "PARTIALLY FILLED", "OPEN"]

    def is_filled(self) -> bool:
        """Check if order is completely filled.

        Returns:
            True if order is filled
        """
        return self.status == "FILLED"

    def is_cancelled(self) -> bool:
        """Check if order is cancelled.

        Returns:
            True if order is cancelled
        """
        return self.status == "CANCELED"

    def get_filled_amount(self) -> float:
        """Get filled amount.

        Returns:
            Filled amount
        """
        return (
            self.amount_orig - self.amount
            if self.amount_orig is not None and self.amount is not None
            else 0
        )

    def get_fill_percentage(self) -> float:
        """Get fill percentage.

        Returns:
            Fill percentage (0-100)
        """
        if self.amount_orig is None or self.amount_orig == 0:
            return 0
        filled = self.get_filled_amount()
        return (filled / self.amount_orig) * 100


class BitfinexWssOrderData(BitfinexOrderData):
    """Bitfinex WebSocket order data container."""

    pass


class BitfinexRequestOrderData(BitfinexOrderData):
    """Bitfinex REST API order data container."""

    pass
