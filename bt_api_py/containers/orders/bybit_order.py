"""Bybit Order Data Container."""

import time
from typing import Any

from bt_api_py.containers.orders.order import OrderData


class BybitOrderData(OrderData):
    """Bybit order data container.

    This class holds order information from Bybit exchange.
    """

    def __init__(
        self,
        order_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bybit order data.

        Args:
            order_info: Order information from exchange (dict or JSON string)
            symbol_name: Symbol name for the order
            asset_type: Asset type (SPOT, FUTURE, etc.)
            has_been_json_encoded: Whether order_info is already JSON encoded
        """
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data: dict[str, Any] | str | None = order_info if has_been_json_encoded else None
        self.order_id: str | None = None
        self.order_link_id: str | None = None
        self.status: str | None = None
        self.side: str | None = None
        self.order_type: str | None = None
        self.price: str | None = None
        self.qty: str | None = None
        self.filled_qty: str | None = None
        self.remaining_qty: str | None = None
        self.cum_exec_value: str | None = None
        self.avg_price: str | None = None
        self.create_time: str | None = None
        self.update_time: str | None = None
        self.time_in_force: str | None = None
        self.leaves_qty: str | None = None
        self.canceled_time: str | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BybitOrderData":
        """Initialize order data by parsing order_info.

        Returns:
            Self for method chaining
        """
        if self.has_been_init_data or self.order_data is None:
            return self

        if isinstance(self.order_data, str):
            return self

        try:
            result = self.order_data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                return self

            order = list_data[0]

            self.order_id = order.get("orderId")
            self.order_link_id = order.get("orderLinkId")
            self.status = order.get("orderStatus")
            self.side = order.get("side")
            self.order_type = order.get("orderType")

            self.price = order.get("price")
            self.qty = order.get("qty")
            self.filled_qty = order.get("cumExecQty")
            self.remaining_qty = order.get("leavesQty")

            self.cum_exec_value = order.get("cumExecValue")
            self.avg_price = order.get("avgPrice")

            self.create_time = order.get("createdTime")
            self.update_time = order.get("updatedTime")
            self.canceled_time = order.get("canceledTime")

            self.time_in_force = order.get("timeInForce")
            self.leaves_qty = order.get("leavesQty")

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error parsing Bybit order data: {e}")
            self.has_been_init_data = False
        return self

        if not isinstance(self.order_data, dict):
            return self

        try:
            result = self.order_data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                return self

            order = list_data[0]

            self.order_id = order.get("orderId")
            self.order_link_id = order.get("orderLinkId")
            self.status = order.get("orderStatus")
            self.side = order.get("side")
            self.order_type = order.get("orderType")

            self.price = order.get("price")
            self.qty = order.get("qty")
            self.filled_qty = order.get("cumExecQty")
            self.remaining_qty = order.get("leavesQty")

            self.cum_exec_value = order.get("cumExecValue")
            self.avg_price = order.get("avgPrice")

            self.create_time = order.get("createdTime")
            self.update_time = order.get("updatedTime")
            self.canceled_time = order.get("canceledTime")

            self.time_in_force = order.get("timeInForce")
            self.leaves_qty = order.get("leavesQty")

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error parsing Bybit order data: {e}")
            self.has_been_init_data = False
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
                "order_link_id": self.order_link_id,
                "status": self.status,
                "side": self.side,
                "order_type": self.order_type,
                "price": self.price,
                "qty": self.qty,
                "filled_qty": self.filled_qty,
                "remaining_qty": self.remaining_qty,
                "cum_exec_value": self.cum_exec_value,
                "avg_price": self.avg_price,
                "create_time": self.create_time,
                "update_time": self.update_time,
                "canceled_time": self.canceled_time,
                "time_in_force": self.time_in_force,
                "leaves_qty": self.leaves_qty,
            }
        return self.all_data

    def is_active(self) -> bool:
        """Check if order is active.

        Returns:
            True if order is active (New or PartiallyFilled)
        """
        return self.status in ["New", "PartiallyFilled"]

    def is_filled(self) -> bool:
        """Check if order is completely filled.

        Returns:
            True if order is filled
        """
        return self.status == "Filled"

    def is_canceled(self) -> bool:
        """Check if order is cancelled.

        Returns:
            True if order is cancelled, rejected, or expired
        """
        return self.status in ["Canceled", "Rejected", "Expired"]

    def get_fill_ratio(self) -> float:
        """Get fill ratio.

        Returns:
            Fill ratio (0.0-1.0)
        """
        if not self.qty or float(self.qty) == 0:
            return 0.0
        return float(self.filled_qty) / float(self.qty) if self.filled_qty else 0.0

    def __str__(self) -> str:
        """Return string representation of order.

        Returns:
            String representation
        """
        self.init_data()
        return (
            f"BybitOrder(id={self.order_id}, "
            f"symbol={self.symbol_name}, "
            f"side={self.side}, "
            f"type={self.order_type}, "
            f"status={self.status}, "
            f"qty={self.qty}, "
            f"filled={self.filled_qty})"
        )


class BybitSpotOrderData(BybitOrderData):
    """Bybit spot order data container."""

    def __init__(
        self,
        order_info: dict[str, Any] | str,
        symbol_name: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bybit spot order data.

        Args:
            order_info: Order information from exchange (dict or JSON string)
            symbol_name: Symbol name for the order
            has_been_json_encoded: Whether order_info is already JSON encoded
        """
        super().__init__(order_info, symbol_name, "spot", has_been_json_encoded)


class BybitSwapOrderData(BybitOrderData):
    """Bybit swap/future order data container."""

    def __init__(
        self,
        order_info: dict[str, Any] | str,
        symbol_name: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Bybit swap order data.

        Args:
            order_info: Order information from exchange (dict or JSON string)
            symbol_name: Symbol name for the order
            has_been_json_encoded: Whether order_info is already JSON encoded
        """
        super().__init__(order_info, symbol_name, "swap", has_been_json_encoded)
