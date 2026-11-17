"""Facts strict binding contract using synthetic owner observations, no service I/O."""

import copy
import pickle
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.service_runtime_thread import (
    BoundCallObservation,
    BoundCallResult,
    _BoundBindingToken,
)
from tests.unit.test_iter63_account_facts_client import _client

PEER = WireIdentity(WireRole.SERVICE, "gateway", "gateway-service")
FINGERPRINT = "f" * 64
EPOCH = "a" * 64


@pytest.fixture
def strict(monkeypatch):
    legacy, runtime, snap, binding, base, event = _client(monkeypatch)
    client = GatewayAccountFactsClient(
        runtime, route=legacy._route, peer=PEER, fingerprint=FINGERPRINT
    )
    calls = []

    def call(_mount, operation, arguments, **kwargs):
        calls.append((operation, arguments, kwargs))
        route = client._route
        observation = BoundCallObservation(
            route.identity,
            "run",
            route.scope,
            "registration",
            "epoch",
            route.mount,
            route.service_id,
            route.instance_id,
            route.scope_ref,
            binding.operations,
            PEER,
            FINGERPRINT,
            1000.0,
            True,
            True,
            operation,
        )
        if operation == "account.facts.read_after":
            wire = {"status": "OK", "page": {**base, "events": [event], "has_more": False}}
        else:
            wire = {
                "status": "OK",
                "progress": {**base, "acknowledged_sequence": arguments["sequence"]},
            }
        proof = _BoundBindingToken(object(), object(), object(), object(), (), (), observation)
        return BoundCallResult(wire, observation, observation, proof)

    monkeypatch.setattr(runtime, "call", lambda *_a, **_k: pytest.fail("strict path used raw call"))
    monkeypatch.setattr(runtime, "call_bound", call)
    return client, runtime, calls, snap, binding, base, event


def test_authenticated_exact_page_token_and_single_ack(strict):
    client, _, calls, *_ = strict
    page = client.read_after()
    assert page.outcome == "OBSERVED" and page._read_token is not None
    assert calls[0][2]["expected_server_key_fingerprint"] == FINGERPRINT
    for copier in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            copier(page._read_token)
    assert (
        client.acknowledge(0, expected_epoch=EPOCH, read_token=page._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    assert (
        client.acknowledge(1, expected_epoch="b" * 64, read_token=page._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=object()).code
        == "READ_BINDING_REQUIRED"
    )
    assert len(calls) == 1
    result = client.acknowledge(1, expected_epoch=EPOCH, read_token=page._read_token)
    assert result.outcome == "OBSERVED"
    assert calls[-1][2]["expected_binding"].operation == "account.facts.read_after"
    assert type(calls[-1][2]["_expected_token"]) is _BoundBindingToken
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=page._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    assert len(calls) == 2


def test_new_or_failed_read_invalidates_prior_token(strict):
    client, runtime, calls, *_ = strict
    page = client.read_after()
    first = page._read_token
    second = client.read_after()._read_token
    assert first is not second
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=first).code
        == "READ_BINDING_REQUIRED"
    )
    runtime.call_bound = lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError())
    assert client.read_after().outcome == "UNKNOWN"
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=second).code
        == "READ_BINDING_REQUIRED"
    )
    assert len(calls) == 2


def test_unknown_ack_is_not_retried(strict):
    client, runtime, calls, *_ = strict
    page = client.read_after()
    runtime.call_bound = lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError())
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=page._read_token).outcome
        == "UNKNOWN"
    )
    runtime.call_bound = lambda *_a, **_k: pytest.fail("ambiguous ACK was retried")
    assert (
        client.acknowledge(1, expected_epoch=EPOCH, read_token=page._read_token).code
        == "READ_BINDING_REQUIRED"
    )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("peer", WireIdentity(WireRole.SERVICE, "other", "other")),
        ("fingerprint", "b" * 64),
        ("registration_id", "other"),
        ("service_epoch", "other"),
        ("operation", "wrong"),
        ("active", False),
    ],
)
def test_wrong_authenticated_evidence_never_issues_token(strict, field, value):
    client, runtime, _, *_ = strict
    original = runtime.call_bound

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        before = replace(result.before, **{field: value})
        return replace(result, before=before, after=before)

    runtime.call_bound = changed
    result = client.read_after()
    assert result.outcome == "UNKNOWN" and result._read_token is None


def test_invalid_page_or_changed_registration_cannot_issue_token(strict, monkeypatch):
    client, runtime, _, snap, binding, _, event = strict
    event["sequence"] = 2
    assert client.read_after().outcome == "UNKNOWN"
    event["sequence"] = 1
    original = runtime.call_bound

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(
            runtime,
            "status",
            lambda: replace(snap, bindings=(replace(binding, registration_id="other"),)),
        )
        return result

    runtime.call_bound = changed
    assert client.read_after().outcome == "UNKNOWN"


def test_changed_binding_during_call_or_missing_owner_proof_is_rejected(strict):
    client, runtime, _, *_ = strict
    original = runtime.call_bound

    def changed_after(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(
            result, after=replace(result.after, lease_deadline=result.after.lease_deadline + 1)
        )

    runtime.call_bound = changed_after
    assert client.read_after().outcome == "UNKNOWN"
    runtime.call_bound = lambda *args, **kwargs: replace(
        original(*args, **kwargs), _binding_token=None
    )
    assert client.read_after().outcome == "UNKNOWN"


def test_legacy_has_no_strict_token_and_partial_auth_configuration_refused(monkeypatch):
    legacy, runtime, _, _, base, event = _client(monkeypatch)
    runtime.call = lambda *_a, **_kw: {
        "status": "OK",
        "page": {**base, "events": [event], "has_more": False},
    }
    assert legacy.read_after()._read_token is None
    with pytest.raises(ValueError):
        GatewayAccountFactsClient(runtime, route=legacy._route, peer=PEER)
    with pytest.raises(ValueError):
        GatewayAccountFactsClient(runtime, route=legacy._route, fingerprint=FINGERPRINT)


@pytest.mark.parametrize(
    "operation",
    [
        "account.facts.read_after",
        "account.facts.snapshot_confirm",
        "account.facts.status",
        "account.facts.progress",
        "account.facts.snapshot_info",
        "account.facts.snapshot_read",
    ],
)
def test_owner_rejects_expected_fingerprint_before_rpc(monkeypatch, operation):
    from bt_api_py import service_runtime_thread as thread
    from tests.unit.test_iter64_cancel_ack_binding_pure import PEER as OWNER_PEER
    from tests.unit.test_iter64_cancel_ack_binding_pure import raw as raw_fixture

    owner = raw_fixture.__wrapped__(monkeypatch)
    owner._bindings[0] = replace(owner._bindings[0], operations=frozenset({operation}))
    route = thread.BoundCallRoute(
        owner.identity, owner._bindings[0].scope, "scope", "service", "instance", OWNER_PEER
    )

    def call(expected):
        return thread._BoundCall(
            "upstream.account",
            operation,
            {},
            {
                "account_ref": "account",
                "portfolio_ref": None,
                "scope_ref": "scope",
                "max_reply_message_bytes": None,
            },
            route,
            expected_server_key_fingerprint=expected,
        )

    with pytest.raises(thread.ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        thread._execute_bound_call(owner, call("b" * 64))
    assert owner.sent == []
    with pytest.raises(thread.ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        thread._execute_bound_call(owner, call("a" * 64))
    assert owner.sent == []  # Synthetic transport has no strict registration proof.
    monkeypatch.setattr(thread, "_facts_stream_lineage", lambda *_: (object(), object(), object()))
    allowed = call("a" * 64)
    thread._execute_bound_call(owner, allowed)
    assert len(owner.sent) == 1
    owner.sent.clear()
    tampered = call("a" * 64)
    tampered.expected_server_key_fingerprint = "b" * 64
    with pytest.raises(thread.ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        thread._execute_bound_call(owner, tampered)
    assert owner.sent == []


def test_owner_strict_ack_without_read_binding_is_not_sent(monkeypatch):
    from bt_api_py import service_runtime_thread as thread
    from tests.unit.test_iter64_cancel_ack_binding_pure import PEER as OWNER_PEER
    from tests.unit.test_iter64_cancel_ack_binding_pure import raw as raw_fixture

    owner = raw_fixture.__wrapped__(monkeypatch)
    operation = "account.facts.ack"
    owner._bindings[0] = replace(owner._bindings[0], operations=frozenset({operation}))
    route = thread.BoundCallRoute(
        owner.identity, owner._bindings[0].scope, "scope", "service", "instance", OWNER_PEER
    )
    call = thread._BoundCall(
        "upstream.account",
        operation,
        {},
        {
            "account_ref": "account",
            "portfolio_ref": None,
            "scope_ref": "scope",
            "max_reply_message_bytes": None,
        },
        route,
        expected_server_key_fingerprint="a" * 64,
    )
    monkeypatch.setattr(thread, "_facts_stream_lineage", lambda *_: (object(), object(), object()))
    with pytest.raises(thread.ThreadedRuntimeError, match="BOUND_ROUTE_UNAVAILABLE"):
        thread._execute_bound_call(owner, call)
    assert owner.sent == []


def test_changed_registration_during_owner_call_never_publishes_observation(monkeypatch):
    from bt_api_py import service_runtime_thread as thread
    from tests.unit.test_iter64_cancel_ack_binding_pure import PEER as OWNER_PEER
    from tests.unit.test_iter64_cancel_ack_binding_pure import raw as raw_fixture

    owner = raw_fixture.__wrapped__(monkeypatch)
    operation = "account.facts.read_after"
    owner._bindings[0] = replace(owner._bindings[0], operations=frozenset({operation}))
    route = thread.BoundCallRoute(
        owner.identity, owner._bindings[0].scope, "scope", "service", "instance", OWNER_PEER
    )
    call = thread._BoundCall(
        "upstream.account",
        operation,
        {},
        {
            "account_ref": "account",
            "portfolio_ref": None,
            "scope_ref": "scope",
            "max_reply_message_bytes": None,
        },
        route,
        expected_server_key_fingerprint="a" * 64,
    )

    def replace_after_send(*_args, **_kwargs):
        owner.sent.append("sent")
        owner._bindings[0] = replace(owner._bindings[0], registration_id="other-registration")
        return {"status": "OK"}

    monkeypatch.setattr(owner, "_rpc", replace_after_send)
    monkeypatch.setattr(thread, "_facts_stream_lineage", lambda *_: (object(), object(), object()))
    outcome = thread._execute_bound_call(owner, call)
    with pytest.raises(thread.ThreadedRuntimeError, match="BOUND_OUTCOME_UNKNOWN"):
        thread._finish_bound_call(owner, call, outcome)
    assert owner.sent == ["sent"]
