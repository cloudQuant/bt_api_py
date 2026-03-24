from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitrue.request_base import BitrueRequestData
from bt_api_py.feeds.live_independent_reserve.request_base import IndependentReserveRequestData


@pytest.mark.asyncio
async def test_bitrue_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = BitrueRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITRUE___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"serverTime": 1710000000000})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /api/v1/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"serverTime": 1710000000000}
    async_request_mock.assert_awaited_once()
    _, kwargs = async_request_mock.await_args
    assert kwargs["method"] == "GET"
    assert str(kwargs["url"]).endswith("/api/v1/time")
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json_data"] is None


@pytest.mark.asyncio
async def test_independent_reserve_async_request_signs_private_body_with_shared_http_client(
    monkeypatch,
) -> None:
    request_data = IndependentReserveRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="INDEPENDENT_RESERVE___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"Data": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)
    monkeypatch.setattr("time.time", lambda: 1710000000.0)

    result = await request_data.async_request(
        "POST /Private/GetAccounts",
        body={"foo": "bar"},
    )

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"Data": []}
    async_request_mock.assert_awaited_once()
    _, kwargs = async_request_mock.await_args
    assert kwargs["method"] == "POST"
    assert str(kwargs["url"]).endswith("/Private/GetAccounts")
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["json_data"]["foo"] == "bar"
    assert kwargs["json_data"]["apiKey"] == "public-key"
    assert kwargs["json_data"]["nonce"] == 1710000000000
    assert kwargs["json_data"]["signature"]
