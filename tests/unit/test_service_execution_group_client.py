from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope, OrderIntent, Side
from bt_api_execution.group_contracts import group_leg_intent_id
from bt_api_execution.group_order_contracts import (
    GroupAccountReservationSnapshot,
    GroupLegPermitReference,
    GroupOrderIntent,
    GroupOrderLegPlan,
    GroupReservationPhase,
    GroupRiskLegSnapshot,
)

from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingError,
    AcceptedGroupBindingStore,
    AcceptedGroupConflictError,
    GroupOriginRunBindingStore,
)
from bt_api_py.service_registry import ActiveRegistrationContext, ServiceCallContext

_PRINCIPAL = "coordinator-service"
_RUN_ID = "strategy-run-7"
_GROUP_ID = "group-17"
_LEG_ID = "leg-a"
_DEADLINE_NS = 1_800_000_000_000_000_000
_WIRE_SCOPE = WireScope(
    execution_domain_ref="paper-domain",
    account_ref="account-a",
    strategy_ref="strategy-a",
    portfolio_ref="portfolio-a",
)
_IDENTITY = WireIdentity(WireRole.SERVICE, _PRINCIPAL, "coordinator")
_MAXIMUM = OrderIntent.limit(
    intent_id=group_leg_intent_id(_GROUP_ID, _LEG_ID),
    scope=ExecutionScope("PAPER", "simulation", "account-a", "strategy-a", "20261015"),
    signal_id="signal-a",
    instrument="BTCUSDT",
    side=Side.BUY,
    quantity=Decimal("3"),
    price=Decimal("100"),
    time_in_force="IOC",
    metadata_version="meta-2",
    deadline_ns=_DEADLINE_NS,
)

_GROUP_SERVICE = "group-coordinator-service"
_GROUP_IDENTITY = WireIdentity(WireRole.SERVICE, _GROUP_SERVICE, "order-groups")
_GROUP_REQUEST = GroupOrderIntent(
    group_id="coordinated-17",
    portfolio_ref="portfolio-a",
    strategy_id="strategy-a",
    coordinator_principal_ref=_GROUP_SERVICE,
    received_at_ns=1_800_000_000_000_000_001,
    deadline_ns=1_800_000_000_000_000_100,
    first_leg_id="leg-a",
    legs=(
        GroupOrderLegPlan(
            leg_id="leg-a",
            account_ref="account-a",
            maximum_intent=OrderIntent.limit(
                intent_id=group_leg_intent_id("coordinated-17", "leg-a"),
                scope=ExecutionScope("PAPER", "simulation", "account-a", "strategy-a", "20261015"),
                signal_id="signal-a",
                instrument="BTCUSDT",
                side=Side.BUY,
                quantity=Decimal("3"),
                price=Decimal("100"),
                time_in_force="IOC",
                metadata_version="meta-2",
                deadline_ns=1_800_000_000_000_000_100,
            ),
            base_units_per_order_unit=Decimal("1"),
            quantity_step=Decimal("1"),
            metadata_valid_until_ns=1_800_000_000_000_000_200,
        ),
        GroupOrderLegPlan(
            leg_id="leg-b",
            account_ref="account-b",
            maximum_intent=OrderIntent.limit(
                intent_id=group_leg_intent_id("coordinated-17", "leg-b"),
                scope=ExecutionScope("PAPER", "simulation", "account-b", "strategy-a", "20261015"),
                signal_id="signal-b",
                instrument="BTCUSDT",
                side=Side.SELL,
                quantity=Decimal("3"),
                price=Decimal("100"),
                time_in_force="IOC",
                metadata_version="meta-2",
                deadline_ns=1_800_000_000_000_000_100,
            ),
            base_units_per_order_unit=Decimal("1"),
            quantity_step=Decimal("1"),
            metadata_valid_until_ns=1_800_000_000_000_000_200,
        ),
    ),
)


def _registration(registration_id: str = "registration-a", *, run_id: str = _RUN_ID):
    context = ServiceCallContext(
        identity=_IDENTITY,
        scope=_WIRE_SCOPE,
        registration_id=registration_id,
        principal_ref=_PRINCIPAL,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id="account-owner-a",
        port="service.group_reservations",
        scope_ref="account-scope-a",
        operations=frozenset(
            {"reservations.reserve", "reservations.query", "reservations.release"}
        ),
        write_operations=frozenset({"reservations.reserve", "reservations.release"}),
        service_epoch="service-epoch-a",
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        expires_at=4_000_000_000.0,
        strategy_write_run_id=run_id,
    )


def _execution_registration(
    operation: str = "orders.submit_group_leg",
    *,
    registration_id: str = "execution-registration-a",
    identity: WireIdentity = _IDENTITY,
    scope: WireScope = _WIRE_SCOPE,
    run_id: str = _RUN_ID,
    expires_at: float = 4_000_000_000.0,
    instance_id: str = "execution-order-owner",
    scope_ref: str = "execution-order-scope",
    policy_version: str = "execution-policy-v2",
    requirements_version: str = "execution-requirements-v2",
    operations: frozenset[str] | None = None,
    write_operations: frozenset[str] | None = None,
    port: str | None = None,
) -> ActiveRegistrationContext:
    operation_ports = {
        "orders.submit_group_leg": "service.order_entry",
        "orders.query_group_leg": "service.order_query",
        "orders.resolve_group_leg": "service.order_entry",
    }
    registered_operations = frozenset({operation}) if operations is None else operations
    writes = (
        frozenset()
        if operation == "orders.query_group_leg"
        else frozenset({operation})
    )
    if write_operations is not None:
        writes = write_operations
    principal_ref = identity.principal_ref
    context = ServiceCallContext(
        identity=identity,
        scope=scope,
        registration_id=registration_id,
        principal_ref=principal_ref,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id=instance_id,
        port=operation_ports.get(operation, "service.order_entry") if port is None else port,
        scope_ref=scope_ref,
        operations=registered_operations,
        write_operations=writes,
        service_epoch="execution-service-epoch-v2",
        policy_version=policy_version,
        requirements_version=requirements_version,
        expires_at=expires_at,
        strategy_write_run_id=run_id,
    )


def _coordinator_registration(
    registration_id: str = "coordinator-registration-a",
    *,
    run_id: str = _RUN_ID,
    service_epoch: str = "coordinator-epoch-a",
    scope: WireScope | None = None,
    expires_at: float = 4_000_000_000.0,
):
    context = ServiceCallContext(
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-client", "strategy-a"),
        scope=scope
        or WireScope(
            execution_domain_ref="paper-domain",
            account_refs=("account-a", "account-b"),
            strategy_ref="strategy-a",
            run_id=run_id,
            portfolio_ref="portfolio-a",
        ),
        registration_id=registration_id,
        principal_ref="strategy-client",
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id="group-coordinator-a",
        port="service.order_group_entry",
        scope_ref="group-scope-a",
        operations=frozenset({"groups.submit", "groups.query"}),
        write_operations=frozenset({"groups.submit"}),
        service_epoch=service_epoch,
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        expires_at=expires_at,
        strategy_write_run_id=None,
    )


def _members() -> tuple[dict[str, object], ...]:
    return ({"leg_id": _LEG_ID, "intent": _MAXIMUM.to_payload()},)


def _risk_snapshot(
    *,
    phase: GroupReservationPhase = GroupReservationPhase.ACTIVE,
    state: str = "ACTIVE",
    claimed: bool = False,
    resolved: bool = False,
    actual_intent_hash: str | None = None,
) -> GroupAccountReservationSnapshot:
    leg = GroupRiskLegSnapshot(
        leg_id=_LEG_ID,
        permit_id="risk-permit-a",
        intent_id=_MAXIMUM.intent_id,
        reserved_intent_hash="d" * 64,
        exposure_profile_fingerprint="e" * 64,
        actual_intent_hash=actual_intent_hash,
        state=state,
        claimed=claimed,
        resolved=resolved,
        expires_at_ns=_DEADLINE_NS,
    )
    return GroupAccountReservationSnapshot(
        group_id=_GROUP_ID,
        account_ref="account-a",
        scope_fingerprint="a" * 64,
        phase=phase,
        group_fingerprint="b" * 64,
        policy_fingerprint="c" * 64,
        deadline_ns=_DEADLINE_NS,
        legs=(leg,),
    )


def _accepted_binding(store: AcceptedGroupBindingStore):
    pending = store.begin_reservation(
        _registration(),
        origin_run_id=_RUN_ID,
        group_id=_GROUP_ID,
        deadline_ns=_DEADLINE_NS,
        members=_members(),
    )
    return store.complete_reservation(pending, _risk_snapshot())


def test_exact_reservation_binding_survives_fresh_registration_and_release(tmp_path):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        pending = store.begin_reservation(
            _registration(),
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            deadline_ns=_DEADLINE_NS,
            members=_members(),
        )
        assert pending.phase == "PENDING"
        completed = store.complete_reservation(pending, _risk_snapshot())
        replay = store.begin_reservation(
            _registration("registration-b"),
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            deadline_ns=_DEADLINE_NS,
            members=_members(),
        )
        assert replay == completed
        assert replay.first_registration_id == "registration-a"
        assert replay.reservation == _risk_snapshot()
        assert (
            store.get_binding(
                _registration("registration-b"),
                origin_run_id=_RUN_ID,
                group_id=_GROUP_ID,
            )
            == completed
        )

        released = _risk_snapshot(
            phase=GroupReservationPhase.RELEASED,
            state="RELEASED",
        )
        updated = store.record_reservation_snapshot(
            _registration("registration-b"),
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            reservation=released,
            operation="reservations.release",
        )
        assert updated.reservation == released
        assert (
            store.get_binding(
                _registration("registration-b"),
                origin_run_id=_RUN_ID,
                group_id=_GROUP_ID,
                operation="reservations.release",
            )
            == updated
        )
    finally:
        store.close()


def test_order_roles_consume_same_stable_binding_without_rewriting_reservation_origin(
    tmp_path,
):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        original = _accepted_binding(store)
        for operation in (
            "orders.submit_group_leg",
            "orders.query_group_leg",
            "orders.resolve_group_leg",
        ):
            current = _execution_registration(operation)
            assert current.instance_id != original.instance_id
            assert current.scope_ref != original.scope_ref
            assert current.policy_version != original.policy_version
            assert current.requirements_version != original.requirements_version
            assert (
                store.get_binding(
                    current,
                    origin_run_id=_RUN_ID,
                    group_id=_GROUP_ID,
                    operation=operation,
                )
                == original
            )

        query_registration = _execution_registration("orders.query_group_leg")
        assert (
            store.find_binding_by_intent(
                query_registration,
                origin_run_id=_RUN_ID,
                intent_id=_MAXIMUM.intent_id,
            )
            == original
        )
        with pytest.raises(AcceptedGroupBindingError):
            store.find_binding_by_intent(
                _execution_registration("orders.submit_group_leg"),
                origin_run_id=_RUN_ID,
                intent_id=_MAXIMUM.intent_id,
                operation="orders.submit_group_leg",
            )
        # Consumer reads use the new exact Registry registration but preserve
        # the original reservation route and all Risk evidence byte-for-byte.
        assert store.get_binding(
            _registration(), origin_run_id=_RUN_ID, group_id=_GROUP_ID
        ) == original
        assert original.instance_id == "account-owner-a"
        assert original.scope_ref == "account-scope-a"
        assert original.policy_version == "policy-v1"
        assert original.requirements_version == "requirements-v1"
        assert original.first_registration_id == "registration-a"
        for operation in ("reservations.query", "orders.resolve_group_leg"):
            with pytest.raises(AcceptedGroupBindingError):
                store.record_reservation_snapshot(
                    _execution_registration(operation)
                    if operation.startswith("orders.")
                    else _registration(),
                    origin_run_id=_RUN_ID,
                    group_id=_GROUP_ID,
                    reservation=_risk_snapshot(),
                    operation=operation,
                )
        assert store.get_binding(
            _registration(), origin_run_id=_RUN_ID, group_id=_GROUP_ID
        ) == original
    finally:
        store.close()


@pytest.mark.parametrize(
    "mismatch",
    (
        "principal",
        "identity",
        "account",
        "portfolio",
        "strategy",
        "run",
        "operation_missing",
        "wrong_operation",
        "wrong_port",
        "expired",
        "not_write_authorized",
    ),
)
def test_order_role_cannot_consume_binding_outside_current_registered_owner(
    tmp_path,
    mismatch,
):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        store_binding = _accepted_binding(store)
        registration = _execution_registration()
        operation = "orders.submit_group_leg"
        if mismatch in {"principal", "identity"}:
            wrong_identity = WireIdentity(
                WireRole.SERVICE,
                "different-principal" if mismatch == "principal" else _PRINCIPAL,
                "different-actor" if mismatch == "identity" else "coordinator",
            )
            registration = _execution_registration(identity=wrong_identity)
        elif mismatch in {"account", "portfolio", "strategy"}:
            if mismatch == "account":
                wrong_scope = replace(_WIRE_SCOPE, account_ref="account-other")
            elif mismatch == "portfolio":
                wrong_scope = replace(_WIRE_SCOPE, portfolio_ref="portfolio-other")
            else:
                wrong_scope = replace(_WIRE_SCOPE, strategy_ref="strategy-other")
            registration = _execution_registration(scope=wrong_scope)
        elif mismatch == "run":
            registration = _execution_registration(run_id="other-strategy-run")
        elif mismatch == "operation_missing":
            registration = _execution_registration(
                operations=frozenset({"orders.query_group_leg"}),
                write_operations=frozenset(),
            )
        elif mismatch == "wrong_operation":
            operation = "orders.cancel_group_leg"
        elif mismatch == "wrong_port":
            registration = _execution_registration(port="service.group_reservations")
        elif mismatch == "expired":
            registration = _execution_registration(expires_at=0.0)
        elif mismatch == "not_write_authorized":
            registration = _execution_registration(write_operations=frozenset())
        else:
            raise AssertionError(f"unhandled registration mismatch: {mismatch}")

        if mismatch in {"account", "portfolio", "strategy"}:
            # The store partitions by trusted stable business scope and does
            # not reveal a row from another account/portfolio/strategy.
            assert (
                store.get_binding(
                    registration,
                    origin_run_id=_RUN_ID,
                    group_id=_GROUP_ID,
                    operation=operation,
                )
                is None
            )
        else:
            with pytest.raises(AcceptedGroupBindingError):
                store.get_binding(
                    registration,
                    origin_run_id=_RUN_ID,
                    group_id=_GROUP_ID,
                    operation=operation,
                )
        # No failed role/scope probe mutates the original accepted binding.
        assert (
            store.get_binding(
                _registration(), origin_run_id=_RUN_ID, group_id=_GROUP_ID
            )
            == store_binding
        )
    finally:
        store.close()


def test_reservation_reads_keep_origin_route_and_policy_version_strict(tmp_path):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        original = _accepted_binding(store)
        wrong_route = replace(
            _registration(),
            instance_id="different-reservation-instance",
        )
        with pytest.raises(AcceptedGroupBindingError):
            store.get_binding(
                wrong_route,
                origin_run_id=_RUN_ID,
                group_id=_GROUP_ID,
                operation="reservations.query",
            )
        wrong_policy = replace(_registration(), policy_version="new-reservation-policy")
        with pytest.raises(AcceptedGroupBindingError):
            store.get_binding(
                wrong_policy,
                origin_run_id=_RUN_ID,
                group_id=_GROUP_ID,
                operation="reservations.query",
            )
        assert store.get_binding(
            _registration(), origin_run_id=_RUN_ID, group_id=_GROUP_ID
        ) == original
    finally:
        store.close()


def test_absent_lookup_is_read_only_and_wrong_run_is_rejected(tmp_path):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        registration = _registration()
        assert (
            store.get_binding(
                registration,
                origin_run_id=_RUN_ID,
                group_id="not-accepted",
            )
            is None
        )
        with pytest.raises(AcceptedGroupBindingError):
            store.get_binding(
                registration,
                origin_run_id="client-selected-other-run",
                group_id=_GROUP_ID,
            )
        with pytest.raises(AcceptedGroupBindingError):
            store.begin_reservation(
                registration,
                origin_run_id="client-selected-other-run",
                group_id=_GROUP_ID,
                deadline_ns=_DEADLINE_NS,
                members=_members(),
            )
        assert (
            store.get_binding(
                registration,
                origin_run_id=_RUN_ID,
                group_id="not-accepted",
            )
            is None
        )
    finally:
        store.close()


def test_pending_binding_reconciles_only_to_exact_risk_snapshot(tmp_path):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        pending = store.begin_reservation(
            _registration(),
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            deadline_ns=_DEADLINE_NS,
            members=_members(),
        )
        completed = store.complete_reservation(pending, _risk_snapshot())
        assert completed.reservation == _risk_snapshot()
        with pytest.raises(AcceptedGroupBindingError):
            store.complete_reservation(
                pending, replace(_risk_snapshot(), group_fingerprint="f" * 64)
            )
        assert (
            store.get_binding(_registration(), origin_run_id=_RUN_ID, group_id=_GROUP_ID)
            == completed
        )
    finally:
        store.close()


def test_permit_binds_exact_child_terms_and_allows_only_quantity_reduction(tmp_path):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        binding = _accepted_binding(store)
        child = replace(_MAXIMUM, quantity=Decimal("2"))
        permit = GroupLegPermitReference(
            group_id=_GROUP_ID,
            leg_id=_LEG_ID,
            portfolio_ref="portfolio-a",
            strategy_id="strategy-a",
            account_ref="account-a",
            coordinator_principal_ref=_PRINCIPAL,
            risk_scope_fingerprint="a" * 64,
            permit_id="risk-permit-a",
            group_fingerprint="b" * 64,
            policy_fingerprint="c" * 64,
            reserved_intent_hash="d" * 64,
            exposure_profile_fingerprint="e" * 64,
            order_intent_hash=child.fingerprint,
            deadline_ns=_DEADLINE_NS,
        )
        assert binding.permit_for(permit, child) == child
        changed = replace(child, side=Side.SELL)
        with pytest.raises(AcceptedGroupBindingError):
            binding.permit_for(replace(permit, order_intent_hash=changed.fingerprint), changed)
        oversized = replace(_MAXIMUM, quantity=Decimal("4"))
        with pytest.raises(AcceptedGroupBindingError):
            binding.permit_for(replace(permit, order_intent_hash=oversized.fingerprint), oversized)
    finally:
        store.close()


def test_reservation_snapshots_cannot_regress_a_consumed_risk_claim(tmp_path):
    store = AcceptedGroupBindingStore(tmp_path / "accepted-groups.sqlite")
    try:
        pending = store.begin_reservation(
            _registration(),
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            deadline_ns=_DEADLINE_NS,
            members=_members(),
        )
        claimed = _risk_snapshot(
            state="SETTLED",
            claimed=True,
            actual_intent_hash="f" * 64,
        )
        store.complete_reservation(pending, claimed)
        with pytest.raises(AcceptedGroupBindingError):
            store.record_reservation_snapshot(
                _registration(),
                origin_run_id=_RUN_ID,
                group_id=_GROUP_ID,
                reservation=_risk_snapshot(),
            )
    finally:
        store.close()


def test_coordinator_run_binding_is_restart_stable_and_uses_registered_scope(tmp_path):
    path = tmp_path / "coordinator-run-bindings.sqlite"
    store = GroupOriginRunBindingStore(path)
    try:
        first = store.bind(
            _coordinator_registration(),
            _GROUP_REQUEST,
            coordinator_principal_ref=_GROUP_SERVICE,
        )
        assert first.origin_run_id == _RUN_ID
        assert first.strategy_scope.account_refs == ("account-a", "account-b")
        assert first.request == _GROUP_REQUEST

        # The service normalizes receive time per call. An exact retry under a
        # renewed registration reuses the first durable request and run.
        replay_request = replace(
            _GROUP_REQUEST,
            received_at_ns=_GROUP_REQUEST.received_at_ns + 1,
        )
        renewed = _coordinator_registration(
            "coordinator-registration-b", service_epoch="coordinator-epoch-b"
        )
        replay = store.bind(
            renewed,
            replay_request,
            coordinator_principal_ref=_GROUP_SERVICE,
        )
        assert replay == first
        assert (
            store.get_for_registration(
                renewed,
                _GROUP_REQUEST.group_id,
                coordinator_principal_ref=_GROUP_SERVICE,
            )
            == first
        )
        assert store.find_by_intent(_GROUP_REQUEST.legs[1].maximum_intent.intent_id) == first
    finally:
        store.close()

    # Process restart changes the Registry service epoch and registration ID,
    # but does not mint a new strategy run or coordinator authority.
    restarted = GroupOriginRunBindingStore(path)
    try:
        current = _coordinator_registration(
            "coordinator-registration-c", service_epoch="coordinator-epoch-c"
        )
        assert (
            restarted.get_for_registration(
                current,
                _GROUP_REQUEST.group_id,
                coordinator_principal_ref=_GROUP_SERVICE,
            )
            == first
        )
        assert restarted.list_all() == (first,)
    finally:
        restarted.close()


def test_coordinator_run_binding_rejects_new_run_scope_and_changed_business_facts(tmp_path):
    store = GroupOriginRunBindingStore(tmp_path / "coordinator-run-bindings.sqlite")
    try:
        store.bind(
            _coordinator_registration(),
            _GROUP_REQUEST,
            coordinator_principal_ref=_GROUP_SERVICE,
        )
        wrong_run = _coordinator_registration("registration-wrong-run", run_id="strategy-run-8")
        with pytest.raises(AcceptedGroupConflictError):
            store.bind(
                wrong_run,
                _GROUP_REQUEST,
                coordinator_principal_ref=_GROUP_SERVICE,
            )
        with pytest.raises(AcceptedGroupBindingError):
            store.get_for_registration(
                wrong_run,
                _GROUP_REQUEST.group_id,
                coordinator_principal_ref=_GROUP_SERVICE,
            )

        altered = replace(_GROUP_REQUEST, first_leg_id="leg-b")
        with pytest.raises(AcceptedGroupConflictError):
            store.bind(
                _coordinator_registration("registration-altered"),
                altered,
                coordinator_principal_ref=_GROUP_SERVICE,
            )

        wrong_accounts = _coordinator_registration(
            "registration-wrong-accounts",
            scope=WireScope(
                execution_domain_ref="paper-domain",
                account_refs=("account-a", "account-c"),
                strategy_ref="strategy-a",
                run_id=_RUN_ID,
                portfolio_ref="portfolio-a",
            ),
        )
        with pytest.raises(AcceptedGroupBindingError):
            store.bind(
                wrong_accounts,
                _GROUP_REQUEST,
                coordinator_principal_ref=_GROUP_SERVICE,
            )
    finally:
        store.close()
