"""Synthetic SDK composition tests for durable Monitor-to-Risk controls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor import (
    CONTROL_COMMAND_HMAC_DOMAIN,
    ControlAction,
    ControlCommandBinding,
    ControlIngress,
    ControlIssuerPolicy,
    ControlOwnerOutcome,
    ControlScopeGrant,
    ControlStatus,
    DurableControlLedger,
    HmacControlCommandVerifier,
    HmacControlKey,
    monitor_freeze_cause_id,
)
from bt_api_monitor.service_control import (
    MonitorControlService,
    MonitorControlTarget,
)
from bt_api_monitor.service_control import (
    MonitorControlServiceError as PackageMonitorControlServiceError,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    IntentAction,
    RiskDeniedError,
    RiskIntent,
    RiskPolicy,
)

from bt_api_py.service_monitor_control import (
    MONITOR_CONTROL_OPERATIONS,
    MonitorControlRuntime,
    MonitorControlRuntimeError,
    MonitorControlServiceError,
    MonitorRiskControlOwner,
)

_NOW = 1_800_000_000.0
_KEY_ID = "synthetic-monitor-key"
_ISSUER = "synthetic-operator"
_ACCOUNT_ID = "synthetic-account"
_MODE = "simulation"
_COMMAND_SCOPE = "synthetic-account-scope"
_RECEIPT = "a" * 64
_SECRET = b"synthetic-monitor-control-key-32-bytes"
_RISK_SCOPE = AccountScope("synthetic", _ACCOUNT_ID, _MODE)
_WIRE_SCOPE = WireScope("paper:synthetic", account_ref="wire-account")
_CALLER = WireIdentity(
    WireRole.APPLICATION_CLIENT,
    "synthetic-application",
    "synthetic-actor",
    delegate_ref="synthetic-delegation",
)
_OWNER = WireIdentity(WireRole.SERVICE, "synthetic-monitor-owner", "monitor-owner")
_EXECUTOR_ID = "synthetic-monitor-executor"


def _default_clock() -> float:
    return _NOW


class _KeyResolver:
    def __init__(self) -> None:
        self._key = HmacControlKey(
            key_id=_KEY_ID,
            issuer=_ISSUER,
            secret=_SECRET,
            not_before_utc=_NOW - 100,
            expires_at_utc=_NOW + 1_000,
        )

    def resolve(self, key_id: str):
        return self._key if key_id == _KEY_ID else None


class _PolicyResolver:
    def __init__(self) -> None:
        self._policy = ControlIssuerPolicy(
            issuer=_ISSUER,
            policy_id="synthetic-monitor-policy",
            grants=(
                ControlScopeGrant(
                    account_id=_ACCOUNT_ID,
                    mode=_MODE,
                    scope=_COMMAND_SCOPE,
                    actions=(ControlAction.FREEZE, ControlAction.RESUME),
                    receipt_digests=(_RECEIPT,),
                    manual_resume_allowed=True,
                ),
            ),
            not_before_utc=_NOW - 100,
            expires_at_utc=_NOW + 1_000,
        )

    def resolve(self, issuer: str):
        return self._policy if issuer == _ISSUER else None


def _signed_request(
    command_id: str,
    sequence: int,
    action: ControlAction,
    *,
    schema_version: int = 2,
    target_freeze_cause_id: str | None = None,
) -> dict[str, str | int]:
    claims = {
        "schema_version": schema_version,
        "key_id": _KEY_ID,
        "issuer": _ISSUER,
        "command_id": command_id,
        "account_id": _ACCOUNT_ID,
        "mode": _MODE,
        "scope": _COMMAND_SCOPE,
        "action": action.value,
        "reason": "synthetic control test",
        "receipt_digest": _RECEIPT,
        "issued_at": _NOW - 10 + sequence,
        "expires_at": _NOW + 100 + sequence,
        "issuer_sequence": sequence,
        "manual_resume_authorized": action is ControlAction.RESUME,
    }
    if schema_version == 2:
        claims["target_freeze_cause_id"] = target_freeze_cause_id
    payload = json.dumps(
        claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    encoded_key_id = _KEY_ID.encode("ascii")
    signed = (
        CONTROL_COMMAND_HMAC_DOMAIN
        + len(encoded_key_id).to_bytes(2, "big")
        + encoded_key_id
        + payload
    )
    signature = hmac.new(_SECRET, signed, hashlib.sha256).digest()
    return {
        "schema_version": 1,
        "payload_b64": base64.b64encode(payload).decode("ascii"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }


def _composition(tmp_path, *, clock=None):
    if clock is None:
        clock = _default_clock
    ledger = DurableControlLedger(tmp_path / "monitor-control.sqlite", clock=clock)
    ingress = ControlIngress(
        ledger,
        HmacControlCommandVerifier(_KeyResolver(), _PolicyResolver(), max_command_ttl_seconds=120),
        clock=clock,
    )
    binding = ControlCommandBinding(
        issuer=_ISSUER,
        account_id=_ACCOUNT_ID,
        mode=_MODE,
        scope=_COMMAND_SCOPE,
        actions=frozenset({ControlAction.FREEZE, ControlAction.RESUME}),
    )
    target = MonitorControlTarget(
        ingress=ingress,
        caller_bindings={_CALLER: binding},
        owner_identity=_OWNER,
        executor_id=_EXECUTOR_ID,
    )
    service = MonitorControlService({_WIRE_SCOPE: target})
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite",
        RiskPolicy(
            policy_id="synthetic-monitor-risk",
            max_increase_notional=Decimal("100"),
            max_increase_count=3,
        ),
        clock=clock,
    )
    owner = MonitorRiskControlOwner(
        service,
        gate,
        _RISK_SCOPE,
        wire_scope=_WIRE_SCOPE,
        owner_identity=_OWNER,
        executor_id=_EXECUTOR_ID,
        account_id=_ACCOUNT_ID,
        mode=_MODE,
        command_scope=_COMMAND_SCOPE,
    )
    runtime = MonitorControlRuntime(service, {_WIRE_SCOPE: owner})
    return ledger, service, gate, owner, runtime


def _call(runtime: MonitorControlRuntime, arguments: dict[str, str | int]):
    return runtime.handle(
        "monitor.control",
        principal_ref=_CALLER.principal_ref,
        identity=_CALLER,
        scope=_WIRE_SCOPE,
        arguments=arguments,
    )


def _reserve_increase(gate: DurableRiskGate, intent_id: str) -> None:
    gate.reserve(
        RiskIntent(
            intent_id=intent_id,
            scope=_RISK_SCOPE,
            action=IntentAction.INCREASE,
            notional=Decimal("1"),
            payload_fingerprint="b" * 64,
        )
    )


def test_runtime_freeze_is_durable_idempotent_and_blocks_risk_admission(tmp_path) -> None:
    assert frozenset({"monitor.control"}) == MONITOR_CONTROL_OPERATIONS
    assert frozenset({"monitor.control"}) == MonitorControlRuntime.OPERATIONS
    assert MonitorControlServiceError is PackageMonitorControlServiceError
    ledger, _service, gate, _owner, runtime = _composition(tmp_path)
    request = _signed_request("freeze-one", 1, ControlAction.FREEZE)

    first = _call(runtime, request)
    duplicate = _call(runtime, request)

    assert first["status"] == ControlStatus.ACKNOWLEDGED.value
    assert first["outcome"] == "applied"
    assert first["owner_final"] is True
    assert first["effect_ref"] == ledger.get("freeze-one").effect_ref
    assert first["effect_result"] == {
        "action": "freeze",
        "cause_id": first["effect_ref"],
        "activation_status": "CREATED",
        "cause_active": True,
        "has_other_active_freezes": False,
        "risk_state": "active",
    }
    assert duplicate == first
    assert ledger.get("freeze-one").effect_ref == first["effect_ref"]
    with pytest.raises(RiskDeniedError) as denied:
        _reserve_increase(gate, "increase-after-monitor-freeze")
    assert denied.value.code == "FROZEN"


def test_resume_clears_only_the_signed_monitor_cause_and_keeps_other_freezes(tmp_path) -> None:
    ledger, _service, gate, _owner, runtime = _composition(tmp_path)
    gate.freeze(_RISK_SCOPE, "manual-incident-42", "independent operator hold")
    freeze_request = _signed_request("freeze-target", 1, ControlAction.FREEZE)
    freeze_result = _call(runtime, freeze_request)
    target_cause = ledger.get("freeze-target").effect_ref
    assert target_cause == freeze_result["effect_ref"]
    assert freeze_result["effect_result"]["has_other_active_freezes"] is True

    resume_request = _signed_request(
        "resume-target", 2, ControlAction.RESUME, target_freeze_cause_id=target_cause
    )
    resumed = _call(runtime, resume_request)

    assert resumed["status"] == ControlStatus.ACKNOWLEDGED.value
    assert resumed["outcome"] == "applied"
    assert resumed["owner_final"] is True
    assert resumed["effect_result"] == {
        "action": "resume",
        "cause_id": target_cause,
        "resolution_status": "CLEARED",
        "has_other_active_freezes": True,
        "account_still_frozen": True,
    }
    with pytest.raises(RiskDeniedError) as denied:
        _reserve_increase(gate, "increase-after-partial-resume")
    assert denied.value.code == "FROZEN"
    assert gate.active_freeze_reasons(_RISK_SCOPE) == ["independent operator hold"]


def test_resume_projects_already_resolved_and_missing_causes_as_typed_results(tmp_path) -> None:
    ledger, _service, _gate, _owner, runtime = _composition(tmp_path)
    _call(runtime, _signed_request("freeze-for-repeat-resume", 1, ControlAction.FREEZE))
    target_cause = ledger.get("freeze-for-repeat-resume").effect_ref

    first_resume = _call(
        runtime,
        _signed_request(
            "resume-clears-target",
            2,
            ControlAction.RESUME,
            target_freeze_cause_id=target_cause,
        ),
    )
    repeated_resume = _call(
        runtime,
        _signed_request(
            "resume-finds-resolved-target",
            3,
            ControlAction.RESUME,
            target_freeze_cause_id=target_cause,
        ),
    )
    missing_resume = _call(
        runtime,
        _signed_request(
            "resume-finds-no-target",
            4,
            ControlAction.RESUME,
            target_freeze_cause_id="monitor-freeze-v1:" + "0" * 64,
        ),
    )

    assert first_resume["effect_result"]["resolution_status"] == "CLEARED"
    assert repeated_resume["status"] == ControlStatus.ACKNOWLEDGED.value
    assert repeated_resume["effect_result"]["resolution_status"] == "ALREADY_RESOLVED"
    assert repeated_resume["effect_result"]["account_still_frozen"] is False
    assert missing_resume["status"] == ControlStatus.REJECTED.value
    assert missing_resume["owner_final"] is True
    assert missing_resume["effect_result"]["resolution_status"] == "NOT_FOUND"


def test_inflight_claim_is_not_returned_as_a_completed_control_result(tmp_path) -> None:
    ledger, service, gate, _owner, runtime = _composition(tmp_path)
    request = _signed_request("freeze-already-claimed", 1, ControlAction.FREEZE)
    service.handle(
        "monitor.control",
        principal_ref=_CALLER.principal_ref,
        identity=_CALLER,
        scope=_WIRE_SCOPE,
        arguments=request,
    )
    claimed = ledger.claim_command(
        "freeze-already-claimed",
        _COMMAND_SCOPE,
        _EXECUTOR_ID,
        account_id=_ACCOUNT_ID,
        mode=_MODE,
    )
    assert claimed is not None

    with pytest.raises(MonitorControlRuntimeError) as unresolved:
        _call(runtime, request)

    assert unresolved.value.code == "CONTROL_ORDER_UNRESOLVED"
    assert ledger.get("freeze-already-claimed").status is ControlStatus.CLAIMED
    assert gate.active_freeze_reasons(_RISK_SCOPE) == []


def test_unknown_freeze_recovery_finishes_before_later_resume(tmp_path) -> None:
    ledger, service, gate, _owner, runtime = _composition(tmp_path)
    real_handle = service.handle
    dropped = False

    def drop_first_applied_ack(
        operation: str,
        *,
        principal_ref: str,
        identity: WireIdentity,
        scope: WireScope,
        arguments,
    ):
        nonlocal dropped
        if (
            operation == "monitor.acknowledge"
            and arguments.get("outcome") == "applied"
            and not dropped
        ):
            dropped = True
            raise RuntimeError("synthetic ACK transport loss")
        return real_handle(
            operation,
            principal_ref=principal_ref,
            identity=identity,
            scope=scope,
            arguments=arguments,
        )

    service.handle = drop_first_applied_ack  # type: ignore[method-assign]
    freeze_request = _signed_request("freeze-uncertain", 1, ControlAction.FREEZE)
    uncertain = _call(runtime, freeze_request)
    target_cause = monitor_freeze_cause_id(ledger.get("freeze-uncertain").command)

    assert dropped is True
    assert uncertain["status"] == ControlStatus.UNKNOWN.value
    assert uncertain["owner_final"] is False
    assert ledger.get("freeze-uncertain").effect_ref is None
    assert gate.active_freeze_reasons(_RISK_SCOPE) == ["synthetic control test"]

    # A later RESUME cannot pass the earlier UNKNOWN FREEZE. Runtime recovers
    # the same executor's exact FREEZE first, then applies the signed RESUME.
    resume_request = _signed_request(
        "resume-after-uncertain-freeze",
        2,
        ControlAction.RESUME,
        target_freeze_cause_id=target_cause,
    )
    resumed = _call(runtime, resume_request)

    assert resumed["status"] == ControlStatus.ACKNOWLEDGED.value
    assert resumed["effect_result"]["resolution_status"] == "CLEARED"
    assert resumed["effect_result"]["account_still_frozen"] is False
    freeze_row = ledger.get("freeze-uncertain")
    assert freeze_row.status is ControlStatus.ACKNOWLEDGED
    assert freeze_row.effect_ref == target_cause
    assert freeze_row.effect_result["activation_status"] == "ALREADY_ACTIVE"
    assert ledger.get("resume-after-uncertain-freeze").status is ControlStatus.ACKNOWLEDGED
    assert gate.active_freeze_reasons(_RISK_SCOPE) == []
    replay = _call(runtime, freeze_request)
    assert replay["status"] == ControlStatus.ACKNOWLEDGED.value
    assert replay["owner_final"] is True
    assert gate.active_freeze_reasons(_RISK_SCOPE) == []


def test_delayed_old_owner_cannot_reactivate_freeze_after_replacement_resume(tmp_path) -> None:
    class MutableClock:
        def __init__(self, value: float) -> None:
            self.value = value

        def __call__(self) -> float:
            return self.value

    clock = MutableClock(_NOW)
    ledger, service, gate_a, owner_a, _runtime_a = _composition(tmp_path, clock=clock)
    risk_path = tmp_path / "risk.sqlite"
    gate_b = DurableRiskGate(
        risk_path,
        RiskPolicy(
            policy_id="synthetic-monitor-risk",
            max_increase_notional=Decimal("100"),
            max_increase_count=3,
        ),
        clock=clock,
    )
    owner_b = MonitorRiskControlOwner(
        service,
        gate_b,
        _RISK_SCOPE,
        wire_scope=_WIRE_SCOPE,
        owner_identity=_OWNER,
        executor_id=_EXECUTOR_ID,
        account_id=_ACCOUNT_ID,
        mode=_MODE,
        command_scope=_COMMAND_SCOPE,
    )
    runtime_b = MonitorControlRuntime(service, {_WIRE_SCOPE: owner_b})

    effect_entered = threading.Event()
    release_old_owner = threading.Event()
    old_owner_activation = []
    activate_a = gate_a.activate_freeze_once

    def delay_old_owner_effect(scope, cause_id, reason):
        effect_entered.set()
        if not release_old_owner.wait(timeout=5):
            raise TimeoutError("synthetic old owner was not released")
        result = activate_a(scope, cause_id, reason)
        old_owner_activation.append(result)
        return result

    # The owner and gate are genuine independent instances. Only A's call
    # boundary is paused so B can reclaim the expired UNKNOWN row and finish a
    # later exact RESUME before A reaches the same durable Risk effect.
    gate_a.activate_freeze_once = delay_old_owner_effect  # type: ignore[method-assign]
    freeze_request = _signed_request("freeze-racing-owner", 1, ControlAction.FREEZE)
    submitted = service.handle(
        "monitor.control",
        principal_ref=_CALLER.principal_ref,
        identity=_CALLER,
        scope=_WIRE_SCOPE,
        arguments=freeze_request,
    )
    assert submitted["status"] == ControlStatus.PENDING.value

    with ThreadPoolExecutor(max_workers=1) as workers:
        old_owner_future = workers.submit(owner_a.run_next)
        assert effect_entered.wait(timeout=5)

        # Expire the original signed FREEZE command while A is still blocked
        # immediately before the Risk transaction. The subsequent RESUME has
        # a later expiry and remains valid.
        clock.value = _NOW + 101.5
        target_cause = monitor_freeze_cause_id(ledger.get("freeze-racing-owner").command)
        resumed = _call(
            runtime_b,
            _signed_request(
                "resume-racing-owner",
                2,
                ControlAction.RESUME,
                target_freeze_cause_id=target_cause,
            ),
        )

        assert resumed["status"] == ControlStatus.ACKNOWLEDGED.value
        assert resumed["effect_result"]["resolution_status"] == "CLEARED"
        assert ledger.get("freeze-racing-owner").status is ControlStatus.ACKNOWLEDGED
        assert ledger.get("resume-racing-owner").status is ControlStatus.ACKNOWLEDGED
        assert gate_b.active_freeze_reasons(_RISK_SCOPE) == []

        release_old_owner.set()
        old_owner_result = old_owner_future.result(timeout=5)

    assert old_owner_activation[0].status.value == "ALREADY_RESOLVED"
    assert old_owner_activation[0].has_other_active_freezes is False
    assert old_owner_result["status"] == ControlStatus.ACKNOWLEDGED.value
    assert old_owner_result["effect_result"]["activation_status"] == "CREATED"
    assert gate_a.active_freeze_reasons(_RISK_SCOPE) == []
    assert gate_b.active_freeze_reasons(_RISK_SCOPE) == []
    _reserve_increase(gate_b, "increase-after-racing-resume")


def test_unrecoverable_legacy_unknown_blocks_later_resume_before_risk_effect(tmp_path) -> None:
    ledger, service, gate, _owner, runtime = _composition(tmp_path)
    legacy_freeze = _signed_request(
        "legacy-freeze-uncertain", 1, ControlAction.FREEZE, schema_version=1
    )
    submitted = service.handle(
        "monitor.control",
        principal_ref=_CALLER.principal_ref,
        identity=_CALLER,
        scope=_WIRE_SCOPE,
        arguments=legacy_freeze,
    )
    assert submitted["status"] == ControlStatus.PENDING.value
    claimed = ledger.claim_command(
        "legacy-freeze-uncertain",
        _COMMAND_SCOPE,
        "previous-executor",
        account_id=_ACCOUNT_ID,
        mode=_MODE,
    )
    assert claimed is not None
    cause_id = monitor_freeze_cause_id(claimed.command)
    gate.freeze(_RISK_SCOPE, cause_id, claimed.command.reason)
    ledger.finish_owner_outcome(
        "legacy-freeze-uncertain",
        "previous-executor",
        ControlOwnerOutcome.UNKNOWN,
    )

    resume_request = _signed_request(
        "resume-blocked-by-legacy-unknown",
        2,
        ControlAction.RESUME,
        target_freeze_cause_id=cause_id,
    )
    with pytest.raises(MonitorControlRuntimeError) as blocked:
        _call(runtime, resume_request)

    assert blocked.value.code == "CONTROL_ORDER_UNRESOLVED"
    assert ledger.get("resume-blocked-by-legacy-unknown").status is ControlStatus.PENDING
    assert gate.active_freeze_reasons(_RISK_SCOPE) == ["synthetic control test"]
