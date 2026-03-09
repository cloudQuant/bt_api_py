"""Bitstamp Ticker Data Container."""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData


class BitstampRequestTickerData(TickerData):
    """Bitstamp ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BITSTAMP"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        """Parse Bitstamp ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data
        if data:
            # Bitstamp ticker format (all values are strings)
            self.ticker_symbol_name = self.symbol_name  # Bitstamp doesn't return symbol in ticker
            self.last_price = self._parse_float(data.get("last"))
            self.bid_price = self._parse_float(data.get("bid"))
            self.ask_price = self._parse_float(data.get("ask"))
            self.volume_24h = self._parse_float(data.get("volume"))
            self.high_24h = self._parse_float(data.get("high"))
            self.low_24h = self._parse_float(data.get("low"))
            self.open_24h = self._parse_float(data.get("open"))

        self.has_been_init_data = True
        return self

    @staticmethod
    def _parse_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    # Getter methods required by TickerData base class
    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_server_time(self) -> float | None:
        return None

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        return None

    def get_ask_volume(self) -> float | None:
        return None

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_last_volume(self) -> float | None:
        return None

    def get_all_data(self) -> dict[str, Any]:
        return {
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "asset_type": self.asset_type,
            "local_update_time": self.local_update_time,
            "ticker_symbol_name": self.ticker_symbol_name,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "open_24h": self.open_24h,
        }

    def __str__(self) -> str:
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()
