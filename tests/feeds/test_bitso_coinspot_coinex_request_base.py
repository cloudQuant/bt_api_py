from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitso.request_base import BitsoRequestData
from bt_api_py.feeds.live_coinex.request_base import CoinExRequestData
from bt_api_py.feeds.live_coinspot.request_base import CoinSpotRequestData


def test_bitso_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BitsoRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITSO___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"success": True, "payload": []},
    )

    result = request_data.request("GET /available_books")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"success": True, "payload": []}


@pytest.mark.asyncio
async def test_coinspot_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = CoinSpotRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINSPOT___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"status": "ok", "prices": {}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /pubapi/v2/latest")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"status": "ok", "prices": {}}


@pytest.mark.asyncio
async def test_bitso_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = BitsoRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITSO___SPOT",
    )

    async_mock = AsyncMock(return_value={"success": True, "payload": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /available_books")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"success": True, "payload": []}
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_coinex_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = CoinExRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINEX___SPOT",
    )

    async_mock = AsyncMock(return_value={"code": 0, "data": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /spot/market")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"code": 0, "data": []}
    async_mock.assert_awaited_once()


def test_coinex_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = CoinExRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINEX___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"code": 0, "data": []},
    )

    result = request_data.request("GET /spot/market")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": 0, "data": []}
