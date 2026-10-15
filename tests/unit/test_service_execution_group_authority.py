from __future__ import annotations

import multiprocessing
import os
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    CancelObservation,
    ExecutionScope,
    ExecutionState,
    GroupPermitBinding,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
    WriterLease,
    group_leg_intent_id,
    payload_sha256,
    risk_account_scope_fingerprint,
    writer_lease_fingerprint,
)
from bt_api_execution.service_order_entry import ExecutionOrderEntryService

from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
    GroupAuthorityBusy,
    GroupAuthorityReconciliationRejected,
    GroupCoordinatorDenied,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
)

_PRINCIPAL = "group-coordinator-principal"
_SECOND_PRINCIPAL = "group-coordinator-principal-b"
_PORTFOLIO = "portfolio-a"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-a"
_DOMAIN = "paper:venue-a"
_GROUP = "group-a"
_LEG = "leg-a"
_RISK_SCOPE_KEY = '{"account_ref":"account-a","provider":"venue-a"}'
_REGISTRY_OPERATION = "coordinator.identity"


def _identity(principal: str = _PRINCIPAL) -> WireIdentity:
    return WireIdentity(WireRole.SERVICE, principal, "coordinator-worker")


def _scope(*, group: str | None = None, leg: str | None = None) -> WireScope:
    return WireScope(
        execution_domain_ref="paper:venue-a",
        account_ref=_ACCOUNT,
        strategy_ref=_STRATEGY,
        portfolio_ref=_PORTFOLIO,
        group_id=group,
        leg_id=leg,
    )


def _operation_scope(
    registered_scope: WireScope,
    *,
    group: str = _GROUP,
    leg: str = _LEG,
) -> WireScope:
    return replace(registered_scope, group_id=group, leg_id=leg)


def _request(
    operation: str,
    payload: dict[str, Any],
    *,
    identity: WireIdentity,
    scope: WireScope,
) -> WireEnvelope:
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid.uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.control"
            if operation.startswith("service.")
            else "sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-10-15T00:00:00Z",
        ),
        payload=payload,
    )


def _registered_contexts(
    path: Path,
    *,
    principals: tuple[str, ...] = (_PRINCIPAL,),
    scope: WireScope | None = None,
) -> tuple[ServiceRegistry, list[ServiceCallContext]]:
    scope = scope or _scope()
    operations = frozenset({_REGISTRY_OPERATION})
    capability = ServiceCapability(
        "execution-owner",
        "service.order_entry",
        operations,
    )
    resource = ResourceGrant(
        "execution-owner",
        "service.order_entry",
        "portfolio-grant-a",
        scope,
        operations,
    )
    registry = ServiceRegistry(
        path,
        service_id="execution-owner-service",
        capabilities=(capability,),
        grants={
            principal: (PrincipalGrant(_identity(principal), (resource,)),)
            for principal in principals
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=60,
    )
    contexts: list[ServiceCallContext] = []
    registry.register_handler(
        "execution-owner",
        _REGISTRY_OPERATION,
        lambda context, _arguments: contexts.append(context) or {"registration_seen": True},
    )
    return registry, contexts


def _register_and_dispatch(
    registry: ServiceRegistry,
    *,
    session: str,
    principal: str = _PRINCIPAL,
    scope: WireScope | None = None,
) -> ServiceCallContext:
    identity = _identity(principal)
    transport_principal = principal
    scope = scope or _scope()
    registration_response = registry.handle(
        transport_principal,
        _request(
            "service.register",
            {
                "registration_key": f"registration-key-{session}",
                "client_session_id": session,
                "instance_id": "execution-owner",
                "port": "service.order_entry",
                "scope_ref": "portfolio-grant-a",
                "operations": (_REGISTRY_OPERATION,),
            },
            identity=identity,
            scope=scope,
        ),
    )
    assert registration_response["ok"] is True, registration_response
    registration = registration_response["result"]
    dispatch_response = registry.handle(
        transport_principal,
        _request(
            _REGISTRY_OPERATION,
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": "execution-owner",
                "arguments": {},
            },
            identity=identity,
            scope=scope,
        ),
    )
    assert dispatch_response["ok"] is True, dispatch_response
    assert dispatch_response["result"] == {"registration_seen": True}
    return registry._captured_contexts[-1]  # type: ignore[attr-defined]


# Capture through a small delegating fixture without replacing Registry's
# public handler path. The factory installs this list for each test registry.
def _capture_registry_contexts(
    path: Path,
    *,
    scope: WireScope | None = None,
) -> tuple[ServiceRegistry, list[ServiceCallContext]]:
    registry, contexts = _registered_contexts(
        path,
        principals=(_PRINCIPAL, _SECOND_PRINCIPAL),
        scope=scope,
    )
    registry._captured_contexts = contexts  # type: ignore[attr-defined]
    return registry, contexts


def _execution_scope(*, session_date: str = "20261015") -> ExecutionScope:
    return ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, session_date)


def _intent_and_binding(
    *,
    coordinator_principal_ref: str = _PRINCIPAL,
    session_date: str = "20261015",
    group_id: str = _GROUP,
    leg_id: str = _LEG,
) -> tuple[OrderIntent, GroupPermitBinding]:
    scope = _execution_scope(session_date=session_date)
    deadline = 9_000_000_000_000
    intent = OrderIntent(
        intent_id=group_leg_intent_id(group_id, leg_id),
        scope=scope,
        signal_id="signal-a",
        instrument="BTC-USD",
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        price=Decimal("60"),
        time_in_force=TimeInForce.GTC,
        deadline_ns=deadline,
    )
    binding = GroupPermitBinding(
        group_id=group_id,
        leg_id=leg_id,
        portfolio_ref=_PORTFOLIO,
        strategy_id=_STRATEGY,
        account_ref=_ACCOUNT,
        coordinator_principal_ref=coordinator_principal_ref,
        risk_scope_fingerprint=risk_account_scope_fingerprint(_RISK_SCOPE_KEY),
        permit_id=f"permit-{group_id}-{leg_id}",
        group_fingerprint=payload_sha256({"group": group_id}),
        policy_fingerprint=payload_sha256({"policy": "risk-v1"}),
        reserved_intent_hash=payload_sha256({"reserved": "maximum"}),
        exposure_profile_fingerprint=payload_sha256({"profile": "btc-long"}),
        actual_child_intent_hash=payload_sha256({"actual": "risk-child"}),
        order_intent_hash=intent.fingerprint,
        deadline_ns=deadline,
    )
    return intent, binding


def _writer_lease(*, session_date: str = "20261015") -> WriterLease:
    execution_scope = _execution_scope(session_date=session_date)
    return WriterLease(
        execution_scope.account_key,
        "execution-owner",
        17,
        9_000_000_000_000,
    )


def _authorization(authority, context, *, intent, binding, lease):
    return authority.guard.hold_group_leg_authorization(
        principal_ref=context.principal_ref,
        identity=context.identity,
        wire_scope=_operation_scope(
            context.scope,
            group=binding.group_id,
            leg=binding.leg_id,
        ),
        execution_scope=intent.scope,
        intent=intent,
        binding=binding,
        writer_lease=lease,
    )


def _child_crash_with_durable_hold(
    database_path: str,
    context: ServiceCallContext,
    intent: OrderIntent,
    binding: GroupPermitBinding,
    lease: WriterLease,
    send_connection,
) -> None:
    authority = ExecutionGroupAuthority(database_path, monotonic_clock_ns=lambda: 1)
    with (
        authority.guard.bind_service_call(context, execution_scope=_execution_scope()),
        _authorization(
            authority,
            context,
            intent=intent,
            binding=binding,
            lease=lease,
        ),
    ):
        hold = authority.pending_holds()[0]
        send_connection.send(hold.hold_id)
        send_connection.close()
        os._exit(73)


class _SyntheticRiskScope:
    def __init__(self, execution_scope: ExecutionScope) -> None:
        self.provider = execution_scope.provider
        self.environment = execution_scope.environment
        self.key = _RISK_SCOPE_KEY


class RiskGroupLegClaimReceipt:
    def __init__(self, binding: GroupPermitBinding, intent: OrderIntent) -> None:
        scope = _SyntheticRiskScope(intent.scope)
        permit = type(
            "Permit",
            (),
            {
                "permit_id": binding.permit_id,
                "intent_id": intent.intent_id,
                "scope": scope,
            },
        )()
        claim = type(
            "Claim",
            (),
            {
                "scope": scope,
                "permit_id": binding.permit_id,
                "intent_id": intent.intent_id,
                "intent_hash": binding.reserved_intent_hash,
            },
        )()
        self.scope = scope
        self.permit = permit
        self.claim = claim
        self.group_id = binding.group_id
        self.leg_id = binding.leg_id
        self.group_fingerprint = binding.group_fingerprint
        self.policy_fingerprint = binding.policy_fingerprint
        self.deadline_ns = binding.deadline_ns
        self.permit_reference = binding.permit_id
        self.reserved_intent_hash = binding.reserved_intent_hash
        self.actual_intent_hash = binding.actual_child_intent_hash
        self.exposure_profile_fingerprint = binding.exposure_profile_fingerprint
        self.actual_quantity = intent.quantity
        self.actual_notional = intent.quantity * (intent.price or Decimal("1"))


class _SyntheticGroupRiskAdmission:
    def __init__(self, binding: GroupPermitBinding) -> None:
        self._binding = binding
        self.claimed: list[str] = []
        self.settled: list[str] = []

    def reserve(self, _intent: OrderIntent) -> None:
        raise AssertionError("group submission must not reserve through the singleton path")

    def claim_group_leg(self, intent: OrderIntent, **_arguments: object) -> object:
        self.claimed.append(intent.intent_id)
        return RiskGroupLegClaimReceipt(self._binding, intent)

    def settle(self, permit_reference: str) -> None:
        self.settled.append(permit_reference)


class _SyntheticProvider:
    def __init__(self, *, outcome: str) -> None:
        self.outcome = outcome
        self.submitted: list[str] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        if self.outcome == "unknown":
            raise RuntimeError("provider response lost")
        if self.outcome == "rejected":
            return ProviderObservation.rejected(intent.intent_id, "synthetic_reject")
        return ProviderObservation.accepted(intent.intent_id, "provider-order-group-a")

    def cancel(self, intent) -> CancelObservation:
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def test_real_registry_registration_id_allocates_generations_and_fences_old_session(
    tmp_path: Path,
) -> None:
    registry, contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3",
        completion_verifier=lambda _hold: True,
        monotonic_clock_ns=lambda: 1,
    )
    try:
        first = _register_and_dispatch(registry, session="coordinator-session-a")
        second = _register_and_dispatch(
            registry,
            session="coordinator-session-b",
            principal=_SECOND_PRINCIPAL,
        )
        assert len(contexts) == 2
        assert first.registration_id != second.registration_id
        assert first.principal_ref != second.principal_ref
        assert first.scope == second.scope

        generation_one = authority.control.activate(
            first,
            execution_scope=_execution_scope(),
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )
        assert generation_one.generation == 1
        intent, binding = _intent_and_binding()
        lease = _writer_lease()
        with (
            authority.guard.bind_service_call(first, execution_scope=_execution_scope()),
            _authorization(
                authority, first, intent=intent, binding=binding, lease=lease
            ) as authorization,
        ):
            active_hold = authority.pending_holds()[0]
            assert authorization.authorization_attempt_id == active_hold.hold_id
            assert authorization.coordinator_epoch == 1
            assert authorization.account_writer_epoch == lease.fencing_token
            assert authorization.execution_writer_lease_fingerprint == writer_lease_fingerprint(
                lease
            )
            with pytest.raises(GroupAuthorityBusy):
                authority.control.reconcile_hold(active_hold.hold_id, "premature")
            with pytest.raises(GroupAuthorityBusy):
                authority.control.revoke(first, execution_scope=_execution_scope())
            with pytest.raises(GroupAuthorityBusy):
                authority.control.activate(
                    second,
                    execution_scope=_execution_scope(session_date="20261016"),
                    risk_account_scope_key=_RISK_SCOPE_KEY,
                )
            assert len(authority.pending_holds()) == 1
        assert authority.pending_holds() == ()

        generation_two = authority.control.activate(
            second,
            execution_scope=_execution_scope(session_date="20261016"),
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )
        assert generation_two.generation == 2
        second_intent, second_binding = _intent_and_binding(
            coordinator_principal_ref=_SECOND_PRINCIPAL,
            session_date="20261016",
        )
        second_lease = _writer_lease(session_date="20261016")
        with (
            authority.guard.bind_service_call(first, execution_scope=_execution_scope()),
            pytest.raises(GroupCoordinatorDenied),
            _authorization(authority, first, intent=intent, binding=binding, lease=lease),
        ):
            pytest.fail("old registration unexpectedly acquired authority")

        with (
            authority.guard.bind_service_call(second, execution_scope=second_intent.scope),
            _authorization(
                authority,
                second,
                intent=second_intent,
                binding=second_binding,
                lease=second_lease,
            ) as authorization,
        ):
            assert authorization.coordinator_epoch == 2
        revoked = authority.control.revoke(second, execution_scope=second_intent.scope)
        assert revoked.generation == 3 and not revoked.active
        with (
            authority.guard.bind_service_call(second, execution_scope=_execution_scope()),
            pytest.raises(GroupCoordinatorDenied),
            _authorization(authority, second, intent=intent, binding=binding, lease=lease),
        ):
            pytest.fail("revoked registration unexpectedly acquired authority")
        with pytest.raises(GroupCoordinatorDenied, match="fresh ServiceRegistry registration"):
            authority.control.activate(
                second,
                execution_scope=second_intent.scope,
                risk_account_scope_key=_RISK_SCOPE_KEY,
            )
        assert authority.ready is True
    finally:
        authority.close()
        registry.close()


@pytest.mark.parametrize(
    ("provider_outcome", "expected_state", "expected_ok"),
    [
        ("accepted", ExecutionState.ACKED, True),
        ("unknown", ExecutionState.UNKNOWN, False),
        ("rejected", ExecutionState.REJECTED, True),
    ],
)
def test_normal_guard_exit_requires_exact_execution_store_completion(
    tmp_path: Path,
    provider_outcome: str,
    expected_state: ExecutionState,
    expected_ok: bool,
) -> None:
    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    execution_scope = _execution_scope()
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    intent, binding = _intent_and_binding()
    provider = _SyntheticProvider(outcome=provider_outcome)
    completion = ExecutionStoreGroupCompletionVerifier(store, execution_scopes=(execution_scope,))
    risk_admission = _SyntheticGroupRiskAdmission(binding)
    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3",
        completion_verifier=completion,
        reconciliation_verifier=lambda hold, _proof: completion(hold),
        monotonic_clock_ns=lambda: 1,
    )
    service = ExecutionOrderEntryService(
        store,
        targets={WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY): execution_scope},
        dispatch=provider,
        admission_gate=AccountScopedGroupAdmissionAdapter(
            risk_admission,
            account_scope=_SyntheticRiskScope(execution_scope),
        ),
        group_coordinator_authority=authority.guard,
    )
    try:
        context = _register_and_dispatch(registry, session="execution-store-completion")
        authority.control.activate(
            context,
            execution_scope=execution_scope,
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )

        def submit() -> dict[str, Any]:
            with authority.guard.bind_service_call(context, execution_scope=execution_scope):
                return dict(
                    service.submit_group_leg(
                        principal_ref=context.principal_ref,
                        identity=context.identity,
                        scope=_operation_scope(
                            context.scope,
                            group=binding.group_id,
                            leg=binding.leg_id,
                        ),
                        intent=intent,
                        binding=binding,
                    )
                )

        reply = submit()
        record = store.get(intent.intent_id, scope=execution_scope)
        assert record is not None, (reply, authority.pending_holds())
        assert record.state is expected_state
        assert reply["ok"] is expected_ok, (reply, record, authority.pending_holds())
        assert risk_admission.claimed == [intent.intent_id]
        assert provider.submitted == [intent.intent_id]

        if expected_state is ExecutionState.ACKED:
            assert record.provider_order_id == "provider-order-group-a"
            assert risk_admission.settled == [binding.permit_id]
            assert authority.pending_holds() == ()
            assert authority.ready is True
            # Same-ID ACK replay validates the exact stored intent, binding,
            # and historical authorization under the current caller and
            # writer lease. It returns before another Risk claim or provider
            # call and does not create a synthetic durable attempt/hold.
            attempts_before = store.list_group_authorization_attempts(
                intent.intent_id, scope=execution_scope
            )
            replay = submit()
            assert replay["ok"] is True
            replay_record = store.get(intent.intent_id, scope=execution_scope)
            assert replay_record == record
            assert (
                store.list_group_authorization_attempts(intent.intent_id, scope=execution_scope)
                == attempts_before
            )
            assert risk_admission.claimed == [intent.intent_id]
            assert provider.submitted == [intent.intent_id]
            assert authority.pending_holds() == ()
            assert authority.ready is True
        elif expected_state is ExecutionState.REJECTED:
            assert record.provider_order_id is None
            assert record.dispatch_attempts == 1
            assert record.filled_quantity == Decimal("0")
            assert record.average_price is None
            assert record.cumulative_commission is None
            assert record.unknown_reason is None
            assert record.review_required is False
            assert risk_admission.settled == [binding.permit_id]
            events = store.read_outbox(scope=execution_scope, limit=100)
            rejected_events = tuple(
                event
                for event in events
                if event.intent_id == intent.intent_id
                and event.event_type == "provider_observation"
            )
            assert len(rejected_events) == 1
            assert rejected_events[0].state is ExecutionState.REJECTED
            assert rejected_events[0].payload == {
                "provider_order_id": None,
                "filled_quantity": "0",
                "average_price": None,
                "cumulative_commission": None,
                "reason_code": "synthetic_reject",
                "source": "provider",
            }
            assert authority.pending_holds() == ()
            assert authority.ready is True
        else:
            assert record.provider_order_id is None
            assert record.review_required is True
            assert risk_admission.settled == []
            assert reply["code"] == "GROUP_AUTHORIZATION_FAILED"
            holds = authority.pending_holds()
            assert len(holds) == 1
            assert holds[0].hold_id == holds[0].authorization_attempt_id
            assert authority.ready is False
            assert submit()["code"] == "GROUP_AUTHORIZATION_FAILED"
            assert risk_admission.claimed == [intent.intent_id]
            assert provider.submitted == [intent.intent_id]
            with pytest.raises(GroupAuthorityReconciliationRejected):
                authority.control.reconcile_hold(holds[0].hold_id, "retry-or-unknown")
            assert authority.pending_holds() == holds
    finally:
        authority.close()
        store.close()
        registry.close()


def test_crash_hold_survives_restart_and_only_verified_reconciliation_releases_it(
    tmp_path: Path,
) -> None:
    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    database_path = tmp_path / "authority.sqlite3"
    authority = ExecutionGroupAuthority(database_path, monotonic_clock_ns=lambda: 1)
    try:
        captured = _register_and_dispatch(registry, session="crash-session")
        # ServiceCallContext contains a Registry-minted registration id, but
        # its optional result-size closure is process-local and not needed by
        # the hold path. Preserve the exact trusted identity/scope/registration.
        context = ServiceCallContext(
            captured.identity,
            captured.scope,
            captured.registration_id,
            captured.principal_ref,
        )
        authority.control.activate(
            context,
            execution_scope=_execution_scope(),
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )
        intent, binding = _intent_and_binding()
        lease = _writer_lease()
        authority.close()

        mp = multiprocessing.get_context("spawn")
        receive_connection, send_connection = mp.Pipe(duplex=False)
        child = mp.Process(
            target=_child_crash_with_durable_hold,
            args=(str(database_path), context, intent, binding, lease, send_connection),
        )
        child.start()
        send_connection.close()
        hold_id = receive_connection.recv()
        receive_connection.close()
        child.join(timeout=10)
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        assert child.exitcode == 73

        accepted_proofs: list[object] = []

        def verify(hold, proof: object) -> bool:
            accepted_proofs.append(proof)
            return proof == {
                "resolution": "provider_terminal",
                "hold_id": hold.hold_id,
                "intent_id": hold.intent_id,
                "execution_scope_key": hold.execution_scope_key,
                "lease_fingerprint": hold.execution_writer_lease_fingerprint,
            }

        restarted = ExecutionGroupAuthority(
            database_path,
            reconciliation_verifier=verify,
            monotonic_clock_ns=lambda: 1,
        )
        try:
            assert restarted.ready is False
            holds = restarted.pending_holds()
            assert len(holds) == 1 and holds[0].hold_id == hold_id
            with pytest.raises(GroupAuthorityBusy):
                restarted.control.revoke(context, execution_scope=_execution_scope())
            with pytest.raises(GroupAuthorityReconciliationRejected):
                restarted.control.reconcile_hold(hold_id, {"resolution": "unknown"})
            assert restarted.ready is False
            hold = holds[0]
            proof = {
                "resolution": "provider_terminal",
                "hold_id": hold.hold_id,
                "intent_id": hold.intent_id,
                "execution_scope_key": hold.execution_scope_key,
                "lease_fingerprint": hold.execution_writer_lease_fingerprint,
            }
            restarted.control.reconcile_hold(hold_id, proof)
            assert accepted_proofs == [{"resolution": "unknown"}, proof]
            assert restarted.pending_holds() == ()
            assert restarted.ready is True
            fresh = _register_and_dispatch(
                registry,
                session="crash-session-restarted",
                principal=_SECOND_PRINCIPAL,
            )
            generation_two = restarted.control.activate(
                fresh,
                execution_scope=_execution_scope(),
                risk_account_scope_key=_RISK_SCOPE_KEY,
            )
            assert generation_two.generation == 2
            with (
                restarted.guard.bind_service_call(context, execution_scope=_execution_scope()),
                pytest.raises(GroupCoordinatorDenied),
                _authorization(
                    restarted,
                    context,
                    intent=intent,
                    binding=binding,
                    lease=lease,
                ),
            ):
                pytest.fail("crashed coordinator registration was reused")
        finally:
            restarted.close()
    finally:
        registry.close()


def test_request_context_cannot_supply_generation_or_change_registered_scope(
    tmp_path: Path,
) -> None:
    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3", monotonic_clock_ns=lambda: 1
    )
    try:
        context = _register_and_dispatch(registry, session="scope-session")
        authority.control.activate(
            context,
            execution_scope=_execution_scope(),
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )
        intent, binding = _intent_and_binding()
        changed_context = ServiceCallContext(
            context.identity,
            _scope(group="different-group", leg="different-leg"),
            context.registration_id,
            context.principal_ref,
        )
        with pytest.raises(
            GroupCoordinatorDenied, match="invalid coordinator registration context"
        ):
            with authority.guard.bind_service_call(
                changed_context,
                execution_scope=_execution_scope(),
            ):
                _authorization(
                    authority,
                    changed_context,
                    intent=intent,
                    binding=binding,
                    lease=_writer_lease(),
                )
            pytest.fail("altered request scope unexpectedly acquired authority")
        with (
            pytest.raises(GroupCoordinatorDenied),
            authority.guard.bind_service_call(
                {"registration_id": context.registration_id},  # type: ignore[arg-type]
                execution_scope=_execution_scope(),
            ),
        ):
            pytest.fail("a mapping was accepted as Registry context")
        assert authority.pending_holds() == ()
    finally:
        authority.close()
        registry.close()


def test_one_registry_registration_authorizes_distinct_group_legs(tmp_path: Path) -> None:
    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3",
        completion_verifier=lambda _hold: True,
        monotonic_clock_ns=lambda: 1,
    )
    try:
        context = _register_and_dispatch(registry, session="stable-coordinator-registration")
        assert context.scope.group_id is None
        assert context.scope.leg_id is None
        authority.control.activate(
            context,
            execution_scope=_execution_scope(),
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )

        # A normal coordinator registration covers the stable portfolio /
        # account / strategy mapping. Each child is separately derived by
        # trusted server code and still passes the same active registration.
        for group_id, leg_id in (("group-a", "leg-a"), ("group-b", "leg-hedge")):
            intent, binding = _intent_and_binding(group_id=group_id, leg_id=leg_id)
            with (
                authority.guard.bind_service_call(context, execution_scope=intent.scope),
                _authorization(
                    authority,
                    context,
                    intent=intent,
                    binding=binding,
                    lease=_writer_lease(),
                ) as authorization,
            ):
                assert authorization.group_id == group_id
                assert authorization.leg_id == leg_id
                assert authority.pending_holds()[0].intent_id == intent.intent_id
            assert authority.pending_holds() == ()
        assert authority.ready is True
    finally:
        authority.close()
        registry.close()


def test_legacy_registration_with_fixed_group_leg_is_rejected(tmp_path: Path) -> None:
    fixed_scope = _scope(group="legacy-fixed-group", leg="legacy-fixed-leg")
    registry, _contexts = _capture_registry_contexts(
        tmp_path / "registry.sqlite3",
        scope=fixed_scope,
    )
    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3",
        monotonic_clock_ns=lambda: 1,
    )
    try:
        context = _register_and_dispatch(
            registry,
            session="legacy-fixed-group-registration",
            scope=fixed_scope,
        )
        with pytest.raises(
            GroupCoordinatorDenied, match="invalid coordinator registration context"
        ):
            authority.control.activate(
                context,
                execution_scope=_execution_scope(),
                risk_account_scope_key=_RISK_SCOPE_KEY,
            )
        assert authority.pending_holds() == ()
    finally:
        authority.close()
        registry.close()


def test_exception_keeps_claim_unknown_until_trusted_reconciliation(tmp_path: Path) -> None:
    registry, _contexts = _capture_registry_contexts(tmp_path / "registry.sqlite3")
    accepted: list[str] = []

    def verify(hold, proof: object) -> bool:
        is_exact = (
            proof == hold.hold_id
            and hold.execution_writer_lease_fingerprint
            and hold.intent_fingerprint
            and hold.binding_fingerprint
        )
        if is_exact:
            accepted.append(hold.hold_id)
        return bool(is_exact)

    authority = ExecutionGroupAuthority(
        tmp_path / "authority.sqlite3",
        reconciliation_verifier=verify,
        monotonic_clock_ns=lambda: 1,
    )
    try:
        context = _register_and_dispatch(registry, session="uncertain-session")
        authority.control.activate(
            context,
            execution_scope=_execution_scope(),
            risk_account_scope_key=_RISK_SCOPE_KEY,
        )
        intent, binding = _intent_and_binding()
        lease = _writer_lease()
        with (
            authority.guard.bind_service_call(context, execution_scope=_execution_scope()),
            pytest.raises(RuntimeError, match="provider outcome uncertain"),
            _authorization(
                authority,
                context,
                intent=intent,
                binding=binding,
                lease=lease,
            ),
        ):
            raise RuntimeError("provider outcome uncertain")
        holds = authority.pending_holds()
        assert len(holds) == 1
        assert authority.ready is False
        with (
            authority.guard.bind_service_call(context, execution_scope=_execution_scope()),
            pytest.raises(GroupAuthorityBusy),
            _authorization(
                authority,
                context,
                intent=intent,
                binding=binding,
                lease=lease,
            ),
        ):
            pytest.fail("an UNKNOWN attempt was redispatched")
        authority.control.reconcile_hold(holds[0].hold_id, holds[0].hold_id)
        assert accepted == [holds[0].hold_id]
        assert authority.pending_holds() == ()
        assert authority.ready is True
    finally:
        authority.close()
        registry.close()
