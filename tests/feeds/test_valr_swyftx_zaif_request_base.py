from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_swyftx.request_base import SwyftxRequestData
from bt_api_py.feeds.live_valr.request_base import ValrRequestData
from bt_api_py.feeds.live_zaif.request_base import ZaifRequestData


@pytest.mark.asyncio
async def test_valr_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = ValrRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="VALR___SPOT",
    )

    async_mock = AsyncMock(return_value=[{"currencyPair": "BTCZAR"}])
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /v1/public/pairs")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == [{"currencyPair": "BTCZAR"}]
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_swyftx_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = SwyftxRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        exchange_name="SWYFTX___SPOT",
    )

    async_mock = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /markets/info/basic/")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"status": "ok"}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_zaif_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = ZaifRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="ZAIF___SPOT",
    )

    async_mock = AsyncMock(return_value={"last_price": 5000000, "bid": 4999000})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /api/1/ticker/btc_jpy")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"last_price": 5000000, "bid": 4999000}
    async_mock.assert_awaited_once()
