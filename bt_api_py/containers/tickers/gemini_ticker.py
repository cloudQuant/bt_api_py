from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestTickerData(RequestData):
    """Gemini Ticker Data Container"""

    def __init__(self, data, symbol=None, asset_type=None, is_rest=True,
                 extra_data=None, status=False, normalize_func=None):
        # Handle positional arguments from test
        if extra_data is None:
            extra_data = {}
        # Support both positional and keyword argument calling patterns
        if isinstance(symbol, dict):
            # Test is calling with positional args in old format
            # (data, symbol, asset_type, is_rest)
            # So symbol is actually the symbol string
            pass
        if not isinstance(symbol, str):
            symbol = None

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
        self.last_price = None
        self.high = None
        self.low = None
        self.volume = None
        self.bid = None
        self.ask = None
        self.timestamp = None
        self.exchange_timestamp = None
        self.change_24h = None
        self.change_percent_24h = None

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
            # Single ticker response
            if "close" in data or "bid" in data or "ask" in data:
                self.last_price = float(data.get("close", data.get("last", 0)))
                self.high = float(data.get("high", 0))
                self.low = float(data.get("low", 0))
                volume_val = data.get("volume", 0)
                if isinstance(volume_val, dict):
                    self.volume = float(volume_val.get(self.symbol or "btcusd", 0) if self.symbol else list(volume_val.values())[0] if volume_val else 0)
                else:
                    self.volume = float(volume_val)
                self.bid = float(data.get("bid", 0))
                self.ask = float(data.get("ask", 0))
                self.timestamp = data.get("timestampms", data.get("timestamp"))
                self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                self.change_24h = float(data.get("changes", {}).get("24h", 0))
                self.change_percent_24h = float(data.get("changes", {}).get("24h_percent", 0))

    def _parse_wss_data(self, data):
        """Parse WebSocket response"""
        if isinstance(data, dict):
            # Handle ticker updates
            if data.get("type") == "update" and "events" in data:
                events = data.get("events", [])
                for event in events:
                    if event.get("type") == "trade":
                        self.last_price = float(event.get("price", 0))
                        self.timestamp = event.get("timestamp")
                        self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                        break

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "last_price": self.last_price,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp,
            "exchange_timestamp": self.exchange_timestamp,
            "change_24h": self.change_24h,
            "change_percent_24h": self.change_percent_24h,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "is_rest": self.is_rest,
        }

    def __str__(self):
        """String representation"""
        return (f"GeminiTicker(symbol={self.symbol}, last={self.last_price}, "
                f"bid={self.bid}, ask={self.ask})")


class GeminiSpotWssTickerData(GeminiRequestTickerData):
    """Gemini Spot WebSocket Ticker Data"""

    def __init__(self, data, symbol=None, asset_type=None):
        super().__init__(data, symbol, asset_type, is_rest=False)