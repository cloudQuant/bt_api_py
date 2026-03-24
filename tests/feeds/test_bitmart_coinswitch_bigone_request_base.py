from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bigone.request_base import BigONERequestData
from bt_api_py.feeds.live_bitmart.request_base import BitmartRequestData
from bt_api_py.feeds.live_coinswitch.request_base import CoinSwitchRequestData


@pytest.mark.asyncio
async def test_bitmart_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = BitmartRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITMART___SPOT",
    )

    async_mock = AsyncMock(return_value={"code": 1000, "data": {"server_time": 1}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /system/time")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"code": 1000, "data": {"server_time": 1}}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_coinswitch_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = CoinSwitchRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINSWITCH___SPOT",
    )

    async_mock = AsyncMock(return_value={"success": True, "data": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /trade/api/v2/coins")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"success": True, "data": []}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_bigone_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = BigONERequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BIGONE___SPOT",
    )

    async_mock = AsyncMock(return_value={"code": 0, "data": {"timestamp": "1710000000"}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /asset_pairs")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"code": 0, "data": {"timestamp": "1710000000"}}
    async_mock.assert_awaited_once()
