from __future__ import annotations

from queue import Queue
from typing import Any

from bt_api_py.feeds.live_bitinka.request_base import BitinkaRequestData
from bt_api_py.feeds.live_bitinka.spot import BitinkaRequestDataSpot


def test_bitinka_headers_include_api_key() -> None:
    request_data = BitinkaRequestData(public_key="public-key")

    headers = request_data._get_headers("GET", "/ticker")

    assert headers["Content-Type"] == "application/json"
    assert headers["X-API-KEY"] == "public-key"


def test_bitinka_convert_symbol_handles_dash_and_underscore() -> None:
    request_data = BitinkaRequestDataSpot()

    assert request_data._convert_symbol("BTC-USD") == "BTC/USD"
    assert request_data._convert_symbol("ETH_USD") == "ETH/USD"


def test_bitinka_get_deals_builds_market_and_limits() -> None:
    request_data = BitinkaRequestDataSpot()

    path, params, extra_data = request_data._get_deals(
        "BTC-USD", count=25, start_time=1, end_time=2
    )

    assert path == "GET /trades"
    assert params == {"market": "BTC/USD", "limit": 25, "start_time": 1, "end_time": 2}
    assert extra_data["request_type"] == "get_deals"


def test_bitinka_async_callback_pushes_result_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BitinkaRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}
