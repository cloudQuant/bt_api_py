"""BYDFi Ticker Data Container."""

import json
import time
from typing import Any, Self

from bt_api_py.containers.tickers.ticker import TickerData


class BYDFiRequestTickerData(TickerData):
    """BYDFi ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BYDFI"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        """Parse BYDFi ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data.get("data", {})

        if data:
            self.ticker_symbol_name = data.get("symbol")
            self.last_price = self._parse_float(data.get("price"))
            self.bid_price = self._parse_float(data.get("bidPrice"))
            self.ask_price = self._parse_float(data.get("askPrice"))
            self.volume_24h = self._parse_float(data.get("quoteVolume"))
            self.high_24h = self._parse_float(data.get("highPrice"))
            self.low_24h = self._parse_float(data.get("lowPrice"))
            self.timestamp = self._parse_int(data.get("time"))

        self.has_been_init_data = True
        return self

    def get_symbol_name(self) -> str:
        """Get the symbol name."""
        return self.symbol_name

    def get_exchange_name(self) -> str:
        """Get the exchange name."""
        return self.exchange_name

    def get_asset_type(self) -> str:
        """Get the asset type."""
        return self.asset_type

    def get_local_update_time(self) -> float:
        """Get local update time."""
        return self.local_update_time

    def get_ticker_symbol_name(self) -> str | None:
        """Get ticker symbol name from API response."""
        return self.ticker_symbol_name

    def get_last_price(self) -> float | None:
        """Get last price."""
        return self.last_price

    def get_bid_price(self) -> float | None:
        """Get bid price."""
        return self.bid_price

    def get_ask_price(self) -> float | None:
        """Get ask price."""
        return self.ask_price

    def get_all_data(self) -> dict[str, Any]:
        """Get all ticker data."""
        return {
            "symbol_name": self.symbol_name,
            "ticker_symbol_name": self.ticker_symbol_name,
            "exchange_name": self.exchange_name,
            "asset_type": self.asset_type,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "timestamp": self.timestamp,
            "local_update_time": self.local_update_time,
        }

    def __str__(self) -> str:
        return f"BYDFiTicker(symbol={self.symbol_name}, price={self.last_price})"

    def __repr__(self) -> str:
        return self.__str__()

    @staticmethod
    def _parse_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_int(value):
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
