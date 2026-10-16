"""A strategy cannot consume another strategy's remaining CLOSE allocation.

This is a local synthetic-owner scenario.  It uses the real authenticated
ServiceHost, Risk admission/journal, and ordinary order query path; initial
per-strategy quantities and the provider's aggregate net position are seeded
by the test, so it is not exchange/account evidence.
"""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
from decimal import Decimal
from typing import Any

import pytest
import zmq

from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.integration import test_dim04_strategy_close_contention as contention
from tests.integration import test_dim04_strategy_net_close_owner_path as net_close
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)

_TOTAL_NET = Decimal("1.5")
_OWNED_QUANTITY = Decimal("0.75")
_OWNED_QUANTITY_TEXT = "0.75"
_A_CLOSE = "dim04-owner-isolation-a-close"
_A_OVERDRAW = "dim04-owner-isolation-a-overdraw"
_B_CLOSE = "dim04-owner-isolation-b-close"


def _closable_signatures(risk_path: str, journal_path: str) -> dict[str, tuple[Any, ...]]:
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
        return {
            strategy_ref: dim04._snapshot_signature(gate, strategy_ref)
            for strategy_ref in (dim01._STRATEGY_A, dim01._STRATEGY_B)
        }
    finally:
        gate.close()


def _assert_filled_close(reply: dict[str, Any], intent_id: str) -> dict[str, Any]:
    assert reply["ok"] is True and reply["code"] == "OK", reply
    assert reply["result"]["found"] is True, reply
    record = reply["result"]["record"]
    assert record["intent_id"] == intent_id, record
    assert record["scope_key"] == dim01._ACCOUNT_SPOT_A.key, record
    assert record["state"] == "FILLED", record
    assert record["filled_quantity"] == _OWNED_QUANTITY_TEXT, record
    assert record["average_price"] == "20", record
    assert record["dispatch_attempts"] == 1, record
    assert type(record["provider_order_id"]) is str and record["provider_order_id"], record
    assert type(record["permit_reference"]) is str and record["permit_reference"], record
    return record


def _assert_provider_write(
    event: tuple[Any, ...],
    record: dict[str, Any],
    provider_path: str,
    before: Decimal,
    after: Decimal,
) -> None:
    assert event[0] == "synthetic_close_written", event
    assert event[2] == record["provider_order_id"], event
    assert Decimal(event[3]) == _OWNED_QUANTITY, event
    assert Decimal(event[4]) == after, event
    rows = net_close._read_rows(
        provider_path,
        "SELECT before_quantity,after_quantity FROM dim04_synthetic_close_calls "
        "WHERE client_order_id=?",
        (event[1],),
    )
    assert len(rows) == 1 and set(rows[0]) == {"before_quantity", "after_quantity"}, rows
    assert Decimal(rows[0]["before_quantity"]) == before, rows
    assert Decimal(rows[0]["after_quantity"]) == after, rows


def test_dim04_owner_close_exhaustion_does_not_borrow_other_strategy_position(tmp_path) -> None:
    """A's second CLOSE is denied while B's synthetic position remains unused."""

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
        "registry": str(tmp_path / "dim04-owner-close-registry.sqlite3"),
        "risk": str(tmp_path / "dim04-owner-close-risk.sqlite3"),
        "journal": str(tmp_path / "dim04-owner-close-journal.sqlite3"),
        "provider": str(tmp_path / "dim04-owner-close-provider.sqlite3"),
    }
    net_close._seed_synthetic_net_position(paths["provider"])
    with sqlite3.connect(paths["provider"]) as connection:
        updated = connection.execute(
            "UPDATE dim04_synthetic_net_positions SET quantity=? WHERE instrument=?",
            (format(_TOTAL_NET, "f"), dim01._BTC),
        )
        assert updated.rowcount == 1
        seeded = connection.execute(
            "SELECT quantity,revision FROM dim04_synthetic_net_positions WHERE instrument=?",
            (dim01._BTC,),
        ).fetchone()
        assert seeded == ("1.5", 1), seeded
    contention._seed_two_strategy_closable_positions(paths["risk"], paths["journal"])

    initial = _closable_signatures(paths["risk"], paths["journal"])
    assert initial == {
        dim01._STRATEGY_A: (
            dim04._CLOSE_VERSION_A,
            _OWNED_QUANTITY,
            Decimal("0"),
            _OWNED_QUANTITY,
            "BTC",
            "LOCAL_LEDGER_COMPLETE",
            "local_closable_position_ledger",
        ),
        dim01._STRATEGY_B: (
            dim04._CLOSE_VERSION_B,
            _OWNED_QUANTITY,
            Decimal("0"),
            _OWNED_QUANTITY,
            "BTC",
            "LOCAL_LEDGER_COMPLETE",
            "local_closable_position_ledger",
        ),
    }, initial

    host = None
    clients: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    client_by_strategy: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    try:
        host = _start_process(
            context,
            contention._two_route_close_host_process,
            (
                dim01._service_config(endpoint),
                paths["registry"],
                key_material,
                paths["risk"],
                paths["journal"],
                paths["provider"],
            ),
            "dim04-owner-close-isolation-host",
        )
        close_contracts = {_receive(host["pipe"]), _receive(host["pipe"])}
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
            assert not process.is_alive(), "DIM04 owner-isolation ServiceHost did not exit"
            exit_code = process.exitcode
            host["pipe"].close()
            process.close()
            host["closed"] = True
            pytest.fail(
                f"DIM04 owner-isolation ServiceHost startup returned {host_ready!r} "
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
                contention._two_strategy_close_runtime_client,
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
                "dim04-owner-close-isolation-" + strategy_ref,
            )
            clients.append(client)
            client_by_strategy[strategy_ref] = client
            ready = _receive(client["pipe"])
            assert ready[0] == "ready" and ready[1], ready

        client_a = client_by_strategy[dim01._STRATEGY_A]
        client_b = client_by_strategy[dim01._STRATEGY_B]

        # A consumes exactly its own declared allocation while B's allocation
        # remains available in the same real Risk database and shared account.
        contention._send_close(client_a, _A_CLOSE, _OWNED_QUANTITY_TEXT, synchronize=False)
        record_a = _assert_filled_close(
            contention._receive_runtime_result(client_a), _A_CLOSE
        )
        records[_A_CLOSE] = record_a
        provider_a = _receive(host["pipe"])
        _assert_provider_write(
            provider_a, record_a, paths["provider"], _TOTAL_NET, Decimal("0.75")
        )

        after_a = _closable_signatures(paths["risk"], paths["journal"])
        assert after_a == {
            dim01._STRATEGY_A: (
                dim04._CLOSE_VERSION_A,
                _OWNED_QUANTITY,
                _OWNED_QUANTITY,
                Decimal("0"),
                "BTC",
                "LOCAL_LEDGER_COMPLETE",
                "local_closable_position_ledger",
            ),
            dim01._STRATEGY_B: (
                dim04._CLOSE_VERSION_B,
                _OWNED_QUANTITY,
                Decimal("0"),
                _OWNED_QUANTITY,
                "BTC",
                "LOCAL_LEDGER_COMPLETE",
                "local_closable_position_ledger",
            ),
        }, after_a

        # The account still has B's 0.75 BTC in the synthetic provider, but
        # A's independently authenticated request cannot consume that credit.
        denied_a = net_close._runtime_result(
            client_a, "submit", _A_OVERDRAW, "0.01", False
        )
        dim04._assert_denied_without_dispatch(denied_a, _A_OVERDRAW)
        assert dim04._denial_reason(
            paths["journal"], dim01._PRINCIPAL_A, _A_OVERDRAW
        ) == "CLOSE_QUANTITY_EXHAUSTED"
        rejected_query = net_close._runtime_result(client_a, "query", _A_OVERDRAW)
        dim04._assert_rejected_query_without_dispatch(rejected_query, _A_OVERDRAW)
        assert rejected_query["result"]["record"]["permit_reference"] is None
        assert _closable_signatures(paths["risk"], paths["journal"]) == after_a
        provider_calls_after_denial = net_close._read_rows(
            paths["provider"],
            "SELECT client_order_id FROM dim04_synthetic_close_calls ORDER BY sequence",
        )
        assert len(provider_calls_after_denial) == 1, provider_calls_after_denial

        # B still closes its complete allocation through its own principal,
        # strategy route, reservation and terminal close proof.
        contention._send_close(client_b, _B_CLOSE, _OWNED_QUANTITY_TEXT, synchronize=False)
        record_b = _assert_filled_close(
            contention._receive_runtime_result(client_b), _B_CLOSE
        )
        records[_B_CLOSE] = record_b
        provider_b = _receive(host["pipe"])
        _assert_provider_write(
            provider_b, record_b, paths["provider"], Decimal("0.75"), Decimal("0")
        )

        query_a = net_close._runtime_result(client_a, "query", _A_CLOSE)
        query_b = net_close._runtime_result(client_b, "query", _B_CLOSE)
        assert query_a["ok"] is True and query_a["result"]["record"] == record_a
        assert query_b["ok"] is True and query_b["result"]["record"] == record_b

        assert _closable_signatures(paths["risk"], paths["journal"]) == {
            dim01._STRATEGY_A: (
                dim04._CLOSE_VERSION_A,
                _OWNED_QUANTITY,
                _OWNED_QUANTITY,
                Decimal("0"),
                "BTC",
                "LOCAL_LEDGER_COMPLETE",
                "local_closable_position_ledger",
            ),
            dim01._STRATEGY_B: (
                dim04._CLOSE_VERSION_B,
                _OWNED_QUANTITY,
                _OWNED_QUANTITY,
                Decimal("0"),
                "BTC",
                "LOCAL_LEDGER_COMPLETE",
                "local_closable_position_ledger",
            ),
        }

        reservations = net_close._read_rows(
            paths["risk"],
            "SELECT permit_id,status,scope_key,strategy_id,instrument AS instrument_id,"
            "action,quantity,quantity_unit,close_position_version FROM risk_reservations "
            "ORDER BY strategy_id",
        )
        assert len(reservations) == 2, reservations
        reservation_by_strategy = {row["strategy_id"]: row for row in reservations}
        assert set(reservation_by_strategy) == {dim01._STRATEGY_A, dim01._STRATEGY_B}
        for strategy_ref, intent_id, close_version in (
            (dim01._STRATEGY_A, _A_CLOSE, dim04._CLOSE_VERSION_A),
            (dim01._STRATEGY_B, _B_CLOSE, dim04._CLOSE_VERSION_B),
        ):
            row = reservation_by_strategy[strategy_ref]
            assert row == {
                "permit_id": records[intent_id]["permit_reference"],
                "status": "settled",
                "scope_key": dim01._ACCOUNT_SPOT_A.key,
                "strategy_id": strategy_ref,
                "instrument_id": dim01._BTC,
                "action": "reduce",
                "quantity": _OWNED_QUANTITY_TEXT,
                "quantity_unit": "BTC",
                "close_position_version": close_version,
            }, row

        commands = net_close._read_rows(
            paths["journal"],
            "SELECT intent_id,principal_ref,client_order_id,canonical_payload_json "
            "FROM risk_order_commands WHERE scope_key=? AND intent_id IN (?,?) "
            "ORDER BY intent_id",
            (dim01._ACCOUNT_SPOT_A.key, _A_CLOSE, _B_CLOSE),
        )
        assert len(commands) == 2, commands
        for command in commands:
            expected_principal = (
                dim01._PRINCIPAL_A if command["intent_id"] == _A_CLOSE else dim01._PRINCIPAL_B
            )
            assert command["principal_ref"] == expected_principal, command
            payload = json.loads(command["canonical_payload_json"])
            assert payload["position_effect"] == "CLOSE", payload
            assert payload["reduce_only"] is True, payload
            assert payload["quantity"] == _OWNED_QUANTITY_TEXT, payload
            assert payload.get("position_side", "NET") == "NET", payload
            assert payload.get("position_bucket", "ALL") == "ALL", payload

        provider_rows = net_close._read_rows(
            paths["provider"],
            "SELECT client_order_id,instrument,side,offset,order_type,quantity,"
            "before_quantity,after_quantity,provider_order_id "
            "FROM dim04_synthetic_close_calls ORDER BY sequence",
        )
        assert len(provider_rows) == 2, provider_rows
        assert Decimal(provider_rows[0]["before_quantity"]) == _TOTAL_NET
        assert Decimal(provider_rows[0]["after_quantity"]) == _OWNED_QUANTITY
        assert Decimal(provider_rows[1]["before_quantity"]) == _OWNED_QUANTITY
        assert Decimal(provider_rows[1]["after_quantity"]) == Decimal("0")
        assert {row["client_order_id"] for row in provider_rows} == {
            command["client_order_id"] for command in commands
        }
        assert all(
            row["instrument"] == dim01._BTC
            and row["side"] == "SELL"
            and row["offset"] == "close"
            and row["quantity"] == _OWNED_QUANTITY_TEXT
            for row in provider_rows
        ), provider_rows
        position = net_close._read_rows(
            paths["provider"],
            "SELECT quantity,revision FROM dim04_synthetic_net_positions WHERE instrument=?",
            (dim01._BTC,),
        )
        assert len(position) == 1, position
        assert Decimal(position[0]["quantity"]) == Decimal("0"), position
        assert position[0]["revision"] == 3, position
        assert dim04._provider_evidence(paths["provider"])[1] == {
            "fill_queries": 2,
            "order_queries": 2,
        }

        host["pipe"].send(("provider_write_count",))
        assert _receive(host["pipe"]) == ("provider_write_count", 2)
        for client in reversed(clients):
            assert _close_normally(client, expected_tag="closed") == ("closed", "closed")
        assert _close_normally(host, expected_tag="closed") == ("closed", "closed")
    finally:
        for client in reversed(clients):
            _bounded_reap_spawned_process(client, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
