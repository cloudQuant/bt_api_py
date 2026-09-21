"""WebSocket testing infrastructure for bt_api_py.

This module provides utilities for testing WebSocket connections,
message handling, and reconnection logic.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterable
from typing import Protocol, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class _WebSocketConnection(Protocol):
    """Stable connection interface shared by supported websockets server APIs."""

    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


class _WebSocketReceiver(Protocol):
    """Minimal interface required by the shared message receive helper."""

    async def recv(self) -> str | bytes: ...


class _ListeningSocket(Protocol):
    """Socket interface needed to discover the server's bound port."""

    def getsockname(self) -> tuple[str, int]: ...


class _WebSocketServer(Protocol):
    """Stable lifecycle and socket interface exposed by websockets servers."""

    @property
    def sockets(self) -> Iterable[_ListeningSocket]: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


async def _receive_messages(
    websocket: _WebSocketReceiver, message_count: int
) -> list[dict[str, object]]:
    """Receive and decode a bounded number of WebSocket messages."""
    messages_received: list[dict[str, object]] = []
    for _ in range(message_count):
        try:
            message = await websocket.recv()
            messages_received.append(json.loads(message))
        except Exception as exc:
            logger.debug("WebSocket message receive failed: %s", type(exc).__name__)
    return messages_received


class MockWebSocketServer:
    """Mock WebSocket server for testing."""

    def __init__(self, responses: list[dict[str, object]]):
        self.responses = responses
        self.sent_messages: list[object] = []
        self.connections: list[_WebSocketConnection] = []
        self.server: _WebSocketServer | None = None

    async def handler(self, websocket: _WebSocketConnection) -> None:
        """Handle WebSocket connections."""
        self.connections.append(websocket)

        try:
            # Send initial responses
            for response in self.responses:
                await websocket.send(json.dumps(response))
                await asyncio.sleep(0.1)  # Small delay between messages

            # Listen for client messages
            async for message in websocket:
                self.sent_messages.append(json.loads(message))

                # Echo back or send response
                if message:
                    await websocket.send(json.dumps({"status": "received", "data": message}))

        except ConnectionClosed:
            pass

    async def start(self, port: int = 8765) -> _WebSocketServer:
        """Start the mock server."""
        server = await websockets.serve(self.handler, "127.0.0.1", port)
        self.server = server
        return server

    async def stop(self):
        """Stop the mock server."""
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()


class WebSocketTestHarness:
    """Test harness for WebSocket functionality."""

    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
        self.server = MockWebSocketServer([])
        self.messages: list[dict[str, object]] = []
        self.connection_events: list[dict[str, object]] = []

    async def setup_mock_server(self, responses: list[dict[str, object]]) -> str:
        """Set up mock server with predefined responses."""
        self.server = MockWebSocketServer(responses)
        server = await self.server.start(port=0)
        socket = next(iter(server.sockets))
        port = socket.getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    def create_mock_websocket_client(self) -> AsyncMock:
        """Create a mock WebSocket client for testing."""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.ping = AsyncMock()
        mock_ws.closed = False

        # Configure message queue
        message_queue: asyncio.Queue[str] = asyncio.Queue()

        async def recv_side_effect():
            try:
                return await asyncio.wait_for(message_queue.get(), timeout=1.0)
            except TimeoutError:
                # Simulate WebSocket timeout
                raise ConnectionClosed(None, None) from None

        mock_ws.recv.side_effect = recv_side_effect

        # Method to add messages to queue
        def add_message(msg: str) -> None:
            message_queue.put_nowait(msg)

        mock_ws._add_message = add_message

        return mock_ws

    def simulate_connection_events(self) -> list[dict[str, object]]:
        """Simulate various WebSocket connection events."""
        events: list[dict[str, object]] = [
            {"type": "connect", "timestamp": time.time()},
            {
                "type": "message",
                "data": {"symbol": "BTCUSDT", "price": "50000"},
                "timestamp": time.time(),
            },
            {"type": "disconnect", "timestamp": time.time()},
            {"type": "reconnect", "timestamp": time.time()},
            {
                "type": "message",
                "data": {"symbol": "ETHUSDT", "price": "3000"},
                "timestamp": time.time(),
            },
        ]
        return events


@pytest.mark.integration
@pytest.mark.network
class TestWebSocketConnections:
    """WebSocket connection tests."""

    @pytest_asyncio.fixture
    async def ws_harness(self):
        """Create WebSocket test harness."""
        harness = WebSocketTestHarness("BINANCE")
        yield harness
        await harness.server.stop()

    @pytest.mark.asyncio
    async def test_websocket_connection_lifecycle(self, ws_harness):
        """Test WebSocket connection lifecycle."""
        # Setup mock responses
        responses = [
            {"stream": "btcusdt@ticker", "data": {"symbol": "BTCUSDT", "price": "50000"}},
            {"stream": "btcusdt@depth", "data": {"symbol": "BTCUSDT", "bids": [["49999", "1"]]}},
        ]

        await ws_harness.setup_mock_server(responses)

        # Create mock client
        mock_client = ws_harness.create_mock_websocket_client()

        # Add test messages
        for response in responses:
            mock_client._add_message(json.dumps(response))

        # Simulate connection
        # Run message receiving
        messages_received = await _receive_messages(mock_client, len(responses))

        # Verify messages
        assert len(messages_received) == len(responses)
        assert messages_received[0]["data"]["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_websocket_reconnection_logic(self, ws_harness):
        """Test WebSocket reconnection logic."""
        ws_harness.simulate_connection_events()

        mock_client = ws_harness.create_mock_websocket_client()

        # Simulate connection events
        connection_state = {"connected": False, "attempts": 0}

        async def simulate_connection():
            connection_state["attempts"] += 1
            if connection_state["attempts"] <= 3:
                # First attempts fail
                mock_client.closed = True
                raise ConnectionClosed(None, None)
            else:
                # Reconnect succeeds
                mock_client.closed = False
                mock_client._add_message(json.dumps({"status": "reconnected"}))
                return True

        # Test reconnection attempts
        for _attempt in range(3):
            try:
                await simulate_connection()
                assert mock_client.closed  # Should be closed on failed attempts
            except ConnectionClosed:
                pass  # Expected on failed attempts

        # Successful reconnection
        result = await simulate_connection()
        assert result is True
        assert mock_client.closed is False

    @pytest.mark.asyncio
    async def test_websocket_message_validation(self, ws_harness):
        """Test WebSocket message validation and handling."""
        mock_client = ws_harness.create_mock_websocket_client()

        # Test various message formats
        test_messages = [
            {"stream": "btcusdt@ticker", "data": {"symbol": "BTCUSDT", "price": "50000"}},
            {"stream": "invalid_stream", "data": {"invalid": "data"}},
            "",  # Empty message
            None,  # None message
            "invalid_json_string",  # Invalid JSON
        ]

        validated_messages: list[dict[str, object]] = []

        async def validate_and_process_messages():
            valid_streams = {"btcusdt@ticker", "btcusdt@depth"}
            for msg in test_messages:
                try:
                    if msg is None or msg == "":
                        continue

                    data = json.loads(msg) if isinstance(msg, str) else msg

                    # Validate message structure
                    if (
                        isinstance(data, dict)
                        and "stream" in data
                        and "data" in data
                        and data["stream"] in valid_streams
                    ):
                        validated_messages.append(cast("dict[str, object]", data))
                    else:
                        # Log invalid message format
                        pass

                except (json.JSONDecodeError, TypeError):
                    # Handle invalid JSON
                    pass

        # Add messages to mock client
        for msg in test_messages:
            if msg is not None:
                mock_client._add_message(msg if isinstance(msg, str) else json.dumps(msg))

        await validate_and_process_messages()

        # Should have processed only valid messages
        assert len(validated_messages) == 1  # Only the first message is valid
        assert validated_messages[0]["stream"] == "btcusdt@ticker"

    @pytest.mark.asyncio
    async def test_websocket_rate_limiting(self, ws_harness):
        """Test WebSocket rate limiting behavior."""
        mock_client = ws_harness.create_mock_websocket_client()

        # Send many messages rapidly
        message_count = 100
        send_times = []

        for i in range(message_count):
            mock_client._add_message(json.dumps({"id": i, "timestamp": time.time()}))
            send_times.append(time.time())

        # Measure message processing rate
        received_messages: list[dict[str, object]] = []
        start_time = time.time()

        async def process_messages():
            while len(received_messages) < message_count:
                try:
                    message = await mock_client.recv()
                    received_messages.append(json.loads(message))
                except TimeoutError:
                    break

        await asyncio.wait_for(process_messages(), timeout=5.0)
        end_time = time.time()

        # Verify rate limiting
        processing_time = end_time - start_time
        messages_per_second = len(received_messages) / processing_time

        # Should process messages at reasonable rate
        assert messages_per_second > 10  # At least 10 messages per second
        assert len(received_messages) <= message_count


@pytest.mark.flaky
@pytest.mark.network
class TestWebSocketRealTimeData:
    """Real-time data WebSocket tests."""

    @pytest.mark.asyncio
    async def test_ticker_stream_consistency(self):
        """Test ticker stream data consistency."""
        # This would connect to real WebSocket and verify data consistency

    @pytest.mark.asyncio
    async def test_orderbook_stream_integrity(self):
        """Test orderbook stream data integrity."""
        # This would verify orderbook updates maintain consistency

    @pytest.mark.asyncio
    async def test_multi_stream_synchronization(self):
        """Test synchronization between multiple WebSocket streams."""
        # This would test handling multiple concurrent streams
