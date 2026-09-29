from __future__ import annotations

import asyncio

import pytest

from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding.router import OrderRouter
from bt_api_py.forwarding.schema import OrderCommand, deserialize_message, serialize_message
from bt_api_py.forwarding.service import ForwardingRuntime, ZmqForwardingRuntime


class CountingBoundAdapter(MockBrokerAdapter):
    def __init__(self, *, exchange_name: str, account_id: str = "paper") -> None:
        super().__init__(account_id=account_id)
        self.exchange_name = exchange_name
        self.write_calls = {"place_order": 0, "cancel_order": 0, "list_orders": 0}

    async def place_order(self, request):
        self.write_calls["place_order"] += 1
        return await super().place_order(request)

    async def cancel_order(self, request):
        self.write_calls["cancel_order"] += 1
        return await super().cancel_order(request)

    async def list_orders(self, account_id: str):
        self.write_calls["list_orders"] += 1
        return await super().list_orders(account_id)


def _runtime(
    adapter: MockBrokerAdapter,
    *,
    exchange: str | None,
    market_type: str | None = "SPOT",
    account_id: str | None = "paper",
) -> ZmqForwardingRuntime:
    return ZmqForwardingRuntime(
        adapter,
        market_endpoint="inproc://scope-market",
        command_endpoint="inproc://scope-command",
        private_endpoint="inproc://scope-private",
        enable_trading=True,
        allow_remote=True,
        expected_exchange=exchange,
        expected_market_type=market_type,
        expected_account_id=account_id,
    )


def _command(
    command_type: str,
    exchange: str,
    *,
    account_id: str = "paper",
    market_type: str = "SPOT",
) -> OrderCommand:
    return OrderCommand(
        command_type=command_type,
        strategy_id="scope-test",
        account_id=account_id,
        exchange=exchange,
        market_type=market_type,
        symbol="RB2510",
        side="buy",
        size="1",
        order_type="market",
        order_id="missing-order",
    )


def _send(runtime: ZmqForwardingRuntime, command: OrderCommand):
    return asyncio.run(runtime.order_router.handle_command(command))


def _send_router(router: OrderRouter, command: OrderCommand):
    return asyncio.run(router.handle_command(command))


_MUTATIONS = ("place_order", "cancel_order", "cancel_all")
_CROSS_VENUES = (
    "CTP___FUTURE",
    "UNKNOWN___FUTURE",
    "ＭＴ５",
    "MT\u200b5",
    "SIM\x00",
    " SIM",
    "SIM ",
)


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("exchange", _CROSS_VENUES)
@pytest.mark.parametrize("wire_roundtrip", (False, True))
def test_configured_ctp_server_rejects_direct_and_wire_mutations_before_adapter(
    command_type: str, exchange: str, wire_roundtrip: bool
) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    runtime = _runtime(adapter, exchange="CTP", market_type="FUTURE")
    command = _command(command_type, exchange)
    if wire_roundtrip:
        command = deserialize_message(serialize_message(command))

    ack = _send(runtime, command)

    assert ack.accepted is False
    assert "forwarding trading is disabled" in ack.reason
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("wire_roundtrip", (False, True))
def test_ctp_server_rejects_even_exact_ctp_commands(
    command_type: str, wire_roundtrip: bool
) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    runtime = _runtime(adapter, exchange="CTP", market_type="FUTURE")
    command = _command(command_type, "CTP", market_type="FUTURE")
    if wire_roundtrip:
        command = deserialize_message(serialize_message(command))

    ack = _send(runtime, command)

    assert ack.accepted is False
    assert "forwarding trading is disabled" in ack.reason
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("expected_exchange", (" CTP", "CTP ", "CTP___FUTURE"))
@pytest.mark.parametrize("command_type", _MUTATIONS)
def test_ctp_alias_in_server_configuration_cannot_enable_writes(
    expected_exchange: str, command_type: str
) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    runtime = _runtime(adapter, exchange=expected_exchange, market_type="FUTURE")

    ack = _send(runtime, _command(command_type, "CTP"))

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
def test_ctp_adapter_identity_cannot_be_relabelled_as_non_ctp(
    command_type: str,
) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    runtime = _runtime(adapter, exchange="SIM", market_type="FUTURE")

    ack = _send(runtime, _command(command_type, "SIM"))

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("write_enabled", (False, True))
def test_direct_router_fails_closed_without_scope(command_type: str, write_enabled: bool) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    router = OrderRouter(adapter, write_enabled=write_enabled)

    ack = _send_router(router, _command(command_type, "CTP", market_type="FUTURE"))

    assert ack.accepted is False
    assert router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
def test_direct_router_revalidates_scope_against_ctp_adapter(command_type: str) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    router = OrderRouter(
        adapter,
        write_enabled=True,
        write_scope=("SIM", "SPOT", "paper"),
    )

    ack = _send_router(router, _command(command_type, "SIM"))

    assert ack.accepted is False
    assert router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


def test_direct_router_keeps_explicitly_bound_sim_write_path() -> None:
    adapter = CountingBoundAdapter(exchange_name="SIM___SPOT", account_id="paper")
    router = OrderRouter(
        adapter,
        write_enabled=True,
        write_scope=("SIM", "SPOT", "paper"),
    )

    ack = _send_router(router, _command("place_order", "SIM"))

    assert ack.accepted is True
    assert router.write_enabled is True
    assert adapter.write_calls["place_order"] == 1


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("write_enabled", (None, True))
def test_forwarding_runtime_defaults_to_fail_closed_without_scope(
    command_type: str, write_enabled: bool | None
) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    runtime = (
        ForwardingRuntime(adapter)
        if write_enabled is None
        else ForwardingRuntime(adapter, write_enabled=write_enabled)
    )

    ack = asyncio.run(runtime.order_router.handle_command(_command(command_type, "CTP")))

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
def test_forwarding_runtime_rejects_sim_scope_on_ctp_adapter(command_type: str) -> None:
    adapter = CountingBoundAdapter(exchange_name="CTP___FUTURE", account_id="paper")
    runtime = ForwardingRuntime(
        adapter,
        write_enabled=True,
        write_scope=("SIM", "SPOT", "paper"),
    )

    ack = asyncio.run(runtime.order_router.handle_command(_command(command_type, "SIM")))

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


def test_forwarding_runtime_keeps_explicitly_bound_sim_write_path() -> None:
    adapter = CountingBoundAdapter(exchange_name="SIM___SPOT", account_id="paper")
    runtime = ForwardingRuntime(
        adapter,
        write_enabled=True,
        write_scope=("SIM", "SPOT", "paper"),
    )

    ack = asyncio.run(runtime.order_router.handle_command(_command("place_order", "SIM")))

    assert ack.accepted is True
    assert runtime.order_router.write_enabled is True
    assert adapter.write_calls["place_order"] == 1


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("wire_roundtrip", (False, True))
def test_sim_server_rejects_cross_venue_or_account_before_adapter(
    command_type: str, wire_roundtrip: bool
) -> None:
    adapter = CountingBoundAdapter(exchange_name="SIM___SPOT", account_id="paper")
    runtime = _runtime(adapter, exchange="SIM")

    for exchange, account_id in (("CTP___FUTURE", "paper"), ("SIM", "other")):
        command = _command(command_type, exchange, account_id=account_id)
        if wire_roundtrip:
            command = deserialize_message(serialize_message(command))

        ack = _send(runtime, command)

        assert ack.accepted is False
        assert "scope" in ack.reason
    assert runtime.order_router.write_enabled is True
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("wire_roundtrip", (False, True))
def test_unbound_adapter_cannot_accept_forwarded_mutations(
    command_type: str, wire_roundtrip: bool
) -> None:
    adapter = CountingBoundAdapter(exchange_name="SIM___SPOT")
    del adapter.exchange_name
    runtime = _runtime(adapter, exchange="SIM")
    command = _command(command_type, "SIM")
    if wire_roundtrip:
        command = deserialize_message(serialize_message(command))

    ack = _send(runtime, command)

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
@pytest.mark.parametrize("wire_roundtrip", (False, True))
def test_command_venue_cannot_supply_missing_server_scope(
    command_type: str, wire_roundtrip: bool
) -> None:
    adapter = CountingBoundAdapter(exchange_name="SIM___SPOT")
    runtime = _runtime(adapter, exchange=None)
    command = _command(command_type, "SIM")
    if wire_roundtrip:
        command = deserialize_message(serialize_message(command))

    ack = _send(runtime, command)

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize("command_type", _MUTATIONS)
def test_adapter_account_cannot_be_relabelled_by_server_configuration(
    command_type: str,
) -> None:
    adapter = CountingBoundAdapter(exchange_name="SIM___SPOT", account_id="paper")
    runtime = _runtime(adapter, exchange="SIM", account_id="other")

    ack = _send(runtime, _command(command_type, "SIM", account_id="other"))

    assert ack.accepted is False
    assert runtime.order_router.write_enabled is False
    assert adapter.write_calls == {"place_order": 0, "cancel_order": 0, "list_orders": 0}


@pytest.mark.parametrize(("exchange", "market_type"), (("SIM", "SPOT"), ("MT5", "FX")))
@pytest.mark.parametrize("command_type", _MUTATIONS)
def test_exactly_bound_non_ctp_server_keeps_mutation_path(
    exchange: str, market_type: str, command_type: str
) -> None:
    adapter = CountingBoundAdapter(exchange_name=f"{exchange}___{market_type}")
    runtime = _runtime(adapter, exchange=exchange, market_type=market_type)
    command = _command(command_type, exchange, market_type=market_type)

    ack = _send(runtime, command)

    assert runtime.order_router.write_enabled is True
    assert "scope" not in ack.reason
    if command_type == "place_order":
        assert ack.accepted is True
        assert adapter.write_calls["place_order"] == 1
    elif command_type == "cancel_order":
        assert adapter.write_calls["cancel_order"] == 1
    else:
        assert adapter.write_calls["list_orders"] == 1
