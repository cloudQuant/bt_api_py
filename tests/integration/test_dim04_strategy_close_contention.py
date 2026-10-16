"""DIM04 concurrent strategy closes through the real local owner path.

Both strategies own separate synthetic 0.75 BTC close allocations against a
single 1.5 BTC synthetic net position.  The authenticated DIM01 ServiceHost,
Risk gate, journal, provider contract, and order/fill path remain real; only
the provider leaf is the existing SQLite synthetic fixture.  This is local
owner evidence, not exchange or real-account acceptance.
"""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.integration import test_dim04_strategy_net_close_owner_path as net_close
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)

_STARTING_NET_POSITION = Decimal("1.5")
_STRATEGY_CLOSE_QUANTITY = Decimal("0.75")
_STRATEGY_CLOSE_QUANTITY_TEXT = "0.75"
_CLOSE_PRICE = "20"
_OVERDRAW_INTENT = "dim04-concurrent-close-strategy-a-overdraw"


def _seed_two_strategy_closable_positions(risk_path: str, journal_path: str) -> None:
    """Declare two exact strategy shares without preloading reservations."""

    journal = dim04.RiskOrderJournal(
        journal_path, writer_id=dim04._OWNER_JOURNAL_ID
    )
    gate = dim04.DurableRiskGate(
        risk_path,
        dim04._risk_policy(journal),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    try:
        for strategy_ref, close_version in (
            (dim01._STRATEGY_A, dim04._CLOSE_VERSION_A),
            (dim01._STRATEGY_B, dim04._CLOSE_VERSION_B),
        ):
            gate.set_strategy_allocation(
                dim01._ACCOUNT_SPOT_A,
                strategy_ref,
                dim04._allocation_version(strategy_ref),
                max_notional=Decimal("100000"),
                notional_unit="USD",
                max_gross_occupancy_notional=Decimal("100000"),
                max_gross_occupancy_intent_count=100,
            )
            gate.set_closable_position(
                dim01._ACCOUNT_SPOT_A,
                dim01._BTC,
                strategy_ref,
                close_version,
                closable_quantity=_STRATEGY_CLOSE_QUANTITY,
                quantity_unit="BTC",
            )

        expected = {
            dim01._STRATEGY_A: (
                dim04._CLOSE_VERSION_A,
                _STRATEGY_CLOSE_QUANTITY,
                Decimal("0"),
                _STRATEGY_CLOSE_QUANTITY,
                "BTC",
                "LOCAL_LEDGER_COMPLETE",
                "local_closable_position_ledger",
            ),
            dim01._STRATEGY_B: (
                dim04._CLOSE_VERSION_B,
                _STRATEGY_CLOSE_QUANTITY,
                Decimal("0"),
                _STRATEGY_CLOSE_QUANTITY,
                "BTC",
                "LOCAL_LEDGER_COMPLETE",
                "local_closable_position_ledger",
            ),
        }
        for strategy_ref, signature in expected.items():
            assert dim04._snapshot_signature(gate, strategy_ref) == signature
    finally:
        gate.close()


def _two_route_close_host_process(control, *host_arguments) -> None:
    """Enable CLOSE on strategy A's exact route, then reuse the B close owner."""

    original_contract = dim01._provider_contract
    strategy_a_contract_calls = 0

    def _contract_with_strategy_a_close(exchange_name, profile_id, **kwargs):
        nonlocal strategy_a_contract_calls
        contract = original_contract(exchange_name, profile_id, **kwargs)
        if (
            exchange_name == dim01._EXCHANGE_SPOT_A
            and profile_id == dim01._PROFILE_SPOT
            and kwargs.get("trade_id_scope") == dim01._TRADE_ID_SCOPE_INSTRUMENT
        ):
            strategy_a_contract_calls += 1
            if (
                strategy_a_contract_calls != 1
                or contract.supported_position_effects != ("OPEN",)
            ):
                raise AssertionError((strategy_a_contract_calls, contract))
            contract = replace(contract, supported_position_effects=("OPEN", "CLOSE"))
            control.send(
                (
                    "synthetic_close_contract",
                    exchange_name,
                    profile_id,
                    contract.supported_position_effects,
                )
            )
        return contract

    dim01._provider_contract = _contract_with_strategy_a_close
    try:
        net_close._dim04_net_close_host_process(
            control, *host_arguments, expected_provider_close_calls=2
        )
        if strategy_a_contract_calls != 1:
            raise AssertionError(("strategy-A close contract calls", strategy_a_contract_calls))
    finally:
        dim01._provider_contract = original_contract


def _two_strategy_close_runtime_client(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
    strategy_ref: str,
    principal_ref: str,
    route_ref: str,
    start_barrier,
) -> None:
    """Issue normal CLOSE submit/query calls, optionally from a shared barrier."""

    runtime = None
    try:
        route = dim01._ROUTES[route_ref]
        scope_ref = route["scope_ref"]
        scope_map = {scope_ref: dim01._SCOPES[scope_ref]}
        config = dim01._client_config(
            endpoint,
            dim01._TRANSPORT_CONNECT,
            strategy_ref,
            (route_ref,),
        )
        runtime = ServiceClientRuntime(
            config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
            scope_resolver=lambda selected: scope_map[selected],
            peer_resolver=lambda _service_id: WireIdentity(
                WireRole.SERVICE,
                dim01._SERVICE_PRINCIPAL,
                dim01._SERVICE_ID,
            ),
            transport_factory=ZmqTransportFactory(
                {
                    dim01._TRANSPORT_CONNECT: ClientCurveKeys(
                        server_public,
                        strategy_public,
                        strategy_secret,
                    )
                }
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        control.send(("ready", runtime.run_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed", runtime.state))
                return
            if command[0] == "submit":
                _, intent_id, quantity, synchronize = command
                if synchronize:
                    start_barrier.wait(timeout=15.0)
                result = runtime.call(
                    "broker.orders",
                    "orders.submit",
                    {
                        "intent_id": intent_id,
                        "signal_id": "signal-" + intent_id,
                        "instrument": dim01._BTC,
                        "side": "SELL",
                        "position_effect": "CLOSE",
                        "order_type": "LIMIT",
                        "quantity": quantity,
                        "price": _CLOSE_PRICE,
                        "time_in_force": "GTC",
                        "reduce_only": True,
                        "metadata_version": dim01._VERSION_BTC_SPOT,
                        "deadline_ns": None,
                        "tags": {},
                    },
                    scope_ref=scope_ref,
                )
                control.send(("runtime_result", dim04._strict_json_thaw(result)))
                continue
            if command[0] == "query":
                _, intent_id = command
                result = runtime.call(
                    "broker.order_query",
                    "orders.query",
                    {"intent_id": intent_id},
                    scope_ref=scope_ref,
                )
                control.send(("runtime_result", dim04._strict_json_thaw(result)))
                continue
            raise AssertionError(("unknown two-strategy close command", command))
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(BaseException):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _send_close(record: dict[str, Any], intent_id: str, quantity: str, *, synchronize: bool) -> None:
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before its request"
    record["pipe"].send(("submit", intent_id, quantity, synchronize))


def _receive_runtime_result(record: dict[str, Any]) -> dict[str, Any]:
    response = _receive(record["pipe"])
    assert response[0] != "error", response
    assert response[0] == "runtime_result", response
    result = response[1]
    assert type(result) is dict and frozenset(result) == {"ok", "code", "result"}, result
    assert type(result["result"]) is dict, result
    return result


def test_dim04_two_strategies_close_one_contract_concurrently_without_cross_credit(tmp_path) -> None:
    """Two strategies close only their own shares through one authenticated owner."""

    context = multiprocessing.get_context("spawn")
    endpoint = dim01._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_a_public, strategy_a_secret = zmq.curve_keypair()
    strategy_b_public, strategy_b_secret = zmq.curve_keypair()
    key_material = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": strategy_a_public,
        "strategy_b_public": strategy_b_public,
    }
    paths = {
        "registry": str(tmp_path / "dim04-contention-registry.sqlite3"),
        "risk": str(tmp_path / "dim04-contention-risk.sqlite3"),
        "journal": str(tmp_path / "dim04-contention-journal.sqlite3"),
        "provider": str(tmp_path / "dim04-contention-provider.sqlite3"),
    }
    net_close._seed_synthetic_net_position(paths["provider"])
    with sqlite3.connect(paths["provider"]) as connection:
        updated = connection.execute(
            "UPDATE dim04_synthetic_net_positions SET quantity=? WHERE instrument=?",
            (format(_STARTING_NET_POSITION, "f"), dim01._BTC),
        )
        assert updated.rowcount == 1
        position = connection.execute(
            "SELECT quantity,revision FROM dim04_synthetic_net_positions WHERE instrument=?",
            (dim01._BTC,),
        ).fetchone()
        assert position == ("1.5", 1), position
    _seed_two_strategy_closable_positions(paths["risk"], paths["journal"])

    host = None
    clients: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    intent_by_strategy = {
        dim01._STRATEGY_A: "dim04-concurrent-close-strategy-a",
        dim01._STRATEGY_B: "dim04-concurrent-close-strategy-b",
    }
    try:
        host = _start_process(
            context,
            _two_route_close_host_process,
            (
                dim01._service_config(endpoint),
                paths["registry"],
                key_material,
                paths["risk"],
                paths["journal"],
                paths["provider"],
            ),
            "dim04-two-strategy-close-owner-host",
        )
        close_contracts = {
            _receive(host["pipe"]),
            _receive(host["pipe"]),
        }
        assert close_contracts == {
            (
                "synthetic_close_contract",
                dim01._EXCHANGE_SPOT_A,
                dim01._PROFILE_SPOT,
                ("OPEN", "CLOSE"),
            ),
            (
                "synthetic_close_contract",
                dim01._EXCHANGE_SPOT_A_ALIAS,
                dim01._PROFILE_SPOT_ROTATED,
                ("OPEN", "CLOSE"),
            ),
        }, close_contracts
        assert _receive(host["pipe"]) == (
            "synthetic_provider_fixture",
            "bt_api.plugins",
            "empty",
        )
        host_ready = _receive(host["pipe"])
        if host_ready[0] == "error":
            process = host["process"]
            process.join(15)
            assert not process.is_alive(), "failed contention ServiceHost did not exit"
            exit_code = process.exitcode
            host["pipe"].close()
            process.close()
            host["closed"] = True
            pytest.fail(
                f"DIM04 contention ServiceHost startup returned {host_ready!r} "
                f"(exit={exit_code})"
            )
        assert host_ready == ("ready", endpoint, "started"), host_ready

        barrier = context.Barrier(2)
        for strategy_ref, principal_ref, public_key, secret_key, route_ref in (
            (
                dim01._STRATEGY_A,
                dim01._PRINCIPAL_A,
                strategy_a_public,
                strategy_a_secret,
                dim01._ROUTE_SPOT_A,
            ),
            (
                dim01._STRATEGY_B,
                dim01._PRINCIPAL_B,
                strategy_b_public,
                strategy_b_secret,
                dim01._ROUTE_SPOT_A_STRATEGY_B,
            ),
        ):
            client = _start_process(
                context,
                _two_strategy_close_runtime_client,
                (
                    endpoint,
                    public_key,
                    secret_key,
                    server_public,
                    strategy_ref,
                    principal_ref,
                    route_ref,
                    barrier,
                ),
                "dim04-concurrent-close-" + strategy_ref,
            )
            clients.append(client)
            ready = _receive(client["pipe"])
            assert ready[0] == "ready" and ready[1], ready

        # Neither RPC starts until both independently authenticated clients
        # have accepted their command.  The owner serializes the resulting
        # requests through its normal single-process ServiceHost loop.
        for client, strategy_ref in zip(clients, intent_by_strategy, strict=True):
            _send_close(
                client,
                intent_by_strategy[strategy_ref],
                _STRATEGY_CLOSE_QUANTITY_TEXT,
                synchronize=True,
            )
        replies = {
            strategy_ref: _receive_runtime_result(client)
            for client, strategy_ref in zip(clients, intent_by_strategy, strict=True)
        }
        records: dict[str, dict[str, Any]] = {}
        for strategy_ref, reply in replies.items():
            intent_id = intent_by_strategy[strategy_ref]
            assert reply["ok"] is True and reply["code"] == "OK", reply
            assert reply["result"]["found"] is True, reply
            record = reply["result"]["record"]
            assert record["intent_id"] == intent_id, record
            assert record["scope_key"] == dim01._ACCOUNT_SPOT_A.key, record
            assert record["state"] == "FILLED", record
            assert record["filled_quantity"] == _STRATEGY_CLOSE_QUANTITY_TEXT, record
            assert record["average_price"] == _CLOSE_PRICE, record
            assert record["dispatch_attempts"] == 1, record
            assert type(record["provider_order_id"]) is str and record["provider_order_id"], record
            assert type(record["permit_reference"]) is str and record["permit_reference"], record
            records[strategy_ref] = record

        command_rows = net_close._read_rows(
            paths["journal"],
            "SELECT principal_ref,intent_id,risk_intent_id,client_order_id,"
            "canonical_payload_json FROM risk_order_commands "
            "WHERE scope_key=? AND intent_id IN (?,?) ORDER BY intent_id",
            (
                dim01._ACCOUNT_SPOT_A.key,
                intent_by_strategy[dim01._STRATEGY_A],
                intent_by_strategy[dim01._STRATEGY_B],
            ),
        )
        assert len(command_rows) == 2, command_rows
        commands = {row["intent_id"]: row for row in command_rows}
        assert set(commands) == set(intent_by_strategy.values()), commands
        principal_by_strategy = {
            dim01._STRATEGY_A: dim01._PRINCIPAL_A,
            dim01._STRATEGY_B: dim01._PRINCIPAL_B,
        }
        for strategy_ref, intent_id in intent_by_strategy.items():
            command = commands[intent_id]
            record = records[strategy_ref]
            assert command["principal_ref"] == principal_by_strategy[strategy_ref], command
            assert type(command["risk_intent_id"]) is str and command["risk_intent_id"], command
            assert command["client_order_id"], command
            canonical = json.loads(command["canonical_payload_json"])
            assert canonical["intent_id"] == intent_id, canonical
            assert canonical["side"] == "SELL", canonical
            assert canonical["position_effect"] == "CLOSE", canonical
            assert canonical["reduce_only"] is True, canonical
            assert canonical["quantity"] == _STRATEGY_CLOSE_QUANTITY_TEXT, canonical
            reserved_events = net_close._read_rows(
                paths["journal"],
                "SELECT payload_json FROM risk_order_events "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=? "
                "AND event_type='RESERVED'",
                (
                    dim01._ACCOUNT_SPOT_A.key,
                    principal_by_strategy[strategy_ref],
                    intent_id,
                ),
            )
            assert len(reserved_events) == 1, reserved_events
            reserved = json.loads(reserved_events[0]["payload_json"])
            assert reserved["permit_id"] == record["permit_reference"], reserved

        provider_events = tuple(_receive(host["pipe"]) for _ in range(2))
        assert all(event[0] == "synthetic_close_written" for event in provider_events), provider_events
        events_by_client = {event[1]: event for event in provider_events}
        assert set(events_by_client) == {
            command["client_order_id"] for command in commands.values()
        }, provider_events
        for strategy_ref, intent_id in intent_by_strategy.items():
            event = events_by_client[commands[intent_id]["client_order_id"]]
            assert event[2] == records[strategy_ref]["provider_order_id"], event
            assert Decimal(event[3]) == _STRATEGY_CLOSE_QUANTITY, event
            assert Decimal(event[4]) in {Decimal("0.75"), Decimal("0")}, event

        # The duplicate request and a later query are owner reads/replays of
        # the durable result; neither can write another synthetic provider order.
        for client, strategy_ref in zip(clients, intent_by_strategy, strict=True):
            intent_id = intent_by_strategy[strategy_ref]
            assert net_close._runtime_result(
                client, "submit", intent_id, _STRATEGY_CLOSE_QUANTITY_TEXT, False
            ) == replies[strategy_ref]
            query = net_close._runtime_result(client, "query", intent_id)
            assert query["ok"] is True and query["code"] == "OK", query
            assert query["result"]["found"] is True, query
            assert query["result"]["record"] == records[strategy_ref], query

        overdraw = net_close._runtime_result(
            clients[0], "submit", _OVERDRAW_INTENT, "0.01", False
        )
        dim04._assert_denied_without_dispatch(overdraw, _OVERDRAW_INTENT)
        assert dim04._denial_reason(
            paths["journal"], dim01._PRINCIPAL_A, _OVERDRAW_INTENT
        ) == "CLOSE_QUANTITY_EXHAUSTED"
        rejected_query = net_close._runtime_result(
            clients[0], "query", _OVERDRAW_INTENT
        )
        dim04._assert_rejected_query_without_dispatch(rejected_query, _OVERDRAW_INTENT)
        rejected_record = rejected_query["result"]["record"]
        assert rejected_record["permit_reference"] is None, rejected_record

        host["pipe"].send(("provider_write_count",))
        assert _receive(host["pipe"]) == ("provider_write_count", 2)
        for client in reversed(clients):
            assert _close_normally(client, expected_tag="closed") == ("closed", "closed")
        assert _close_normally(host, expected_tag="closed") == ("closed", "closed")

        provider_calls = net_close._read_rows(
            paths["provider"],
            "SELECT client_order_id,instrument,side,offset,order_type,quantity,"
            "before_quantity,after_quantity,provider_order_id "
            "FROM dim04_synthetic_close_calls ORDER BY sequence",
        )
        assert len(provider_calls) == 2, provider_calls
        assert Decimal(provider_calls[0]["before_quantity"]) == _STARTING_NET_POSITION, provider_calls
        assert Decimal(provider_calls[0]["after_quantity"]) == Decimal("0.75"), provider_calls
        assert Decimal(provider_calls[1]["before_quantity"]) == Decimal("0.75"), provider_calls
        assert Decimal(provider_calls[1]["after_quantity"]) == Decimal("0"), provider_calls
        for row in provider_calls:
            assert row["instrument"] == dim01._BTC, row
            assert row["side"] == "SELL" and row["offset"] == "close", row
            assert row["order_type"] == "sell-limit", row
            assert row["quantity"] == _STRATEGY_CLOSE_QUANTITY_TEXT, row
        assert {row["client_order_id"] for row in provider_calls} == {
            command["client_order_id"] for command in commands.values()
        }, provider_calls
        assert {row["provider_order_id"] for row in provider_calls} == {
            record["provider_order_id"] for record in records.values()
        }, provider_calls
        position_rows = net_close._read_rows(
            paths["provider"],
            "SELECT quantity,revision FROM dim04_synthetic_net_positions WHERE instrument=?",
            (dim01._BTC,),
        )
        assert len(position_rows) == 1 and Decimal(position_rows[0]["quantity"]) == Decimal("0"), position_rows
        assert position_rows[0]["revision"] == 3, position_rows
        order_rows, provider_counters = dim04._provider_evidence(paths["provider"])
        assert len(order_rows) == 2, order_rows
        assert {row["client_order_id"] for row in order_rows} == {
            command["client_order_id"] for command in commands.values()
        }, order_rows
        assert {row["provider_order_id"] for row in order_rows} == {
            record["provider_order_id"] for record in records.values()
        }, order_rows
        assert provider_counters == {"fill_queries": 2, "order_queries": 2}, provider_counters

        reservations = net_close._read_rows(
            paths["risk"],
            "SELECT permit_id,status,scope_key,strategy_id,instrument AS instrument_id,action,"
            "quantity,quantity_unit,close_position_version FROM risk_reservations "
            "ORDER BY strategy_id",
        )
        assert len(reservations) == 2, reservations
        reservation_by_strategy = {row["strategy_id"]: row for row in reservations}
        assert set(reservation_by_strategy) == {dim01._STRATEGY_A, dim01._STRATEGY_B}, reservations
        for strategy_ref, close_version in (
            (dim01._STRATEGY_A, dim04._CLOSE_VERSION_A),
            (dim01._STRATEGY_B, dim04._CLOSE_VERSION_B),
        ):
            row = reservation_by_strategy[strategy_ref]
            assert row["permit_id"] == records[strategy_ref]["permit_reference"], row
            assert row["status"] == "settled", row
            assert row["scope_key"] == dim01._ACCOUNT_SPOT_A.key, row
            assert row["instrument_id"] == dim01._BTC and row["action"] == "reduce", row
            assert row["quantity"] == _STRATEGY_CLOSE_QUANTITY_TEXT, row
            assert row["quantity_unit"] == "BTC", row
            assert row["close_position_version"] == close_version, row

        for strategy_ref, intent_id in intent_by_strategy.items():
            journal_rows = net_close._read_rows(
                paths["journal"],
                "SELECT event_type,payload_json FROM risk_order_events "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
                (dim01._ACCOUNT_SPOT_A.key, principal_by_strategy[strategy_ref], intent_id),
            )
            event_types = [row["event_type"] for row in journal_rows]
            for required in ("ORDER_OBSERVATION", "FILL_OBSERVATION", "ACKED_TRACKED"):
                assert event_types.count(required) == 1, (intent_id, required, event_types)
            assert "PROOF_READY" not in event_types, (intent_id, event_types)
            ack = json.loads(journal_rows[-1]["payload_json"])
            assert journal_rows[-1]["event_type"] == "ACKED_TRACKED", journal_rows[-1]
            assert ack["position_effect"] == "CLOSE", ack
            assert ack["dispatch_attempt_count"] == 1, ack
            assert ack["close_reference_id"] and len(ack["close_reference_sha256"]) == 64, ack
            assert ack["close_proof_sha256"] and len(ack["close_proof_sha256"]) == 64, ack
            terminal_close_proofs = net_close._read_rows(
                paths["journal"],
                "SELECT proof_sha256 FROM risk_order_terminal_close_proofs "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (dim01._ACCOUNT_SPOT_A.key, principal_by_strategy[strategy_ref], intent_id),
            )
            assert terminal_close_proofs == ({"proof_sha256": ack["proof_sha256"]},), terminal_close_proofs

        verification_journal = dim04.RiskOrderJournal(
            paths["journal"], writer_id=dim04._OWNER_JOURNAL_ID
        )
        verification_gate = dim04.DurableRiskGate(
            paths["risk"],
            dim04._risk_policy(verification_journal),
            execution_journal_authority=verification_journal,
            gross_exposure_authority=verification_journal,
        )
        try:
            for strategy_ref, close_version in (
                (dim01._STRATEGY_A, dim04._CLOSE_VERSION_A),
                (dim01._STRATEGY_B, dim04._CLOSE_VERSION_B),
            ):
                assert dim04._snapshot_signature(verification_gate, strategy_ref) == (
                    close_version,
                    _STRATEGY_CLOSE_QUANTITY,
                    _STRATEGY_CLOSE_QUANTITY,
                    Decimal("0"),
                    "BTC",
                    "LOCAL_LEDGER_COMPLETE",
                    "local_closable_position_ledger",
                )
                record = records[strategy_ref]
                receipt = verification_gate.dispatch_close_resolution_receipt(
                    str(record["permit_reference"])
                )
                assert receipt.status.value == "TERMINAL_CLOSE_RESOLVED", receipt
                assert receipt.position_effect == "CLOSE", receipt
                assert receipt.final_state.value == "FILLED", receipt
                assert receipt.requested_quantity == receipt.filled_quantity == _STRATEGY_CLOSE_QUANTITY, receipt
                assert receipt.dispatch_latch_active is False, receipt
        finally:
            verification_gate.close()
    finally:
        for client in reversed(clients):
            _bounded_reap_spawned_process(client, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
