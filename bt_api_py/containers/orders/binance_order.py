import json
import time
from typing import Any

from bt_api_py.containers.orders.order import OrderData, OrderStatus
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


class BinanceForceOrderData:
    """Binance force order data container.

    This class holds force order information from Binance exchange.
    """

    def __init__(
        self,
        order_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Binance force order data.

        Args:
            order_info: Order information from exchange (dict or JSON string)
            symbol_name: Symbol name for the order
            asset_type: Asset type (SPOT, FUTURE, etc.)
            has_been_json_encoded: Whether order_info is already JSON encoded

        """
        self.order_info = order_info
        self.exchange_name = "BINANCE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.has_been_json_encoded = has_been_json_encoded
        self.order_data: dict[str, Any] | None = None
        if has_been_json_encoded and isinstance(self.order_info, dict):
            self.order_data = self.order_info.get("o")
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False
        self.server_time: float | None = None
        self.order_symbol_name: str | None = None
        self.order_side: str | None = None
        self.order_type: str | None = None
        self.order_time_in_force: str | None = None
        self.order_price: float | None = None
        self.order_qty: float | None = None
        self.order_avg_price: float | None = None
        self.order_status: OrderStatus | None = None
        self.trade_time: float | None = None
        self.last_trade_volume: float | None = None
        self.total_trade_volume: float | None = None

    def init_data(self) -> None:
        """Initialize order data by parsing order_info."""
        if self.has_been_init_data:
            return
        if not self.has_been_json_encoded:
            if isinstance(self.order_info, str):
                self.order_info = json.loads(self.order_info)
            self.order_data = self.order_info["o"] if isinstance(self.order_info, dict) else None
        server_time_raw = from_dict_get_float(self.order_info, "E")
        self.server_time = float(server_time_raw) if server_time_raw is not None else None
        self.order_symbol_name = from_dict_get_string(self.order_data, "s")
        self.order_side = from_dict_get_string(self.order_data, "S")
        self.order_type = from_dict_get_string(self.order_data, "o")
        self.order_time_in_force = from_dict_get_string(self.order_data, "f")
        self.order_price = from_dict_get_float(self.order_data, "p")
        self.order_qty = from_dict_get_float(self.order_data, "q")
        self.order_avg_price = from_dict_get_float(self.order_data, "ap")
        self.order_status = OrderStatus.from_value(from_dict_get_string(self.order_data, "X"))
        self.trade_time = from_dict_get_float(self.order_data, "T")
        self.last_trade_volume = from_dict_get_float(self.order_data, "l")
        self.total_trade_volume = from_dict_get_float(self.order_data, "z")

    def get_all_data(self) -> dict[str, Any]:
        """Get all order data as a dictionary.

        Returns:
            Dictionary containing all order data fields

        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "order_symbol_name": self.order_symbol_name,
                "order_side": self.order_side,
                "order_type": self.order_type,
                "order_time_in_force": self.order_time_in_force,
                "order_price": self.order_price,
                "order_qty": self.order_qty,
                "order_avg_price": self.order_avg_price,
                "order_status": self.order_status.value if self.order_status else None,
                "trade_time": self.trade_time,
                "last_trade_volume": self.last_trade_volume,
                "total_trade_volume": self.total_trade_volume,
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
            Exchange name (BINANCE)

        """
        return self.exchange_name

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

    def get_server_time(self) -> float | None:
        """Get server timestamp.

        Returns:
            Server timestamp

        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update timestamp

        """
        return self.local_update_time

    def get_order_price(self) -> float | None:
        """Get order price.

        Returns:
            Order price

        """
        return self.order_price

    def get_order_qty(self) -> float | None:
        """Get order quantity.

        Returns:
            Order quantity

        """
        return self.order_qty

    def get_order_side(self) -> str | None:
        """Get order side.

        Returns:
            Order side (BUY/SELL)

        """
        return self.order_side

    def get_order_status(self) -> OrderStatus | None:
        """Get order status.

        Returns:
            Order status

        """
        return self.order_status

    def get_order_symbol_name(self) -> str | None:
        """Get order symbol name.

        Returns:
            Order symbol name

        """
        return self.order_symbol_name

    def get_order_time_in_force(self) -> str | None:
        """Get order time in force.

        Returns:
            Order time in force type

        """
        return self.order_time_in_force

    def get_order_type(self) -> str | None:
        """Get order type.

        Returns:
            Order type

        """
        return self.order_type

    def get_order_avg_price(self) -> float | None:
        """Get order average price.

        Returns:
            Average price

        """
        return self.order_avg_price

    def get_trade_time(self) -> float | None:
        """Get trade time.

        Returns:
            Trade timestamp

        """
        return self.trade_time

    def get_last_trade_volume(self) -> float | None:
        """Get last trade volume.

        Returns:
            Last trade volume

        """
        return self.last_trade_volume

    def get_total_trade_volume(self) -> float | None:
        """Get total trade volume.

        Returns:
            Total trade volume

        """
        return self.total_trade_volume


class BinanceOrderData(OrderData):
    """Binance order data container.

    This class holds order information from Binance exchange.
    """

    def __init__(
        self,
        order_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Binance order data.

        Args:
            order_info: Order information from exchange (dict or JSON string)
            symbol_name: Symbol name for the order
            asset_type: Asset type (SPOT, FUTURE, etc.)
            has_been_json_encoded: Whether order_info is already JSON encoded

        """
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_data = self.order_info if has_been_json_encoded else None
        self.server_time: float | None = None
        self.trade_id: float | None = None
        self.client_order_id: str | None = None
        self.cum_quote: float | None = None
        self.executed_qty: float | None = None
        self.order_id: str | None = None
        self.order_size: float | None = None
        self.order_price: float | None = None
        self.reduce_only: bool | None = None
        self.order_side: str | None = None
        self.order_status: OrderStatus | None = None
        self.order_symbol_name: str | None = None
        self.order_type: str | None = None
        self.order_time_in_force: str | None = None
        self.order_avg_price: float | None = None
        self.origin_order_type: str | None = None
        self.position_side: str | None = None
        self.close_position: bool | None = None
        self.trailing_stop_price: float | None = None
        self.trailing_stop_trigger_price: float | None = None
        self.trailing_stop_trigger_price_type: str | None = None
        self.trailing_stop_callback_rate: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BinanceOrderData":
        """Initialize order data by parsing order_info.

        Returns:
            Self for method chaining

        Raises:
            NotImplementedError: This method should be overridden by subclasses

        """
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        """Get all order data as a dictionary.

        Returns:
            Dictionary containing all order data fields

        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
                "order_symbol_name": self.order_symbol_name,
                "order_type": self.order_type,
                "order_status": self.order_status.value if self.order_status else None,
                "order_size": self.order_size,
                "order_price": self.order_price,
                "trade_id": self.trade_id,
                "position_side": self.position_side,
                "cum_quote": self.cum_quote,
                "executed_qty": self.executed_qty,
                "order_avg_price": self.order_avg_price,
                "reduce_only": self.reduce_only,
                "trailing_stop_callback_rate": self.trailing_stop_callback_rate,
                "trailing_stop_price": self.trailing_stop_price,
                "trailing_stop_trigger_price": self.trailing_stop_trigger_price,
                "trailing_stop_trigger_price_type": self.trailing_stop_trigger_price_type,
                "close_position": self.close_position,
                "origin_order_type": self.origin_order_type,
                "order_time_in_force": self.order_time_in_force,
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
            Exchange name (BINANCE)

        """
        return self.exchange_name

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

    def get_server_time(self) -> float | None:
        """Get server timestamp.

        Returns:
            Server timestamp

        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local update timestamp

        """
        return self.local_update_time

    def get_trade_id(self) -> float | None:
        """Get trade ID.

        Returns:
            Unique trade ID from exchange

        """
        return self.trade_id

    def get_client_order_id(self) -> str | None:
        """Get client order ID.

        Returns:
            Client custom order ID

        """
        return self.client_order_id

    def get_cum_quote(self) -> float | None:
        """Get cumulative quote amount.

        Returns:
            Cumulative quote amount

        """
        return self.cum_quote

    def get_executed_qty(self) -> float | None:
        """Get executed quantity.

        Returns:
            Executed quantity

        """
        return self.executed_qty

    def get_order_id(self) -> str | None:
        """Get order ID.

        Returns:
            Order ID

        """
        return self.order_id

    def get_order_size(self) -> float | None:
        """Get order size.

        Returns:
            Original order quantity

        """
        return self.order_size

    def get_order_price(self) -> float | None:
        """Get order price.

        Returns:
            Order price

        """
        return self.order_price

    def get_reduce_only(self) -> bool | None:
        """Get reduce only flag.

        Returns:
            Whether this is a reduce-only order

        """
        return self.reduce_only

    def get_order_side(self) -> str | None:
        """Get order side.

        Returns:
            Order side (BUY/SELL)

        """
        return self.order_side

    def get_order_status(self) -> OrderStatus | None:
        """Get order status.

        Returns:
            Order status

        """
        return self.order_status

    def get_trailing_stop_price(self) -> float | None:
        """Get trailing stop price.

        Returns:
            Trailing stop price for conditional orders

        """
        return self.trailing_stop_price

    def get_trailing_stop_trigger_price(self) -> float | None:
        """Get trailing stop trigger price.

        Returns:
            Trailing stop activation price

        """
        return self.trailing_stop_trigger_price

    def get_trailing_stop_callback_rate(self) -> float | None:
        """Get trailing stop callback rate.

        Returns:
            Trailing stop callback rate

        """
        return self.trailing_stop_callback_rate

    def get_order_symbol_name(self) -> str | None:
        """Get order symbol name.

        Returns:
            Order symbol name

        """
        return self.order_symbol_name

    def get_order_time_in_force(self) -> str | None:
        """Get order time in force.

        Returns:
            Order time in force type

        """
        return self.order_time_in_force

    def get_order_type(self) -> str | None:
        """Get order type.

        Returns:
            Order type

        """
        return self.order_type

    def get_trailing_stop_trigger_price_type(self) -> str | None:
        """Get trailing stop trigger price type.

        Returns:
            Trigger price type

        """
        return self.trailing_stop_trigger_price_type

    def get_order_avg_price(self) -> float | None:
        """Get order average price.

        Returns:
            Average price

        """
        return self.order_avg_price

    def get_origin_order_type(self) -> str | None:
        """Get original order type.

        Returns:
            Original order type

        """
        return self.origin_order_type

    def get_position_side(self) -> str | None:
        """Get position side.

        Returns:
            Position side

        """
        return self.position_side

    def get_close_position(self) -> bool | None:
        """Get close position flag.

        Returns:
            Whether this is a close position order (only for conditional orders)

        """
        return self.close_position

    def get_stop_loss_price(self) -> None:
        """Get stop loss price.

        Returns:
            Stop loss price (not implemented)

        """
        return None

    def get_stop_loss_trigger_price(self) -> None:
        """Get stop loss trigger price.

        Returns:
            Stop loss trigger price (not implemented)

        """
        return None

    def get_stop_loss_trigger_price_type(self) -> None:
        """Get stop loss trigger price type.

        Returns:
            Stop loss trigger price type (not implemented)

        """
        return None

    def get_take_profit_price(self) -> None:
        """Get take profit price.

        Returns:
            Take profit price (not implemented)

        """
        return None

    def get_take_profit_trigger_price(self) -> None:
        """Get take profit trigger price.

        Returns:
            Take profit trigger price (not implemented)

        """
        return None

    def get_take_profit_trigger_price_type(self) -> None:
        """Get take profit trigger price type.

        Returns:
            Take profit trigger price type (not implemented)

        """
        return None


class BinanceRequestOrderData(BinanceOrderData):
    """Binance REST API order data container."""

    def init_data(self) -> "BinanceRequestOrderData":
        """Initialize order data from REST API response.

        Returns:
            Self for method chaining

        """
        if not self.has_been_json_encoded:
            self.order_info = json.loads(self.order_info)
            self.order_data = self.order_info["data"]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.order_data, "updateTime")
        self.trade_id = from_dict_get_float(self.order_data, "tradeId")
        self.client_order_id = from_dict_get_string(self.order_data, "clientOrderId")
        self.cum_quote = from_dict_get_float(self.order_data, "cumQuote")
        self.executed_qty = from_dict_get_float(self.order_data, "cumQty")
        self.order_id = from_dict_get_string(self.order_data, "orderId")
        self.order_size = from_dict_get_float(self.order_data, "origQty")
        self.order_price = from_dict_get_float(self.order_data, "price")
        self.reduce_only = from_dict_get_bool(self.order_data, "reduceOnly")
        self.order_side = from_dict_get_string(self.order_data, "side")
        self.order_status = OrderStatus.from_value(from_dict_get_string(self.order_data, "status"))
        self.order_symbol_name = from_dict_get_string(self.order_data, "symbol")
        self.order_type = from_dict_get_string(self.order_data, "type")
        self.order_time_in_force = from_dict_get_string(self.order_data, "timeInForce")
        self.order_avg_price = from_dict_get_float(self.order_data, "avgPrice")
        self.origin_order_type = from_dict_get_string(self.order_data, "origType")
        self.position_side = from_dict_get_string(self.order_data, "positionSide")
        self.close_position = from_dict_get_bool(self.order_data, "closePosition")
        self.trailing_stop_price = from_dict_get_float(self.order_data, "stopPrice")
        self.trailing_stop_trigger_price = from_dict_get_float(self.order_data, "activatePrice")
        self.trailing_stop_trigger_price_type = from_dict_get_string(self.order_data, "workingType")
        self.trailing_stop_callback_rate = from_dict_get_float(self.order_data, "priceRate")
        self.has_been_init_data = True
        return self


class BinanceSwapWssOrderData(BinanceOrderData):
    """Binance swap WebSocket order data container."""

    def init_data(self) -> "BinanceSwapWssOrderData":
        """Initialize order data from WebSocket message.

        Returns:
            Self for method chaining

        """
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        if self.order_data is None:
            return self
        self.server_time = from_dict_get_float(self.order_data, "E")
        order_dict = self.order_data["o"]
        self.trade_id = from_dict_get_float(order_dict, "t")
        self.client_order_id = from_dict_get_string(order_dict, "c")
        self.executed_qty = from_dict_get_float(order_dict, "z")
        self.order_id = from_dict_get_string(order_dict, "i")
        self.order_size = from_dict_get_float(order_dict, "q")
        self.order_price = from_dict_get_float(order_dict, "p")
        self.reduce_only = from_dict_get_bool(order_dict, "R")
        self.order_side = from_dict_get_string(order_dict, "S")
        self.order_status = OrderStatus.from_value(from_dict_get_string(order_dict, "X"))
        self.trailing_stop_price = from_dict_get_float(order_dict, "sp")
        self.trailing_stop_trigger_price = from_dict_get_float(order_dict, "AP")
        self.trailing_stop_callback_rate = from_dict_get_float(order_dict, "cr")
        self.order_symbol_name = from_dict_get_string(order_dict, "s")
        self.order_time_in_force = from_dict_get_string(order_dict, "f")
        self.order_type = from_dict_get_string(order_dict, "o")
        self.trailing_stop_trigger_price_type = from_dict_get_string(order_dict, "wt")
        self.order_avg_price = from_dict_get_float(order_dict, "ap")
        self.origin_order_type = from_dict_get_string(order_dict, "ot")
        self.position_side = from_dict_get_string(order_dict, "ps")
        self.close_position = from_dict_get_bool(order_dict, "cp")
        self.has_been_init_data = True
        return self


class BinanceSpotWssOrderData(BinanceOrderData):
    """Binance spot WebSocket order data container."""

    def init_data(self) -> "BinanceSpotWssOrderData":
        """Initialize order data from WebSocket message.

        Returns:
            Self for method chaining

        """
        if not self.has_been_json_encoded:
            self.order_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.order_data, "E")
        self.trade_id = from_dict_get_float(self.order_data, "t")
        self.client_order_id = from_dict_get_string(self.order_data, "c")
        self.cum_quote = from_dict_get_float(self.order_data, "cum_quote")
        self.executed_qty = from_dict_get_float(self.order_data, "z")
        self.order_id = from_dict_get_string(self.order_data, "i")
        self.order_size = from_dict_get_float(self.order_data, "q")
        self.order_price = from_dict_get_float(self.order_data, "p")
        self.order_side = from_dict_get_string(self.order_data, "S")
        self.order_status = OrderStatus.from_value(from_dict_get_string(self.order_data, "X"))
        self.order_symbol_name = from_dict_get_string(self.order_data, "s")
        self.order_time_in_force = from_dict_get_string(self.order_data, "f")
        self.order_type = from_dict_get_string(self.order_data, "o")
        self.has_been_init_data = True
        return self
