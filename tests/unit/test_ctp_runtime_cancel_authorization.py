"""Focused source-fake coverage for CTP runtime cancel authorization.

The runtime, TraderClient, Store, Risk gates, cancellation facade, and managed
cancel port remain the real composed objects.  The provider below is an
explicit synthetic authorization leaf derived from the fixture's already
configured ENTRY binding; it is not a production approval issuer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import bt_api_py.ctp_execution_runtime as runtime_module
from bt_api_execution import ExecutionState
from bt_api_risk import DispatchResolutionReceiptStatus
from bt_api_py._ctp_execution_authorization import (
    APPROVAL_PURPOSE,
    RECOVERY_APPROVAL_PURPOSE,
    SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
)


_CANCEL_RECOVERY_HOOK_CODES = frozenset(
    {
        "CANCEL_RECOVERY_RUNTIME_UNAVAILABLE",
        "CANCEL_RECOVERY_SESSION_UNAVAILABLE",
        "CANCEL_RECOVERY_SESSION_MISMATCH",
        "CANCEL_RECOVERY_TARGET_MISMATCH",
        "CANCEL_RECOVERY_BINDING_MISMATCH",
        "CANCEL_RECOVERY_SCOPE_MISMATCH",
        "CANCEL_RECOVERY_ACTION_MISMATCH",
        "CANCEL_RECOVERY_PROVIDER_UNAVAILABLE",
        "CANCEL_RECOVERY_SETUP_UNAVAILABLE",
        "CANCEL_RECOVERY_PROVIDER_FAILED",
        "CANCEL_RECOVERY_CONTEXT_CHANGED",
        "CANCEL_RECOVERY_BINDING_REJECTED",
    }
)
_CANCEL_RECOVERY_HOOK_ERROR_TYPES = frozenset(
    {
        "AttributeError",
        "AssertionError",
        "KeyError",
        "RuntimeError",
        "TypeError",
        "ValueError",
    }
)


def _own_cancel_hook_frames(error):
    """Keep at most three public runtime/port frame names and line numbers."""

    frames = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        filename = frame.f_code.co_filename.replace("\\", "/")
        if filename.endswith(
            ("/bt_api_py/ctp_execution_runtime.py", "/bt_api_py/ctp_managed_cancel_port.py")
        ):
            frames.append((frame.f_code.co_name, int(traceback.tb_lineno)))
        traceback = traceback.tb_next
    return frames[-3:]


def _observe_runtime_cancel_recovery_hook(monkeypatch):
    """Capture only bounded hook calls and finite own exception facts."""

    runtime_type = runtime_module.CtpExecutionRuntime
    original = runtime_type._prepare_cancel_recovery_binding
    observation = {"calls": 0, "returns": 0, "errors": [], "error_frames": []}

    def observe(runtime, intent):
        observation["calls"] += 1
        try:
            result = original(runtime, intent)
        except Exception as error:
            if type(error) is runtime_module.CtpExecutionRuntimeError:
                error_type = "CtpExecutionRuntimeError"
                code = error.code
                if type(code) is not str or code not in _CANCEL_RECOVERY_HOOK_CODES:
                    code = "OTHER_CTP_RECOVERY_CODE"
            else:
                candidate_type = type(error).__name__
                error_type = (
                    candidate_type
                    if candidate_type in _CANCEL_RECOVERY_HOOK_ERROR_TYPES
                    else "OTHER_EXCEPTION"
                )
                code = None
            observation["errors"].append((error_type, code))
            observation["error_frames"].append(
                _own_cancel_hook_frames(error) if code is None else []
            )
            raise
        observation["returns"] += 1
        return result

    monkeypatch.setattr(
        runtime_type,
        "_prepare_cancel_recovery_binding",
        observe,
        raising=True,
    )
    return observation


def _assert_cancel_recovery_hook(
    observation,
    *,
    returns,
    errors=(),
    case=None,
    provider=None,
    provider_requests=None,
    quota_calls=None,
    quota_returns=None,
):
    facts = {"hook": observation}
    request_count = 0 if provider is None else len(provider.requests)
    facts["provider_requests"] = request_count
    if provider_requests is not None:
        assert request_count == provider_requests, facts
    if case is not None:
        calls = len(getattr(case, "cancel_quota_calls", ()))
        returned = len(getattr(case, "cancel_quota_returns", ()))
        facts["quota"] = {"calls": calls, "returned": returned}
        if quota_calls is not None:
            assert calls == quota_calls, facts
        if quota_returns is not None:
            assert returned == quota_returns, facts
    assert observation["calls"] == 1, facts
    assert observation["returns"] == returns, facts
    assert observation["errors"] == list(errors), facts


def _iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _synthetic_recovery_binding(initial_binding, request, *, binding_fault=None):
    """Build a sealed test binding from the exact startup ENTRY authority."""

    payload = dict(initial_binding.approval.payload)
    now = datetime.now(UTC).replace(microsecond=0)
    issued_at = now - timedelta(seconds=1)
    expires_at = now + timedelta(hours=1)
    action_id = request.cancel_intent.cancel_id
    instrument_id = request.prepared_target.instrument_id
    if binding_fault == "wrong_action_id":
        action_id += "-wrong"
    elif binding_fault == "wrong_target":
        instrument_id += "-wrong"
    elif binding_fault is not None:
        raise ValueError("unsupported synthetic cancel-binding fault")

    payload.update(
        {
            "purpose": RECOVERY_APPROVAL_PURPOSE,
            "schema_version": SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
            "recovery_actions": [
                {
                    "action_id": action_id,
                    "action_kind": "cancel",
                    "instrument_id": instrument_id,
                    "exchange_id": request.prepared_target.exchange_id,
                    "account_fingerprint": request.account_fingerprint,
                    "trading_day": request.trading_day,
                    "connection_generation": request.connection_generation,
                    "environment_profile": request.environment_profile,
                    "expires_at": _iso_utc(expires_at),
                }
            ],
            "approval_id": "ctp-runtime-cancel-auth-synthetic-approval",
            "nonce": "ctp-runtime-cancel-auth-synthetic-nonce",
            "issued_at": _iso_utc(issued_at),
            "not_before": _iso_utc(issued_at),
            "expires_at": _iso_utc(expires_at),
            "revocation_snapshot_version": 1,
        }
    )
    payload.pop("authorized_instruments", None)
    payload.pop("primary_instrument", None)

    approval = runtime_fixtures.CtpExecutionApproval(
        payload=MappingProxyType(payload),
        payload_sha256="0" * 64,
        trust_root_sha256="0" * 64,
        signature=b"synthetic-test-only",
        revocation_snapshot=MappingProxyType(
            {"version": 1, "expires_at": _iso_utc(expires_at)}
        ),
        _seal=runtime_fixtures._CAPABILITY_SEAL,
    )
    return runtime_fixtures.CtpRuntimeSimNowCredentialBinding(
        owner=initial_binding.owner,
        approval_context=initial_binding.approval_context,
        approval=approval,
        credential_binding_verifier=initial_binding.credential_binding_verifier,
        td_front=initial_binding.td_front,
        md_front=initial_binding.md_front,
        environment_profile=initial_binding.environment_profile,
        write_intent_verifier=initial_binding.write_intent_verifier,
    )


class _SyntheticCancelRecoveryBindingProvider:
    """Test-only resolver; production per-CancelIntent issuance is not modeled."""

    def __init__(self, startup_capture, configuration_calls, *, binding_fault=None):
        self._startup_capture = startup_capture
        self.configuration_calls = configuration_calls
        self.binding_fault = binding_fault
        self.requests = []
        self.returned_bindings = []
        self.event_order = []
        self.setup = None

    def resolve_verified_binding(self, request):
        self.event_order.append("provider_resolve")
        self.requests.append(request)
        setup = self.setup
        if type(setup) is not runtime_module.CtpRuntimeCredentialBindingSetupV1:
            raise AssertionError("fixture did not retain the startup credential binding setup")
        binding = _synthetic_recovery_binding(
            setup.initial_binding,
            request,
            binding_fault=self.binding_fault,
        )
        self.returned_bindings.append(binding)
        return binding


def install_synthetic_cancel_recovery_provider(
    case, monkeypatch, *, binding_fault=None
):
    """Install the synthetic resolver and make startup return the exact setup."""

    if binding_fault not in {None, "wrong_action_id", "wrong_target"}:
        raise ValueError("unsupported synthetic cancel-binding fault")

    hook_observation = _observe_runtime_cancel_recovery_hook(monkeypatch)
    client_module = runtime_fixtures.ctp_client_fixtures.client_module
    original_configure = (
        client_module.TraderClient.configure_runtime_simnow_credential_binding
    )
    startup_capture = {}
    configuration_calls = []
    provider = _SyntheticCancelRecoveryBindingProvider(
        startup_capture,
        configuration_calls,
        binding_fault=binding_fault,
    )
    provider.cancel_hook_observation = hook_observation

    def observe_configure(trader, capability, binding):
        before = trader.get_execution_gate_state()
        result = original_configure(trader, capability, binding)
        after = trader.get_execution_gate_state()
        configuration_calls.append(
            {
                "trader": trader,
                "capability": capability,
                "binding": binding,
                "before": before,
                "after": after,
            }
        )
        payload = getattr(getattr(binding, "approval", None), "payload", {})
        if (
            isinstance(payload, Mapping)
            and payload.get("schema_version")
            == runtime_fixtures.SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION
        ):
            startup_capture.update(
                trader=trader,
                capability=capability,
                binding=binding,
            )
        return result

    monkeypatch.setattr(
        client_module.TraderClient,
        "configure_runtime_simnow_credential_binding",
        observe_configure,
        raising=True,
    )

    original_prepare = case.config.prepare_trader

    def prepare_trader(trader):
        original_prepare(trader)
        if startup_capture.get("trader") is not trader:
            raise AssertionError("startup ENTRY binding was not captured for this Trader")
        setup = runtime_module.CtpRuntimeCredentialBindingSetupV1(
            capability=startup_capture["capability"],
            initial_binding=startup_capture["binding"],
        )
        provider.setup = setup
        return setup

    configured = replace(
        case.config,
        prepare_trader=prepare_trader,
        cancel_recovery_binding_provider=provider,
    )
    case.config = configured
    case.provider.config = configured
    case.cancel_auth_event_order = provider.event_order
    return provider


def _install_cancel_dispatch_observers(case, monkeypatch, *, api, event_order):
    resources = case.resources
    assert resources is not None
    cancel_port = resources.values["cancel_dispatch"]
    original_prepare_target = cancel_port.prepare_target

    def observe_prepare_target(intent):
        facts = original_prepare_target(intent)
        event_order.append("target_prepared")
        return facts

    monkeypatch.setattr(cancel_port, "prepare_target", observe_prepare_target)

    original_refresh_target = cancel_port.refresh_target

    def observe_refresh_target(intent):
        facts = original_refresh_target(intent)
        event_order.append("target_refreshed")
        return facts

    monkeypatch.setattr(cancel_port, "refresh_target", observe_refresh_target)

    cancel_gate = resources.values["admission"]["cancel_gate"]
    original_claim = cancel_gate.claim_before_dispatch

    def observe_claim(*args, **kwargs):
        event_order.append("risk_claim")
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(cancel_gate, "claim_before_dispatch", observe_claim)

    original_action = api.ReqOrderAction

    def observe_action(*args, **kwargs):
        event_order.append("native_action")
        return original_action(*args, **kwargs)

    monkeypatch.setattr(api, "ReqOrderAction", observe_action, raising=True)


def _submit_acknowledged_order(
    case, chain, component, monkeypatch, api, intent_id, *, price="100"
):
    callback_case, session_binding, req_order_insert = chain._install_submit_callback(
        case, api, intent_id
    )
    monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
    chain._install_zero_fill_submit_query_callback(
        case, api, monkeypatch, callback_case, intent_id
    )
    arguments = chain._order_arguments(intent_id)
    arguments["price"] = price
    submitted = component.handlers["orders.submit"](
        chain._service_context(case), arguments
    )
    assert submitted["ok"] is True
    assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
    return callback_case, session_binding


def _install_cancel_callbacks(case, chain, monkeypatch, api, callback_case, intent_id):
    chain._install_cancel_callbacks(
        case, api, monkeypatch, callback_case, intent_id
    )


@pytest.mark.unit
def test_runtime_cancel_resolves_exact_persisted_recovery_authority_and_replays_once(
    tmp_path, monkeypatch
):
    import test_ctp_runtime_order_chain as chain

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        provider = install_synthetic_cancel_recovery_provider(case, monkeypatch)
        component = chain._build_owner_control_execution_component(case, monkeypatch)
        chain._seed_synthetic_order_ref_cutover(case)
        chain._observe_authenticated_reserve(case, monkeypatch)
        chain._observe_submit_authorized(case, monkeypatch)
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-cancel-auth-positive"
        callback_case, session_binding = _submit_acknowledged_order(
            case, chain, component, monkeypatch, api, intent_id, price="49999"
        )
        reservation, _submit_command_id, _submit_command = chain._submit_binding(
            case, intent_id
        )
        bridge = resources.values["admission"]["order_gate"]
        submit_binding = bridge.read_dispatch_binding(chain._PRINCIPAL, intent_id)
        assert submit_binding is not None
        submit_receipt = case.risk_gate.dispatch_resolution_receipt(
            submit_binding["permit_id"]
        )
        assert submit_receipt.status is (
            DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        )
        assert submit_receipt.dispatch_latch_active is False
        assert Decimal(
            case.risk_gate.snapshot(
                resources.values["admission"]["order_gate"].account_scope
            )["increase_notional"]
        ) == Decimal("99998")

        _install_cancel_callbacks(
            case, chain, monkeypatch, api, callback_case, intent_id
        )
        _install_cancel_dispatch_observers(
            case,
            monkeypatch,
            api=api,
            event_order=provider.event_order,
        )
        cancel_command_id = "ctp-runtime-cancel-auth-command"
        cancelled = component.handlers["owner.cancel"](
            chain._owner_control_context(case), {"command_id": cancel_command_id}
        )

        _assert_cancel_recovery_hook(
            provider.cancel_hook_observation,
            returns=1,
            case=case,
            provider=provider,
            provider_requests=1,
            quota_calls=1,
            quota_returns=1,
        )
        assert cancelled["ok"] is True
        assert cancelled["result"]["outcome"] == "APPLIED"
        assert cancelled["result"]["total_count"] == 1
        item = cancelled["result"]["items"][0]
        assert item["decision"] == "READY"
        assert item["cancel_state"] == ExecutionState.CANCELLED.value
        assert item["dispatch_attempts"] == 1
        assert len(provider.requests) == 1
        request = provider.requests[0]
        persisted_intent = resources.values["store"].get_cancel_intent(
            item["cancel_id"], scope=case.execution_scope
        )
        cancel_record = resources.values["store"].get_cancel(
            item["cancel_id"], scope=case.execution_scope
        )
        assert persisted_intent is not None and cancel_record is not None
        assert request.cancel_intent == persisted_intent
        assert request.cancel_intent.cancel_id == cancel_record.cancel_id
        assert request.cancel_intent.target_intent_id == cancel_record.target_intent_id
        assert request.cancel_intent.provider_order_id == cancel_record.provider_order_id
        assert request.cancel_intent.scope == case.execution_scope
        target = request.prepared_target
        assert target.cancel_id == cancel_record.cancel_id
        assert target.target_intent_id == cancel_record.target_intent_id
        assert target.provider_order_id == cancel_record.provider_order_id
        assert target.account_key == case.execution_scope.account_key
        assert target.scope_key == case.execution_scope.key
        assert target.trading_day == case.execution_scope.trading_day
        assert target.runtime_order_id == reservation.runtime_order_id
        assert target.order_ref == reservation.order_ref
        assert target.instrument_id == "rb2610"
        assert target.exchange_id == "SHFE"
        assert target.session_generation_id == session_binding.session_generation_id
        assert request.account_key == case.execution_scope.account_key
        assert request.scope_key == case.execution_scope.key
        assert request.trading_day == case.execution_scope.trading_day
        assert request.session_generation_id == session_binding.session_generation_id
        assert request.callback_owner_intent_id == session_binding.owner_intent_id
        assert request.connection_generation == session_binding.connection_generation
        assert request.environment_profile == case.config.trader.ctp_env_profile
        assert request.td_front == case.config.trader.front
        assert request.md_front == case.config.trader.md_front
        assert request.account_fingerprint == provider.returned_bindings[0].approval.payload[
            "account_fingerprint"
        ]
        action = provider.returned_bindings[0].approval.payload["recovery_actions"][0]
        assert action["action_id"] == cancel_record.cancel_id
        assert action["instrument_id"] == target.instrument_id
        assert action["exchange_id"] == target.exchange_id
        assert provider.event_order == [
            "target_prepared",
            "provider_resolve",
            "target_prepared",
            "target_refreshed",
            "risk_claim",
            "native_action",
        ]

        setup = provider.setup
        assert type(setup) is runtime_module.CtpRuntimeCredentialBindingSetupV1
        assert setup.initial_binding is provider._startup_capture["binding"]
        trader = runtime._trader
        assert trader is provider._startup_capture["trader"]
        recovery_configurations = [
            call
            for call in provider.configuration_calls
            if call["binding"].approval.payload.get("schema_version")
            == SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION
        ]
        assert len(recovery_configurations) == 1
        recovery_configuration = recovery_configurations[0]
        assert recovery_configuration["trader"] is trader
        assert recovery_configuration["capability"] is setup.capability
        assert recovery_configuration["binding"] is provider.returned_bindings[0]
        assert recovery_configuration["before"]["armed"] is False
        assert recovery_configuration["after"]["armed"] is False

        # RECOVERY is scoped to this cancellation.  The production owner must
        # restore the exact startup ENTRY binding only after the terminal
        # callback has been read back from Store.
        assert provider.configuration_calls[-1]["trader"] is trader
        assert provider.configuration_calls[-1]["capability"] is setup.capability
        assert provider.configuration_calls[-1]["binding"] is setup.initial_binding
        assert (
            setup.initial_binding.approval.payload["purpose"]
            == APPROVAL_PURPOSE
        )
        assert runtime.is_ready() is True
        assert resources.ready() is True

        replay = component.handlers["owner.cancel"](
            chain._owner_control_context(case), {"command_id": cancel_command_id}
        )
        assert replay == cancelled
        assert len(provider.requests) == 1
        assert provider.event_order == [
            "target_prepared",
            "provider_resolve",
            "target_prepared",
            "target_refreshed",
            "risk_claim",
            "native_action",
        ]
        assert chain._counts(api) == (1, 3, 1)
        assert cancel_record.dispatch_attempts == 1

        retry_intent_id = "ctp-runtime-cancel-auth-follow-up-submit"
        denied = component.handlers["orders.submit"](
            chain._service_context(case), chain._order_arguments(retry_intent_id)
        )
        assert denied["ok"] is True
        assert denied["code"] == "OK"
        assert denied["result"]["record"]["state"] == ExecutionState.REJECTED.value
        assert denied["result"]["record"]["unknown_reason"] == "admission_denied"
        assert denied["result"]["record"]["dispatch_attempts"] == 0
        assert denied["result"]["record"]["provider_order_id"] is None
        assert case.risk_gate.dispatch_resolution_receipt(
            submit_binding["permit_id"]
        ).dispatch_latch_active is False
        assert runtime.is_ready() is True
        assert resources.ready() is True
        assert provider.configuration_calls[-1]["binding"] is setup.initial_binding
        assert (
            setup.initial_binding.approval.payload["purpose"]
            == APPROVAL_PURPOSE
        )
        assert Decimal(
            case.risk_gate.snapshot(
                resources.values["admission"]["order_gate"].account_scope
            )["increase_notional"]
        ) == Decimal("99998")
        assert chain._counts(api) == (1, 3, 1)


@pytest.mark.unit
def test_runtime_cancel_risk_claim_failure_after_recovery_stays_unready(
    tmp_path, monkeypatch
):
    import test_ctp_runtime_order_chain as chain
    from bt_api_risk import IntentAction, PermitInvalidError

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        provider = install_synthetic_cancel_recovery_provider(case, monkeypatch)
        component = chain._build_owner_control_execution_component(case, monkeypatch)
        chain._seed_synthetic_order_ref_cutover(case)
        chain._observe_authenticated_reserve(case, monkeypatch)
        chain._observe_submit_authorized(case, monkeypatch)
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-cancel-auth-risk-claim-failure"
        callback_case, _session_binding = _submit_acknowledged_order(
            case, chain, component, monkeypatch, api, intent_id
        )
        _install_cancel_callbacks(
            case, chain, monkeypatch, api, callback_case, intent_id
        )
        event_order = provider.event_order
        _install_cancel_dispatch_observers(
            case, monkeypatch, api=api, event_order=event_order
        )

        risk_claim_attempts = []

        def expire_cancel_permit_before_claim(permit_reference, risk_intent):
            risk_claim_attempts.append((permit_reference, risk_intent))
            assert risk_intent.action is IntentAction.CANCEL
            # Model the typed Risk expiry returned by the real durable gate at
            # its claim boundary.  The adapter performs its normal no-claim
            # readback and the facade stops before entering the native port.
            raise PermitInvalidError(
                "PERMIT_NOT_ACTIVE", "synthetic cancel permit expired"
            )

        monkeypatch.setattr(
            case.risk_gate,
            "claim_for_dispatch",
            expire_cancel_permit_before_claim,
            raising=True,
        )
        cancelled = component.handlers["owner.cancel"](
            chain._owner_control_context(case),
            {"command_id": "ctp-runtime-cancel-auth-risk-claim-failure-command"},
        )

        _assert_cancel_recovery_hook(
            provider.cancel_hook_observation,
            returns=1,
            case=case,
            provider=provider,
            provider_requests=1,
            quota_calls=1,
            quota_returns=1,
        )
        assert cancelled["ok"] is True
        assert len(provider.requests) == 1
        assert len(provider.returned_bindings) == 1
        assert len(risk_claim_attempts) == 1
        cancel_id = provider.requests[0].cancel_intent.cancel_id
        cancel_record = resources.values["store"].get_cancel(
            cancel_id, scope=case.execution_scope
        )
        assert cancel_record is not None
        assert cancel_record.state is ExecutionState.BLOCKED
        assert cancel_record.unknown_reason == "cancel_admission_claim_not_created"
        assert cancel_record.dispatch_attempts == 1
        assert cancel_record.review_required is True
        assert provider.configuration_calls[-1]["binding"] is provider.returned_bindings[0]
        assert provider.configuration_calls[-1]["binding"] is not provider.setup.initial_binding
        assert runtime.is_ready() is False
        assert resources.ready() is False
        assert event_order == [
            "target_prepared",
            "provider_resolve",
            "target_prepared",
            "target_refreshed",
            "risk_claim",
        ]
        assert "native_action" not in event_order
        assert chain._counts(api) == (1, 3, 0)


@pytest.mark.unit
def test_runtime_cancel_without_recovery_provider_blocks_before_risk_claim_and_native(
    tmp_path, monkeypatch
):
    import test_ctp_runtime_order_chain as chain

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        config = replace(case.config, cancel_recovery_binding_provider=None)
        case.config = config
        case.provider.config = config
        hook_observation = _observe_runtime_cancel_recovery_hook(monkeypatch)
        event_order = []
        component = chain._build_owner_control_execution_component(case, monkeypatch)
        chain._seed_synthetic_order_ref_cutover(case)
        chain._observe_authenticated_reserve(case, monkeypatch)
        chain._observe_submit_authorized(case, monkeypatch)
        resources = case.resources
        assert resources is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-cancel-auth-missing-provider"
        callback_case, _session_binding = _submit_acknowledged_order(
            case, chain, component, monkeypatch, api, intent_id
        )
        _install_cancel_callbacks(
            case, chain, monkeypatch, api, callback_case, intent_id
        )
        _install_cancel_dispatch_observers(
            case, monkeypatch, api=api, event_order=event_order
        )

        reply = component.handlers["owner.cancel"](
            chain._owner_control_context(case),
            {"command_id": "ctp-runtime-cancel-auth-missing-command"},
        )

        _assert_cancel_recovery_hook(
            hook_observation,
            returns=0,
            errors=[
                (
                    "CtpExecutionRuntimeError",
                    "CANCEL_RECOVERY_PROVIDER_UNAVAILABLE",
                )
            ],
            case=case,
            provider=None,
            provider_requests=0,
            quota_calls=1,
            quota_returns=1,
        )
        assert reply["ok"] is True
        item = reply["result"]["items"][0]
        assert item["cancel_state"] == ExecutionState.BLOCKED.value
        assert item["dispatch_attempts"] == 1
        record = resources.values["store"].get_cancel(
            item["cancel_id"], scope=case.execution_scope
        )
        assert record is not None
        assert record.state is ExecutionState.BLOCKED
        assert record.unknown_reason == "cancel_pre_dispatch_guard_failed"
        assert event_order == ["target_prepared"]
        assert chain._counts(api) == (1, 2, 0)


@pytest.mark.unit
def test_runtime_cancel_rejects_binding_for_another_target_before_risk_claim_and_native(
    tmp_path, monkeypatch
):
    import test_ctp_runtime_order_chain as chain

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        provider = install_synthetic_cancel_recovery_provider(
            case, monkeypatch, binding_fault="wrong_action_id"
        )
        component = chain._build_owner_control_execution_component(case, monkeypatch)
        chain._seed_synthetic_order_ref_cutover(case)
        chain._observe_authenticated_reserve(case, monkeypatch)
        chain._observe_submit_authorized(case, monkeypatch)
        resources = case.resources
        assert resources is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-cancel-auth-wrong-binding"
        callback_case, _session_binding = _submit_acknowledged_order(
            case, chain, component, monkeypatch, api, intent_id
        )
        _install_cancel_callbacks(
            case, chain, monkeypatch, api, callback_case, intent_id
        )
        _install_cancel_dispatch_observers(
            case,
            monkeypatch,
            api=api,
            event_order=provider.event_order,
        )

        reply = component.handlers["owner.cancel"](
            chain._owner_control_context(case),
            {"command_id": "ctp-runtime-cancel-auth-wrong-binding-command"},
        )

        _assert_cancel_recovery_hook(
            provider.cancel_hook_observation,
            returns=0,
            errors=[
                (
                    "CtpExecutionRuntimeError",
                    "CANCEL_RECOVERY_ACTION_MISMATCH",
                )
            ],
            case=case,
            provider=provider,
            provider_requests=1,
            quota_calls=1,
            quota_returns=1,
        )
        assert reply["ok"] is True
        item = reply["result"]["items"][0]
        assert item["cancel_state"] == ExecutionState.BLOCKED.value
        assert item["dispatch_attempts"] == 1
        assert len(provider.requests) == 1
        assert len(provider.returned_bindings) == 1
        request = provider.requests[0]
        persisted_intent = resources.values["store"].get_cancel_intent(
            item["cancel_id"], scope=case.execution_scope
        )
        assert persisted_intent is not None
        assert request.cancel_intent == persisted_intent
        wrong_action = provider.returned_bindings[0].approval.payload[
            "recovery_actions"
        ][0]
        assert wrong_action["action_id"] == item["cancel_id"] + "-wrong"
        assert provider.event_order == [
            "target_prepared",
            "provider_resolve",
            "target_prepared",
            "target_refreshed",
        ]
        record = resources.values["store"].get_cancel(
            item["cancel_id"], scope=case.execution_scope
        )
        assert record is not None
        assert record.state is ExecutionState.BLOCKED
        assert record.unknown_reason == "cancel_pre_dispatch_guard_failed"
        assert chain._counts(api) == (1, 3, 0)


@pytest.mark.unit
def test_runtime_cancel_late_terminal_callback_replay_restores_entry_without_redispatch(
    tmp_path, monkeypatch
):
    import test_ctp_runtime_order_chain as chain

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        provider = install_synthetic_cancel_recovery_provider(case, monkeypatch)
        component = chain._build_owner_control_execution_component(case, monkeypatch)
        chain._seed_synthetic_order_ref_cutover(case)
        chain._observe_authenticated_reserve(case, monkeypatch)
        chain._observe_submit_authorized(case, monkeypatch)
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        api = case.factory.instances[0]
        intent_id = "ctp-runtime-cancel-auth-late-terminal"
        callback_case, _session_binding = _submit_acknowledged_order(
            case, chain, component, monkeypatch, api, intent_id
        )
        _install_cancel_callbacks(
            case, chain, monkeypatch, api, callback_case, intent_id
        )

        dispatched_actions = []

        def defer_cancel_callbacks(field, request_id):
            api.calls.append(("ReqOrderAction", field, request_id))
            dispatched_actions.append((field, request_id))
            return 0

        monkeypatch.setattr(
            api, "ReqOrderAction", defer_cancel_callbacks, raising=False
        )
        _install_cancel_dispatch_observers(
            case,
            monkeypatch,
            api=api,
            event_order=provider.event_order,
        )

        command_id = "ctp-runtime-cancel-auth-late-terminal-command"
        first = component.handlers["owner.cancel"](
            chain._owner_control_context(case), {"command_id": command_id}
        )

        _assert_cancel_recovery_hook(
            provider.cancel_hook_observation,
            returns=1,
            case=case,
            provider=provider,
            provider_requests=1,
            quota_calls=1,
            quota_returns=1,
        )
        assert first["ok"] is False, first
        assert first["code"] == "OWNER_CANCEL_UNKNOWN", first
        first_item = first["result"]["items"][0]
        assert first["result"]["outcome"] == "UNKNOWN"
        assert first_item["cancel_state"] == ExecutionState.UNKNOWN.value
        assert first_item["dispatch_attempts"] == 1
        assert len(dispatched_actions) == 1
        assert len(provider.requests) == 1
        assert provider.configuration_calls[-1]["binding"] is provider.returned_bindings[0]
        assert runtime.is_ready() is False
        assert resources.ready() is False
        assert provider.event_order == [
            "target_prepared",
            "provider_resolve",
            "target_prepared",
            "target_refreshed",
            "risk_claim",
            "native_action",
        ]
        assert chain._counts(api) == (1, 3, 1)

        store = resources.values["store"]
        cancel_id = first_item["cancel_id"]
        cancel_record = store.get_cancel(cancel_id, scope=case.execution_scope)
        cancel_intent = store.get_cancel_intent(cancel_id, scope=case.execution_scope)
        order_intent = store.get_intent(intent_id, scope=case.execution_scope)
        reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
        submit_calls = [call for call in api.calls if call[0] == "ReqOrderInsert"]
        assert cancel_record is not None and cancel_intent is not None
        assert order_intent is not None and reservation is not None
        assert cancel_record.cancel_id == cancel_intent.cancel_id == cancel_id
        assert cancel_intent.target_intent_id == intent_id
        assert len(submit_calls) == 1
        submit_request_id = submit_calls[0][2]

        action_field, cancel_request_id = dispatched_actions[0]
        api.spi.OnRspOrderAction(
            action_field,
            SimpleNamespace(ErrorID=0, ErrorMsg="synthetic CTP action ACK"),
            cancel_request_id,
            True,
        )
        chain.callback_fixtures._emit_order_status(
            callback_case,
            order_intent,
            reservation,
            submit_request_id,
            status="5",
            submit_status="3",
            traded=0,
            sequence=2,
        )

        # The owner loop must respect its bounded poll deadline while still
        # unready. Advancing that deadline is a test clock control only.
        runtime._next_reconciliation_ns = runtime_module.monotonic_ns() + 1_000_000_000
        runtime.maintain(frozenset())
        assert runtime.is_ready() is False
        assert chain._counts(api) == (1, 3, 1)
        runtime._next_reconciliation_ns = 0
        runtime.maintain(frozenset())
        assert runtime.is_ready() is True, runtime.status
        assert resources.ready() is True
        assert (
            provider.configuration_calls[-1]["binding"]
            is provider.setup.initial_binding
        )
        assert (
            provider.setup.initial_binding.approval.payload["purpose"]
            == APPROVAL_PURPOSE
        )
        assert len(provider.requests) == 1
        assert len(dispatched_actions) == 1
        assert chain._counts(api) == (1, 3, 1)

        cancel_record = store.get_cancel(cancel_id, scope=case.execution_scope)
        assert cancel_record is not None
        assert cancel_record.state is ExecutionState.CANCELLED
        assert cancel_record.dispatch_attempts == 1
        assert cancel_record.permit_reference is not None
        cancel_resolution = case.risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert cancel_resolution.status.value == "TERMINAL_RESOLVED"
        assert cancel_resolution.dispatch_latch_active is False
        assert cancel_resolution.proof is not None

        replay = component.handlers["owner.cancel"](
            chain._owner_control_context(case), {"command_id": command_id}
        )

        assert replay["ok"] is True
        replay_item = replay["result"]["items"][0]
        assert replay_item["cancel_id"] == cancel_id
        assert replay_item["cancel_state"] == ExecutionState.CANCELLED.value
        assert replay_item["dispatch_attempts"] == 1
        assert cancel_record.dispatch_attempts == 1
        assert len(provider.requests) == 1
        assert len(dispatched_actions) == 1
        assert provider.configuration_calls[-1]["binding"] is provider.setup.initial_binding
        assert (
            provider.setup.initial_binding.approval.payload["purpose"]
            == APPROVAL_PURPOSE
        )
        assert runtime.is_ready() is True
        assert resources.ready() is True
        assert provider.event_order == [
            "target_prepared",
            "provider_resolve",
            "target_prepared",
            "target_refreshed",
            "risk_claim",
            "native_action",
        ]
        assert chain._counts(api) == (1, 3, 1)
