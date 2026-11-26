"""Gateway ports of the Risk service process (64.09). UNIT/FAKE evidence: no Gateway, no account."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.gateway_execution_stream_protocol import (
    GatewayExecutionObservation,
    GatewayExecutionStreamEvent,
)
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.control_intent_wire import CONTROL_INTENT_REJECTION_CODES

from bt_api_py.gateway_account_observer_client import GatewayAccountObservedEvent
from bt_api_py.risk_service_config import RiskInstrumentBinding
from bt_api_py.risk_service_gateway import (
    DelegateOrderPort,
    GatewayControlAdapter,
    ObservationPump,
    RiskFactsCache,
    account_snapshot_from_observation,
    map_delegate_result,
    observed_event_from_account_event,
    price_fact_from_market_event,
    strategy_financials_from_reply,
)
from bt_api_py.service_runtime_thread import (
    BoundCallObservation,
    BoundCallResult,
    BoundCallRoute,
)

ACCOUNT = "account-a"
RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
RISK = WireIdentity(WireRole.SERVICE, "risk-principal", "risk-actor")
EPOCH = "e" * 64
CONTROL_FINGERPRINT = "f" * 64
CONTROL_OPERATIONS = frozenset(
    {
        "direct.control.identity",
        "direct.control.apply",
        "direct.control.status",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
CONTROL_INTENT_OPERATION = "direct.control.intent.apply"
CONTROL_INTENT_OPERATIONS = CONTROL_OPERATIONS | frozenset({CONTROL_INTENT_OPERATION})
CONTROL_ROUTE = BoundCallRoute(
    WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-control-actor"),
    WireScope("test-domain", account_ref=ACCOUNT),
    "scope-a",
    "gateway",
    "gateway-a",
    WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway"),
)


def obs(state="RESERVED", version=None, digest="d" * 64):
    return SimpleNamespace(state=state, reservation_id="res-1", digest=digest,
                           version=version or {"RESERVED": 1, "DISPATCHING": 2, "SENT": 3,
                                               "NOT_SENT": 3, "UNKNOWN": 3, "RELEASED": 2,
                                               "EXPIRED": 2}[state],
                           owner_generation="g1", reservation_generation="g1")


def result(outcome, observation=None, code=None):
    return SimpleNamespace(outcome=outcome, observation=observation, code=code)


# ---------------------------------------------------------------------------- outcome mapping


@pytest.mark.parametrize("outcome", ["REQUEST_DENIED", "TRANSPORT_NOT_STARTED", "UNKNOWN"])
def test_client_outcomes_pass_through_exactly(outcome):
    reply = map_delegate_result(result(outcome, code="SOME_CODE"))
    assert reply.outcome == outcome and reply.code == "SOME_CODE" and reply.state is None


def test_observed_carries_the_gateway_proof_fields():
    reply = map_delegate_result(result("OBSERVED", obs("RELEASED")))
    assert (reply.outcome, reply.state, reply.reservation_id, reply.digest, reply.version) == (
        "OBSERVED", "RELEASED", "res-1", "d" * 64, 2)
    assert reply.owner_generation == reply.reservation_generation == "g1"


@pytest.mark.parametrize("value", [
    result("OBSERVED", None), result("OBSERVED", SimpleNamespace(state="BOGUS")),
    result("SENT_MAYBE"), object(), result("QUEUED"),
])
def test_anything_unproven_is_unknown(value):
    assert map_delegate_result(value).outcome == "UNKNOWN"


# ---------------------------------------------------------------------------- delegate port


class FakeClient:
    def __init__(self, host):
        self.host, self.calls = host, []
        self.actor_state = "RESERVED"
        self.dispatch_result = result("QUEUED")
        self.release_result = SimpleNamespace(outcome="OBSERVED", state="RELEASED", code=None)

    def reserve(self, command, request, *, ttl_seconds, timeout_seconds):
        self.calls.append(("reserve", command, request, ttl_seconds))
        return result("OBSERVED", obs("RESERVED"))

    def dispatch(self, request_key, *, market_ref, timeout_seconds):
        self.calls.append(("dispatch", request_key, market_ref))
        return self.dispatch_result

    def actor_status(self, request_key, *, timeout_seconds):
        self.calls.append(("status", request_key))
        self.last = SimpleNamespace(command=obs(self.actor_state), native_identity_digest=None)
        return result("OBSERVED", self.last)

    def release_actor(self, observation, *, operation_id, timeout_seconds):
        assert observation is self.last
        self.calls.append(("release", operation_id))
        self.host.saved = obs("RELEASED")
        return self.release_result


class FakeHost:
    saved = None

    def load_delegate_observation(self, _key):
        return self.saved if self.saved is not None else obs("RESERVED")


class Handle:
    def __init__(self, route):
        self.route, self.host = route, FakeHost()
        self.client, self.closed = FakeClient(self.host), False

    def close(self):
        self.closed = True
        # A successful replacement requires an explicit cleanup observation.
        # None is not evidence that a registration was removed.
        return SimpleNamespace(state="closed", thread_alive=False, error_code=None,
                               unregister_uncertainties=0, owner_cleanup_uncertainties=0,
                               unknown_registration_count=0)


def port(connect=None, clock=None):
    handles = []

    def default(route):
        handle = Handle(route)
        handles.append(handle)
        return handle

    value = DelegateOrderPort(
        account_scope=ACCOUNT, execution_domain_ref="paper:demo", identity=RISK,
        service_id="gateway", instance_id="gateway-a", scope_ref="scope-a",
        grant_revisions={"strategy-a": "risk-v1"},
        instrument_for=lambda venue, native: RB if (venue, native) == ("SHFE", "rb") else None,
        connect=connect or default, timeout_seconds=5, retry_seconds=10,
        monotonic=clock or (lambda: 0.0),
    )
    return value, handles


def canonical(key="k1"):
    from bt_api_base.gateway_direct_protocol import canonical_gateway_payload

    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    request = OrderRequest(symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
                           quantity=Decimal("1"), price=Decimal("10"), account_id=ACCOUNT,
                           client_order_id="c", idempotency_key=key, offset="open",
                           quantity_unit="lots", exchange_id="SHFE")
    return canonical_gateway_payload(request.to_dict())


def test_one_delegate_client_per_strategy_run_and_a_new_run_retires_the_old(tmp_path):
    value, handles = port()
    value.note_run("strategy-a", "run-1")
    reply = value.reserve(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          canonical_payload=canonical(), parent_ref=None, child_id=None,
                          ttl_seconds=10)
    assert reply.outcome == "OBSERVED" and reply.state == "RESERVED"
    (handle,) = handles
    assert handle.route.scope.run_id == "run-1" and handle.route.scope.strategy_ref == "strategy-a"
    _, command, _, ttl = handle.client.calls[0]
    assert command.run_id == "run-1" and command.actor_kind == "risk" and ttl == 10
    assert command.actor_principal == RISK.principal_ref and command.grant_revision == "risk-v1"
    assert command.instrument == RB
    # Same run: same client.
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert len(handles) == 1
    # The strategy registered a new run: the old client is closed, the old run gets none.
    value.note_run("strategy-a", "run-2")
    assert handle.closed
    old = value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert old.outcome == "TRANSPORT_NOT_STARTED" and len(handles) == 1
    value.status(strategy_ref="strategy-a", run_id="run-2", request_key="k1")
    assert len(handles) == 2 and handles[1].route.scope.run_id == "run-2"
    # A strategy without a grant revision never gets a client.
    assert value.status(strategy_ref="strategy-z", run_id="run-1",
                        request_key="k1").outcome == "TRANSPORT_NOT_STARTED"
    value.close()
    assert handles[1].closed


def test_failed_registration_is_not_retried_without_cleanup_proof():
    now = [0.0]
    attempts = []

    def connect(route):
        attempts.append(route)
        raise RuntimeError("refused")

    value, _ = port(connect=connect, clock=lambda: now[0])
    first = value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert first.outcome == "TRANSPORT_NOT_STARTED" and first.code == "ROUTE_UNAVAILABLE"
    now[0] = 5.0
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert len(attempts) == 1
    now[0] = 10.5
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    # A generic failure might have left a registration behind: elapsed time
    # alone cannot authorize another connection attempt.
    assert len(attempts) == 1 and value._slots["strategy-a"].blocked


def test_unconfirmed_delegate_unregister_prevents_replacement():
    value, handles = port()
    first = value.reserve(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          canonical_payload=canonical(), parent_ref=None, child_id=None,
                          ttl_seconds=10)
    assert first.outcome == "OBSERVED"
    old = handles[0]
    old.runtime = SimpleNamespace(status=lambda: SimpleNamespace(state="failed"))
    closes = []
    def close_unconfirmed():
        closes.append(1)
        return SimpleNamespace(state="failed", thread_alive=False, error_code="MAINTENANCE_FAILED",
                               unregister_uncertainties=1, owner_cleanup_uncertainties=0)
    old.close = close_unconfirmed
    result = value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert result.outcome == "UNKNOWN" and len(handles) == 1
    assert value._slots["strategy-a"].handle is old
    assert [call[0] for call in old.client.calls] == ["reserve"]
    assert not value.close() and closes == [1]


def test_failed_delegate_cleanup_blocks_both_old_and_new_run():
    value, handles = port()
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    old = handles[0]
    closes = []
    def fail():
        closes.append(1)
        raise RuntimeError("cleanup unconfirmed")
    old.close = fail
    value.note_run("strategy-a", "run-2")
    assert value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1").outcome == "UNKNOWN"
    assert value.status(strategy_ref="strategy-a", run_id="run-2", request_key="k1").outcome == "UNKNOWN"
    assert len(handles) == 1 and value._slots["strategy-a"].handle is old
    assert not value.close() and closes == [1]


def test_observer_reconnect_invalidates_delegate_without_replaying_prior_write():
    value, handles = port()
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="initial-probe")
    old = handles[0]
    def unknown_reserve(*_args, **_kwargs):
        old.client.calls.append(("reserve", "k1"))
        return result("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
    old.client.reserve = unknown_reserve
    attempt = value.reserve(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                            canonical_payload=canonical(), parent_ref=None, child_id=None,
                            ttl_seconds=10)
    assert attempt.outcome == "UNKNOWN"
    value.invalidate_registrations()
    assert len(handles) == 1  # merely marks old registrations; it sends nothing
    reply = value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert reply.outcome == "OBSERVED" and old.closed and len(handles) == 2
    assert [call[0] for call in old.client.calls] == ["status", "reserve"]
    assert [call[0] for call in handles[1].client.calls] == ["status"]
    assert handles[1].route == old.route


def test_delegate_owner_cleanup_uncertainty_prevents_replacement():
    value, handles = port()
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    old = handles[0]
    closes = []
    def close_unconfirmed():
        closes.append(1)
        return SimpleNamespace(state="failed", thread_alive=False, error_code="MAINTENANCE_FAILED",
                               unregister_uncertainties=0, owner_cleanup_uncertainties=1)
    old.close = close_unconfirmed
    value.invalidate_registrations()
    assert value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1").outcome == "UNKNOWN"
    assert len(handles) == 1 and value._slots["strategy-a"].handle is old
    assert not value.close() and closes == [1]


def test_delegate_factory_cleanup_unproven_does_not_reconnect():
    value, handles = port()
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    old = handles[0]
    old.runtime = SimpleNamespace(status=lambda: SimpleNamespace(state="failed"))
    closes = []
    def close_unconfirmed():
        closes.append(1)
        return SimpleNamespace(state="failed", thread_alive=False,
            error_code="FACTORY_FAILED_CLEANUP_UNPROVEN", unregister_uncertainties=0,
            owner_cleanup_uncertainties=0)
    old.close = close_unconfirmed
    assert value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1").outcome == "UNKNOWN"
    assert len(handles) == 1 and value._slots["strategy-a"].handle is old
    assert not value.close() and closes == [1]


def test_dispatch_queued_is_dispatching_and_other_outcomes_are_exact():
    value, handles = port()
    reply = value.dispatch(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                           market_ref={"key": {}, "epoch": "e", "sequence": 1, "message_id": "m"})
    assert (reply.outcome, reply.state, reply.reservation_id) == ("QUEUED", "DISPATCHING", "res-1")
    assert handles[0].client.calls[0][2]["sequence"] == 1
    handles[0].client.dispatch_result = result("UNKNOWN", code="DISPATCH_ALREADY_ATTEMPTED")
    reply = value.dispatch(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                           market_ref=None)
    assert reply.outcome == "UNKNOWN" and reply.code == "DISPATCH_ALREADY_ATTEMPTED"


def test_release_returns_the_verified_terminal_observation_only():
    value, handles = port()
    reply = value.release(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          operation_id="risk-release-1")
    assert (reply.outcome, reply.state, reply.digest) == ("OBSERVED", "RELEASED", "d" * 64)
    client = handles[0].client
    assert [c[0] for c in client.calls] == ["status", "release"]
    client.release_result = SimpleNamespace(outcome="UNKNOWN", state=None, code="X")
    assert value.release(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                         operation_id="r2").outcome == "UNKNOWN"
    # Not RESERVED any more: no release call, the status itself is the answer.
    client.actor_state = "SENT"
    calls = len(client.calls)
    reply = value.release(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          operation_id="r3")
    assert reply.state == "SENT" and len(client.calls) == calls + 1


# ---------------------------------------------------------------------------- control port


class Runtime:
    def __init__(self, replies, registered=True, *, operations=CONTROL_OPERATIONS):
        self.replies, self.calls, self.registered = list(replies), [], registered
        self.operations = operations
        self.generation = "generation-a"
        self.identity_schema_version = 1
        self.token = object()

    def call_bound(self, mount, operation, arguments, **kw):
        if not self.registered:
            from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError

            raise ServiceRuntimeRequestNotSentError()
        route = kw["route"]
        assert route == CONTROL_ROUTE
        assert kw["expected_server_key_fingerprint"] == CONTROL_FINGERPRINT
        observation = BoundCallObservation(
            route.identity,
            "run-a",
            route.scope,
            "registration-a",
            "service-epoch-a",
            mount,
            route.service_id,
            route.instance_id,
            route.scope_ref,
            self.operations,
            route.peer,
            CONTROL_FINGERPRINT,
            100.0,
            True,
            True,
            operation,
        )
        if operation == "direct.control.identity":
            reply = {
                "status": "OBSERVED",
                "schema_version": self.identity_schema_version,
                "account_scope": ACCOUNT,
                "journal_id": "journal-a",
                "journal_generation": self.generation,
            }
        else:
            assert kw["expected_binding"] == replace(observation, operation="direct.control.identity")
            assert kw["_expected_token"] is self.token
            reply = self.replies.pop(0)
        self.calls.append((operation, arguments, kw))
        if isinstance(reply, Exception):
            raise reply
        return BoundCallResult(reply, observation, observation, self.token)


def control(
    replies,
    registered=True,
    *,
    operations=CONTROL_OPERATIONS,
    control_intent_enabled=False,
):
    runtime = Runtime(replies, registered, operations=operations)
    return GatewayControlAdapter(runtime, mount="application.direct_write", service_id="gateway",
                                 instance_id="gateway-a", scope_ref="scope-a",
                                 account_ref=ACCOUNT, timeout_seconds=5, route=CONTROL_ROUTE,
                                 server_key_fingerprint=CONTROL_FINGERPRINT,
                                 control_intent_enabled=control_intent_enabled), runtime


def control_request_digest(
    *, cause_id="c1", mode="STOP_NEW", scope_kind="account", scope_value="", latch_id="latch-a"
):
    from bt_api_risk.pretrade.account_bound_intents import request_digest_for_control

    return request_digest_for_control(
        cause_id=cause_id,
        mode=mode,
        scope_kind=scope_kind,
        scope_value=scope_value,
        latch_id=latch_id,
    )


def test_control_binding_capture_is_owner_bound_and_observational():
    adapter, runtime = control([])
    binding = adapter.capture_intent_binding()
    assert binding is not None
    assert (
        binding.account_scope,
        binding.journal_id,
        binding.owner_generation,
        binding.registration_id,
        binding.service_epoch,
    ) == (ACCOUNT, "journal-a", "generation-a", "registration-a", "service-epoch-a")
    assert [call[0] for call in runtime.calls] == ["direct.control.identity"]


def test_control_intent_apply_is_default_off_after_a_valid_binding_capture():
    adapter, runtime = control([], operations=CONTROL_INTENT_OPERATIONS)
    binding = adapter.capture_intent_binding()
    assert binding is not None
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=control_request_digest(),
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.code) == (
        "TRANSPORT_NOT_STARTED",
        "CONTROL_INTENT_UNAVAILABLE",
    )
    assert [call[0] for call in runtime.calls] == ["direct.control.identity"]


def test_control_intent_enable_flag_requires_an_exact_bool():
    with pytest.raises(ValueError, match="RISK_CONTROL_INTENT_CONFIG_INVALID"):
        control([], control_intent_enabled=1)


def test_control_intent_binding_rejects_boolean_identity_schema_version():
    adapter, runtime = control([], operations=CONTROL_INTENT_OPERATIONS)
    runtime.identity_schema_version = True
    assert adapter.capture_intent_binding() is None
    assert [call[0] for call in runtime.calls] == ["direct.control.identity"]


def test_control_intent_apply_uses_fresh_exact_binding_and_wire_fields():
    adapter, runtime = control(
        [
            {"status": "OBSERVED", "cause_id": "c1", "version": 1, "active": True},
        ],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    assert binding is not None
    request_digest = control_request_digest()
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=request_digest,
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.cause_id, reply.active, reply.version) == (
        "OBSERVED",
        "c1",
        True,
        1,
    )
    unbound_status = adapter.status(cause_id="c1")
    assert (unbound_status.outcome, unbound_status.code) == (
        "UNKNOWN",
        "CONTROL_INTENT_BINDING_REQUIRED",
    )
    assert [call[0] for call in runtime.calls] == [
        "direct.control.identity",
        "direct.control.identity",
        CONTROL_INTENT_OPERATION,
    ]
    arguments = runtime.calls[2][1]
    assert set(arguments) == {
        "schema_version",
        "cause_id",
        "mode",
        "scope_kind",
        "scope_value",
        "latch_id",
        "request_digest",
        "binding_digest",
        "expected_journal_id",
        "expected_generation",
    }
    assert arguments == {
        "schema_version": 1,
        "cause_id": "c1",
        "mode": "STOP_NEW",
        "scope_kind": "account",
        "scope_value": "",
        "latch_id": "latch-a",
        "request_digest": request_digest,
        "binding_digest": binding.digest,
        "expected_journal_id": "journal-a",
        "expected_generation": "generation-a",
    }


def test_control_intent_apply_rejects_stale_binding_before_the_intent_call():
    adapter, runtime = control(
        [],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    assert binding is not None
    runtime.generation = "generation-b"
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=control_request_digest(),
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.code) == (
        "TRANSPORT_NOT_STARTED",
        "CONTROL_INTENT_BINDING_MISMATCH",
    )
    assert [call[0] for call in runtime.calls] == [
        "direct.control.identity",
        "direct.control.identity",
    ]


def test_control_intent_apply_rejects_unpersisted_request_or_missing_operation():
    adapter, runtime = control(
        [],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    assert binding is not None
    mismatch = adapter.apply_intent(
        binding=binding,
        request_digest="0" * 64,
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (mismatch.outcome, mismatch.code) == (
        "REQUEST_DENIED",
        "CONTROL_INTENT_REQUEST_DIGEST_MISMATCH",
    )
    assert [call[0] for call in runtime.calls] == ["direct.control.identity"]

    old_adapter, old_runtime = control(
        [], operations=CONTROL_OPERATIONS, control_intent_enabled=True
    )
    old_binding = old_adapter.capture_intent_binding()
    unavailable = old_adapter.apply_intent(
        binding=old_binding,
        request_digest=control_request_digest(),
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (unavailable.outcome, unavailable.code) == (
        "TRANSPORT_NOT_STARTED",
        "CONTROL_INTENT_UNAVAILABLE",
    )
    assert [call[0] for call in old_runtime.calls] == ["direct.control.identity"]


def test_control_intent_apply_rejects_invalid_digest_shape_without_a_fresh_call():
    adapter, runtime = control(
        [],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=True,
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.code) == ("REQUEST_DENIED", "INVALID_REQUEST")
    assert [call[0] for call in runtime.calls] == ["direct.control.identity"]


def test_control_intent_apply_maps_unexpected_digest_failure_to_unknown(monkeypatch):
    from bt_api_risk.pretrade import account_bound_intents

    adapter, runtime = control(
        [],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()

    def fail_unexpectedly(**_kwargs):
        raise RuntimeError("unexpected digest implementation failure")

    monkeypatch.setattr(
        account_bound_intents,
        "request_digest_for_control",
        fail_unexpectedly,
    )
    reply = adapter.apply_intent(
        binding=binding,
        request_digest="0" * 64,
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.code) == (
        "UNKNOWN",
        "INVALID_OR_UNAVAILABLE_REPLY",
    )
    assert [call[0] for call in runtime.calls] == ["direct.control.identity"]


@pytest.mark.parametrize("wire_code", sorted(CONTROL_INTENT_REJECTION_CODES))
def test_control_intent_apply_maps_every_declared_rejection(wire_code):
    adapter, _runtime = control(
        [{"status": "REQUEST_REJECTED", "code": wire_code}],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=control_request_digest(),
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.code) == ("REQUEST_DENIED", wire_code)


@pytest.mark.parametrize(
    "wire_reply",
    [
        {"status": "UNKNOWN", "cause_id": "c1"},
        RuntimeError("uncertain transport result"),
    ],
)
def test_control_intent_apply_keeps_unknown_unknown_without_retry(wire_reply):
    adapter, runtime = control(
        [wire_reply],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=control_request_digest(),
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert reply.outcome == "UNKNOWN"
    assert [call[0] for call in runtime.calls] == [
        "direct.control.identity",
        "direct.control.identity",
        CONTROL_INTENT_OPERATION,
    ]


@pytest.mark.parametrize(
    "wire_reply,outcome,code",
    [
        (
            {"status": "REQUEST_REJECTED", "code": "CONTROL_INTENT_HISTORY_GAP"},
            "REQUEST_DENIED",
            "CONTROL_INTENT_HISTORY_GAP",
        ),
        (
            {"status": "REQUEST_REJECTED", "code": "UNDECLARED_ERROR"},
            "UNKNOWN",
            "INVALID_OR_UNAVAILABLE_REPLY",
        ),
        (
            {"status": "OBSERVED", "cause_id": "c1", "version": True, "active": True},
            "UNKNOWN",
            "INVALID_OR_UNAVAILABLE_REPLY",
        ),
        (
            {"status": "UNKNOWN"},
            "UNKNOWN",
            "INVALID_OR_UNAVAILABLE_REPLY",
        ),
        (
            {"status": "UNKNOWN", "cause_id": "other"},
            "UNKNOWN",
            "INVALID_OR_UNAVAILABLE_REPLY",
        ),
    ],
)
def test_control_intent_apply_maps_only_declared_finite_replies(wire_reply, outcome, code):
    adapter, _runtime = control(
        [wire_reply],
        operations=CONTROL_INTENT_OPERATIONS,
        control_intent_enabled=True,
    )
    binding = adapter.capture_intent_binding()
    reply = adapter.apply_intent(
        binding=binding,
        request_digest=control_request_digest(),
        cause_id="c1",
        mode="STOP_NEW",
        scope_kind="account",
        scope_value="",
        latch_id="latch-a",
    )
    assert (reply.outcome, reply.code) == (outcome, code)


def test_control_apply_and_status_reuse_the_original_owner_binding():
    adapter, runtime = control(
        [
            {"status": "OBSERVED", "cause_id": "c1", "version": 1, "active": True},
            {"status": "NOT_FOUND", "cause_id": "c1"},
        ]
    )
    applied = adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account", scope_value="")
    absent = adapter.status(cause_id="c1")
    assert applied.outcome == "OBSERVED" and absent.outcome == "OBSERVED"
    assert [call[0] for call in runtime.calls] == [
        "direct.control.identity", "direct.control.apply", "direct.control.status"]
    for _operation, arguments, _kwargs in runtime.calls[1:]:
        assert arguments["expected_journal_id"] == "journal-a"
        assert arguments["expected_generation"] == "generation-a"


def test_control_status_without_the_original_binding_fails_closed():
    adapter, runtime = control([])
    reply = adapter.status(cause_id="old-cause")
    assert reply.outcome == "UNKNOWN" and reply.code == "CONTROL_INTENT_BINDING_REQUIRED"
    assert runtime.calls == []


def test_control_cancel_status_without_the_original_binding_fails_closed():
    adapter, runtime = control([])
    assert adapter.cancel_scope_status("old-cancel") is None
    assert runtime.calls == []


def test_control_cancel_and_status_reuse_the_original_owner_binding():
    counts = dict.fromkeys(("QUEUED", "SENT", "NOT_SENT", "UNKNOWN", "TERMINAL"), 0)
    adapter, runtime = control(
        [
            {
                "status": "OBSERVED",
                "operation_id": "cancel-1",
                "scope_kind": "account",
                "scope_value": "",
                "issued": 0,
                "counts": counts,
            },
            {"status": "NOT_FOUND", "operation_id": "cancel-1"},
        ]
    )
    cancelled = adapter.cancel_scope(
        operation_id="cancel-1", scope_kind="account", scope_value=""
    )
    status = adapter.cancel_scope_status("cancel-1")
    assert cancelled.outcome == "OBSERVED"
    assert status == {"status": "NOT_FOUND", "operation_id": "cancel-1"}
    assert [call[0] for call in runtime.calls] == [
        "direct.control.identity",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    ]
    for _operation, arguments, _kwargs in runtime.calls[1:]:
        assert arguments["expected_journal_id"] == "journal-a"
        assert arguments["expected_generation"] == "generation-a"


def test_control_apply_instrument_scope_is_refused_locally():
    adapter, runtime = control([])
    reply = adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="instrument",
                          scope_value="SHFE:rb")
    assert (reply.outcome, reply.code) == ("REQUEST_DENIED", "RISK_CONTROL_SCOPE_UNSUPPORTED")
    assert runtime.calls == []


def test_control_strategy_scope_is_refused_locally_and_never_sent():
    adapter, runtime = control([])
    reply = adapter.apply(cause_id="c1", mode="FREEZE", scope_kind="strategy",
                          scope_value="strategy-a")
    assert reply.outcome == "REQUEST_DENIED" and reply.code == "RISK_CONTROL_SCOPE_UNSUPPORTED"
    assert adapter.cancel_scope(operation_id="o", scope_kind="strategy",
                                scope_value="strategy-a").outcome == "REQUEST_DENIED"
    assert runtime.calls == []


def test_control_refusals_unknowns_and_not_registered():
    adapter, _ = control([{"status": "REQUEST_REJECTED", "code": "SCOPE_DENIED"},
                          {"status": "UNKNOWN"}, RuntimeError("lost"),
                          {"status": "NOT_FOUND", "cause_id": "c1"}])
    assert adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account",
                         scope_value="").code == "SCOPE_DENIED"
    assert adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account",
                         scope_value="").outcome == "UNKNOWN"
    assert adapter.status(cause_id="c1").outcome == "UNKNOWN"
    absent = adapter.status(cause_id="c1")
    assert absent.outcome == "OBSERVED" and absent.active is False
    unregistered, runtime = control([], registered=False)
    assert unregistered.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account",
                              scope_value="").outcome == "TRANSPORT_NOT_STARTED"
    assert runtime.calls == []


# ---------------------------------------------------------------------------- facts


def binding():
    return RiskInstrumentBinding("rb", RB, Decimal(10), Decimal(1))


def test_facts_cache_instrument_facts_staleness_and_trading_day():
    now = [1_000]
    facts = RiskFactsCache({"rb": binding()}, now_ns=lambda: now[0], account_max_age_ns=100,
                           financial_max_age_ns=100)
    assert facts.instrument_facts(RB).multiplier == Decimal(10)
    other = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb2")
    assert facts.instrument_facts(other) is None
    assert facts.trading_day() is None
    facts.note_event_day("20300101")
    facts.note_tick("rb", None, None, "20291231")
    assert facts.trading_day() == "20300101"
    facts.note_tick("rb", None, None, "20300102")
    assert facts.trading_day() == "20300102"
    from bt_api_risk.pretrade import AccountSnapshot, StrategyFinancialFact

    account = AccountSnapshot(complete=True, positions={}, as_of_ns=1000, valid_until_ns=1050)
    financial = StrategyFinancialFact("s", "20300101", Decimal(1), 1000, True,
                                      valid_until_ns=1050)
    facts.set_account(account)
    facts.set_strategy_financials({"s": financial})
    assert facts.account_snapshot().complete is True
    assert facts.strategy_financial("s").complete is True
    now[0] += 101
    assert facts.account_snapshot().complete is False
    assert facts.strategy_financial("s").complete is False
    # Receiving the same expired source again must not renew its lease.
    facts.set_account(account)
    facts.set_strategy_financials({"s": financial})
    assert facts.account_snapshot().complete is False
    assert facts.strategy_financial("s").complete is False


def test_strategy_financial_mapping_fails_closed():
    reply = {"status": "OBSERVED", "schema_version": 1, "account_scope": ACCOUNT,
             "owner_generation": "g", "complete": True, "strategies": [
                 {"strategy": "s1", "currency": "CNY", "status": "OBSERVED", "value": "100.5",
                  "cash": "1"},
                 {"strategy": "s2", "currency": "CNY", "status": "UNAVAILABLE"},
                 {"strategy": "s3", "currency": "CNY", "status": "OBSERVED", "value": "1"},
                 {"strategy": "s3", "currency": "USD", "status": "OBSERVED", "value": "2"}]}
    values = strategy_financials_from_reply(reply, account_scope=ACCOUNT,
                                            trading_day="20300101", now_ns=5)
    assert set(values) == {"s1"}
    fact = values["s1"]
    assert (fact.value, fact.trading_day, fact.complete, fact.day_start_value) == (
        Decimal("100.5"), "20300101", False, None)
    assert fact.as_of_ns is None and fact.valid_until_ns is None
    # A current valuation is not proof of the day's opening value or high-water.
    assert fact.day_high_water_value is None
    assert not strategy_financials_from_reply(dict(reply, complete=False), account_scope=ACCOUNT,
                                              trading_day="20300101", now_ns=5)["s1"].complete
    assert not strategy_financials_from_reply(reply, account_scope=ACCOUNT, trading_day=None,
                                              now_ns=5)["s1"].complete
    with pytest.raises(ValueError):
        strategy_financials_from_reply(dict(reply, account_scope="other"), account_scope=ACCOUNT,
                                       trading_day="20300101", now_ns=5)


def test_account_snapshot_mapping_marks_unprovable_rows_incomplete():
    def position(side, quantity, today=None, yesterday=None, venue="SHFE"):
        instrument = MarketInstrumentKey("ctp", venue, "simnow", "future", "rb", "rb")
        return SimpleNamespace(side=side, quantity=quantity, today_quantity=today,
                               yesterday_quantity=yesterday, instrument=instrument, qty_unit="contract")

    def observation(*positions):
        return SimpleNamespace(
            positions_complete=True, balances_complete=True, positions=positions,
            balances=(SimpleNamespace(total="100", available="90", currency="CNY"),),
            valuation=None, completed_at_utc="2030-01-01T01:00:00Z")

    ids = {RB: "rb"}
    full = account_snapshot_from_observation(observation(position("long", "3", "1", "2")),
                                             instrument_ids=ids, dated_venues=frozenset({"SHFE"}))
    assert not full.complete and full.positions[("rb", "LONG")] == (Decimal(1), Decimal(2))
    assert full.valid_until_ns is None
    assert full.balance == Decimal(100) and full.available == Decimal(90)
    assert full.margin is None and full.close_profit is None and full.trading_day is None
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        account_snapshot_from_observation(observation(position("long", "3")),
                                          instrument_ids=ids, dated_venues=frozenset({"SHFE"}))
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        account_snapshot_from_observation(observation(position("net", "3", "1", "2")),
                                          instrument_ids=ids, dated_venues=frozenset())


@pytest.mark.parametrize(
    ("valuation_available", "valuation_currency", "balance", "available", "currency"),
    [
        ("0", "CNY", Decimal(100), Decimal(0), "CNY"),
        (None, "CNY", Decimal(100), Decimal(90), "CNY"),
        ("invalid", "CNY", Decimal(100), None, "CNY"),
        (None, "USD", None, None, "USD"),
        ("7", "USD", None, Decimal(7), "USD"),
        ("7", None, None, None, None),
    ],
)
def test_account_snapshot_keeps_zero_and_never_mixes_currencies(
    valuation_available, valuation_currency, balance, available, currency
):
    observation = SimpleNamespace(
        positions_complete=True, balances_complete=True, positions=(),
        balances=(SimpleNamespace(total="100", available="90", currency="CNY"),),
        valuation=SimpleNamespace(available_cash=valuation_available, equity="120",
                                  currency=valuation_currency),
        completed_at_utc="2030-01-01T01:00:00Z",
    )
    result = account_snapshot_from_observation(
        observation, instrument_ids={}, dated_venues=frozenset()
    )
    assert (result.balance, result.available, result.currency) == (balance, available, currency)
    assert result.equity == (None if valuation_currency is None else Decimal(120))


def test_market_event_maps_to_price_and_own_market_reference(monkeypatch):
    import bt_api_base.market_tick_protocol as ticks

    key = MarketSubscriptionKey("src", "ent", RB, "tick")
    tick = SimpleNamespace(instrument=RB, last_price=3601.0, provider_event_time_utc=
                           "2030-01-01T01:00:00Z", quality_contract_ref="q",
                           continuity_status="continuous", trading_date="20300101")
    monkeypatch.setattr(ticks.MarketTickPayload, "from_dict", classmethod(lambda cls, raw: tick))
    event = SimpleNamespace(payload={"key": key.to_dict(), "data": {}},
                            header=SimpleNamespace(stream_epoch="ep", sequence=7, message_id="m7"))
    received = SimpleNamespace(event=event, receipt=SimpleNamespace(received_wall_ns=99))
    fact, ref, day = price_fact_from_market_event(received, key)
    assert fact.last_price == Decimal("3601.0") and fact.quality_ok is True
    assert fact.received_time_ns == 99 and day == "20300101"
    assert ref == {"key": key.to_dict(), "epoch": "ep", "sequence": 7, "message_id": "m7"}
    foreign = SimpleNamespace(event=SimpleNamespace(payload={"key": {}, "data": {}},
                                                    header=event.header), receipt=None)
    with pytest.raises(ValueError):
        price_fact_from_market_event(foreign, key)


# ---------------------------------------------------------------------------- observation pump


def account_event(sequence, *, principal="other", key="k", status="accepted", day="20300101",
                  side="buy", quantity="1"):
    observation = GatewayExecutionObservation(
        account_scope=ACCOUNT, route="CTP___FUTURE",
        trading_day=day, instrument=RB,
        kind=DirectExecutionEventKind.ORDER, side=side, offset="open", order_quantity=quantity,
        filled_quantity="0", limit_price="10", status=DirectOrderStatus(status), trade_quantity=None,
        trade_price=None, trade_key_digest=None, venue_key_digest=None)
    event = GatewayExecutionStreamEvent(sequence=sequence, request_key=key,
                                        reservation_id="r" + str(sequence),
                                        command_principal=principal, command_digest="a" * 64,
                                        fact_digest="b" * 64, observation=observation)
    return GatewayAccountObservedEvent(strategy_scope="strategy-a", event=event)


def page(events, *, epoch=EPOCH, has_more=False):
    progress = SimpleNamespace(epoch=epoch, offered_sequence=events[-1].event.sequence if events else 0,
                               acknowledged_sequence=None)
    return SimpleNamespace(outcome="OBSERVED", code=None,
                           page=SimpleNamespace(progress=progress, events=tuple(events),
                                                has_more=has_more))


class Observer:
    def __init__(self, pages, offered=0, epoch=EPOCH, acknowledged=0):
        self.pages, self.reads, self.acks = list(pages), [], []
        self.offered, self.epoch, self.acknowledged = offered, epoch, acknowledged

    def progress(self, *, timeout_seconds):
        return SimpleNamespace(outcome="OBSERVED", code=None, progress=SimpleNamespace(
            epoch=self.epoch, offered_sequence=self.offered, acknowledged_sequence=self.acknowledged))

    def read_after(self, **kw):
        self.reads.append(kw)
        result = self.pages.pop(0)
        progress = getattr(getattr(result, "page", None), "progress", None)
        if progress is not None and getattr(progress, "acknowledged_sequence", None) is None:
            progress.acknowledged_sequence = self.acknowledged
        offered = getattr(progress, "offered_sequence", None)
        if type(offered) is int and offered >= 0:
            self.offered = max(self.offered, offered)
        return result

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
        self.acks.append(sequence)
        self.acknowledged = max(self.acknowledged, sequence)
        return SimpleNamespace(outcome="OBSERVED", progress=SimpleNamespace(
            epoch=expected_epoch, acknowledged_sequence=self.acknowledged,
            offered_sequence=max(self.offered, self.acknowledged),
        ))


class Engine:
    def __init__(self, fail=False):
        self.applied, self.resets, self.gaps, self.fail = [], [], [], fail
        self.last = -1
        self.epoch, self.revision, self.valid = None, 0, False
        self.seen = set()

    def reset_observations(self, epoch, head):
        self.resets.append((epoch, head))
        self.last = -1
        self.epoch, self.valid = epoch, True
        self.revision += 1

    def reset_observations_bound(self, epoch, head):
        from bt_api_risk.pretrade.engine import ObservationApplyResult

        self.reset_observations(epoch, head)
        return ObservationApplyResult(epoch, -1, 0, 0, False, str(self.revision))

    def mark_observation_gap(self, reason):
        self.gaps.append(reason)
        self.valid = False

    def apply_observations(self, events):
        if self.fail:
            raise RuntimeError("storage lost")
        self.applied.extend(events)
        self.last = max([self.last] + [e.sequence for e in events])
        return self.last

    def apply_observations_bound(self, events):
        from bt_api_risk.pretrade.engine import ObservationApplyResult

        unseen = {(event.epoch, event.sequence) for event in events} - self.seen
        last = self.apply_observations(events)
        self.seen.update(unseen)
        self.revision += 1
        return ObservationApplyResult(self.epoch, last,
                                      len(unseen), len(events), False,
                                      str(self.revision))

    def observation_cursor_matches(self, epoch, sequence, revision):
        return self.valid and (epoch, sequence, revision) == (self.epoch, self.last, str(self.revision))


def pump(engine, observer, facts=None):
    return ObservationPump(engine, observer, facts or RiskFactsCache({}),
                           risk_principal=RISK.principal_ref,
                           intent_payload=lambda key: None, timeout_seconds=5)


def test_pump_resets_maps_sequences_and_acks_only_what_was_applied():
    engine, facts = Engine(), RiskFactsCache({})
    observer = Observer([page([account_event(1, principal=RISK.principal_ref, key="mine"),
                               account_event(2)], has_more=True), page([account_event(3)])],
                        offered=2)
    value = pump(engine, observer, facts)
    assert value.poll() == 3
    assert engine.resets == [(EPOCH, 1)]  # head = offered - 1 (engine counts from 0)
    assert [e.sequence for e in engine.applied] == [0, 1, 2]
    assert engine.applied[0].origin == "risk:" + RISK.principal_ref
    assert engine.applied[1].origin == "self" and engine.applied[1].side == "BUY"
    assert observer.acks == [2, 3] and observer.reads[1]["after_sequence"] == 2
    assert facts.trading_day() == "20300101" and value.verified_epoch == EPOCH


def test_a_restarted_pump_marks_the_persisted_projection_unverified_before_replay():
    engine = Engine()
    observer = Observer([], offered=0)
    observer.progress = lambda **kw: SimpleNamespace(outcome="UNKNOWN", code="LOST", progress=None)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert engine.gaps == ["observation_restart"] and engine.resets == []
    value.poll()
    assert engine.gaps == ["observation_restart"]  # once per process


def test_pump_never_acks_when_the_durable_apply_fails():
    engine, observer = Engine(fail=True), Observer([page([account_event(1)])])
    value = pump(engine, observer)
    with pytest.raises(RuntimeError):
        value.poll()
    assert observer.acks == []


def test_pump_epoch_change_marks_a_gap_and_replays_from_zero():
    engine = Engine()
    observer = Observer([page([account_event(1)]), page([account_event(2)], epoch="f" * 64)])
    value = pump(engine, observer)
    value.poll()
    observer.epoch = "f" * 64
    observer.acknowledged = 0
    observer.offered = 0
    value.poll()
    assert engine.gaps == ["observation_restart", "epoch_changed"]
    assert value.verified_epoch is None
    observer.pages.append(page([account_event(1)], epoch="f" * 64))
    value.poll()
    # The rejected new-epoch page already exposed an offered lower bound of 2.
    assert engine.resets[-1] == ("f" * 64, 1) and observer.reads[-1]["after_sequence"] == 0


def test_replaced_observer_marks_gap_and_replays_without_implicit_ack():
    engine = Engine()
    original = Observer([page([account_event(1)])])
    value = pump(engine, original)
    value.poll()
    replacement = Observer([page([account_event(1)])], epoch=EPOCH, offered=1, acknowledged=1)
    value.replace_observer(replacement)
    assert value.verified_epoch is None
    assert engine.gaps == ["observation_restart", "observation_reconnect"]
    assert replacement.acks == []
    value.poll()
    assert replacement.reads[0]["after_sequence"] == 0
    assert replacement.acks == [1]


@pytest.mark.parametrize("reply, reason", [
    (SimpleNamespace(outcome="REQUEST_DENIED", code="CONSUMER_REPLAY_REQUIRED", page=None),
     "observation_replay_required"),
    (SimpleNamespace(outcome="UNKNOWN", code=None, page=None), "observation_read_failed"),
])
def test_pump_read_failures_mark_the_projection_unverified(reply, reason):
    engine = Engine()
    value = pump(engine, Observer([page([account_event(1)]), reply]))
    value.poll()
    value.poll()
    assert engine.gaps == ["observation_restart", reason] and value.verified_epoch is None


def test_pump_unmappable_event_applies_the_prefix_and_marks_a_gap():
    engine = Engine()
    observer = Observer([page([account_event(1), account_event(2, side=None), account_event(3)])])
    value = pump(engine, observer)
    assert value.poll() == 1
    assert [e.sequence for e in engine.applied] == [0]
    assert observer.acks == [1]
    assert engine.gaps == ["observation_restart", "observation_unmappable"]


def test_own_event_without_economics_uses_risks_own_durable_intent_only():
    payload = {"side": "buy", "offset": "open", "quantity": "2", "price": "10"}
    item = account_event(1, principal=RISK.principal_ref, side=None, quantity=None)
    event = observed_event_from_account_event(item, epoch=EPOCH, risk_principal=RISK.principal_ref,
                                              intent_payload=lambda key: payload)
    assert (event.side, event.quantity, event.offset) == ("BUY", Decimal(2), "OPEN")
    from bt_api_risk.pretrade import RiskFactError

    with pytest.raises(RiskFactError):
        observed_event_from_account_event(account_event(1, side=None), epoch=EPOCH,
                                          risk_principal=RISK.principal_ref,
                                          intent_payload=lambda key: payload)


def test_cancel_error_keeps_the_order_working():
    item = account_event(1)
    object.__setattr__(item.event.observation, "kind", DirectExecutionEventKind.CANCEL_ERROR)
    object.__setattr__(item.event.observation, "status", None)
    event = observed_event_from_account_event(item, epoch=EPOCH, risk_principal="x",
                                              intent_payload=lambda key: None)
    assert event.kind == "cancel_error" and event.status == "accepted"
    assert json.dumps(event.order_identity)
