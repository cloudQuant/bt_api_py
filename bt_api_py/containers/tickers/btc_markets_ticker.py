"""BTC Markets Ticker Data Container."""

import json
import time
from typing import Self

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.containers.tickers.ticker_utils import parse_float


class BtcMarketsRequestTickerData(TickerData):
    """BTC Markets ticker data container."""

    def __init__(
        self, ticker_info, symbol_name, asset_type, has_been_json_encoded: bool = False
    ) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "BTC_MARKETS"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name: str | None = None
        self.has_been_init_data = False
        self.last_price: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.volume_24h: float | None = None
        self.high_24h: float | None = None
        self.low_24h: float | None = None

    def init_data(self) -> "Self":
        """Parse BTC Markets ticker response."""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.ticker_data if isinstance(self.ticker_data, dict) else {}
        if data:
            self.ticker_symbol_name = data.get("marketId")
            self.last_price = parse_float(data.get("lastPrice"))
            self.bid_price = parse_float(data.get("bestBid"))
            self.ask_price = parse_float(data.get("bestAsk"))
            self.volume_24h = parse_float(data.get("volume24h"))
            self.high_24h = parse_float(data.get("high24h"))
            self.low_24h = parse_float(data.get("low24h"))

        self.has_been_init_data = True
        return self

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return str(self.symbol_name)

    def get_ticker_symbol_name(self) -> str | None:
        val = self.ticker_symbol_name
        return None if val is None else str(val)

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
