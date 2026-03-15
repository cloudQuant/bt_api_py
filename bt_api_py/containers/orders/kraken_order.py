"""
Kraken Order Data Container
Provides standardized order data structure for Kraken exchange.
"""

import time
from typing import Any

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.logging_factory import get_logger


class KrakenRequestOrderData(OrderData):
    """Kraken Request Order Data Container"""

    def __init__(
        self,
        data: dict[str, Any],
        symbol: str,
        asset_type: str,
        is_response_data: bool = False,
        has_been_json_encoded=False,
    ):
        """Initialize Kraken order data.

        Args:
            data: Raw order data from Kraken API
            symbol: Trading pair symbol
            asset_type: Asset type (e.g., 'SPOT')
            is_response_data: Whether data is from API response
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        # Store symbol and asset_type before parsing
        self.symbol = symbol
        self.asset_type = asset_type
        self.average_price: float | None = None
        self.logger = get_logger("kraken_order")
        self.is_response_data = is_response_data
        self._parse_data(data)

    def _parse_data(self, data: dict[str, Any]):
        """Parse Kraken order data.

        Kraken order response format for AddOrder:
        {
            "error": [],
            "result": {
                "descr": {
                    "order": "buy 0.001 XBTUSD @ limit 50000.0"
                },
                "txid": ["OUF4EM-FRGI2-MQMWZD"]
            }
        }

        Kraken open orders format:
        {
            "error": [],
            "result": {
                "open": {
                    "OUF4EM-FRGI2-MQMWZD": {
                        "refid": null,
                        "userref": 0,
                        "status": "open",
                        "opentm": 1688671955.1234,
                        "starttm": 0,
                        "expiretm": 0,
                        "descr": {
                            "pair": "XBTUSD",
                            "type": "buy",
                            "ordertype": "limit",
                            "price": "50000.0",
                            "price2": "0",
                            "leverage": "none",
                            "order": "buy 0.001 XBTUSD @ limit 50000.0"
                        },
                        "vol": "0.001",
                        "vol_exec": "0.000",
                        "cost": "0.000",
                        "fee": "0.000",
                        "price": "0.000",
                        "misc": "",
                        "oflags": "fciq"
                    }
                }
            }
        }
        """
        try:
            if self.is_response_data and "txid" in data:
                # New order response
                self._parse_new_order_response(data)
            elif isinstance(data, dict) and "status" in data:
                # Order status update
                self._parse_order_status(data)
            else:
                # Handle normalized data from feed
                self._parse_normalized_data(data)

        except Exception as e:
            self.logger.error(f"Error parsing Kraken order data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    def _parse_new_order_response(self, data: dict[str, Any]):
        """Parse new order response from Kraken."""
        # Handle both simple txid response and full order data
        if "txid" in data and len(data) > 1:
            # Full order response with txid and other fields
            # Use _parse_order_status for full responses
            self._parse_order_status(data)
        else:
            # Simple txid-only response
            self.order_id = data.get("txid", data.get("order_id", str(time.time())))
            self.status = "new"
            self.timestamp = time.time()
            self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
            self.exchange = "kraken"

    def _parse_order_status(self, data: dict[str, Any]):
        """Parse order status from Kraken API."""
        # Basic order info
        # Try both 'id' and 'txid' for order ID
        self.order_id = data.get("id") or data.get("txid") or str(time.time())
        self.status = data.get("status", "unknown")
        self.timestamp = data.get("opentm", time.time())
        self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        self.exchange = "kraken"

        # Order details from descr
        descr = data.get("descr", {})
        self.symbol = descr.get("pair", self.symbol)
        self.side = descr.get("type", "unknown")
        self.order_type = descr.get("ordertype", "unknown")
        p = descr.get("price")
        self.price = float(p) if p is not None else None
        v = descr.get("vol")
        self.quantity = float(v) if v is not None else None

        # Execution info
        ve = data.get("vol_exec", "0")
        self.executed_quantity = float(ve) if ve is not None else 0.0
        self.remaining_quantity = self.quantity - self.executed_quantity if self.quantity else None
        c = data.get("cost", "0")
        self.cost = float(c) if c is not None else 0.0
        f = data.get("fee", "0")
        self.fee = float(f) if f is not None else 0.0

        # Additional fields
        self.userref = data.get("userref")
        self.leverage = data.get("leverage", "none")
        self.order_description = descr.get("order")
        self.misc = data.get("misc", "")
        self.oflags = data.get("oflags", "")

        # Time fields
        self.start_time = data.get("starttm")
        self.expire_time = data.get("expiretm")

        # Calculate average price
        if self.executed_quantity > 0 and self.cost > 0:
            self.average_price = self.cost / self.executed_quantity
        else:
            self.average_price = None

    def _parse_normalized_data(self, data: dict[str, Any]):
        """Parse normalized order data."""
        # Extract basic fields
        self.order_id = data.get("id", str(time.time()))
        self.symbol = data.get("symbol", self.symbol)
        self.status = data.get("status", "unknown")
        self.side = data.get("side", "unknown")
        self.type = data.get("type", "unknown")
        self.exchange = data.get("exchange", "kraken")

        # Timestamps
        self.timestamp = data.get("created_at", time.time())
        if isinstance(self.timestamp, str):
            self.timestamp = time.time()
        self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

        # Order details
        qty = data.get("quantity")
        self.quantity = float(qty) if qty is not None else None
        prc = data.get("price")
        self.price = float(prc) if prc is not None else None
        eq = data.get("executed_quantity", "0")
        self.executed_quantity = float(eq) if eq is not None else 0.0
        self.remaining_quantity = data.get("remaining_quantity")
        if self.remaining_quantity is None and self.quantity:
            self.remaining_quantity = self.quantity - self.executed_quantity

        # Cost and fees
        c = data.get("cost", "0")
        self.cost = float(c) if c is not None else 0.0
        f = data.get("fee", "0")
        self.fee = float(f) if f is not None else 0.0
        ap = data.get("average_price")
        self.average_price = float(ap) if ap is not None else None

        # Additional fields
        self.userref = data.get("userref")
        self.leverage = data.get("leverage")
        self.order_description = data.get("order_description")
        self.misc = data.get("misc", "")
        self.oflags = data.get("oflags", "")

        # Time fields
        self.start_time = data.get("start_time")
        self.expire_time = data.get("expire_time")

    # Base class interface methods
    def init_data(self):
        """Initialize order data from parsed data.

        This is a no-op since data is already parsed in __init__ via _parse_data.
        Kept for compatibility with the base class interface.
        """
        # Data is already parsed in __init__, just return self
        return self

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange

    def get_local_update_time(self):
        """Get local update time."""
        return self.timestamp

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_trade_id(self):
        """Get trade ID."""
        return

    def get_client_order_id(self):
        """Get client order ID."""
        return self.userref

    def get_cum_quote(self):
        """Get cumulative quote."""
        return self.cost

    def get_executed_qty(self):
        """Get executed quantity."""
        return self.executed_quantity

    def get_order_id(self):
        """Get order ID."""
        return self.order_id

    def get_order_size(self):
        """Get order size."""
        return self.quantity

    def get_order_price(self):
        """Get order price."""
        return self.price

    def get_reduce_only(self):
        """Get reduce only flag."""
        return

    def get_order_side(self):
        """Get order side."""
        return self.side

    def get_order_status(self):
        """Get order status."""
        return self.status

    def get_order_symbol_name(self):
        """Get order symbol name."""
        return self.symbol

    def get_order_time_in_force(self):
        """Get order time in force."""
        return

    def get_order_type(self):
        """Get order type."""
        return self.type

    def get_order_avg_price(self):
        """Get average price."""
        return self.average_price

    def get_origin_order_type(self):
        """Get original order type."""
        return self.type

    def get_position_side(self):
        """Get position side."""
        return

    def get_trailing_stop_price(self):
        """Get trailing stop price."""
        return

    def get_trailing_stop_trigger_price(self):
        """Get trailing stop trigger price."""
        return

    def get_trailing_stop_callback_rate(self):
        """Get trailing stop callback rate."""
        return

    def get_trailing_stop_trigger_price_type(self):
        """Get trailing stop trigger price type."""
        return

    def get_stop_loss_price(self):
        """Get stop loss price."""
        return

    def get_stop_loss_trigger_price(self):
        """Get stop loss trigger price."""
        return

    def get_stop_loss_trigger_price_type(self):
        """Get stop loss trigger price type."""
        return

    def get_take_profit_price(self):
        """Get take profit price."""
        return

    def get_take_profit_trigger_price(self):
        """Get take profit trigger price."""
        return

    def get_take_profit_trigger_price_type(self):
        """Get take profit trigger price type."""
        return

    def get_close_position(self):
        """Get close position flag."""
        return

    def get_order_offset(self):
        """Get order offset."""
        return

    def get_order_exchange_id(self):
        """Get order exchange ID."""
        return "kraken"

    def to_dict(self) -> dict[str, Any]:
        """Convert order data to dictionary."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "status": self.status,
            "side": self.side,
            "type": self.type,
            "quantity": self.quantity,
            "price": self.price,
            "executed_quantity": self.executed_quantity,
            "remaining_quantity": self.remaining_quantity,
            "average_price": self.average_price,
            "cost": self.cost,
            "fee": self.fee,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "exchange": self.exchange,
            "userref": self.userref,
            "leverage": self.leverage,
            "order_description": self.order_description,
            "misc": self.misc,
            "oflags": self.oflags,
            "start_time": self.start_time,
            "expire_time": self.expire_time,
            "asset_type": self.asset_type,
        }

    def validate(self) -> bool:
        """Validate order data integrity."""
        if not self.order_id:
            return False

        if self.status not in ["new", "open", "closed", "canceled", "expired", "unknown"]:
            return False

        if self.side not in ["buy", "sell"]:
            return False

        if self.type not in [
            "market",
            "limit",
            "stop-loss",
            "take-profit",
            "stop-loss-limit",
            "take-profit-limit",
        ]:
            return False

        # Validate price and quantity
        if self.type in ["limit", "stop-loss-limit", "take-profit-limit"] and self.price is None:
            return False

        if self.quantity is None or self.quantity <= 0:
            return False

        # Validate execution
        if self.executed_quantity is None:
            return True
        return 0 <= self.executed_quantity <= self.quantity

    def is_open(self) -> bool:
        """Check if order is open."""
        return self.status in ["open", "new"]

    def is_filled(self) -> bool:
        """Check if order is fully filled."""
        return self.status == "closed" and self.executed_quantity == self.quantity

    def get_fill_percentage(self) -> float:
        """Calculate fill percentage."""
        if self.quantity is None or self.quantity == 0:
            return 0.0
        return (self.executed_quantity / self.quantity) * 100

    def update_from_trade(self, trade_data: dict[str, Any]):
        """Update order from trade execution."""
        if "executed_quantity" in trade_data:
            self.executed_quantity = float(trade_data["executed_quantity"])
        if "cost" in trade_data:
            self.cost = float(trade_data["cost"])
        if "fee" in trade_data:
            self.fee = float(trade_data["fee"])

        # Update remaining quantity
        if self.quantity is not None:
            self.remaining_quantity = self.quantity - self.executed_quantity

        # Update status if fully filled
        if self.executed_quantity == self.quantity and self.quantity > 0:
            self.status = "closed"

    def cancel(self):
        """Cancel the order."""
        self.status = "canceled"
        self.remaining_quantity = 0

    def __str__(self) -> str:
        """String representation of order."""
        return (
            f"KrakenOrder({self.order_id}: {self.side} {self.quantity} {self.symbol} "
            f"@ {self.price} [{self.status}])"
        )

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (
            f"KrakenRequestOrderData(order_id='{self.order_id}', "
            f"symbol='{self.symbol}', side='{self.side}', "
            f"quantity={self.quantity}, price={self.price}, "
            f"status='{self.status}')"
        )


class KrakenSpotWssOrderData(OrderData):
    """Kraken Spot WebSocket Order Data Container"""

    def __init__(
        self, data: dict[str, Any], symbol: str, asset_type: str, has_been_json_encoded=False
    ):
        """Initialize Kraken WebSocket order data.

        Args:
            data: Raw order data from Kraken API
            symbol: Trading pair symbol
            asset_type: Asset type (e.g., 'SPOT')
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(data, has_been_json_encoded)
        # Store symbol and asset_type before parsing
        self.symbol = symbol
        self.asset_type = asset_type
        self.logger = get_logger("kraken_wss_order")
        self._parse_wss_data(data)

    def _parse_wss_data(self, data: dict[str, Any]):
        """Parse WebSocket order data."""
        self.order_id = data.get("orderId")
        self.symbol = data.get("symbol", self.symbol)
        self.status = data.get("status", "unknown")
        self.side = data.get("side", "unknown")
        self.type = data.get("type", "unknown")
        qty = data.get("qty")
        self.quantity = float(qty) if qty is not None else None
        prc = data.get("price")
        self.price = float(prc) if prc is not None else None
        eq = data.get("executedQty", "0")
        self.executed_quantity = float(eq) if eq is not None else 0.0
        rq = data.get("remainingQty")
        self.remaining_quantity = float(rq) if rq is not None else None
        self.timestamp = data.get("time", time.time())
        self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        self.exchange = "kraken"

        # Calculate average price and cost
        if self.executed_quantity > 0:
            # This might need to be calculated from the actual trades
            pass

    def to_dict(self) -> dict[str, Any]:
        """Convert WebSocket order data to dictionary."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "status": self.status,
            "side": self.side,
            "type": self.type,
            "quantity": self.quantity,
            "price": self.price,
            "executed_quantity": self.executed_quantity,
            "remaining_quantity": self.remaining_quantity,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "exchange": self.exchange,
            "asset_type": self.asset_type,
        }
