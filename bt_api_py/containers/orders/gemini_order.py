from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestOrderData(RequestData):
    """Gemini Order Data Container"""

    def __init__(
        self,
        data,
        symbol=None,
        asset_type=None,
        is_rest=True,
        extra_data=None,
        status=False,
        normalize_func=None,
    ):
        # Handle positional arguments from test
        if extra_data is None:
            extra_data = {}

        # Set exchange_name and symbol_name in extra_data for RequestData
        extra_data.setdefault("exchange_name", "GEMINI")
        if symbol:
            extra_data.setdefault("symbol_name", symbol)
        if asset_type:
            extra_data.setdefault("asset_type", asset_type)

        super().__init__(data, extra_data, status, normalize_func)
        self.symbol = symbol
        self.asset_type = asset_type
        self.is_rest = is_rest

        # Default values
        self.order_id = None
        self.client_order_id = None
        self.symbol_name = None
        self.side = None
        self.type = None
        self.status = None
        self.price = None
        self.original_amount = None
        self.executed_amount = None
        self.remaining_amount = None
        self.timestamp = None
        self.exchange_timestamp = None
        self.fee = None
        self.fee_currency = None
        self.avg_price = None

        if data:
            self._parse_data(data)

    def _parse_data(self, data):
        """Parse Gemini API response"""
        if self.is_rest:
            self._parse_rest_data(data)
        else:
            self._parse_wss_data(data)

    def _parse_rest_data(self, data):
        """Parse REST API response"""
        if isinstance(data, dict):
            # Order placement response
            if "order_id" in data:
                self.order_id = str(data.get("order_id"))
                self.client_order_id = data.get("client_order_id")
                self.symbol_name = data.get("symbol")
                self.side = data.get("side")
                self.type = data.get("type")
                self.status = data.get("status", "new")
                self.price = float(data.get("price", 0))
                self.original_amount = float(data.get("original_amount", 0))
                self.executed_amount = float(data.get("executed_amount", 0))
                self.remaining_amount = float(data.get("remaining_amount", 0))
                self.timestamp = data.get("timestampms")
                self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                self.avg_price = (
                    float(data.get("avg_execution_price", 0))
                    if data.get("avg_execution_price")
                    else None
                )

            # Order status query response
            elif "id" in data:
                self.order_id = str(data.get("id"))
                self.client_order_id = data.get("client_order_id")
                self.symbol_name = data.get("symbol")
                self.side = data.get("side")
                self.type = data.get("type")
                self.status = self._normalize_status(data.get("is_live"))
                self.price = float(data.get("price", 0))
                self.original_amount = float(data.get("original_amount", 0))
                self.executed_amount = float(data.get("executed_amount", 0))
                self.remaining_amount = float(data.get("remaining_amount", 0))
                self.timestamp = data.get("timestampms")
                self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                self.avg_price = (
                    float(data.get("avg_execution_price", 0))
                    if data.get("avg_execution_price")
                    else None
                )

    def _parse_wss_data(self, data):
        """Parse WebSocket response"""
        if isinstance(data, dict):
            # Handle order events
            if "type" in data:
                event_type = data.get("type")
                if event_type in ["order_started", "order_filled", "order_canceled"]:
                    self.order_id = data.get("order_id")
                    self.client_order_id = data.get("client_order_id")
                    self.symbol_name = data.get("symbol")
                    self.side = data.get("side")
                    self.type = data.get("type")
                    self.status = self._normalize_status(data.get("event_type"))
                    self.price = float(data.get("price", 0))
                    self.original_amount = float(data.get("original_amount", 0))
                    self.executed_amount = float(data.get("executed_amount", 0))
                    self.remaining_amount = float(data.get("remaining_amount", 0))
                    self.timestamp = data.get("timestamp")
                    self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                    self.fee = float(data.get("fee", 0))
                    self.fee_currency = data.get("fee_currency")

    def _normalize_status(self, status):
        """Normalize order status to standard format"""
        status_map = {
            True: "active",
            False: "filled",
            "new": "active",
            "active": "active",
            "filled": "filled",
            "canceled": "canceled",
            "rejected": "rejected",
            "expired": "canceled",
        }
        return status_map.get(status, status)

    # Getter methods for compatibility with tests
    def get_order_id(self):
        return self.order_id

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol_name": self.symbol_name,
            "side": self.side,
            "type": self.type,
            "status": self.status,
            "price": self.price,
            "original_amount": self.original_amount,
            "executed_amount": self.executed_amount,
            "remaining_amount": self.remaining_amount,
            "timestamp": self.timestamp,
            "exchange_timestamp": self.exchange_timestamp,
            "fee": self.fee,
            "fee_currency": self.fee_currency,
            "avg_price": self.avg_price,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "is_rest": self.is_rest,
        }

    def __str__(self):
        """String representation"""
        return (
            f"GeminiOrder(id={self.order_id}, symbol={self.symbol_name}, "
            f"side={self.side}, type={self.type}, status={self.status}, "
            f"price={self.price}, amount={self.original_amount})"
        )


class GeminiSpotWssOrderData(GeminiRequestOrderData):
    """Gemini Spot WebSocket Order Data"""

    def __init__(self, data, symbol=None, asset_type=None) -> None:
        super().__init__(data, symbol, asset_type, is_rest=False)
