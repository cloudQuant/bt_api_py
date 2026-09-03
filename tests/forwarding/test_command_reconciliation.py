"""Forwarded command fidelity, idempotency collision, and reconciliation."""

from __future__ import annotations

import asyncio

import pytest

from bt_api_py._contracts.errors import CommandResultUnknownError, ProtocolCorrelationError
from bt_api_py._contracts.models import CommandStatus
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.router import OrderRouter
from bt_api_py.forwarding.schema import CommandAck, OrderCommand


def test_wire_command_keeps_all_typed_order_intent_and_fingerprint() -> None:
    command = ForwardingClient()._payload_to_order_command(
        {
            "exchange": "SIM",
            "market_type": "SPOT",
            "strategy_id": "strategy-1",
            "account_id": "acct-1",
            "symbol": "BTC-USDT",
            "side": "buy",
            "quantity": 2,
            "order_type": "limit",
            "price": 100,
            "time_in_force": "IOC",
            "reduce_only": True,
            "client_order_id": "client-1",
            "idempotency_key": "idem-1",
        }
    )

    assert command.account_id == "acct-1"
    assert command.strategy_id == "strategy-1"
    assert command.time_in_force == "IOC"
    assert command.reduce_only is True
    assert command.client_order_id == "client-1"
    assert command.idempotency_key == "idem-1"
    assert len(command.request_fingerprint) == 64


@pytest.mark.asyncio
async def test_router_rejects_same_idempotency_key_with_different_fingerprint() -> None:
    router = OrderRouter(MockBrokerAdapter())
    await router.connect()
    first = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500,
        idempotency_key="same-key",
    )
    conflicting = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=2,
        order_type="limit",
        price=3500,
        idempotency_key="same-key",
    )

    await router.handle_command(first)
    with pytest.raises(ProtocolCorrelationError, match="idempotency_key"):
        await router.handle_command(conflicting)


def test_timeout_carries_command_correlation_for_reconciliation() -> None:
    class TimeoutBus(InMemoryForwardingBus):
        def send_command_sync(
            self, command: OrderCommand, *, timeout: float | None = None
        ) -> CommandAck:
            raise TimeoutError("timed out")

    client = ForwardingClient(bus=TimeoutBus(), command_timeout=0.01)
    with pytest.raises(CommandResultUnknownError) as error:
        client.submit_order(
            {
                "symbol": "RB2510",
                "side": "buy",
                "size": 1,
                "order_type": "limit",
                "price": 3500,
            }
        )

    assert error.value.command_id
    assert error.value.idempotency_key


def test_client_reconciles_terminal_command_status() -> None:
    bus = InMemoryForwardingBus()
    router = OrderRouter(MockBrokerAdapter(), bus=bus)
    asyncio.run(router.connect())
    client = ForwardingClient(bus=bus, account_id="paper", strategy_id="s1")
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="reconcile-1",
    )
    ack = client._send_command_sync(command)

    status = client.get_command_status(ack.command_id)

    assert isinstance(status, CommandStatus)
    assert status.command_id == ack.command_id
    assert status.status == "succeeded"
