"""DIM04 owner-path self-trade isolation for two strategies sharing an account.

The scenario preloads one real, strategy-bound REDUCE reservation into the
accepted SQLite risk owner, then exercises authenticated strategy runtimes
through the accepted ServiceHost and order-entry component. The provider is
the existing synthetic SQLite fixture; this is local owner evidence, not a
real-account or exchange-provider acceptance. The owner process exposes only
the scenario's explicit empty provider-plugin catalog and uses the injected
synthetic feeds as its external-provider leaf.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import sqlite3
import sys
import time
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_risk import (
    DurableRiskGate,
    ExposureAccountingMode,
    IntentAction,
    RiskIntent,
    RiskPolicy,
)
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)


_POLICY_ID = "dim01-risk-policy-v1"
_OWNER_JOURNAL_ID = "dim01-risk-journal-owner"
_CLOSE_VERSION_A = "dim04-strategy-a-btc-v1"
_CLOSE_VERSION_B = "dim04-strategy-b-btc-v1"
_CLOSE_INTENT_ID = "dim04-preexisting-strategy-a-close"
_CLOSE_QUANTITY = Decimal("0.75")


def _risk_policy(journal: RiskOrderJournal) -> RiskPolicy:
    """Match the accepted DIM01 service owner's durable admission policy."""

    return RiskPolicy(
        _POLICY_ID,
        Decimal("100000"),
        max_increase_count=100,
        require_strategy_allocation=True,
        notional_unit="USD",
        exposure_accounting_mode=ExposureAccountingMode.LIVE_GROSS,
        max_gross_occupancy_notional=Decimal("100000"),
        max_gross_occupancy_intent_count=100,
        max_exposure_facts_age_seconds=3600,
    )


def _allocation_version(strategy_ref: str) -> str:
    account = dim01._ACCOUNT_SPOT_A
    return f"dim01-allocation-{account.account_id}-{account.environment}-{strategy_ref}"


def _snapshot_signature(gate: DurableRiskGate, strategy_ref: str) -> tuple[Any, ...]:
    snapshot = gate.get_closable_position(
        dim01._ACCOUNT_SPOT_A, dim01._BTC, strategy_ref
    )
    return (
        snapshot.revision,
        snapshot.declared_quantity,
        snapshot.used_quantity,
        snapshot.available_quantity,
        snapshot.quantity_unit,
        snapshot.completeness,
        snapshot.source,
    )


def _seed_shared_account_close(risk_path: str, journal_path: str) -> tuple[str, tuple[Any, ...], tuple[Any, ...]]:
    """Create one durable bound close before the service owner starts."""

    account = dim01._ACCOUNT_SPOT_A
    journal = RiskOrderJournal(journal_path, writer_id=_OWNER_JOURNAL_ID)
    gate = DurableRiskGate(
        risk_path,
        _risk_policy(journal),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    try:
        for strategy_ref in (dim01._STRATEGY_A, dim01._STRATEGY_B):
            gate.set_strategy_allocation(
                account,
                strategy_ref,
                _allocation_version(strategy_ref),
                max_notional=Decimal("100000"),
                notional_unit="USD",
                max_gross_occupancy_notional=Decimal("100000"),
                max_gross_occupancy_intent_count=100,
            )

        gate.set_closable_position(
            account,
            dim01._BTC,
            dim01._STRATEGY_A,
            _CLOSE_VERSION_A,
            closable_quantity=Decimal("2"),
            quantity_unit="BTC",
        )
        gate.set_closable_position(
            account,
            dim01._BTC,
            dim01._STRATEGY_B,
            _CLOSE_VERSION_B,
            closable_quantity=Decimal("2"),
            quantity_unit="BTC",
        )
        observed_at_ns = time.time_ns()
        intent = RiskIntent(
            intent_id=_CLOSE_INTENT_ID,
            scope=account,
            action=IntentAction.REDUCE,
            notional=Decimal("0"),
            payload_fingerprint=sha256(
                b"dim04-existing-bound-close|strategy-a|BTC-USD|0.75"
            ).hexdigest(),
            strategy_id=dim01._STRATEGY_A,
            allocation_version=_allocation_version(dim01._STRATEGY_A),
            instrument=dim01._BTC,
            quantity=_CLOSE_QUANTITY,
            quantity_unit="BTC",
            notional_unit="USD",
            exposure_profile_fingerprint=sha256(
                ("profile|" + dim01._BTC).encode("utf-8")
            ).hexdigest(),
            instrument_facts_observed_at_ns=observed_at_ns,
            instrument_facts_valid_until_ns=observed_at_ns + 3_600_000_000_000,
        )
        permit = gate.reserve(intent)
        snapshot_a = _snapshot_signature(gate, dim01._STRATEGY_A)
        snapshot_b = _snapshot_signature(gate, dim01._STRATEGY_B)
        assert snapshot_a == (
            _CLOSE_VERSION_A,
            Decimal("2"),
            _CLOSE_QUANTITY,
            Decimal("1.25"),
            "BTC",
            "LOCAL_LEDGER_COMPLETE",
            "local_closable_position_ledger",
        )
        assert snapshot_b == (
            _CLOSE_VERSION_B,
            Decimal("2"),
            Decimal("0"),
            Decimal("2"),
            "BTC",
            "LOCAL_LEDGER_COMPLETE",
            "local_closable_position_ledger",
        )
        return permit.permit_id, snapshot_a, snapshot_b
    finally:
        gate.close()


def _strict_json_thaw(value: Any) -> Any:
    """Copy an immutable Runtime.call reply into strict, pickleable JSON data."""

    if isinstance(value, Mapping):
        thawed: dict[str, Any] = {}
        for key, nested in value.items():
            if type(key) is not str:
                raise TypeError("runtime reply object keys must be strings")
            thawed[key] = _strict_json_thaw(nested)
        return thawed
    if type(value) in (list, tuple):
        return [_strict_json_thaw(nested) for nested in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(f"runtime reply contains a non-JSON value: {type(value).__name__}")


def _dim04_host_process(control, *host_arguments) -> None:
    """Run DIM01's real owner with this scenario's explicit synthetic provider catalog."""

    import bt_api_base.plugins.loader as plugin_loader

    original_entry_points = plugin_loader.importlib_metadata.entry_points
    original_bt_api_init = dim01.BtApi.__init__
    entry_point_calls = 0
    catalog_callsite_valid = False

    def _empty_synthetic_provider_entry_points(*args, **kwargs):
        nonlocal catalog_callsite_valid, entry_point_calls
        entry_point_calls += 1
        caller = sys._getframe(1)
        valid = (
            not args
            and not kwargs
            and entry_point_calls == 1
            and caller.f_globals.get("__name__") == "bt_api_base.plugins.loader"
            and caller.f_code.co_name == "_discover_entry_points"
            and caller.f_locals.get("group") == "bt_api.plugins"
        )
        if valid:
            catalog_callsite_valid = True
            control.send(("synthetic_provider_fixture", "bt_api.plugins", "empty"))
        return ()

    def _bt_api_init_with_synthetic_provider_catalog(self, *args, **kwargs):
        plugin_loader.importlib_metadata.entry_points = _empty_synthetic_provider_entry_points
        try:
            return original_bt_api_init(self, *args, **kwargs)
        finally:
            plugin_loader.importlib_metadata.entry_points = original_entry_points

    dim01.BtApi.__init__ = _bt_api_init_with_synthetic_provider_catalog
    try:
        dim01._host_process(control, *host_arguments)
        assert entry_point_calls == 1 and catalog_callsite_valid, (
            entry_point_calls,
            catalog_callsite_valid,
        )
    finally:
        plugin_loader.importlib_metadata.entry_points = original_entry_points
        dim01.BtApi.__init__ = original_bt_api_init


def _direct_runtime_client(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
    strategy_ref: str,
    principal_ref: str,
    route_ref: str,
) -> None:
    """Use the public Runtime.call result mapping, with normal SDK registration."""

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
        identity = dim01.WireIdentity(
            dim01.WireRole.STRATEGY_CLIENT,
            principal_ref,
            strategy_ref,
        )
        peer = dim01.WireIdentity(
            dim01.WireRole.SERVICE,
            dim01._SERVICE_PRINCIPAL,
            dim01._SERVICE_ID,
        )
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda selected: scope_map[selected],
            peer_resolver=lambda _service_id: peer,
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
                _, intent_id, instrument, metadata_version, quantity, price = command
                result = runtime.call(
                    "broker.orders",
                    "orders.submit",
                    {
                        "intent_id": intent_id,
                        "signal_id": "signal-" + intent_id,
                        "instrument": instrument,
                        "side": "BUY",
                        "position_effect": "OPEN",
                        "order_type": "LIMIT",
                        "quantity": quantity,
                        "price": price,
                        "time_in_force": "GTC",
                        "reduce_only": False,
                        "metadata_version": metadata_version,
                        "deadline_ns": None,
                        "tags": {},
                    },
                    scope_ref=scope_ref,
                )
                control.send(("runtime_result", _strict_json_thaw(result)))
                continue
            if command[0] == "query":
                _, intent_id = command
                result = runtime.call(
                    "broker.order_query",
                    "orders.query",
                    {"intent_id": intent_id},
                    scope_ref=scope_ref,
                )
                control.send(("runtime_result", _strict_json_thaw(result)))
                continue
            raise AssertionError("unknown DIM04 strategy command")
    except BaseException as exc:
        if runtime is not None:
            try:
                runtime.close()
            except BaseException:
                pass
        try:
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        except BaseException:
            pass
        raise
    finally:
        control.close()


def _runtime_call(record: dict[str, Any], *command: Any) -> dict[str, Any]:
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before its request"
    record["pipe"].send(command)
    response = _receive(record["pipe"])
    assert response[0] != "error", response
    assert response[0] == "runtime_result", response
    result = response[1]
    assert type(result) is dict, result
    # Runtime.call returns the handler's mapping directly; there is no
    # transport-result envelope to unwrap a second time.
    assert frozenset(result) == {"ok", "code", "result"}, result
    assert type(result["result"]) is dict, result
    return result


def _read_rows(database_path: str, query: str, parameters: tuple[Any, ...] = ()) -> tuple[dict[str, Any], ...]:
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return tuple(dict(row) for row in connection.execute(query, parameters).fetchall())


def _denial_reason(journal_path: str, principal_ref: str, intent_id: str) -> str:
    rows = _read_rows(
        journal_path,
        "SELECT event_type,payload_json FROM risk_order_events "
        "WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
        (dim01._ACCOUNT_SPOT_A.key, principal_ref, intent_id),
    )
    assert rows, (principal_ref, intent_id)
    assert rows[-1]["event_type"] == "ADMISSION_DENIED", rows
    return str(json.loads(rows[-1]["payload_json"]).get("reason_code"))


def _assert_denied_without_dispatch(reply: dict[str, Any], intent_id: str) -> None:
    assert reply["ok"] is False, reply
    assert reply["code"] == "ADMISSION_DENIED", reply
    payload = reply["result"]
    assert payload["found"] is True, payload
    record = payload["record"]
    assert record["intent_id"] == intent_id, record
    assert record["state"] == "REJECTED", record
    assert record["dispatch_attempts"] == 0, record
    assert record["provider_order_id"] is None, record


def _assert_rejected_query_without_dispatch(reply: dict[str, Any], intent_id: str) -> None:
    """Check the durable rejection projection returned by the real query route."""

    assert reply["ok"] is False, reply
    assert reply["code"] == "ADMISSION_DENIED", reply
    payload = reply["result"]
    assert type(payload) is dict, payload
    assert frozenset(payload) == {"found", "record"}, payload
    assert payload["found"] is True, payload
    record = payload["record"]
    assert type(record) is dict, record
    assert frozenset(record) == {
        "intent_id",
        "scope_key",
        "payload_sha256",
        "state",
        "provider_order_id",
        "filled_quantity",
        "average_price",
        "cumulative_commission",
        "permit_reference",
        "dispatch_attempts",
        "unknown_reason",
        "review_required",
        "created_at_ns",
        "updated_at_ns",
        "is_terminal",
    }, record
    assert record["intent_id"] == intent_id, record
    assert record["scope_key"] == dim01._ACCOUNT_SPOT_A.key, record
    payload_sha256 = record["payload_sha256"]
    assert (
        type(payload_sha256) is str
        and len(payload_sha256) == 64
        and all(character in "0123456789abcdef" for character in payload_sha256)
    ), record
    assert record["state"] == "REJECTED", record
    assert record["provider_order_id"] is None, record
    assert record["filled_quantity"] == "0", record
    assert record["average_price"] is None, record
    assert record["cumulative_commission"] is None, record
    assert record["permit_reference"] is None, record
    assert type(record["dispatch_attempts"]) is int and record["dispatch_attempts"] == 0, record
    assert record["unknown_reason"] is None, record
    assert record["review_required"] is False, record
    assert type(record["created_at_ns"]) is int and record["created_at_ns"] > 0, record
    assert type(record["updated_at_ns"]) is int and record["updated_at_ns"] > 0, record
    assert record["is_terminal"] is True, record


def _provider_evidence(provider_path: str) -> tuple[tuple[dict[str, Any], ...], dict[str, int]]:
    orders = _read_rows(
        provider_path,
        "SELECT id_namespace,client_order_id,provider_order_id,instrument "
        "FROM provider_orders ORDER BY id_namespace,client_order_id",
    )
    counters = _read_rows(
        provider_path,
        "SELECT name,count FROM provider_counters WHERE id_namespace=? ORDER BY name",
        (dim01._TRADE_DOMAIN_SPOT_A,),
    )
    return orders, {str(row["name"]): int(row["count"]) for row in counters}


def test_dim04_shared_account_owner_rejects_selftrade_without_spending_close_credit(tmp_path) -> None:
    """Two authenticated strategies share one real SQLite owner and account."""

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
        "registry": str(tmp_path / "dim04-registry.sqlite3"),
        "risk": str(tmp_path / "dim04-risk.sqlite3"),
        "journal": str(tmp_path / "dim04-journal.sqlite3"),
        "provider": str(tmp_path / "dim04-provider.sqlite3"),
    }

    close_permit_id, before_a, before_b = _seed_shared_account_close(
        paths["risk"], paths["journal"]
    )
    host = None
    clients: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    try:
        host = _start_process(
            context,
            _dim04_host_process,
            (
                dim01._service_config(endpoint),
                paths["registry"],
                key_material,
                paths["risk"],
                paths["journal"],
                paths["provider"],
            ),
            "dim04-shared-risk-owner-host",
        )
        provider_fixture = _receive(host["pipe"])
        assert provider_fixture == (
            "synthetic_provider_fixture",
            "bt_api.plugins",
            "empty",
        ), provider_fixture
        host_ready = _receive(host["pipe"])
        if host_ready[0] == "error":
            process = host["process"]
            process.join(15)
            assert not process.is_alive(), "failed DIM04 ServiceHost did not exit"
            exit_code = process.exitcode
            host["pipe"].close()
            process.close()
            host["closed"] = True
            pytest.fail(f"DIM04 ServiceHost startup returned {host_ready!r} (exit={exit_code})")
        assert host_ready == ("ready", endpoint, "started"), host_ready

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
                _direct_runtime_client,
                (
                    endpoint,
                    public_key,
                    secret_key,
                    server_public,
                    strategy_ref,
                    principal_ref,
                    route_ref,
                ),
                "dim04-" + strategy_ref + "-runtime",
            )
            clients.append(client)
            client_ready = _receive(client["pipe"])
            assert client_ready[0] == "ready" and client_ready[1], client_ready

        intent_a = "dim04-strategy-a-opposing-open"
        denied_a = _runtime_call(
            clients[0],
            "submit",
            intent_a,
            dim01._BTC,
            dim01._VERSION_BTC_SPOT,
            "0.25",
            "40",
        )
        _assert_denied_without_dispatch(denied_a, intent_a)

        intent_b = "dim04-strategy-b-opposing-open"
        denied_b = _runtime_call(
            clients[1],
            "submit",
            intent_b,
            dim01._BTC,
            dim01._VERSION_BTC_SPOT,
            "0.25",
            "41",
        )
        _assert_denied_without_dispatch(denied_b, intent_b)

        for client, principal_ref, intent_id in (
            (clients[0], dim01._PRINCIPAL_A, intent_a),
            (clients[1], dim01._PRINCIPAL_B, intent_b),
        ):
            query = _runtime_call(client, "query", intent_id)
            _assert_rejected_query_without_dispatch(query, intent_id)
            assert _denial_reason(paths["journal"], principal_ref, intent_id) == "SELF_TRADE_CONFLICT"

        # Scope control: the same strategy on the same shared account may open
        # an unrelated instrument; the local synthetic provider records it.
        unrelated_intent = "dim04-strategy-b-unrelated-eth-open"
        unrelated = _runtime_call(
            clients[1],
            "submit",
            unrelated_intent,
            dim01._ETH,
            dim01._VERSION_ETH_SPOT,
            "1",
            "10",
        )
        assert unrelated["ok"] is True and unrelated["code"] == "OK", unrelated
        unrelated_record = unrelated["result"]["record"]
        assert unrelated["result"]["found"] is True, unrelated
        assert unrelated_record["intent_id"] == unrelated_intent, unrelated_record
        assert unrelated_record["state"] == "FILLED", unrelated_record
        assert unrelated_record["filled_quantity"] == "1", unrelated_record
        assert unrelated_record["dispatch_attempts"] == 1, unrelated_record

        for client in reversed(clients):
            closed = _close_normally(client, expected_tag="closed")
            assert closed == ("closed", "closed"), closed
        provider_write_count = dim01._client_command(host, "provider_write_count")
        assert provider_write_count == ("provider_write_count", 1), provider_write_count
        host_closed = _close_normally(host, expected_tag="closed")
        assert host_closed == ("closed", "closed"), host_closed

        assert _denial_reason(paths["journal"], dim01._PRINCIPAL_A, intent_a) == "SELF_TRADE_CONFLICT"
        assert _denial_reason(paths["journal"], dim01._PRINCIPAL_B, intent_b) == "SELF_TRADE_CONFLICT"
        close_rows = _read_rows(
            paths["risk"],
            "SELECT permit_id,status,scope_key,strategy_id,instrument AS instrument_id,action,quantity,"
            "quantity_unit,close_position_version FROM risk_reservations "
            "WHERE permit_id=?",
            (close_permit_id,),
        )
        assert len(close_rows) == 1, close_rows
        assert close_rows[0] == {
            "permit_id": close_permit_id,
            "status": "active",
            "scope_key": dim01._ACCOUNT_SPOT_A.key,
            "strategy_id": dim01._STRATEGY_A,
            "instrument_id": dim01._BTC,
            "action": "reduce",
            "quantity": "0.75",
            "quantity_unit": "BTC",
            "close_position_version": _CLOSE_VERSION_A,
        }, close_rows

        orders, provider_counters = _provider_evidence(paths["provider"])
        assert len(orders) == 1, orders
        assert orders[0]["instrument"] == dim01._ETH, orders
        # The successful ETH write performs exactly one normal reconciliation
        # read of its own order and fills; the two denied BTC intents add none.
        assert provider_counters == {"fill_queries": 1, "order_queries": 1}, provider_counters

        verification_journal = RiskOrderJournal(
            paths["journal"], writer_id=_OWNER_JOURNAL_ID
        )
        verification_gate = DurableRiskGate(
            paths["risk"],
            _risk_policy(verification_journal),
            execution_journal_authority=verification_journal,
            gross_exposure_authority=verification_journal,
        )
        try:
            assert _snapshot_signature(verification_gate, dim01._STRATEGY_A) == before_a
            assert _snapshot_signature(verification_gate, dim01._STRATEGY_B) == before_b
        finally:
            verification_gate.close()
    finally:
        for client in reversed(clients):
            _bounded_reap_spawned_process(client, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
