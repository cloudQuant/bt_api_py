import json
import logging

import pytest

from examples.network_tests.integration import test_websocket_infrastructure


class FakeWebSocket:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    async def recv(self):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_receive_messages_continues_after_errors_and_logs_only_error_types(caplog):
    private_message = "private-message-content"
    private_error = "private-exception-detail"
    websocket = FakeWebSocket(
        [
            RuntimeError(private_error),
            json.dumps({"payload": private_message, "id": 1}),
            "malformed private-message-content",
            json.dumps({"payload": "later-message", "id": 2}),
        ]
    )
    caplog.set_level(logging.DEBUG, logger=test_websocket_infrastructure.__name__)

    received = await test_websocket_infrastructure._receive_messages(websocket, 4)

    assert received == [
        {"payload": private_message, "id": 1},
        {"payload": "later-message", "id": 2},
    ]
    assert [record.getMessage() for record in caplog.records] == [
        "WebSocket message receive failed: RuntimeError",
        "WebSocket message receive failed: JSONDecodeError",
    ]
    assert private_error not in caplog.text
    assert private_message not in caplog.text
