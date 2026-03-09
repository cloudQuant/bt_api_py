"""Bitget Order Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitgetOrderData(OrderData):
    """Bitget order data container.

    This class holds order information from Bitget exchange.
    """

    def __init__(
        self,
        order_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bitget order data.

        Args:
            order_info: Order information from exchange (dict or JSON string)
            symbol_name: Symbol name for the order
            asset_type: Asset type (SPOT, FUTURE, etc.)
            has_been_json_encoded: Whether order_info is already JSON encoded
        """
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data: dict[str, Any] | None = order_info if has_been_json_encoded else None
        self.order_id: str | None = None
        self.client_order_id: str | None = None
        self.symbol: str | None = None
        self.side: str | None = None
        self.order_type: str | None = None
        self.status: str | None = None
        self.size: float | None = None
        self.filled_size: float | None = None
        self.remaining_size: float | None = None
        self.price: float | None = None
        self.avg_price: float | None = None
        self.create_time: float | None = None
        self.update_time: float | None = None
        self.done_at: float | None = None
        self.fee: float | None = None
        self.fee_currency: str | None = None
        self.stop_price: float | None = None
        self.trigger_price: float | None = None
        self.time_in_force: str | None = None
        self.post_only: str | None = None
        self.hidden: str | None = None
        self.reduce_only: str | None = None
        self.iceberg: str | None = None
        self.iceberg_size: float | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BitgetOrderData":
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

        self.order_id = from_dict_get_string(self.order_data, "orderId") or from_dict_get_string(
            self.order_data, "id"
        )
        self.client_order_id = from_dict_get_string(self.order_data, "clientOid")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "orderType")
        self.status = from_dict_get_string(self.order_data, "status")
        self.size = from_dict_get_float(self.order_data, "size")
        self.filled_size = from_dict_get_float(self.order_data, "filledSize")
        self.remaining_size = from_dict_get_float(self.order_data, "remainingSize")
        self.price = from_dict_get_float(self.order_data, "price")
        self.avg_price = from_dict_get_float(self.order_data, "avgPrice") or from_dict_get_float(
            self.order_data, "avgFillPrice"
        )
        self.create_time = from_dict_get_float(self.order_data, "cTime") or from_dict_get_float(
            self.order_data, "createTime"
        )
        self.update_time = from_dict_get_float(self.order_data, "uTime") or from_dict_get_float(
            self.order_data, "updateTime"
        )
        self.done_at = from_dict_get_float(self.order_data, "doneAt")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "feeCurrency")
        self.stop_price = from_dict_get_float(self.order_data, "stopPrice")
        self.trigger_price = from_dict_get_float(self.order_data, "triggerPrice")
        self.time_in_force = from_dict_get_string(self.order_data, "tif")
        self.post_only = from_dict_get_string(self.order_data, "postOnly")
        self.hidden = from_dict_get_string(self.order_data, "hidden")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly")
        self.iceberg = from_dict_get_string(self.order_data, "iceberg")
        self.iceberg_size = from_dict_get_float(self.order_data, "icebergSize")
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
                "client_order_id": self.client_order_id,
                "symbol": self.symbol,
                "side": self.side,
                "order_type": self.order_type,
                "status": self.status,
                "size": self.size,
                "filled_size": self.filled_size,
                "remaining_size": self.remaining_size,
                "price": self.price,
                "avg_price": self.avg_price,
                "create_time": self.create_time,
                "update_time": self.update_time,
                "done_at": self.done_at,
                "fee": self.fee,
                "fee_currency": self.fee_currency,
                "stop_price": self.stop_price,
                "trigger_price": self.trigger_price,
                "time_in_force": self.time_in_force,
                "post_only": self.post_only,
                "hidden": self.hidden,
                "reduce_only": self.reduce_only,
                "iceberg": self.iceberg,
                "iceberg_size": self.iceberg_size,
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
            Exchange name (BITGET)
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

    def get_order_id(self) -> str | None:
        """Get order ID.

        Returns:
            Order ID
        """
        return self.order_id

    def get_client_order_id(self) -> str | None:
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

    def get_side(self) -> str | None:
        """Get order side.

        Returns:
            Order side (buy/sell)
        """
        return self.side

    def get_order_type(self) -> str | None:
        """Get order type.

        Returns:
            Order type
        """
        return self.order_type

    def get_status(self) -> str | None:
        """Get order status.

        Returns:
            Order status
        """
        return self.status

    def get_size(self) -> float | None:
        """Get order size.

        Returns:
            Order size
        """
        return self.size

    def get_filled_size(self) -> float | None:
        """Get filled size.

        Returns:
            Filled size
        """
        return self.filled_size

    def get_remaining_size(self) -> float | None:
        """Get remaining size.

        Returns:
            Remaining size
        """
        return self.remaining_size

    def get_price(self) -> float | None:
        """Get order price.

        Returns:
            Order price
        """
        return self.price

    def get_avg_price(self) -> float | None:
        """Get average price.

        Returns:
            Average price
        """
        return self.avg_price

    def get_create_time(self) -> float | None:
        """Get creation time.

        Returns:
            Creation timestamp
        """
        return self.create_time

    def get_update_time(self) -> float | None:
        """Get update time.

        Returns:
            Update timestamp
        """
        return self.update_time

    def get_done_at(self) -> float | None:
        """Get done time.

        Returns:
            Done timestamp
        """
        return self.done_at

    def get_fee(self) -> float | None:
        """Get fee.

        Returns:
            Fee amount
        """
        return self.fee

    def get_fee_currency(self) -> str | None:
        """Get fee currency.

        Returns:
            Fee currency
        """
        return self.fee_currency

    def get_stop_price(self) -> float | None:
        """Get stop price.

        Returns:
            Stop price
        """
        return self.stop_price

    def get_trigger_price(self) -> float | None:
        """Get trigger price.

        Returns:
            Trigger price
        """
        return self.trigger_price

    def get_time_in_force(self) -> str | None:
        """Get time in force.

        Returns:
            Time in force
        """
        return self.time_in_force

    def get_post_only(self) -> str | None:
        """Get post only flag.

        Returns:
            Post only flag
        """
        return self.post_only

    def get_hidden(self) -> str | None:
        """Get hidden flag.

        Returns:
            Hidden flag
        """
        return self.hidden

    def get_reduce_only(self) -> str | None:
        """Get reduce only flag.

        Returns:
            Reduce only flag
        """
        return self.reduce_only

    def get_iceberg(self) -> str | None:
        """Get iceberg flag.

        Returns:
            Iceberg flag
        """
        return self.iceberg

    def get_iceberg_size(self) -> float | None:
        """Get iceberg size.

        Returns:
            Iceberg size
        """
        return self.iceberg_size


class BitgetWssOrderData(BitgetOrderData):
    """Bitget WebSocket order data container."""

    def init_data(self) -> "BitgetWssOrderData":
        """Initialize order data from WebSocket message.

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

        self.order_id = from_dict_get_string(self.order_data, "o")
        self.client_order_id = from_dict_get_string(self.order_data, "C")
        self.symbol = from_dict_get_string(self.order_data, "s")
        self.side = from_dict_get_string(self.order_data, "S")
        self.order_type = from_dict_get_string(self.order_data, "ot")
        self.status = from_dict_get_string(self.order_data, "X")
        self.size = from_dict_get_float(self.order_data, "z")
        self.filled_size = from_dict_get_float(self.order_data, "L")
        self.remaining_size = from_dict_get_float(self.order_data, "r")
        self.price = from_dict_get_float(self.order_data, "p")
        self.avg_price = from_dict_get_float(self.order_data, "P")
        self.create_time = from_dict_get_float(self.order_data, "O")
        self.update_time = from_dict_get_float(self.order_data, "E")
        self.done_at = from_dict_get_float(self.order_data, "T")
        self.fee = from_dict_get_float(self.order_data, "n")
        self.fee_currency = from_dict_get_string(self.order_data, "N")
        self.stop_price = from_dict_get_float(self.order_data, "SP")
        self.trigger_price = from_dict_get_float(self.order_data, "trdPx")
        self.time_in_force = from_dict_get_string(self.order_data, "tif")
        self.post_only = from_dict_get_string(self.order_data, "postOnly")
        self.hidden = from_dict_get_string(self.order_data, "h")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly")
        self.iceberg = from_dict_get_string(self.order_data, "ice")
        self.iceberg_size = from_dict_get_float(self.order_data, "iSz")
        self.has_been_init_data = True
        return self


class BitgetRequestOrderData(BitgetOrderData):
    """Bitget REST API order data container."""

    def init_data(self) -> "BitgetRequestOrderData":
        """Initialize order data from REST API response.

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

        self.order_id = from_dict_get_string(self.order_data, "orderId")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOid")
        self.symbol = from_dict_get_string(self.order_data, "symbol")
        self.side = from_dict_get_string(self.order_data, "side")
        self.order_type = from_dict_get_string(self.order_data, "orderType")
        self.status = from_dict_get_string(self.order_data, "status")
        self.size = from_dict_get_float(self.order_data, "size")
        self.filled_size = from_dict_get_float(self.order_data, "filledSize")
        self.remaining_size = from_dict_get_float(self.order_data, "remainingSize")
        self.price = from_dict_get_float(self.order_data, "price")
        self.avg_price = from_dict_get_float(self.order_data, "avgPrice")
        self.create_time = from_dict_get_float(self.order_data, "cTime")
        self.update_time = from_dict_get_float(self.order_data, "uTime")
        self.done_at = from_dict_get_float(self.order_data, "doneAt")
        self.fee = from_dict_get_float(self.order_data, "fee")
        self.fee_currency = from_dict_get_string(self.order_data, "feeCurrency")
        self.stop_price = from_dict_get_float(self.order_data, "stopPrice")
        self.trigger_price = from_dict_get_float(self.order_data, "triggerPrice")
        self.time_in_force = from_dict_get_string(self.order_data, "tif")
        self.post_only = from_dict_get_string(self.order_data, "postOnly")
        self.hidden = from_dict_get_string(self.order_data, "hidden")
        self.reduce_only = from_dict_get_string(self.order_data, "reduceOnly")
        self.iceberg = from_dict_get_string(self.order_data, "iceberg")
        self.iceberg_size = from_dict_get_float(self.order_data, "icebergSize")
        self.has_been_init_data = True
        return self
