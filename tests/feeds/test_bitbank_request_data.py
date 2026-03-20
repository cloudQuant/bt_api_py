from __future__ import annotations

from queue import Queue
from typing import Any
from unittest.mock import MagicMock

from bt_api_py.feeds.live_bitbank.request_base import BitbankRequestData
from bt_api_py.feeds.live_bitbank.spot import BitbankRequestDataSpot


def test_bitbank_private_headers_include_signature_fields(monkeypatch: Any) -> None:
    request_data = BitbankRequestData(public_key="public-key", private_key="secret-key")
    monkeypatch.setattr("bt_api_py.feeds.live_bitbank.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/v1/user/assets", {"asset": "btc"})

    assert headers["ACCESS-KEY"] == "public-key"
    assert headers["ACCESS-REQUEST-TIME"] == "1700000000000"
    assert headers["ACCESS-TIME-WINDOW"] == "5000"
    assert headers["ACCESS-SIGNATURE"]


def test_bitbank_normalize_pair_uses_lowercase_underscore() -> None:
    request_data = BitbankRequestDataSpot()

    assert request_data._normalize_pair("BTC-JPY") == "btc_jpy"
    assert request_data._normalize_pair("ETH/JPY") == "eth_jpy"


def test_bitbank_kline_normalizer_extracts_ohlcv() -> None:
    payload = {
        "success": 1,
        "data": {
            "candlestick": [
                {
                    "ohlcv": [["1", "2", "3", "4", "5", "6"]],
                }
            ]
        },
    }

    data, status = BitbankRequestDataSpot._get_kline_normalize_function(payload, {})

    assert status is True
    assert data == [{"ohlcv": [["1", "2", "3", "4", "5", "6"]]}]


def test_bitbank_async_callback_pushes_result_to_queue() -> None:
    queue: Queue[Any] = Queue()
    request_data = BitbankRequestData(data_queue=queue)

    class _Future:
        def result(self) -> dict[str, Any]:
            return {"ok": True}

    request_data.async_callback(_Future())

    assert queue.get_nowait() == {"ok": True}


def test_bitbank_disconnect_closes_http_client() -> None:
    request_data = BitbankRequestData(public_key="public-key", private_key="secret-key")
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_bitbank_falls_back_to_api_credentials_when_aliases_are_empty(monkeypatch: Any) -> None:
    request_data = BitbankRequestData(
        public_key="",
        api_key="public-key",
        private_key="",
        api_secret="secret-key",
    )
    monkeypatch.setattr("bt_api_py.feeds.live_bitbank.request_base.time.time", lambda: 1700000000.0)

    headers = request_data._get_headers("GET", "/v1/user/assets", {"asset": "btc"})

    assert request_data._params.api_key == "public-key"
    assert request_data._params.api_secret == "secret-key"
    assert headers["ACCESS-KEY"] == "public-key"
