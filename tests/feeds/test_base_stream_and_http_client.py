from __future__ import annotations

import queue
from typing import Any
from unittest.mock import MagicMock, patch

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


def test_http_client_process_response_raises_for_404() -> None:
    with patch("bt_api_py.feeds.http_client.httpx.Client") as client_cls:
        sync_client = MagicMock()
        sync_client.request.return_value = httpx.Response(404, json={"msg": "not found"})
        sync_client.is_closed = False
        client_cls.return_value = sync_client

        client = HttpClient(venue="TEST")
        with pytest.raises(RequestFailedError):
            client.request("GET", "https://example.com/missing")
