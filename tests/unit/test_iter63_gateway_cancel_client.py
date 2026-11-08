"""Explicit cancel plans and recovery using a synthetic client runtime."""

import threading
from dataclasses import replace

import pytest
from bt_api_base.gateway_cancel_protocol import GatewayCancelRecord
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_direct_client import (
    GatewayDirectClient,
    gateway_cancel_plan_from_mapping,
    gateway_cancel_plan_to_mapping,
    validate_gateway_cancel_record_progress,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.unit.test_iter63_gateway_direct_client import (
    MemoryHost,
    Owner,
    _request,
    _wire_record,
)


class CancelHost:
    def __init__(self):
        self.plans = {}
        self.records = {}
        self.fail = set()
        self.lock = threading.Lock()
        self.plan_barrier = None
        self.old_save_entered = None
        self.old_save_continue = None

    def save_cancel_plan(self, plan):
        if "plan" in self.fail:
            raise RuntimeError
        if self.plan_barrier is not None:
            self.plan_barrier.wait(timeout=5)
        with self.lock:
            winner = self.plans.setdefault(plan.operation_id, plan)
            return None if "plan_winner" in self.fail else winner

    def load_cancel_plan(self, operation_id):
        if "plan_read" in self.fail:
            return None
        with self.lock:
            return self.plans.get(operation_id)

    def save_cancel_record(self, operation_id, record):
        if "record" in self.fail:
            raise RuntimeError
        if self.old_save_entered is not None and record.state == "REQUESTED":
            self.old_save_entered.set()
            assert self.old_save_continue.wait(5)
        with self.lock:
            previous = self.records.get(operation_id)
            if previous is not None and previous.version > record.version:
                validate_gateway_cancel_record_progress(record, previous)
                return previous
            validate_gateway_cancel_record_progress(previous, record)
            self.records[operation_id] = record
            return None if "record_winner" in self.fail else record

    def load_cancel_record(self, operation_id):
        with self.lock:
            return self.records.get(operation_id)


def _new_runtime(route, host, operations):
    holders = []

    def factory():
        owner = Owner(route, host)
        owner.bindings[0].operations = frozenset(operations)
        holders.append(owner)
        return owner

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=2)
    return runtime, holders[0]


@pytest.fixture
def system():
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
    from bt_api_base.service_protocol import WireIdentity, WireRole

    from bt_api_py.gateway_direct_client import GatewayClientRoute

    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope(
        "paper:demo", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a"
    )
    route = GatewayClientRoute(
        "broker.direct_write", "gateway-service", "gateway-a", "scope-a", scope, identity
    )
    binding = GatewayCommandBinding("journal-a", "account-a", "strategy-a", "principal-a")
    host, cancel_host = MemoryHost(), CancelHost()
    runtime, owner = _new_runtime(
        route,
        host,
        {"direct.reserve", "direct.command_status", "direct.cancel", "direct.cancel_status"},
    )
    client = GatewayDirectClient(
        runtime, binding=binding, route=route, host=host, cancel_host=cancel_host
    )
    try:
        assert client.reserve(_request()).outcome == "OBSERVED"
        intent = host.intents["key-1"]
        native = {
            "exchange_name": "TEST",
            "client_order_id": "ref",
            "fields": {"trading_day": "20300101"},
            "session_generation": 1,
        }
        owner.override = lambda *_: {
            "status": "OK",
            "record": _wire_record(
                intent,
                state="SENT",
                version=3,
                native_identity=native,
                strong_identity_key_digests=["b" * 64],
            ),
        }
        assert client.status("key-1").record.state == "SENT"
        yield client, host, cancel_host, owner, runtime, route, binding
    finally:
        runtime.close(timeout_seconds=2)


def _record(plan, *, state="REQUESTED", error_code=None, **changes):
    fields = {
        "schema_version": 1,
        "binding": plan.binding,
        "operation_id": plan.operation_id,
        "attempt_id": "attempt-a",
        "request_key": plan.request_key,
        "target_reservation_id": plan.reservation_id,
        "target_digest": plan.digest,
        "state": state,
        "version": {"REQUESTED": 1, "DISPATCHING": 2, "SENT": 3, "NOT_SENT": 3, "UNKNOWN": 3}[
            state
        ],
        "owner_generation": "g1",
        "attempt_generation": "g1",
        "generation_current": True,
        "error_code": error_code,
    }
    fields.update(changes)
    return GatewayCancelRecord(**fields)


def _reply(host, *, state="REQUESTED", queue_state="QUEUED"):
    plan = host.plans["cancel-1"]
    return {
        "status": "OK",
        "record": _record(plan, state=state).to_mapping(),
        "cancel_queue_state": queue_state,
    }


def test_plan_is_saved_and_read_back_before_first_cancel(system):
    client, _, cancel_host, owner, _, route, binding = system

    def reply(operation, arguments):
        assert operation == "direct.cancel"
        plan = cancel_host.plans["cancel-1"]
        assert arguments == {
            "request_key": "key-1",
            "reservation_id": "reservation-a",
            "digest": plan.digest,
            "operation_id": "cancel-1",
        }
        return _reply(cancel_host)

    owner.override = reply
    result = client.cancel("key-1", operation_id="cancel-1")
    assert result.outcome == "QUEUED" and result.record.state == "REQUESTED"
    plan = cancel_host.plans["cancel-1"]
    raw = gateway_cancel_plan_to_mapping(plan)
    assert (
        gateway_cancel_plan_from_mapping(raw, expected_binding=binding, expected_route=route)
        == plan
    )
    raw["route"]["scope"]["run_id"] = "another"
    with pytest.raises(ValueError):
        gateway_cancel_plan_from_mapping(raw, expected_binding=binding, expected_route=route)


@pytest.mark.parametrize("failure", ["plan", "plan_read", "plan_winner"])
def test_host_save_or_readback_failure_prevents_send(system, failure):
    client, _, cancel_host, owner, _, _, _ = system
    cancel_host.fail.add(failure)
    before = len(owner.calls)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "TRANSPORT_NOT_STARTED"
    assert len(owner.calls) == before


def test_lost_reply_keeps_original_plan_and_same_operation_retry(system):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: (_ for _ in ()).throw(TimeoutError())
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "UNKNOWN"
    plan = cancel_host.plans["cancel-1"]
    owner.override = lambda *_: _reply(cancel_host, queue_state="ALREADY_QUEUED")
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    assert cancel_host.plans["cancel-1"] == plan
    assert [call[2]["operation_id"] for call in owner.calls[-2:]] == ["cancel-1", "cancel-1"]


def test_bad_reply_and_record_save_failure_are_unknown_and_never_overwrite(system):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: _reply(cancel_host)
    cancel_host.fail.add("record")
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "UNKNOWN"
    assert "cancel-1" in cancel_host.plans and not cancel_host.records
    cancel_host.fail.clear()
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    prior = cancel_host.records["cancel-1"]
    owner.override = lambda *_: {
        "status": "OK",
        "record": replace(prior, request_key="other").to_mapping(),
    }
    assert client.cancel_status("cancel-1").outcome == "UNKNOWN"
    assert cancel_host.records["cancel-1"] == prior


def test_missing_record_winner_is_unknown_even_if_host_wrote(system):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: _reply(cancel_host)
    cancel_host.fail.add("record_winner")
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "UNKNOWN"
    assert "cancel-1" in cancel_host.records


def test_recovery_status_allows_equivalent_new_run_but_not_new_cancel(system):
    client, host, cancel_host, owner, _, route, binding = system
    owner.override = lambda *_: _reply(cancel_host)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    new_route = replace(route, scope=replace(route.scope, run_id="run-b"))
    runtime, new_owner = _new_runtime(new_route, host, {"direct.cancel", "direct.cancel_status"})
    try:
        resumed = GatewayDirectClient(
            runtime, binding=binding, route=new_route, host=host, cancel_host=cancel_host
        )
        new_owner.override = lambda *_: {
            "status": "OK",
            "record": _record(cancel_host.plans["cancel-1"], state="SENT").to_mapping(),
        }
        assert resumed.cancel_status("cancel-1").record.state == "SENT"
        assert resumed.cancel("key-1", operation_id="cancel-1").outcome == "TRANSPORT_NOT_STARTED"
        assert [call[1] for call in new_owner.calls] == ["direct.cancel_status"]
    finally:
        runtime.close(timeout_seconds=2)


def test_monotonic_progress_and_pid_guard(system):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: _reply(cancel_host, state="SENT", queue_state="OBSERVED")
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "OBSERVED"
    old = cancel_host.records["cancel-1"]
    owner.override = lambda *_: {
        "status": "OK",
        "record": _record(cancel_host.plans["cancel-1"]).to_mapping(),
    }
    result = client.cancel_status("cancel-1")
    assert result.outcome == "OBSERVED" and result.record == old
    assert cancel_host.records["cancel-1"] == old
    client._pid = -1
    assert client.cancel_status("cancel-1").code == "WRONG_PROCESS"


def test_existing_operation_must_match_exact_original_target(system):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: _reply(cancel_host)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    stored = cancel_host.plans["cancel-1"]
    cancel_host.plans["cancel-1"] = replace(stored, reservation_id="other")
    before = len(owner.calls)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "TRANSPORT_NOT_STARTED"
    assert len(owner.calls) == before
    assert client.cancel_status("cancel-1").outcome == "TRANSPORT_NOT_STARTED"


def test_cancel_queue_denial_and_malformed_reply_are_finite(system):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: {"status": "REQUEST_REJECTED", "code": "CANCEL_QUEUE_FULL"}
    result = client.cancel("key-1", operation_id="cancel-1")
    assert (result.outcome, result.code) == ("REQUEST_DENIED", "CANCEL_QUEUE_FULL")
    assert "cancel-1" in cancel_host.plans
    owner.override = lambda *_: {
        "status": "OK",
        "record": _record(cancel_host.plans["cancel-1"]).to_mapping(),
        "cancel_queue_state": "unexpected",
    }
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "UNKNOWN"
    assert not cancel_host.records


def test_legacy_host_has_no_implicit_cancel_storage(system):
    _, host, _, owner, runtime, route, binding = system
    old_client = GatewayDirectClient(runtime, binding=binding, route=route, host=host)
    before = len(owner.calls)
    assert old_client.cancel("key-1", operation_id="cancel-1").code == "CANCEL_HOST_UNAVAILABLE"
    assert old_client.cancel_status("cancel-1").outcome == "TRANSPORT_NOT_STARTED"
    assert len(owner.calls) == before


def test_plan_codec_rejects_extra_or_mismatched_bindings(system):
    client, _, cancel_host, owner, _, route, binding = system
    owner.override = lambda *_: _reply(cancel_host)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    raw = gateway_cancel_plan_to_mapping(cancel_host.plans["cancel-1"])
    with pytest.raises(TypeError):
        gateway_cancel_plan_from_mapping(raw)
    with pytest.raises(ValueError):
        gateway_cancel_plan_from_mapping(raw, expected_binding=None)
    assert gateway_cancel_plan_from_mapping(raw, expected_binding=binding).route == route
    raw["extra"] = "unexpected"
    with pytest.raises(ValueError):
        gateway_cancel_plan_from_mapping(raw, expected_binding=binding, expected_route=route)
    del raw["extra"]
    raw["binding"]["account_scope"] = "other"
    with pytest.raises(ValueError):
        gateway_cancel_plan_from_mapping(raw, expected_binding=binding, expected_route=route)


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt_id", "different-attempt"),
        ("operation_id", "different-operation"),
        ("target_reservation_id", "different-reservation"),
        ("target_digest", "c" * 64),
        ("attempt_generation", "g2"),
    ],
)
def test_status_rejects_changed_attempt_identity(system, field, value):
    client, _, cancel_host, owner, _, _, _ = system
    owner.override = lambda *_: _reply(cancel_host)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    previous = cancel_host.records["cancel-1"]
    values = {field: value}
    if field == "attempt_generation":
        values["generation_current"] = False
    changed = replace(previous, **values)
    owner.override = lambda *_: {"status": "OK", "record": changed.to_mapping()}
    assert client.cancel_status("cancel-1").outcome == "UNKNOWN"
    assert cancel_host.records["cancel-1"] == previous


def test_two_clients_racing_same_operation_keep_first_plan(system):
    client, host, cancel_host, owner, _, route, binding = system
    owner.override = None
    assert client.reserve(_request("key-2")).outcome == "OBSERVED"
    intent = host.intents["key-2"]
    native = {
        "exchange_name": "TEST",
        "client_order_id": "ref-2",
        "fields": {"trading_day": "20300101"},
        "session_generation": 1,
    }
    owner.override = lambda *_: {
        "status": "OK",
        "record": _wire_record(
            intent,
            state="SENT",
            version=3,
            native_identity=native,
            strong_identity_key_digests=["c" * 64],
        ),
    }
    assert client.status("key-2").outcome == "OBSERVED"
    second_runtime, second_owner = _new_runtime(
        route, host, {"direct.cancel", "direct.cancel_status"}
    )
    second = GatewayDirectClient(
        second_runtime, binding=binding, route=route, host=host, cancel_host=cancel_host
    )
    owner.override = lambda *_: _reply(cancel_host)
    second_owner.override = lambda *_: _reply(cancel_host)
    cancel_host.plan_barrier = threading.Barrier(2)
    results = []
    one = threading.Thread(
        target=lambda: results.append(client.cancel("key-1", operation_id="cancel-1"))
    )
    two = threading.Thread(
        target=lambda: results.append(second.cancel("key-2", operation_id="cancel-1"))
    )
    try:
        one.start()
        two.start()
        one.join(5)
        two.join(5)
        assert not one.is_alive() and not two.is_alive()
        assert sorted(item.outcome for item in results) == ["QUEUED", "TRANSPORT_NOT_STARTED"]
        sent = [
            call
            for actor in (owner, second_owner)
            for call in actor.calls
            if call[1] == "direct.cancel"
        ]
        assert len(sent) == 1
        assert sent[0][2]["request_key"] == cancel_host.plans["cancel-1"].request_key
    finally:
        second_runtime.close(timeout_seconds=2)


def test_two_clients_delayed_old_observation_cannot_overwrite_newer(system):
    client, host, cancel_host, owner, _, route, binding = system
    owner.override = lambda *_: _reply(cancel_host)
    assert client.cancel("key-1", operation_id="cancel-1").outcome == "QUEUED"
    second_runtime, second_owner = _new_runtime(route, host, {"direct.cancel_status"})
    second = GatewayDirectClient(
        second_runtime, binding=binding, route=route, host=host, cancel_host=cancel_host
    )
    old = _record(cancel_host.plans["cancel-1"])
    latest = _record(cancel_host.plans["cancel-1"], state="SENT")
    owner.override = lambda *_: {"status": "OK", "record": old.to_mapping()}
    second_owner.override = lambda *_: {"status": "OK", "record": latest.to_mapping()}
    cancel_host.old_save_entered = threading.Event()
    cancel_host.old_save_continue = threading.Event()
    results = []
    worker = threading.Thread(target=lambda: results.append(client.cancel_status("cancel-1")))
    try:
        worker.start()
        assert cancel_host.old_save_entered.wait(5)
        assert second.cancel_status("cancel-1").record == latest
        cancel_host.old_save_continue.set()
        worker.join(5)
        assert not worker.is_alive()
        assert results[0].outcome == "OBSERVED" and results[0].record == latest
        assert cancel_host.records["cancel-1"] == latest
    finally:
        cancel_host.old_save_continue.set()
        worker.join(5)
        second_runtime.close(timeout_seconds=2)
