"""Concrete command and client-scope paths for the ZMQ operation backend."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bt_api_py._contracts.models import (
    CancelAllRequest,
    CancelOrderRequest,
    CommandStatus,
    ForwardingConfig,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
)
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend
from bt_api_py.forwarding.schema import CommandAck, OrderCommand


def _config() -> ForwardingConfig:
    return ForwardingConfig(
        command_endpoint="inproc://commands",
        market_endpoint="inproc://market",
        private_endpoint="inproc://private",
        account_id="acct-1",
        strategy_id="strategy-1",
    )


class _CommandClient:
    def __init__(self) -> None:
        self.commands: list[OrderCommand] = []

    def _send_command_sync(self, command: OrderCommand) -> CommandAck:
        self.commands.append(command)
        return CommandAck(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key or "",
            accepted=True,
            status="accepted",
            account_id=command.account_id,
            strategy_id=command.strategy_id,
            order_id=command.order_id,
        )

    def get_command_status(self, command_id: str) -> CommandStatus:
        return CommandStatus(
            command_id=command_id,
            idempotency_key="idem-1",
            status="succeeded",
            account_id="acct-1",
            strategy_id="strategy-1",
            accepted=True,
        )


def test_backend_preserves_typed_command_intent_and_status_reconciliation() -> None:
    backend = ZmqBtApiBackend(_config())
    client = _CommandClient()
    backend._client = client
    request = OrderRequest(
        symbol="BTC-USDT",
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        price=Decimal("101"),
        account_id="acct-1",
        client_order_id="client-1",
        time_in_force="IOC",
        reduce_only=True,
        idempotency_key="place-1",
    )

    place = backend.make_order("sim___spot", request)
    cancel = backend.cancel_order(
        "SIM___SPOT",
        CancelOrderRequest(
            symbol="BTC-USDT",
            account_id="acct-1",
            order_id="order-1",
            idempotency_key="cancel-1",
        ),
    )
    cancel_all = backend.cancel_all(
        "SIM___SPOT",
        CancelAllRequest(account_id="acct-1", symbol="BTC-USDT", idempotency_key="cancel-all-1"),
    )
    query = backend.query_order(
        "SIM___SPOT",
        QueryOrderRequest(symbol="BTC-USDT", account_id="acct-1", order_id="order-1"),
    )

    assert all(ack.accepted for ack in (place, cancel, cancel_all, query))
    assert [command.command_type for command in client.commands] == [
        "place_order",
        "cancel_order",
        "cancel_all",
        "query_order",
    ]
    place_command = client.commands[0]
    assert place_command.exchange == "SIM"
    assert place_command.market_type == "SPOT"
    assert place_command.time_in_force == "IOC"
    assert place_command.reduce_only is True
    assert place_command.client_order_id == "client-1"
    assert place_command.idempotency_key == "place-1"
    assert backend.get_command_status("SIM___SPOT", "command-1").status == "succeeded"
    assert backend.get_capabilities("SIM___SPOT")["get_command_status"] is True


def test_backend_creates_and_reuses_clients_per_normalized_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []

    class _ScopedClient:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr("bt_api_py.forwarding.client.ZmqForwardingClient", _ScopedClient)
    backend = ZmqBtApiBackend(_config())

    first = backend._ensure_client("sim___spot")
    second = backend._ensure_client("SIM___SPOT")

    assert first is second
    assert created == [
        {
            "market_endpoint": "inproc://market",
            "command_endpoint": "inproc://commands",
            "private_endpoint": "inproc://private",
            "exchange": "SIM",
            "market_type": "SPOT",
            "account_id": "acct-1",
            "strategy_id": "strategy-1",
        }
    ]
    with pytest.raises(ValueError, match="EXCHANGE.*MARKET_TYPE"):
        backend._ensure_client("missing-scope")
