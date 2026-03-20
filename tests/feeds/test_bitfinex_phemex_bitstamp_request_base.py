from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bitfinex.request_base import BitfinexRequestData
from bt_api_py.feeds.live_bitstamp.request_base import BitstampRequestData
from bt_api_py.feeds.live_phemex.request_base import PhemexRequestData


def test_bitfinex_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BitfinexRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITFINEX___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: [1710000000000],
    )

    result = request_data.request("GET /v2/platform/status")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == [1710000000000]


@pytest.mark.asyncio
async def test_phemex_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = PhemexRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="PHEMEX___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"code": 0, "data": {}})
    monkeypatch.setattr(request_data, "async_http_request", async_request_mock)

    result = await request_data.async_request("GET /public/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": 0, "data": {}}


def test_bitstamp_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = BitstampRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="BITSTAMP___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"server_time": 1710000000},
    )

    result = request_data.request("GET /api/v2/timestamp/")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"server_time": 1710000000}
