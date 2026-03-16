from __future__ import annotations

from queue import Queue
from typing import Any

from bt_api_py.feeds.live_bitvavo.request_base import BitvavoRequestData
from bt_api_py.feeds.live_bitvavo.spot import BitvavoRequestDataSpot


def test_bitvavo_private_headers_include_auth_signature(monkeypatch: Any) -> None:
    request_data = BitvavoRequestData(public_key="public-key", private_key="secret-key")
    monkeypatch.setattr("bt_api_py.feeds.live_bitvavo.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/balance", {"symbol": "BTC"})

    assert headers["Bitvavo-Access-Key"] == "public-key"
    assert headers["Bitvavo-Access-Timestamp"] == "1700000000000"
    assert headers["Bitvavo-Access-Signature"]


def test_bitvavo_request_passes_query_params_for_get(monkeypatch: Any) -> None:
    request_data = BitvavoRequestData()
    captured: dict[str, Any] = {}

    def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(request_data._http_client, "request", fake_request)

    request_data.request("GET /ticker/24h", params={"market": "BTC-EUR"})

    assert captured["params"] == {"market": "BTC-EUR"}


def test_bitvavo_exchange_info_normalizer_wraps_list() -> None:
    payload = [{"market": "BTC-EUR"}, {"market": "ETH-EUR"}]

    data, status = BitvavoRequestDataSpot._get_exchange_info_normalize_function(payload, {})

    assert status is True
    assert data == [payload]


def test_bitvavo_async_callback_pushes_result_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BitvavoRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}
