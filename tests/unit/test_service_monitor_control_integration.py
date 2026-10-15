"""Spawned ServiceHost acceptance for authenticated monitor-to-Risk control.

The service and client communicate over a loopback CURVE socket. Risk and
monitor control state use synthetic SQLite databases owned by the child host;
there is no provider, account credential, or external endpoint in this test.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import multiprocessing
import socket
import sqlite3
import sys
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_monitor.control import (
    ControlAction,
    ControlCommand,
    ControlStatus,
    DurableControlLedger,
)
from bt_api_monitor.ingress import (
    CONTROL_COMMAND_HMAC_DOMAIN,
    ControlIssuerPolicy,
    ControlScopeGrant,
    HmacControlCommandVerifier,
    HmacControlKey,
)
from bt_api_monitor.service_control import (
    ControlCommandBinding,
    ControlIngress,
    MonitorControlService,
    MonitorControlTarget,
    monitor_freeze_cause_id,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskGate,
    IntentAction,
    RiskDeniedError,
    RiskIntent,
    RiskPolicy,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_SERVICE_ID = "synthetic-monitor-control-service"
_SERVICE_PRINCIPAL = "synthetic-monitor-control-principal"
_SERVICE_INSTANCE = "monitor-control-synthetic"
_SERVICE_SCOPE_REF = "monitor-control-route-v1"
_CLIENT_PRINCIPAL = "synthetic-monitor-control-client"
_APPLICATION_REF = "application-synthetic"
_CALLER_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT,
    _CLIENT_PRINCIPAL,
    _APPLICATION_REF,
    delegate_ref="operator-delegation:synthetic-desk",
)
_OWNER_IDENTITY = WireIdentity(
    WireRole.SERVICE,
    "synthetic-risk-owner",
    "account-owner:synthetic",
    delegate_ref="monitor-owner-delegation:synthetic",
)
_WIRE_SCOPE = WireScope(
    execution_domain_ref="paper:synthetic-control",
    account_ref="paper-account-control",
)
_RISK_SCOPE = AccountScope("synthetic", "paper-account-control", "simulation")
_ACCOUNT_ID = "paper-account-control"
_MODE = "simulation"
_COMMAND_SCOPE = "account:paper-control"
_ISSUER = "operator:synthetic-desk"
_KEY_ID = "monitor-control-integration-key"
_RECEIPT = "c" * 64
_SECRET = b"synthetic-local-monitor-control-key-material-v1"
_NOW = 1_800_000_000.0
_MAX_MESSAGE_BYTES = 65_536
_REQUEST_TIMEOUT_MS = 5_000
_INDEPENDENT_FREEZE_CAUSE = "risk-incident:synthetic-independent-hold"
_INDEPENDENT_FREEZE_REASON = "synthetic independent risk hold"
_DISPATCH_INTENT_ID = "synthetic-dispatch-latch-001"
_DISPATCH_FREEZE_CAUSE = f"dispatch-inflight:{_DISPATCH_INTENT_ID}"
_RISK_POLICY = RiskPolicy(
    policy_id="monitor-control-integration-risk-policy-v1",
    max_increase_notional=Decimal("250"),
    max_increase_count=8,
    permit_ttl_seconds=86_400,
)


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _communication(endpoint: str, *, server: bool) -> dict[str, Any]:
    profile = "listener-v1" if server else "client-curve-v1"
    security = "server-curve-v1" if server else "client-curve-v1"
    options: dict[str, int] = {
        "high_water_mark": 16,
        "max_message_bytes": _MAX_MESSAGE_BYTES,
    }
    if not server:
        options["request_timeout_ms"] = _REQUEST_TIMEOUT_MS
    return {
        "config_schema_version": 1,
        "profiles": {
            profile: {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_bind" if server else "rpc_connect": endpoint},
                "security_profile_ref": security,
                "options": options,
            }
        },
    }


def _service_config(endpoint: str):
    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "service-root-synthetic-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": "monitor-control-registration-v1",
        "service_requirements_ref": "monitor-control-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.monitor_control"],
                    "options": {"role": "control"},
                },
            }
        },
        "communication": _communication(endpoint, server=True),
    }
    return parse_service_config(raw)


def _client_config(endpoint: str):
    raw = {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": _APPLICATION_REF},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SERVICE_SCOPE_REF,
                            "transport_ref": "client-curve-v1",
                        }
                    ],
                    "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                },
            }
        },
        "communication": _communication(endpoint, server=False),
    }
    return parse_client_config(raw)


class _KeyResolver:
    def __init__(self, key: HmacControlKey) -> None:
        self._key = key

    def resolve(self, key_id: str) -> HmacControlKey | None:
        return self._key if key_id == self._key.key_id else None


class _PolicyResolver:
    def __init__(self, policy: ControlIssuerPolicy) -> None:
        self._policy = policy

    def resolve(self, issuer: str) -> ControlIssuerPolicy | None:
        return self._policy if issuer == self._policy.issuer else None


def _verifier() -> HmacControlCommandVerifier:
    key = HmacControlKey(
        key_id=_KEY_ID,
        issuer=_ISSUER,
        secret=_SECRET,
        not_before_utc=_NOW - 100,
        expires_at_utc=_NOW + 100,
    )
    policy = ControlIssuerPolicy(
        issuer=_ISSUER,
        policy_id="synthetic-monitor-control-policy-v1",
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
        expires_at_utc=_NOW + 100,
    )
    return HmacControlCommandVerifier(
        _KeyResolver(key),
        _PolicyResolver(policy),
        max_command_ttl_seconds=60,
    )


def _signed_request(
    command_id: str,
    *,
    action: ControlAction = ControlAction.FREEZE,
    issuer_sequence: int = 1,
    account_id: str = _ACCOUNT_ID,
    mode: str = _MODE,
    scope: str = _COMMAND_SCOPE,
    issuer: str = _ISSUER,
    target_freeze_cause_id: str | None = None,
) -> dict[str, Any]:
    claims = {
        "schema_version": 2,
        "key_id": _KEY_ID,
        "issuer": issuer,
        "command_id": command_id,
        "account_id": account_id,
        "mode": mode,
        "scope": scope,
        "action": action.value,
        "reason": "synthetic integration control",
        "receipt_digest": _RECEIPT,
        "issued_at": _NOW - 1,
        "expires_at": _NOW + 20,
        "issuer_sequence": issuer_sequence,
        "manual_resume_authorized": action is ControlAction.RESUME,
        "target_freeze_cause_id": target_freeze_cause_id,
    }
    payload = json.dumps(
        claims,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    key_id = _KEY_ID.encode("ascii")
    signed = CONTROL_COMMAND_HMAC_DOMAIN + len(key_id).to_bytes(2, "big") + key_id + payload
    signature = hmac.new(_SECRET, signed, hashlib.sha256).digest()
    return {
        "schema_version": 1,
        "payload_b64": base64.b64encode(payload).decode("ascii"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }


def _risk_intent(intent_id: str) -> RiskIntent:
    return RiskIntent(
        intent_id=intent_id,
        scope=_RISK_SCOPE,
        action=IntentAction.INCREASE,
        notional=Decimal("1"),
        payload_fingerprint=f"synthetic-payload:{intent_id}",
    )


def _freeze_cause_id(command_id: str) -> str:
    """Derive the exact cause from the caller's own signed FREEZE claims."""

    return monitor_freeze_cause_id(
        ControlCommand(
            command_id=command_id,
            account_id=_ACCOUNT_ID,
            mode=_MODE,
            scope=_COMMAND_SCOPE,
            action=ControlAction.FREEZE,
            issuer=_ISSUER,
            key_id=_KEY_ID,
            reason="synthetic integration control",
            receipt_digest=_RECEIPT,
            issued_at=_NOW - 1,
            expires_at=_NOW + 20,
            manual_resume_authorized=False,
            schema_version=2,
        )
    )


def _resource_provider(paths: dict[str, str], effects: dict[str, int]):
    def provider(component, _instance_id, _options, _scope):
        if component != "monitor":
            raise AssertionError("the selected service contains only monitor control")

        gate = DurableRiskGate(paths["risk"], _RISK_POLICY, clock=lambda: _NOW)
        # Establish one independent risk freeze and a real dispatch latch before
        # the monitor owner is allowed to install or clear its own exact cause.
        intent = _risk_intent(_DISPATCH_INTENT_ID)
        permit = gate.reserve(intent)
        gate.claim_for_dispatch(permit.permit_id, intent)
        gate.freeze(_RISK_SCOPE, _INDEPENDENT_FREEZE_CAUSE, _INDEPENDENT_FREEZE_REASON)

        real_activate = gate.activate_freeze_once

        def count_freeze_effect(scope, cause_id, reason):
            effects["risk_freeze_calls"] += 1
            return real_activate(scope, cause_id, reason)

        gate.activate_freeze_once = count_freeze_effect
        real_resolve = gate.resolve_freeze_exact

        def count_resume_effect(scope, cause_id):
            effects["risk_resume_calls"] += 1
            return real_resolve(scope, cause_id)

        gate.resolve_freeze_exact = count_resume_effect

        ledger = DurableControlLedger(paths["control"], clock=lambda: _NOW)
        ingress = ControlIngress(ledger, _verifier(), clock=lambda: _NOW)
        binding = ControlCommandBinding(
            issuer=_ISSUER,
            account_id=_ACCOUNT_ID,
            mode=_MODE,
            scope=_COMMAND_SCOPE,
            actions=frozenset({ControlAction.FREEZE, ControlAction.RESUME}),
        )
        target = MonitorControlTarget(
            ingress=ingress,
            caller_bindings={_CALLER_IDENTITY: binding},
            owner_identity=_OWNER_IDENTITY,
            executor_id="risk-owner:synthetic",
        )
        service = MonitorControlService({_WIRE_SCOPE: target})

        # Drop exactly one final acknowledgement after the real Risk effect.
        # All subsequent calls delegate to the same durable service object.
        handle = service.handle
        fault_armed = True

        def handle_with_one_lost_ack(operation: str, **kwargs):
            nonlocal fault_armed
            if operation == "monitor.acknowledge" and fault_armed:
                fault_armed = False
                effects["lost_ack_calls"] += 1
                raise OSError("synthetic post-effect acknowledgement cut")
            return handle(operation, **kwargs)

        service.handle = handle_with_one_lost_ack

        from bt_api_py.service_monitor_control import (
            MonitorControlRuntime,
            MonitorRiskControlOwner,
        )

        owner = MonitorRiskControlOwner(
            service,
            gate,
            _RISK_SCOPE,
            wire_scope=_WIRE_SCOPE,
            owner_identity=_OWNER_IDENTITY,
            executor_id="risk-owner:synthetic",
            account_id=_ACCOUNT_ID,
            mode=_MODE,
            command_scope=_COMMAND_SCOPE,
        )
        owners = {_WIRE_SCOPE: owner}
        runtime = MonitorControlRuntime(service, owners)
        if frozenset({"monitor.control"}) != runtime.OPERATIONS:
            raise AssertionError("monitor runtime exposed a non-public operation")

        def close_resources() -> None:
            # DurableControlLedger opens and closes a SQLite connection for
            # each operation; it has no persistent connection to release.
            gate.close()

        return ComponentResources.owned(
            {"service": service, "owners": owners},
            ready=lambda: True,
            close=close_resources,
        )

    return provider


def _service_host_process(
    control,
    raw_service_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    effects: dict[str, int],
    server_public: bytes,
    server_secret: bytes,
    client_public: bytes,
) -> None:
    host = None
    try:
        config = parse_service_config(raw_service_config)
        public_resource = ResourceGrant(
            _SERVICE_INSTANCE,
            "service.monitor_control",
            _SERVICE_SCOPE_REF,
            _WIRE_SCOPE,
            frozenset({"monitor.control"}),
        )
        grants = {_CLIENT_PRINCIPAL: (PrincipalGrant(_CALLER_IDENTITY, (public_resource,)),)}
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
                scope=_WIRE_SCOPE,
                scope_ref=config.scope_ref,
                scope_version="synthetic-service-scope-v1",
                security_ref="server-curve-v1",
                security_version="synthetic-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="synthetic-registration-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="synthetic-service-requirements-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={client_public: _CLIENT_PRINCIPAL},
                grants=grants,
            ),
            registry_path,
            _resource_provider(paths, effects),
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            if control.poll(0.01) and control.recv() == "close":
                break
            host.serve_once(25)
        host.close()
        control.send(("closed", dict(effects), host.state))
    except BaseException as exc:
        host_state = None
        cleanup_failures: tuple[str, ...] = ()
        if host is not None:
            with suppress(BaseException):
                host_state = host.state
                cleanup_failures = tuple(host.summary.get("cleanup_failures", ()))
            with suppress(BaseException):
                host.close()
        code = getattr(exc, "code", type(exc).__name__)
        with suppress(Exception):
            control.send(("error", code, host_state, cleanup_failures))
        raise
    finally:
        control.close()


def _direct_request(
    transport,
    binding,
    *,
    operation: str,
    arguments: dict[str, Any],
    identity: WireIdentity = _CALLER_IDENTITY,
    scope: WireScope = _WIRE_SCOPE,
):
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
        {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": arguments,
        },
    )
    return transport.request(request, timeout=5.0)


def _ledger_storage(
    path: str, command_id: str
) -> tuple[str, str | None, str | None, str | None] | None:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            """SELECT status, executor_id, outcome, effect_ref
               FROM monitor_control_commands WHERE command_id = ?""",
            (command_id,),
        ).fetchone()
        return (
            None
            if row is None
            else (
                str(row[0]),
                None if row[1] is None else str(row[1]),
                None if row[2] is None else str(row[2]),
                None if row[3] is None else str(row[3]),
            )
        )
    finally:
        connection.close()


def _risk_freeze_row(path: str, cause_id: str) -> tuple[str, int] | None:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT cause_id, active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
            (_RISK_SCOPE.key, cause_id),
        ).fetchone()
        return None if row is None else (str(row[0]), int(row[1]))
    finally:
        connection.close()


def _risk_freezes_for_reason(path: str, reason: str) -> tuple[tuple[str, int], ...]:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT cause_id, active FROM risk_freezes WHERE scope_key = ? AND reason = ?",
            (_RISK_SCOPE.key, reason),
        ).fetchall()
        return tuple((str(row[0]), int(row[1])) for row in rows)
    finally:
        connection.close()


def test_spawned_curve_monitor_control_applies_recovers_and_resumes_exact_risk_cause(
    tmp_path: Path,
) -> None:
    paths = {
        "risk": str(tmp_path / "risk-control.sqlite3"),
        "control": str(tmp_path / "monitor-control.sqlite3"),
    }
    effects = {
        "risk_freeze_calls": 0,
        "risk_resume_calls": 0,
        "lost_ack_calls": 0,
    }
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    endpoint = _endpoint()
    service_config = _service_config(endpoint).to_public_dict()
    process_context = multiprocessing.get_context("spawn")
    parent, child = process_context.Pipe(duplex=True)
    process = process_context.Process(
        target=_service_host_process,
        args=(
            child,
            service_config,
            str(tmp_path / "service-registry.sqlite3"),
            paths,
            effects,
            server_public,
            server_secret,
            client_public,
        ),
    )
    runtime = None
    direct_transport = None
    shutdown = None
    process.start()
    child.close()
    try:
        assert parent.poll(20), "spawned monitor-control host did not report startup"
        startup = parent.recv()
        assert startup[0] == "ready", startup
        _kind, bound_endpoint = startup
        assert bound_endpoint == endpoint

        service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        keys = ClientCurveKeys(server_public, client_public, client_secret)
        transport_factory = ZmqTransportFactory({"client-curve-v1": keys})
        client_config = _client_config(endpoint)
        runtime = ServiceClientRuntime(
            client_config,
            identity=_CALLER_IDENTITY,
            scope_resolver=lambda _ref: _WIRE_SCOPE,
            peer_resolver=lambda _service: service_identity,
            transport_factory=transport_factory,
            requested_operations={"application.monitor": frozenset({"monitor.control"})},
        )
        receipt = runtime.start()
        assert receipt.degraded_count == 0
        assert len(receipt.bindings) == 1
        binding = receipt.bindings[0]
        assert binding.mount == "application.monitor"
        assert binding.operations == frozenset({"monitor.control"})

        profile = client_config.communication["profiles"]["client-curve-v1"]
        direct_transport = transport_factory(profile, service_identity)

        # Internal owner operations have no registered public capability.
        for operation, arguments in (
            ("monitor.claim", {"schema_version": 2, "command_id": "freeze-primary"}),
            (
                "monitor.acknowledge",
                {
                    "schema_version": 2,
                    "command_id": "freeze-primary",
                    "outcome": "applied",
                    "effect_ref": "synthetic",
                    "effect_result": {"action": "freeze"},
                },
            ),
        ):
            reply = _direct_request(
                direct_transport,
                binding,
                operation=operation,
                arguments=arguments,
            )
            assert reply.payload["ok"] is False
            assert reply.payload["code"] == "CAPABILITY_UNAVAILABLE"

        primary = _signed_request("freeze-primary")
        wrong_wire_scope = _direct_request(
            direct_transport,
            binding,
            operation="monitor.control",
            arguments=primary,
            scope=WireScope(
                execution_domain_ref=_WIRE_SCOPE.execution_domain_ref,
                account_ref="another-synthetic-account",
            ),
        )
        assert wrong_wire_scope.payload["ok"] is False
        assert wrong_wire_scope.payload["code"] == "SCOPE_DENIED"

        invalid_claims = (
            _signed_request("reject-cross-account", account_id="another-account"),
            _signed_request("reject-cross-scope", scope="account:another"),
            _signed_request("reject-cross-issuer", issuer="operator:other-desk"),
            _signed_request(
                "reject-reserved-cause",
                action=ControlAction.RESUME,
                target_freeze_cause_id=_DISPATCH_FREEZE_CAUSE,
            ),
        )
        for arguments in invalid_claims:
            with pytest.raises(ServiceRuntimeError) as denied:
                runtime.call("application.monitor", "monitor.control", arguments)
            assert denied.value.code == "SCOPE_DENIED"

        # The application request is durably uncertain after the real Risk
        # freeze, because its first owner acknowledgement is deliberately lost.
        uncertain = runtime.call("application.monitor", "monitor.control", primary)
        assert uncertain["command_id"] == "freeze-primary"
        assert uncertain["status"] == "unknown"
        assert uncertain["outcome"] == "unknown"
        assert uncertain["owner_final"] is False
        assert uncertain["reconciliation_required"] is True
        assert uncertain["effect_ref"] is None
        assert uncertain["effect_result"] is None
        assert _ledger_storage(paths["control"], "freeze-primary") == (
            "unknown",
            "risk-owner:synthetic",
            "unknown",
            None,
        )
        pending_manual_effects = _risk_freezes_for_reason(
            paths["risk"], "synthetic integration control"
        )
        assert len(pending_manual_effects) == 1
        assert pending_manual_effects[0][1] == 1
        assert pending_manual_effects[0][0] == _freeze_cause_id("freeze-primary")

        expected_cause = _freeze_cause_id("freeze-primary")
        resume = _signed_request(
            "resume-primary",
            action=ControlAction.RESUME,
            issuer_sequence=2,
            target_freeze_cause_id=expected_cause,
        )

        # Submitting the later RESUME is the recovery trigger. The owner must
        # first recover and durably finalize this exact same-executor v2
        # UNKNOWN FREEZE, then apply RESUME against its deterministic cause.
        resumed = runtime.call("application.monitor", "monitor.control", resume)
        assert resumed["command_id"] == "resume-primary"
        assert resumed["status"] == "acknowledged"
        assert resumed["outcome"] == "applied"
        assert resumed["effect_ref"] == expected_cause
        assert resumed["effect_result"]["cause_id"] == expected_cause
        assert resumed["effect_result"]["resolution_status"] == "CLEARED"
        assert resumed["effect_result"]["has_other_active_freezes"] is True
        assert resumed["effect_result"]["account_still_frozen"] is True
        assert "tradable" not in resumed["effect_result"]
        assert _ledger_storage(paths["control"], "freeze-primary") == (
            "acknowledged",
            "risk-owner:synthetic",
            "applied",
            expected_cause,
        )

        recovered = runtime.call("application.monitor", "monitor.control", primary)
        assert recovered["command_id"] == "freeze-primary"
        assert recovered["status"] == "acknowledged"
        assert recovered["outcome"] == "applied"
        assert recovered["owner_final"] is True
        assert recovered["reconciliation_required"] is False
        assert recovered["effect_ref"] == expected_cause
        assert recovered["effect_result"]["action"] == "freeze"
        assert recovered["effect_result"]["cause_id"] == expected_cause
        assert recovered["effect_result"]["activation_status"] == "ALREADY_ACTIVE"
        assert recovered["effect_result"]["cause_active"] is True
        assert pending_manual_effects[0][0] == expected_cause

        # An exact delayed FREEZE retry after its later RESUME only returns the
        # immutable terminal projection; it cannot restore the cleared cause.
        assert runtime.call("application.monitor", "monitor.control", primary) == recovered

        direct_transport.close()
        direct_transport = None
        runtime.close()
        assert runtime.cleanup_failures == 0
        runtime = None

        parent.send("close")
        assert parent.poll(15), "spawned monitor-control host did not close normally"
        shutdown = parent.recv()
        assert shutdown[0] == "closed", shutdown
        assert shutdown[2] == "closed"
        process.join(15)
        assert process.exitcode == 0
        assert not process.is_alive()

        ledger = DurableControlLedger(paths["control"], clock=lambda: _NOW)
        persisted_freeze = ledger.get("freeze-primary")
        persisted_resume = ledger.get("resume-primary")
        assert persisted_freeze is not None
        assert persisted_freeze.status is ControlStatus.ACKNOWLEDGED
        assert persisted_freeze.outcome == "applied"
        assert persisted_freeze.effect_ref == recovered["effect_ref"]
        assert persisted_resume is not None
        assert persisted_resume.status is ControlStatus.ACKNOWLEDGED
        assert persisted_resume.outcome == "applied"
        assert persisted_resume.effect_ref == recovered["effect_ref"]

        for rejected_id in (
            "reject-cross-account",
            "reject-cross-scope",
            "reject-cross-issuer",
            "reject-reserved-cause",
        ):
            assert _ledger_storage(paths["control"], rejected_id) is None

        risk_gate = DurableRiskGate(paths["risk"], _RISK_POLICY, clock=lambda: _NOW)
        try:
            assert set(risk_gate.active_freeze_reasons(_RISK_SCOPE)) == {
                _INDEPENDENT_FREEZE_REASON,
                _DISPATCH_FREEZE_CAUSE,
            }
            with pytest.raises(RiskDeniedError) as blocked:
                risk_gate.reserve(_risk_intent("still-blocked-after-manual-resume"))
            assert blocked.value.code == "FROZEN"
        finally:
            risk_gate.close()

        manual_cause = recovered["effect_ref"]
        assert _risk_freeze_row(paths["risk"], manual_cause) == (manual_cause, 0)
        assert shutdown[1] == {
            "risk_freeze_calls": 2,
            "risk_resume_calls": 1,
            "lost_ack_calls": 1,
        }
    finally:
        active_exception = sys.exc_info()[1]
        cleanup_diagnostics: list[str] = []
        if direct_transport is not None:
            try:
                direct_transport.close()
            except Exception as error:
                code = getattr(error, "code", type(error).__name__)
                cleanup_diagnostics.append(f"direct_transport_close:{code}")
        if runtime is not None:
            try:
                runtime.close()
                if runtime.state != "closed" or runtime.cleanup_failures != 0:
                    cleanup_diagnostics.append("client_runtime_close_incomplete")
            except Exception as error:
                code = getattr(error, "code", type(error).__name__)
                cleanup_diagnostics.append(f"client_runtime_close:{code}")
        if process.is_alive():
            with suppress(Exception):
                parent.send("close")
            process.join(10)
        if process.is_alive():
            cleanup_diagnostics.append("host_process_forced_termination")
            process.terminate()
            process.join(5)
        if shutdown is None and parent.poll(1):
            child_result = parent.recv()
            if not isinstance(child_result, tuple) or not child_result:
                cleanup_diagnostics.append("host_close_reply_invalid")
            elif child_result[0] == "error":
                code = child_result[1] if len(child_result) > 1 else "unknown"
                cleanup_diagnostics.append(f"host_close:{code}")
                if len(child_result) > 2 and child_result[2] not in (None, "closed"):
                    cleanup_diagnostics.append(f"host_state:{child_result[2]}")
                if len(child_result) > 3 and child_result[3]:
                    cleanup_diagnostics.extend(f"host_cleanup:{item}" for item in child_result[3])
            elif child_result[0] == "closed":
                if len(child_result) > 2 and child_result[2] != "closed":
                    cleanup_diagnostics.append(f"host_state:{child_result[2]}")
            else:
                cleanup_diagnostics.append("host_close_reply_unexpected")
        elif shutdown is None and process.exitcode is not None:
            cleanup_diagnostics.append("host_close_reply_missing")
        parent.close()
        if process.exitcode is not None:
            process.close()
        if cleanup_diagnostics:
            detail = "; ".join(cleanup_diagnostics)
            if active_exception is not None:
                active_exception.add_note(f"synthetic cleanup diagnostics: {detail}")
            else:
                pytest.fail(f"synthetic cleanup diagnostics: {detail}")
