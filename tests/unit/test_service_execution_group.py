"""Focused SDK adapter coverage for authenticated two-leg coordination.

The account/provider boundary is deliberately synthetic here. The test uses
the real Execution group store/coordinator and real SDK adapter, then verifies
that an ambiguous child is queried by its original ID after a registration
restart instead of submitted a second time.
"""

from __future__ import annotations
from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION as _WIRE_PROTOCOL_VERSION

import hashlib
import time
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import MappingProxyType
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
    ExecutionScope,
    ExecutionState,
    GroupAccountReservationSnapshot,
    GroupBudgetUnsupportedOrderError,
    GroupChildOrderStatus,
    GroupLegOrderReceipt,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupRiskLegSnapshot,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    TimeInForce,
    build_instrument_risk_group_budget_quote,
    group_leg_intent_id,
    order_intent_from_payload,
    risk_account_scope_fingerprint,
)
from bt_api_execution.group_order_contracts import GroupReservationPhase
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DispatchEvidenceClass,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskDeniedError,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import BorrowedServiceClient
from bt_api_py.service_execution_group import (
    GROUP_ACCOUNT_OWNER_OPERATIONS,
    GROUP_COORDINATOR_OPERATIONS,
    ExecutionGroupOrderEntryService,
    GroupAccountRoute,
    GroupCoordinatorService,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
    ServiceExecutionGroupError,
)
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingStore,
    GroupOriginRunBindingStore,
)
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofTarget,
)
from bt_api_py.service_execution_group_proofs import (
    GroupReservationTarget as ProofReservationTarget,
)
from bt_api_py.service_group_reservations import (
    GroupReservationsService,
    GroupReservationTarget,
)
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.unit._service_group_provider_fixtures import (
    _V2_FILL_SCHEMA,
    _V2_INSTRUMENT,
    _V2_METADATA_VERSION,
    _V2_ORDER_SCHEMA,
    _V2_PROVIDER_ACCOUNT,
    _V2SyntheticBtApiFeed,
)

_DOMAIN = "paper:group-adapter"
_STRATEGY = "strategy-a"
_RUN = "run-a"
_PORTFOLIO = "portfolio-a"
_COORDINATOR_INSTANCE = "group-coordinator-a"
_COORDINATOR_SCOPE_REF = "group-scope-v1"
_COORDINATOR_PRINCIPAL = "group-coordinator-principal"
_STRATEGY_PRINCIPAL = "strategy-principal"
_GROUP_ID = "group-a"
_ACCOUNTS = ("account-a", "account-b")
_REGISTRATION_EXPIRY_NS = 3_600_000_000_000


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metadata(now_ns: int) -> InstrumentRiskMetadata:
    return InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="metadata-v1",
        as_of_ns=now_ns - 1_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000000"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )


def _strategy_scope(*, run_id: str = _RUN) -> WireScope:
    return WireScope(
        execution_domain_ref=_DOMAIN,
        account_refs=_ACCOUNTS,
        strategy_ref=_STRATEGY,
        run_id=run_id,
        portfolio_ref=_PORTFOLIO,
    )


def _registration(
    now_ns: int,
    *,
    registration_id: str = "registration-a",
    service_epoch: str = "epoch-a",
    scope: WireScope | None = None,
) -> ActiveRegistrationContext:
    actual_scope = _strategy_scope() if scope is None else scope
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _STRATEGY_PRINCIPAL,
        actor_ref=_STRATEGY,
    )
    context = ServiceCallContext(
        identity=identity,
        scope=actual_scope,
        registration_id=registration_id,
        principal_ref=_STRATEGY_PRINCIPAL,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id=_COORDINATOR_INSTANCE,
        port="service.order_group_entry",
        scope_ref=_COORDINATOR_SCOPE_REF,
        operations=GROUP_COORDINATOR_OPERATIONS,
        write_operations=frozenset({"groups.submit"}),
        service_epoch=service_epoch,
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        expires_at=now_ns / 1_000_000_000 + 3_600,
        strategy_write_run_id=_RUN,
    )


def _intent(
    *,
    account_ref: str,
    leg_id: str,
    deadline_ns: int,
    metadata: InstrumentRiskMetadata,
    side: Side,
) -> OrderIntent:
    execution_scope = ExecutionScope("PAPER", "simulation", account_ref, _STRATEGY, "20261015")
    return OrderIntent.limit(
        intent_id=group_leg_intent_id(_GROUP_ID, leg_id),
        scope=execution_scope,
        signal_id=f"signal-{leg_id}",
        instrument=metadata.instrument,
        side=side,
        quantity=Decimal("1"),
        price=Decimal("100"),
        time_in_force=TimeInForce.IOC,
        metadata_version=metadata.metadata_version,
        deadline_ns=deadline_ns,
        tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
    )


class _LoopbackRuntime:
    """Call-only synthetic transport used behind BorrowedServiceClient."""

    state = "registered"
    healthy = True

    def __init__(self, scope_fingerprints: dict[str, str]) -> None:
        self._scope_fingerprints = scope_fingerprints
        self.calls: list[tuple[str, str, str, str, dict[str, Any]]] = []
        self.reservations: dict[tuple[str, str], GroupAccountReservationSnapshot] = {}
        self.orders: dict[str, GroupLegOrderReceipt] = {}
        self.submitted: list[str] = []

    def call(
        self,
        mount: str,
        operation: str,
        arguments: dict[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
    ) -> dict[str, Any]:
        del max_reply_message_bytes
        if account_ref is None or portfolio_ref != _PORTFOLIO or scope_ref is None:
            raise AssertionError("group adapter omitted its fixed downstream scope")
        self.calls.append((mount, operation, account_ref, scope_ref, dict(arguments)))
        if mount == "upstream.account_reservations":
            group_id = arguments["group_id"]
            key = (account_ref, group_id)
            if operation == "reservations.query":
                reservation = self.reservations.get(key)
                if reservation is None:
                    return {"ok": False, "code": "GROUP_NOT_FOUND", "result": {}}
                return _success(reservation.to_payload())
            if operation == "reservations.reserve":
                members = [
                    (item["leg_id"], order_intent_from_payload(item["intent"]))
                    for item in arguments["members"]
                ]
                legs = tuple(
                    GroupRiskLegSnapshot(
                        leg_id=leg_id,
                        permit_id=f"permit-{account_ref}-{leg_id}",
                        intent_id=intent.intent_id,
                        reserved_intent_hash=intent.fingerprint,
                        exposure_profile_fingerprint=_digest(f"profile:{account_ref}:{leg_id}"),
                        actual_intent_hash=None,
                        state="ACTIVE",
                        claimed=False,
                        resolved=False,
                        expires_at_ns=arguments["deadline_ns"],
                    )
                    for leg_id, intent in members
                )
                group_fingerprint = _digest(f"group:{group_id}")
                reservation = GroupAccountReservationSnapshot(
                    group_id=group_id,
                    account_ref=account_ref,
                    scope_fingerprint=self._scope_fingerprints[account_ref],
                    phase=GroupReservationPhase.ACTIVE,
                    group_fingerprint=group_fingerprint,
                    policy_fingerprint=_digest("policy-v1"),
                    deadline_ns=arguments["deadline_ns"],
                    legs=legs,
                )
                self.reservations[key] = reservation
                return _success(reservation.to_payload())
            if operation == "reservations.release":
                reservation = self.reservations[key]
                return _success(reservation.to_payload())
            raise AssertionError(f"unexpected reservation operation: {operation}")

        if mount == "upstream.order_query" and operation == "orders.query_group_leg":
            return _success_value(
                None
                if arguments["intent_id"] not in self.orders
                else self.orders[arguments["intent_id"]].to_payload()
            )
        if mount == "upstream.orders" and operation == "orders.submit_group_leg":
            intent = order_intent_from_payload(arguments["intent"])
            self.submitted.append(intent.intent_id)
            receipt = GroupLegOrderReceipt(
                status=GroupChildOrderStatus(
                    intent_id=intent.intent_id,
                    state=ExecutionState.UNKNOWN,
                    filled_quantity=Decimal("0"),
                    reason_code="provider_outcome_unknown",
                )
            )
            self.orders[intent.intent_id] = receipt
            return _success(receipt.to_payload())
        if mount == "upstream.orders" and operation == "orders.resolve_group_leg":
            return {"ok": False, "code": "GROUP_RESOLUTION_UNAVAILABLE", "result": {}}
        raise AssertionError(f"unexpected group RPC route: {mount}/{operation}")


class _ExecutionGroupServiceLoopback:
    """Borrowed RPC surface that invokes the real group service handlers."""

    state = "registered"
    healthy = True

    def __init__(self, reservation_context: ServiceCallContext, entry_context: ServiceCallContext,
                 query_context: ServiceCallContext) -> None:
        self.reservation_context = reservation_context
        self.entry_context = entry_context
        self.query_context = query_context
        self.reservations: GroupReservationsService | None = None
        self.orders: ExecutionGroupOrderEntryService | None = None
        self.calls: list[tuple[str, str, str, str, dict[str, Any]]] = []
        self.replies: list[tuple[str, str, dict[str, Any]]] = []

    def call(
        self,
        mount: str,
        operation: str,
        arguments: dict[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
    ) -> dict[str, Any]:
        if (
            account_ref is None
            or portfolio_ref is None
            or scope_ref is None
            or max_reply_message_bytes != 1_000_000
        ):
            raise AssertionError("group RPC omitted its bounded trusted route")
        self.calls.append((mount, operation, account_ref, scope_ref, dict(arguments)))
        if mount == "upstream.account_reservations" and self.reservations is not None:
            reply = self.reservations.handle(
                operation,
                context=self.reservation_context,
                arguments=arguments,
            )
            self.replies.append((mount, operation, dict(reply)))
            return reply
        if mount == "upstream.orders" and self.orders is not None:
            reply = self.orders.handle(
                operation,
                context=self.entry_context,
                arguments=arguments,
            )
            self.replies.append((mount, operation, dict(reply)))
            return reply
        if mount == "upstream.order_query" and self.orders is not None:
            reply = self.orders.handle(
                operation,
                context=self.query_context,
                arguments=arguments,
            )
            self.replies.append((mount, operation, dict(reply)))
            return reply
        raise AssertionError(f"unexpected or unbound group RPC route: {mount}/{operation}")


class _UnusedExecutionDispatch:
    """The group session's call-local dispatcher must own every provider write."""

    def submit(self, _intent: OrderIntent) -> object:
        raise AssertionError("ordinary Execution dispatcher bypassed group proof session")

    def cancel(self, _intent: object) -> object:
        raise AssertionError("group-only service must not dispatch cancellation")


def _success(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "code": "OK", "result": result}


def _success_value(result: object) -> dict[str, Any]:
    return {"ok": True, "code": "OK", "result": result}


def _target(now_ns: int) -> tuple[WireScope, GroupCoordinatorTarget, dict[str, str]]:
    stable_scope = _strategy_scope(run_id=None)
    metadata = _metadata(now_ns)
    fingerprints = {account_ref: _digest(f"risk:{account_ref}") for account_ref in _ACCOUNTS}
    accounts = {
        account_ref: GroupAccountRoute(
            execution_scope=ExecutionScope(
                "PAPER", "simulation", account_ref, _STRATEGY, "20261015"
            ),
            risk_scope_fingerprint=fingerprints[account_ref],
            reservation_scope_ref=f"reservation-{account_ref}-v1",
            order_scope_ref=f"order-{account_ref}-v1",
            query_scope_ref=f"query-{account_ref}-v1",
            instruments={
                metadata.instrument: GroupInstrumentRule(
                    metadata=metadata,
                    base_units_per_order_unit=Decimal("1"),
                    allowed_order_types=frozenset({OrderType.LIMIT}),
                )
            },
        )
        for account_ref in _ACCOUNTS
    }
    budget_policy, budget_quote_builder = _budget_binding(metadata)
    return (
        stable_scope,
        GroupCoordinatorTarget(
            scope_ref=_COORDINATOR_SCOPE_REF,
            accounts=accounts,
            max_group_lifetime_ns=300_000_000_000,
            portfolio_budget_policy=budget_policy,
            budget_quote_builder=budget_quote_builder,
        ),
        fingerprints,
    )


def _budget_binding(
    metadata: InstrumentRiskMetadata,
    *,
    portfolio_ref: str = _PORTFOLIO,
    max_group_gross_notional: Decimal = Decimal("1000000"),
    max_active_gross_notional: Decimal = Decimal("10000000"),
    max_active_groups: int = 100,
):
    policy = PortfolioGroupBudgetPolicy(
        portfolio_ref=portfolio_ref,
        policy_id="portfolio-budget-v1",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=max_group_gross_notional,
        max_active_gross_notional=max_active_gross_notional,
        max_active_groups=max_active_groups,
    )
    builder = partial(
        build_instrument_risk_group_budget_quote,
        registry=InstrumentRiskRegistry((metadata,)),
    )
    return policy, builder


def _make_service(
    tmp_path: Path,
    runtime: _LoopbackRuntime,
    stable_scope: WireScope,
    target: GroupCoordinatorTarget,
    *,
    now_ns: list[int],
) -> tuple[GroupCoordinatorService, SqliteOrderGroupStore, GroupOriginRunBindingStore]:
    group_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    run_bindings = GroupOriginRunBindingStore(tmp_path / "group-runs.sqlite3")
    service = GroupCoordinatorService(
        group_store,
        {stable_scope: target},
        run_bindings,
        BorrowedServiceClient(runtime),
        WireIdentity(WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "coordinator-actor"),
        instance_id=_COORDINATOR_INSTANCE,
        clock_ns=lambda: now_ns[0],
    )
    return service, group_store, run_bindings


def _arguments(now_ns: int, metadata: InstrumentRiskMetadata) -> dict[str, Any]:
    deadline_ns = now_ns + 120_000_000_000
    return {
        "group_id": _GROUP_ID,
        "deadline_ns": deadline_ns,
        "first_leg_id": "leg-a",
        "legs": [
            {
                "leg_id": "leg-a",
                "account_ref": "account-a",
                "intent": _intent(
                    account_ref="account-a",
                    leg_id="leg-a",
                    deadline_ns=deadline_ns,
                    metadata=metadata,
                    side=Side.BUY,
                ).to_payload(),
            },
            {
                "leg_id": "leg-b",
                "account_ref": "account-b",
                "intent": _intent(
                    account_ref="account-b",
                    leg_id="leg-b",
                    deadline_ns=deadline_ns,
                    metadata=metadata,
                    side=Side.SELL,
                ).to_payload(),
            },
        ],
    }


def _new_coordinator_service(
    tmp_path: Path,
    runtime: _LoopbackRuntime,
    scopes: Mapping[WireScope, GroupCoordinatorTarget],
) -> tuple[GroupCoordinatorService, SqliteOrderGroupStore, GroupOriginRunBindingStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    group_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    run_bindings = GroupOriginRunBindingStore(tmp_path / "group-runs.sqlite3")
    try:
        service = GroupCoordinatorService(
            group_store,
            scopes,
            run_bindings,
            BorrowedServiceClient(runtime),
            WireIdentity(WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "coordinator-actor"),
            instance_id=_COORDINATOR_INSTANCE,
            clock_ns=time.time_ns,
        )
    except Exception:
        group_store.close()
        run_bindings.close()
        raise
    return service, group_store, run_bindings


def test_missing_budget_policy_rejects_submit_before_run_binding_and_keeps_query_readable(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    target_without_budget = replace(target, portfolio_budget_policy=None, budget_quote_builder=None)
    runtime = _LoopbackRuntime(fingerprints)
    service, group_store, run_bindings = _make_service(
        tmp_path, runtime, stable_scope, target_without_budget, now_ns=now_ns
    )
    registration = _registration(now_ns[0])
    service.maintain_registrations((registration,))
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    try:
        denied = service.handle(
            "groups.submit",
            context=registration.context,
            arguments=_arguments(now_ns[0], metadata),
        )
        assert denied == {"ok": False, "code": "GROUP_BUDGET_POLICY_REQUIRED", "result": {}}
        assert run_bindings.get(_PORTFOLIO, _GROUP_ID) is None
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
        assert runtime.calls == []
        assert runtime.reservations == {}
        assert runtime.submitted == []

        queried = service.handle(
            "groups.query",
            context=registration.context,
            arguments={"group_id": _GROUP_ID},
        )
        assert queried == {"ok": True, "code": "OK", "result": {"found": False, "snapshot": None}}
        assert runtime.calls == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_wire_cannot_supply_budget_quote_or_policy_resources(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    runtime = _LoopbackRuntime(fingerprints)
    service, group_store, run_bindings = _make_service(
        tmp_path, runtime, stable_scope, target, now_ns=now_ns
    )
    registration = _registration(now_ns[0])
    service.maintain_registrations((registration,))
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    arguments = _arguments(now_ns[0], metadata)
    arguments["budget_quote"] = {"gross_notional": "0.01"}
    arguments["max_active_gross_notional"] = "999999999999"
    try:
        denied = service.handle("groups.submit", context=registration.context, arguments=arguments)
        assert denied == {"ok": False, "code": "INVALID_REQUEST", "result": {}}
        assert run_bindings.list_all() == ()
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
        assert runtime.calls == []
        assert runtime.reservations == {}
        assert runtime.submitted == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()






def test_budget_resources_must_pair_match_portfolio_and_not_conflict(tmp_path: Path) -> None:
    now_ns = time.time_ns()
    stable_scope, target, fingerprints = _target(now_ns)
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    with pytest.raises(ServiceExecutionGroupError) as unpaired:
        replace(target, budget_quote_builder=None)
    assert unpaired.value.code == "GROUP_BUDGET_CONFIGURATION_INVALID"

    wrong_policy, wrong_builder = _budget_binding(metadata, portfolio_ref="another-portfolio")
    wrong_target = replace(
        target,
        portfolio_budget_policy=wrong_policy,
        budget_quote_builder=wrong_builder,
    )
    runtime = _LoopbackRuntime(fingerprints)
    with pytest.raises(ServiceExecutionGroupError) as wrong_portfolio:
        _new_coordinator_service(
            tmp_path / "wrong-portfolio", runtime, {stable_scope: wrong_target}
        )
    assert wrong_portfolio.value.code == "GROUP_BUDGET_POLICY_SCOPE_MISMATCH"

    other_strategy_scope = replace(stable_scope, strategy_ref="strategy-b")
    other_accounts = {
        account_ref: replace(
            route,
            execution_scope=replace(route.execution_scope, strategy_id="strategy-b"),
        )
        for account_ref, route in target.accounts.items()
    }
    conflicting_policy = replace(target.portfolio_budget_policy, policy_id="different-policy")
    assert conflicting_policy is not None
    other_target = replace(
        target,
        accounts=other_accounts,
        portfolio_budget_policy=conflicting_policy,
    )
    with pytest.raises(ServiceExecutionGroupError) as conflict:
        _new_coordinator_service(
            tmp_path / "policy-conflict",
            runtime,
            {stable_scope: target, other_strategy_scope: other_target},
        )
    assert conflict.value.code == "GROUP_BUDGET_POLICY_CONFLICT"


def test_coordinator_uses_trusted_registration_and_never_resubmits_unknown_after_restart(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, scope_fingerprints = _target(now_ns[0])
    request = _arguments(
        now_ns[0], next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    )
    runtime = _LoopbackRuntime(scope_fingerprints)
    service, group_store, run_bindings = _make_service(
        tmp_path, runtime, stable_scope, target, now_ns=now_ns
    )
    registration = _registration(now_ns[0])
    service.maintain_registrations((registration,))
    primary_resources_open = True
    restarted_resources: (
        tuple[GroupCoordinatorService, SqliteOrderGroupStore, GroupOriginRunBindingStore] | None
    ) = None
    try:
        accepted = service.handle("groups.submit", context=registration.context, arguments=request)
        assert accepted["ok"] is True
        snapshot = GroupOrderSnapshot.from_payload(accepted["result"]["snapshot"])
        assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert snapshot.legs[0].phase.value == "UNKNOWN"
        first_intent_id = snapshot.legs[0].submitted_intent.intent_id
        assert runtime.submitted == [first_intent_id]
        assert len(runtime.reservations) == 2

        # A new registration triggers one recovery query but never redispatches
        # an UNKNOWN order. The later read only returns the durable result.
        service.close()
        group_store.close()
        run_bindings.close()
        primary_resources_open = False
        restarted_runtime = _LoopbackRuntime(scope_fingerprints)
        restarted_runtime.orders.update(runtime.orders)
        restarted_resources = _make_service(
            tmp_path,
            restarted_runtime,
            stable_scope,
            target,
            now_ns=now_ns,
        )
        restarted_service, restarted_groups, restarted_bindings = restarted_resources
        renewed = _registration(
            now_ns[0], registration_id="registration-b", service_epoch="epoch-b"
        )
        restarted_service.maintain_registrations((renewed,))
        queried = restarted_service.handle(
            "groups.query",
            context=renewed.context,
            arguments={"group_id": _GROUP_ID},
        )
        assert queried["ok"] is True
        recovered_snapshot = GroupOrderSnapshot.from_payload(queried["result"]["snapshot"])
        assert recovered_snapshot.revision == snapshot.revision + 1
        assert replace(recovered_snapshot, revision=snapshot.revision) == snapshot
        assert [call[1] for call in restarted_runtime.calls] == ["orders.query_group_leg"]
        assert restarted_runtime.submitted == []

        replayed = restarted_service.handle(
            "groups.submit", context=renewed.context, arguments=request
        )
        assert replayed["ok"] is True
        replayed_snapshot = GroupOrderSnapshot.from_payload(replayed["result"]["snapshot"])
        assert replayed_snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert replayed_snapshot.legs[0].submitted_intent.intent_id == first_intent_id
        assert restarted_runtime.submitted == []
        assert [call[1] for call in restarted_runtime.calls] == [
            "orders.query_group_leg", "orders.query_group_leg"
        ]
    finally:
        service.close()
        if primary_resources_open:
            group_store.close()
            run_bindings.close()
        if restarted_resources is not None:
            restarted_service, restarted_groups, restarted_bindings = restarted_resources
            restarted_service.close()
            restarted_groups.close()
            restarted_bindings.close()


def test_coordinator_rejects_caller_run_authority_and_expired_registration_without_rpc(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    runtime = _LoopbackRuntime(fingerprints)
    service, group_store, run_bindings = _make_service(
        tmp_path, runtime, stable_scope, target, now_ns=now_ns
    )
    registration = replace(_registration(now_ns[0]), expires_at=now_ns[0] / 1_000_000_000 + 1)
    service.maintain_registrations((registration,))
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    request = _arguments(now_ns[0], metadata)
    try:
        forged = dict(request, origin_run_id="caller-selected-run")
        denied = service.handle("groups.submit", context=registration.context, arguments=forged)
        assert denied["ok"] is False
        assert denied["code"] == "INVALID_REQUEST"
        assert runtime.calls == []
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
        assert run_bindings.list_all() == ()

        now_ns[0] = int(registration.expires_at * 1_000_000_000) + 1_000_000
        expired = service.handle("groups.submit", context=registration.context, arguments=request)
        assert expired["ok"] is False
        assert expired["code"] == "REGISTRATION_EXPIRED"
        assert runtime.calls == []
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_group_target_fails_closed_when_it_advertises_market_order_terms() -> None:
    now_ns = time.time_ns()
    metadata = _metadata(now_ns)
    try:
        GroupInstrumentRule(
            metadata=metadata,
            base_units_per_order_unit=Decimal("1"),
            allowed_order_types=frozenset({OrderType.MARKET}),
        )
    except ServiceExecutionGroupError as error:
        assert error.code == "GROUP_INSTRUMENT_RULE_INVALID"
    else:
        raise AssertionError("the trusted group route exposed unsupported market orders")


def test_query_only_registration_cannot_advance_or_submit_a_group(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    runtime = _LoopbackRuntime(fingerprints)
    service, group_store, run_bindings = _make_service(
        tmp_path, runtime, stable_scope, target, now_ns=now_ns
    )
    submit_registration = _registration(now_ns[0])
    query_registration = replace(
        submit_registration,
        context=replace(
            submit_registration.context,
            registration_id="query-only-registration",
        ),
        operations=frozenset({"groups.query"}),
        write_operations=frozenset(),
    )
    service.maintain_registrations((query_registration,))
    try:
        missing = service.handle(
            "groups.query",
            context=query_registration.context,
            arguments={"group_id": _GROUP_ID},
        )
        assert missing == _success({"found": False, "snapshot": None})
        metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
        denied = service.handle(
            "groups.submit",
            context=query_registration.context,
            arguments=_arguments(now_ns[0], metadata),
        )
        assert denied["ok"] is False
        assert denied["code"] == "OPERATION_NOT_GRANTED"
        assert runtime.calls == []
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
        assert run_bindings.get(_PORTFOLIO, _GROUP_ID) is None
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_re_registration_by_a_different_strategy_principal_cannot_take_over_group(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    request = _arguments(now_ns[0], metadata)
    runtime = _LoopbackRuntime(fingerprints)
    service, group_store, run_bindings = _make_service(
        tmp_path, runtime, stable_scope, target, now_ns=now_ns
    )
    original = _registration(now_ns[0])
    service.maintain_registrations((original,))
    try:
        first = service.handle("groups.submit", context=original.context, arguments=request)
        assert first["ok"] is True
        submitted = tuple(runtime.submitted)
        calls = tuple(runtime.calls)
        assert len(submitted) == 1

        changed_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            "different-strategy-principal",
            actor_ref=_STRATEGY,
        )
        changed_context = replace(
            original.context,
            identity=changed_identity,
            registration_id="different-principal-registration",
            principal_ref=changed_identity.principal_ref,
        )
        changed_registration = replace(
            original,
            context=changed_context,
            service_epoch="epoch-c",
        )
        service.maintain_registrations((changed_registration,))
        takeover = service.handle("groups.submit", context=changed_context, arguments=request)
        assert takeover["ok"] is False
        assert takeover["code"] == "GROUP_BINDING_CONFLICT"
        assert tuple(runtime.submitted) == submitted
        assert tuple(runtime.calls) == calls
    finally:
        service.close()
        group_store.close()
        run_bindings.close()
