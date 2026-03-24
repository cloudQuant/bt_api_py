from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_dydx.request_base import DydxRequestData
from bt_api_py.feeds.live_wazirx.request_base import WazirxRequestData
from bt_api_py.feeds.live_yobit.request_base import YobitRequestData
from bt_api_py.feeds.live_zebpay.request_base import ZebpayRequestData


@pytest.mark.asyncio
async def test_dydx_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = DydxRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        exchange_name="DYDX___SPOT",
    )

    async_mock = AsyncMock(return_value={"markets": {}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /v3/markets")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"markets": {}}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_yobit_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = YobitRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="YOBIT___SPOT",
    )

    async_mock = AsyncMock(return_value={"server_time": 1710000000})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /api/3/info")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"server_time": 1710000000}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_wazirx_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = WazirxRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="WAZIRX___SPOT",
    )

    async_mock = AsyncMock(return_value={"serverTime": 1710000000})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /sapi/v1/time")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"serverTime": 1710000000}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_zebpay_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = ZebpayRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="ZEBPAY___SPOT",
    )

    async_mock = AsyncMock(return_value={"pair": "BTC-INR", "buy": "5000000"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /api/v1/ticker")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"pair": "BTC-INR", "buy": "5000000"}
    async_mock.assert_awaited_once()
