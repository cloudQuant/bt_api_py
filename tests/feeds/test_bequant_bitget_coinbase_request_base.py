from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bequant.request_base import BeQuantRequestData
from bt_api_py.feeds.live_bitget.request_base import BitgetRequestData
from bt_api_py.feeds.live_coinbase.request_base import CoinbaseRequestData


def test_bitget_defaults_exchange_name() -> None:
    request_data = BitgetRequestData(public_key="public-key", private_key="secret-key")

    assert request_data.exchange_name == "BITGET___SPOT"


def test_bequant_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BeQuantRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BEQUANT___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"timestamp": "2024-01-01T00:00:00.000Z"},
    )

    result = request_data.request("GET /api/3/public/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"timestamp": "2024-01-01T00:00:00.000Z"}


@pytest.mark.asyncio
async def test_bequant_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BeQuantRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BEQUANT___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"timestamp": "2024-01-01T00:00:00.000Z"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /api/3/public/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"timestamp": "2024-01-01T00:00:00.000Z"}


@pytest.mark.asyncio
async def test_bitget_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BitgetRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITGET___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"code": "00000", "data": {}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /api/v2/spot/public/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": "00000", "data": {}}


def test_coinbase_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = CoinbaseRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINBASE___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"epochMillis": "1710000000000"},
    )

    result = request_data.request("GET /brokerage/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"epochMillis": "1710000000000"}


def test_bitget_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = BitgetRequestData(api_key="public-key", api_secret="secret-key")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"


@pytest.mark.asyncio
async def test_coinbase_async_request_uses_shared_http_client(monkeypatch) -> None:
    request_data = CoinbaseRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        exchange_name="COINBASE___SPOT",
    )

    async_mock = AsyncMock(return_value={"epochMillis": "1710000000000"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_mock)

    result = await request_data.async_request("GET /brokerage/time")

    assert isinstance(result, RequestData)
    assert result.get_input_data() == {"epochMillis": "1710000000000"}
    async_mock.assert_awaited_once()


def test_coinbase_accepts_api_secret_aliases() -> None:
    request_data = CoinbaseRequestData(
        None,
        api_key="public-key",
        api_secret="secret-key",
    )

    assert request_data.api_key == "public-key"
    assert request_data.private_key == "secret-key"
