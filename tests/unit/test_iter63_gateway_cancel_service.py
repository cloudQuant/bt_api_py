"""Synthetic service projection and admission tests, without provider I/O."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import _identity

from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_direct_service import (
    CANCEL_OPERATIONS,
    SUPPORTED_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    validate_service_options,
)
from bt_api_py.service_registry import ServiceDispatchAdmission, ServiceResultTooLargeError
from tests.unit.test_iter63_gateway_direct_service import _context, _order


@pytest.fixture
def harness(tmp_path, monkeypatch):
    journal = GatewayDirectCommandJournal.create(tmp_path / "db", "account-a", generation="g1")
    order = journal.reserve("strategy-a", "key-1", "principal-a", _order())
    native = NativeOrderIdentity("TEST", "client-a", {"trading_day": "20300101", "front_id": 1}, 1)
    order = journal.begin_dispatch(
        "strategy-a",
        "key-1",
        order.reservation_id,
        order.digest,
        "principal-a",
        1,
        _identity(native, "TEST"),
        ("order-key",),
    ).record
    owner = object.__new__(GatewayDirectOwner)
    monkeypatch.setattr(
        GatewayDirectOwner,
        "binding",
        lambda _: SimpleNamespace(
            account_scope=journal.account_scope,
            journal_id=journal.journal_id,
            generation=journal.generation,
        ),
    )
    monkeypatch.setattr(
        GatewayDirectOwner, "is_bound_to", lambda _, candidate: candidate is journal
    )
    monkeypatch.setattr(GatewayDirectOwner, "supports_cancel", property(lambda _: True))
    pending = []
    queue = {"result": "QUEUED"}

    def enqueue(_, command, *, request_fence, finalize):
        state = queue["result"]
        if state == "QUEUED":
            pending.append((command, request_fence, finalize))
        else:
            finalize()
        return state

    monkeypatch.setattr(GatewayDirectOwner, "enqueue_cancel", enqueue)

    def matches_context(self, context):
        return context.dispatch_admission is self

    monkeypatch.setattr(ServiceDispatchAdmission, "matches_context", matches_context)
    monkeypatch.setattr(
        ServiceDispatchAdmission, "matches", lambda self, arguments: arguments == self.arguments
    )
    monkeypatch.setattr(
        ServiceDispatchAdmission,
        "matches_operation",
        lambda self, operation: operation == self.operation,
    )
    monkeypatch.setattr(ServiceDispatchAdmission, "close", lambda self: self.closed.append(True))

    @contextmanager
    def creation_scope(self):
        if self.deny_entry:
            raise RuntimeError("revoked before scope")

        def recheck():
            self.checks.append("inside-db")
            if self.deny_recheck:
                raise RuntimeError("revoked during commit")

        yield recheck

    @contextmanager
    def scope(self):
        yield lambda: None

    monkeypatch.setattr(ServiceDispatchAdmission, "creation_scope", creation_scope)
    monkeypatch.setattr(ServiceDispatchAdmission, "scope", scope)
    service = GatewayDirectServiceBinding(
        journal,
        {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"}))},
        owner=owner,
        cancel_enabled=True,
    )
    yield SimpleNamespace(
        journal=journal,
        order=order,
        native=native,
        service=service,
        queue=queue,
        pending=pending,
        path=tmp_path / "db",
    )


def token(arguments, operation="direct.cancel"):
    value = object.__new__(ServiceDispatchAdmission)
    value.arguments = dict(arguments)
    value.operation = operation
    value.closed, value.checks = [], []
    value.deny_entry = value.deny_recheck = False
    return value


def args(h, operation="cancel-1"):
    return {
        "request_key": "key-1",
        "reservation_id": h.order.reservation_id,
        "digest": h.order.digest,
        "operation_id": operation,
    }


def call(h, data, permit, *, fits=None):
    context = replace(_context(), dispatch_admission=permit, result_fits=fits)
    return h.service.handle("direct.cancel", context=context, arguments=data)


def test_options_and_capabilities_are_explicit(harness, monkeypatch):
    h = harness
    assert CANCEL_OPERATIONS <= h.service.supported_operations
    assert not CANCEL_OPERATIONS & SUPPORTED_OPERATIONS
    assert validate_service_options({"role": "trading_owner"}, ("service.direct_write",)) == {
        "role": "trading_owner",
        "dispatch_enabled": False,
        "cancel_enabled": False,
    }
    with pytest.raises(ValueError, match="INVALID_DIRECT_OPTIONS"):
        validate_service_options(
            {"role": "trading_owner", "cancel_enabled": True}, ("service.direct_write",)
        )
    monkeypatch.setattr(GatewayDirectOwner, "supports_cancel", property(lambda _: False))
    owner = object.__new__(GatewayDirectOwner)
    with pytest.raises(ValueError, match="CANCEL_OWNER_UNAVAILABLE"):
        GatewayDirectServiceBinding(
            h.journal,
            {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"rb"}))},
            owner=owner,
            cancel_enabled=True,
        )


def test_cancel_creation_queue_replay_and_public_status(harness):
    h = harness
    data = args(h)
    permit = token(data)
    response = call(h, data, permit)
    assert response["status"] == "OK" and response["cancel_queue_state"] == "QUEUED"
    assert response["record"]["state"] == "REQUESTED" and permit.checks == ["inside-db"]
    assert not permit.closed and len(h.pending) == 1
    assert not ({"prepared_cancel", "target_identity", "request_keys"} & response["record"].keys())
    status = h.service.handle(
        "direct.cancel_status", context=_context(), arguments={"operation_id": "cancel-1"}
    )
    assert status == {"status": "OK", "record": response["record"]}
    h.queue["result"] = "DUPLICATE"
    second = token(data)
    replay = call(h, data, second)
    assert (
        replay["record"] == response["record"] and replay["cancel_queue_state"] == "ALREADY_QUEUED"
    )
    assert second.closed == [True]
    h.pending[0][2]()
    h.pending[0][2]()
    assert permit.closed == [True]


def test_revocation_during_creation_and_same_id_conflict(harness):
    h = harness
    data = args(h)
    denied = token(data)
    denied.deny_recheck = True
    with pytest.raises(RuntimeError, match="revoked during commit"):
        call(h, data, denied)
    assert denied.closed == [True]
    assert (
        h.service.handle(
            "direct.cancel_status", context=_context(), arguments={"operation_id": "cancel-1"}
        )["code"]
        == "NOT_FOUND"
    )
    permit = token(data)
    call(h, data, permit)
    conflict = dict(data, reservation_id="other")
    token2 = token(conflict)
    assert call(h, conflict, token2)["code"] == "REQUEST_CONFLICT" and token2.closed == [True]
    replay = token(data)
    replay.deny_recheck = True
    with pytest.raises(RuntimeError, match="revoked during commit"):
        call(h, data, replay)
    assert replay.closed == [True]
    assert len(h.pending) == 1


def test_budget_failure_persists_unqueued_attempt(harness):
    h = harness
    data = args(h)
    permit = token(data)
    with pytest.raises(ServiceResultTooLargeError):
        call(h, data, permit, fits=lambda _: False)
    assert permit.closed == [True] and not h.pending
    status = h.service.handle(
        "direct.cancel_status", context=_context(), arguments={"operation_id": "cancel-1"}
    )
    assert status["record"]["state"] == "REQUESTED"


@pytest.mark.parametrize(
    "queue_result,code",
    [
        ("FULL", "CANCEL_QUEUE_FULL"),
        ("CLOSING", "CANCEL_QUEUE_CLOSING"),
        ("BUSY", "CANCEL_QUEUE_BUSY"),
        ("CONFLICT", "CANCEL_QUEUE_CONFLICT"),
        ("UNSUPPORTED", "CANCEL_UNAVAILABLE"),
    ],
)
def test_failed_queue_finalizes_once_and_retains_attempt(harness, queue_result, code):
    h = harness
    h.queue["result"] = queue_result
    data = args(h)
    permit = token(data)
    assert call(h, data, permit)["code"] == code
    assert permit.closed == [True]
    assert (
        h.service.handle(
            "direct.cancel_status", context=_context(), arguments={"operation_id": "cancel-1"}
        )["record"]["state"]
        == "REQUESTED"
    )


def test_scope_and_operation_bound_permit(harness):
    h = harness
    data = args(h)
    wrong = token(data, "direct.dispatch")
    assert call(h, data, wrong)["code"] == "ADMISSION_DENIED" and wrong.closed == [True]
    wrong_scope = replace(_context(), scope=replace(_context().scope, account_ref="different"))
    permit = token(data)
    assert (
        h.service.handle(
            "direct.cancel", context=replace(wrong_scope, dispatch_admission=permit), arguments=data
        )["code"]
        == "SCOPE_DENIED"
    )
    assert permit.closed == [True] and not h.pending


@pytest.mark.parametrize(
    "sent,expected", [(False, "CANCEL_NOT_SENT"), (None, "CANCEL_OUTCOME_UNKNOWN"), (True, None)]
)
def test_terminal_replay_is_observed_and_redacts_native_error(harness, sent, expected):
    from bt_api_base.direct_cancel_protocol import PreparedCancelRequest

    h = harness
    data = args(h)
    first = token(data)
    call(h, data, first)
    h.pending.pop()[2]()
    prepared = PreparedCancelRequest(
        "provider-reservation", h.native, "rb", "20300102", 2, {"RequestID": 12}, ("cancel-key",)
    )
    started = h.service._cancel_journal.begin_dispatch(
        "strategy-a", "cancel-1", "principal-a", 1, prepared
    ).record
    h.service._cancel_journal.finish(
        "strategy-a",
        "cancel-1",
        "principal-a",
        2,
        started.marker_id,
        prepared.digest(),
        h.journal.generation,
        sent,
        error_code="native_secret_code",
    )
    second = token(data)
    replay = call(h, data, second)
    assert replay["cancel_queue_state"] == "OBSERVED" and second.closed == [True]
    assert replay["record"]["error_code"] == expected
    assert "native_secret_code" not in str(replay)
    assert not h.pending


def test_creation_commit_then_error_propagates_and_status_recovers(harness, monkeypatch):
    h = harness
    data = args(h)
    original = h.service._cancel_journal.request

    def lost_reply(*a, **kw):
        original(*a, **kw)
        raise RuntimeError("commit reply lost")

    monkeypatch.setattr(h.service._cancel_journal, "request", lost_reply)
    permit = token(data)
    with pytest.raises(RuntimeError, match="commit reply lost"):
        call(h, data, permit)
    assert permit.closed == [True] and not h.pending
    observed = h.service.handle(
        "direct.cancel_status", context=_context(), arguments={"operation_id": "cancel-1"}
    )
    assert observed["record"]["state"] == "REQUESTED"


def test_cancel_token_cannot_dispatch_and_input_is_exact(harness):
    h = harness
    dispatch_args = {
        "request_key": "key-1",
        "reservation_id": h.order.reservation_id,
        "digest": h.order.digest,
        "expected_version": h.order.version,
    }
    permit = token(dispatch_args, "direct.cancel")
    response = h.service.handle(
        "direct.dispatch",
        context=replace(_context(), dispatch_admission=permit),
        arguments=dispatch_args,
    )
    assert response["code"] == "ADMISSION_DENIED" and permit.closed == [True]
    extra = {**args(h), "symbol": "rb"}
    extra_token = token(extra)
    assert call(h, extra, extra_token)["code"] == "INVALID_REQUEST"
    assert extra_token.closed == [True] and not h.pending


def test_cancel_rechecks_exact_owner_scope_when_dequeued(harness):
    h = harness
    data = args(h)
    permit = token(data)
    call(h, data, permit)
    command, fence, finalize = h.pending.pop()
    row = h.service._cancel_journal.get("strategy-a", "cancel-1", "principal-a")
    with fence(row) as recheck:
        recheck()
    with pytest.raises(RuntimeError, match="DIRECT_CANCEL_BINDING_CHANGED"):
        with fence(replace(row, target_principal="other")):
            pass
    with pytest.raises(RuntimeError, match="DIRECT_CANCEL_BINDING_CHANGED"):
        with fence(replace(row, symbol="other")):
            pass
    finalize()
    assert permit.closed == [True]


def test_actor_delegate_and_symbol_grant_fail_closed(harness):
    h = harness
    data = args(h)
    context = _context()
    permit = token(data)
    actor_mismatch = replace(
        context, identity=replace(context.identity, actor_ref="other"), dispatch_admission=permit
    )
    assert (
        h.service.handle("direct.cancel", context=actor_mismatch, arguments=data)["code"]
        == "SCOPE_DENIED"
    )
    assert permit.closed == [True]
    permit = token(data)
    delegated = replace(
        context,
        identity=replace(context.identity, delegate_ref="delegate"),
        dispatch_admission=permit,
    )
    assert (
        h.service.handle("direct.cancel", context=delegated, arguments=data)["code"]
        == "SCOPE_DENIED"
    )
    assert permit.closed == [True]
    service = GatewayDirectServiceBinding(
        h.journal,
        {"principal-a": GatewayDirectGrant("strategy-a", frozenset({"sa"}))},
        owner=h.service._owner,
        cancel_enabled=True,
    )
    permit = token(data)
    result = service.handle(
        "direct.cancel", context=replace(context, dispatch_admission=permit), arguments=data
    )
    assert result["code"] == "SCOPE_DENIED" and permit.closed == [True]
    assert not h.pending


def test_cancel_rejects_unrepresentable_fields_before_durable_request(harness):
    h = harness
    for data in (
        dict(args(h), operation_id="中" * 100),
        dict(args(h), digest="A" * 64),
        dict(args(h), operation_id="\ud800"),
    ):
        permit = token(data)
        assert call(h, data, permit)["code"] == "INVALID_REQUEST"
        assert permit.closed == [True]
    assert not h.pending
    with h.journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM direct_cancel_attempts").fetchone()[0] == 0
