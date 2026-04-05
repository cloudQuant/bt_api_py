"""Gemini Bar/Candle Data Container."""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.utils.time import convert_utc_timestamp


class GeminiRequestBarData(RequestData):
    """Gemini Bar/Candle Data Container."""

    def __init__(
        self,
        data: Any,
        symbol: str | None = None,
        asset_type: str | None = None,
        time_frame: str | None = None,
        is_rest: bool = True,
        extra_data: dict[str, Any] | None = None,
        status: bool = False,
        normalize_func: Any = None,
    ) -> None:
        """Initialize Gemini bar data.

        Args:
            data: Raw bar data from Gemini API
            symbol: Trading symbol (e.g., "BTCUSD")
            asset_type: Asset type (e.g., "SPOT")
            time_frame: Time frame (e.g., "1m", "1h", "1d")
            is_rest: Whether data is from REST API (vs WebSocket)
            extra_data: Additional data to pass to parent class
            status: Status flag
            normalize_func: Data normalization function
        """
        if extra_data is None:
            extra_data = {}

        extra_data.setdefault("exchange_name", "GEMINI")
        if symbol:
            extra_data.setdefault("symbol_name", symbol)
        if asset_type:
            extra_data.setdefault("asset_type", asset_type)

        super().__init__(data, extra_data, status, normalize_func)
        self.symbol: str | None = symbol
        self.asset_type = asset_type
        self.time_frame: str | None = time_frame
        self.is_rest = is_rest

        self.open: float | None = None
        self.high: float | None = None
        self.low: float | None = None
        self.close: float | None = None
        self.volume: float | None = None
        self.timestamp: int | None = None
        self.exchange_timestamp: str | None = None
        self.is_closed: bool = False

        if data:
            self._parse_data(data)

    def _parse_data(self, data: Any) -> None:
        """Parse Gemini API response."""
        if self.is_rest:
            self._parse_rest_data(data)
        else:
            self._parse_rest_data(data)  # WSS uses same structure as REST

    def _parse_rest_data(self, data: Any) -> None:
        """Parse REST API response."""
        if isinstance(data, list):
            bars = []
            for bar_data in data:
                bar = self._parse_single_bar(bar_data)
                if bar:
                    bars.append(bar)

            if bars:
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
            self.open = float(data.get("open", 0))
            self.high = float(data.get("high", 0))
            self.low = float(data.get("low", 0))
            self.close = float(data.get("close", 0))
            self.volume = float(data.get("volume", 0))
            self.timestamp = data.get("timestamp")
            self.exchange_timestamp = convert_utc_timestamp(self.timestamp)

    def _parse_single_bar(self, bar_data: Any) -> GeminiRequestBarData | None:
        """Parse a single bar/candle.

        Args:
            bar_data: Raw bar data array

        Returns:
            Parsed bar object or None
        """
        if len(bar_data) >= 6:
            bar = GeminiRequestBarData(
                data=None,
                symbol=self.symbol,
                asset_type=self.asset_type,
                time_frame=self.time_frame,
                is_rest=self.is_rest,
            )
            bar.open = float(bar_data[1])
            bar.high = float(bar_data[2])
            bar.low = float(bar_data[3])
            bar.close = float(bar_data[4])
            bar.volume = float(bar_data[5])
            bar.timestamp = bar_data[0]
            bar.exchange_timestamp = convert_utc_timestamp(bar.timestamp)
            return bar
        return None

    def get_exchange_name(self) -> str:
        """Get exchange name."""
        return "GEMINI"

    def get_asset_type(self) -> str:
        """Get asset type."""
        return self.asset_type or ""

    def get_symbol_name(self) -> str:
        """Get symbol name."""
        return self.symbol or ""

    def get_server_time(self) -> int | None:
        """Get server time."""
        return self.timestamp

    def get_local_update_time(self):
        """Get local update time."""
        return self.exchange_timestamp

    def get_open_price(self) -> float | None:
        """Get open price."""
        return self.open

    def get_high_price(self) -> float | None:
        """Get high price."""
        return self.high

    def get_low_price(self) -> float | None:
        """Get low price."""
        return self.low

    def get_close_price(self) -> float | None:
        """Get close price."""
        return self.close

    def get_volume(self) -> float | None:
        """Get volume."""
        return self.volume
