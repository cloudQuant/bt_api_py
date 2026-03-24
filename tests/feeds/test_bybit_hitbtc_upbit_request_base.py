from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bybit.request_base import BybitRequestData
from bt_api_py.feeds.live_hitbtc.request_base import HitBtcRequestData
from bt_api_py.feeds.live_upbit.request_base import UpbitRequestData


def test_bybit_defaults_exchange_name() -> None:
    request_data = BybitRequestData(public_key="public-key", private_key="secret-key")

    assert request_data.exchange_name == "BYBIT___SPOT"


def test_bybit_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BybitRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BYBIT___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"retCode": 0, "result": {}},
    )

    result = request_data.request("GET /v5/market/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"retCode": 0, "result": {}}


def test_upbit_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = UpbitRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="UPBIT___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: [{"market": "KRW-BTC"}],
    )

    result = request_data.request("GET /v1/market/all", is_sign=False)

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == [{"market": "KRW-BTC"}]


@pytest.mark.asyncio
async def test_upbit_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = UpbitRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="UPBIT___SPOT",
    )

    async_request_mock = AsyncMock(return_value=[{"market": "KRW-BTC"}])
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /v1/market/all", is_sign=False)

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == [{"market": "KRW-BTC"}]


@pytest.mark.asyncio
async def test_hitbtc_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = HitBtcRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="HITBTC___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"timestamp": "2024-01-01T00:00:00.000Z"})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /api/3/public/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"timestamp": "2024-01-01T00:00:00.000Z"}


def test_bybit_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = BybitRequestData(api_key="public-key", api_secret="secret-key")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
