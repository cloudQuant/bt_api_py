from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_kucoin.request_base import KuCoinRequestData
from bt_api_py.feeds.live_okx.request_base import OkxRequestData


def test_okx_defaults_exchange_name() -> None:
    request_data = OkxRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        passphrase="passphrase",
    )

    assert request_data.exchange_name == "OKX___SWAP"


def test_kucoin_defaults_exchange_name() -> None:
    request_data = KuCoinRequestData(
        public_key="public-key",
        private_key="secret-key",
        passphrase="passphrase",
    )

    assert request_data.exchange_name == "KUCOIN___SPOT"


def test_okx_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = OkxRequestData(
        None,
        public_key="public-key",
        private_key="secret-key",
        passphrase="passphrase",
        exchange_name="OKX___SWAP",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"code": "0", "data": []},
    )

    result = request_data.request("GET /api/v5/public/time")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": "0", "data": []}


@pytest.mark.asyncio
async def test_kucoin_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = KuCoinRequestData(
        public_key="public-key",
        private_key="secret-key",
        passphrase="passphrase",
        exchange_name="KUCOIN___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"code": "200000", "data": {"serverTime": 1}})
    monkeypatch.setattr(request_data, "async_http_request", async_request_mock)

    result = await request_data.async_request("GET /api/v1/timestamp")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": "200000", "data": {"serverTime": 1}}


def test_okx_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = OkxRequestData(
        None,
        api_key="public-key",
        api_secret="secret-key",
        passphrase="passphrase",
    )

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"


def test_kucoin_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = KuCoinRequestData(
        api_key="public-key",
        api_secret="secret-key",
        passphrase="passphrase",
    )

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
