from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import test_ctp_execution_owner as ctp_owner_fixtures
import test_ctp_managed_submit_port as managed_submit_fixtures
import test_ctp_order_target_query as query_fixtures
import test_service_execution_risk_cancel_claim as risk_fixtures

from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    CancelIntent,
    ContractValidationError,
    CtpOwnerCallbackObservationNotReadyV1,
    CtpVerifiedCallbackEvidence,
    ExecutionState,
    InvalidStateTransition,
    ManagedCancellationFacade,
    WriterLeaseUnavailable,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedDispatchBinding,
    stable_managed_command_id,
)
from bt_api_py.ctp_managed_cancel_port import CtpManagedCancelPort
from bt_api_py.ctp_service_execution import encode_ctp_provider_order_id
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy


def _open_target(case, suffix: str, monkeypatch):
    """Create and callback-ack one real Store-backed CTP submit target."""

    intent = managed_submit_fixtures._day_intent(case, suffix)
    submit_port, _basic_gate, submit_facade, admission_context = (
        managed_submit_fixtures._compose(case, intent)
    )
    pending = submit_facade.submit(
        intent, submit_port, admission_context=admission_context
    )
    assert pending.state is ExecutionState.UNKNOWN
    assert pending.dispatch_attempts == 1

    reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
    assert reservation is not None
    submit_calls = managed_submit_fixtures._submit_calls(case)
    assert len(submit_calls) == 1
    request_id = submit_calls[0][2]
    assert submit_calls[0][1].OrderRef == reservation.order_ref

    managed_submit_fixtures._emit_order(
        case,
        intent,
        reservation,
        request_id,
        status="3",
        traded=0,
        sequence=1,
    )
    acknowledged = submit_facade.reconcile_with_port(intent.intent_id, submit_port)
    assert acknowledged.state is ExecutionState.ACKED
    assert acknowledged.provider_order_id == encode_ctp_provider_order_id(
        "SHFE", "SYS" + reservation.order_ref
    )
    assert case.owner_handle.owner_intent_id != intent.intent_id
    native_row = query_fixtures._native_order(
        case, intent=intent, reservation=reservation
    )
    query_fixtures._install_native_query_rows(case, monkeypatch, (native_row,))

    command_id = stable_managed_command_id(
        "submit", intent.intent_id, reservation.runtime_order_id
    )
    command = case.store.read_ctp_dispatch_command(case.scope, command_id)
    assert command is not None
    assert command.operation == "SUBMIT"
    assert command.status == "COMPLETED"
    assert command.local_queue_receipt_queued is True
    return intent, reservation, submit_port, submit_facade


def _cancel_intent(
    target,
    reservation,
    suffix: str,
    *,
    provider_order_id: str | None = None,
):
    cancel_id = f"ctp-managed-cancel-{suffix}"
    return CancelIntent(
        cancel_id=cancel_id,
        scope=target.scope,
        target_intent_id=target.intent_id,
        provider_order_id=provider_order_id
        or encode_ctp_provider_order_id(
            "SHFE", "SYS" + reservation.order_ref
        ),
        metadata_version="synthetic-ctp-v1",
        tags={
            "owner_cancel_binding_version": "1",
            "owner_cancel_batch_key": "batch-" + cancel_id,
            "owner_cancel_command_id": "command-" + cancel_id,
            "owner_cancel_principal_ref": "operator-principal",
            "owner_cancel_actor_ref": "operator-actor",
            "owner_cancel_strategy_ref": target.scope.strategy_id,
            "owner_cancel_run_id": "run-ctp-managed-cancel",
        },
    )


class _CancelActionResponseSourceFake:
    """Bounded test verifier for the mapped synthetic OnRspOrderAction fields.

    This does not model a production trust decision. The callback ingress and
    native-field mapper remain real; only the verifier leaf reads the mapped
    response error code for this source-fake test.
    """

    def __init__(self, delegate):
        self._delegate = delegate

    def verify_callback(self, command, callback, callback_payload, *, now_ns):
        if command.operation != "CANCEL":
            return self._delegate.verify_callback(
                command, callback, callback_payload, now_ns=now_ns
            )

        native_fields = callback_payload.get("native_fields")
        error_id = callback_payload.get("response_error_id")
        if (
            callback_payload.get("source_callback") != "OnRspOrderAction"
            or callback_payload.get("response_is_last") is not True
            or type(error_id) is not int
            or type(native_fields) is not dict
            or callback.callback_family != "CANCEL_ACTION"
        ):
            raise ContractValidationError(
                "synthetic cancel verifier requires a terminal mapped action response"
            )

        return CtpVerifiedCallbackEvidence(
            evidence_type="ctp_verified_callback.v1",
            callback_key=callback,
            callback_payload_sha256=payload_sha256(callback_payload),
            projection_state="ACKNOWLEDGED" if error_id == 0 else "REJECTED",
            source_digest_sha256=hashlib.sha256(
                b"synthetic OnRspOrderAction response verifier"
            ).hexdigest(),
            verifier_id="test-sourcefake-ctp-cancel-action-response",
            verified_at_ns=now_ns,
            expires_at_ns=now_ns + 5_000_000_000,
        )


@contextmanager
def _cancel_stack(case, tmp_path, submit_port, submit_facade):
    risk_scope = AccountScope(
        case.scope.provider, case.scope.account_ref, case.scope.environment
    )
    wire_scope = WireScope(
        "ctp:managed-cancel-test",
        account_ref=case.scope.account_ref,
        strategy_ref=case.scope.strategy_id,
    )
    journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3",
        writer_id="ctp-managed-cancel-journal",
    )
    risk_path = tmp_path / "risk.sqlite3"
    risk_gate = DurableRiskGate(
        risk_path,
        RiskPolicy(
            "ctp-managed-cancel-v1",
            Decimal("0"),
            0,
            permit_ttl_seconds=60.0,
            notional_unit="USD",
        ),
        clock=lambda: 100.0,
        execution_journal_authority=journal,
    )
    try:
        # _open_target already applied the real SUBMIT callback. Install a
        # narrow synthetic callback-verifier leaf for the later real
        # OnRspOrderAction ingress so ErrorID=0 and ErrorID!=0 map distinctly.
        case.reconciliation_port._callback_verifier = _CancelActionResponseSourceFake(
            case.reconciliation_port._callback_verifier
        )
        admission = RiskExecutionCancelAdmissionGate(
            risk_gate,
            journal,
            case.store,
            risk_scope=risk_scope,
            execution_scope=case.scope,
            wire_scope_template=wire_scope,
        )
        facade = ManagedCancellationFacade(
            case.store,
            case.scope,
            acquire_writer_lease=submit_facade.acquire_writer_lease,
            admission_gate=admission,
        )
        port = CtpManagedCancelPort(
            submit_port._owner,
            admission,
            callback_pump_events=32,
            query_timeout=0.25,
        )
        yield SimpleNamespace(
            journal=journal,
            risk_path=risk_path,
            risk_gate=risk_gate,
            admission=admission,
            facade=facade,
            port=port,
        )
    finally:
        risk_gate.close()


def _cancel_calls(case):
    return [call for call in case.api.calls if call[0] == "ReqOrderAction"]


def _query_calls(case):
    return [call for call in case.api.calls if call[0] == "ReqQryOrder"]


def _cancel_command(case, target, cancel_intent):
    reservation = case.store.read_ctp_order_identity(case.scope, target.intent_id)
    command_id = stable_managed_command_id(
        "cancel",
        target.intent_id,
        reservation.runtime_order_id,
        cancel_intent.cancel_id,
    )
    return case.store.read_ctp_dispatch_command(case.scope, command_id)


def _emit_cancel_response(case, *, error_id: int):
    calls = _cancel_calls(case)
    assert len(calls) == 1
    _name, field, request_id = calls[0]
    case.trader._spi.OnRspOrderAction(
        field,
        SimpleNamespace(ErrorID=error_id, ErrorMsg="synthetic CTP action response"),
        request_id,
        True,
    )
    return field, request_id


@pytest.mark.unit
def test_managed_cancel_waits_for_real_action_callback_and_reconciles_exact_bindings(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        target, reservation, submit_port, submit_facade = _open_target(
            case, "callback-ack", monkeypatch
        )
        with _cancel_stack(case, tmp_path, submit_port, submit_facade) as stack:
            intent = _cancel_intent(target, reservation, "callback-ack")
            first = stack.facade.cancel(
                intent,
                stack.port,
                before_dispatch=stack.port.prepare_target,
            )

            # A successful local native return and Store queue receipt prove
            # only dispatch. Missing CTP action evidence remains UNKNOWN.
            assert first.state is ExecutionState.UNKNOWN
            assert first.dispatch_attempts == 1
            assert len(_cancel_calls(case)) == 1
            assert len(_query_calls(case)) == 1
            assert stack.port._queue.unfinished_tasks == 0
            command = _cancel_command(case, target, intent)
            assert command is not None
            assert command.operation == "CANCEL"
            assert command.local_queue_receipt_queued is True
            cancel_binding = CtpManagedDispatchBinding.from_command(
                command, reservation
            )
            native_action_ref = cancel_binding.native_action_ref
            assert native_action_ref > 0

            cached = stack.port._targets[intent.cancel_id]
            provenance = cached.target_projection.projection.query_provenance
            assert provenance is not None
            assert provenance.owner_intent_id == case.owner_handle.owner_intent_id
            assert provenance.owner_intent_id != target.intent_id
            assert provenance.managed_intent_id == target.intent_id
            assert stack.port.reconcile(intent) is None

            replay = stack.facade.cancel(
                intent,
                stack.port,
                before_dispatch=stack.port.prepare_target,
            )
            assert replay.state is ExecutionState.UNKNOWN
            assert replay.dispatch_attempts == 1
            assert len(_cancel_calls(case)) == 1
            assert len(_query_calls(case)) == 1

            field, _request_id = _emit_cancel_response(case, error_id=0)
            assert field.OrderRef == reservation.order_ref
            assert field.ExchangeID == "SHFE"
            assert field.OrderSysID == "SYS" + reservation.order_ref
            assert field.FrontID == case.session_binding.dispatch_front_id
            assert field.SessionID == case.session_binding.dispatch_session_id
            assert field.ActionFlag == "0"
            assert field.OrderActionRef == native_action_ref

            observation = stack.port.reconcile(intent)
            assert observation is not None
            assert observation.cancel_id == intent.cancel_id
            assert observation.target_intent_id == target.intent_id
            assert observation.provider_order_id == intent.provider_order_id
            assert observation.state is ExecutionState.ACKED

            submit_binding = cached.submit_binding
            before_read = case.store._connection.total_changes
            assert case.reconciliation_port.read_cancel_observation(
                cancel_binding, submit_binding
            ) == observation
            substituted = replace(
                submit_binding, command_id=submit_binding.command_id + "-foreign"
            )
            rejected = case.reconciliation_port.read_cancel_observation(
                cancel_binding, substituted
            )
            assert type(rejected) is CtpOwnerCallbackObservationNotReadyV1
            assert rejected.reason_code == "COMMAND_NOT_FOUND"
            assert case.store._connection.total_changes == before_read

            reconciled = stack.facade.reconcile(observation)
            assert reconciled.state is ExecutionState.ACKED
            assert reconciled.dispatch_attempts == 1
            assert len(_cancel_calls(case)) == 1
            assert stack.port._queue.unfinished_tasks == 0


@pytest.mark.unit
def test_managed_cancel_rejects_forged_risk_receipt_before_ctp_action(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        target, reservation, submit_port, submit_facade = _open_target(
            case, "forged-risk-receipt", monkeypatch
        )
        with _cancel_stack(case, tmp_path, submit_port, submit_facade) as stack:
            intent = _cancel_intent(
                target, reservation, "forged-risk-receipt"
            )

            class _ReceiptTamperingBoundary:
                def __init__(self, delegate):
                    self.delegate = delegate
                    self.received = None

                def prepare_target(self, cancel_intent):
                    return self.delegate.prepare_target(cancel_intent)

                def cancel_with_claim(self, cancel_intent, receipt):
                    self.received = receipt
                    return self.delegate.cancel_with_claim(
                        cancel_intent,
                        replace(receipt, intent_fingerprint="f" * 64),
                    )

            boundary = _ReceiptTamperingBoundary(stack.port)
            result = stack.facade.cancel(
                intent, boundary, before_dispatch=boundary.prepare_target
            )

            assert result.state is ExecutionState.UNKNOWN
            assert result.dispatch_attempts == 1
            assert boundary.received is not None
            assert boundary.received.cancel_id == intent.cancel_id
            assert boundary.received.intent_fingerprint == intent.fingerprint
            assert len(_query_calls(case)) == 1
            assert _cancel_calls(case) == []
            assert _cancel_command(case, target, intent) is None
            risk_state = risk_fixtures._risk_state(stack)
            assert len(risk_state[1]) == 1
            assert stack.port._queue.unfinished_tasks == 0


@pytest.mark.unit
def test_managed_cancel_native_exception_poison_keeps_claimed_inflight_without_resend(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        target, reservation, submit_port, submit_facade = _open_target(
            case, "ambiguous-native-result", monkeypatch
        )
        with _cancel_stack(case, tmp_path, submit_port, submit_facade) as stack:
            intent = _cancel_intent(
                target, reservation, "ambiguous-native-result"
            )

            def raise_after_native_entry(field, request_id):
                case.api.calls.append(("ReqOrderAction", field, request_id))
                raise OSError("synthetic native boundary ambiguity")

            monkeypatch.setattr(
                case.api, "ReqOrderAction", raise_after_native_entry, raising=True
            )
            with pytest.raises(WriterLeaseUnavailable):
                stack.facade.cancel(
                    intent,
                    stack.port,
                    before_dispatch=stack.port.prepare_target,
                )

            assert len(_cancel_calls(case)) == 1
            assert len(_query_calls(case)) == 1
            assert stack.port._queue.unfinished_tasks == 0
            cancel_record = case.store.get_cancel(intent.cancel_id, scope=case.scope)
            assert cancel_record is not None
            assert cancel_record.state is ExecutionState.DISPATCHING
            assert cancel_record.dispatch_attempts == 1
            assert cancel_record.unknown_reason is None
            assert cancel_record.permit_reference is not None

            command = _cancel_command(case, target, intent)
            assert command is not None
            assert command.status == "CLAIMED"
            assert command.local_queue_receipt_queued is True
            assert command.native_receipt_payload is None
            assert command.native_receipt_sha256 is None
            assert command.completion_echo_sha256 is None
            inflight_row = case.store._connection.execute(
                "SELECT status, native_call_inflight, unknown_reason "
                "FROM ctp_dispatch_commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            assert inflight_row is not None
            assert tuple(inflight_row) == ("CLAIMED", 1, None)
            assert case.trader._callback_ingress.poisoned is True
            assert case.trader._callback_ingress.poison_reason == "native_call_ambiguous"
            owner_row = case.store._connection.execute(
                "SELECT owner_state, poison_code FROM ctp_account_family_owners"
            ).fetchone()
            assert owner_row is not None
            assert tuple(owner_row) == ("POISONED", "native_call_ambiguous")

            risk_state_before_replay = risk_fixtures._risk_state(stack)
            claims = risk_state_before_replay[1]
            assert len(claims) == 1
            risk_intent_id = (
                "cancel-admission:" + intent.scope.key + ":" + intent.cancel_id
            )
            assert claims[0][0] == cancel_record.permit_reference
            assert claims[0][1] == risk_intent_id
            assert claims[0][4] == "dispatch-inflight:" + risk_intent_id

            with pytest.raises(InvalidStateTransition):
                stack.facade.cancel(
                    intent,
                    stack.port,
                    before_dispatch=stack.port.prepare_target,
                )

            assert len(_cancel_calls(case)) == 1
            assert len(_query_calls(case)) == 1
            assert case.store.get_cancel(intent.cancel_id, scope=case.scope) == cancel_record
            assert _cancel_command(case, target, intent) == command
            assert risk_fixtures._risk_state(stack) == risk_state_before_replay
            assert stack.port._queue.unfinished_tasks == 0


@pytest.mark.unit
def test_managed_cancel_wrong_query_order_row_is_blocked_before_risk_claim_or_action(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        target, reservation, submit_port, submit_facade = _open_target(
            case, "wrong-query-order-row", monkeypatch
        )
        wrong_row = query_fixtures._native_order(
            case, intent=target, reservation=reservation
        )
        wrong_row.OrderSysID += "-other"
        query_fixtures._install_native_query_rows(case, monkeypatch, (wrong_row,))
        with _cancel_stack(case, tmp_path, submit_port, submit_facade) as stack:
            intent = _cancel_intent(target, reservation, "wrong-query-order-row")
            result = stack.facade.cancel(
                intent,
                stack.port,
                before_dispatch=stack.port.prepare_target,
            )

            assert result.state is ExecutionState.BLOCKED
            assert _cancel_calls(case) == []
            assert len(_query_calls(case)) == 1
            assert _cancel_command(case, target, intent) is None
            assert risk_fixtures._risk_state(stack)[1] == ()
            assert stack.port._queue.unfinished_tasks == 0


@pytest.mark.unit
def test_managed_cancel_rejection_is_derived_from_order_action_callback(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        target, reservation, submit_port, submit_facade = _open_target(
            case, "action-rejected", monkeypatch
        )
        with _cancel_stack(case, tmp_path, submit_port, submit_facade) as stack:
            intent = _cancel_intent(target, reservation, "action-rejected")
            result = stack.facade.cancel(
                intent,
                stack.port,
                before_dispatch=stack.port.prepare_target,
            )
            assert result.state is ExecutionState.UNKNOWN
            assert len(_cancel_calls(case)) == 1

            _emit_cancel_response(case, error_id=17)
            observation = stack.port.reconcile(intent)
            assert observation is not None
            assert observation.state is ExecutionState.REJECTED
            assert observation.reason_code == "ctp_cancel_rejected"
            assert observation.cancel_id == intent.cancel_id
            assert observation.target_intent_id == target.intent_id
            assert len(_cancel_calls(case)) == 1
            assert stack.port._queue.unfinished_tasks == 0
