"""PancakeSwap Ticker Data Container.

Provides standardized ticker data structure for PancakeSwap
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PancakeSwapRequestTickerData:
    """PancakeSwap Ticker Request Data."""

    symbol: str
    price: float
    timestamp: int
    volume: float
    quote_volume: float
    high: float | None = None
    low: float | None = None
    bid: float | None = None
    ask: float | None = None
    count: int | None = None

    def __post_init__(self):
        """Validate and normalize data after initialization."""
        if self.price < 0:
            self.price = 0.0
        if self.volume < 0:
            self.volume = 0.0
        if self.quote_volume < 0:
            self.quote_volume = 0.0
        if self.high is not None and self.high < 0:
            self.high = 0.0
        if self.low is not None and self.low < 0:
            self.low = 0.0
        if self.bid is not None and self.bid < 0:
            self.bid = 0.0
        if self.ask is not None and self.ask < 0:
            self.ask = 0.0
        if self.count is not None and self.count < 0:
            self.count = 0

    @property
    def price_change(self) -> float | None:
        """Calculate price change if high and low are available."""
        if self.high is not None and self.low is not None:
            return self.high - self.low
        return None

    @property
    def price_change_percent(self) -> float | None:
        """Calculate price change percentage if high and low are available."""
        if self.high is not None and self.low is not None and self.low > 0:
            return (self.high - self.low) / self.low * 100
        return None

    @property
    def spread(self) -> float | None:
        """Calculate bid-ask spread if available."""
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    @property
    def spread_percent(self) -> float | None:
        """Calculate bid-ask spread percentage if available."""
        if self.bid is not None and self.ask is not None and self.bid > 0:
            return (self.ask - self.bid) / self.bid * 100
        return None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "price": self.price,
            "timestamp": self.timestamp,
            "volume": self.volume,
            "quote_volume": self.quote_volume,
            "high": self.high,
            "low": self.low,
            "bid": self.bid,
            "ask": self.ask,
            "count": self.count,
            "price_change": self.price_change,
            "price_change_percent": self.price_change_percent,
            "spread": self.spread,
            "spread_percent": self.spread_percent,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        import json

        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> PancakeSwapRequestTickerData:
        """Create from dictionary."""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> PancakeSwapRequestTickerData:
        """Create from JSON string."""
        import json

        data = json.loads(json_str)
        return cls.from_dict(data)
