"""Module-level docstring."""
from __future__ import annotations

import queue
from dataclasses import asdict, is_dataclass
from typing import Any

from bt_api_py.forwarding.hub import MarketDataHub
from bt_api_py.forwarding.schema import MarketEvent


class BtApiForwardingAdapter:
    """Bridge an existing BtApi instance into MarketDataHub."""

    def __init__(
        self,
        bt_api: Any,
        hub: MarketDataHub,
        *,
        default_exchange: str = "UNKNOWN",
        default_market_type: str = "SPOT",
    ) -> None:
        """__init__ method"""
        self.bt_api = bt_api
        self.hub = hub
        self.default_exchange = default_exchange
        self.default_market_type = default_market_type

    def add_exchange(self, *args: Any, **kwargs: Any) -> Any:
        """add_exchange method"""
        return self.bt_api.add_exchange(*args, **kwargs)

    def subscribe(self, dataname: str, topics: list[str] | None = None) -> Any:
        """subscribe method"""
        return self.bt_api.subscribe(dataname, topics or ["ticker"])

    def forward_once(self, exchange_name: str, max_items: int = 100) -> int:
        """forward_once method"""
        max_items = _normalize_non_negative_int(max_items, "max_items")
        data_queue = self.bt_api.get_data_queue(exchange_name)
        forwarded = 0
        for _ in range(max_items):
            try:
                raw = data_queue.get_nowait()
            except queue.Empty:
                break
            event = self.normalize(raw, fallback_exchange=exchange_name)
            self.hub.publish(event)
            forwarded += 1
        return forwarded

    def normalize(self, raw: Any, *, fallback_exchange: str = "") -> MarketEvent:
        """normalize method"""
        payload = _to_payload(raw)
        event_type = _normalize_event_type(
            payload.get("event_type") or payload.get("type") or payload.get("event")
        )
        symbol = str(
            payload.get("symbol")
            or payload.get("instrument")
            or payload.get("instrument_id")
            or payload.get("dataname")
            or ""
        )
        exchange = str(
            payload.get("exchange")
            or payload.get("exchange_name")
            or fallback_exchange
            or self.default_exchange
        )
        market_type = str(
            payload.get("market_type")
            or payload.get("asset_type")
            or payload.get("category")
            or self.default_market_type
        )
        return MarketEvent(
            event_type=event_type,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            payload=payload,
            source="bt_api",
        )


def _to_payload(raw: Any) -> dict[str, Any]:
    if is_dataclass(raw):
        return asdict(raw)
    if isinstance(raw, dict):
        return dict(raw)
    payload = dict(getattr(raw, "__dict__", {}) or {})
    if payload:
        return payload
    return {"value": repr(raw)}


def _normalize_event_type(value: Any) -> str:
    text = str(value or "event").strip().lower()
    aliases = {
        "ticker": "tick",
        "tickerevent": "tick",
        "tickerdata": "tick",
        "depth": "orderbook",
        "order_book": "orderbook",
        "orderbookevent": "orderbook",
        "kline": "bar",
        "candle": "bar",
    }
    return aliases.get(text, text)


def _normalize_non_negative_int(value: int, name: str) -> int:
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value
