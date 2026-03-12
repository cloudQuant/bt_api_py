"""HTX Ticker Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float


class HtxRequestTickerData(TickerData):
    """HTX REST API ticker data."""

    def __init__(
        self,
        ticker_info: str | dict[str, Any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize Htx ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.

        """
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )
        self.ticker_symbol_name: str | None = None
        self.server_time: float | None = None
        self.bid_price: float | None = None
        self.ask_price: float | None = None
        self.bid_volume: float | None = None
        self.ask_volume: float | None = None
        self.last_price: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "HtxRequestTickerData":
        """Initialize ticker data from HTX response.

        HTX ticker response format (from /market/detail/merged):
        {
            "status": "ok",
            "ch": "market.btcusdt.detail.merged",
            "ts": 1688671955000,
            "tick": {
                "id": 123456789,
                "ts": 1688671955000,
                "close": 50000,
                "open": 49500,
                "high": 51000,
                "low": 49000,
                "amount": 1234.5678,
                "count": 10000,
                "vol": 61728350,
                "ask": [50001, 1.5],
                "bid": [49999, 2.3]
            }
        }
        """
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)

        # Extract tick data
        data = self.ticker_data or {}
        tick = data.get("tick", {})

        self.server_time = from_dict_get_float(data, "ts")
        self.ticker_symbol_name = self.symbol_name

        # Best bid
        bid = tick.get("bid", [0, 0])
        if isinstance(bid, list) and len(bid) >= 2:
            self.bid_price = from_dict_get_float({0: bid[0]}, "0")
            self.bid_volume = from_dict_get_float({0: bid[1]}, "0")

        # Best ask
        ask = tick.get("ask", [0, 0])
        if isinstance(ask, list) and len(ask) >= 2:
            self.ask_price = from_dict_get_float({0: ask[0]}, "0")
            self.ask_volume = from_dict_get_float({0: ask[1]}, "0")

        # Last price
        self.last_price = from_dict_get_float(tick, "close")

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "last_price": self.last_price,
            }
        return self.all_data or {}

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_server_time(self) -> float | None:
        return self.server_time

    def get_bid_price(self) -> float | None:
        self.init_data()
        return self.bid_price

    def get_ask_price(self) -> float | None:
        self.init_data()
        return self.ask_price

    def get_bid_volume(self) -> float | None:
        self.init_data()
        return self.bid_volume

    def get_ask_volume(self) -> float | None:
        self.init_data()
        return self.ask_volume

    def get_last_price(self) -> float | None:
        self.init_data()
        return self.last_price
