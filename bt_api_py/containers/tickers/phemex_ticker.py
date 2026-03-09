"""Phemex Ticker Data Container
Provides standardized ticker data structure for Phemex exchange.
"""

import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.logging_factory import get_logger


class PhemexRequestTickerData(TickerData):
    """Phemex Request Ticker Data Container."""

    def __init__(
        self, data: dict[str, Any], symbol: str, asset_type: str, has_been_json_encoded=False
    ) -> None:
        """Initialize Phemex ticker data.

        Args:
            data: Raw ticker data from Phemex API
            symbol: Trading pair symbol
            asset_type: Asset type (e.g., 'SPOT', 'PERPETUAL')
            has_been_json_encoded: Whether data has been JSON encoded

        """
        super().__init__(data, has_been_json_encoded)
        self.symbol = symbol
        self.asset_type = asset_type
        self.logger = get_logger("phemex_ticker")
        self._parse_data(data)

    def _parse_data(self, data: dict[str, Any]) -> None:
        """Parse Phemex ticker data.

        Phemex ticker response format:
        {
            "code": 0,
            "data": {
                "symbol": "BTCUSDT",
                "lastEp": 50000000000,  # Scaled last price (1e8 scale)
                "highEp": 51000000000,
                "lowEp": 49000000000,
                "volumeEv": 1000000000,  # Scaled volume
                "turnoverEv": 50000000000,
                "openInterest": 1000,
                "indexEp": 50000000000,
                "markEp": 50000000000,
                "fundingRateEp": 100000,
                ...
            }
        }

        For spot symbols (prefixed with 's'):
        {
            "code": 0,
            "data": {
                "symbol": "sBTCUSDT",
                "lastEp": 50000000000,
                ...
            }
        }
        """
        try:
            # Phemex wraps response in {'code': 0, 'data': {...}}
            result = data.get("data", {})

            # Basic information
            # Use the symbol from result if available, otherwise use the one passed in
            self.symbol = result.get("symbol", self.symbol)
            self.exchange = "phemex"
            self.timestamp = data.get("timestamp", time.time())
            self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

            # Price data (Phemex uses Ep suffix for scaled prices, 1e8 scale)
            SCALE = 1e8
            self.last_price = self._unscale(result.get("lastEp"), SCALE)
            self.high_price = self._unscale(result.get("highEp"), SCALE)
            self.low_price = self._unscale(result.get("lowEp"), SCALE)
            self.open_price = self._unscale(result.get("openEp"), SCALE)

            # Volume data (Phemex uses Ev suffix for scaled volumes)
            VOLUME_SCALE = 1e8
            self.volume_24h = self._unscale(result.get("volumeEv"), VOLUME_SCALE)
            self.turnover_24h = self._unscale(result.get("turnoverEv"), SCALE)

            # Additional fields
            self.open_interest = result.get("openInterest")
            self.index_price = self._unscale(result.get("indexEp"), SCALE)
            self.mark_price = self._unscale(result.get("markEp"), SCALE)

            # Funding rate (scaled)
            self.funding_rate = self._unscale(result.get("fundingRateEp"), 1e6)

            # Ask/Bid prices (if available)
            self.ask_price = self._unscale(result.get("askEp"), SCALE)
            self.bid_price = self._unscale(result.get("bidEp"), SCALE)

            # Calculate price change
            if self.last_price and self.open_price:
                self.price_change = self.last_price - self.open_price
                self.price_change_percentage = (
                    (self.last_price - self.open_price) / self.open_price
                ) * 100
            else:
                self.price_change = None
                self.price_change_percentage = None

            # Calculate spread
            if self.ask_price and self.bid_price:
                self.spread = self.ask_price - self.bid_price
                self.spread_percentage = (
                    (self.spread / self.bid_price) * 100 if self.bid_price else None
                )
            else:
                self.spread = None
                self.spread_percentage = None

        except Exception as e:
            self.logger.error(f"Error parsing Phemex ticker data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    def _unscale(self, value: int | None, scale: float) -> float | None:
        """Unscale a Phemex value.

        Args:
            value: Scaled value
            scale: Scaling factor

        Returns:
            Unscaled float value

        """
        if value is None:
            return None
        return value / scale

    def to_dict(self) -> dict[str, Any]:
        """Convert ticker data to dictionary."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "last_price": self.last_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "open_price": self.open_price,
            "volume_24h": self.volume_24h,
            "turnover_24h": self.turnover_24h,
            "ask_price": self.ask_price,
            "bid_price": self.bid_price,
            "spread": self.spread,
            "spread_percentage": self.spread_percentage,
            "price_change": self.price_change,
            "price_change_percentage": self.price_change_percentage,
            "open_interest": self.open_interest,
            "index_price": self.index_price,
            "mark_price": self.mark_price,
            "funding_rate": self.funding_rate,
            "asset_type": self.asset_type,
        }

    def validate(self) -> bool:
        """Validate ticker data integrity."""
        if not self.symbol:
            return False

        # Check required price fields
        if not self.last_price or self.last_price <= 0:
            return False

        # Validate bid-ask spread
        if self.ask_price and self.bid_price:
            if self.ask_price < self.bid_price:
                return False

        return True

    def __str__(self) -> str:
        """String representation of ticker."""
        return (
            f"PhemexTicker({self.symbol}: {self.last_price} "
            f"Bid:{self.bid_price} Ask:{self.ask_price} "
            f"Vol24h:{self.volume_24h} Chg:{self.price_change_percentage:.2f}%)"
        )

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (
            f"PhemexRequestTickerData(symbol='{self.symbol}', "
            f"last_price={self.last_price}, "
            f"bid_ask=({self.bid_price}, {self.ask_price}), "
            f"volume_24h={self.volume_24h}, "
            f"timestamp={self.timestamp})"
        )
