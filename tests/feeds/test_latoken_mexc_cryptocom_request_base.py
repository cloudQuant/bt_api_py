from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_cryptocom.request_base import CryptoComRequestData
from bt_api_py.feeds.live_latoken.request_base import LatokenRequestData
from bt_api_py.feeds.live_mexc.request_base import MexcRequestData


def test_mexc_defaults_exchange_name_for_http_client() -> None:
    request_data = MexcRequestData(public_key="public-key", private_key="secret-key")

    assert request_data.exchange_name == "MEXC___SPOT"
    assert request_data._http_client._venue == "MEXC___SPOT"


def test_mexc_disconnect_closes_http_client() -> None:
    request_data = MexcRequestData(public_key="public-key", private_key="secret-key")
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


def test_latoken_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = LatokenRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="LATOKEN___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"status": "SUCCESS"},
    )

    result = request_data.request("GET /v2/trading/pairs")

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"status": "SUCCESS"}


@pytest.mark.asyncio
async def test_mexc_async_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = MexcRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="MEXC___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"code": 0, "data": []})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /api/v3/time", is_sign=False)

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": 0, "data": []}


def test_cryptocom_request_allows_missing_extra_data(monkeypatch) -> None:
    request_data = CryptoComRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="CRYPTOCOM___SPOT",
    )

    monkeypatch.setattr(
        request_data,
        "http_request",
        lambda method, url, headers, body, timeout: {"code": 0, "result": {}},
    )

    result = request_data.request("GET /public/get-time", is_sign=False)

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": 0, "result": {}}


def test_cryptocom_disconnect_closes_http_client() -> None:
    request_data = CryptoComRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="CRYPTOCOM___SPOT",
    )
    request_data._http_client.close = MagicMock()

    request_data.disconnect()

    request_data._http_client.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_cryptocom_async_request_uses_initialized_http_client(monkeypatch) -> None:
    request_data = CryptoComRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="CRYPTOCOM___SPOT",
    )

    async_request_mock = AsyncMock(return_value={"code": 0, "result": {"server_time": 1}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    result = await request_data.async_request("GET /public/get-time", is_sign=False)

    assert isinstance(result, RequestData)
    assert result.get_extra_data() == {}
    assert result.get_input_data() == {"code": 0, "result": {"server_time": 1}}
    async_request_mock.assert_awaited_once_with(
        method="GET",
        url=f"{request_data._params.rest_url}/public/get-time",
        headers={"Content-Type": "application/json"},
        json_data=None,
        timeout=10,
    )


@pytest.mark.asyncio
async def test_cryptocom_async_request_passes_signed_payload_via_json_data(monkeypatch) -> None:
    request_data = CryptoComRequestData(
        public_key="public-key",
        private_key="secret-key",
        exchange_name="CRYPTOCOM___SPOT",
    )

    monkeypatch.setattr(request_data, "sign", lambda api_method, params, req_id, nonce: "signed")
    async_request_mock = AsyncMock(return_value={"code": 0, "result": {}})
    monkeypatch.setattr(request_data._http_client, "async_request", async_request_mock)

    await request_data.async_request(
        "GET /private/get-account-summary",
        params={"instrument_name": "BTC_USDT"},
        timeout=7,
        is_sign=True,
    )

    awaited_kwargs = async_request_mock.await_args.kwargs
    assert awaited_kwargs["method"] == "POST"
    assert awaited_kwargs["url"] == f"{request_data._params.rest_url}/private/get-account-summary"
    assert awaited_kwargs["headers"] == {"Content-Type": "application/json"}
    assert awaited_kwargs["timeout"] == 7
    assert awaited_kwargs["json_data"]["method"] == "private/get-account-summary"
    assert awaited_kwargs["json_data"]["api_key"] == "public-key"
    assert awaited_kwargs["json_data"]["params"] == {"instrument_name": "BTC_USDT"}
    assert awaited_kwargs["json_data"]["sig"] == "signed"


def test_mexc_accepts_api_key_and_api_secret_aliases() -> None:
    request_data = MexcRequestData(api_key="public-key", api_secret="secret-key")

    assert request_data.public_key == "public-key"
    assert request_data.private_key == "secret-key"
