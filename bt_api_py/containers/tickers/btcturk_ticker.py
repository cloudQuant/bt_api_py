"""BTCTurk Ticker Data Container."""

import json
import time
from typing import Self

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float, parse_int


class BTCTurkRequestTickerData(TickerData):
    """BTCTurk ticker data container."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BTCTURK"
        self.local_update_time = time.time()
        self.ticker_data: dict | list | None = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name: str | None = None
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.volume_24h: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None
        self.timestamp: int | None = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        """Parse BTCTurk ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Initialize all attributes to None
        self.ticker_symbol_name = None
        self.last_price = None
        self.bid_price = None
        self.ask_price = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.timestamp = None

        data = (
            (self.ticker_data or {}).get("data", {}) if isinstance(self.ticker_data, dict) else {}
        )
        # BTCTurk returns either an array or single object
        if isinstance(data, list) and len(data) > 0:
            ticker = data[0]
        elif isinstance(data, dict):
            ticker = data
        else:
            ticker = {}

        if ticker:
            self.ticker_symbol_name = ticker.get("pairSymbol")
            self.last_price = parse_float(ticker.get("last"))
            self.bid_price = parse_float(ticker.get("bid"))
            self.ask_price = parse_float(ticker.get("ask"))
            self.volume_24h = parse_float(ticker.get("volume"))
            self.high_24h = parse_float(ticker.get("high"))
            self.low_24h = parse_float(ticker.get("low"))
            self.timestamp = parse_int(ticker.get("timestamp"))

        self.has_been_init_data = True
        return self

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return str(self.symbol_name)

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return str(self.asset_type)

    def get_server_time(self) -> float | None:
        return getattr(self, "timestamp", None)

    def get_bid_price(self) -> float | None:
        return self.bid_price

    def get_ask_price(self) -> float | None:
        return self.ask_price

    def get_last_price(self) -> float | None:
        return self.last_price
