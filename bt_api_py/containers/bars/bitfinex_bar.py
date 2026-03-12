import json
import time

from bt_api_py.containers.bars.bar import BarData


class BitfinexRequestBarData(BarData):
    """Bitfinex Request Bar Data"""

    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.bar_data = bar_info if has_been_json_encoded else None
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.bar_data = json.loads(self.bar_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.bar_data, list):
            for bar in self.bar_data:
                if isinstance(bar, dict):
                    self.open = bar.get("open")
                    self.high = bar.get("high")
                    self.low = bar.get("low")
                    self.close = bar.get("close")
                    self.volume = bar.get("volume")
                    self.timestamp = bar.get("timestamp")
                elif isinstance(bar, list) and len(bar) >= 6:
                    # Format: [MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]
                    self.timestamp = bar[0]
                    self.open = bar[1]
                    self.close = bar[2]
                    self.high = bar[3]
                    self.low = bar[4]
                    self.volume = bar[5]

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "timestamp": self.timestamp,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_open(self):
        return self.open

    def get_high(self):
        return self.high

    def get_low(self):
        return self.low

    def get_close(self):
        return self.close

    def get_volume(self):
        return self.volume

    def get_timestamp(self):
        return self.timestamp

    def get_time(self):
        return self.timestamp

    def is_valid(self):
        """Check if bar data is valid"""
        return all(v is not None for v in [self.open, self.high, self.low, self.close])

    def get_range(self):
        """Get price range"""
        return self.high - self.low if self.high is not None and self.low is not None else 0

    def get_mid_price(self):
        """Get mid price"""
        if (
            self.open is not None
            and self.high is not None
            and self.low is not None
            and self.close is not None
        ):
            return (self.open + self.high + self.low + self.close) / 4
        return None
