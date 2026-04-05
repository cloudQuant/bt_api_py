"""
IB Tick 数据容器
对应 IB TWS API 的 TickData / Ticker
"""

from __future__ import annotations

from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.tickers.ticker import TickerData


class IbTickerData(TickerData):
    """IB Tick 行情数据"""

    def __init__(
        self,
        ticker_info: Any,
        symbol_name: Any = None,
        asset_type: Any = "STK",
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "IB"
        self._initialized = False
        self.contract_symbol: str | None = None
        self.bid_val: float | None = None
        self.ask_val: float | None = None
        self.bid_size_val: float | None = None
        self.ask_size_val: float | None = None
        self.last_val: float | None = None
        self.last_size_val: float | None = None
        self.volume_val: int | None = None
        self.high_val: float | None = None
        self.low_val: float | None = None
        self.close_val: float | None = None
        self.timestamp_val: float | str | None = None

    def init_data(self) -> Self:
        if self._initialized:
            return self
        info = self.ticker_info
        if isinstance(info, dict):
            self.contract_symbol = info.get("symbol", self.symbol_name)
            self.bid_val = float(info.get("bid", 0)) if info.get("bid") else None
            self.ask_val = float(info.get("ask", 0)) if info.get("ask") else None
            self.bid_size_val = float(info.get("bidSize", 0))
            self.ask_size_val = float(info.get("askSize", 0))
            self.last_val = float(info.get("last", 0)) if info.get("last") else None
            self.last_size_val = float(info.get("lastSize", 0))
            self.volume_val = int(info.get("volume", 0))
            self.high_val = float(info.get("high", 0)) if info.get("high") else None
            self.low_val = float(info.get("low", 0)) if info.get("low") else None
            self.close_val = float(info.get("close", 0)) if info.get("close") else None
            self.timestamp_val = info.get("time", "")
        self._initialized = True
        return self

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_local_update_time(self) -> float:
        ts = self.timestamp_val
        return float(ts) if isinstance(ts, (int, float)) else 0.0

    def get_symbol_name(self) -> str:
        return str(self.contract_symbol or self.symbol_name or "")

    def get_ticker_symbol_name(self) -> str | None:
        return self.contract_symbol or self.symbol_name

    def get_asset_type(self) -> str:
        return str(self.asset_type)

    def get_server_time(self) -> float | None:
        ts = self.timestamp_val
        return float(ts) if isinstance(ts, (int, float)) else None

    def get_bid_price(self) -> float | None:
        return self.bid_val

    def get_ask_price(self) -> float | None:
        return self.ask_val

    def get_bid_volume(self) -> float | None:
        return self.bid_size_val

    def get_ask_volume(self) -> float | None:
        return self.ask_size_val

    def get_last_price(self) -> float | None:
        return self.last_val

    def get_last_volume(self) -> float | None:
        return self.last_size_val

    def get_all_data(self) -> dict[str, Any]:
        return {
            "exchange_name": self.exchange_name,
            "symbol": self.contract_symbol,
            "bid": self.bid_val,
            "ask": self.ask_val,
            "bid_size": self.bid_size_val,
            "ask_size": self.ask_size_val,
            "last": self.last_val,
            "last_size": self.last_size_val,
            "volume": self.volume_val,
            "high": self.high_val,
            "low": self.low_val,
            "close": self.close_val,
            "time": self.timestamp_val,
        }
