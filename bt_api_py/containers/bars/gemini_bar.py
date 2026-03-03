from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestBarData(RequestData):
    """Gemini Bar/Candle Data Container"""

    def __init__(self, data, symbol=None, asset_type=None, time_frame=None, is_rest=True,
                 extra_data=None, status=False, normalize_func=None):
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
        self.time_frame = time_frame
        self.is_rest = is_rest

        # Default values
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = None
        self.timestamp = None
        self.exchange_timestamp = None
        self.is_closed = False

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
        if isinstance(data, list):
            # Parse multiple bars
            bars = []
            for bar_data in data:
                bar = self._parse_single_bar(bar_data)
                if bar:
                    bars.append(bar)

            if bars:
                # Use the most recent bar
                latest_bar = bars[-1]
                self.open = latest_bar.open
                self.high = latest_bar.high
                self.low = latest_bar.low
                self.close = latest_bar.close
                self.volume = latest_bar.volume
                self.timestamp = latest_bar.timestamp
                self.exchange_timestamp = latest_bar.exchange_timestamp
                self.is_closed = latest_bar.is_closed
        elif isinstance(data, dict):
            # Parse single bar dict (from test data)
            self.open = float(data.get("open", 0))
            self.high = float(data.get("high", 0))
            self.low = float(data.get("low", 0))
            self.close = float(data.get("close", 0))
            self.volume = float(data.get("volume", 0))
            self.timestamp = data.get("timestamp")
            self.exchange_timestamp = convert_utc_timestamp(self.timestamp)

    def _parse_single_bar(self, bar_data):
        """Parse a single bar/candle"""
        if len(bar_data) >= 6:
            bar = GeminiRequestBarData(
                data=bar_data,
                symbol=self.symbol,
                asset_type=self.asset_type,
                time_frame=self.time_frame,
                is_rest=self.is_rest
            )
            bar.open = float(bar_data[1])  # open
            bar.high = float(bar_data[2])  # high
            bar.low = float(bar_data[3])  # low
            bar.close = float(bar_data[4])  # close
            bar.volume = float(bar_data[5])  # volume
            bar.timestamp = bar_data[0]  # timestamp_ms
            bar.exchange_timestamp = convert_utc_timestamp(bar.timestamp)
            return bar
        return None

    def _parse_wss_data(self, data):
        """Parse WebSocket response"""
        if isinstance(data, dict):
            if data.get("type") == "update" and "events" in data:
                events = data.get("events", [])
                for event in events:
                    if event.get("type") == "candle":
                        self.open = float(event.get("open", 0))
                        self.high = float(event.get("high", 0))
                        self.low = float(event.get("low", 0))
                        self.close = float(event.get("close", 0))
                        self.volume = float(event.get("volume", 0))
                        self.timestamp = event.get("timestamp")
                        self.exchange_timestamp = convert_utc_timestamp(self.timestamp)
                        self.is_closed = event.get("is_closed", False)
                        break

    # Getter methods for compatibility with tests
    def get_symbol_name(self):
        return self.symbol

    def get_open_price(self):
        return self.open

    def get_high_price(self):
        return self.high

    def get_low_price(self):
        return self.low

    def get_close_price(self):
        return self.close

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "time_frame": self.time_frame,
            "is_rest": self.is_rest,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "exchange_timestamp": self.exchange_timestamp,
            "is_closed": self.is_closed,
        }

    def __str__(self):
        """String representation"""
        return (f"GeminiBar(symbol={self.symbol}, tf={self.time_frame}, "
                f"O={self.open}, H={self.high}, L={self.low}, C={self.close})")


class GeminiSpotWssBarData(GeminiRequestBarData):
    """Gemini Spot WebSocket Bar Data"""

    def __init__(self, data, symbol=None, asset_type=None, time_frame=None):
        super().__init__(data, symbol, asset_type, time_frame, is_rest=False)