from __future__ import annotations

from queue import Queue
from typing import Any
from unittest.mock import MagicMock

from bt_api_py.feeds.live_bitflyer.request_base import BitflyerRequestData
from bt_api_py.feeds.live_bitflyer.spot import BitflyerRequestDataSpot


def test_bitflyer_private_headers_include_auth_fields(monkeypatch: Any) -> None:
    request_data = BitflyerRequestData(public_key="public-key", private_key="secret-key")
    monkeypatch.setattr("bt_api_py.feeds.live_bitflyer.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/v1/me/getbalance")

    assert headers["ACCESS-KEY"] == "public-key"
    assert headers["ACCESS-TIMESTAMP"] == "1700000000.0"
    assert headers["ACCESS-SIGN"]


def test_bitflyer_normalize_product_code_uses_uppercase_underscore() -> None:
    request_data = BitflyerRequestDataSpot()

    assert request_data._normalize_product_code("btc-jpy") == "BTC_JPY"
    assert request_data._normalize_product_code("eth/jpy") == "ETH_JPY"


def test_bitflyer_health_normalizer_extracts_status() -> None:
    payload = {"status": "NORMAL"}

    data, status = BitflyerRequestDataSpot._get_health_normalize_function(payload, {})

    assert status is True
    assert data == [payload]


def test_bitflyer_kline_normalizer_accepts_execution_list() -> None:
    payload = [{"id": 1, "price": 100}]

    data, status = BitflyerRequestDataSpot._get_kline_normalize_function(payload, {})

    assert status is True
    assert data == [payload]


def test_bitflyer_async_callback_pushes_result_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BitflyerRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}


def test_bitflyer_disconnect_closes_http_client() -> None:
    request_data = BitflyerRequestData(public_key="public-key", private_key="secret-key")
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_bitflyer_falls_back_to_api_credentials_when_aliases_are_empty(monkeypatch: Any) -> None:
    request_data = BitflyerRequestData(
        public_key="",
        api_key="public-key",
        private_key="",
        api_secret="secret-key",
    )
    monkeypatch.setattr("bt_api_py.feeds.live_bitflyer.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/v1/me/getbalance")

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["ACCESS-KEY"] == "public-key"
