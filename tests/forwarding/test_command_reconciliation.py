"""Command DTO fidelity remains useful after the forwarding writer retirement."""

from __future__ import annotations

import asyncio

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.router import OrderRouter
from bt_api_py.forwarding.schema import OrderCommand


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


def test_direct_router_handler_entry_fails_closed_without_constructor_state() -> None:
    router = object.__new__(OrderRouter)
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="legacy-direct-handler",
    )

    with pytest.raises(LegacyForwardingRetiredError, match="service.order_entry"):
        asyncio.run(router.handle_command(command))
