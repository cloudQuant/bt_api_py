"""In-process tests of durable P5A sequencing against typed fake owner ports."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_execution import (
    ExecutionScope,
    ExecutionState,
    GroupAccountReservationSnapshot,
    GroupBudgetLegQuote,
    GroupBudgetQuote,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    GroupOrderCoordinator,
    GroupOrderIntent,
    GroupOrderLegPlan,
    GroupOrderState,
    GroupReservationDeniedError,
    GroupReservationPhase,
    GroupRiskLegSnapshot,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    PositionEffect,
    Side,
    SqliteOrderGroupStore,
    TimeInForce,
    group_leg_intent_id,
    payload_sha256,
)
from bt_api_execution.errors import ContractValidationError

_NOW = 1_800_000_000_000_000_000
_DEADLINE = _NOW + 10_000_000_000


class _Clock:
    def __init__(self, now_ns: int) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


class _BusinessOutcomeUnknownError(RuntimeError):
    code = "BUSINESS_OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class _Scope:
    key: str


class _Mapper:
    def __init__(self, scope: _Scope) -> None:
        self.scope = scope

    def map_group_intent(self, intent: OrderIntent) -> object:
        profile = payload_sha256(
            {"instrument": intent.instrument, "side": intent.side.value, "effect": "OPEN"}
        )
        return SimpleNamespace(
            scope=self.scope,
            intent_id=intent.intent_id,
            fingerprint=payload_sha256({"intent": intent.to_payload(), "risk": "mapped"}),
            exposure_profile_fingerprint=profile,
            quantity=intent.quantity,
            strategy_id=intent.scope.strategy_id,
            instrument=intent.instrument,
            action=SimpleNamespace(value="INCREASE"),
        )


class _ReservationPort:
    def __init__(self, scope: _Scope, mapper: _Mapper, *, deny: str | None = None) -> None:
        self.scope = scope
        self.mapper = mapper
        self.deny = deny
        self.groups: dict[str, object] = {}
        self.reserve_calls: list[tuple[str, tuple[str, ...]]] = []
        self.query_calls: list[str] = []
        self.release_calls: list[str] = []
        self.lose_release_response = False
        self.lose_reserve_response = False

    @property
    def risk_scope_fingerprint(self) -> str:
        return payload_sha256({"scope_key": self.scope.key})

    def query_group_vector(self, group_id: str) -> GroupAccountReservationSnapshot:
        self.query_calls.append(group_id)
        receipt = self.groups.get(group_id)
        if receipt is None:
            error = LookupError("missing")
            error.code = "GROUP_NOT_FOUND"
            raise error
        return self._receipt(group_id)

    def reserve_group_vector(
        self, group_id: str, members: tuple[tuple[str, OrderIntent], ...], *, deadline_ns: int
    ) -> GroupAccountReservationSnapshot:
        if self.deny is not None:
            raise GroupReservationDeniedError(self.deny)
        self.reserve_calls.append((group_id, tuple(leg_id for leg_id, _ in members)))
        legs = {}
        for leg_id, intent in sorted(members):
            mapped = self.mapper.map_group_intent(intent)
            legs[leg_id] = {
                "permit_id": f"permit-{group_id}-{leg_id}",
                "intent_id": intent.intent_id,
                "reserved_intent_hash": mapped.fingerprint,
                "exposure_profile_fingerprint": mapped.exposure_profile_fingerprint,
                "actual_intent_hash": None,
                "state": "ACTIVE",
                "claimed": False,
                "resolved": False,
                "expires_at_ns": deadline_ns - 1,
            }
        self.groups[group_id] = SimpleNamespace(
            scope_fingerprint=self.risk_scope_fingerprint,
            group_id=group_id,
            account_ref=members[0][1].scope.account_ref,
            group_fingerprint=payload_sha256({"scope": self.scope.key, "group": group_id}),
            policy_fingerprint=payload_sha256({"policy": "unit"}),
            deadline_ns=deadline_ns,
            legs=legs,
        )
        if self.lose_reserve_response:
            self.lose_reserve_response = False
            raise _BusinessOutcomeUnknownError("reservation committed but reply was lost")
        return self._receipt(group_id)

    def release_group_unclaimed(
        self, group_id: str, reason: str
    ) -> GroupAccountReservationSnapshot:
        assert reason in {"group_rejected_before_dispatch", "group_first_leg_no_fill"}
        self.release_calls.append(group_id)
        receipt = self.groups[group_id]
        for leg in receipt.legs.values():
            if not leg["claimed"]:
                leg["state"] = "RELEASED"
        if self.lose_release_response:
            self.lose_release_response = False
            raise TimeoutError("release committed but reply was lost")
        return self._receipt(group_id)

    def set_claim(
        self, group_id: str, leg_id: str, intent: OrderIntent, claim: GroupLegClaimReceipt
    ) -> None:
        receipt = self.groups[group_id]
        leg = receipt.legs[leg_id]
        mapped = self.mapper.map_group_intent(intent)
        assert mapped.fingerprint == claim.actual_child_intent_hash
        leg["actual_intent_hash"] = mapped.fingerprint
        leg["claimed"] = True
        leg["resolved"] = False
        leg["state"] = "SETTLED"

    def resolve_claim(self, claim: GroupLegClaimReceipt) -> None:
        self.groups[claim.permit.group_id].legs[claim.permit.leg_id]["resolved"] = True

    def _receipt(self, group_id: str) -> GroupAccountReservationSnapshot:
        receipt = self.groups[group_id]
        return GroupAccountReservationSnapshot(
            group_id=receipt.group_id,
            account_ref=receipt.account_ref,
            scope_fingerprint=receipt.scope_fingerprint,
            phase=GroupReservationPhase.ACTIVE,
            group_fingerprint=receipt.group_fingerprint,
            policy_fingerprint=receipt.policy_fingerprint,
            deadline_ns=receipt.deadline_ns,
            legs=tuple(
                GroupRiskLegSnapshot(leg_id=leg_id, **value)
                for leg_id, value in sorted(receipt.legs.items())
            ),
        )


class _OrdersPort:
    def __init__(self, reservations: _ReservationPort) -> None:
        self.reservations = reservations
        self.submissions: list[OrderIntent] = []
        self.statuses: dict[str, GroupChildOrderStatus] = {}
        self.query_ids: list[str] = []
        self.terminal_on_submit: dict[str, tuple[ExecutionState, Decimal, int, int]] = {}
        self.lose_first_response = False
        self.lose_response_leg_id: str | None = None
        self.fail_resolution = False
        self.claims: dict[str, GroupLegClaimReceipt] = {}
        self.resolutions: dict[str, GroupDispatchResolutionReceipt] = {}
        self.resolution_calls: list[str] = []

    def query(self, intent_id: str) -> GroupLegOrderReceipt | None:
        self.query_ids.append(intent_id)
        status = self.statuses.get(intent_id)
        if status is None:
            return None
        return GroupLegOrderReceipt(
            status=status,
            claim=self.claims.get(intent_id),
            resolution=self.resolutions.get(intent_id),
        )

    def submit_group_leg(
        self, intent: OrderIntent, permit: GroupLegPermitReference
    ) -> GroupLegOrderReceipt:
        self.submissions.append(intent)
        permit.validate_order(intent)
        assert permit.group_id == "group-p5a"
        if permit.leg_id == "leg-hedge":
            first_id = group_leg_intent_id(permit.group_id, "leg-first")
            if first_id in self.statuses:
                first_status = self.statuses[first_id]
                assert first_status.state in {ExecutionState.FILLED, ExecutionState.CANCELLED}
                assert first_status.filled_quantity > 0
                assert self.reservations.groups[permit.group_id].legs["leg-first"]["resolved"]
        state, filled, trade_count, order_revision = self.terminal_on_submit[intent.intent_id]
        mapped = self.reservations.mapper.map_group_intent(intent)
        claim = GroupLegClaimReceipt(
            permit=permit,
            actual_child_intent_hash=mapped.fingerprint,
            claim_digest=payload_sha256({"claim": permit.permit_id, "order": intent.fingerprint}),
            actual_quantity=intent.quantity,
            actual_notional=Decimal("1"),
        )
        self.claims[intent.intent_id] = claim
        self.reservations.set_claim(permit.group_id, permit.leg_id, intent, claim)
        status = GroupChildOrderStatus(
            intent_id=intent.intent_id,
            state=state,
            filled_quantity=filled,
            provider_order_id=f"order-{permit.leg_id}",
            order_revision=order_revision,
            trade_count=trade_count,
        )
        self.statuses[intent.intent_id] = status
        if (self.lose_first_response and permit.leg_id == "leg-first") or (
            self.lose_response_leg_id == permit.leg_id
        ):
            status = GroupChildOrderStatus(
                intent_id=intent.intent_id,
                state=ExecutionState.UNKNOWN,
                filled_quantity=Decimal("0"),
            )
            self.statuses[intent.intent_id] = status
            raise TimeoutError("lost response")
        return GroupLegOrderReceipt(status=status, claim=claim)

    def resolve_group_leg(
        self,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        claim: GroupLegClaimReceipt,
        status: GroupChildOrderStatus,
    ) -> GroupDispatchResolutionReceipt:
        assert claim.permit == permit
        assert status.intent_id == intent.intent_id
        assert status.state in {
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.BLOCKED,
            ExecutionState.FILLED,
        }
        if self.fail_resolution:
            raise TimeoutError("risk exposure resolution unavailable")
        self.resolution_calls.append(intent.intent_id)
        no_fill = status.filled_quantity == 0
        receipt = GroupDispatchResolutionReceipt(
            claim=claim,
            resolution_kind=(
                "TERMINAL_NO_FILL_RESOLVED" if no_fill else "TERMINAL_EXPOSURE_RESOLVED"
            ),
            proof_sha256=payload_sha256(
                {"proof": claim.claim_digest, "fill": str(status.filled_quantity)}
            ),
            final_state=status.state,
            requested_quantity=claim.actual_quantity,
            filled_quantity=status.filled_quantity,
            trade_count=status.trade_count or 0,
            order_revision=None if no_fill else status.order_revision,
            journal_revision=status.order_revision or 1,
            exposure_proof_sha256=None
            if no_fill
            else payload_sha256({"observation": claim.claim_digest}),
            exposure_reservation_id=None if no_fill else f"exposure-{claim.permit.leg_id}",
            exposure_reservation_sha256=None
            if no_fill
            else payload_sha256({"exposure": claim.claim_digest}),
            dispatch_latch_active=False,
            has_other_active_freezes=False,
        )
        self.resolutions[intent.intent_id] = receipt
        self.reservations.resolve_claim(claim)
        return receipt


def _make_request(*, accounts: tuple[str, str] = ("account-a", "account-a")) -> GroupOrderIntent:
    scopes = tuple(ExecutionScope("paper", "demo", account, "strategy-p5a") for account in accounts)
    first = OrderIntent(
        intent_id=group_leg_intent_id("group-p5a", "leg-first"),
        scope=scopes[0],
        signal_id="signal-p5a",
        instrument="FOO-USD",
        side=Side.BUY,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=Decimal("2"),
        price=Decimal("10"),
        metadata_version="fixture-metadata-v1",
        time_in_force=TimeInForce.IOC,
        deadline_ns=_DEADLINE,
        tags={"instrument_metadata_digest": payload_sha256({"instrument": "FOO-USD"})},
    )
    second = OrderIntent(
        intent_id=group_leg_intent_id("group-p5a", "leg-hedge"),
        scope=scopes[1],
        signal_id="signal-p5a",
        instrument="BAR-USD",
        side=Side.SELL,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1.5"),
        price=Decimal("20"),
        metadata_version="fixture-metadata-v1",
        time_in_force=TimeInForce.IOC,
        deadline_ns=_DEADLINE,
        tags={"instrument_metadata_digest": payload_sha256({"instrument": "BAR-USD"})},
    )
    return GroupOrderIntent(
        group_id="group-p5a",
        portfolio_ref="portfolio-p5a",
        strategy_id="strategy-p5a",
        coordinator_principal_ref="coordinator-p5a",
        received_at_ns=_NOW,
        deadline_ns=_DEADLINE,
        first_leg_id="leg-first",
        legs=(
            GroupOrderLegPlan(
                "leg-first", accounts[0], first, Decimal("1"), Decimal("0.25"), _DEADLINE
            ),
            GroupOrderLegPlan(
                "leg-hedge", accounts[1], second, Decimal("2"), Decimal("0.25"), _DEADLINE
            ),
        ),
    )


def _coordinator(
    tmp_path,
    request: GroupOrderIntent,
    ports=None,
    *,
    path="groups.sqlite",
    clock_ns=None,
    budget_policy=None,
):
    reservations = {}
    orders = {}
    account_refs = request.account_refs
    for account_ref in account_refs:
        scope = _Scope(
            f'{{"account_id":"risk-{account_ref}","environment":"demo","provider":"paper"}}'
        )
        mapper = _Mapper(scope)
        reservation = _ReservationPort(scope, mapper)
        reservations[account_ref] = reservation
        orders[account_ref] = _OrdersPort(reservation)
    if ports is not None:
        reservations, orders = ports
    store = SqliteOrderGroupStore(tmp_path / path)
    policy = budget_policy or PortfolioGroupBudgetPolicy(
        portfolio_ref=request.portfolio_ref,
        policy_id="test-portfolio-budget",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=Decimal("100000"),
        max_active_gross_notional=Decimal("100000"),
        max_active_groups=10,
    )
    coordinator = GroupOrderCoordinator(
        store,
        reservations=reservations,
        orders=orders,
        portfolio_budget_policies={request.portfolio_ref: policy},
        budget_quote_builder=_test_budget_quote,
        clock_ns=(lambda: _NOW + 1) if clock_ns is None else clock_ns,
    )
    return coordinator, store, reservations, orders


def _test_budget_quote(request, policy, *, now_ns):
    legs = []
    for plan in request.legs:
        intent = plan.maximum_intent
        digest = intent.tags["instrument_metadata_digest"]
        # Component tests bind a deterministic synthetic quote. Production
        # composition uses build_instrument_risk_group_budget_quote.
        gross = intent.quantity * intent.price + Decimal("0.5")
        legs.append(
            GroupBudgetLegQuote(
                leg_id=plan.leg_id,
                account_ref=plan.account_ref,
                intent_fingerprint=intent.fingerprint,
                metadata_digest=digest,
                assessment_fingerprint=payload_sha256(
                    {"synthetic-assessment": intent.fingerprint, "gross": format(gross, "f")}
                ),
                valuation_unit="USD",
                gross_notional=gross,
                assessed_at_ns=now_ns,
                valid_until_ns=plan.metadata_valid_until_ns,
            )
        )
    total = sum((item.gross_notional for item in legs), Decimal("0"))
    return GroupBudgetQuote(
        policy_fingerprint=policy.fingerprint,
        valuation_unit="USD",
        gross_notional=total,
        legs=tuple(legs),
    )


def _install_terminal_responses(request, reservations, orders, *, lose_first=False):
    first, second = request.ordered_legs
    orders[first.account_ref].terminal_on_submit[first.maximum_intent.intent_id] = (
        ExecutionState.CANCELLED,
        Decimal("1.5"),
        1,
        3,
    )
    orders[second.account_ref].terminal_on_submit[second.maximum_intent.intent_id] = (
        ExecutionState.FILLED,
        Decimal("0.75"),
        1,
        5,
    )
    orders[first.account_ref].lose_first_response = lose_first


def test_group_coordinator_reserves_one_vector_sizes_hedge_and_pairs(tmp_path) -> None:
    request = _make_request()
    coordinator, store, reservations, orders = _coordinator(tmp_path, request)
    _install_terminal_responses(request, reservations, orders)

    accepted = coordinator.accept(request)
    assert accepted.state is GroupOrderState.PREPARING
    revision = accepted.revision
    assert coordinator.query(request.portfolio_ref, request.group_id).revision == revision
    assert all(not port.reserve_calls for port in reservations.values())
    assert all(not port.submissions for port in orders.values())
    assert all(not port.query_calls for port in reservations.values())

    paired = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert paired.state is GroupOrderState.PAIRED
    assert [item.quantity for item in orders["account-a"].submissions] == [
        Decimal("2"),
        Decimal("0.75"),
    ]
    assert reservations["account-a"].reserve_calls == [
        (request.group_id, ("leg-first", "leg-hedge"))
    ]
    assert paired.residual_base_units == Decimal("0")
    assert paired.legs[0].child_status.filled_quantity == Decimal("1.5")
    assert paired.legs[1].submitted_intent.quantity == Decimal("0.75")
    assert paired.budget_reservation.state.value == "HELD"
    assert orders["account-a"].resolution_calls == [
        item.intent_id for item in orders["account-a"].submissions
    ]
    assert store.events(request.portfolio_ref, request.group_id)
    store.close()


def test_group_unknown_restart_queries_same_order_id_without_resubmit(tmp_path) -> None:
    request = _make_request()
    coordinator, store, reservations, orders = _coordinator(tmp_path, request)
    _install_terminal_responses(request, reservations, orders, lose_first=True)
    coordinator.accept(request)
    blocked = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert blocked.state is GroupOrderState.RECOVERY_REQUIRED
    assert blocked.budget_reservation.state.value == "HELD"
    assert len(orders["account-a"].submissions) == 1
    first_id = request.ordered_legs[0].maximum_intent.intent_id
    orders["account-a"].statuses[first_id] = GroupChildOrderStatus(
        intent_id=first_id,
        state=ExecutionState.CANCELLED,
        filled_quantity=Decimal("1.5"),
        provider_order_id="order-leg-first",
        order_revision=3,
        trade_count=1,
    )
    store.close()

    restarted_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite")
    persisted = restarted_store.get(request.portfolio_ref, request.group_id)
    assert persisted.budget_reservation.state.value == "HELD"
    restarted = GroupOrderCoordinator(
        restarted_store,
        reservations=reservations,
        orders=orders,
        clock_ns=lambda: _NOW + 2,
    )
    result = restarted.advance_group(request.portfolio_ref, request.group_id)
    assert result.state is GroupOrderState.PAIRED
    assert result.budget_reservation.state.value == "HELD"
    assert orders["account-a"].submissions.count(request.ordered_legs[0].maximum_intent) == 1
    assert first_id in orders["account-a"].query_ids
    assert len(orders["account-a"].submissions) == 2
    assert len(orders["account-a"].statuses) == 2
    restarted_store.close()


def test_lost_reserve_reply_with_unknown_code_recovers_active_vector_by_query(
    tmp_path,
) -> None:
    request = _make_request()
    coordinator, store, reservations, orders = _coordinator(tmp_path, request)
    port = reservations["account-a"]
    port.lose_reserve_response = True
    coordinator.accept(request)

    interrupted = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert interrupted.state is GroupOrderState.RECOVERY_REQUIRED
    assert interrupted.reservations[0].phase is GroupReservationPhase.UNKNOWN
    assert port.groups[request.group_id].legs
    assert all(
        leg["state"] == "ACTIVE" and not leg["claimed"]
        for leg in port.groups[request.group_id].legs.values()
    )
    # The initial GROUP_NOT_FOUND query is still classified as absence, after
    # which the remote reservation commits and its unknown-code reply is lost.
    assert port.query_calls == [request.group_id]
    assert len(port.reserve_calls) == 1
    assert not orders["account-a"].submissions
    assert not port.release_calls
    store.close()

    reopened_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite")
    recovered_coordinator = GroupOrderCoordinator(
        reopened_store,
        reservations=reservations,
        orders=orders,
        clock_ns=lambda: _NOW + 2,
    )
    recovered = recovered_coordinator._advance_reservations(
        recovered_coordinator._required(request.portfolio_ref, request.group_id)
    )

    assert recovered.state is GroupOrderState.RECOVERY_REQUIRED
    assert recovered.reservations[0].phase is GroupReservationPhase.ACTIVE
    assert port.query_calls == [request.group_id, request.group_id]
    assert len(port.reserve_calls) == 1
    assert not orders["account-a"].submissions
    assert not port.release_calls
    reopened_store.close()


def test_expired_unclaimed_account_vectors_release_before_dispatch_and_reopen(
    tmp_path,
) -> None:
    request = _make_request(accounts=("account-a", "account-b"))
    clock = _Clock(_NOW + 1)
    coordinator, store, reservations, orders = _coordinator(tmp_path, request, clock_ns=clock)
    coordinator.accept(request)

    prepared = coordinator._advance_reservations(
        coordinator._required(request.portfolio_ref, request.group_id)
    )
    assert all(item.phase is GroupReservationPhase.ACTIVE for item in prepared.reservations)
    assert all(len(port.reserve_calls) == 1 for port in reservations.values())
    assert all(not port.submissions for port in orders.values())
    assert all(not port.release_calls for port in reservations.values())

    # The second account commits its release, but its RPC reply is lost. The
    # coordinator must retain the portfolio hold until the original Risk IDs
    # are queried and both terminal receipts are verified after restart.
    reservations["account-b"].lose_release_response = True
    clock.now_ns = request.deadline_ns + 1
    interrupted = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert interrupted.state is GroupOrderState.RECOVERY_REQUIRED
    assert interrupted.budget_reservation.state.value == "RESERVED"
    assert all(leg.submitted_intent is None for leg in interrupted.legs)
    assert all(len(port.reserve_calls) == 1 for port in reservations.values())
    assert all(port.release_calls == [request.group_id] for port in reservations.values())
    assert reservations["account-a"].groups[request.group_id].legs
    assert all(
        leg["state"] == "RELEASED" and not leg["claimed"] and not leg["resolved"]
        for port in reservations.values()
        for leg in port.groups[request.group_id].legs.values()
    )
    assert all(not port.submissions and not port.query_ids for port in orders.values())
    store.close()

    reopened_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite")
    recovered_coordinator = GroupOrderCoordinator(
        reopened_store,
        reservations=reservations,
        orders=orders,
        portfolio_budget_policies={request.portfolio_ref: interrupted.budget_reservation.policy},
        budget_quote_builder=_test_budget_quote,
        clock_ns=clock,
    )
    recovered = recovered_coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert recovered.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
    assert recovered.error_code == "reservation_not_active"
    assert recovered.budget_reservation.state.value == "RELEASED"
    assert all(item.phase is GroupReservationPhase.RELEASED for item in recovered.reservations)
    assert all(len(port.reserve_calls) == 1 for port in reservations.values())
    assert all(port.release_calls == [request.group_id] for port in reservations.values())
    assert all(not port.submissions and not port.query_ids for port in orders.values())
    reopened_store.close()


def test_expired_vector_does_not_release_any_account_if_another_has_a_claim(
    tmp_path,
) -> None:
    request = _make_request(accounts=("account-a", "account-b"))
    clock = _Clock(_NOW + 1)
    coordinator, store, reservations, orders = _coordinator(tmp_path, request, clock_ns=clock)
    coordinator.accept(request)
    prepared = coordinator._advance_reservations(
        coordinator._required(request.portfolio_ref, request.group_id)
    )
    assert all(item.phase is GroupReservationPhase.ACTIVE for item in prepared.reservations)

    # A claim observed on the second account blocks compensation of the first
    # account, even though the coordinator has no submitted child intent.
    claimed = reservations["account-b"].groups[request.group_id].legs["leg-hedge"]
    claimed["actual_intent_hash"] = payload_sha256({"exact-child": "persisted"})
    claimed["claimed"] = True
    claimed["state"] = "SETTLED"
    clock.now_ns = request.deadline_ns + 1

    blocked = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert blocked.state is GroupOrderState.RECOVERY_REQUIRED
    assert blocked.error_code == "reservation_claim_before_dispatch"
    assert blocked.budget_reservation.state.value == "RESERVED"
    assert all(leg.submitted_intent is None for leg in blocked.legs)
    assert all(not port.release_calls for port in reservations.values())
    assert all(not port.submissions and not port.query_ids for port in orders.values())
    store.close()


def test_trusted_portfolio_budget_denial_has_no_account_or_order_effects(tmp_path) -> None:
    request = _make_request(accounts=("account-a", "account-b"))
    policy = PortfolioGroupBudgetPolicy(
        portfolio_ref=request.portfolio_ref,
        policy_id="tight-portfolio-budget",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=Decimal("50"),
        max_active_gross_notional=Decimal("100"),
        max_active_groups=2,
    )
    coordinator, store, reservations, orders = _coordinator(tmp_path, request, budget_policy=policy)

    denied = coordinator.accept(request)

    assert denied.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
    assert denied.budget_reservation.state.value == "DENIED"
    assert denied.error_code == "group_budget_limit_exceeded"
    assert all(not port.query_calls and not port.reserve_calls for port in reservations.values())
    assert all(not port.submissions and not port.query_ids for port in orders.values())
    store.close()


def test_expired_deadline_still_queries_unknown_hedge_by_original_id(tmp_path) -> None:
    request = _make_request()
    clock = _Clock(_NOW + 1)
    coordinator, store, reservations, orders = _coordinator(tmp_path, request, clock_ns=clock)
    _install_terminal_responses(request, reservations, orders)
    orders["account-a"].lose_response_leg_id = "leg-hedge"
    coordinator.accept(request)

    interrupted = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert interrupted.state is GroupOrderState.RECOVERY_REQUIRED
    hedge = request.ordered_legs[1]
    assert len(orders["account-a"].submissions) == 2
    assert (
        orders["account-a"].statuses[hedge.maximum_intent.intent_id].state is ExecutionState.UNKNOWN
    )

    # Reconcile the already-submitted hedge after the absolute deadline. The
    # query is allowed, while _submit_leg remains the only path to a new send.
    clock.now_ns = request.deadline_ns + 1
    orders["account-a"].statuses[hedge.maximum_intent.intent_id] = GroupChildOrderStatus(
        intent_id=hedge.maximum_intent.intent_id,
        state=ExecutionState.FILLED,
        filled_quantity=Decimal("0.75"),
        provider_order_id="order-leg-hedge",
        order_revision=5,
        trade_count=1,
    )

    recovered = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert recovered.state is GroupOrderState.PAIRED
    assert hedge.maximum_intent.intent_id in orders["account-a"].query_ids
    assert (
        sum(
            intent.intent_id == hedge.maximum_intent.intent_id
            for intent in orders["account-a"].submissions
        )
        == 1
    )
    store.close()


def test_group_unknown_missing_local_order_is_never_resubmitted(tmp_path) -> None:
    request = _make_request()
    coordinator, store, reservations, orders = _coordinator(tmp_path, request)
    _install_terminal_responses(request, reservations, orders, lose_first=True)
    coordinator.accept(request)
    first = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert first.state is GroupOrderState.RECOVERY_REQUIRED
    intent_id = request.ordered_legs[0].maximum_intent.intent_id
    del orders["account-a"].statuses[intent_id]

    result = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert result.state is GroupOrderState.RECOVERY_REQUIRED
    assert len(orders["account-a"].submissions) == 1
    assert result.error_code == "first_leg_still_unknown"
    store.close()


def test_group_resolution_unknown_never_submits_hedge(tmp_path) -> None:
    request = _make_request()
    coordinator, store, reservations, orders = _coordinator(tmp_path, request)
    _install_terminal_responses(request, reservations, orders)
    orders["account-a"].fail_resolution = True
    coordinator.accept(request)

    blocked = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert blocked.state is GroupOrderState.RECOVERY_REQUIRED
    assert blocked.error_code == "group_risk_resolution_unknown"
    assert len(orders["account-a"].submissions) == 1
    assert orders["account-a"].resolution_calls == []
    assert reservations["account-a"].groups[request.group_id].legs["leg-first"]["claimed"]
    assert not reservations["account-a"].groups[request.group_id].legs["leg-first"]["resolved"]
    store.close()


def test_release_commit_with_lost_reply_recovers_from_durable_receipt(tmp_path) -> None:
    request = _make_request()
    coordinator, store, reservations, orders = _coordinator(tmp_path, request)
    first, second = request.ordered_legs
    orders["account-a"].terminal_on_submit[first.maximum_intent.intent_id] = (
        ExecutionState.CANCELLED,
        Decimal("0"),
        0,
        None,
    )
    orders["account-a"].terminal_on_submit[second.maximum_intent.intent_id] = (
        ExecutionState.FILLED,
        Decimal("0.75"),
        1,
        5,
    )
    reservations["account-a"].lose_release_response = True
    coordinator.accept(request)

    interrupted = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert interrupted.state is GroupOrderState.RECOVERY_REQUIRED
    assert interrupted.error_code == "no_fill_risk_release_unresolved"
    assert len(orders["account-a"].submissions) == 1
    assert (
        reservations["account-a"].groups[request.group_id].legs["leg-hedge"]["state"] == "RELEASED"
    )

    recovered = coordinator.advance_group(request.portfolio_ref, request.group_id)

    assert recovered.state is GroupOrderState.CLOSED
    assert recovered.error_code is None
    assert len(orders["account-a"].submissions) == 1
    assert orders["account-a"].query_ids.count(first.maximum_intent.intent_id) >= 2
    store.close()


def test_group_cross_account_denial_releases_prior_vector_before_any_order(tmp_path) -> None:
    request = _make_request(accounts=("account-a", "account-b"))
    reservations = {}
    orders = {}
    for account_ref in request.account_refs:
        scope = _Scope(
            f'{{"account_id":"risk-{account_ref}","environment":"demo","provider":"paper"}}'
        )
        mapper = _Mapper(scope)
        reservation = _ReservationPort(
            scope,
            mapper,
            deny="LIVE_GROSS_LIMIT_EXCEEDED" if account_ref == "account-b" else None,
        )
        reservations[account_ref] = reservation
        orders[account_ref] = _OrdersPort(reservation)
    coordinator, store, *_ = _coordinator(
        tmp_path,
        request,
        ports=(reservations, orders),
    )
    coordinator.accept(request)
    result = coordinator.advance_group(request.portfolio_ref, request.group_id)
    assert result.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
    assert reservations["account-a"].release_calls == [request.group_id]
    assert not orders["account-a"].submissions and not orders["account-b"].submissions
    store.close()


def test_group_request_rejects_same_side_legs(tmp_path) -> None:
    request = _make_request()
    second = request.legs[1]
    invalid_second = GroupOrderLegPlan(
        second.leg_id,
        second.account_ref,
        OrderIntent(
            intent_id=second.maximum_intent.intent_id,
            scope=second.maximum_intent.scope,
            signal_id=second.maximum_intent.signal_id,
            instrument=second.maximum_intent.instrument,
            side=Side.BUY,
            position_effect=second.maximum_intent.position_effect,
            order_type=second.maximum_intent.order_type,
            quantity=second.maximum_intent.quantity,
            price=second.maximum_intent.price,
            time_in_force=second.maximum_intent.time_in_force,
            deadline_ns=second.maximum_intent.deadline_ns,
        ),
        second.base_units_per_order_unit,
        second.quantity_step,
        second.metadata_valid_until_ns,
    )
    with pytest.raises(ContractValidationError, match="opposing sides"):
        GroupOrderIntent(
            group_id=request.group_id,
            portfolio_ref=request.portfolio_ref,
            strategy_id=request.strategy_id,
            coordinator_principal_ref=request.coordinator_principal_ref,
            received_at_ns=request.received_at_ns,
            deadline_ns=request.deadline_ns,
            first_leg_id=request.first_leg_id,
            legs=(request.legs[0], invalid_second),
        )
