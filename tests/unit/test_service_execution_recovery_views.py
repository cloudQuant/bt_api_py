"""Execution journal recovery views and their scoped client wrappers."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AllocationOwnerKind,
    ExecutionOwnerScope,
    ExecutionScope,
    ExecutionScopeDirectory,
    ExecutionState,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
)

from bt_api_py.service_execution import (
    ServiceExecutionClient,
    ServiceExecutionError,
)
from bt_api_py.service_execution_account_books import (
    ACCOUNT_BOOKS_QUERY_OPERATION,
    ExecutionAccountBookReadAdapter,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_PRINCIPAL = "strategy-principal"
_WIRE_A = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-a")
_WIRE_B = WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-b")
_IDENTITY_A = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, "strategy-a")


def _scope(strategy_id: str) -> ExecutionScope:
    return ExecutionScope("PAPER", "simulation", "account-a", strategy_id, "20261016")


def _directory(*, revision: str = "directory-v1") -> ExecutionScopeDirectory:
    scope_a = _scope("strategy-a")
    scope_b = _scope("strategy-b")
    return ExecutionScopeDirectory(
        account_key=scope_a.account_key,
        source="test-owner-directory",
        source_ref="account-owner-resource",
        revision=revision,
        covers_from_ns=1,
        as_of_ns=2,
        scopes=(
            ExecutionOwnerScope(scope_a, AllocationOwnerKind.STRATEGY, "strategy-a", _WIRE_A),
            ExecutionOwnerScope(scope_b, AllocationOwnerKind.STRATEGY, "strategy-b", _WIRE_B),
        ),
    )


class _ReadSource:
    def __init__(self, directories: tuple[ExecutionScopeDirectory, ...]) -> None:
        self.directories = directories
        self.directory_reads = 0
        self.input_reads = 0

    def read_inputs(self, **_kwargs):
        self.input_reads += 1
        raise AssertionError("recovery views must not query provider/account snapshots")

    def authorize_account_view(self, **_kwargs):
        return False

    def read_scope_directory(self, *, account_key: str) -> ExecutionScopeDirectory:
        self.directory_reads += 1
        assert account_key == _scope("strategy-a").account_key
        return self.directories[min(self.directory_reads - 1, len(self.directories) - 1)]


def _append_partial_fill(store: SqliteExecutionStore, scope: ExecutionScope, intent_id: str) -> None:
    lease = store.acquire_or_renew_lease(scope, "recovery-view-test")
    intent = OrderIntent(
        intent_id=intent_id,
        scope=scope,
        signal_id=f"signal-{intent_id}",
        instrument="BTC-USDT",
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        price=Decimal("10"),
        tags={"caller-note": "not-returned"},
    )
    store.admit_intent(intent, writer_lease=lease)
    store.activate_intent(intent.intent_id, scope, writer_lease=lease)
    claimed, did_claim = store.claim_for_dispatch(intent.intent_id, scope, writer_lease=lease)
    assert did_claim is True and claimed.state is ExecutionState.DISPATCHING
    store.record_observation(
        ProviderObservation(
            intent_id=intent.intent_id,
            state=ExecutionState.PARTIALLY_FILLED,
            provider_order_id=f"provider-{intent_id}",
            filled_quantity=Decimal("1"),
            average_price=Decimal("10"),
            cumulative_commission=Decimal("0.12"),
            cumulative_commission_currency="USDT",
        ),
        scope=scope,
        writer_lease=lease,
    )


def _adapter(store: SqliteExecutionStore, source: _ReadSource) -> ExecutionAccountBookReadAdapter:
    return ExecutionAccountBookReadAdapter(
        store,
        {_WIRE_A: _scope("strategy-a"), _WIRE_B: _scope("strategy-b")},
        source,
    )


@pytest.mark.unit
def test_recovery_snapshot_and_page_are_scoped_versioned_and_provider_independent(tmp_path) -> None:
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    try:
        _append_partial_fill(store, _scope("strategy-a"), "intent-a")
        # A different authorized strategy creates global sequence gaps in this strategy's view.
        lease_b = store.acquire_or_renew_lease(_scope("strategy-b"), "recovery-view-test")
        other = OrderIntent(
            intent_id="intent-b",
            scope=_scope("strategy-b"),
            signal_id="signal-b",
            instrument="ETH-USDT",
            side=Side.BUY,
            position_effect=PositionEffect.OPEN,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
        )
        store.admit_intent(other, writer_lease=lease_b)

        source = _ReadSource((_directory(),))
        adapter = _adapter(store, source)
        snapshot_reply = adapter.handle(
            ACCOUNT_BOOKS_QUERY_OPERATION,
            principal_ref=_PRINCIPAL,
            identity=_IDENTITY_A,
            scope=_WIRE_A,
            arguments={"view": "RECOVERY_SNAPSHOT", "version": 1},
        )
        assert snapshot_reply["ok"] is True
        snapshot = snapshot_reply["result"]
        assert set(snapshot) == {"view", "version", "coverage", "orders"}
        assert snapshot["view"] == "RECOVERY_SNAPSHOT" and snapshot["version"] == 1
        coverage = snapshot["coverage"]
        assert set(coverage) == {
            "account_key",
            "owner_kind",
            "owner_ref",
            "scope_directory_revision",
            "journal_generation",
            "first_sequence",
            "last_sequence",
            "high_water_sequence",
            "complete",
            "source",
            "freshness",
            "scope_semantics",
        }
        assert coverage["owner_kind"] == "STRATEGY"
        assert coverage["owner_ref"] == "strategy-a"
        assert coverage["scope_directory_revision"] == "directory-v1"
        assert coverage["first_sequence"] == 1
        assert coverage["last_sequence"] == coverage["high_water_sequence"] == 5
        assert coverage["source"] == "EXECUTION_SQLITE_JOURNAL"
        assert coverage["freshness"] == "DURABLE_CUT_NOT_PROVIDER_LIVE"
        assert coverage["scope_semantics"] == "DURABLE_STRATEGY_SCOPE_NO_RUN_ISOLATION"
        assert coverage["complete"] is True
        assert len(snapshot["orders"]) == 1
        order = snapshot["orders"][0]
        assert order["intent_id"] == "intent-a"
        assert order["quantity"] == "2"
        assert order["filled_quantity"] == "1"
        assert order["cumulative_commission"] == "0.12"
        assert order["cumulative_commission_currency"] == "USDT"
        assert order["latest_source_sequence"] == 4
        assert order["latest_observation_sequence"] == 4
        assert "caller-note" not in str(order)
        assert set(order) == {
            "scope_key",
            "intent_id",
            "intent_fingerprint",
            "submit_fingerprint",
            "signal_id",
            "instrument",
            "side",
            "position_effect",
            "order_type",
            "quantity",
            "price",
            "time_in_force",
            "reduce_only",
            "metadata_version",
            "deadline_ns",
            "state",
            "provider_order_id",
            "filled_quantity",
            "average_price",
            "cumulative_commission",
            "cumulative_commission_currency",
            "dispatch_attempts",
            "unknown_reason_code",
            "review_required",
            "latest_source_sequence",
            "latest_source_event_id",
            "latest_observation_sequence",
            "latest_observation_event_id",
        }

        request = {
            "view": "EVENTS_AFTER",
            "version": 1,
            "after_sequence": 2,
            "expected_journal_generation": coverage["journal_generation"],
            "expected_directory_revision": coverage["scope_directory_revision"],
            "limit": 2,
        }
        page_reply = adapter.handle(
            ACCOUNT_BOOKS_QUERY_OPERATION,
            principal_ref=_PRINCIPAL,
            identity=_IDENTITY_A,
            scope=_WIRE_A,
            arguments=request,
        )
        assert page_reply["ok"] is True
        page = page_reply["result"]
        assert set(page) == {"view", "version", "coverage", "events"}
        assert page["view"] == "EVENTS_AFTER" and page["version"] == 1
        assert set(page["coverage"]) == set(coverage) | {
            "after_sequence",
            "scanned_through",
            "has_more",
        }
        assert page["coverage"]["after_sequence"] == 2
        assert page["coverage"]["scanned_through"] == 5
        assert page["coverage"]["has_more"] is False
        assert [event["sequence"] for event in page["events"]] == [3, 4]
        assert set(page["events"][0]) == {
            "scope_key",
            "intent_id",
            "intent_fingerprint",
            "submit_fingerprint",
            "event_id",
            "sequence",
            "journal_generation",
            "event_type",
            "state",
            "event_sha256",
            "details",
        }
        assert page["events"][0]["details"] == {"dispatch_attempt": 1}
        assert page["events"][1]["details"]["cumulative_commission_currency"] == "USDT"
        assert source.input_reads == 0
        assert source.directory_reads == 4
    finally:
        store.close()


@pytest.mark.unit
def test_recovery_page_rejects_directory_revision_race_and_unapproved_events(tmp_path) -> None:
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    try:
        _append_partial_fill(store, _scope("strategy-a"), "intent-a")
        changed_source = _ReadSource((_directory(), _directory(revision="directory-v2")))
        reply = _adapter(store, changed_source).handle(
            ACCOUNT_BOOKS_QUERY_OPERATION,
            principal_ref=_PRINCIPAL,
            identity=_IDENTITY_A,
            scope=_WIRE_A,
            arguments={
                "view": "EVENTS_AFTER",
                "version": 1,
                "after_sequence": 0,
                "expected_journal_generation": store.journal_source_identity()["generation"],
                "expected_directory_revision": "directory-v1",
                "limit": 2,
            },
        )
        assert reply == {"ok": False, "code": "UNAVAILABLE", "result": {}}

        with store._lock:
            store._connection.execute(
                "UPDATE execution_outbox SET event_type = 'unknown_event' WHERE sequence = 2"
            )
        stable_source = _ReadSource((_directory(),))
        reply = _adapter(store, stable_source).handle(
            ACCOUNT_BOOKS_QUERY_OPERATION,
            principal_ref=_PRINCIPAL,
            identity=_IDENTITY_A,
            scope=_WIRE_A,
            arguments={"view": "RECOVERY_SNAPSHOT", "version": 1},
        )
        assert reply == {"ok": False, "code": "UNAVAILABLE", "result": {}}
        assert stable_source.input_reads == 0
    finally:
        store.close()


@pytest.mark.unit
def test_recovery_preserves_pre_v22_recorded_intent_event_after_schema_migration(tmp_path) -> None:
    database_path = tmp_path / "execution.sqlite3"
    store = SqliteExecutionStore(database_path)
    scope = _scope("strategy-a")
    _append_partial_fill(store, scope, "intent-1")
    try:
        legacy_event = json.dumps(
            {"payload_sha256": store.get("intent-1", scope=scope).payload_sha256},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        with store._lock:
            store._connection.execute(
                "UPDATE execution_outbox SET payload_json = ? WHERE sequence = 1",
                (legacy_event,),
            )
            store._connection.execute(
                "UPDATE execution_meta SET value = '21' WHERE key = 'schema_version'"
            )
    finally:
        store.close()

    migrated = SqliteExecutionStore(database_path)
    try:
        version = migrated._connection.execute(
            "SELECT value FROM execution_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert version == str(migrated._SCHEMA_VERSION)
        first_event = migrated._connection.execute(
            "SELECT payload_json FROM execution_outbox WHERE sequence = 1"
        ).fetchone()["payload_json"]
        assert first_event == legacy_event

        reply = _adapter(migrated, _ReadSource((_directory(),))).handle(
            ACCOUNT_BOOKS_QUERY_OPERATION,
            principal_ref=_PRINCIPAL,
            identity=_IDENTITY_A,
            scope=_WIRE_A,
            arguments={"view": "RECOVERY_SNAPSHOT", "version": 1},
        )
        assert reply["ok"] is True
        assert reply["result"]["orders"][0]["intent_id"] == "intent-1"
        assert reply["result"]["orders"][0]["state"] == "PARTIALLY_FILLED"
    finally:
        migrated.close()


class _StubRuntime(ServiceClientRuntime):
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def call(self, mount, operation, arguments, *, scope_ref):
        self.calls.append((mount, operation, dict(arguments), scope_ref))
        return self.reply


@pytest.mark.unit
def test_execution_client_recovery_wrappers_use_query_binding_and_unwrap_result() -> None:
    result = {"view": "RECOVERY_SNAPSHOT", "version": 1, "orders": []}
    runtime = _StubRuntime({"ok": True, "code": "OK", "result": result})
    client = ServiceExecutionClient(
        runtime,
        "broker.orders",
        scope_ref="strategy-order-entry",
        query_mount="broker.order_query",
        query_scope_ref="strategy-order-query",
    )
    assert client.query_recovery_snapshot() == result
    generation = "a" * 32
    page = {"view": "EVENTS_AFTER", "version": 1, "events": []}
    runtime.reply = {"ok": True, "code": "OK", "result": page}
    assert client.query_recovery_events_after(
        after_sequence=7,
        expected_journal_generation=generation,
        expected_directory_revision="directory-v1",
        limit=32,
    ) == page
    assert runtime.calls == [
        (
            "broker.order_query",
            "execution.account_books.query",
            {"view": "RECOVERY_SNAPSHOT", "version": 1},
            "strategy-order-query",
        ),
        (
            "broker.order_query",
            "execution.account_books.query",
            {
                "view": "EVENTS_AFTER",
                "version": 1,
                "after_sequence": 7,
                "expected_journal_generation": generation,
                "expected_directory_revision": "directory-v1",
                "limit": 32,
            },
            "strategy-order-query",
        ),
    ]

    runtime.reply = {"ok": False, "code": "UNAVAILABLE", "result": {}}
    with pytest.raises(ServiceExecutionError, match="UNAVAILABLE"):
        client.query_recovery_snapshot()

    with pytest.raises(ServiceExecutionError, match="INVALID_RECOVERY_LIMIT"):
        client.query_recovery_events_after(
            after_sequence=0,
            expected_journal_generation=generation,
            expected_directory_revision="directory-v1",
            limit=129,
        )


@pytest.mark.unit
def test_execution_client_recovery_wrappers_require_query_route_and_owner_thread() -> None:
    runtime = _StubRuntime({"ok": True, "code": "OK", "result": {}})
    no_query = ServiceExecutionClient(runtime, "broker.orders", scope_ref="strategy-order-entry")
    with pytest.raises(ServiceExecutionError, match="QUERY_NOT_CONFIGURED"):
        no_query.query_recovery_snapshot()

    client = ServiceExecutionClient(
        runtime,
        "broker.orders",
        scope_ref="strategy-order-entry",
        query_mount="broker.order_query",
        query_scope_ref="strategy-order-query",
    )
    errors = []

    import threading

    worker = threading.Thread(
        target=lambda: _capture_error(client.query_recovery_snapshot, errors)
    )
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert len(errors) == 1 and errors[0].code == "WRONG_OWNER_THREAD"
    assert runtime.calls == []


def _capture_error(call, errors) -> None:
    try:
        call()
    except ServiceExecutionError as error:
        errors.append(error)
