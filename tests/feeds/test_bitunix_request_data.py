from __future__ import annotations

from queue import Queue
from typing import Any

from bt_api_py.feeds.live_bitunix.request_base import BitunixRequestData
from bt_api_py.feeds.live_bitunix.spot import BitunixRequestDataSpot


def test_bitunix_headers_include_generated_signature(monkeypatch: Any) -> None:
    request_data = BitunixRequestData(public_key="public-key", private_key="secret-key")
    monkeypatch.setattr("bt_api_py.feeds.live_bitunix.request_base.time.time", lambda: 1700000000.0)
    monkeypatch.setattr(
        "bt_api_py.feeds.live_bitunix.request_base.uuid.uuid4",
        lambda: "12345678-1234-1234-1234-123456789abc",
    )

    headers = request_data._get_headers("GET", "/api/v1/futures/account", {"symbol": "BTCUSDT"})

    assert headers["api-key"] == "public-key"
    assert headers["nonce"] == "12345678123412341234123456789abc"
    assert headers["timestamp"] == "1700000000000"
    assert headers["sign"]


def test_bitunix_get_kline_builder_maps_interval() -> None:
    request_data = BitunixRequestDataSpot()

    path, params, extra_data = request_data._get_kline("BTCUSDT", "1h", count=5)

    assert path == "GET /api/v1/futures/market/klines"
    assert params["symbol"] == "BTCUSDT"
    assert params["limit"] == 5
    assert extra_data["request_type"] == "get_kline"


def test_bitunix_exchange_info_normalizer_wraps_payload() -> None:
    payload = {"data": [{"symbol": "BTCUSDT"}]}

    data, status = BitunixRequestDataSpot._get_exchange_info_normalize_function(payload, {})

    assert status is True
    assert data == [[{"symbol": "BTCUSDT"}]]


def test_bitunix_async_callback_pushes_result_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BitunixRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}
