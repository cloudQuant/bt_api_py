from __future__ import annotations

import base64
import queue
from concurrent.futures import Future
from unittest.mock import MagicMock

from bt_api_py.feeds.live_luno.request_base import LunoRequestData
from bt_api_py.feeds.live_luno.spot import LunoRequestDataSpot


def test_luno_request_data_builds_basic_auth_header() -> None:
    feed = LunoRequestData()
    feed._params.api_key = "key"
    feed._params.api_secret = "secret"

    headers = feed._get_auth_headers()

    expected = base64.b64encode(b"key:secret").decode("utf-8")
    assert headers["Authorization"] == f"Basic {expected}"


def test_luno_request_data_accepts_public_private_key_aliases() -> None:
    feed = LunoRequestData(public_key="key", private_key="secret")

    headers = feed._get_auth_headers()

    expected = base64.b64encode(b"key:secret").decode("utf-8")
    assert headers["Authorization"] == f"Basic {expected}"


def test_luno_request_data_resolves_exchange_url_for_market_metadata() -> None:
    feed = LunoRequestData()

    assert feed._resolve_url("/markets") == feed._params.rest_exchange_url
    assert feed._resolve_url("/candles") == feed._params.rest_exchange_url
    assert feed._resolve_url("/ticker") == feed._params.rest_url


def test_luno_spot_get_tick_prepares_request_data() -> None:
    feed = LunoRequestDataSpot()

    path, params, extra_data = feed._get_tick("XBTZAR")

    assert path == "GET /ticker"
    assert params == {"pair": "XBTZAR"}
    assert extra_data["request_type"] == "get_tick"
    assert extra_data["symbol_name"] == "XBTZAR"


def test_luno_spot_kline_normalizer_returns_candles_payload() -> None:
    normalized, ok = LunoRequestDataSpot._get_kline_normalize_function(
        {"candles": [{"close": "1"}]},
        {},
    )

    assert ok is True
    assert normalized == [[{"close": "1"}]]


def test_luno_request_data_async_callback_pushes_result_to_queue() -> None:
    data_queue: queue.Queue[object] = queue.Queue()
    feed = LunoRequestData(data_queue=data_queue)
    future: Future[object] = Future()
    future.set_result({"ok": True})

    feed.async_callback(future)

    assert data_queue.get_nowait() == {"ok": True}


def test_luno_disconnect_closes_http_client() -> None:
    feed = LunoRequestData()
    feed._http_client.close = MagicMock()

    feed.disconnect()

    feed._http_client.close.assert_called_once_with()
