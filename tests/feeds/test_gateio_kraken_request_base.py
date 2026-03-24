from __future__ import annotations

import base64
from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_gateio.request_base import GateioRequestData
from bt_api_py.feeds.live_kraken.request_base import KrakenRequestData


def test_gateio_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = GateioRequestData(api_key="public-key", api_secret="secret-key")
    headers = request_data._build_auth_headers("GET", "/api/v4/spot/accounts")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
    assert headers["KEY"] == "public-key"
    assert headers["SIGN"]


def test_kraken_accepts_api_key_and_api_secret_aliases() -> None:
    private_key = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = KrakenRequestData(api_key="public-key", api_secret=private_key)
    headers = request_data._sign_request("/0/private/Balance", {})

    assert request_data.public_key == "public-key"
    assert request_data.private_key == private_key
    assert headers["API-Key"] == "public-key"
    assert headers["API-Sign"]


def test_kraken_request_post_uses_shared_http_client(monkeypatch) -> None:
    private_key = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = KrakenRequestData(api_key="public-key", api_secret=private_key)
    captured: dict[str, object] = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return {"error": [], "result": {"ZUSD": "1.0"}}

    monkeypatch.setattr(request_data._http_client, "request", fake_request)

    result = request_data.request(
        "POST /0/private/Balance",
        params={"asset": "BTC"},
        body={"aclass": "currency"},
        timeout=7,
    )

    assert isinstance(result, RequestData)
    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith("/0/private/Balance")
    assert captured["timeout"] == 7
    assert captured["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert captured["headers"]["API-Key"] == "public-key"
    assert "API-Sign" in captured["headers"]
    assert "asset=BTC" in captured["data"]
    assert "aclass=currency" in captured["data"]
    assert "nonce=" in captured["data"]


@pytest.mark.asyncio
async def test_gateio_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = GateioRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="GATEIO___SPOT",
    )

    async_mock = AsyncMock(return_value={"currency_pairs": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /spot/currency_pairs")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"currency_pairs": []}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_kraken_async_request_post_uses_shared_http_client(monkeypatch) -> None:
    private_key = base64.b64encode(b"secret-key").decode("utf-8")
    request_data = KrakenRequestData(api_key="public-key", api_secret=private_key)

    async_request_mock = AsyncMock(return_value={"error": [], "result": {"ZUSD": "2.0"}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request(
        "POST /0/private/Balance",
        params={"asset": "ETH"},
        body={"aclass": "currency"},
        timeout=3,
    )

    assert isinstance(result, RequestData)
    async_request_mock.assert_awaited_once()
    _, kwargs = async_request_mock.await_args
    assert kwargs["method"] == "POST"
    assert str(kwargs["url"]).endswith("/0/private/Balance")
    assert kwargs["timeout"] == 3
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert kwargs["headers"]["API-Key"] == "public-key"
    assert "API-Sign" in kwargs["headers"]
    assert "asset=ETH" in kwargs["data"]
    assert "aclass=currency" in kwargs["data"]
    assert "nonce=" in kwargs["data"]
