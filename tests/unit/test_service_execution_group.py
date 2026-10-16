"""Focused SDK adapter coverage for authenticated two-leg coordination.

The account/provider boundary is deliberately synthetic here. The test uses
the real Execution group store/coordinator and real SDK adapter, then verifies
that an ambiguous child is queried by its original ID after a registration
restart instead of submitted a second time.
"""

from __future__ import annotations

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


def test_budget_denial_and_unsupported_builder_error_are_stable_and_pre_dispatch(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    small_policy, small_builder = _budget_binding(
        metadata,
        max_group_gross_notional=Decimal("1"),
        max_active_gross_notional=Decimal("100000"),
    )
    over_limit_target = replace(
        target,
        portfolio_budget_policy=small_policy,
        budget_quote_builder=small_builder,
    )
    runtime = _LoopbackRuntime(fingerprints)
    over_limit_path = tmp_path / "over-limit"
    over_limit_path.mkdir()
    service, group_store, run_bindings = _make_service(
        over_limit_path, runtime, stable_scope, over_limit_target, now_ns=now_ns
    )
    registration = _registration(now_ns[0])
    service.maintain_registrations((registration,))
    try:
        result = service.handle(
            "groups.submit",
            context=registration.context,
            arguments=_arguments(now_ns[0], metadata),
        )
        assert result["ok"] is True
        denied_snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])
        assert denied_snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
        assert denied_snapshot.error_code == "group_budget_limit_exceeded"
        assert denied_snapshot.budget_reservation is not None
        assert denied_snapshot.budget_reservation.policy_fingerprint == small_policy.fingerprint
        assert runtime.calls == []
        assert runtime.reservations == {}
        assert runtime.submitted == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()

    def unsupported_builder(_request, _policy, *, now_ns: int):
        del now_ns
        raise GroupBudgetUnsupportedOrderError("trusted price bound is unavailable")

    unsupported_target = replace(target, budget_quote_builder=unsupported_builder)
    runtime = _LoopbackRuntime(fingerprints)
    unsupported_path = tmp_path / "unsupported"
    unsupported_path.mkdir()
    service, group_store, run_bindings = _make_service(
        unsupported_path, runtime, stable_scope, unsupported_target, now_ns=now_ns
    )
    service.maintain_registrations((registration,))
    try:
        result = service.handle(
            "groups.submit",
            context=registration.context,
            arguments=_arguments(now_ns[0], metadata),
        )
        assert result == {
            "ok": False,
            "code": "GROUP_BUDGET_MARKET_PRICE_UNBOUNDED",
            "result": {},
        }
        assert runtime.calls == []
        assert runtime.reservations == {}
        assert runtime.submitted == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()

    def denied_builder(_request, _policy, *, now_ns: int):
        del now_ns
        raise RiskDeniedError("INSTRUMENT_PRICE_UNPROVEN", "untrusted details are not returned")

    denied_target = replace(target, budget_quote_builder=denied_builder)
    runtime = _LoopbackRuntime(fingerprints)
    denied_path = tmp_path / "risk-denied"
    denied_path.mkdir()
    service, group_store, run_bindings = _make_service(
        denied_path, runtime, stable_scope, denied_target, now_ns=now_ns
    )
    service.maintain_registrations((registration,))
    try:
        result = service.handle(
            "groups.submit",
            context=registration.context,
            arguments=_arguments(now_ns[0], metadata),
        )
        assert result == {
            "ok": False,
            "code": "INSTRUMENT_PRICE_UNPROVEN",
            "result": {},
        }
        assert runtime.calls == []
        assert runtime.reservations == {}
        assert runtime.submitted == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_exact_expired_group_submit_replay_uses_registry_binding_without_requoting(
    tmp_path: Path,
) -> None:
    """A current Registry grant can replay the exact durable ID after its deadline.

    The first request is durably rejected by the trusted budget policy, so the
    test can exercise the persisted idempotency boundary without dispatching a
    provider order or creating account reservations.
    """

    now_ns = [time.time_ns()]
    stable_scope, target, fingerprints = _target(now_ns[0])
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    policy, base_builder = _budget_binding(
        metadata,
        max_group_gross_notional=Decimal("1"),
        max_active_gross_notional=Decimal("100000"),
    )
    quote_calls: list[tuple[str, int]] = []

    def counted_builder(request, current_policy, *, now_ns: int):
        quote_calls.append((request.group_id, now_ns))
        return base_builder(request, current_policy, now_ns=now_ns)

    target = replace(
        target,
        portfolio_budget_policy=policy,
        budget_quote_builder=counted_builder,
    )
    runtime = _LoopbackRuntime(fingerprints)
    service_path = tmp_path / "coordinator"
    service_path.mkdir()
    service, group_store, run_bindings = _make_service(
        service_path, runtime, stable_scope, target, now_ns=now_ns
    )

    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _STRATEGY_PRINCIPAL,
        actor_ref=_STRATEGY,
    )
    registered_scope = replace(stable_scope, run_id=_RUN)
    capability = ServiceCapability(
        _COORDINATOR_INSTANCE,
        "service.order_group_entry",
        GROUP_COORDINATOR_OPERATIONS,
        frozenset({"groups.submit"}),
    )
    resource = ResourceGrant(
        _COORDINATOR_INSTANCE,
        "service.order_group_entry",
        _COORDINATOR_SCOPE_REF,
        stable_scope,
        GROUP_COORDINATOR_OPERATIONS,
    )
    registry = ServiceRegistry(
        tmp_path / "registry.sqlite3",
        service_id="group-coordinator-test",
        capabilities=(capability,),
        grants={_STRATEGY_PRINCIPAL: (PrincipalGrant(identity, (resource,)),)},
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=7_200,
        clock=lambda: now_ns[0] / 1_000_000_000,
    )
    service_holder: list[GroupCoordinatorService] = [service]
    registry.register_handler(
        _COORDINATOR_INSTANCE,
        "groups.submit",
        lambda context, arguments: service_holder[0].handle(
            "groups.submit", context=context, arguments=arguments
        ),
    )
    registry.register_handler(
        _COORDINATOR_INSTANCE,
        "groups.query",
        lambda context, arguments: service_holder[0].handle(
            "groups.query", context=context, arguments=arguments
        ),
    )

    message_number = 0

    def registry_call(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal message_number
        message_number += 1
        request = WireEnvelope(
            header=WireHeader(
                protocol_version=2,
                kind=WireMessageKind.REQUEST,
                message_id=f"expired-replay-{message_number}",
                correlation_id=None,
                identity=identity,
                scope=registered_scope,
                operation=operation,
                schema_id="sdk.service.call",
                schema_version=1,
                sent_at_utc="2026-10-16T00:00:00Z",
            ),
            payload={
                "registration_id": registration_id,
                "service_epoch": service_epoch,
                "instance_id": _COORDINATOR_INSTANCE,
                "arguments": payload,
            },
        )
        response = registry.handle(_STRATEGY_PRINCIPAL, request)
        assert response["ok"] is True
        return dict(response["result"])

    registration_response = registry.handle(
        _STRATEGY_PRINCIPAL,
        WireEnvelope(
            header=WireHeader(
                protocol_version=2,
                kind=WireMessageKind.REQUEST,
                message_id="expired-replay-register",
                correlation_id=None,
                identity=identity,
                scope=registered_scope,
                operation="service.register",
                schema_id="sdk.service.control",
                schema_version=1,
                sent_at_utc="2026-10-16T00:00:00Z",
            ),
            payload={
                "registration_key": "expired-replay-registration-key",
                "client_session_id": "expired-replay-client-session",
                "instance_id": _COORDINATOR_INSTANCE,
                "port": "service.order_group_entry",
                "scope_ref": _COORDINATOR_SCOPE_REF,
                "operations": tuple(sorted(GROUP_COORDINATOR_OPERATIONS)),
            },
        ),
    )
    assert registration_response["ok"] is True
    registration_result = registration_response["result"]
    registration_id = registration_result["registration_id"]
    service_epoch = registration_result["service_epoch"]
    registration_contexts = registry.active_registration_contexts(
        instance_id=_COORDINATOR_INSTANCE,
        port="service.order_group_entry",
    )
    assert len(registration_contexts) == 1
    service.maintain_registrations(registration_contexts)

    request = _arguments(now_ns[0], metadata)
    resources_open = True
    restarted_resources = None
    try:
        first = registry_call("groups.submit", request)
        assert first["ok"] is True
        first_snapshot = GroupOrderSnapshot.from_payload(first["result"]["snapshot"])
        assert first_snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
        assert first_snapshot.error_code == "group_budget_limit_exceeded"
        assert quote_calls == [(request["group_id"], now_ns[0])]
        assert run_bindings.get(_PORTFOLIO, _GROUP_ID) is not None
        assert runtime.calls == []
        assert runtime.reservations == {}
        assert runtime.submitted == []

        # Close and reopen only the coordinator's durable stores. The real
        # Registry registration remains current while the original absolute
        # group deadline expires.
        deadline_ns = request["deadline_ns"]
        service.close()
        group_store.close()
        run_bindings.close()
        resources_open = False
        now_ns[0] = deadline_ns + 1

        restarted_runtime = _LoopbackRuntime(fingerprints)
        restarted_resources = _make_service(
            service_path,
            restarted_runtime,
            stable_scope,
            target,
            now_ns=now_ns,
        )
        restarted_service, restarted_groups, restarted_bindings = restarted_resources
        service_holder[0] = restarted_service
        restarted_service.maintain_registrations(
            registry.active_registration_contexts(
                instance_id=_COORDINATOR_INSTANCE,
                port="service.order_group_entry",
            )
        )

        queried = registry_call("groups.query", {"group_id": _GROUP_ID})
        assert queried["ok"] is True
        assert GroupOrderSnapshot.from_payload(queried["result"]["snapshot"]) == first_snapshot

        replayed = registry_call("groups.submit", request)
        assert replayed["ok"] is True
        assert GroupOrderSnapshot.from_payload(replayed["result"]["snapshot"]) == first_snapshot
        assert len(quote_calls) == 1
        assert restarted_runtime.calls == []
        assert restarted_runtime.reservations == {}
        assert restarted_runtime.submitted == []

        changed_request = dict(request)
        changed_legs = [dict(item) for item in request["legs"]]
        changed_intent = order_intent_from_payload(changed_legs[0]["intent"])
        changed_legs[0]["intent"] = replace(
            changed_intent,
            price=changed_intent.price + Decimal("1"),
        ).to_payload()
        changed_request["legs"] = changed_legs
        conflict = registry_call("groups.submit", changed_request)
        assert conflict == {"ok": False, "code": "GROUP_BINDING_CONFLICT", "result": {}}
        assert len(quote_calls) == 1
        assert restarted_runtime.calls == []
        assert restarted_runtime.reservations == {}
        assert restarted_runtime.submitted == []

        expired_group_id = "new-expired-group"
        expired_request = {
            "group_id": expired_group_id,
            "deadline_ns": deadline_ns,
            "first_leg_id": "new-leg-a",
            "legs": [],
        }
        for leg_id, account_ref, side in (
            ("new-leg-a", "account-a", Side.BUY),
            ("new-leg-b", "account-b", Side.SELL),
        ):
            child = _intent(
                account_ref=account_ref,
                leg_id=leg_id,
                deadline_ns=deadline_ns,
                metadata=metadata,
                side=side,
            )
            child = replace(child, intent_id=group_leg_intent_id(expired_group_id, leg_id))
            expired_request["legs"].append(
                {"leg_id": leg_id, "account_ref": account_ref, "intent": child.to_payload()}
            )
        expired = registry_call("groups.submit", expired_request)
        assert expired == {"ok": False, "code": "GROUP_DEADLINE_INVALID", "result": {}}
        assert restarted_bindings.get(_PORTFOLIO, expired_group_id) is None
        assert restarted_groups.get(_PORTFOLIO, expired_group_id) is None
        assert len(quote_calls) == 1
        assert restarted_runtime.calls == []
        assert restarted_runtime.reservations == {}
        assert restarted_runtime.submitted == []
    finally:
        if resources_open:
            service.close()
            group_store.close()
            run_bindings.close()
        if restarted_resources is not None:
            restarted_service, restarted_groups, restarted_bindings = restarted_resources
            restarted_service.close()
            restarted_groups.close()
            restarted_bindings.close()
        registry.close()


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

        # A new component and registration epoch can inspect the durable local
        # coordinator state without any downstream call or redispatch.
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
        assert GroupOrderSnapshot.from_payload(queried["result"]["snapshot"]) == snapshot
        assert restarted_runtime.calls == []

        replayed = restarted_service.handle(
            "groups.submit", context=renewed.context, arguments=request
        )
        assert replayed["ok"] is True
        replayed_snapshot = GroupOrderSnapshot.from_payload(replayed["result"]["snapshot"])
        assert replayed_snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert replayed_snapshot.legs[0].submitted_intent.intent_id == first_intent_id
        assert restarted_runtime.submitted == []
        assert [call[1] for call in restarted_runtime.calls] == ["orders.query_group_leg"]
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
    registration = _registration(now_ns[0])
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

        now_ns[0] = int(registration.expires_at * 1_000_000_000) + 1
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


def test_normal_group_services_complete_two_filled_legs_from_source_backed_provider_facts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exercise the real SDK group adapters and durable stores end to end.

    Only the exchange leaf is synthetic. The request crosses the same typed
    borrowed-client route used by ServiceHost, reservations go through the
    real Risk adapter, and the account owner uses the real Execution facade,
    BtApi normalization, proof journal, and Risk terminal receipt.
    """

    from contextlib import ExitStack

    now_ns = time.time_ns()
    deadline_ns = now_ns + 120_000_000_000
    account_ref = "account-a"
    group_id = "group-full"
    execution_scope = ExecutionScope(
        "fixture", "simulation", account_ref, _STRATEGY, "20261015"
    )
    owner_scope = WireScope(
        _DOMAIN,
        account_ref=account_ref,
        strategy_ref=_STRATEGY,
        portfolio_ref=_PORTFOLIO,
    )
    strategy_template = WireScope(
        _DOMAIN,
        account_refs=(account_ref,),
        strategy_ref=_STRATEGY,
        portfolio_ref=_PORTFOLIO,
    )
    strategy_scope = replace(strategy_template, run_id=_RUN)
    metadata = InstrumentRiskMetadata(
        instrument=_V2_INSTRUMENT,
        metadata_version=_V2_METADATA_VERSION,
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("100000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="base",
        valuation_unit="USD",
    )
    registry = InstrumentRiskRegistry((metadata,))
    provider_account = _V2_PROVIDER_ACCOUNT
    risk_scope = AccountScope("fixture", provider_account, "simulation")
    risk_target = RiskOrderTarget(
        account_scope=risk_scope,
        strategy_id=_STRATEGY,
        exchange_name="FIXTURE___SPOT",
        provider_account_id=provider_account,
        registry=registry,
    )
    class _GroupMappedV2SyntheticFeed(_V2SyntheticBtApiFeed):
        def make_order(
            self,
            symbol: str,
            volume: float,
            price: float,
            order_type: str,
            *,
            client_order_id: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            # Check the arguments the real DirectBackend/FeedAdapter actually
            # deliver from the typed Binance mapper before recording a write.
            assert symbol == _V2_INSTRUMENT
            assert Decimal(str(volume)) == Decimal("1")
            assert Decimal(str(price)) == Decimal("100")
            assert order_type in {"buy-limit", "sell-limit"}
            assert client_order_id.startswith("grp-") and len(client_order_id) == 44
            assert kwargs == {
                "offset": "open",
                "post_only": False,
                "reduce_only": False,
                "time_in_force": "IOC",
            }
            return super().make_order(
                symbol,
                volume,
                price,
                order_type,
                client_order_id=client_order_id,
                **kwargs,
            )

    provider = _GroupMappedV2SyntheticFeed(
        str(tmp_path / "group-provider.sqlite3"), ("filled", "filled"), provider_account
    )

    # FIXTURE___SPOT is synthetic and intentionally absent from the production
    # mapper registry. Its typed group order has the same OPEN/IOC/base-unit
    # mapping as the real Binance mapper. Verify that mapping from the exact
    # typed request shape, then install only that trusted mapper function under
    # the synthetic venue for this test. Production DirectBackend rejection
    # for an unmapped venue remains unchanged.
    from bt_api_py._contracts.models import (
        OrderRequest as BtOrderRequest,
    )
    from bt_api_py._contracts.models import (
        OrderType as BtOrderType,
    )
    from bt_api_py._contracts.models import (
        Side as BtSide,
    )
    from bt_api_py._venue_mappers import MAPPERS

    fixture_mapper = MAPPERS["BINANCE___SPOT"]
    mapped_preflight = fixture_mapper(
        BtOrderRequest(
            symbol=_V2_INSTRUMENT,
            side=BtSide.BUY,
            order_type=BtOrderType.LIMIT,
            quantity=Decimal("1"),
            account_id=provider_account,
            client_order_id="group-fixture-preflight",
            price=Decimal("100"),
            time_in_force="IOC",
            reduce_only=False,
            idempotency_key="group-fixture-preflight",
            quantity_unit="base",
            offset="open",
        )
    )
    assert mapped_preflight == {
        "symbol": _V2_INSTRUMENT,
        "vol": "1",
        "price": "100",
        "order_type": "buy-limit",
        "offset": "open",
        "post_only": False,
        "client_order_id": "group-fixture-preflight",
        "reduce_only": False,
        "time_in_force": "IOC",
    }
    monkeypatch.setitem(MAPPERS, "FIXTURE___SPOT", fixture_mapper)

    with ExitStack() as cleanup:
        bt_api = BtApi(debug=False)
        cleanup.callback(bt_api.close)
        bt_api.exchange_feeds["FIXTURE___SPOT"] = provider

        execution_store = SqliteExecutionStore(tmp_path / "group-execution.sqlite3")
        cleanup.callback(execution_store.close)
        risk_order_journal = RiskOrderJournal(
            tmp_path / "group-risk-order.sqlite3", writer_id="group-risk-order-owner"
        )
        proof_journal = GroupProofJournal(
            tmp_path / "group-proof.sqlite3", writer_id="group-proof-owner"
        )
        cleanup.callback(proof_journal.close)

        provider_contract = RiskBtApiProviderContract(
            exchange_name="FIXTURE___SPOT",
            profile_id="service-group-provider-v2",
            order_schema=_V2_ORDER_SCHEMA,
            fill_schema=_V2_FILL_SCHEMA,
            client_order_id_query=True,
            client_order_id_query_unique=True,
            complete_order_snapshot=True,
            complete_fill_history=True,
            provider_accepts_client_order_id=True,
            provider_status_mapping=(
                ("cancelled", "canceled"),
                ("expired", "expired"),
                ("filled", "completed"),
                ("open", "accepted"),
                ("partial", "partial"),
                ("rejected", "rejected"),
            ),
            supported_order_types=("LIMIT",),
            supported_time_in_force=("IOC",),
            supported_position_effects=("OPEN",),
            normalizer_schema="bt-api-order-source-provenance-v2",
            order_revision_field="order_revision",
            fill_revision_field="fill_revision",
            fill_watermark_field="fill_watermark",
            fee_amount_field="fee_amount",
            fee_currency_field="fee_currency",
            quote_currency_by_instrument=((_V2_INSTRUMENT, "USD"),),
        )
        proof_target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=provider_contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        proof_source = GroupProofSource(
            proof_journal, {owner_scope: proof_target}
        )
        risk_gate = DurableRiskGate(
            tmp_path / "group-risk.sqlite3",
            RiskPolicy(
                "service-group-risk-policy-v1",
                Decimal("100000"),
                max_increase_count=10,
                permit_ttl_seconds=240,
                require_strategy_allocation=False,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
            execution_journal_authority=proof_source,
        )
        cleanup.callback(risk_gate.close)
        risk_gate.set_strategy_allocation(
            risk_scope,
            _STRATEGY,
            "group-allocation-v1",
            max_notional=Decimal("100000"),
            notional_unit="USD",
        )
        mapper = InstrumentRiskAdmissionMapper(
            risk_scope,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda routed_scope: risk_gate.get_strategy_allocation(
                risk_scope, routed_scope.strategy_id
            ),
            wire_account_ref=account_ref,
        )
        risk_admission = DurableRiskAdmissionAdapter(risk_gate, mapper)
        account_admission = AccountScopedGroupAdmissionAdapter(
            risk_admission, account_scope=risk_scope
        )
        reservation_target = GroupReservationTarget(risk_scope, risk_admission)
        proof_reservation_target = ProofReservationTarget(risk_scope, risk_admission)
        bundle = GroupProofRuntimeBundle(
            risk_admission,
            account_admission,
            MappingProxyType({owner_scope: proof_reservation_target}),
            MappingProxyType({owner_scope: proof_target}),
            proof_source,
        )
        bundle.require_runtime_bundle(
            account_scoped_admission=account_admission,
            risk_admission=risk_admission,
        )

        accepted_store = AcceptedGroupBindingStore(tmp_path / "group-accepted.sqlite3")
        cleanup.callback(accepted_store.close)
        authority = ExecutionGroupAuthority(
            tmp_path / "group-authority.sqlite3",
            completion_verifier=ExecutionStoreGroupCompletionVerifier(
                execution_store, execution_scopes=(execution_scope,)
            ),
        )
        cleanup.callback(authority.close)

        service_identity = WireIdentity(
            WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "group-coordinator-actor"
        )
        owner_instance = "group-account-owner-a"
        reservation_scope_ref = "reservation-scope-a"
        entry_scope_ref = "order-entry-scope-a"
        query_scope_ref = "order-query-scope-a"
        reservation_context = ServiceCallContext(
            identity=service_identity,
            scope=owner_scope,
            registration_id="group-reservation-registration",
            principal_ref=_COORDINATOR_PRINCIPAL,
        )
        entry_context = ServiceCallContext(
            identity=service_identity,
            scope=owner_scope,
            registration_id="group-order-entry-registration",
            principal_ref=_COORDINATOR_PRINCIPAL,
        )
        query_context = ServiceCallContext(
            identity=service_identity,
            scope=owner_scope,
            registration_id="group-order-query-registration",
            principal_ref=_COORDINATOR_PRINCIPAL,
        )
        expiry = time.time() + 3600
        service_epoch = "group-owner-epoch-a"
        policy_version = "group-policy-v1"
        requirements_version = "group-requirements-v1"
        reservation_operations = GroupReservationsService.SUPPORTED_OPERATIONS
        reservation_registration = ActiveRegistrationContext(
            context=reservation_context,
            instance_id=owner_instance,
            port="service.group_reservations",
            scope_ref=reservation_scope_ref,
            operations=reservation_operations,
            write_operations=frozenset({"reservations.reserve", "reservations.release"}),
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expiry,
            strategy_write_run_id=_RUN,
        )
        entry_operations = frozenset(
            {"orders.submit_group_leg", "orders.resolve_group_leg"}
        )
        entry_registration = ActiveRegistrationContext(
            context=entry_context,
            instance_id=owner_instance,
            port="service.order_entry",
            scope_ref=entry_scope_ref,
            operations=entry_operations,
            write_operations=entry_operations,
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expiry,
            strategy_write_run_id=_RUN,
        )
        query_registration = ActiveRegistrationContext(
            context=query_context,
            instance_id=owner_instance,
            port="service.order_query",
            scope_ref=query_scope_ref,
            operations=frozenset({"orders.query_group_leg"}),
            write_operations=frozenset(),
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expiry,
            strategy_write_run_id=_RUN,
        )
        reservation_policy = TrustedGroupReservationPolicy(
            owner_instance,
            _COORDINATOR_PRINCIPAL,
            service_identity,
            reservation_scope_ref,
            owner_scope,
            _RUN,
            account_ref,
        )
        write_policy = TrustedGroupWritePolicy(
            owner_instance,
            _COORDINATOR_PRINCIPAL,
            service_identity,
            entry_scope_ref,
            owner_scope,
            _RUN,
        )
        resolution_policy = TrustedGroupResolutionPolicy(
            owner_instance,
            _COORDINATOR_PRINCIPAL,
            service_identity,
            entry_scope_ref,
            owner_scope,
            _RUN,
            account_ref,
        )

        owner_service = ExecutionGroupOrderEntryService(
            execution_store,
            {owner_scope: execution_scope},
            _UnusedExecutionDispatch(),
            account_admission,
            accepted_store,
            authority.guard,
            proof_source,
            (write_policy,),
            (resolution_policy,),
            writer_id=owner_instance,
        )
        cleanup.callback(owner_service.close)
        assert owner_service.supported_operations == GROUP_ACCOUNT_OWNER_OPERATIONS
        assert owner_service.recover_interrupted_dispatches() == ()
        owner_service.maintain_registrations((entry_registration, query_registration))
        assert owner_service.ready is True

        no_provider_activity = provider.snapshot()

        def assert_query_denied(
            registrations: tuple[ActiveRegistrationContext, ...],
            context: ServiceCallContext,
            expected_code: str,
        ) -> None:
            owner_service.maintain_registrations(registrations)
            denied = owner_service.handle(
                "orders.query_group_leg",
                context=context,
                arguments={"origin_run_id": _RUN, "intent_id": "group-full-leg-a"},
            )
            assert denied["ok"] is False
            assert denied["code"] == expected_code
            assert provider.snapshot() == no_provider_activity

        # Query is admitted by its own current, read-only Registry view. It
        # cannot be inferred from a write policy when that view is absent,
        # stale for this run, bound to another identity, or out of target.
        assert_query_denied(
            (entry_registration,), query_context, "REGISTRATION_NOT_ACTIVE"
        )
        wrong_run_query = replace(query_registration, strategy_write_run_id="another-run")
        assert_query_denied(
            (entry_registration, wrong_run_query),
            query_context,
            "REGISTRATION_NOT_ACTIVE",
        )
        wrong_identity = WireIdentity(
            WireRole.SERVICE,
            _COORDINATOR_PRINCIPAL,
            "different-group-service-actor",
        )
        wrong_identity_context = replace(query_context, identity=wrong_identity)
        wrong_identity_query = replace(query_registration, context=wrong_identity_context)
        assert_query_denied(
            (entry_registration, wrong_identity_query),
            wrong_identity_context,
            "REGISTRATION_NOT_ACTIVE",
        )
        wrong_target_scope = WireScope(
            _DOMAIN,
            account_ref="account-b",
            strategy_ref=_STRATEGY,
            portfolio_ref=_PORTFOLIO,
        )
        wrong_target_context = replace(query_context, scope=wrong_target_scope)
        wrong_target_query = replace(query_registration, context=wrong_target_context)
        assert_query_denied(
            (entry_registration, wrong_target_query),
            wrong_target_context,
            "SERVICE_AUTHORITY_INVALID",
        )
        owner_service.maintain_registrations((entry_registration, query_registration))

        authority.control.activate(
            entry_context,
            execution_scope=execution_scope,
            risk_account_scope_key=risk_scope.key,
        )
        reservation_service = GroupReservationsService(
            {owner_scope: reservation_target}, accepted_store, (reservation_policy,)
        )
        cleanup.callback(reservation_service.close)
        reservation_service.maintain_registrations((reservation_registration,))
        assert reservation_service.ready is True

        loopback = _ExecutionGroupServiceLoopback(
            reservation_context, entry_context, query_context
        )
        loopback.reservations = reservation_service
        loopback.orders = owner_service
        borrowed_upstream = BorrowedServiceClient(loopback)
        coordinator_store = SqliteOrderGroupStore(tmp_path / "group-coordinator.sqlite3")
        cleanup.callback(coordinator_store.close)
        origin_run_bindings = GroupOriginRunBindingStore(
            tmp_path / "group-origin-runs.sqlite3"
        )
        cleanup.callback(origin_run_bindings.close)
        route = GroupAccountRoute(
            execution_scope=execution_scope,
            risk_scope_fingerprint=risk_account_scope_fingerprint(risk_scope.key),
            reservation_scope_ref=reservation_scope_ref,
            order_scope_ref=entry_scope_ref,
            query_scope_ref=query_scope_ref,
            instruments={
                metadata.instrument: GroupInstrumentRule(
                    metadata=metadata,
                    base_units_per_order_unit=Decimal("1"),
                    allowed_order_types=frozenset({OrderType.LIMIT}),
                )
            },
        )
        budget_policy, budget_quote_builder = _budget_binding(
            metadata, portfolio_ref=strategy_template.portfolio_ref
        )
        coordinator_target = GroupCoordinatorTarget(
            scope_ref=_COORDINATOR_SCOPE_REF,
            accounts={account_ref: route},
            max_group_lifetime_ns=300_000_000_000,
            portfolio_budget_policy=budget_policy,
            budget_quote_builder=budget_quote_builder,
        )
        coordinator_service = GroupCoordinatorService(
            coordinator_store,
            {strategy_template: coordinator_target},
            origin_run_bindings,
            borrowed_upstream,
            service_identity,
            instance_id=_COORDINATOR_INSTANCE,
        )
        cleanup.callback(coordinator_service.close)
        strategy_registration = _registration(
            now_ns,
            registration_id="group-strategy-registration",
            service_epoch="group-strategy-epoch",
            scope=strategy_scope,
        )
        coordinator_service.maintain_registrations((strategy_registration,))
        assert coordinator_service.ready is True

        def child_intent(leg_id: str, side: Side) -> OrderIntent:
            return OrderIntent.limit(
                intent_id=group_leg_intent_id(group_id, leg_id),
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

        request = {
            "group_id": group_id,
            "deadline_ns": deadline_ns,
            "first_leg_id": "leg-a",
            "legs": [
                {
                    "leg_id": "leg-a",
                    "account_ref": account_ref,
                    "intent": child_intent("leg-a", Side.BUY).to_payload(),
                },
                {
                    "leg_id": "leg-b",
                    "account_ref": account_ref,
                    "intent": child_intent("leg-b", Side.SELL).to_payload(),
                },
            ],
        }
        result = coordinator_service.handle(
            "groups.submit", context=strategy_registration.context, arguments=request
        )
        assert result["ok"] is True, result
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])
        assert snapshot.state is GroupOrderState.PAIRED, (
            snapshot,
            tuple((operation, reply.get("code")) for _, operation, reply in loopback.replies),
            provider.snapshot(),
        )
        assert snapshot.error_code is None
        assert snapshot.residual_base_units == 0
        assert len(snapshot.reservations) == 1
        assert snapshot.reservations[0].phase is GroupReservationPhase.ACTIVE
        assert len(snapshot.legs) == 2
        assert all(item.phase.value == "OBSERVED" for item in snapshot.legs)
        assert all(item.child_status is not None for item in snapshot.legs)
        assert all(item.child_status.state is ExecutionState.FILLED for item in snapshot.legs)
        assert all(item.claim_receipt is not None for item in snapshot.legs)
        assert all(item.resolution_receipt is not None for item in snapshot.legs)
        assert all(
            item.resolution_receipt.resolution_kind == "TERMINAL_EXPOSURE_RESOLVED"
            and item.resolution_receipt.trade_count == 1
            and item.resolution_receipt.dispatch_latch_active is False
            for item in snapshot.legs
        )

        writes, counters = provider.snapshot()
        expected_intent_ids = {group_leg_intent_id(group_id, "leg-a"), group_leg_intent_id(group_id, "leg-b")}
        assert len(writes) == 2
        assert len(set(writes)) == 2
        assert set(counters) == {"order_queries", "fill_queries"}
        assert counters == {"order_queries": 2, "fill_queries": 2}
        assert len(expected_intent_ids) == 2

        for leg in snapshot.legs:
            intent = leg.submitted_intent
            assert intent is not None
            record = execution_store.get(intent.intent_id, scope=execution_scope)
            assert record is not None
            assert record.state is ExecutionState.FILLED
            assert record.dispatch_attempts == 1
            assert record.group_binding is not None
            assert record.group_binding.group_id == group_id
            assert record.group_binding.leg_id == leg.leg_id
            assert record.payload_sha256 == intent.fingerprint
        assert {leg.submitted_intent.intent_id for leg in snapshot.legs} == expected_intent_ids

        vector = risk_admission.read_group_vector_snapshot(risk_scope, group_id)
        assert vector.group_id == group_id
        assert len(vector.legs) == 2
        assert all(leg.claimed and leg.resolved for leg in vector.legs)
        assert authority.pending_holds() == ()

        queried = coordinator_service.handle(
            "groups.query",
            context=strategy_registration.context,
            arguments={"group_id": group_id},
        )
        assert queried["ok"] is True
        assert GroupOrderSnapshot.from_payload(queried["result"]["snapshot"]) == snapshot
        writes_after_query, counters_after_query = provider.snapshot()
        assert writes_after_query == writes
        assert counters_after_query == counters
        assert [call[1] for call in loopback.calls].count("orders.submit_group_leg") == 2
        assert all(call[2] == account_ref and call[3] in {
            reservation_scope_ref, entry_scope_ref, query_scope_ref
        } for call in loopback.calls)
        assert all(call[0] in {
            "upstream.account_reservations", "upstream.orders", "upstream.order_query"
        } for call in loopback.calls)
