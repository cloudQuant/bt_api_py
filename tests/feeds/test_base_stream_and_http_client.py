from __future__ import annotations

import asyncio
import queue
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bt_api_py.exceptions import RequestFailedError
from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState
from bt_api_py.feeds.http_client import HttpClient


class _DummyStream(BaseDataStream):
    def connect(self) -> None:
        self.state = ConnectionState.CONNECTED

    def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics: list[dict[str, Any]]) -> None:
        self._topics = topics

    def _run_loop(self) -> None:
        self.state = ConnectionState.CONNECTED


def test_base_data_stream_pushes_to_queue() -> None:
    data_queue: queue.Queue[dict[str, int]] = queue.Queue()
    stream = _DummyStream(data_queue=data_queue, stream_name="dummy")

    stream.push_data({"value": 1})

    assert data_queue.get_nowait() == {"value": 1}


def test_base_data_stream_wait_connected_succeeds_when_state_is_connected() -> None:
    stream = _DummyStream(stream_name="dummy")
    stream.state = ConnectionState.CONNECTED

    assert stream.wait_connected(timeout=0.01, interval=0.001) is True


def test_http_client_request_adds_cookie_header() -> None:
    with patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls:
        sync_client = MagicMock()
        sync_client.request.return_value = httpx.Response(200, json={"ok": True})
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        client = HttpClient(venue="TEST")
        result = client.request(
            "GET",
            "https://example.com",
            cookies={"session": "abc"},
        )

    assert result == {"ok": True}
    request_kwargs = sync_client.request.call_args.kwargs
    assert request_kwargs["headers"]["Cookie"] == "session=abc"


def test_http_client_explicit_empty_proxies_disable_env_proxy_inheritance() -> None:
    with patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls:
        sync_client = MagicMock()
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        HttpClient(venue="TEST", proxies={})

    init_kwargs = client_cls.call_args.kwargs
    assert init_kwargs["trust_env"] is False


def test_http_client_process_response_raises_for_404() -> None:
    with patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls:
        sync_client = MagicMock()
        sync_client.request.return_value = httpx.Response(404, json={"msg": "not found"})
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        client = HttpClient(venue="TEST")
        with pytest.raises(RequestFailedError):
            client.request("GET", "https://example.com/missing")


def test_http_client_request_wraps_generic_httpx_request_error() -> None:
    with patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls:
        sync_client = MagicMock()
        sync_client.request.side_effect = httpx.RequestError(
            "broken transport",
            request=httpx.Request("GET", "https://example.com"),
        )
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        client = HttpClient(venue="TEST")

        with pytest.raises(
            RequestFailedError, match="HTTP client error: broken transport"
        ) as exc_info:
            client.request("GET", "https://example.com")

    assert exc_info.value.venue == "TEST"


@pytest.mark.asyncio
async def test_http_client_async_request_wraps_generic_httpx_request_error() -> None:
    with (
        patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls,
        patch("bt_api_py.feeds.http_client.httpx.AsyncClient") as async_client_cls,
    ):
        sync_client = MagicMock()
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        async_client = AsyncMock()
        async_client.is_closed = False
        async_client.request.side_effect = httpx.RequestError(
            "async transport failure",
            request=httpx.Request("GET", "https://example.com"),
        )
        async_client_cls.return_value = async_client

        client = HttpClient(venue="TEST")

        with pytest.raises(
            RequestFailedError,
            match="Async HTTP client error: async transport failure",
        ) as exc_info:
            await client.async_request("GET", "https://example.com")

    assert exc_info.value.venue == "TEST"


def test_http_client_close_closes_initialized_async_client_without_running_loop() -> None:
    with (
        patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls,
        patch("bt_api_py.feeds.http_client.httpx.AsyncClient") as async_client_cls,
    ):
        sync_client = MagicMock()
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        async_client = AsyncMock()
        async_client.is_closed = False
        async_client_cls.return_value = async_client

        client = HttpClient(venue="TEST")
        client._async_client = async_client
        client.close()

    sync_client.close.assert_called_once_with()
    async_client.aclose.assert_awaited_once()
    assert client._async_client is None


@pytest.mark.asyncio
async def test_http_client_close_closes_initialized_async_client_with_running_loop() -> None:
    with (
        patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls,
        patch("bt_api_py.feeds.http_client.httpx.AsyncClient") as async_client_cls,
    ):
        sync_client = MagicMock()
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        async_client = AsyncMock()
        async_client.is_closed = False
        async_client_cls.return_value = async_client

        client = HttpClient(venue="TEST")
        client._async_client = async_client
        client.close()
        await asyncio.sleep(0)

    sync_client.close.assert_called_once_with()
    async_client.aclose.assert_awaited_once()
    assert client._async_client is None
