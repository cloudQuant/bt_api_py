from __future__ import annotations

from queue import Queue
from typing import Any
from unittest.mock import MagicMock

from bt_api_py.feeds.live_bingx.request_base import BingXRequestData
from bt_api_py.feeds.live_bingx.spot import BingXRequestDataSpot


def test_bingx_private_headers_include_api_key() -> None:
    request_data = BingXRequestData(public_key="public-key")

    headers = request_data._get_headers("GET", "/openApi/spot/v1/account/balance")

    assert headers["X-BX-APIKEY"] == "public-key"
    assert headers["Content-Type"] == "application/json"


def test_bingx_request_adds_timestamp_and_signature(monkeypatch: Any) -> None:
    request_data = BingXRequestData(public_key="public-key", private_key="secret-key")
    captured: dict[str, Any] = {}

    def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"code": 0}

    monkeypatch.setattr("bt_api_py.feeds.live_bingx.request_base.time.time", lambda: 1700000000.0)
    monkeypatch.setattr(request_data._http_client, "request", fake_request)

    request_data.request(
        "GET /openApi/spot/v1/account/balance",
        params={"symbol": "BTC-USDT"},
    )

    assert captured["headers"]["X-BX-APIKEY"] == "public-key"
    assert captured["params"]["timestamp"] == 1700000000000
    assert "signature" in captured["params"]


def test_bingx_spot_get_tick_builder_sets_normalize_function() -> None:
    request_data = BingXRequestDataSpot()

    path, params, extra_data = request_data._get_tick("BTC-USDT")

    assert path == "GET /openApi/spot/v1/ticker/24hr"
    assert params == {"symbol": "BTC-USDT"}
    assert extra_data["request_type"] == "get_tick"
    assert extra_data["normalize_function"] is request_data._get_tick_normalize_function


def test_bingx_exchange_info_normalizer_returns_symbol_list() -> None:
    payload = {"data": {"symbols": [{"symbol": "BTC-USDT"}, {"symbol": "ETH-USDT"}]}}

    data, status = BingXRequestDataSpot._get_exchange_info_normalize_function(payload, {})

    assert status is True
    assert data == [{"symbol": "BTC-USDT"}, {"symbol": "ETH-USDT"}]


def test_bingx_async_callback_pushes_request_data_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BingXRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}


def test_bingx_disconnect_closes_http_client() -> None:
    request_data = BingXRequestData(public_key="public-key", private_key="secret-key")
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_bingx_falls_back_to_api_credentials_when_aliases_are_empty() -> None:
    request_data = BingXRequestData(
        public_key="",
        api_key="public-key",
        private_key="",
        api_secret="secret-key",
    )

    headers = request_data._get_headers("GET", "/openApi/spot/v1/account/balance")
    signed_params = request_data._add_signature({"timestamp": 1})

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["X-BX-APIKEY"] == "public-key"
    assert "signature" in signed_params
