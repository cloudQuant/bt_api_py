from __future__ import annotations

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData


def test_binance_defaults_exchange_name() -> None:
    request_data = BinanceRequestData(public_key="public-key", private_key="secret-key")

    assert request_data.exchange_name == "BINANCE___SWAP"


def test_binance_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BinanceRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BINANCE___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"symbol": "BTCUSDT"},
    )

    result = request_data.request("GET /api/v3/ticker/price", is_sign=False)

    assert isinstance(result, RequestData)
    assert result.get_data() == {"symbol": "BTCUSDT"}
    assert result.get_extra_data() == {}


def test_binance_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = BinanceRequestData(api_key="public-key", api_secret="secret-key")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
