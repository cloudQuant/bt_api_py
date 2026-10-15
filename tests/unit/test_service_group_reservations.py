from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope, OrderIntent, Side
from bt_api_execution.group_contracts import group_leg_intent_id
from bt_api_execution.group_order_contracts import (
    GroupAccountReservationSnapshot,
    GroupReservationPhase,
)
from bt_api_py.service_execution_group_client import AcceptedGroupBindingStore
from bt_api_py.service_group_reservations import (
    GroupReservationTarget,
    GroupReservationsService,
)
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    TrustedGroupReservationPolicy,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    INSTRUMENT_METADATA_DIGEST_TAG,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)


_PRINCIPAL = "coordinator-service"
_INSTANCE = "risk-owner-a"
_SCOPE_REF = "risk-group-reservations-v1"
_RUN_ID = "strategy-run-a"
_GROUP_ID = "group-a"
_WIRE_ACCOUNT = "wire-account-a"
_STRATEGY = "strategy-a"
_PORTFOLIO = "portfolio-a"
_WIRE_SCOPE = WireScope(
    execution_domain_ref="paper-domain",
    account_ref=_WIRE_ACCOUNT,
    strategy_ref=_STRATEGY,
    portfolio_ref=_PORTFOLIO,
)
_IDENTITY = WireIdentity(WireRole.SERVICE, _PRINCIPAL, "coordinator-instance")
_RISK_SCOPE = AccountScope("PAPER", "risk-internal-account-a", "simulation")


def _registration(registration_id: str = "registration-a") -> ActiveRegistrationContext:
    context = ServiceCallContext(
        identity=_IDENTITY,
        scope=_WIRE_SCOPE,
        registration_id=registration_id,
        principal_ref=_PRINCIPAL,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id=_INSTANCE,
        port="service.group_reservations",
        scope_ref=_SCOPE_REF,
        operations=frozenset(
            {"reservations.reserve", "reservations.query", "reservations.release"}
        ),
        write_operations=frozenset({"reservations.reserve", "reservations.release"}),
        service_epoch="service-epoch-a",
        policy_version="risk-policy-v1",
        requirements_version="risk-requirements-v1",
        expires_at=time.time() + 3_600,
        strategy_write_run_id=_RUN_ID,
    )


def _metadata() -> InstrumentRiskMetadata:
    now_ns = time.time_ns()
    return InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="risk-instrument-v1",
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("50000"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )


def _intent(
    metadata: InstrumentRiskMetadata,
    *,
    group_id: str,
    leg_id: str,
    deadline_ns: int,
    side: Side,
    quantity: str = "1",
) -> OrderIntent:
    return OrderIntent.limit(
        intent_id=group_leg_intent_id(group_id, leg_id),
        scope=ExecutionScope(
            "PAPER", "simulation", _WIRE_ACCOUNT, _STRATEGY, "20261015"
        ),
        signal_id=f"signal-{leg_id}",
        instrument=metadata.instrument,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal("100"),
        time_in_force="IOC",
        metadata_version=metadata.metadata_version,
        deadline_ns=deadline_ns,
        tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
    )


def _arguments(
    group_id: str,
    deadline_ns: int,
    members: tuple[tuple[str, OrderIntent], ...],
) -> dict[str, object]:
    return {
        "origin_run_id": _RUN_ID,
        "group_id": group_id,
        "deadline_ns": deadline_ns,
        "members": [
            {"leg_id": leg_id, "intent": intent.to_payload()}
            for leg_id, intent in members
        ],
    }


def _runtime(
    tmp_path: Path,
    *,
    gate_path: str = "risk.sqlite3",
    binding_path: str = "accepted-groups.sqlite3",
    metadata: InstrumentRiskMetadata | None = None,
    seed_allocation: bool = True,
    account_limit: Decimal = Decimal("10000"),
):
    risk_scope = _RISK_SCOPE
    gate = DurableRiskGate(
        tmp_path / gate_path,
        RiskPolicy(
            "group-reservation-policy-v1",
            account_limit,
            max_increase_count=20,
            permit_ttl_seconds=300,
            require_strategy_allocation=True,
            notional_unit="USD",
        ),
    )
    if seed_allocation:
        gate.set_strategy_allocation(
            risk_scope,
            _STRATEGY,
            "allocation-v1",
            max_notional=Decimal("10000"),
            notional_unit="USD",
        )
    instrument_metadata = metadata or _metadata()
    mapper = InstrumentRiskAdmissionMapper(
        risk_scope,
        InstrumentRiskRegistry((instrument_metadata,)),
        allocation_reader=lambda execution_scope: gate.get_strategy_allocation(
            risk_scope, execution_scope.strategy_id
        ),
        wire_account_ref=_WIRE_ACCOUNT,
    )
    admission = DurableRiskAdmissionAdapter(gate, mapper)
    policy = TrustedGroupReservationPolicy(
        instance_id=_INSTANCE,
        principal_ref=_PRINCIPAL,
        identity=_IDENTITY,
        scope_ref=_SCOPE_REF,
        scope=_WIRE_SCOPE,
        run_id=_RUN_ID,
        account_ref=_WIRE_ACCOUNT,
    )
    binding_store = AcceptedGroupBindingStore(tmp_path / binding_path)
    service = GroupReservationsService(
        {_WIRE_SCOPE: GroupReservationTarget(risk_scope, admission)},
        binding_store,
        (policy,),
    )
    registration = _registration()
    service.maintain_registrations((registration,))
    return service, binding_store, admission, gate, registration, instrument_metadata


def _close_runtime(service, store, gate) -> None:
    service.close()
    store.close()
    gate.close()


def _sqlite_rows(path: Path, sql: str, parameters: tuple[object, ...] = ()):
    with sqlite3.connect(path) as connection:
        return connection.execute(sql, parameters).fetchall()


def test_real_risk_vector_reserve_query_is_read_only_then_release_unclaimed(tmp_path):
    service, store, admission, gate, registration, metadata = _runtime(tmp_path)
    deadline_ns = time.time_ns() + 120_000_000_000
    members = (
        (
            "leg-a",
            _intent(
                metadata,
                group_id=_GROUP_ID,
                leg_id="leg-a",
                deadline_ns=deadline_ns,
                side=Side.BUY,
            ),
        ),
        (
            "leg-b",
            _intent(
                metadata,
                group_id=_GROUP_ID,
                leg_id="leg-b",
                deadline_ns=deadline_ns,
                side=Side.SELL,
            ),
        ),
    )
    try:
        reserved = service.handle(
            "reservations.reserve",
            context=registration.context,
            arguments=_arguments(_GROUP_ID, deadline_ns, members),
        )
        assert reserved["ok"] is True
        snapshot = GroupAccountReservationSnapshot.from_payload(reserved["result"])
        assert snapshot.phase is GroupReservationPhase.ACTIVE
        assert snapshot.account_ref == _WIRE_ACCOUNT
        assert snapshot.group_id == _GROUP_ID
        assert [leg.leg_id for leg in snapshot.legs] == ["leg-a", "leg-b"]
        assert all(leg.state == "ACTIVE" and not leg.claimed for leg in snapshot.legs)
        assert all(leg.expires_at_ns <= deadline_ns for leg in snapshot.legs)
        assert len({leg.permit_id for leg in snapshot.legs}) == 2

        accepted_before = _sqlite_rows(
            tmp_path / "accepted-groups.sqlite3",
            "SELECT binding_json, updated_at_ns FROM accepted_group_bindings",
        )
        reservations_before = _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id, intent_id, status, group_id FROM risk_reservations "
            "ORDER BY group_leg_id",
        )
        pure_risk_snapshot = admission.read_group_vector_snapshot(_RISK_SCOPE, _GROUP_ID)
        query = service.handle(
            "reservations.query",
            context=registration.context,
            arguments={"origin_run_id": _RUN_ID, "group_id": _GROUP_ID},
        )
        assert query["ok"] is True
        queried = GroupAccountReservationSnapshot.from_payload(query["result"])
        assert queried == snapshot
        assert admission.read_group_vector_snapshot(_RISK_SCOPE, _GROUP_ID) == pure_risk_snapshot
        assert _sqlite_rows(
            tmp_path / "accepted-groups.sqlite3",
            "SELECT binding_json, updated_at_ns FROM accepted_group_bindings",
        ) == accepted_before
        assert _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id, intent_id, status, group_id FROM risk_reservations "
            "ORDER BY group_leg_id",
        ) == reservations_before

        released = service.handle(
            "reservations.release",
            context=registration.context,
            arguments={
                "origin_run_id": _RUN_ID,
                "group_id": _GROUP_ID,
                "reason": "coordinator-aborted-before-claim",
            },
        )
        assert released["ok"] is True
        release_snapshot = GroupAccountReservationSnapshot.from_payload(released["result"])
        assert release_snapshot.phase is GroupReservationPhase.RELEASED
        assert {leg.state for leg in release_snapshot.legs} == {"RELEASED"}
        assert all(not leg.claimed and not leg.resolved for leg in release_snapshot.legs)
        assert {
            leg.state.value
            for leg in admission.read_group_vector_snapshot(_RISK_SCOPE, _GROUP_ID).legs
        } == {"released"}
    finally:
        _close_runtime(service, store, gate)


def test_claimed_unknown_group_cannot_release_sibling_occupancy(tmp_path):
    service, store, admission, gate, registration, metadata = _runtime(tmp_path)
    deadline_ns = time.time_ns() + 120_000_000_000
    maximum = _intent(
        metadata,
        group_id=_GROUP_ID,
        leg_id="leg-a",
        deadline_ns=deadline_ns,
        side=Side.BUY,
        quantity="2",
    )
    sibling = _intent(
        metadata,
        group_id=_GROUP_ID,
        leg_id="leg-b",
        deadline_ns=deadline_ns,
        side=Side.SELL,
        quantity="2",
    )
    try:
        reserved = service.handle(
            "reservations.reserve",
            context=registration.context,
            arguments=_arguments(_GROUP_ID, deadline_ns, (("leg-a", maximum), ("leg-b", sibling))),
        )
        assert reserved["ok"] is True
        snapshot = GroupAccountReservationSnapshot.from_payload(reserved["result"])
        child = _intent(
            metadata,
            group_id=_GROUP_ID,
            leg_id="leg-a",
            deadline_ns=deadline_ns,
            side=Side.BUY,
            quantity="1",
        )
        claim = admission.claim_group_leg(
            child,
            scope=_RISK_SCOPE,
            group_id=_GROUP_ID,
            leg_id="leg-a",
            permit_reference=snapshot.legs[0].permit_id,
            group_fingerprint=snapshot.group_fingerprint,
            deadline_ns=deadline_ns,
        )
        assert claim.claimed is True
        assert claim.reserved_intent_hash == snapshot.legs[0].reserved_intent_hash
        assert claim.actual_intent_hash != claim.reserved_intent_hash

        denied = service.handle(
            "reservations.release",
            context=registration.context,
            arguments={
                "origin_run_id": _RUN_ID,
                "group_id": _GROUP_ID,
                "reason": "do-not-drop-claimed-risk",
            },
        )
        assert denied["ok"] is False
        assert denied["code"] == "GROUP_CLAIM_UNRESOLVED"
        after = admission.read_group_vector_snapshot(_RISK_SCOPE, _GROUP_ID)
        assert [(leg.leg_id, leg.state.value, leg.claimed) for leg in after.legs] == [
            ("leg-a", "active", True),
            ("leg-b", "active", False),
        ]
    finally:
        _close_runtime(service, store, gate)


def test_pending_binding_recovers_risk_commit_after_reply_loss_and_restart(tmp_path):
    metadata = _metadata()
    service, store, admission, gate, registration, metadata = _runtime(
        tmp_path, metadata=metadata
    )
    deadline_ns = time.time_ns() + 120_000_000_000
    members = (
        (
            "leg-a",
            _intent(
                metadata,
                group_id=_GROUP_ID,
                leg_id="leg-a",
                deadline_ns=deadline_ns,
                side=Side.BUY,
            ),
        ),
    )
    arguments = _arguments(_GROUP_ID, deadline_ns, members)
    try:
        pending = store.begin_reservation(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            deadline_ns=deadline_ns,
            members=tuple(
                {"leg_id": leg_id, "intent": intent.to_payload()}
                for leg_id, intent in members
            ),
        )
        assert pending.reservation is None
        committed = admission.reserve_group_vector(
            _RISK_SCOPE,
            _GROUP_ID,
            members,
            deadline_ns=deadline_ns,
        )
        assert len(committed.legs) == 1
        assert store.get_binding(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            operation="reservations.reserve",
        ).reservation is None
    finally:
        _close_runtime(service, store, gate)

    service, store, admission, gate, registration, _ = _runtime(
        tmp_path,
        metadata=metadata,
        seed_allocation=False,
    )
    try:
        read_only_query = service.handle(
            "reservations.query",
            context=registration.context,
            arguments={"origin_run_id": _RUN_ID, "group_id": _GROUP_ID},
        )
        assert read_only_query["ok"] is True
        assert store.get_binding(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            operation="reservations.query",
        ).reservation is None

        recovered = service.handle(
            "reservations.reserve",
            context=registration.context,
            arguments=arguments,
        )
        assert recovered["ok"] is True
        receipt = GroupAccountReservationSnapshot.from_payload(recovered["result"])
        assert receipt.legs[0].permit_id == committed.legs[0].permit_id
        assert receipt.phase is GroupReservationPhase.ACTIVE
        binding = store.get_binding(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            operation="reservations.reserve",
        )
        assert binding is not None and binding.reservation == receipt
        assert _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id FROM risk_reservations WHERE group_id=?",
            (_GROUP_ID,),
        ) == [(committed.legs[0].permit_id,)]
    finally:
        _close_runtime(service, store, gate)


def test_release_does_not_reconcile_pending_binding_after_risk_reply_loss(tmp_path):
    service, store, admission, gate, registration, metadata = _runtime(tmp_path)
    deadline_ns = time.time_ns() + 120_000_000_000
    members = (
        (
            "leg-a",
            _intent(
                metadata,
                group_id=_GROUP_ID,
                leg_id="leg-a",
                deadline_ns=deadline_ns,
                side=Side.BUY,
            ),
        ),
    )
    try:
        pending = store.begin_reservation(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            deadline_ns=deadline_ns,
            members=tuple(
                {"leg_id": leg_id, "intent": intent.to_payload()}
                for leg_id, intent in members
            ),
        )
        assert pending.reservation is None
        risk_commit = admission.reserve_group_vector(
            _RISK_SCOPE,
            _GROUP_ID,
            members,
            deadline_ns=deadline_ns,
        )
        before_risk = _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id, intent_id, status, group_id FROM risk_reservations",
        )
        before_binding = _sqlite_rows(
            tmp_path / "accepted-groups.sqlite3",
            "SELECT binding_json, updated_at_ns FROM accepted_group_bindings",
        )

        denied = service.handle(
            "reservations.release",
            context=registration.context,
            arguments={
                "origin_run_id": _RUN_ID,
                "group_id": _GROUP_ID,
                "reason": "reserve-replay-required",
            },
        )
        assert denied == {
            "ok": False,
            "code": "GROUP_BINDING_INCOMPLETE",
            "result": {},
        }
        assert _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id, intent_id, status, group_id FROM risk_reservations",
        ) == before_risk
        assert _sqlite_rows(
            tmp_path / "accepted-groups.sqlite3",
            "SELECT binding_json, updated_at_ns FROM accepted_group_bindings",
        ) == before_binding
        current = admission.read_group_vector_snapshot(_RISK_SCOPE, _GROUP_ID)
        assert current == risk_commit
        still_pending = store.get_binding(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            operation="reservations.release",
        )
        assert still_pending is not None and still_pending.reservation is None
    finally:
        _close_runtime(service, store, gate)


def test_untrusted_run_or_wrong_wire_account_cannot_create_binding(tmp_path):
    service, store, admission, gate, registration, metadata = _runtime(tmp_path)
    deadline_ns = time.time_ns() + 120_000_000_000
    intent = _intent(
        metadata,
        group_id=_GROUP_ID,
        leg_id="leg-a",
        deadline_ns=deadline_ns,
        side=Side.BUY,
    )
    try:
        arguments = _arguments(_GROUP_ID, deadline_ns, (("leg-a", intent),))
        arguments["origin_run_id"] = "caller-selected-run"
        denied = service.handle(
            "reservations.reserve", context=registration.context, arguments=arguments
        )
        assert denied["ok"] is False
        assert denied["code"] == "GROUP_BINDING_UNAVAILABLE"
        assert store.get_binding(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            operation="reservations.query",
        ) is None

        foreign_intent = OrderIntent.limit(
            intent_id=group_leg_intent_id(_GROUP_ID, "leg-a"),
            scope=ExecutionScope(
                "PAPER", "simulation", "other-wire-account", _STRATEGY, "20261015"
            ),
            signal_id="signal-foreign",
            instrument=metadata.instrument,
            side=Side.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            time_in_force="IOC",
            metadata_version=metadata.metadata_version,
            deadline_ns=deadline_ns,
            tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
        )
        denied = service.handle(
            "reservations.reserve",
            context=registration.context,
            arguments=_arguments(_GROUP_ID, deadline_ns, (("leg-a", foreign_intent),)),
        )
        assert denied["ok"] is False
        assert denied["code"] == "GROUP_MEMBER_SCOPE_MISMATCH"
        assert _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id FROM risk_reservations",
        ) == []
    finally:
        _close_runtime(service, store, gate)


def test_overbudget_vector_rolls_back_all_risk_legs(tmp_path):
    service, store, admission, gate, registration, metadata = _runtime(
        tmp_path, account_limit=Decimal("250")
    )
    deadline_ns = time.time_ns() + 120_000_000_000
    members = (
        (
            "leg-a",
            _intent(
                metadata,
                group_id=_GROUP_ID,
                leg_id="leg-a",
                deadline_ns=deadline_ns,
                side=Side.BUY,
                quantity="2",
            ),
        ),
        (
            "leg-b",
            _intent(
                metadata,
                group_id=_GROUP_ID,
                leg_id="leg-b",
                deadline_ns=deadline_ns,
                side=Side.SELL,
                quantity="2",
            ),
        ),
    )
    try:
        denied = service.handle(
            "reservations.reserve",
            context=registration.context,
            arguments=_arguments(_GROUP_ID, deadline_ns, members),
        )
        assert denied["ok"] is False
        assert denied["code"] == "ACCOUNT_LIMIT_EXHAUSTED"
        assert _sqlite_rows(
            tmp_path / "risk.sqlite3",
            "SELECT permit_id FROM risk_reservations WHERE group_id=?",
            (_GROUP_ID,),
        ) == []
        pending = store.get_binding(
            registration,
            origin_run_id=_RUN_ID,
            group_id=_GROUP_ID,
            operation="reservations.reserve",
        )
        assert pending is not None and pending.reservation is None
    finally:
        _close_runtime(service, store, gate)
