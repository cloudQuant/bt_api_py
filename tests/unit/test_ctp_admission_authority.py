"""Real BasicAdmission and CTP Store-authority composition tests.

The TraderClient in these cases is the repository's Python fallback backed by
the existing synthetic CTP API leaf.  No native library, network, or account is
loaded.  The positive case deliberately goes through CtpExecutionOwner so the
new verifier is exercised at the durable, one-use Store claim boundary.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import time

import pytest

import test_ctp_execution_owner as ctp_owner_fixtures
import test_ctp_service_execution as execution_fixtures
import bt_api_py.ctp_admission_authority as ctp_authority_module
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ContractValidationError,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionScope,
    ExecutionState,
    ExecutionProviderValidatorV1,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
)
from bt_api_execution.basic_admission import ExecutionAdmissionContextV1
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedSingleWorkerCandidate,
    stable_managed_command_id,
)
from bt_api_ctp.ctp._ctp_base import is_ctp_native_loaded
from bt_api_py.ctp_admission_authority import (
    CtpAdmissionAuthorityIssuerV1,
    CtpNativeDispatchBindingV1,
)
from bt_api_py.ctp_execution_owner import CtpExecutionOwner


_ADMISSION_PRINCIPAL = "ctp-admission-strategy-principal"
_ADMISSION_DOMAIN = "ctp-admission-authority-test-domain"
_PROVIDER_ID = "ctp"
_PROVIDER_INSTRUMENT = "rb2710"
_EXCHANGE_ID = "SHFE"
_CALLER_REQUEST_ID = 73
_CALLER_QUEUE_RECEIPT_ID = "a" * 32
_TRUSTED_INVESTOR_ID = "investor-1"
_OTHER_INVESTOR_ID = "investor-2"


class _RegisteredCtpDispatchPort:
    """Code-owned admission port; actual CTP dispatch uses CtpExecutionOwner."""

    def submit_authorized(self, _intent, _authorization):
        raise AssertionError("BasicAdmission must not bypass CtpExecutionOwner")


class _FrozenClock:
    def __init__(self, now_ns: int) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


def _composition(case, *, clock_ns=time.time_ns, permit_ttl_ns=5_000_000_000,
                 stored_context_fingerprint: str | None = None,
                 stored_basic_admission_reference: str | None = None,
                 intent=None,
                 issuer_ttl_ns=5_000_000_000,
                 investor_id: str = _TRUSTED_INVESTOR_ID):
    scope = case.scope
    intent = case.intent if intent is None else intent
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _ADMISSION_PRINCIPAL,
        scope.strategy_id,
    )
    wire_scope = WireScope(
        _ADMISSION_DOMAIN,
        account_ref=scope.account_ref,
        strategy_ref=scope.strategy_id,
    )
    context = ExecutionAdmissionContextV1(
        principal_ref=_ADMISSION_PRINCIPAL,
        identity=identity,
        wire_scope=wire_scope,
        execution_scope=scope,
        action="orders.submit",
    )
    dispatch_port = _RegisteredCtpDispatchPort()
    validator = ExecutionProviderValidatorV1(
        provider_id=_PROVIDER_ID,
        validator_id="ctp-managed-order-authority-v1",
        version="v1",
        dispatch_port=dispatch_port,
        instrument_map={intent.instrument: _PROVIDER_INSTRUMENT},
        quantity_steps={intent.instrument: Decimal("1")},
        max_quantities={intent.instrument: Decimal("100")},
        price_ticks={intent.instrument: Decimal("0.01")},
        supported_order_types=(OrderType.LIMIT,),
        supported_time_in_force=(intent.time_in_force,),
        supported_actions=((intent.side, intent.position_effect),),
        supports_reduce_only=intent.reduce_only,
    )
    admission_gate = ExecutionBasicAdmissionGate(
        case.store,
        ExecutionAdmissionValidatorRegistry((validator,)),
        permit_ttl_ns=permit_ttl_ns,
        clock_ns=clock_ns,
        retain_claimed_authorizations=True,
    )

    # The accepted CTP session fixture has a genuine PENDING_ADMISSION row and
    # same-Store OrderRef reservation.  Advance it only through public Store
    # and BasicAdmission APIs; no SQL writes or fabricated admission receipts.
    assert case.store.get_intent(intent.intent_id, scope=scope) == intent
    permit_reference = admission_gate.reserve_for_dispatch(
        context, intent, dispatch_port, case.writer_lease
    )
    persisted_context_fingerprint = (
        context.fingerprint
        if stored_context_fingerprint is None
        else stored_context_fingerprint
    )
    persisted_basic_admission_reference = (
        permit_reference
        if stored_basic_admission_reference is None
        else stored_basic_admission_reference
    )
    activated = case.store.activate_intent(
        intent.intent_id,
        scope,
        "risk-permit-" + intent.intent_id,
        basic_admission_reference=persisted_basic_admission_reference,
        admission_context_sha256=persisted_context_fingerprint,
        writer_lease=case.writer_lease,
    )
    assert activated.state is ExecutionState.PENDING_DISPATCH
    dispatching, claimed = case.store.claim_for_dispatch(
        intent.intent_id,
        scope,
        writer_lease=case.writer_lease,
    )
    assert claimed is True
    assert dispatching.state is ExecutionState.DISPATCHING

    authorization = admission_gate.claim_for_dispatch(
        permit_reference,
        intent,
        context,
        dispatch_port,
        case.writer_lease,
    )
    native_binding = CtpNativeDispatchBindingV1(
        account_ref=scope.account_ref,
        scope_key=scope.key,
        investor_id=investor_id,
        intent_instrument=intent.instrument,
        provider_instrument=_PROVIDER_INSTRUMENT,
        exchange_id=_EXCHANGE_ID,
    )
    issuer = CtpAdmissionAuthorityIssuerV1(
        case.store,
        scope,
        case.writer_lease,
        admission_gate,
        case.session_adapter,
        dispatch_port,
        native_binding,
        ttl_ns=issuer_ttl_ns,
    )
    return context, dispatch_port, admission_gate, authorization, issuer


def _prepared(case, issuer, authorization, *, intent=None):
    return issuer.prepare_submit(
        case.intent if intent is None else intent,
        authorization,
        native_request_id=_CALLER_REQUEST_ID,
        local_queue_receipt_id=_CALLER_QUEUE_RECEIPT_ID,
    )


def _admit_intent_variant(case, suffix, *, position_effect=PositionEffect.OPEN,
                          position_bucket="ALL", deadline_ns=None):
    """Admit and reserve a DAY intent beside the fixture's legacy GTC row."""

    intent = replace(
        case.intent,
        intent_id=f"ctp-authority-{suffix}",
        signal_id=f"ctp-authority-{suffix}-signal",
        time_in_force=TimeInForce.DAY,
        position_effect=position_effect,
        position_bucket=position_bucket,
        reduce_only=position_effect is not PositionEffect.OPEN,
        deadline_ns=deadline_ns,
    )
    case.store.admit_intent(intent, writer_lease=case.writer_lease)
    proof = replace(
        execution_fixtures._order_ref_proof(
            case.scope, case.session_binding.session_generation_id
        ),
        native_front_id=case.session_binding.dispatch_front_id,
        native_session_id=case.session_binding.dispatch_session_id,
    )
    reservation = case.store.seed_ctp_order_ref_and_reserve_identity(
        case.scope,
        proof,
        intent.intent_id,
        execution_fixtures._runtime_order_id(intent.intent_id),
        writer_lease=case.writer_lease,
    )
    return intent, reservation


def _queue_receipt(prepared):
    return {
        "kind": "command_receipt",
        "command": "submit",
        "receipt_id": prepared.local_queue_receipt_id,
        "queued": True,
    }


@pytest.mark.unit
def test_ctp_admission_authority_dispatches_once_through_real_owner_and_replays_safely(
    tmp_path, monkeypatch
):
    assert is_ctp_native_loaded() is False
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        assert is_ctp_native_loaded() is False
        intent, reservation = _admit_intent_variant(case, "open")
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case, intent=intent
        )
        prepared = _prepared(case, issuer, authorization, intent=intent)

        assert prepared.operation == "submit"
        assert prepared.managed_intent_id == intent.intent_id
        assert prepared.order_ref == reservation.order_ref
        assert prepared.native_request_id == _CALLER_REQUEST_ID
        assert prepared.local_queue_receipt_id == _CALLER_QUEUE_RECEIPT_ID
        assert authorization.parameters.provider_instrument == _PROVIDER_INSTRUMENT

        owner = CtpExecutionOwner(
            case.store,
            case.scope,
            case.writer_lease,
            issuer,
            case.trader,
            case.session_adapter,
            case.reconciliation_port,
            session_generation_id=case.session_binding.session_generation_id,
        )
        first = owner.dispatch_submit(prepared, _queue_receipt(prepared))
        assert first.command.status == "COMPLETED"
        assert first.binding.local_queue_receipt_queued is True

        insert_calls = [call for call in case.api.calls if call[0] == "ReqOrderInsert"]
        assert len(insert_calls) == 1
        _, native_field, request_id = insert_calls[0]
        assert request_id == _CALLER_REQUEST_ID
        assert native_field.OrderRef == reservation.order_ref
        assert native_field.BrokerID == "9999"
        assert native_field.UserID == "investor-1"
        assert native_field.InvestorID == _TRUSTED_INVESTOR_ID
        assert native_field.InstrumentID == intent.instrument
        assert native_field.ExchangeID == _EXCHANGE_ID
        assert native_field.TimeCondition == "3"
        assert native_field.CombOffsetFlag == "0"

        replay = owner.dispatch_submit(prepared, _queue_receipt(prepared))
        assert replay.command.command_id == first.command.command_id
        assert replay.command.status == "COMPLETED"
        assert len([call for call in case.api.calls if call[0] == "ReqOrderInsert"]) == 1
        assert is_ctp_native_loaded() is False


@pytest.mark.unit
def test_ctp_admission_authority_rejects_copy_without_poisoning_exact_approval(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, _reservation = _admit_intent_variant(case, "copy")
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case, intent=intent
        )
        copied_authorization = replace(authorization)
        with pytest.raises(ContractValidationError):
            _prepared(case, issuer, copied_authorization, intent=intent)

        command_id = stable_managed_command_id(
            "submit", intent.intent_id, _reservation.runtime_order_id
        )
        assert case.store.read_ctp_dispatch_command(case.scope, command_id) is None
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]

        # A bad copy cannot reserve the Gate reference or poison the genuine
        # authorization in the same real issuer instance.
        prepared = _prepared(case, issuer, authorization, intent=intent)
        assert prepared.managed_intent_id == intent.intent_id


@pytest.mark.unit
def test_ctp_admission_authority_rejects_altered_intent_before_staging(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(case, "altered")
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case, intent=intent
        )
        changed_intent = replace(intent, price=Decimal("101"))
        with pytest.raises(ContractValidationError):
            issuer.prepare_submit(
                changed_intent,
                authorization,
                native_request_id=_CALLER_REQUEST_ID,
                local_queue_receipt_id=_CALLER_QUEUE_RECEIPT_ID,
            )

        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        assert case.store.read_ctp_dispatch_command(case.scope, command_id) is None
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
@pytest.mark.parametrize("binding_mismatch", ("permit", "context"))
def test_ctp_admission_authority_requires_exact_persisted_dispatch_provenance(
    tmp_path, monkeypatch, binding_mismatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(case, "provenance")
        context, _dispatch_port, _gate, authorization, issuer = _composition(
            case,
            stored_basic_admission_reference=(
                "b" * 32 if binding_mismatch == "permit" else None
            ),
            stored_context_fingerprint=("c" * 64 if binding_mismatch == "context" else None),
            intent=intent,
        )
        record = case.store.get(intent.intent_id, scope=case.scope)
        assert record is not None
        assert record.state is ExecutionState.DISPATCHING
        assert record.permit_reference == "risk-permit-" + intent.intent_id
        if binding_mismatch == "context":
            assert record.basic_admission_reference == authorization.permit_reference
            assert record.admission_context_sha256 != context.fingerprint
        else:
            assert record.basic_admission_reference != authorization.permit_reference

        with pytest.raises(ContractValidationError):
            _prepared(case, issuer, authorization, intent=intent)
        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        assert case.store.read_ctp_dispatch_command(case.scope, command_id) is None
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
def test_ctp_admission_authority_rejects_configured_investor_login_mismatch(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(case, "investor-mismatch")
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case, intent=intent, investor_id=_OTHER_INVESTOR_ID
        )
        with pytest.raises(ContractValidationError):
            _prepared(case, issuer, authorization, intent=intent)

        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        assert case.store.read_ctp_dispatch_command(case.scope, command_id) is None
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
def test_ctp_admission_authority_fails_closed_at_gate_expiry(tmp_path, monkeypatch):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(case, "gate-expiry")
        clock = _FrozenClock(time.time_ns())
        with monkeypatch.context() as patcher:
            # Gate and issuer must observe the same deterministic wall clock;
            # Store lease/session setup above remains on the real clock.
            patcher.setattr(ctp_authority_module.time, "time_ns", clock)
            _context, _dispatch_port, _gate, authorization, issuer = _composition(
                case,
                clock_ns=clock,
                permit_ttl_ns=1_000_000,
                intent=intent,
            )
            clock.now_ns = authorization.expires_at_ns
            with pytest.raises(ContractValidationError):
                _prepared(case, issuer, authorization, intent=intent)

        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        assert case.store.read_ctp_dispatch_command(case.scope, command_id) is None
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
def test_ctp_admission_authority_binds_close_offset_and_intent_deadline(
    tmp_path, monkeypatch
):
    deadline_ns = time.time_ns() + 20_000_000_000
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(
            case,
            "close-today-deadline",
            position_effect=PositionEffect.CLOSE,
            position_bucket="TODAY",
            deadline_ns=deadline_ns,
        )
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case,
            permit_ttl_ns=60_000_000_000,
            issuer_ttl_ns=30_000_000_000,
            intent=intent,
        )
        prepared = _prepared(case, issuer, authorization, intent=intent)
        assert intent.reduce_only is True
        assert prepared.request_payload["Direction"] == "0"
        assert prepared.request_payload["CombOffsetFlag"] == "3"
        assert prepared.request_payload["VolumeTotalOriginal"] == 1
        assert prepared.request_payload["TimeCondition"] == "3"

        candidate = CtpManagedSingleWorkerCandidate(
            case.store, case.scope, case.writer_lease, issuer
        )
        binding = candidate.stage_prepared_dispatch(prepared)
        candidate.record_managed_queue_receipt(
            prepared.command_id, binding, _queue_receipt(prepared)
        )
        command = case.store.read_ctp_dispatch_command(case.scope, prepared.command_id)
        assert command is not None and command.status == "READY"
        authority = issuer.verify_action(command, now_ns=time.time_ns())
        assert authority.expires_at_ns <= intent.deadline_ns
        assert authority.expires_at_ns > authority.verified_at_ns
        assert reservation.order_ref == prepared.order_ref
        assert authorization.parameters.position_effect is PositionEffect.CLOSE
        assert authorization.parameters.reduce_only is True
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
def test_ctp_admission_authority_rejects_ambiguous_shfe_close_bucket(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(
            case,
            "close-all-rejected",
            position_effect=PositionEffect.CLOSE,
            position_bucket="ALL",
        )
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case, intent=intent
        )
        with pytest.raises(ContractValidationError):
            _prepared(case, issuer, authorization, intent=intent)

        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        assert case.store.read_ctp_dispatch_command(case.scope, command_id) is None
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
def test_ctp_admission_authority_rejects_tampered_native_payload_before_claim(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation = _admit_intent_variant(case, "payload-tamper")
        _context, _dispatch_port, _gate, authorization, issuer = _composition(
            case, intent=intent
        )
        prepared = _prepared(case, issuer, authorization, intent=intent)
        tampered = replace(
            prepared,
            request_payload={
                **dict(prepared.request_payload),
                "VolumeTotalOriginal": int(intent.quantity) + 1,
            },
        )
        candidate = CtpManagedSingleWorkerCandidate(
            case.store, case.scope, case.writer_lease, issuer
        )
        binding = candidate.stage_prepared_dispatch(tampered)
        candidate.record_managed_queue_receipt(
            tampered.command_id, binding, _queue_receipt(tampered)
        )

        with pytest.raises(ContractValidationError):
            case.store.claim_ctp_dispatch_command_for_session(
                case.scope,
                tampered.command_id,
                owner_handle=case.owner_handle,
                writer_lease=case.writer_lease,
                authority_verifier=issuer,
                required_local_queue_receipt_id=tampered.local_queue_receipt_id,
            )
        durable = case.store.read_ctp_dispatch_command(case.scope, tampered.command_id)
        assert durable is not None
        assert durable.status == "READY"
        assert durable.local_queue_receipt_queued is True
        assert durable.native_receipt_payload is None
        assert case.store.get_intent(intent.intent_id, scope=case.scope) == intent
        assert reservation.order_ref == tampered.order_ref
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]
