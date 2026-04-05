from __future__ import annotations

import time

from bt_api_py.containers.bars.bar import BarData
from bt_api_py.functions.utils import from_dict_get_float


class HitBtcRequestBarData(BarData):
    """保存HitBTC K线信息"""

    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.bar_data = bar_info if has_been_json_encoded else None
        self.timestamp = None
        self.open = None
        self.high = None
        self.low = None
        self.close = None
        self.volume = None
        self.volume_quote = None
        self.count = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return

        if self.bar_data is None:
            return

        # 提取数据
        self.timestamp = from_dict_get_float(self.bar_data, "timestamp")
        self.open = from_dict_get_float(self.bar_data, "open")
        self.high = from_dict_get_float(self.bar_data, "max")
        self.low = from_dict_get_float(self.bar_data, "min")
        self.close = from_dict_get_float(self.bar_data, "close")
        self.volume = from_dict_get_float(self.bar_data, "volume")
        self.volume_quote = from_dict_get_float(self.bar_data, "volumeQuote")
        self.count = from_dict_get_float(self.bar_data, "count")

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "timestamp": self.timestamp,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "volume_quote": self.volume_quote,
                "count": self.count,
            }
        return self.all_data

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_open_price(self):
        """Get open price."""
        return self.open

    def get_high_price(self):
        """Get high price."""
        return self.high

    def get_low_price(self):
        """Get low price."""
        return self.low

    def get_close_price(self):
        """Get close price."""
        return self.close

    def get_volume(self):
        """Get volume."""
        return self.volume

    def get_amount(self):
        """Get quote asset volume."""
        return self.volume_quote

    def get_close_time(self):
        """Get close time (same as timestamp for HitBTC)."""
        return self.timestamp

    def get_quote_asset_volume(self):
        """Get quote asset volume."""
        return self.volume_quote

    def get_base_asset_volume(self):
        """Get base asset volume (same as volume for HitBTC)."""
        return self.volume

    def get_num_trades(self):
        """Get number of trades."""
        return self.count

    def __str__(self):
        return f"HITBTC Bar {self.symbol_name}: O={self.open} H={self.high} L={self.low} C={self.close}"

    def __repr__(self):
        return f"<HitBtcBarData {self.symbol_name} {self.close}>"
