"""Owner lifecycle and shared-runtime diagnostic client using synthetic RPCs."""

from copy import copy, deepcopy
from dataclasses import FrozenInstanceError, replace
from threading import Thread
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.gateway_instrument_status_query import OPERATIONS
from bt_api_py.gateway_strategy_instrument_status import (
    GatewayInstrumentStatusOwner,
    GatewayStrategyInstrumentStatusClient,
)
from bt_api_py.service_runtime import RegisteredBinding
from bt_api_py.service_runtime_thread import OwnerOperationError, ThreadedRuntimeError
from tests.unit.test_gateway_instrument_status_query import KEY, observed
from tests.unit.test_iter63_gateway_strategy_client import parts


@pytest.fixture
def rig():
    values = parts()
    route, peer = values["route"], values["peer"]
    binding = RegisteredBinding(
        "broker.direct_write",
        route.service_id,
        route.instance_id,
        route.scope_ref,
        route.scope,
        "registration",
        "epoch",
        OPERATIONS,
        1000,
        "policy",
        "requirements",
        "transport",
        5,
        lease_deadline=1000,
    )

    class Runtime:
        def __init__(self):
            self.bindings = (binding,)
            self.identity, self.run_id = route.identity, route.scope.run_id
            self.peer, self.fingerprint = peer, "a" * 64
            self.now, self.answer = 1, observed()

        def _monotonic_now(self):
            return self.now

        def authenticated_peer_for_binding(self, _):
            return (self.peer, self.fingerprint)

        def call(self, mount, operation, arguments, **kwargs):
            assert mount == "broker.direct_write" and operation == "direct.instrument_status"
            assert arguments == {"instrument": KEY.to_dict()} and kwargs == {
                "account_ref": "account",
                "scope_ref": "scope",
            }
            return deepcopy(self.answer)

    runtime = Runtime()
    owner = GatewayInstrumentStatusOwner(
        account_ref="account", scope_ref="scope", route=route, peer=peer
    )
    owner.start(runtime)
    return owner, runtime, binding, route, peer


def read(owner):
    return owner.invoke("query", {"instrument": KEY.to_dict()})


def validate(owner, value):
    return owner.invoke(
        "validate",
        {
            "registration_id": value["wire"]["registration_id"],
            "service_epoch": value["wire"]["service_epoch"],
            "observation_id": value["observation_id"],
        },
    )


def test_owner_binds_exact_proof_and_rejects_replaced_binding(rig):
    owner, runtime, binding, _, _ = rig
    first = read(owner)
    assert validate(owner, first) == {"valid": True, "peer_fingerprint": "a" * 64}
    runtime.bindings = (replace(binding),)  # equal values, different binding object
    second = read(owner)
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        validate(owner, first)
    assert validate(owner, second) == {"valid": True, "peer_fingerprint": "a" * 64}
    runtime.fingerprint = "b" * 64
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        validate(owner, second)
    owner.close()
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        read(owner)


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "run",
        "identity",
        "lease",
        "inactive",
        "operation",
        "peer",
        "fingerprint",
        "registration",
        "epoch",
    ],
)
def test_owner_refuses_wrong_scope_lease_proof_or_reply(rig, change):
    owner, runtime, binding, _, _ = rig
    if change == "scope":
        runtime.bindings = (replace(binding, scope=replace(binding.scope, account_ref="other")),)
    elif change == "run":
        runtime.run_id = "other"
    elif change == "identity":
        runtime.identity = WireIdentity(WireRole.STRATEGY_CLIENT, "other", "strategy")
    elif change == "lease":
        runtime.now = binding.lease_deadline
    elif change == "inactive":
        runtime.bindings = (replace(binding, remote_lease_inactive=True),)
    elif change == "operation":
        runtime.bindings = (replace(binding, operations=frozenset()),)
    elif change == "peer":
        runtime.peer = WireIdentity(WireRole.SERVICE, "other", "gateway")
    elif change == "fingerprint":
        runtime.fingerprint = "untrusted"
    elif change == "registration":
        runtime.answer["registration_id"] = "old"
    elif change == "epoch":
        runtime.answer["service_epoch"] = "old"
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        read(owner)


def test_mutation_during_call_and_wrong_thread_pid(rig):
    owner, runtime, binding, _, _ = rig
    original = runtime.call

    def mutate(*args, **kwargs):
        response = original(*args, **kwargs)
        object.__setattr__(binding, "policy_version", "changed")
        return response

    runtime.call = mutate
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        read(owner)
    errors = []

    def wrong_thread():
        try:
            read(owner)
        except OwnerOperationError as exc:
            errors.append(exc.code)

    thread = Thread(target=wrong_thread)
    thread.start()
    thread.join()
    assert errors == ["WRONG_OWNER_THREAD"]
    owner._pid = -1
    with pytest.raises(OwnerOperationError, match="WRONG_PROCESS"):
        read(owner)


def test_frozen_binding_fields_are_rechecked_on_validate(rig):
    owner, _, binding, _, _ = rig
    value = read(owner)
    object.__setattr__(binding, "policy_version", "changed")
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        validate(owner, value)


def test_peer_change_during_query_discards_reply(rig):
    owner, runtime, _, _, _ = rig
    original = runtime.call

    def changed(*args, **kwargs):
        response = original(*args, **kwargs)
        runtime.fingerprint = "b" * 64
        return response

    runtime.call = changed
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        read(owner)


def test_runtime_copy_cannot_reuse_original_token_or_query(rig):
    owner, runtime, _, _, _ = rig
    value = read(owner)
    owner._runtime = copy(runtime)  # retains the same binding, credentials and peer proof
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        validate(owner, value)
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        read(owner)


def test_runtime_replacement_during_rpc_discards_observation(rig):
    owner, runtime, _, _, _ = rig
    original = runtime.call

    def changed(*args, **kwargs):
        response = original(*args, **kwargs)
        owner._runtime = copy(runtime)
        return response

    runtime.call = changed
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        read(owner)
    assert not owner._issued


def test_rejection_does_not_create_observation_token(rig):
    owner, runtime, _, _, _ = rig
    runtime.answer = {"status": "REQUEST_REJECTED", "code": "SCOPE_DENIED"}
    value = read(owner)
    assert value == {
        "wire": {"status": "REQUEST_REJECTED", "code": "SCOPE_DENIED"},
        "observation_id": None,
    }
    assert not owner._issued
    with pytest.raises(OwnerOperationError, match="INVALID_OWNER_ARGUMENTS"):
        owner.invoke(
            "validate", {"registration_id": "r", "service_epoch": "e", "observation_id": []}
        )


def test_old_token_eviction_is_bounded(rig):
    owner, _, _, _, _ = rig
    old = read(owner)
    for _ in range(128):
        read(owner)
    assert len(owner._issued) == 128
    with pytest.raises(OwnerOperationError, match="INSTRUMENT_STATUS_UNAVAILABLE"):
        validate(owner, old)
    owner.close()
    assert not owner._issued


def test_typed_client_retains_pending_and_returns_immutable_snapshot(rig):
    owner, _, binding, route, peer = rig

    class Handle:
        def __init__(self, func, timeout=False):
            self.func, self.first = func, timeout

        def result(self, *, timeout_seconds):
            if self.first:
                self.first = False
                raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")
            return self.func()

    class ClientRuntime:
        def __init__(self):
            self.operations = []

        def submit_owner(self, op, args):
            self.operations.append(op)
            assert op in ("instrument_status.query", "instrument_status.validate")
            return Handle(
                lambda: owner.invoke(op.split(".")[1], args), timeout=op.endswith("query")
            )

        def status(self):
            item = SimpleNamespace(
                mount=binding.mount,
                active=True,
                registration_id=binding.registration_id,
                service_epoch=binding.service_epoch,
                service_id=binding.service_id,
                instance_id=binding.instance_id,
                scope_ref=binding.scope_ref,
                scope=binding.scope,
                authenticated_peer=peer,
                server_key_fingerprint="a" * 64,
            )
            return SimpleNamespace(
                state="running",
                healthy=True,
                bindings=(item,),
                identity=route.identity,
                run_id=route.scope.run_id,
            )

    runtime = ClientRuntime()
    client = GatewayStrategyInstrumentStatusClient(runtime, route=route, peer=peer)
    assert client.query(KEY).status == "UNAVAILABLE"
    other = replace(KEY, native_symbol="IH2612")
    assert client.query(other).status == "UNAVAILABLE"
    result = client.query(KEY)
    assert result.status == "OBSERVED" and result.projection == observed_projection()
    assert (
        result.decision_eligible is result.continuity_proven is result.authorizes_trading is False
    )
    assert runtime.operations == ["instrument_status.query", "instrument_status.validate"]
    with pytest.raises(FrozenInstanceError):
        result.projection.state = "STALE"
    with pytest.raises(FrozenInstanceError):
        result.instrument.venue = "OTHER"
    with pytest.raises(ValueError):
        replace(result, decision_eligible=True)
    client._pid = -1
    with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
        client.query(KEY)


def observed_projection():
    from tests.unit.test_gateway_instrument_status_query import PROJECTION

    return PROJECTION


@pytest.mark.parametrize(
    "change", ["peer", "fingerprint", "registration", "epoch", "scope", "identity", "run", "closed"]
)
def test_typed_client_rechecks_final_runtime_snapshot(rig, change):
    owner, _, binding, route, peer = rig

    class Handle:
        def __init__(self, value):
            self.value = value

        def result(self, *, timeout_seconds):
            return self.value

    class ClientRuntime:
        def submit_owner(self, operation, arguments):
            return Handle(owner.invoke(operation.split(".")[1], arguments))

        def status(self):
            item = SimpleNamespace(
                mount=binding.mount,
                active=True,
                registration_id=binding.registration_id,
                service_epoch=binding.service_epoch,
                service_id=binding.service_id,
                instance_id=binding.instance_id,
                scope_ref=binding.scope_ref,
                scope=binding.scope,
                authenticated_peer=peer,
                server_key_fingerprint="a" * 64,
            )
            snapshot = SimpleNamespace(
                state="running",
                healthy=True,
                bindings=(item,),
                identity=route.identity,
                run_id=route.scope.run_id,
            )
            if change == "peer":
                item.authenticated_peer = WireIdentity(WireRole.SERVICE, "other", "gateway")
            elif change == "fingerprint":
                item.server_key_fingerprint = "b" * 64
            elif change == "registration":
                item.registration_id = "old"
            elif change == "epoch":
                item.service_epoch = "old"
            elif change == "scope":
                item.scope = replace(item.scope, run_id="old")
            elif change == "identity":
                snapshot.identity = WireIdentity(WireRole.STRATEGY_CLIENT, "other", "strategy")
            elif change == "run":
                snapshot.run_id = "other"
            elif change == "closed":
                snapshot.state, snapshot.healthy = "closed", False
            return snapshot

    result = GatewayStrategyInstrumentStatusClient(ClientRuntime(), route=route, peer=peer).query(
        KEY
    )
    assert result.status == "UNAVAILABLE" and result.projection is None


def test_timed_out_validation_rechecks_original_binding_before_delivery(rig):
    owner, runtime, binding, route, peer = rig

    class Handle:
        def __init__(self, value, timeout=False):
            self.value, self.first = value, timeout

        def result(self, *, timeout_seconds):
            if self.first:
                self.first = False
                raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")
            return self.value

    class ClientRuntime:
        def __init__(self):
            self.operations = []

        def submit_owner(self, operation, arguments):
            self.operations.append(operation)
            value = owner.invoke(operation.split(".")[1], arguments)
            return Handle(
                value,
                timeout=operation == "instrument_status.validate"
                and self.operations.count(operation) == 1,
            )

    shared = ClientRuntime()
    client = GatewayStrategyInstrumentStatusClient(shared, route=route, peer=peer)
    assert client.query(KEY).status == "UNAVAILABLE"
    runtime.bindings = (replace(binding),)
    assert client.query(KEY).status == "UNAVAILABLE"
    assert shared.operations == [
        "instrument_status.query",
        "instrument_status.validate",
        "instrument_status.validate",
    ]


def test_typed_client_runtime_replacement_keeps_submitted_handle(rig):
    _, _, _, route, peer = rig

    class Handle:
        def result(self, *, timeout_seconds):
            raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")

    class ClientRuntime:
        def __init__(self):
            self.operations = []

        def submit_owner(self, operation, arguments):
            self.operations.append(operation)
            client._runtime = copy(self)
            return Handle()

    shared = ClientRuntime()
    client = GatewayStrategyInstrumentStatusClient(shared, route=route, peer=peer)
    assert client.query(KEY).status == "UNAVAILABLE"
    assert client._pending is not None
    assert client.query(KEY).status == "UNAVAILABLE"
    assert shared.operations == ["instrument_status.query"]
