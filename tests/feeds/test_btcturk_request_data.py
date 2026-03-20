from __future__ import annotations

import base64
from queue import Queue
from typing import Any
from unittest.mock import MagicMock

from bt_api_py.feeds.live_btcturk.request_base import BTCTurkRequestData
from bt_api_py.feeds.live_btcturk.spot import BTCTurkRequestDataSpot


def test_btcturk_private_headers_include_signature(monkeypatch: Any) -> None:
    secret = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = BTCTurkRequestData(public_key="public-key", private_key=secret)
    monkeypatch.setattr("bt_api_py.feeds.live_btcturk.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/api/v1/users/balances")

    assert headers["X-PCK"] == "public-key"
    assert headers["X-Stamp"] == "1700000000000"
    assert headers["X-Signature"]


def test_btcturk_get_kline_builds_time_window(monkeypatch: Any) -> None:
    request_data = BTCTurkRequestDataSpot()
    monkeypatch.setattr("bt_api_py.feeds.live_btcturk.spot._time.time", lambda: 1700000000)

    path, params, extra_data = request_data._get_kline("BTCTRY", "1h", count=2)

    assert path == "GET /api/v2/ohlcs"
    assert params == {"pair": "BTCTRY", "from": 1699992800, "to": 1700000000}
    assert extra_data["request_type"] == "get_kline"


def test_btcturk_depth_normalizer_returns_data_payload() -> None:
    payload = {"data": {"bids": [[1, 2]], "asks": [[3, 4]]}}

    data, status = BTCTurkRequestDataSpot._get_depth_normalize_function(payload, {})

    assert status is True
    assert data == [payload["data"]]


def test_btcturk_async_callback_pushes_result_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BTCTurkRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}


def test_btcturk_disconnect_closes_http_client() -> None:
    request_data = BTCTurkRequestData(public_key="public-key", private_key="secret-key")
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_btcturk_falls_back_to_api_credentials_when_aliases_are_empty(monkeypatch: Any) -> None:
    secret = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = BTCTurkRequestData(
        public_key="",
        api_key="public-key",
        private_key="",
        api_secret=secret,
    )
    monkeypatch.setattr("bt_api_py.feeds.live_btcturk.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/api/v1/users/balances")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == secret
    assert headers["X-PCK"] == "public-key"
