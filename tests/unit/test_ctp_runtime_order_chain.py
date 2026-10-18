"""Runtime-built execution service chains over one synthetic CTP API leaf.

The built-in service component, Store, admission gates, bridge, TraderClient,
callback SPI/verifier, CTP owner, and managed ports are production objects.
The native API, owner-cancel quota callback, and OrderRef cutover proof are
synthetic leaves; native callbacks still enter through the already-registered
SPI. This is source-fake integration evidence, not actual cutover, quota,
account, or exchange acceptance.
"""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
import test_ctp_callback_evidence as callback_fixtures
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_order_target_query as query_fixtures
from test_ctp_runtime_cancel_authorization import (
    _assert_cancel_recovery_hook,
    install_synthetic_cancel_recovery_provider,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CtpDispatchCallbackApplyResult,
    CtpDispatchTradeFactApplyResult,
    ExecutionState,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
)
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_execution.strategy_cancel import owner_cancel_id
from bt_api_risk import (
    CancelDispatchResolutionReceiptStatus,
    DispatchResolutionReceiptStatus,
)

from bt_api_py.service_registry import ServiceCallContext
from bt_api_py._ctp_execution_authorization import (
    RECOVERY_APPROVAL_PURPOSE,
    SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
)

_PRINCIPAL = "ctp-runtime-strategy-client"
_RUN_ID = "ctp-runtime-order-chain-run"
_INSTRUMENT = "rb2610"
_EXCHANGE = "SHFE"
_CUTOVER_ANCHOR_INTENT = "ctp-runtime-order-ref-cutover-anchor"


def _service_context(case) -> ServiceCallContext:
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _PRINCIPAL,
        case.execution_scope.strategy_id,
    )
    wire_scope = WireScope(
        case.service_scope.execution_domain_ref,
        account_ref=case.execution_scope.account_ref,
        strategy_ref=case.execution_scope.strategy_id,
        run_id=_RUN_ID,
    )
    return ServiceCallContext(
        identity,
        wire_scope,
        "ctp-runtime-order-chain-registration",
        _PRINCIPAL,
    )


def _owner_control_context(case) -> ServiceCallContext:
    identity = WireIdentity(
        WireRole.SERVICE,
        "execution-owner-service",
        case.execution_scope.strategy_id,
    )
    wire_scope = WireScope(
        case.service_scope.execution_domain_ref,
        account_ref=case.execution_scope.account_ref,
        strategy_ref=case.execution_scope.strategy_id,
        run_id=_RUN_ID,
    )
    return ServiceCallContext(
        identity,
        wire_scope,
        "ctp-runtime-owner-control-registration",
        identity.principal_ref,
    )


def _build_owner_control_execution_component(case, monkeypatch):
    """Use the real owner route with a synthetic no-op quota callback."""

    admission_calls = []
    admission_returns = []
    original_factory = case.config.risk_admission_factory

    def risk_admission_factory(context):
        admission = dict(original_factory(context))

        def cancel_quota_admission(*args):
            admission_calls.append(args)
            admission_returns.append(True)
            return None

        admission["cancel_quota_admission"] = cancel_quota_admission
        return admission

    case.provider.config = replace(
        case.config, risk_admission_factory=risk_admission_factory
    )
    case.cancel_quota_calls = admission_calls
    case.cancel_quota_returns = admission_returns

    # `_prepared()` normally asks the service-host fixture to bind a socket.
    # Keep this composed component test on the same fixed synthetic endpoint
    # as the runtime fixture so the socket guard remains in force.
    monkeypatch.setattr(
        runtime_fixtures.service_host_fixtures,
        "_endpoint",
        lambda: "tcp://127.0.0.1:17991",
    )
    service_components = runtime_fixtures.service_component_fixtures
    prepared = service_components._prepared(
        ports=(
            "service.order_entry",
            "service.order_query",
            "service.owner_control",
        )
    )
    monkeypatch.setattr(service_components, "_prepared", lambda: prepared)
    return runtime_fixtures._build_execution_component(case, monkeypatch)


def _submit_diagnostics(case, api, intent_id: str, reply) -> dict[str, object]:
    result = reply.get("result") if isinstance(reply, Mapping) else None
    record = result.get("record") if isinstance(result, Mapping) else None
    admission = case.resources.values.get("admission")
    bridge = admission.get("order_gate") if isinstance(admission, Mapping) else None
    binding = (
        bridge.read_dispatch_binding(_PRINCIPAL, intent_id)
        if bridge is not None
        else None
    )
    return {
        "response_ok": reply.get("ok") if isinstance(reply, Mapping) else None,
        "response_code": reply.get("code") if isinstance(reply, Mapping) else None,
        "record_state": record.get("state") if isinstance(record, Mapping) else None,
        "record_unknown_reason": (
            record.get("unknown_reason") if isinstance(record, Mapping) else None
        ),
        "native_counts": _counts(api),
        "risk_dispatch_binding_present": binding is not None,
        "submit_authorized_errors": getattr(
            case, "submit_authorized_errors", []
        ),
        "native_lease_errors": getattr(case, "native_lease_errors", []),
        "native_submit_errors": getattr(case, "native_submit_errors", []),
        "native_action_errors": getattr(case, "native_action_errors", []),
    }


def _cancel_diagnostics(case, api, reply) -> dict[str, object]:
    result = reply.get("result") if isinstance(reply, Mapping) else None
    raw_items = result.get("items") if isinstance(result, Mapping) else None
    items = ()
    if type(raw_items) is list:
        items = tuple(
            {
                "intent_id": item.get("intent_id"),
                "decision": item.get("decision"),
                "cancel_state": item.get("cancel_state"),
                "unknown_reason": item.get("unknown_reason"),
                "review_required": item.get("review_required"),
                "dispatch_attempts": item.get("dispatch_attempts"),
            }
            for item in raw_items
            if isinstance(item, Mapping)
        )
    return {
        "response_ok": reply.get("ok") if isinstance(reply, Mapping) else None,
        "response_code": reply.get("code") if isinstance(reply, Mapping) else None,
        "outcome": result.get("outcome") if isinstance(result, Mapping) else None,
        "total_count": result.get("total_count") if isinstance(result, Mapping) else None,
        "items": items,
        "native_counts": _counts(api),
        "native_lease_errors": getattr(case, "native_lease_errors", []),
        "native_action_errors": getattr(case, "native_action_errors", []),
    }


def _seed_synthetic_order_ref_cutover(case):
    """Seed a separate anchor with the established synthetic proof fixture."""

    runtime = case.provider.last_runtime
    assert runtime is not None
    store = case.resources.values["store"]
    owner = runtime._execution_owner
    session_binding = store.read_ctp_callback_session_context_facts(
        owner._owner_handle
    )[0]
    assert session_binding.session_generation_id == owner._session_generation_id

    execution_fixtures = runtime_fixtures.ctp_owner_fixtures.execution_fixtures
    anchor_intent = replace(
        execution_fixtures._intent(case.execution_scope, quantity=Decimal("1")),
        intent_id=_CUTOVER_ANCHOR_INTENT,
        signal_id=_CUTOVER_ANCHOR_INTENT + "-signal",
    )
    store.admit_intent(anchor_intent, writer_lease=runtime._writer_lease)
    proof = replace(
        execution_fixtures._order_ref_proof(
            case.execution_scope, session_binding.session_generation_id
        ),
        native_front_id=session_binding.dispatch_front_id,
        native_session_id=session_binding.dispatch_session_id,
    )
    reservation = store.seed_ctp_order_ref_and_reserve_identity(
        case.execution_scope,
        proof,
        anchor_intent.intent_id,
        execution_fixtures._runtime_order_id(anchor_intent.intent_id),
        writer_lease=runtime._writer_lease,
    )
    assert reservation.managed_intent_id == _CUTOVER_ANCHOR_INTENT
    return proof, reservation


def _capture_synthetic_entry_binding(case, monkeypatch) -> None:
    """Capture the fixture's actual public binding call for a later scoped swap."""

    client_module = runtime_fixtures.ctp_client_fixtures.client_module
    original = client_module.TraderClient.configure_runtime_simnow_credential_binding
    captured: dict[str, object] = {}

    def observe(trader, capability, binding):
        result = original(trader, capability, binding)
        if (
            binding.approval.schema_version
            == runtime_fixtures.SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION
        ):
            captured.update(
                trader=trader,
                capability=capability,
                binding=binding,
            )
        return result

    monkeypatch.setattr(
        client_module.TraderClient,
        "configure_runtime_simnow_credential_binding",
        observe,
        raising=True,
    )
    case.synthetic_entry_binding_capture = captured


def _install_synthetic_cancel_recovery_binding(
    case, cancel_id: str, expected_session_binding
):
    """Use the public client API to bind one synthetic exact-cancel grant.

    This is a test authorization leaf only. It uses the same real TraderClient,
    capability, credential owner, and session context as startup; it does not
    model the missing production per-CancelIntent approval issuer.
    """

    runtime = case.provider.last_runtime
    assert runtime is not None
    captured = case.synthetic_entry_binding_capture
    trader = runtime._trader
    entry_trader = captured.get("trader")
    capability = captured.get("capability")
    entry_binding = captured.get("binding")
    assert trader is entry_trader
    assert entry_binding is not None
    assert runtime._execution_owner._trader is trader
    owner_before = runtime._execution_owner
    gate_before = trader.get_execution_gate_state()
    # The configured managed profile stays disarmed. Its public gate state
    # therefore confirms there is no armed execution proof to replace.
    assert gate_before["armed"] is False
    assert gate_before["managed"] is True

    store = case.resources.values["store"]
    callback_binding_before = store.read_ctp_callback_session_context_facts(
        owner_before._owner_handle
    )[0]
    assert callback_binding_before == expected_session_binding
    assert callback_binding_before.session_generation_id == (
        owner_before._session_generation_id
    )

    now = datetime.now(UTC).replace(microsecond=0)
    issued = now - timedelta(seconds=1)
    expires = now + timedelta(hours=1)

    def iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    base_payload = dict(entry_binding.approval.payload)
    recovery_payload = dict(base_payload)
    recovery_payload.update(
        {
            "purpose": RECOVERY_APPROVAL_PURPOSE,
            "schema_version": SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
            "recovery_actions": [
                {
                    "action_id": cancel_id,
                    "action_kind": "cancel",
                    "instrument_id": _INSTRUMENT,
                    "exchange_id": _EXCHANGE,
                    "account_fingerprint": base_payload["account_fingerprint"],
                    "trading_day": callback_binding_before.trading_day,
                    "connection_generation": callback_binding_before.connection_generation,
                    "environment_profile": case.config.trader.ctp_env_profile,
                    "expires_at": iso(expires),
                }
            ],
            "approval_id": "ctp-runtime-chain-recovery-approval",
            "nonce": "ctp-runtime-chain-recovery-nonce",
            "issued_at": iso(issued),
            "not_before": iso(issued),
            "expires_at": iso(expires),
            "revocation_snapshot_version": 1,
        }
    )
    recovery_payload.pop("authorized_instruments", None)
    recovery_payload.pop("primary_instrument", None)
    approval = runtime_fixtures.CtpExecutionApproval(
        payload=MappingProxyType(recovery_payload),
        payload_sha256="0" * 64,
        trust_root_sha256="0" * 64,
        signature=b"synthetic-test-only",
        revocation_snapshot=MappingProxyType(
            {"version": 1, "expires_at": iso(expires)}
        ),
        _seal=runtime_fixtures._CAPABILITY_SEAL,
    )
    recovery_binding = runtime_fixtures.CtpRuntimeSimNowCredentialBinding(
        owner=entry_binding.owner,
        approval_context=entry_binding.approval_context,
        approval=approval,
        credential_binding_verifier=entry_binding.credential_binding_verifier,
        td_front=entry_binding.td_front,
        md_front=entry_binding.md_front,
        environment_profile=entry_binding.environment_profile,
        write_intent_verifier=entry_binding.write_intent_verifier,
    )

    configured = trader.configure_runtime_simnow_credential_binding(
        capability, recovery_binding
    )
    assert configured["configured"] is True
    assert configured["connection_generation"] == (
        callback_binding_before.connection_generation
    )
    gate_after = trader.get_execution_gate_state()
    assert gate_after["armed"] is False
    assert gate_after["managed"] is True
    assert gate_after["runtime_simnow_credential_binding_configured"] is True
    assert gate_after["runtime_simnow_write_verifier_configured"] is True
    assert runtime._execution_owner is owner_before
    assert runtime._execution_owner._trader is trader
    assert store.read_ctp_callback_session_context_facts(
        owner_before._owner_handle
    )[0] == callback_binding_before
    assert recovery_binding.owner is entry_binding.owner
    assert recovery_binding.approval_context is entry_binding.approval_context
    return recovery_binding


def _observe_authenticated_reserve(case, monkeypatch):
    """Observe finite bridge failures while preserving the real call path."""

    runtime = case.provider.last_runtime
    assert runtime is not None
    bridge = runtime._order_gate
    original = bridge.reserve_for_authenticated_execution
    errors: list[dict[str, object]] = []

    def observe(intent, *, admission_context, writer_lease, dispatch_port):
        try:
            return original(
                intent,
                admission_context=admission_context,
                writer_lease=writer_lease,
                dispatch_port=dispatch_port,
            )
        except Exception as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            frame = frames[-1] if frames else None
            error_code = getattr(exc, "code", None)
            errors.append(
                {
                    "exception_type": type(exc).__name__,
                    "error_code": error_code if type(error_code) is str else None,
                    "filename": Path(frame.filename).name if frame is not None else None,
                    "line": frame.lineno if frame is not None else None,
                }
            )
            raise

    monkeypatch.setattr(
        bridge, "reserve_for_authenticated_execution", observe, raising=True
    )
    return errors


def _observe_submit_authorized(case, monkeypatch) -> None:
    """Capture finite failures at each real managed-submit boundary."""

    submit_port = case.resources.values["dispatch"]
    runtime = case.provider.last_runtime
    assert runtime is not None
    trader = runtime._trader

    def finite_exception_chain(exc: BaseException) -> tuple[dict[str, object], ...]:
        chain: list[dict[str, object]] = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and len(chain) < 3 and id(current) not in seen:
            seen.add(id(current))
            frames = traceback.extract_tb(current.__traceback__)
            error_code = getattr(current, "code", None)
            reason_code = getattr(current, "reason_code", None)
            chain.append(
                {
                    "exception_type": type(current).__name__,
                    "error_code": (
                        error_code
                        if type(error_code) is str
                        and error_code.isascii()
                        and len(error_code) <= 96
                        else None
                    ),
                    "reason_code": (
                        reason_code
                        if type(reason_code) is str
                        and reason_code.isascii()
                        and len(reason_code) <= 96
                        else None
                    ),
                    "frames": tuple(
                        (Path(frame.filename).name, frame.lineno)
                        for frame in frames[-3:]
                    ),
                }
            )
            current = current.__cause__ or current.__context__
        return tuple(chain)

    native_lease_errors: list[tuple[dict[str, object], ...]] = []
    original_acquire = trader.acquire_managed_native_call_lease

    def observe_native_lease(owner_handle, binding):
        try:
            return original_acquire(owner_handle, binding)
        except Exception as exc:
            native_lease_errors.append(finite_exception_chain(exc))
            raise

    native_submit_errors: list[tuple[dict[str, object], ...]] = []
    original_native_submit = trader.submit_order_insert_with_lease

    def observe_native_submit(lease, native_field, request_id):
        try:
            return original_native_submit(lease, native_field, request_id)
        except Exception as exc:
            native_submit_errors.append(finite_exception_chain(exc))
            raise

    native_action_errors: list[tuple[dict[str, object], ...]] = []
    original_native_action = trader.submit_order_action_with_lease

    def observe_native_action(
        lease,
        native_field,
        request_id,
        *,
        runtime_action_id=None,
        managed_cancel_intent_id=None,
    ):
        try:
            return original_native_action(
                lease,
                native_field,
                request_id,
                runtime_action_id=runtime_action_id,
                managed_cancel_intent_id=managed_cancel_intent_id,
            )
        except Exception as exc:
            native_action_errors.append(finite_exception_chain(exc))
            raise

    case.native_lease_errors = native_lease_errors
    case.native_submit_errors = native_submit_errors
    case.native_action_errors = native_action_errors
    monkeypatch.setattr(
        trader, "acquire_managed_native_call_lease", observe_native_lease
    )
    monkeypatch.setattr(
        trader, "submit_order_insert_with_lease", observe_native_submit
    )
    monkeypatch.setattr(
        trader, "submit_order_action_with_lease", observe_native_action
    )

    issuer = submit_port._issuer
    original_verify = issuer.verify_action
    authority_errors: list[dict[str, object]] = []

    def observe_verify(command, *, now_ns):
        try:
            return original_verify(command, now_ns=now_ns)
        except Exception as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            frame = frames[-1] if frames else None
            error_code = getattr(exc, "code", None)
            authority_errors.append(
                {
                    "exception_type": type(exc).__name__,
                    "error_code": error_code if type(error_code) is str else None,
                    "filename": Path(frame.filename).name if frame is not None else None,
                    "line": frame.lineno if frame is not None else None,
                }
            )
            raise

    case.authority_verify_errors = authority_errors
    monkeypatch.setattr(issuer, "verify_action", observe_verify, raising=True)

    original = submit_port.submit_authorized
    errors: list[dict[str, object]] = []

    def observe(intent, authorization):
        try:
            return original(intent, authorization)
        except Exception as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            frame = frames[-1] if frames else None
            error_code = getattr(exc, "code", None)
            errors.append(
                {
                    "exception_type": type(exc).__name__,
                    "error_code": error_code if type(error_code) is str else None,
                    "filename": Path(frame.filename).name if frame is not None else None,
                    "line": frame.lineno if frame is not None else None,
                }
            )
            raise

    case.submit_authorized_errors = errors
    monkeypatch.setattr(submit_port, "submit_authorized", observe, raising=True)


def _order_arguments(intent_id: str) -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "signal_id": intent_id + "-signal",
        "instrument": _INSTRUMENT,
        "side": Side.BUY.value,
        "position_effect": PositionEffect.OPEN.value,
        "order_type": OrderType.LIMIT.value,
        "quantity": "2",
        "price": "100",
        "time_in_force": TimeInForce.DAY.value,
        "reduce_only": False,
        "metadata_version": "ctp-runtime-chain-v1",
        "deadline_ns": None,
        "tags": {},
    }


def _install_submit_callback(case, api, intent_id: str):
    runtime = case.provider.last_runtime
    assert runtime is not None
    owner = runtime._execution_owner
    facts = case.resources.values["store"].read_ctp_callback_session_context_facts(
        owner._owner_handle
    )
    session_binding = facts[0]
    callback_case = SimpleNamespace(
        trader=runtime._trader,
        scope=case.execution_scope,
        session_binding=session_binding,
    )

    def req_order_insert(field, request_id):
        api.calls.append(("ReqOrderInsert", field, request_id))
        store = case.resources.values["store"]
        intent = store.get_intent(intent_id, scope=case.execution_scope)
        reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
        assert type(intent) is OrderIntent
        assert reservation is not None
        assert field.OrderRef == reservation.order_ref
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=1,
        )
        return 0

    return callback_case, session_binding, req_order_insert


def _install_zero_fill_submit_query_callback(
    case, api, monkeypatch, callback_case, intent_id: str
):
    """Emit the exact Store-backed current order when ACK proof queries it."""

    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="synthetic query response")
    query_request_ids: list[int] = []

    def req_qry_order(field, request_id):
        api.calls.append(("ReqQryOrder", field, request_id))
        query_request_ids.append(request_id)
        store = case.resources.values["store"]
        intent = store.get_intent(intent_id, scope=case.execution_scope)
        reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
        assert type(intent) is OrderIntent and reservation is not None
        row_case = SimpleNamespace(
            api=api,
            scope=case.execution_scope,
            session_binding=callback_case.session_binding,
        )
        row = query_fixtures._native_order(
            row_case, intent=intent, reservation=reservation
        )
        api.spi.OnRspQryOrder(row, response_info, request_id, False)
        api.spi.OnRspQryOrder(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(api, "ReqQryOrder", req_qry_order, raising=False)
    return query_request_ids


def _install_cancel_callbacks(case, api, monkeypatch, callback_case, intent_id):
    store = case.resources.values["store"]
    intent = store.get_intent(intent_id, scope=case.execution_scope)
    reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
    assert type(intent) is OrderIntent
    assert reservation is not None
    query_case = SimpleNamespace(
        api=api,
        scope=case.execution_scope,
        session_binding=callback_case.session_binding,
    )
    native_row = query_fixtures._native_order(
        query_case,
        intent=intent,
        reservation=reservation,
    )
    query_fixtures._install_native_query_rows(query_case, monkeypatch, (native_row,))
    submit_calls = [call for call in api.calls if call[0] == "ReqOrderInsert"]
    assert len(submit_calls) == 1
    submit_request_id = submit_calls[0][2]

    def req_order_action(field, request_id):
        api.calls.append(("ReqOrderAction", field, request_id))
        api.spi.OnRspOrderAction(
            field,
            SimpleNamespace(ErrorID=0, ErrorMsg="synthetic CTP action ACK"),
            request_id,
            True,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            submit_request_id,
            status="5",
            submit_status="3",
            traded=0,
            sequence=2,
        )
        return 0

    monkeypatch.setattr(api, "ReqOrderAction", req_order_action, raising=False)


def _counts(api) -> tuple[int, int, int]:
    names = [call[0] for call in api.calls]
    return (
        names.count("ReqOrderInsert"),
        names.count("ReqQryOrder"),
        names.count("ReqOrderAction"),
    )


def _submit_binding(case, intent_id: str):
    store = case.resources.values["store"]
    reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
    assert reservation is not None
    command_id = stable_managed_command_id(
        "submit", intent_id, reservation.runtime_order_id
    )
    command = store.read_ctp_dispatch_command(case.execution_scope, command_id)
    assert command is not None
    return reservation, command_id, command


@pytest.mark.unit
def test_runtime_built_service_submits_queries_and_cancels_once_on_the_same_ctp_owner(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        recovery_provider = install_synthetic_cancel_recovery_provider(
            case, monkeypatch
        )
        component = _build_owner_control_execution_component(case, monkeypatch)
        _seed_synthetic_order_ref_cutover(case)
        reserve_errors = _observe_authenticated_reserve(case, monkeypatch)
        _observe_submit_authorized(case, monkeypatch)
        resources = case.resources
        runtime = case.provider.last_runtime
        assert resources is not None and runtime is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-chain-cancel"
        callback_case, session_binding, req_order_insert = _install_submit_callback(
            case, api, intent_id
        )
        monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
        submit_query_request_ids = _install_zero_fill_submit_query_callback(
            case, api, monkeypatch, callback_case, intent_id
        )
        context = _service_context(case)

        submitted = component.handlers["orders.submit"](
            context, _order_arguments(intent_id)
        )
        assert reserve_errors == [], reserve_errors
        assert case.authority_verify_errors == [], (
            f"authority_verify_errors={case.authority_verify_errors!r}"
        )
        assert case.native_lease_errors == [], (
            f"native_lease_errors={case.native_lease_errors!r}"
        )
        assert case.native_submit_errors == [], (
            f"native_submit_errors={case.native_submit_errors!r}"
        )
        assert case.submit_authorized_errors == [], (
            f"submit_authorized_errors={case.submit_authorized_errors!r}"
        )
        assert submitted["ok"] is True, _submit_diagnostics(
            case, api, intent_id, submitted
        )
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value, (
            _submit_diagnostics(case, api, intent_id, submitted)
        )
        assert len(submit_query_request_ids) == 1
        assert _counts(api) == (1, 1, 0)
        reservation, submit_command_id, submit_command = _submit_binding(case, intent_id)
        assert submit_command.operation == "SUBMIT"
        assert submit_command.status == "COMPLETED"
        assert submit_command.local_queue_receipt_queued is True

        queried = component.handlers["orders.query"](
            context, {"intent_id": intent_id}
        )
        assert queried["ok"] is True
        assert queried["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert _counts(api) == (1, 1, 0)

        bridge = resources.values["admission"]["order_gate"]
        risk_binding_before = bridge.read_dispatch_binding(_PRINCIPAL, intent_id)
        assert risk_binding_before is not None
        assert risk_binding_before["submit_command_id"] == submit_command_id
        assert risk_binding_before["managed_intent_id"] == intent_id
        assert risk_binding_before["runtime_order_id"] == reservation.runtime_order_id
        assert risk_binding_before["order_ref"] == reservation.order_ref
        assert risk_binding_before["native_request_id"] == (
            submit_command.correlation_key.native_request_id
        )
        assert runtime._execution_owner is resources.values["dispatch"]._owner
        assert runtime._execution_owner is resources.values["cancel_dispatch"]._owner
        submit_risk_receipt = case.risk_gate.dispatch_resolution_receipt(
            risk_binding_before["permit_id"]
        )
        assert submit_risk_receipt.status is (
            DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        )
        assert submit_risk_receipt.dispatch_latch_active is False
        assert Decimal(
            case.risk_gate.snapshot(
                resources.values["admission"]["order_gate"].account_scope
            )["increase_notional"]
        ) == Decimal("200")

        _install_cancel_callbacks(
            case, api, monkeypatch, callback_case, intent_id
        )
        cancel_risk_trace = []
        for method_name in (
            "read_cancel_resolution_target_binding",
            "has_resolved_ack_history",
            "create_cancel_dispatch_resolution_proof",
            "verify_cancel_dispatch_resolution_source",
        ):
            original_method = getattr(case.journal, method_name)

            def observe_journal(*args, _name=method_name, _method=original_method, **kwargs):
                try:
                    value = _method(*args, **kwargs)
                except Exception as error:
                    code = getattr(error, "code", None)
                    if not (type(code) is str and len(code) <= 128 and code.replace("_", "").isalnum()):
                        code = None
                    cancel_risk_trace.append((_name, "error", type(error).__name__, code))
                    raise
                cancel_risk_trace.append((_name, "return", value if type(value) is bool else type(value).__name__))
                return value

            monkeypatch.setattr(case.journal, method_name, observe_journal)
        expected_cancel_id = owner_cancel_id(
            command_id="ctp-runtime-owner-cancel-batch",
            intent_id="ctp-runtime-chain-cancel",
        )
        assert recovery_provider.requests == []
        cancelled = component.handlers["owner.cancel"](
            _owner_control_context(case),
            {"command_id": "ctp-runtime-owner-cancel-batch"},
        )
        _assert_cancel_recovery_hook(
            recovery_provider.cancel_hook_observation,
            returns=1,
            case=case,
            provider=recovery_provider,
            provider_requests=1,
            quota_calls=1,
            quota_returns=1,
        )
        assert len(recovery_provider.requests) == 1
        assert (
            recovery_provider.requests[0].cancel_intent.cancel_id
            == expected_cancel_id
        )
        assert (
            recovery_provider.requests[0].prepared_target.session_generation_id
            == session_binding.session_generation_id
        )
        assert case.native_lease_errors == [], case.native_lease_errors
        assert case.native_action_errors == [], case.native_action_errors
        assert cancelled["ok"] is True, _cancel_diagnostics(case, api, cancelled)
        assert cancelled["result"]["outcome"] == "APPLIED"
        assert cancelled["result"]["total_count"] == 1
        item = cancelled["result"]["items"][0]
        assert item["intent_id"] == intent_id
        assert item["cancel_id"] == expected_cancel_id
        assert item["decision"] == "READY"
        assert item["cancel_state"] == ExecutionState.CANCELLED.value
        assert item["dispatch_attempts"] == 1
        assert len(case.cancel_quota_calls) == 1
        quota_args = case.cancel_quota_calls[0]
        assert quota_args[:5] == (
            case.execution_scope.account_ref,
            case.execution_scope.strategy_id,
            _RUN_ID,
            _INSTRUMENT,
            "CANCEL",
        )
        assert type(quota_args[5]) is int and quota_args[5] > 0
        assert _counts(api) == (1, 3, 1)
        cancel_id = item["cancel_id"]
        cancel_record = resources.values["store"].get_cancel(
            cancel_id, scope=case.execution_scope
        )
        assert cancel_record is not None
        assert cancel_record.dispatch_attempts == 1
        assert cancel_record.review_required is False, cancel_risk_trace
        cancel_command_id = stable_managed_command_id(
            "cancel", intent_id, reservation.runtime_order_id, cancel_id
        )
        cancel_command = resources.values["store"].read_ctp_dispatch_command(
            case.execution_scope, cancel_command_id
        )
        assert cancel_command is not None
        assert cancel_command.operation == "CANCEL"
        assert cancel_command.status == "COMPLETED"
        action_call = next(call for call in api.calls if call[0] == "ReqOrderAction")
        assert action_call[1].OrderActionRef == (
            cancel_command.correlation_key.native_action_ref
        )
        assert action_call[2] == cancel_command.correlation_key.native_request_id
        assert cancel_command.local_queue_receipt_queued is True
        cancel_risk_receipt = case.risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert cancel_risk_receipt.status is (
            CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED
        )
        assert cancel_risk_receipt.dispatch_latch_active is False
        assert submit_risk_receipt.has_other_active_freezes is False
        assert case.risk_gate.dispatch_resolution_receipt(
            risk_binding_before["permit_id"]
        ) == submit_risk_receipt
        assert risk_binding_before == bridge.read_dispatch_binding(_PRINCIPAL, intent_id)

        replay = component.handlers["owner.cancel"](
            _owner_control_context(case),
            {"command_id": "ctp-runtime-owner-cancel-batch"},
        )
        assert replay == cancelled
        assert _counts(api) == (1, 3, 1)
        assert len(case.cancel_quota_calls) == 1
        assert session_binding.dispatch_front_id == 7
        assert session_binding.dispatch_session_id == 19


@pytest.mark.unit
def test_runtime_built_service_persists_real_trade_callback_without_inventing_fees(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        _seed_synthetic_order_ref_cutover(case)
        reserve_errors = _observe_authenticated_reserve(case, monkeypatch)
        _observe_submit_authorized(case, monkeypatch)
        resources = case.resources
        runtime = case.provider.last_runtime
        assert resources is not None and runtime is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-chain-partial-fill"
        callback_case, _session_binding, req_order_insert = _install_submit_callback(
            case, api, intent_id
        )
        monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
        submit_query_request_ids = _install_zero_fill_submit_query_callback(
            case, api, monkeypatch, callback_case, intent_id
        )
        context = _service_context(case)

        arguments = _order_arguments(intent_id)
        arguments["price"] = "49999"
        submitted = component.handlers["orders.submit"](
            context, arguments
        )
        assert reserve_errors == [], reserve_errors
        assert case.authority_verify_errors == [], (
            f"authority_verify_errors={case.authority_verify_errors!r}"
        )
        assert case.native_lease_errors == [], (
            f"native_lease_errors={case.native_lease_errors!r}"
        )
        assert case.native_submit_errors == [], (
            f"native_submit_errors={case.native_submit_errors!r}"
        )
        assert case.submit_authorized_errors == [], (
            f"submit_authorized_errors={case.submit_authorized_errors!r}"
        )
        assert submitted["ok"] is True, _submit_diagnostics(
            case, api, intent_id, submitted
        )
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value, (
            _submit_diagnostics(case, api, intent_id, submitted)
        )
        assert len(submit_query_request_ids) == 1
        reservation, command_id, command = _submit_binding(case, intent_id)
        intent = resources.values["store"].get_intent(
            intent_id, scope=case.execution_scope
        )
        assert type(intent) is OrderIntent

        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="RUNTIMEFAKE-TRADE-001",
            sequence=1,
            volume=1,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            next(call[2] for call in api.calls if call[0] == "ReqOrderInsert"),
            status="1",
            submit_status="1",
            traded=1,
            sequence=2,
        )
        applied = runtime._execution_owner.pump_callbacks(32)
        assert len(applied) == 2
        assert type(applied[0]) is CtpDispatchTradeFactApplyResult
        assert applied[0].trade_quantity == 1
        assert applied[0].cumulative_trade_quantity == 1
        assert applied[0].duplicate is False
        assert type(applied[1]) is CtpDispatchCallbackApplyResult
        assert applied[1].projection_state == "PARTIALLY_FILLED"

        queried = component.handlers["orders.query"](
            context, {"intent_id": intent_id}
        )
        assert queried["ok"] is True
        # This is the public ExecutionRecord read path, not callback
        # reconciliation. Pumping the real CTP inbox updates the CTP Store
        # snapshot below; the service query does not mutate this record.
        assert queried["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert _counts(api) == (1, 1, 0)

        facts = resources.values["store"].read_ctp_dispatch_trade_facts(
            case.execution_scope, command_id
        )
        assert len(facts.trades) == 1
        trade = facts.trades[0]
        assert trade.trade_id == "RUNTIMEFAKE-TRADE-001"
        assert trade.trade_quantity == 1
        assert trade.cumulative_trade_quantity == 1
        assert trade.trade_price == Decimal("49999")
        assert trade.cumulative_commission is None
        assert trade.commission_quality == "INCOMPLETE"
        snapshot = resources.values["store"].read_ctp_owner_callback_observation_snapshot(
            case.execution_scope, command_id
        )
        assert snapshot.provider_state == "PARTIALLY_FILLED"
        assert snapshot.filled_quantity == 1
        assert snapshot.trade_fact_count == 1
        assert snapshot.trade_notional == Decimal("49999")
        assert snapshot.cumulative_commission is None
        assert snapshot.commission_quality == "INCOMPLETE"

        bridge = resources.values["admission"]["order_gate"]
        risk_binding = bridge.read_dispatch_binding(_PRINCIPAL, intent_id)
        assert risk_binding is not None
        assert risk_binding["submit_command_id"] == command.command_id
        assert risk_binding["runtime_order_id"] == reservation.runtime_order_id
        assert risk_binding["order_ref"] == reservation.order_ref
        submit_risk_receipt = case.risk_gate.dispatch_resolution_receipt(
            risk_binding["permit_id"]
        )
        assert submit_risk_receipt.status is (
            DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        )
        assert submit_risk_receipt.dispatch_latch_active is False

        # The zero-fill proof clears the dispatch latch, but the full accepted
        # reservation remains counted. This second order exceeds that real
        # remaining notional capacity and must be denied before native send.
        assert Decimal(
            case.risk_gate.snapshot(
                resources.values["admission"]["order_gate"].account_scope
            )["increase_notional"]
        ) == Decimal("99998")
        denied = component.handlers["orders.submit"](
            context, _order_arguments("ctp-runtime-chain-increase-while-reserved")
        )
        assert denied["ok"] is True
        assert denied["code"] == "OK"
        assert denied["result"]["record"]["state"] == ExecutionState.REJECTED.value
        assert denied["result"]["record"]["unknown_reason"] == "admission_denied"
        assert denied["result"]["record"]["dispatch_attempts"] == 0
        assert denied["result"]["record"]["provider_order_id"] is None
        assert _counts(api) == (1, 1, 0)
        assert case.risk_gate.dispatch_resolution_receipt(
            risk_binding["permit_id"]
        ) == submit_risk_receipt

