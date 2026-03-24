from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_coinone.request_base import CoinoneRequestData
from bt_api_py.feeds.live_korbit.request_base import KorbitRequestData
from bt_api_py.feeds.live_localbitcoins.request_base import LocalBitcoinsRequestData


@pytest.mark.asyncio
async def test_localbitcoins_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = LocalBitcoinsRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        exchange_name="LOCALBITCOINS___SPOT",
    )

    async_mock = AsyncMock(return_value={"data": {"ad_list": []}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /api/ad-get/")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"data": {"ad_list": []}}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_coinone_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = CoinoneRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINONE___SPOT",
    )

    async_mock = AsyncMock(return_value={"result": "success", "errorCode": "0"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /ticker/")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"result": "success", "errorCode": "0"}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_korbit_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = KorbitRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="KORBIT___SPOT",
    )

    async_mock = AsyncMock(return_value={"timestamp": 1710000000, "last": "50000"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /v1/ticker/detailed")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"timestamp": 1710000000, "last": "50000"}
    async_mock.assert_awaited_once()
