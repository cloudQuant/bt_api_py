from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_direct_client import GatewayClientRoute, GatewayDirectClient
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


class MemoryHost:
    def __init__(self):
        self.intents = {}
        self.records = {}
        self.releases = {}
        self.fail = set()
        self.markers = []

    def save_intent(self, intent):
        if "intent" in self.fail:
            raise RuntimeError("storage unavailable")
        self.intents.setdefault(intent.request_key, intent)

    def load_intent(self, key):
        return self.intents.get(key)

    def save_record(self, key, record):
        if "record" in self.fail:
            raise RuntimeError("storage unavailable")
        self.records[key] = record

    def load_record(self, key):
        return self.records.get(key)

    def commit_pre_dispatch(self, key, record):
        if "marker" in self.fail:
            raise RuntimeError("marker unavailable")
        self.markers.append((key, record))
        return "marker-1"

    def save_release(self, plan):
        if "release" in self.fail:
            raise RuntimeError("release storage unavailable")
        self.releases.setdefault(plan.request_key, plan)

    def load_release(self, key):
        return self.releases.get(key)


def _request(key="key-1"):
    return OrderRequest(
        symbol="rb",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        account_id="account-a",
        client_order_id="client-a",
        price=Decimal("10"),
        idempotency_key=key,
    )


def _wire_record(intent, *, state="RESERVED", version=1, **changes):
    result = {
        "schema_version": 1,
        "journal_id": intent.binding.journal_id,
        "account_scope": intent.binding.account_scope,
        "strategy_scope": intent.binding.strategy_scope,
        "principal": intent.binding.principal,
        "request_key": intent.request_key,
        "reservation_id": "reservation-a",
        "digest": intent.digest,
        "state": state,
        "version": version,
        "owner_generation": "g1",
        "reservation_generation": "g1",
        "generation_current": True,
        "remaining_ttl_seconds": 9.0 if state == "RESERVED" else None,
        "expiry_pending": False,
        "release_operation_id": None,
        "release_expected_version": None,
        "native_identity": None,
        "strong_identity_key_digests": [],
    }
    result.update(changes)
    return result


class Owner:
    def __init__(self, route, host):
        self.identity = route.identity
        self.run_id = route.scope.run_id
        self.bindings = (
            SimpleNamespace(
                registration_id="reg-a",
                service_epoch="epoch-a",
                active=True,
                mount=route.mount,
                service_id=route.service_id,
                instance_id=route.instance_id,
                scope_ref=route.scope_ref,
                scope=route.scope,
                operations=frozenset(
                    {"direct.reserve", "direct.command_status", "direct.dispatch", "direct.release"}
                ),
                lease_deadline=time.monotonic() + 1000,
                required=True,
            ),
        )
        self.host = host
        self.healthy = True
        self.cleanup_failures = self.unregister_uncertainties = 0
        self.calls = []
        self.override = None

    def start(self):
        pass

    def maintain(self):
        pass

    def next_maintenance_delay(self):
        return 1.0

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((mount, operation, arguments))
        if self.override is not None:
            return self.override(operation, arguments)
        intent = self.host.load_intent(arguments["request_key"])
        if operation == "direct.dispatch":
            return {
                "status": "OK",
                "record": self.host.load_record(intent.request_key).to_mapping(),
                "dispatch_queue_state": "QUEUED",
            }
        if operation == "direct.release":
            return {
                "status": "OK",
                "record": _wire_record(
                    intent,
                    state="RELEASED",
                    version=2,
                    release_operation_id=arguments["operation_id"],
                    release_expected_version=arguments["expected_version"],
                ),
            }
        return {"status": "OK", "record": _wire_record(intent)}

    def close(self):
        self.healthy = False


@pytest.fixture
def system():
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope(
        "paper:demo", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a"
    )
    route = GatewayClientRoute(
        "broker.direct_write", "gateway-service", "gateway-a", "scope-a", scope, identity
    )
    binding = GatewayCommandBinding("journal-a", "account-a", "strategy-a", "principal-a")
    host = MemoryHost()
    owner = []

    def factory():
        obj = Owner(route, host)
        owner.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=2)
    try:
        yield (
            GatewayDirectClient(runtime, binding=binding, route=route, host=host),
            host,
            owner[0],
            runtime,
            route,
            binding,
        )
    finally:
        runtime.close(timeout_seconds=2)


def test_persist_before_reserve_queue_is_not_sent_and_release_is_stable(system):
    client, host, owner, _, _, _ = system
    reserved = client.reserve(_request())
    assert reserved.outcome == "OBSERVED" and reserved.record.state == "RESERVED"
    assert host.intents["key-1"].digest == reserved.record.digest
    queued = client.dispatch("key-1")
    assert queued.outcome == "QUEUED" and queued.marker_id == "marker-1"
    assert queued.record.state == "RESERVED" and host.markers
    released = client.release("key-1", operation_id="release-a")
    assert released.outcome == "OBSERVED" and released.record.state == "RELEASED"
    again = client.release("key-1", operation_id="release-a")
    assert again.outcome == "OBSERVED"
    assert host.releases["key-1"].expected_version == 1
    before = len(owner.calls)
    assert client.release("key-1", operation_id="different").outcome == "TRANSPORT_NOT_STARTED"
    assert len(owner.calls) == before


@pytest.mark.parametrize("code", ["STRATEGY_LEDGER_UNAVAILABLE", "STRATEGY_CLOSE_EXCEEDS",
                                  "SYSTEM_ORDER_UNKNOWN", "UNKNOWN_SCOPE_UNAVAILABLE"])
def test_strategy_ledger_denial_is_request_only_and_never_applies_to_dispatch(system, code):
    client, host, owner, _, _, _ = system
    owner.override = lambda _op, _args: {"status": "REQUEST_REJECTED", "code": code}
    denied = client.reserve(_request())
    assert (denied.outcome, denied.code) == ("REQUEST_DENIED", code)
    assert not host.records
    owner.override = None
    assert client.reserve(_request("key-2")).outcome == "OBSERVED"
    owner.override = lambda _op, _args: {"status": "REQUEST_REJECTED", "code": code}
    assert client.dispatch("key-2").outcome == "UNKNOWN"


def test_submit_left_to_the_risk_delegate_is_a_plain_refusal_of_reserve_and_dispatch(system):
    # D63-37: the Gateway answers SUBMIT_NOT_GRANTED to a strategy whose SUBMIT belongs to its
    # Risk delegate. A code the client does not know is reported as an unknown outcome, and the
    # strategy then waits instead of treating the order as refused.
    client, host, owner, _, _, _ = system
    refusal = {"status": "REQUEST_REJECTED", "code": "SUBMIT_NOT_GRANTED"}
    owner.override = lambda _op, _args: refusal
    denied = client.reserve(_request())
    assert (denied.outcome, denied.code) == ("REQUEST_DENIED", "SUBMIT_NOT_GRANTED")
    assert not host.records
    owner.override = None
    assert client.reserve(_request("key-2")).outcome == "OBSERVED"
    owner.override = lambda _op, _args: refusal
    denied = client.dispatch("key-2")
    assert (denied.outcome, denied.code) == ("REQUEST_DENIED", "SUBMIT_NOT_GRANTED")


@pytest.mark.parametrize("code", ["DISPATCH_CLAIM_UNAVAILABLE", "MARKET_GATE_UNAVAILABLE"])
def test_pre_marker_dispatch_denial_is_dispatch_only(system, code):
    # SimNow 63.09: the Gateway refuses dispatch before the owner queue or any
    # marker (no state change); the client must report a clean denial, not
    # UNKNOWN, and must not accept the code for any other operation.
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    owner.override = lambda _op, _args: {"status": "REQUEST_REJECTED", "code": code}
    denied = client.dispatch("key-1")
    assert (denied.outcome, denied.code) == ("REQUEST_DENIED", code)
    assert client.reserve(_request("key-2")).outcome == "UNKNOWN"


@pytest.mark.parametrize("failure", ["intent", "marker", "release"])
def test_host_failure_prevents_corresponding_rpc(system, failure):
    client, host, owner, _, _, _ = system
    if failure != "intent":
        assert client.reserve(_request()).outcome == "OBSERVED"
    before = len(owner.calls)
    host.fail.add(failure)
    if failure == "intent":
        result = client.reserve(_request())
    elif failure == "marker":
        result = client.dispatch("key-1")
    else:
        result = client.release("key-1", operation_id="release-a")
    assert result.outcome == "TRANSPORT_NOT_STARTED"
    assert len(owner.calls) == before


def test_reserve_lost_reply_recovers_same_original_key(system):
    client, host, owner, _, _, _ = system

    def lost(operation, arguments):
        raise TimeoutError("unknown transport result")

    owner.override = lost
    assert client.reserve(_request()).outcome == "UNKNOWN"
    assert "key-1" in host.intents and not host.records
    owner.override = None
    result = client.status("key-1")
    assert result.outcome == "OBSERVED" and result.record.digest == host.intents["key-1"].digest
    assert [operation for _, operation, _ in owner.calls] == [
        "direct.reserve",
        "direct.command_status",
    ]


def test_bad_digest_missing_fields_and_version_rollback_are_unknown(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    bad = _wire_record(intent, digest="a" * 64)
    owner.override = lambda *_: {"status": "OK", "record": bad}
    assert client.status("key-1").outcome == "UNKNOWN"
    missing = _wire_record(intent)
    del missing["reservation_id"]
    owner.override = lambda *_: {"status": "OK", "record": missing}
    assert client.status("key-1").outcome == "UNKNOWN"
    expired = _wire_record(intent, state="EXPIRED", version=2)
    owner.override = lambda *_: {"status": "OK", "record": expired}
    assert client.status("key-1").outcome == "OBSERVED"
    owner.override = lambda *_: {"status": "OK", "record": _wire_record(intent)}
    assert client.status("key-1").outcome == "UNKNOWN"
    assert host.records["key-1"].state == "EXPIRED"


def test_bad_release_pair_remains_unknown_and_plan_is_retained(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    owner.override = lambda *_: {
        "status": "OK",
        "record": _wire_record(
            intent,
            state="RELEASED",
            version=2,
            release_operation_id="other-release",
            release_expected_version=1,
        ),
    }
    assert client.release("key-1", operation_id="release-a").outcome == "UNKNOWN"
    assert host.releases["key-1"].operation_id == "release-a"
    assert host.records["key-1"].state == "RESERVED"


def test_route_scope_and_reply_binding_mismatches_fail_closed(system):
    client, host, owner, runtime, route, binding = system
    for changed in (
        replace(route, instance_id="other"),
        replace(route, scope_ref="other"),
        replace(route, scope=replace(route.scope, execution_domain_ref="other")),
    ):
        bad = GatewayDirectClient(runtime, binding=binding, route=changed, host=host)
        assert bad.reserve(_request()).outcome == "TRANSPORT_NOT_STARTED"
    assert not owner.calls
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    owner.override = lambda *_: {
        "status": "OK",
        "record": _wire_record(intent, journal_id="other-journal"),
    }
    assert client.status("key-1").outcome == "UNKNOWN"
    assert host.records["key-1"].binding.journal_id == "journal-a"


def test_dynamic_ttl_and_owner_generation_are_not_version_conflicts(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    observations = [
        _wire_record(intent, remaining_ttl_seconds=1),
        _wire_record(
            intent,
            owner_generation="g2",
            generation_current=False,
            remaining_ttl_seconds=0,
            expiry_pending=True,
        ),
        _wire_record(intent, remaining_ttl_seconds=8),
    ]
    for observed in observations:
        owner.override = lambda *_, record=observed: {"status": "OK", "record": record}
        assert client.status("key-1").outcome == "OBSERVED"


@pytest.mark.parametrize("release_version", [1, 2])
def test_expired_same_version_can_add_release_pair_once(system, release_version):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    expired = _wire_record(intent, state="EXPIRED", version=2)
    owner.override = lambda *_: {"status": "OK", "record": expired}
    assert client.status("key-1").outcome == "OBSERVED"
    with_pair = dict(
        expired, release_operation_id="historic-release", release_expected_version=release_version
    )
    owner.override = lambda *_: {"status": "OK", "record": with_pair}
    assert client.status("key-1").outcome == "OBSERVED"
    owner.override = lambda *_: {"status": "OK", "record": expired}
    assert client.status("key-1").outcome == "UNKNOWN"


def test_expired_release_wrong_expected_version_is_unknown(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    owner.override = lambda *_: {
        "status": "OK",
        "record": _wire_record(
            intent,
            state="EXPIRED",
            version=2,
            release_operation_id="release-a",
            release_expected_version=2,
        ),
    }
    assert client.release("key-1", operation_id="release-a").outcome == "UNKNOWN"
    assert host.releases["key-1"].expected_version == 1
    assert host.records["key-1"].state == "RESERVED"


def test_lost_release_reply_preserves_plan_and_status_can_observe(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]

    def lost(*_):
        raise TimeoutError("synthetic")

    owner.override = lost
    assert client.release("key-1", operation_id="release-a").outcome == "UNKNOWN"
    assert host.releases["key-1"].operation_id == "release-a"
    owner.override = lambda *_: {
        "status": "OK",
        "record": _wire_record(
            intent,
            state="EXPIRED",
            version=2,
            release_operation_id="release-a",
            release_expected_version=1,
        ),
    }
    assert client.status("key-1").outcome == "OBSERVED"
    assert host.releases["key-1"].operation_id == "release-a"


def test_dispatch_lost_reply_keeps_marker_and_original_identity(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"

    def lost(*_):
        raise TimeoutError("synthetic")

    owner.override = lost
    result = client.dispatch("key-1")
    assert result.outcome == "UNKNOWN" and result.marker_id == "marker-1"
    assert host.markers[0][0] == "key-1"
    assert host.intents["key-1"].request_key == "key-1"
    assert [op for _, op, _ in owner.calls] == ["direct.reserve", "direct.dispatch"]


def test_bad_native_identity_does_not_replace_record(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    bad = _wire_record(
        intent,
        state="DISPATCHING",
        version=2,
        native_identity={
            "exchange_name": "native",
            "client_order_id": "one",
            "fields": {"secret": "private"},
            "session_generation": 1,
        },
        strong_identity_key_digests=["a" * 64],
    )
    owner.override = lambda *_: {"status": "OK", "record": bad}
    assert client.status("key-1").outcome == "UNKNOWN"
    assert host.records["key-1"].state == "RESERVED"


def test_fork_child_rejects_before_inherited_client_lock(system):
    import os
    import select
    import signal
    import threading

    if not hasattr(os, "fork"):
        pytest.skip("fork unavailable")
    client, _, _, _, _, _ = system
    held, release = threading.Event(), threading.Event()

    def holder():
        with client._lock:
            held.set()
            release.wait(3)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(2)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        signal.alarm(2)
        try:
            results = [
                client.reserve(_request()),
                client.status("key-1"),
                client.dispatch("key-1"),
                client.release("key-1", operation_id="release-a"),
            ]
            good = all(
                item.outcome == "TRANSPORT_NOT_STARTED" and item.code == "WRONG_PROCESS"
                for item in results
            )
            os.write(write_fd, b"ok" if good else b"bad")
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 3)
        result = os.read(read_fd, 16) if ready else b""
        _, exit_status = os.waitpid(pid, 0)
        assert result == b"ok" and os.waitstatus_to_exitcode(exit_status) == 0
    finally:
        os.close(read_fd)
        release.set()
        thread.join(3)


def test_host_record_failure_after_remote_write_is_unknown(system):
    client, host, owner, _, _, _ = system
    host.fail.add("record")
    result = client.reserve(_request())
    assert result.outcome == "UNKNOWN"
    assert "key-1" in host.intents and not host.records
    assert [operation for _, operation, _ in owner.calls] == ["direct.reserve"]


def test_unknown_rejection_reply_is_not_claimed_definitive(system):
    client, host, owner, _, _, _ = system
    owner.override = lambda *_: {"status": "REQUEST_REJECTED", "code": "UNRECOGNIZED"}
    assert client.reserve(_request()).outcome == "UNKNOWN"
    assert "key-1" in host.intents


def test_proven_current_rpc_not_sent_keeps_original_intent(system):
    client, host, owner, _, _, _ = system

    def not_sent(*_):
        raise ServiceRuntimeRequestNotSentError()

    owner.override = not_sent
    result = client.reserve(_request())
    assert result.outcome == "TRANSPORT_NOT_STARTED" and result.code == "REQUEST_NOT_SENT"
    assert "key-1" in host.intents and not host.records


def test_different_retry_ttl_uses_first_saved_intent(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request(), ttl_seconds=10).outcome == "OBSERVED"
    assert client.reserve(_request(), ttl_seconds=30).outcome == "OBSERVED"
    assert host.intents["key-1"].ttl_seconds == 10
    assert owner.calls[-1][2]["ttl_seconds"] == 10


def test_thread_snapshot_carries_immutable_exact_route_and_identity(system):
    _, _, _, runtime, route, _ = system
    snapshot = runtime.status()
    assert snapshot.identity == route.identity and snapshot.run_id == route.scope.run_id
    (binding,) = snapshot.bindings
    assert binding.scope == route.scope and binding.mount == route.mount
    assert binding.service_id == route.service_id and binding.instance_id == route.instance_id
    assert binding.scope_ref == route.scope_ref and type(binding.operations) is frozenset
    with pytest.raises((AttributeError, TypeError)):
        binding.scope_ref = "changed"


def test_import_does_not_load_gateway_dispatcher_or_native_provider():
    import subprocess
    import sys

    # bt_api_py.__init__ currently imports a pure CTP credential-binding type
    # module; this check concerns providers, loaders and local write assembly.
    code = (
        "import sys; import bt_api_py.gateway_direct_client; "
        "assert not any(x.startswith(('bt_api_gateway', 'bt_api_ctp', "
        "'bt_api_py.gateway_direct_dispatch', 'bt_api_py.direct_write', 'bt_api_okx', "
        "'bt_api_binance', 'backtrader_runtime._ctp_credential_binding')) for x in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_explicit_dispatch_after_lost_reply_observes_sent_without_new_send(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    calls = [0]

    def sent_then_lost(operation, arguments):
        assert operation == "direct.dispatch"
        calls[0] += 1
        if calls[0] == 1:
            raise TimeoutError("sent before lost reply")
        return {
            "status": "OK",
            "dispatch_queue_state": "OBSERVED",
            "record": _wire_record(
                intent,
                state="SENT",
                version=3,
                native_identity={
                    "exchange_name": "native",
                    "client_order_id": "client-a",
                    "fields": {"venue_order_id": "order-a"},
                    "session_generation": 1,
                },
                strong_identity_key_digests=["a" * 64],
            ),
        }

    owner.override = sent_then_lost
    first = client.dispatch("key-1")
    assert first.outcome == "UNKNOWN" and first.marker_id == "marker-1"
    assert host.records["key-1"].state == "RESERVED"
    second = client.dispatch("key-1")
    assert second.outcome == "OBSERVED" and second.record.state == "SENT"
    assert second.marker_id == first.marker_id and calls[0] == 2


@pytest.mark.parametrize("state", ["EXPIRED", "RELEASED"])
def test_dispatch_replay_observes_terminal_record(system, state):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    intent = host.intents["key-1"]
    changes = (
        {"release_operation_id": "release-a", "release_expected_version": 1}
        if state == "RELEASED"
        else {}
    )
    record = _wire_record(intent, state=state, version=2, **changes)
    owner.override = lambda *_: {"status": "OK", "record": record, "dispatch_queue_state": "OBSERVED"}
    observed = client.dispatch("key-1")
    assert observed.outcome == "OBSERVED" and observed.record.state == state


def test_dispatch_observed_cannot_describe_unqueued_reserved_record(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    record = _wire_record(host.intents["key-1"], state="RESERVED", version=1)
    owner.override = lambda *_: {"status": "OK", "record": record, "dispatch_queue_state": "OBSERVED"}
    result = client.dispatch("key-1")
    assert result.outcome == "UNKNOWN" and result.code == "INVALID_REPLY_OR_HOST_FAILURE"
    assert host.records["key-1"].state == "RESERVED"


@pytest.mark.parametrize("field,value", [("principal", "other"), ("reservation_id", "other"), ("digest", "b" * 64)])
def test_dispatch_observed_rejects_wrong_original_identity(system, field, value):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    record = _wire_record(host.intents["key-1"], state="UNKNOWN", version=3,
        native_identity={"exchange_name": "native", "client_order_id": "client-a",
                         "fields": {"venue_order_id": "order-a"}, "session_generation": 1},
        strong_identity_key_digests=["a" * 64])
    record[field] = value
    owner.override = lambda *_: {"status": "OK", "record": record, "dispatch_queue_state": "OBSERVED"}
    result = client.dispatch("key-1")
    assert result.outcome == "UNKNOWN" and result.code == "INVALID_REPLY_OR_HOST_FAILURE"
    assert host.records["key-1"].state == "RESERVED"


def test_dispatch_observed_unknown_keeps_original_identity_and_does_not_resend(system):
    client, host, owner, _, _, _ = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    record = _wire_record(host.intents["key-1"], state="UNKNOWN", version=3,
        native_identity={"exchange_name": "native", "client_order_id": "client-a",
                         "fields": {"venue_order_id": "order-a"}, "session_generation": 1},
        strong_identity_key_digests=["a" * 64])
    owner.override = lambda *_: {"status": "OK", "record": record, "dispatch_queue_state": "OBSERVED"}
    result = client.dispatch("key-1")
    assert result.outcome == "OBSERVED" and result.record.state == "UNKNOWN"
    assert result.record.reservation_id == "reservation-a" and host.records["key-1"] == result.record
    assert client.dispatch("key-1").outcome == "TRANSPORT_NOT_STARTED"


def test_new_run_can_read_and_release_but_cannot_dispatch_old_intent(system):
    client, host, _, _, old_route, binding = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    new_route = replace(old_route, scope=replace(old_route.scope, run_id="run-b"))
    owner = []

    def factory():
        obj = Owner(new_route, host)
        owner.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=2)
    try:
        restarted = GatewayDirectClient(runtime, binding=binding, route=new_route, host=host)
        assert restarted.status("key-1").outcome == "OBSERVED"
        assert restarted.dispatch("key-1").outcome == "TRANSPORT_NOT_STARTED"
        assert all(operation != "direct.dispatch" for _, operation, _ in owner[0].calls)
        assert restarted.release("key-1", operation_id="release-a").outcome == "OBSERVED"
    finally:
        runtime.close(timeout_seconds=2)


def test_status_never_tofu_adopts_unknown_local_intent(system):
    client, host, owner, _, _, _ = system
    assert client.status("unknown-key").outcome == "TRANSPORT_NOT_STARTED"
    assert not host.intents and not owner.calls


def test_write_requires_status_capability_for_original_key_recovery(system):
    _, host, _, _, route, binding = system
    owner = []

    def factory():
        obj = Owner(route, host)
        obj.bindings[0].operations = frozenset({"direct.reserve"})
        owner.append(obj)
        return obj

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=2)
    try:
        client = GatewayDirectClient(runtime, binding=binding, route=route, host=host)
        assert client.reserve(_request()).outcome == "TRANSPORT_NOT_STARTED"
        assert not owner[0].calls and not host.intents
    finally:
        runtime.close(timeout_seconds=2)


@pytest.mark.parametrize("adopted", ["origin", "third", "duplicate"])
def test_recovery_route_matches_persisted_origin_only(system, adopted):
    client, host, _, _, old_route, binding = system
    assert client.reserve(_request()).outcome == "OBSERVED"
    new_route = replace(old_route, scope=replace(old_route.scope, run_id="run-new"))
    owner = []

    def factory():
        obj = Owner(new_route, host)
        target = (
            old_route.scope if adopted != "third" else replace(old_route.scope, run_id="run-third")
        )
        obj.bindings[0].scope = target
        if adopted == "duplicate":
            obj.bindings += (replace_binding(obj.bindings[0], scope=old_route.scope),)
        owner.append(obj)
        return obj

    def replace_binding(original, **changes):
        return SimpleNamespace(**(vars(original) | changes))

    runtime = ThreadedServiceClientRuntime(factory)
    runtime.start(timeout_seconds=2)
    try:
        restarted = GatewayDirectClient(runtime, binding=binding, route=new_route, host=host)
        assert restarted.reserve(_request("new-key")).code == "ROUTE_UNAVAILABLE"
        assert restarted.dispatch("key-1").outcome == "TRANSPORT_NOT_STARTED"
        if adopted == "origin":
            assert restarted.status("key-1").outcome == "OBSERVED"
            assert restarted.release("key-1", operation_id="release-a").outcome == "OBSERVED"
            assert [item[1] for item in owner[0].calls] == [
                "direct.command_status",
                "direct.release",
            ]
        else:
            assert restarted.status("key-1").code == "ROUTE_UNAVAILABLE"
            assert restarted.release("key-1", operation_id="release-a").code == "ROUTE_UNAVAILABLE"
            assert owner[0].calls == [] and not host.releases
    finally:
        runtime.close(timeout_seconds=2)
