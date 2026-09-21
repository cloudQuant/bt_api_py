"""Offline benchmarks for hot paths in normalized trading events."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bt_api_py._normalization import normalize_event

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def static_orderbook_normalizer() -> Callable[[], dict[str, object]]:
    """Create a deterministic input and fresh output on every invocation."""

    def normalize_static_orderbook() -> dict[str, object]:
        return normalize_event(
            {
                "symbol": "BTCUSDT",
                "sequence": 123456,
                "bids": [[100.5, 2.0], [100.0, 3.25], [99.5, 5.0]],
                "asks": [[101.0, 1.5], [101.5, 2.25], [102.0, 4.0]],
            },
            "BINANCE___SPOT",
            kind="orderbook",
            symbol="BTCUSDT",
        )

    return normalize_static_orderbook


@pytest.mark.performance
def test_normalize_event_orderbook_dict_hot_path(
    benchmark: Callable[[Callable[[], dict[str, object]]], dict[str, object]],
    static_orderbook_normalizer: Callable[[], dict[str, object]],
) -> None:
    """Benchmark a populated static orderbook without external dependencies."""
    event = benchmark(static_orderbook_normalizer)

    assert event["kind"] == "orderbook"
    assert event["sequence"] == 123456
    assert event["bids"] == [(100.5, 2.0), (100.0, 3.25), (99.5, 5.0)]
    assert event["asks"] == [(101.0, 1.5), (101.5, 2.25), (102.0, 4.0)]
