"""Authenticated same-generation Gateway control-intent owner intake.

Local Registry, owner and SQLite journal only.  No provider, network, account,
Risk engine retry or production Gateway profile is enabled by this suite.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.control_intent_history import (
    GatewayControlIntentHistory,
    GatewayControlIntentOrigin,
    GatewayControlIntentSpec,
)
from bt_api_gateway.control_intent_wire import CONTROL_INTENT_APPLY_OPERATION
from bt_api_gateway.direct_command_journal import DirectCommandJournalError

from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayControlIntentRoute,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_cancel_owner import owner_for
from tests.unit.test_service_registry import _next_request, _register

ACCOUNT = WireScope("domain", account_ref="account")
RISK_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT, "risk-principal", "risk-service"
)
OTHER_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT, "other-principal", "other-application"
)
PEER = WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway-service")
LEGACY_OPERATIONS = frozenset(
    {
        "direct.control.identity",
        "direct.control.apply",
        "direct.control.status",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
OPERATIONS = LEGACY_OPERATIONS | {CONTROL_INTENT_APPLY_OPERATION}
FINGERPRINT = "a" * 64


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _request_digest(
    cause_id: str, mode: str = "STOP_NEW", latch_id: str | None = None
) -> str:
    return _digest(
        ["control-intent-v1", cause_id, mode, "account", "", latch_id]
    )


def _control_origin() -> str:
    return "risk:" + _digest(
        [RISK_IDENTITY.principal_ref, RISK_IDENTITY.actor_ref]
    )


@pytest.fixture
def port(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    assert owner.start(2)
    registry = None
    try:
        def install():
            control = harness.journal.enable_controls(harness.guard)
            history = harness.journal.enable_control_intent_history(harness.guard)
            return control, history

        control, history = owner.submit_service_call(install).result(timeout_seconds=2)
        route = GatewayControlIntentRoute(
            "application.direct_write",
            "gateway-service",
            "gateway-a",
            "scope-a",
            PEER,
            FINGERPRINT,
        )
        control_grant = GatewayControlGrant(
            RISK_IDENTITY.actor_ref,
            True,
            frozenset(),
            LEGACY_OPERATIONS,
            kind="risk",
            modes=frozenset({"STOP_NEW", "FREEZE"}),
        )
        binding = GatewayDirectServiceBinding(
            harness.journal,
            {"strategy-principal": GatewayDirectGrant("strategy", frozenset({"X"}))},
            owner=owner,
            control=control,
            control_grants={RISK_IDENTITY.principal_ref: control_grant},
            control_intent_route=route,
        )
        capability = ServiceCapability(
            "gateway-a",
            "service.direct_write",
            OPERATIONS,
            frozenset({CONTROL_INTENT_APPLY_OPERATION}),
        )
        resource = ResourceGrant(
            "gateway-a", "service.direct_write", "scope-a", ACCOUNT, OPERATIONS
        )
        registry = ServiceRegistry(
            ":memory:",
            service_id="gateway-service",
            capabilities=(capability,),
            grants={
                RISK_IDENTITY.principal_ref: (
                    PrincipalGrant(RISK_IDENTITY, (resource,)),
                ),
                OTHER_IDENTITY.principal_ref: (
                    PrincipalGrant(OTHER_IDENTITY, (resource,)),
                ),
            },
            policy_version="policy",
            requirements_version="requirements",
            lease_seconds=10,
            clock=lambda: 100.0,
        )
        for operation in OPERATIONS:
            registry.register_handler(
                "gateway-a",
                operation,
                lambda context, arguments, op=operation: binding.handle(
                    op, context=context, arguments=arguments
                ),
            )
        registrations = {}
        for identity in (RISK_IDENTITY, OTHER_IDENTITY):
            response = _register(
                registry,
                principal=identity.principal_ref,
                identity=identity,
                scope=ACCOUNT,
                key="registration-" + identity.principal_ref,
                client_session_id="session-" + identity.principal_ref,
                instance_id="gateway-a",
                port="service.direct_write",
                scope_ref="scope-a",
                operations=tuple(sorted(OPERATIONS)),
            )
            assert response["ok"], response
            registrations[identity.principal_ref] = response["result"]

        registration = registrations[RISK_IDENTITY.principal_ref]
        origin = GatewayControlIntentOrigin(
            schema_version=1,
            account_scope=harness.journal.account_scope,
            journal_id=harness.journal.journal_id,
            owner_generation=harness.journal.generation,
            mount=route.mount,
            service_id=route.service_id,
            instance_id=route.instance_id,
            scope_ref=route.scope_ref,
            registration_id=registration["registration_id"],
            service_epoch=registration["service_epoch"],
            peer_principal_ref=PEER.principal_ref,
            peer_actor_ref=PEER.actor_ref,
            server_key_fingerprint=FINGERPRINT,
            operations=OPERATIONS,
        )

        def arguments(cause_id="risk-stop", *, mode="STOP_NEW", latch_id=None):
            return {
                "schema_version": 1,
                "cause_id": cause_id,
                "mode": mode,
                "scope_kind": "account",
                "scope_value": "",
                "latch_id": latch_id,
                "request_digest": _request_digest(cause_id, mode, latch_id),
                "binding_digest": origin.digest,
                "expected_journal_id": harness.journal.journal_id,
                "expected_generation": harness.journal.generation,
            }

        def call(request, *, identity=RISK_IDENTITY):
            current = registrations[identity.principal_ref]
            return registry.handle(
                identity.principal_ref,
                _next_request(
                    CONTROL_INTENT_APPLY_OPERATION,
                    {
                        "registration_id": current["registration_id"],
                        "service_epoch": current["service_epoch"],
                        "instance_id": "gateway-a",
                        "arguments": request,
                    },
                    identity=identity,
                    scope=ACCOUNT,
                ),
            )

        def legacy_apply():
            current = registrations[RISK_IDENTITY.principal_ref]
            return registry.handle(
                RISK_IDENTITY.principal_ref,
                _next_request(
                    "direct.control.apply",
                    {
                        "registration_id": current["registration_id"],
                        "service_epoch": current["service_epoch"],
                        "instance_id": "gateway-a",
                        "arguments": {
                            "cause_id": "legacy",
                            "mode": "STOP_NEW",
                            "expected_journal_id": harness.journal.journal_id,
                            "expected_generation": harness.journal.generation,
                        },
                    },
                    identity=RISK_IDENTITY,
                    scope=ACCOUNT,
                ),
            )

        def state(request):
            spec = GatewayControlIntentSpec(
                schema_version=1,
                intent_kind="control",
                intent_id=request["cause_id"],
                request_digest=request["request_digest"],
                binding=origin,
                mode=request["mode"],
                request_source=None,
                latch_id=request["latch_id"],
                wait_cause_id=None,
                scope_kind="account",
                scope_value="",
                control_origin=_control_origin(),
            )

            def observe():
                receipt = history.read(spec)
                cause = None
                with suppress(DirectCommandJournalError):
                    cause = control.get(request["cause_id"])
                with harness.journal._transaction() as database:
                    history_count = database.execute(
                        "SELECT count(*) FROM direct_control_intent_history"
                    ).fetchone()[0]
                    cause_count = database.execute(
                        "SELECT count(*) FROM direct_control_causes"
                    ).fetchone()[0]
                return receipt, cause, history_count, cause_count

            return owner.submit_service_call(observe).result(timeout_seconds=2)

        yield SimpleNamespace(
            arguments=arguments,
            call=call,
            legacy_apply=legacy_apply,
            state=state,
            registry=registry,
            registrations=registrations,
            harness=harness,
            owner=owner,
            control=control,
            history=history,
            route=route,
            grant=control_grant,
        )
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def test_authenticated_intent_apply_and_exact_replay_are_single_effect(port):
    request = port.arguments()
    expected = {
        "status": "OBSERVED",
        "cause_id": "risk-stop",
        "version": 1,
        "active": True,
    }
    first = port.call(request)
    second = port.call(request)
    assert first["ok"] and first["result"] == expected
    assert second["ok"] and second["result"] == expected
    assert set(first["result"]) == {"status", "cause_id", "version", "active"}
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None
    assert (receipt.state, receipt.version, receipt.consumed_generation) == (
        "CONSUMED",
        2,
        port.harness.journal.generation,
    )
    assert cause["cause_id"] == "risk-stop"
    assert (history_count, cause_count) == (1, 1)


@pytest.mark.parametrize(
    "change,code",
    [
        ({"request_digest": "0" * 64}, "CONTROL_INTENT_REQUEST_DIGEST_MISMATCH"),
        ({"binding_digest": "0" * 64}, "CONTROL_INTENT_BINDING_MISMATCH"),
        ({"expected_journal_id": "stale-journal"}, "JOURNAL_ID_MISMATCH"),
        ({"expected_generation": "stale-generation"}, "JOURNAL_GENERATION_MISMATCH"),
        ({"proof_id": "client-proof"}, "INVALID_REQUEST"),
        ({"mode": []}, "INVALID_REQUEST"),
        ({"cause_id": "forged\nwarning"}, "INVALID_REQUEST"),
    ],
)
def test_invalid_wire_binding_and_generation_never_create_an_effect(port, change, code):
    request = port.arguments()
    request.update(change)
    response = port.call(request)
    assert response["ok"]
    assert response["result"] == {"status": "REQUEST_REJECTED", "code": code}

    clean = port.arguments()
    receipt, cause, history_count, cause_count = port.state(clean)
    assert receipt is None and cause is None
    assert (history_count, cause_count) == (0, 0)


def test_other_authenticated_application_has_no_risk_intent_grant(port):
    request = port.arguments("other-stop")
    response = port.call(request, identity=OTHER_IDENTITY)
    assert response["ok"]
    assert response["result"] == {
        "status": "REQUEST_REJECTED",
        "code": "SCOPE_DENIED",
    }
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is None and cause is None
    assert (history_count, cause_count) == (0, 0)


def test_unmapped_history_failure_is_unknown_not_a_safe_rejection(
    port, monkeypatch
):
    def uncertain(_history, _spec, *, before_commit=None):
        assert callable(before_commit)
        raise DirectCommandJournalError("CONTROL_INTENT_PREPARE_UNCERTAIN")

    monkeypatch.setattr(GatewayControlIntentHistory, "prepare", uncertain)
    request = port.arguments("uncertain")
    response = port.call(request)
    assert response["ok"]
    assert response["result"] == {"status": "UNKNOWN", "cause_id": "uncertain"}
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is None and cause is None
    assert (history_count, cause_count) == (0, 0)


def test_lost_prepare_commit_reply_is_unknown_then_exact_replay_consumes_once(
    port, monkeypatch
):
    journal = port.harness.journal
    original_commit = journal._commit

    def lost_reply(database):
        original_commit(database)
        raise RuntimeError("synthetic lost prepare reply")

    monkeypatch.setattr(journal, "_commit", lost_reply)
    request = port.arguments("lost-prepare")
    first = port.call(request)
    assert first["ok"]
    assert first["result"] == {
        "status": "UNKNOWN",
        "cause_id": "lost-prepare",
    }
    monkeypatch.setattr(journal, "_commit", original_commit)

    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None and receipt.state == "PREPARED"
    assert cause is None
    assert (history_count, cause_count) == (1, 0)

    replay = port.call(request)
    assert replay["ok"]
    assert replay["result"] == {
        "status": "OBSERVED",
        "cause_id": "lost-prepare",
        "version": 1,
        "active": True,
    }
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None and receipt.state == "CONSUMED"
    assert cause is not None and cause["cause_id"] == "lost-prepare"
    assert (history_count, cause_count) == (1, 1)


def test_lost_effect_commit_reply_is_unknown_then_replay_stays_single_effect(
    port, monkeypatch
):
    journal = port.harness.journal
    original_commit = journal._commit

    def lost_effect_reply(database):
        original_commit(database)
        history_row = database.execute(
            "SELECT state FROM direct_control_intent_history "
            "WHERE intent_id='lost-effect'"
        ).fetchone()
        cause_row = database.execute(
            "SELECT cause_id FROM direct_control_causes WHERE cause_id='lost-effect'"
        ).fetchone()
        if history_row is not None and history_row[0] == "CONSUMED" and cause_row:
            raise RuntimeError("synthetic lost effect reply")

    monkeypatch.setattr(journal, "_commit", lost_effect_reply)
    request = port.arguments("lost-effect")
    first = port.call(request)
    assert first["ok"]
    assert first["result"] == {"status": "UNKNOWN", "cause_id": "lost-effect"}
    monkeypatch.setattr(journal, "_commit", original_commit)

    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None and receipt.state == "CONSUMED"
    assert cause is not None and cause["cause_id"] == "lost-effect"
    assert (history_count, cause_count) == (1, 1)

    replay = port.call(request)
    assert replay["ok"]
    assert replay["result"] == {
        "status": "OBSERVED",
        "cause_id": "lost-effect",
        "version": 1,
        "active": True,
    }
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None and receipt.state == "CONSUMED"
    assert cause is not None and cause["cause_id"] == "lost-effect"
    assert (history_count, cause_count) == (1, 1)


def test_admission_loss_after_prepare_stays_unknown_with_no_effect(
    port, monkeypatch
):
    original_scope = ServiceDispatchAdmission.scope
    calls = [0]

    def fail_second_scope(admission):
        calls[0] += 1
        if calls[0] == 2:
            raise ServiceDispatchAdmissionError("SCOPE_DENIED")
        return original_scope(admission)

    monkeypatch.setattr(ServiceDispatchAdmission, "scope", fail_second_scope)
    request = port.arguments("prepared-only")
    response = port.call(request)
    assert response["ok"]
    assert response["result"] == {
        "status": "UNKNOWN",
        "cause_id": "prepared-only",
    }
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None and receipt.state == "PREPARED"
    assert cause is None
    assert (history_count, cause_count) == (1, 0)


def test_registration_revocation_after_prepare_is_observed_before_effect(
    port, monkeypatch
):
    original_scope = ServiceDispatchAdmission.scope
    calls = [0]

    def revoke_before_second_scope(admission):
        calls[0] += 1
        if calls[0] == 2:
            registration = port.registrations[RISK_IDENTITY.principal_ref]
            response = port.registry.handle(
                RISK_IDENTITY.principal_ref,
                _next_request(
                    "service.unregister",
                    {
                        "registration_id": registration["registration_id"],
                        "service_epoch": registration["service_epoch"],
                    },
                    identity=RISK_IDENTITY,
                    scope=ACCOUNT,
                ),
            )
            assert response["ok"], response
        return original_scope(admission)

    monkeypatch.setattr(
        ServiceDispatchAdmission, "scope", revoke_before_second_scope
    )
    request = port.arguments("revoked-after-prepare")
    response = port.call(request)
    assert response["ok"]
    assert response["result"] == {
        "status": "UNKNOWN",
        "cause_id": "revoked-after-prepare",
    }
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is not None and receipt.state == "PREPARED"
    assert cause is None
    assert (history_count, cause_count) == (1, 0)


def test_owner_timeout_returns_unknown_and_closes_active_admission(
    port, monkeypatch
):
    original_prepare = GatewayControlIntentHistory.prepare
    entered, release = threading.Event(), threading.Event()

    def slow_prepare(history, spec, *, before_commit=None):
        entered.set()
        assert release.wait(2)
        return original_prepare(history, spec, before_commit=before_commit)

    monkeypatch.setattr(GatewayControlIntentHistory, "prepare", slow_prepare)
    request = port.arguments("owner-timeout")
    response = port.call(request)
    assert entered.is_set()
    assert response["ok"]
    assert response["result"] == {
        "status": "UNKNOWN",
        "cause_id": "owner-timeout",
    }
    release.set()
    port.owner.submit_service_call(lambda: True).result(timeout_seconds=2)
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is None and cause is None
    assert (history_count, cause_count) == (0, 0)


def test_success_reply_budget_is_checked_before_any_history_write(port, monkeypatch):
    def budget(_request, reply, _limit):
        result = reply.get("result", {})
        return result.get("status") != "OBSERVED"

    monkeypatch.setattr(port.registry, "_reply_size_checker", budget)
    request = port.arguments("budget-denied")
    response = port.call(request)
    assert response["ok"] is False
    # Registry conservatively projects a write-handler reply budget failure as
    # HANDLER_FAILED; the owner precheck is what proves no local effect exists.
    assert response["code"] == "HANDLER_FAILED"
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is None and cause is None
    assert (history_count, cause_count) == (0, 0)


def test_legacy_control_write_is_not_a_registered_write_on_intent_route(port):
    response = port.legacy_apply()
    assert response["ok"] is False
    assert response["code"] == "CAPABILITY_UNAVAILABLE"
    request = port.arguments("legacy")
    receipt, cause, history_count, cause_count = port.state(request)
    assert receipt is None and cause is None
    assert (history_count, cause_count) == (0, 0)


def test_history_enabled_binding_rejects_legacy_only_and_non_risk_composition(port):
    grants = {
        "strategy-principal": GatewayDirectGrant("strategy", frozenset({"X"}))
    }
    with pytest.raises(ValueError, match="^INVALID_CONTROL_INTENT_BINDING$"):
        GatewayDirectServiceBinding(
            port.harness.journal,
            grants,
            owner=port.owner,
            control=port.control,
            control_grants={RISK_IDENTITY.principal_ref: port.grant},
        )

    operator_grant = GatewayControlGrant(
        "operator",
        True,
        frozenset(),
        LEGACY_OPERATIONS,
        kind="operator",
    )
    with pytest.raises(ValueError, match="^INVALID_CONTROL_INTENT_BINDING$"):
        GatewayDirectServiceBinding(
            port.harness.journal,
            grants,
            owner=port.owner,
            control=port.control,
            control_grants={"operator": operator_grant},
            control_intent_route=port.route,
        )

    storage_only = GatewayDirectServiceBinding(
        port.harness.journal,
        grants,
        owner=port.owner,
    )
    assert CONTROL_INTENT_APPLY_OPERATION not in storage_only.supported_operations


def test_intent_route_requires_history_enabled_at_composition(tmp_path):
    harness = Harness(tmp_path)
    owner = owner_for(harness.factory)
    assert owner.start(2)
    try:
        control = owner.submit_service_call(
            lambda: harness.journal.enable_controls(harness.guard)
        ).result(timeout_seconds=2)
        route = GatewayControlIntentRoute(
            "application.direct_write",
            "gateway-service",
            "gateway-a",
            "scope-a",
            PEER,
            FINGERPRINT,
        )
        grant = GatewayControlGrant(
            RISK_IDENTITY.actor_ref,
            True,
            frozenset(),
            LEGACY_OPERATIONS,
            kind="risk",
        )
        with pytest.raises(ValueError, match="^INVALID_CONTROL_INTENT_BINDING$"):
            GatewayDirectServiceBinding(
                harness.journal,
                {"strategy-principal": GatewayDirectGrant("strategy", frozenset({"X"}))},
                owner=owner,
                control=control,
                control_grants={RISK_IDENTITY.principal_ref: grant},
                control_intent_route=route,
            )
    finally:
        assert owner.close(2)
