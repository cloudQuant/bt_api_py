"""Module-level docstring."""
from __future__ import annotations

from typing import Any

from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.schema import MarketEvent, normalize_market_symbol


class MarketDataHub:
    """Exchange-agnostic market fan-out hub."""

    def __init__(self, bus: InMemoryForwardingBus | None = None) -> None:
        """__init__ method"""
        self.bus = bus or InMemoryForwardingBus()
        self.subscription_refcounts: dict[tuple[str, str, str, str], int] = {}

    def subscribe(
        self, exchange: str, market_type: str, symbol: str, event_type: str = "tick"
    ) -> None:
        """subscribe method"""
        key = self._key(exchange, market_type, symbol, event_type)
        self.subscription_refcounts[key] = self.subscription_refcounts.get(key, 0) + 1

    def unsubscribe(
        self, exchange: str, market_type: str, symbol: str, event_type: str = "tick"
    ) -> None:
        """unsubscribe method"""
        key = self._key(exchange, market_type, symbol, event_type)
        current = self.subscription_refcounts.get(key, 0)
        if current <= 1:
            self.subscription_refcounts.pop(key, None)
        else:
            self.subscription_refcounts[key] = current - 1

    def publish(self, event: MarketEvent) -> MarketEvent:
        """publish method"""
        return self.bus.publish_market(event)

    def publish_tick(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        price: float,
        volume: float = 0.0,
        direction: str = "buy",
        payload: dict[str, Any] | None = None,
    ) -> MarketEvent:
        """publish_tick method"""
        data = dict(payload or {})
        data["price"] = price
        data["volume"] = volume
        data["direction"] = direction
        return self.publish(
            MarketEvent(
                event_type="tick",
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                payload=data,
            )
        )

    def publish_orderbook(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        payload: dict[str, Any] | None = None,
    ) -> MarketEvent:
        """publish_orderbook method"""
        data = dict(payload or {})
        data["bids"] = bids
        data["asks"] = asks
        return self.publish(
            MarketEvent(
                event_type="orderbook",
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                payload=data,
            )
        )

    def publish_bar(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        openinterest: float = 0.0,
        payload: dict[str, Any] | None = None,
    ) -> MarketEvent:
        """publish_bar method"""
        data = dict(payload or {})
        data["open"] = open_price
        data["high"] = high
        data["low"] = low
        data["close"] = close
        data["volume"] = volume
        data["openinterest"] = openinterest
        return self.publish(
            MarketEvent(
                event_type="bar",
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                payload=data,
            )
        )

    def stats(self) -> dict[str, Any]:
        """stats method"""
        active_subscriptions = {
            ".".join(key): count for key, count in self.subscription_refcounts.items()
        }
        return {
            "active_subscription_count": len(self.subscription_refcounts),
            "subscription_refcounts": active_subscriptions,
            "bus": self.bus.stats(),
        }

    @staticmethod
    def _key(
        exchange: str, market_type: str, symbol: str, event_type: str
    ) -> tuple[str, str, str, str]:
        return (
            str(exchange or "").upper(),
            str(market_type or "").upper(),
            normalize_market_symbol(symbol),
            str(event_type or "").lower(),
        )
