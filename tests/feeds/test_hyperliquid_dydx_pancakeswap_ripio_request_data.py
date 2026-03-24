from typing import Any
from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_dydx.request_base import DydxRequestData
from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData
from bt_api_py.feeds.live_pancakeswap.request_base import PancakeSwapRequestData
from bt_api_py.feeds.live_ripio.request_base import RipioRequestData


def test_hyperliquid_accepts_public_private_key_aliases() -> None:
    request_data = HyperliquidRequestData(
        public_key="public-key",
        private_key="0x59c6995e998f97a5a0044966f0945382d6f7d28e17f72c0f0f6f7d7f9d1c1b11",
    )

    assert request_data.api_key == "public-key"
    assert request_data.private_key.startswith("0x59c699")
    assert request_data.address is not None


@pytest.mark.asyncio
async def test_hyperliquid_async_request_uses_shared_http_client(monkeypatch: Any) -> None:
    request_data = HyperliquidRequestData(public_key="public-key")
    async_request_mock = AsyncMock(return_value={"status": "ok", "data": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request(
        "/info",
        body={"type": "allMids"},
        extra_data={"request_type": "get_tick"},
        timeout=9,
    )

    assert isinstance(result, RequestData)
    async_request_mock.assert_awaited_once()
    _, kwargs = async_request_mock.await_args
    assert kwargs["method"] == "POST"
    assert str(kwargs["url"]).endswith("/info")
    assert kwargs["timeout"] == 9
    assert kwargs["json_data"] == {"type": "allMids"}
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["headers"]["X-API-Key"] == "public-key"


def test_hyperliquid_make_request_uses_shared_http_client(monkeypatch: Any) -> None:
    request_data = HyperliquidRequestData(public_key="public-key")
    captured: dict[str, object] = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "data": []}

    monkeypatch.setattr(request_data._http_client, "request", fake_request)

    response = request_data._make_request("get_meta", type="meta")

    assert response == {"status": "ok", "data": []}
    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith(request_data._params.get_rest_path("get_meta"))
    assert captured["json_data"] == {"type": "meta"}
    assert captured["headers"]["X-API-Key"] == "public-key"


def test_hyperliquid_make_signed_request_uses_shared_http_client(monkeypatch: Any) -> None:
    request_data = HyperliquidRequestData(
        public_key="public-key",
        private_key="0x59c6995e998f97a5a0044966f0945382d6f7d28e17f72c0f0f6f7d7f9d1c1b11",
    )
    captured: dict[str, object] = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "data": []}

    monkeypatch.setattr(request_data._http_client, "request", fake_request)

    response = request_data._make_signed_request("cancel_order", oid=123)

    assert response == {"status": "ok", "data": []}
    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith(request_data._params.get_rest_path("cancel_order"))
    assert captured["json_data"] == {"oid": 123}
    assert captured["headers"]["Content-Type"] == "application/json"


def test_dydx_accepts_public_private_key_aliases() -> None:
    request_data = DydxRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.private_key == "secret-key"


def test_pancakeswap_accepts_public_private_key_aliases() -> None:
    request_data = PancakeSwapRequestData(
        public_key="public-key",
        private_key="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"


def test_ripio_accepts_public_private_key_aliases(monkeypatch: Any) -> None:
    request_data = RipioRequestData(public_key="public-key", private_key="secret-key")

    timestamp = "1710000000000"
    monkeypatch.setattr("time.time", lambda: 1710000000.0)
    headers = request_data._build_headers("GET", "/api/v1/balances", is_sign=True)

    assert request_data.api_key == "public-key"
    assert request_data.api_secret == "secret-key"
    assert headers["X-API-KEY"] == "public-key"
    assert headers["X-API-TIMESTAMP"] == timestamp
    assert headers["X-API-SIGNATURE"]
