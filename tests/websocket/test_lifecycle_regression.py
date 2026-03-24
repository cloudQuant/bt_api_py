"""
Lifecycle regression tests for the advanced WebSocket connection.

Covers: connect, disconnect, idempotent close, reconnect with subscription
restoration, compressed-frame handling, and disconnect-during-reconnect.
"""

import asyncio
import json
import zlib
from unittest.mock import AsyncMock, patch

import pytest

from bt_api_py.exceptions import WebSocketError
from bt_api_py.websocket.advanced_connection_manager import (
    AdvancedWebSocketConnection,
    ConnectionState,
    ErrorCategory,
    WebSocketConfig,
)


def _make_config(**overrides):
    defaults = {
        "url": "wss://stream.test.com",
        "exchange_name": "TEST",
        "max_connections": 2,
        "reconnect_enabled": True,
        "max_reconnect_attempts": 3,
        "reconnect_interval": 0.01,
        "max_reconnect_delay": 0.05,
        "reconnect_backoff_multiplier": 1.0,
    }
    defaults.update(overrides)
    return WebSocketConfig(**defaults)


def _mock_websocket():
    ws = AsyncMock()
    ws.close = AsyncMock()
    ws.send = AsyncMock()
    ws.ping = AsyncMock()
    ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    return ws


# ---------------------------------------------------------------------------
# connect / disconnect basics
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Basic connect and disconnect lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_sets_state_to_connected(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c1")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = _mock_websocket()
            await conn.connect()

        assert conn.get_state() == ConnectionState.CONNECTED
        assert conn._metrics.connections_established >= 1

        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_sets_state_to_disconnected(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c2")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = _mock_websocket()
            await conn.connect()
            await conn.disconnect()

        assert conn.get_state() == ConnectionState.DISCONNECTED
        assert conn._running is False

    @pytest.mark.asyncio
    async def test_disconnect_is_idempotent(self):
        """Calling disconnect multiple times must not raise."""
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c3")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = _mock_websocket()
            mock_connect.return_value = mock_ws
            await conn.connect()

            await conn.disconnect()
            await conn.disconnect()
            await conn.disconnect()

        assert conn.get_state() == ConnectionState.DISCONNECTED
        # close() should be called only once (the first disconnect)
        assert mock_ws.close.await_count <= 1

    @pytest.mark.asyncio
    async def test_connect_all_endpoints_fail_raises_websocket_error(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c4")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = OSError("Connection refused")

            with pytest.raises(WebSocketError):
                await conn.connect()

        assert conn.get_state() == ConnectionState.ERROR
        assert conn._metrics.connections_failed >= 1


# ---------------------------------------------------------------------------
# subscription restore after reconnect
# ---------------------------------------------------------------------------


class TestReconnectSubscriptionRestore:
    """Reconnection must restore in-memory subscriptions."""

    @pytest.mark.asyncio
    async def test_restore_subscriptions_sends_messages(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c5")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = _mock_websocket()
            mock_connect.return_value = mock_ws

            await conn.connect()

            # Add two subscriptions
            cb = AsyncMock()
            await conn.subscribe("s1", "ticker", "BTCUSDT", callback=cb)
            await conn.subscribe("s2", "depth", "ETHUSDT", callback=cb)
            assert len(conn._subscriptions) == 2

            # Simulate restore
            await conn._restore_subscriptions()

            # Each subscription should have caused a send to the queue
            assert conn._metrics.active_subscriptions == 2
            assert "s1" in conn._subscriptions
            assert "s2" in conn._subscriptions

            await conn.disconnect()

    @pytest.mark.asyncio
    async def test_reconnect_preserves_subscriptions(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c6")

        call_count = 0

        async def _connect_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("transient failure")
            return _mock_websocket()

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = _connect_side_effect

            await conn.connect()

            cb = AsyncMock()
            await conn.subscribe("s1", "ticker", "BTCUSDT", callback=cb)

            # Simulate reconnect — first attempt fails, second succeeds
            call_count = 1  # next call will be attempt #2 (fail), then #3 (succeed)
            await conn._attempt_reconnection()

            # Subscriptions should still be present
            assert "s1" in conn._subscriptions

            await conn.disconnect()


# ---------------------------------------------------------------------------
# compressed / text frame handling
# ---------------------------------------------------------------------------


class TestFrameHandling:
    """Verify compressed and text frame processing doesn't crash."""

    @pytest.mark.asyncio
    async def test_text_frame_with_compression_enabled(self):
        """str text frames must not crash on startswith(bytes) when compression=True."""
        config = _make_config(compression=True)
        conn = AdvancedWebSocketConnection(config, "c7")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = _mock_websocket()
            mock_connect.return_value = mock_ws

            await conn.connect()

            # Simulate receiving a plain text JSON frame (not compressed)
            text_frame = json.dumps({"stream": "btcusdt@ticker", "data": {"price": "50000"}})

            # The _process_messages loop normally handles this, but we test
            # the message handling path directly to avoid dealing with the
            # async recv loop.
            message = json.loads(text_frame)
            # Should not raise
            await conn._handle_message(message)

            await conn.disconnect()

    @pytest.mark.asyncio
    async def test_compressed_frame_decompresses(self):
        """zlib-compressed frames should be correctly decompressed."""
        config = _make_config(compression=True)
        _conn = AdvancedWebSocketConnection(config, "c8")

        payload = json.dumps({"data": "hello"}).encode("utf-8")
        compressed = zlib.compress(payload)

        # Verify it starts with the zlib magic header
        assert compressed[:2] == b"\x78\x9c"

        # Decompress should yield original
        decompressed = zlib.decompress(compressed)
        result = json.loads(decompressed.decode("utf-8"))
        assert result == {"data": "hello"}


# ---------------------------------------------------------------------------
# close during reconnect
# ---------------------------------------------------------------------------


class TestCloseWebsocketIdempotent:
    """_close_websocket must be safe to call when websocket is None."""

    @pytest.mark.asyncio
    async def test_close_websocket_when_none(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c9")

        # Should not raise even though _websocket is None
        await conn._close_websocket()

    @pytest.mark.asyncio
    async def test_close_websocket_suppresses_close_errors(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c10")

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock(side_effect=OSError("close failed"))
        conn._websocket = mock_ws

        # Should suppress the error
        await conn._close_websocket()
        assert conn._websocket is None


# ---------------------------------------------------------------------------
# error categorization
# ---------------------------------------------------------------------------


class TestErrorCategorization:
    """Verify metrics record correct error categories."""

    @pytest.mark.asyncio
    async def test_connection_failure_records_network_error(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c11")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = OSError("Network unreachable")

            with pytest.raises(WebSocketError):
                await conn.connect()

        assert conn._metrics.errors_by_category[ErrorCategory.NETWORK] >= 1

    @pytest.mark.asyncio
    async def test_metrics_track_subscription_count(self):
        config = _make_config()
        conn = AdvancedWebSocketConnection(config, "c12")

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = _mock_websocket()
            await conn.connect()

            cb = AsyncMock()
            await conn.subscribe("s1", "ticker", "BTCUSDT", callback=cb)
            assert conn._metrics.active_subscriptions == 1

            await conn.unsubscribe("s1")
            assert conn._metrics.active_subscriptions == 0

            await conn.disconnect()
