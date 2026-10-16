"""ServiceHost adapters for the durable two-leg Execution group workflow.

The coordinator accepts only authenticated strategy registrations and derives
run authority from the Registry snapshot.  Account-owner RPCs are routed over
the Host's borrowed upstream client; each account owner independently binds a
permit to its accepted reservation, current writer lease, and durable group
proof source.  Query operations never advance or dispatch a group.
"""

from __future__ import annotations

import contextvars
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    ContractValidationError,
    ExecutionRecord,
    ExecutionScope,
    GroupAccountReservationSnapshot,
    GroupBudgetQuote,
    GroupBudgetQuoteBuilder,
    GroupBudgetUnsupportedOrderError,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    GroupOrderCoordinator,
    GroupOrderIntent,
    GroupOrderLegPlan,
    GroupPermitBinding,
    GroupReservationDeniedError,
    GroupRiskLegSnapshot,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    PositionEffect,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    TimeInForce,
    group_leg_intent_id,
    order_intent_from_payload,
)
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    InstrumentRiskMetadata,
    RiskDeniedError,
)
from bt_api_risk.service_component import GroupPolicyError

from .service_components import BorrowedServiceClient
from .service_execution_group_authority import ExecutionGroupAuthorityGuard
from .service_execution_group_client import (
    AcceptedGroupBinding,
    AcceptedGroupBindingError,
    AcceptedGroupBindingStore,
    AcceptedGroupConflictError,
    GroupOriginRunBinding,
    GroupOriginRunBindingStore,
)
from .service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)

GROUP_COORDINATOR_OPERATIONS = frozenset({"groups.submit", "groups.query"})
GROUP_ACCOUNT_OWNER_OPERATIONS = frozenset(
    {"orders.submit_group_leg", "orders.query_group_leg", "orders.resolve_group_leg"}
)

GROUP_COORDINATOR_PORT = "service.order_group_entry"
GROUP_ORDER_ENTRY_PORT = "service.order_entry"
GROUP_ORDER_QUERY_PORT = "service.order_query"
_RESERVATION_MOUNT = "upstream.account_reservations"
_ORDERS_MOUNT = "upstream.orders"
_ORDER_QUERY_MOUNT = "upstream.order_query"
_MAX_REPLY_BYTES = 1_000_000
_MAX_RPC_JSON_DEPTH = 64
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_LEASE_TTL_NS = 5_000_000_000


class ServiceExecutionGroupError(RuntimeError):
    """Finite, non-sensitive failure from a group service adapter."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _REFERENCE.fullmatch(code) is None:
            raise ValueError("invalid group service error")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GroupInstrumentRule:
    """Operator-supplied sizing and instrument metadata for one account."""

    metadata: InstrumentRiskMetadata
    base_units_per_order_unit: Decimal
    allowed_order_types: frozenset[OrderType]

    def __post_init__(self) -> None:
        if type(self.metadata) is not InstrumentRiskMetadata:
            raise ServiceExecutionGroupError("GROUP_INSTRUMENT_METADATA_INVALID")
        if (
            type(self.base_units_per_order_unit) is not Decimal
            or not self.base_units_per_order_unit.is_finite()
            or self.base_units_per_order_unit <= 0
        ):
            raise ServiceExecutionGroupError("GROUP_INSTRUMENT_RULE_INVALID")
        if type(self.allowed_order_types) is not frozenset or self.allowed_order_types != frozenset(
            {OrderType.LIMIT}
        ):
            raise ServiceExecutionGroupError("GROUP_INSTRUMENT_RULE_INVALID")


@dataclass(frozen=True, slots=True)
class GroupAccountRoute:
    """Trusted fixed downstream route and instrument rules for one account."""

    execution_scope: ExecutionScope
    risk_scope_fingerprint: str
    reservation_scope_ref: str
    order_scope_ref: str
    query_scope_ref: str
    instruments: Mapping[str, GroupInstrumentRule]

    def __post_init__(self) -> None:
        if type(self.execution_scope) is not ExecutionScope:
            raise ServiceExecutionGroupError("GROUP_ROUTE_INVALID")
        if type(self.risk_scope_fingerprint) is not str or not _SHA256.fullmatch(
            self.risk_scope_fingerprint
        ):
            raise ServiceExecutionGroupError("GROUP_ROUTE_INVALID")
        if not all(
            type(value) is str and _REFERENCE.fullmatch(value)
            for value in (
                self.reservation_scope_ref,
                self.order_scope_ref,
                self.query_scope_ref,
            )
        ):
            raise ServiceExecutionGroupError("GROUP_ROUTE_INVALID")
        if not isinstance(self.instruments, Mapping) or not self.instruments:
            raise ServiceExecutionGroupError("GROUP_ROUTE_INVALID")
        copied = dict(self.instruments)
        if any(
            type(instrument) is not str
            or type(rule) is not GroupInstrumentRule
            or rule.metadata.instrument != instrument
            for instrument, rule in copied.items()
        ):
            raise ServiceExecutionGroupError("GROUP_ROUTE_INVALID")
        object.__setattr__(self, "instruments", MappingProxyType(copied))

    @property
    def account_ref(self) -> str:
        return self.execution_scope.account_ref


@dataclass(frozen=True, slots=True)
class GroupCoordinatorTarget:
    """Trusted account routes for one stable strategy registration scope."""

    scope_ref: str
    accounts: Mapping[str, GroupAccountRoute]
    max_group_lifetime_ns: int
    portfolio_budget_policy: PortfolioGroupBudgetPolicy | None = None
    budget_quote_builder: GroupBudgetQuoteBuilder | None = None

    def __post_init__(self) -> None:
        if type(self.scope_ref) is not str or _REFERENCE.fullmatch(self.scope_ref) is None:
            raise ServiceExecutionGroupError("GROUP_TARGET_INVALID")
        if type(self.max_group_lifetime_ns) is not int or self.max_group_lifetime_ns <= 0:
            raise ServiceExecutionGroupError("GROUP_TARGET_INVALID")
        if not isinstance(self.accounts, Mapping) or not self.accounts:
            raise ServiceExecutionGroupError("GROUP_TARGET_INVALID")
        copied = dict(self.accounts)
        if any(
            type(account_ref) is not str
            or type(route) is not GroupAccountRoute
            or route.account_ref != account_ref
            for account_ref, route in copied.items()
        ):
            raise ServiceExecutionGroupError("GROUP_TARGET_INVALID")
        if (self.portfolio_budget_policy is None) != (self.budget_quote_builder is None):
            raise ServiceExecutionGroupError("GROUP_BUDGET_CONFIGURATION_INVALID")
        if self.portfolio_budget_policy is not None and (
            type(self.portfolio_budget_policy) is not PortfolioGroupBudgetPolicy
            or not callable(self.budget_quote_builder)
        ):
            raise ServiceExecutionGroupError("GROUP_BUDGET_CONFIGURATION_INVALID")
        object.__setattr__(self, "accounts", MappingProxyType(copied))


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, str]:
    """Validate the two fixed Execution group component roles."""

    if not isinstance(options, Mapping) or type(bind_ports) is not tuple:
        raise ServiceExecutionGroupError("INVALID_OPTIONS")
    role = options.get("role")
    if type(role) is str and role == "group_coordinator" and bind_ports == (
        GROUP_COORDINATOR_PORT,
    ):
        if set(options) != {"role", "portfolio_ref"}:
            raise ServiceExecutionGroupError("INVALID_OPTIONS")
        portfolio_ref = options.get("portfolio_ref")
        if type(portfolio_ref) is not str or _REFERENCE.fullmatch(portfolio_ref) is None:
            raise ServiceExecutionGroupError("INVALID_OPTIONS")
        return MappingProxyType({"role": role, "portfolio_ref": portfolio_ref})
    if (
        type(role) is str
        and role == "group_account_owner"
        and set(options) == {"role"}
        and type(bind_ports) is tuple
        and len(bind_ports) == 2
        and set(bind_ports) == {GROUP_ORDER_ENTRY_PORT, GROUP_ORDER_QUERY_PORT}
    ):
        return MappingProxyType({"role": role})
    raise ServiceExecutionGroupError("INVALID_OPTIONS")


@dataclass(frozen=True, slots=True)
class _CoordinatorCall:
    registration: ActiveRegistrationContext
    target_scope: WireScope
    target: GroupCoordinatorTarget
    binding: GroupOriginRunBinding


_CURRENT_COORDINATOR_CALL: contextvars.ContextVar[_CoordinatorCall | None] = contextvars.ContextVar(
    "bt_api_group_coordinator_call", default=None
)


class _ReservationsRpcPort:
    def __init__(
        self, account_ref: str, risk_scope_fingerprint: str, upstream: BorrowedServiceClient
    ):
        self._account_ref = account_ref
        self._risk_scope_fingerprint = risk_scope_fingerprint
        self._upstream = upstream

    @property
    def risk_scope_fingerprint(self) -> str:
        return self._risk_scope_fingerprint

    def reserve_group_vector(
        self,
        group_id: str,
        members: Sequence[tuple[str, OrderIntent]],
        *,
        deadline_ns: int,
    ) -> GroupAccountReservationSnapshot:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _RESERVATION_MOUNT,
            "reservations.reserve",
            {
                "origin_run_id": current.binding.origin_run_id,
                "group_id": group_id,
                "deadline_ns": deadline_ns,
                "members": [
                    {"leg_id": leg_id, "intent": intent.to_payload()} for leg_id, intent in members
                ],
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.reservation_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _snapshot_response(response)

    def query_group_vector(self, group_id: str) -> GroupAccountReservationSnapshot:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _RESERVATION_MOUNT,
            "reservations.query",
            {"origin_run_id": current.binding.origin_run_id, "group_id": group_id},
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.reservation_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _snapshot_response(response)

    def release_group_unclaimed(
        self, group_id: str, reason: str
    ) -> GroupAccountReservationSnapshot:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _RESERVATION_MOUNT,
            "reservations.release",
            {
                "origin_run_id": current.binding.origin_run_id,
                "group_id": group_id,
                "reason": reason,
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.reservation_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _snapshot_response(response)


class _OrdersRpcPort:
    def __init__(self, account_ref: str, upstream: BorrowedServiceClient):
        self._account_ref = account_ref
        self._upstream = upstream

    def submit_group_leg(
        self, intent: OrderIntent, permit: GroupLegPermitReference
    ) -> GroupLegOrderReceipt:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _ORDERS_MOUNT,
            "orders.submit_group_leg",
            {
                "origin_run_id": current.binding.origin_run_id,
                "intent": intent.to_payload(),
                "permit": permit.to_payload(),
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.order_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _order_receipt_response(response)

    def query(self, intent_id: str) -> GroupLegOrderReceipt | None:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _ORDER_QUERY_MOUNT,
            "orders.query_group_leg",
            {"origin_run_id": current.binding.origin_run_id, "intent_id": intent_id},
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.query_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _optional_order_receipt_response(response)

    def resolve_group_leg(
        self,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        claim: GroupLegClaimReceipt,
        status: GroupChildOrderStatus,
    ) -> GroupDispatchResolutionReceipt:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _ORDERS_MOUNT,
            "orders.resolve_group_leg",
            {
                "origin_run_id": current.binding.origin_run_id,
                "intent": intent.to_payload(),
                "permit": permit.to_payload(),
                "claim": claim.to_payload(),
                "status": status.to_payload(),
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.order_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        receipt = _order_receipt_response(response)
        if receipt.resolution is None:
            raise GroupReservationDeniedError("GROUP_RESOLUTION_UNPROVEN")
        return receipt.resolution


class GroupCoordinatorService:
    """Normal ServiceHost entry for durable, ordered two-leg group requests."""

    SUPPORTED_OPERATIONS = GROUP_COORDINATOR_OPERATIONS

    def __init__(
        self,
        store: SqliteOrderGroupStore,
        targets: Mapping[WireScope, GroupCoordinatorTarget],
        run_bindings: GroupOriginRunBindingStore,
        borrowed_upstream: BorrowedServiceClient,
        service_identity: WireIdentity,
        *,
        instance_id: str,
        group_admission: Any = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(store) is not SqliteOrderGroupStore:
            raise ServiceExecutionGroupError("GROUP_STORE_INVALID")
        if type(run_bindings) is not GroupOriginRunBindingStore:
            raise ServiceExecutionGroupError("GROUP_RUN_BINDINGS_INVALID")
        if type(borrowed_upstream) is not BorrowedServiceClient:
            raise ServiceExecutionGroupError("UPSTREAM_CLIENT_INVALID")
        if (
            type(service_identity) is not WireIdentity
            or service_identity.role is not WireRole.SERVICE
            or _REFERENCE.fullmatch(service_identity.principal_ref) is None
        ):
            raise ServiceExecutionGroupError("SERVICE_IDENTITY_INVALID")
        if type(instance_id) is not str or _REFERENCE.fullmatch(instance_id) is None:
            raise ServiceExecutionGroupError("COMPONENT_INSTANCE_INVALID")
        if group_admission is not None:
            gate_evaluate = getattr(group_admission, "evaluate", None)
            gate_ref = getattr(group_admission, "group_policy_ref", None)
            if not callable(gate_evaluate) or type(gate_ref) is not str:
                raise ServiceExecutionGroupError("GROUP_POLICY_RESOURCE_INVALID")
        if not callable(clock_ns) or not isinstance(targets, Mapping) or not targets:
            raise ServiceExecutionGroupError("GROUP_TARGETS_INVALID")
        target_copy: dict[WireScope, GroupCoordinatorTarget] = {}
        for stable_scope, target in targets.items():
            if type(stable_scope) is not WireScope or type(target) is not GroupCoordinatorTarget:
                raise ServiceExecutionGroupError("GROUP_TARGETS_INVALID")
            if not _coordinator_stable_scope(stable_scope):
                raise ServiceExecutionGroupError("GROUP_TARGET_SCOPE_INVALID")
            if tuple(sorted(target.accounts)) != tuple(stable_scope.account_refs):
                raise ServiceExecutionGroupError("GROUP_TARGET_SCOPE_INVALID")
            policy = target.portfolio_budget_policy
            if policy is not None and policy.portfolio_ref != stable_scope.portfolio_ref:
                raise ServiceExecutionGroupError("GROUP_BUDGET_POLICY_SCOPE_MISMATCH")
            for route in target.accounts.values():
                if (
                    route.execution_scope.account_ref not in target.accounts
                    or route.execution_scope.strategy_id != stable_scope.strategy_ref
                ):
                    raise ServiceExecutionGroupError("GROUP_TARGET_SCOPE_INVALID")
            target_copy[stable_scope] = target
        self._store = store
        self._run_bindings = run_bindings
        self._upstream = borrowed_upstream
        self._identity = service_identity
        self._instance_id = instance_id
        self._group_admission = group_admission
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._closed = False
        self._registrations: Mapping[str, ActiveRegistrationContext] = MappingProxyType({})
        fingerprints: dict[str, str] = {}
        for target in target_copy.values():
            for account, route in target.accounts.items():
                prior = fingerprints.setdefault(account, route.risk_scope_fingerprint)
                if prior != route.risk_scope_fingerprint:
                    raise ServiceExecutionGroupError("GROUP_ACCOUNT_RISK_SCOPE_AMBIGUOUS")
        reservations = {
            account: _ReservationsRpcPort(account, fingerprint, borrowed_upstream)
            for account, fingerprint in fingerprints.items()
        }
        orders = {account: _OrdersRpcPort(account, borrowed_upstream) for account in fingerprints}
        portfolio_policies: dict[str, PortfolioGroupBudgetPolicy] = {}
        for stable_scope, target in target_copy.items():
            policy = target.portfolio_budget_policy
            if policy is None:
                continue
            existing_policy = portfolio_policies.get(stable_scope.portfolio_ref)
            if existing_policy is not None and existing_policy.fingerprint != policy.fingerprint:
                raise ServiceExecutionGroupError("GROUP_BUDGET_POLICY_CONFLICT")
            portfolio_policies.setdefault(stable_scope.portfolio_ref, policy)
        try:
            self._coordinator = GroupOrderCoordinator(
                store,
                reservations=reservations,
                orders=orders,
                portfolio_budget_policies=portfolio_policies,
                budget_quote_builder=self._build_budget_quote,
                clock_ns=clock_ns,
            )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_COORDINATOR_INVALID") from None
        self._targets = MappingProxyType(target_copy)

    @property
    def supported_operations(self) -> frozenset[str]:
        return self.SUPPORTED_OPERATIONS

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._closed and bool(self._targets)

    def maintain_registrations(self, contexts: tuple[ActiveRegistrationContext, ...]) -> None:
        if type(contexts) is not tuple:
            raise ServiceExecutionGroupError("REGISTRATION_SNAPSHOT_INVALID")
        retained: dict[str, ActiveRegistrationContext] = {}
        for registration in contexts:
            if type(registration) is not ActiveRegistrationContext:
                raise ServiceExecutionGroupError("REGISTRATION_SNAPSHOT_INVALID")
            if registration.port != GROUP_COORDINATOR_PORT:
                continue
            target = self._target_for_scope(registration.context.scope)
            if target is None or not self._registration_is_eligible(registration, target):
                continue
            previous = retained.get(registration.context.registration_id)
            if previous is not None and previous != registration:
                raise ServiceExecutionGroupError("REGISTRATION_SNAPSHOT_CONFLICT")
            retained[registration.context.registration_id] = registration
        with self._lock:
            if self._closed:
                raise ServiceExecutionGroupError("CLOSED")
            # An empty view is valid at Host startup and never mutates or
            # revokes persistent accepted groups or authority records.
            self._registrations = MappingProxyType(retained)

    def handle(
        self, operation: str, *, context: ServiceCallContext, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            registration, target_scope, target = self._authorize(operation, context)
            if operation == "groups.query":
                return self._query(registration, target, arguments)
            if target.portfolio_budget_policy is None or target.budget_quote_builder is None:
                return _failure("GROUP_BUDGET_POLICY_REQUIRED")
            run_binding = self._existing_submit_binding(registration, arguments)
            if run_binding is None:
                request = self._parse_request(registration, target, arguments)
                if self._group_admission is not None:
                    try:
                        admission_decision = self._group_admission.evaluate(
                            portfolio_ref=request.portfolio_ref,
                            group_id=request.group_id,
                        )
                    except GroupPolicyError as error:
                        return _failure(error.code)
                    if admission_decision.admitted is not True:
                        return _failure(admission_decision.code)
                # Persist the Registry-derived run before package acceptance.
                run_binding = self._run_bindings.bind(
                    registration,
                    request,
                    coordinator_principal_ref=self._identity.principal_ref,
                )
            else:
                # A retry may arrive after the immutable group deadline. Only
                # a current, authorized registration may recover an existing
                # binding, and its complete business payload must match the
                # previously persisted request before owner recovery advances
                # anything. New IDs still take the strict fresh-deadline path
                # above; no deadline, ID, quote, or reservation is replaced.
                self._assert_exact_submit_replay(run_binding.request, arguments)
            call = _CoordinatorCall(registration, target_scope, target, run_binding)
            token = _CURRENT_COORDINATOR_CALL.set(call)
            try:
                try:
                    self._coordinator.accept(run_binding.request)
                except ContractValidationError:
                    return _failure("GROUP_ACCEPTANCE_REJECTED")
                snapshot = self._coordinator.advance_group(
                    run_binding.request.portfolio_ref, run_binding.request.group_id
                )
            finally:
                _CURRENT_COORDINATOR_CALL.reset(token)
            return _success({"found": True, "snapshot": snapshot.to_payload()})
        except ServiceExecutionGroupError as error:
            return _failure(error.code)
        except AcceptedGroupConflictError:
            return _failure("GROUP_BINDING_CONFLICT")
        except AcceptedGroupBindingError:
            return _failure("GROUP_BINDING_UNAVAILABLE")
        except (TypeError, ValueError, KeyError):
            return _failure("INVALID_REQUEST")

    def _existing_submit_binding(
        self,
        registration: ActiveRegistrationContext,
        arguments: Mapping[str, Any],
    ) -> GroupOriginRunBinding | None:
        """Read an existing same-ID submit binding under the current grant."""

        expected_keys = {"group_id", "deadline_ns", "first_leg_id", "legs"}
        if not isinstance(arguments, Mapping) or set(arguments) != expected_keys:
            return None
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        return self._run_bindings.get_for_registration(
            registration,
            group_id,
            coordinator_principal_ref=self._identity.principal_ref,
            operation="groups.submit",
        )

    @staticmethod
    def _assert_exact_submit_replay(
        accepted: GroupOrderIntent,
        arguments: Mapping[str, Any],
    ) -> None:
        """Require the replay body to equal the previously accepted business intent."""

        if not isinstance(arguments, Mapping) or set(arguments) != {
            "group_id",
            "deadline_ns",
            "first_leg_id",
            "legs",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        first_leg_id = _reference(arguments["first_leg_id"], "FIRST_LEG_INVALID")
        deadline_ns = arguments["deadline_ns"]
        if type(deadline_ns) is not int or not 0 < deadline_ns <= _MAX_SQLITE_INTEGER:
            raise ServiceExecutionGroupError("GROUP_DEADLINE_INVALID")
        raw_legs = arguments["legs"]
        if isinstance(raw_legs, (str, bytes)) or not isinstance(raw_legs, Sequence):
            raise ServiceExecutionGroupError("GROUP_LEGS_INVALID")
        if len(raw_legs) != len(accepted.legs):
            raise ServiceExecutionGroupError("GROUP_LEGS_INVALID")
        if (
            group_id != accepted.group_id
            or deadline_ns != accepted.deadline_ns
            or first_leg_id != accepted.first_leg_id
        ):
            raise AcceptedGroupConflictError("submit replay changed its accepted group identity")
        for raw_leg, planned in zip(raw_legs, accepted.legs, strict=True):
            if not isinstance(raw_leg, Mapping) or set(raw_leg) != {
                "leg_id",
                "account_ref",
                "intent",
            }:
                raise ServiceExecutionGroupError("GROUP_LEG_INVALID")
            leg_id = _reference(raw_leg["leg_id"], "GROUP_LEG_INVALID")
            account_ref = _reference(raw_leg["account_ref"], "GROUP_ACCOUNT_INVALID")
            payload = raw_leg["intent"]
            if not isinstance(payload, Mapping):
                raise ServiceExecutionGroupError("GROUP_INTENT_INVALID")
            try:
                intent = order_intent_from_payload(payload)
            except Exception:
                raise ServiceExecutionGroupError("GROUP_INTENT_INVALID") from None
            if (
                leg_id != planned.leg_id
                or account_ref != planned.account_ref
                or intent.to_payload() != planned.maximum_intent.to_payload()
            ):
                raise AcceptedGroupConflictError("submit replay changed an accepted leg")

    def _build_budget_quote(
        self,
        request: GroupOrderIntent,
        package_policy: PortfolioGroupBudgetPolicy,
        *,
        now_ns: int,
    ) -> GroupBudgetQuote:
        """Select the quote builder only from the authenticated target context."""

        current = _CURRENT_COORDINATOR_CALL.get()
        if current is None or self._targets.get(current.target_scope) is not current.target:
            raise ServiceExecutionGroupError("GROUP_BUDGET_TARGET_UNAVAILABLE")
        target_scope = current.target_scope
        target_policy = current.target.portfolio_budget_policy
        builder = current.target.budget_quote_builder
        if (
            target_policy is None
            or builder is None
            or package_policy.fingerprint != target_policy.fingerprint
            or request.portfolio_ref != target_scope.portfolio_ref
            or request.strategy_id != target_scope.strategy_ref
            or request.coordinator_principal_ref != self._identity.principal_ref
            or not set(request.account_refs).issubset(current.target.accounts)
            or any(leg.account_ref not in current.target.accounts for leg in request.legs)
        ):
            raise ServiceExecutionGroupError("GROUP_BUDGET_TARGET_MISMATCH")
        try:
            quote = builder(request, target_policy, now_ns=now_ns)
        except GroupBudgetUnsupportedOrderError:
            raise ServiceExecutionGroupError("GROUP_BUDGET_MARKET_PRICE_UNBOUNDED") from None
        except RiskDeniedError as error:
            code = error.code
            if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code) is None:
                code = "GROUP_BUDGET_RISK_DENIED"
            raise ServiceExecutionGroupError(code) from None
        except ContractValidationError:
            raise ServiceExecutionGroupError("GROUP_BUDGET_QUOTE_INVALID") from None
        except Exception:
            raise ServiceExecutionGroupError("GROUP_BUDGET_QUOTE_FAILED") from None
        if type(quote) is not GroupBudgetQuote or quote.policy_fingerprint != target_policy.fingerprint:
            raise ServiceExecutionGroupError("GROUP_BUDGET_QUOTE_INVALID")
        return quote

    def close(self) -> None:
        with self._lock:
            self._registrations = MappingProxyType({})
            self._closed = True

    def _authorize(
        self, operation: str, context: ServiceCallContext
    ) -> tuple[ActiveRegistrationContext, WireScope, GroupCoordinatorTarget]:
        if type(operation) is not str or operation not in self.SUPPORTED_OPERATIONS:
            raise ServiceExecutionGroupError("UNKNOWN_OPERATION")
        if type(context) is not ServiceCallContext:
            raise ServiceExecutionGroupError("SERVICE_CONTEXT_REQUIRED")
        if (
            type(context.identity) is not WireIdentity
            or context.identity.role is not WireRole.STRATEGY_CLIENT
            or context.identity.principal_ref != context.principal_ref
            or context.identity.actor_ref != context.scope.strategy_ref
            or not _coordinator_call_scope(context.scope)
            or type(context.registration_id) is not str
        ):
            raise ServiceExecutionGroupError("SERVICE_AUTHORITY_INVALID")
        with self._lock:
            if self._closed:
                raise ServiceExecutionGroupError("CLOSED")
            registration = self._registrations.get(context.registration_id)
        if registration is None or not _same_call(registration.context, context):
            raise ServiceExecutionGroupError("REGISTRATION_NOT_ACTIVE")
        target_scope = self._target_scope_for(context.scope)
        if target_scope is None:
            raise ServiceExecutionGroupError("SCOPE_DENIED")
        target = self._targets[target_scope]
        if not self._registration_is_eligible(registration, target):
            raise ServiceExecutionGroupError("REGISTRATION_SCOPE_MISMATCH")
        if operation not in registration.operations or (
            operation == "groups.submit" and operation not in registration.write_operations
        ):
            raise ServiceExecutionGroupError("OPERATION_NOT_GRANTED")
        if registration.expires_at <= self._clock_ns() / 1_000_000_000:
            raise ServiceExecutionGroupError("REGISTRATION_EXPIRED")
        return registration, target_scope, target

    def _registration_is_eligible(
        self, registration: ActiveRegistrationContext, target: GroupCoordinatorTarget
    ) -> bool:
        context = registration.context
        scope = context.scope
        full_write_view = (
            registration.operations == self.SUPPORTED_OPERATIONS
            and registration.write_operations == frozenset({"groups.submit"})
        )
        query_only_view = (
            registration.operations == frozenset({"groups.query"})
            and registration.write_operations == frozenset()
        )
        return (
            registration.instance_id == self._instance_id
            and registration.port == GROUP_COORDINATOR_PORT
            and registration.scope_ref == target.scope_ref
            and (full_write_view or query_only_view)
            and context.identity.role is WireRole.STRATEGY_CLIENT
            and context.identity.actor_ref == scope.strategy_ref
            and context.identity.principal_ref == context.principal_ref
            and scope.run_id is not None
            and _REFERENCE.fullmatch(scope.run_id) is not None
            and registration.strategy_write_run_id == scope.run_id
            and scope.strategy_ref is not None
            and scope.portfolio_ref is not None
            and scope.execution_domain_ref
            and tuple(scope.account_refs) == tuple(sorted(target.accounts))
            and scope.account_ref is None
            and scope.manual_book_ref is None
            and scope.group_id is None
            and scope.leg_id is None
        )

    def _target_scope_for(self, scope: WireScope) -> WireScope | None:
        for template in self._targets:
            if _matches_only_run(template, scope):
                return template
        return None

    def _target_for_scope(self, scope: WireScope) -> GroupCoordinatorTarget | None:
        template = self._target_scope_for(scope)
        return None if template is None else self._targets[template]

    def _parse_request(
        self,
        registration: ActiveRegistrationContext,
        target: GroupCoordinatorTarget,
        arguments: Mapping[str, Any],
    ) -> GroupOrderIntent:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "group_id",
            "deadline_ns",
            "first_leg_id",
            "legs",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        first_leg_id = _reference(arguments["first_leg_id"], "FIRST_LEG_INVALID")
        deadline_ns = arguments["deadline_ns"]
        received_at_ns = self._clock_ns()
        if (
            type(deadline_ns) is not int
            or not received_at_ns < deadline_ns <= _MAX_SQLITE_INTEGER
            or deadline_ns - received_at_ns > target.max_group_lifetime_ns
        ):
            raise ServiceExecutionGroupError("GROUP_DEADLINE_INVALID")
        raw_legs = arguments["legs"]
        if isinstance(raw_legs, (str, bytes)) or not isinstance(raw_legs, Sequence):
            raise ServiceExecutionGroupError("GROUP_LEGS_INVALID")
        if len(raw_legs) != 2:
            raise ServiceExecutionGroupError("GROUP_LEGS_INVALID")
        scope = registration.context.scope
        plans: list[GroupOrderLegPlan] = []
        used_leg_ids: set[str] = set()
        for raw_leg in raw_legs:
            if not isinstance(raw_leg, Mapping) or set(raw_leg) != {
                "leg_id",
                "account_ref",
                "intent",
            }:
                raise ServiceExecutionGroupError("GROUP_LEG_INVALID")
            leg_id = _reference(raw_leg["leg_id"], "GROUP_LEG_INVALID")
            account_ref = _reference(raw_leg["account_ref"], "GROUP_ACCOUNT_INVALID")
            if leg_id in used_leg_ids or account_ref not in target.accounts:
                raise ServiceExecutionGroupError("GROUP_LEG_INVALID")
            used_leg_ids.add(leg_id)
            payload = raw_leg["intent"]
            if not isinstance(payload, Mapping):
                raise ServiceExecutionGroupError("GROUP_INTENT_INVALID")
            try:
                intent = order_intent_from_payload(payload)
            except Exception:
                raise ServiceExecutionGroupError("GROUP_INTENT_INVALID") from None
            route = target.accounts[account_ref]
            self._validate_leg(intent, route, scope, group_id, leg_id, deadline_ns, received_at_ns)
            plans.append(
                GroupOrderLegPlan(
                    leg_id=leg_id,
                    account_ref=account_ref,
                    maximum_intent=intent,
                    base_units_per_order_unit=next(
                        rule.base_units_per_order_unit
                        for rule in route.instruments.values()
                        if rule.metadata.instrument == intent.instrument
                    ),
                    quantity_step=route.instruments[intent.instrument].metadata.quantity_step,
                    metadata_valid_until_ns=route.instruments[
                        intent.instrument
                    ].metadata.expires_at_ns,
                )
            )
        try:
            return GroupOrderIntent(
                group_id=group_id,
                portfolio_ref=scope.portfolio_ref,
                strategy_id=scope.strategy_ref,
                coordinator_principal_ref=self._identity.principal_ref,
                received_at_ns=received_at_ns,
                deadline_ns=deadline_ns,
                first_leg_id=first_leg_id,
                legs=tuple(plans),  # type: ignore[arg-type]
            )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_INTENT_INVALID") from None

    @staticmethod
    def _validate_leg(
        intent: OrderIntent,
        route: GroupAccountRoute,
        registration_scope: WireScope,
        group_id: str,
        leg_id: str,
        deadline_ns: int,
        now_ns: int,
    ) -> None:
        rule = route.instruments.get(intent.instrument)
        if rule is None:
            raise ServiceExecutionGroupError("GROUP_INSTRUMENT_NOT_CONFIGURED")
        metadata = rule.metadata
        try:
            expected_id = group_leg_intent_id(group_id, leg_id)
        except (TypeError, ValueError):
            raise ServiceExecutionGroupError("GROUP_LEG_ID_INVALID") from None
        if (
            intent.intent_id != expected_id
            or intent.scope != route.execution_scope
            or intent.scope.account_ref != route.account_ref
            or intent.scope.strategy_id != registration_scope.strategy_ref
            or intent.deadline_ns != deadline_ns
            or intent.position_effect is not PositionEffect.OPEN
            or intent.reduce_only
            or intent.time_in_force is not TimeInForce.IOC
            or intent.order_type not in rule.allowed_order_types
            or intent.metadata_version != metadata.metadata_version
            or intent.tags.get(INSTRUMENT_METADATA_DIGEST_TAG) != metadata.digest
            or metadata.as_of_ns > now_ns
            or metadata.expires_at_ns < deadline_ns
            or intent.quantity % metadata.quantity_step != 0
            or (metadata.min_quantity is not None and intent.quantity < metadata.min_quantity)
            or (metadata.max_quantity is not None and intent.quantity > metadata.max_quantity)
            or (
                intent.order_type is OrderType.LIMIT
                and intent.price is not None
                and intent.price % metadata.tick_size != 0
            )
        ):
            raise ServiceExecutionGroupError("GROUP_LEG_TERMS_INVALID")

    def _query(
        self,
        registration: ActiveRegistrationContext,
        target: GroupCoordinatorTarget,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {"group_id"}:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        binding = self._run_bindings.get_for_registration(
            registration,
            group_id,
            coordinator_principal_ref=self._identity.principal_ref,
            operation="groups.query",
        )
        if binding is None:
            return _success({"found": False, "snapshot": None})
        snapshot = self._coordinator.query(binding.request.portfolio_ref, group_id)
        if snapshot is None:
            return _failure("GROUP_JOURNAL_INCOMPLETE")
        return _success({"found": True, "snapshot": snapshot.to_payload()})


class ExecutionGroupOrderEntryService:
    """Authenticated account-owner adapter for the three group-only routes.

    It reuses the one configured Execution store, provider dispatcher, writer
    lease machinery, and Risk admission adapter. Group proof callbacks and the
    dispatcher are created per request by the trusted proof source; none can
    be selected by the wire payload.
    """

    SUPPORTED_OPERATIONS = GROUP_ACCOUNT_OWNER_OPERATIONS

    def __init__(
        self,
        store: SqliteExecutionStore,
        targets: Mapping[WireScope, ExecutionScope],
        dispatch: object,
        admission: AccountScopedGroupAdmissionAdapter,
        accepted_group_store: AcceptedGroupBindingStore,
        group_authority: ExecutionGroupAuthorityGuard,
        proof_source: object,
        trusted_write_policies: tuple[TrustedGroupWritePolicy, ...],
        trusted_resolution_policies: tuple[TrustedGroupResolutionPolicy, ...],
        *,
        writer_id: str,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise ServiceExecutionGroupError("EXECUTION_STORE_INVALID")
        if not isinstance(targets, Mapping) or not targets:
            raise ServiceExecutionGroupError("EXECUTION_TARGETS_INVALID")
        if type(admission) is not AccountScopedGroupAdmissionAdapter:
            raise ServiceExecutionGroupError("GROUP_ADMISSION_INVALID")
        if type(accepted_group_store) is not AcceptedGroupBindingStore:
            raise ServiceExecutionGroupError("ACCEPTED_GROUP_STORE_INVALID")
        if (
            not _has_method(group_authority, "bind_service_call")
            or not _has_method(group_authority, "hold_group_leg_authorization")
            or not _has_method(group_authority, "assert_current_group_leg_authorization")
        ):
            raise ServiceExecutionGroupError("GROUP_AUTHORITY_INVALID")
        if (
            type(trusted_write_policies) is not tuple
            or not trusted_write_policies
            or any(type(item) is not TrustedGroupWritePolicy for item in trusted_write_policies)
            or type(trusted_resolution_policies) is not tuple
            or not trusted_resolution_policies
            or any(
                type(item) is not TrustedGroupResolutionPolicy
                for item in trusted_resolution_policies
            )
        ):
            raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
        if type(writer_id) is not str or _REFERENCE.fullmatch(writer_id) is None:
            raise ServiceExecutionGroupError("WRITER_ID_INVALID")
        if not callable(clock_ns):
            raise ServiceExecutionGroupError("CLOCK_INVALID")
        try:
            proof_module = __import__(
                "bt_api_py.service_execution_group_proofs", fromlist=["GroupProofSource"]
            )
            proof_source_type = proof_module.GroupProofSource
            proof_bundle_type = proof_module.GroupProofRuntimeBundle
            if (
                type(proof_source) is not proof_source_type
                or not all(
                    callable(getattr(proof_source, name, None))
                    for name in ("bind_permit", "bind_dispatch", "read_receipt", "resolve_receipt")
                )
            ):
                raise TypeError
        except Exception:
            raise ServiceExecutionGroupError("GROUP_PROOF_SOURCE_INVALID") from None

        target_copy: dict[WireScope, ExecutionScope] = {}
        for wire_scope, execution_scope in targets.items():
            if (
                type(wire_scope) is not WireScope
                or type(execution_scope) is not ExecutionScope
                or not _owner_scope_template(wire_scope)
                or wire_scope.account_ref != execution_scope.account_ref
                or wire_scope.strategy_ref != execution_scope.strategy_id
            ):
                raise ServiceExecutionGroupError("EXECUTION_TARGETS_INVALID")
            target_copy[wire_scope] = execution_scope

        proof_bundle = getattr(proof_source, "runtime_bundle", None)
        if (
            type(proof_bundle) is not proof_bundle_type
            or proof_bundle.proof_source is not proof_source
            or proof_bundle.account_scoped_admission is not admission
            or set(proof_bundle.proof_targets) != set(target_copy)
        ):
            raise ServiceExecutionGroupError("GROUP_PROOF_COMPOSITION_INVALID")
        try:
            proof_bundle.require_runtime_bundle(
                account_scoped_admission=admission,
                risk_admission=proof_bundle.risk_admission,
            )
            for wire_scope, execution_scope in target_copy.items():
                # Constructor targets are stable account scopes. The public
                # proof_target_for() resolver accepts a per-leg child scope
                # (it requires group_id/leg_id), so composition validation
                # must inspect the exact immutable stable-keyed target map.
                proof_target = proof_bundle.proof_targets.get(wire_scope)
                if (
                    proof_target.execution_scope != execution_scope
                    or proof_target.execution_store is not store
                ):
                    raise ValueError
        except Exception:
            raise ServiceExecutionGroupError("GROUP_PROOF_COMPOSITION_INVALID") from None

        instance_ids = {item.instance_id for item in trusted_write_policies}
        if len(instance_ids) != 1 or instance_ids != {
            item.instance_id for item in trusted_resolution_policies
        }:
            raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
        self._instance_id = next(iter(instance_ids))
        write_by_key: dict[tuple[object, ...], TrustedGroupWritePolicy] = {}
        for policy in trusted_write_policies:
            if (
                policy.port != GROUP_ORDER_ENTRY_PORT
                or policy.operation != "orders.submit_group_leg"
                or policy.scope not in target_copy
                or getattr(policy, "account_ref", policy.scope.account_ref)
                != policy.scope.account_ref
            ):
                raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
            key = _policy_key(policy)
            if key in write_by_key:
                raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
            write_by_key[key] = policy
        resolution_keys: set[tuple[object, ...]] = set()
        for policy in trusted_resolution_policies:
            if (
                policy.port != GROUP_ORDER_ENTRY_PORT
                or policy.operation != "orders.resolve_group_leg"
                or policy.scope not in target_copy
                or getattr(policy, "account_ref", policy.scope.account_ref)
                != policy.scope.account_ref
            ):
                raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
            key = _policy_key(policy)
            if key in resolution_keys:
                raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
            resolution_keys.add(key)
        if set(write_by_key) != resolution_keys:
            raise ServiceExecutionGroupError("GROUP_POLICIES_MISMATCH")

        self._store = store
        self._targets = MappingProxyType(target_copy)
        self._accepted_groups = accepted_group_store
        self._authority = group_authority
        self._proof_source = proof_source
        self._write_policies = tuple(trusted_write_policies)
        self._resolution_policies = tuple(trusted_resolution_policies)
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._closed = False
        self._registrations: Mapping[tuple[str, str], ActiveRegistrationContext] = MappingProxyType(
            {}
        )
        try:
            from bt_api_execution.service_order_entry import ExecutionOrderEntryService

            self._order_entry = ExecutionOrderEntryService(
                store,
                target_copy,
                dispatch,
                admission_gate=admission,
                group_coordinator_authority=group_authority,
                allow_unprotected=False,
                writer_id=writer_id,
            )
        except Exception:
            raise ServiceExecutionGroupError("EXECUTION_ORDER_ENTRY_INVALID") from None

    @property
    def supported_operations(self) -> frozenset[str]:
        return self.SUPPORTED_OPERATIONS

    @property
    def ready(self) -> bool:
        with self._lock:
            return (
                not self._closed
                and bool(self._targets)
                and callable(getattr(self._proof_source, "read_receipt", None))
            )

    def maintain_registrations(self, contexts: tuple[ActiveRegistrationContext, ...]) -> None:
        if type(contexts) is not tuple:
            raise ServiceExecutionGroupError("REGISTRATION_SNAPSHOT_INVALID")
        retained: dict[tuple[str, str], ActiveRegistrationContext] = {}
        for registration in contexts:
            if type(registration) is not ActiveRegistrationContext:
                raise ServiceExecutionGroupError("REGISTRATION_SNAPSHOT_INVALID")
            if registration.instance_id != self._instance_id:
                continue
            if registration.port == GROUP_ORDER_ENTRY_PORT:
                expected_ops = frozenset({"orders.submit_group_leg", "orders.resolve_group_leg"})
                expected_writes = expected_ops
                expected_policy_ops = {
                    "orders.submit_group_leg": self._write_policies,
                    "orders.resolve_group_leg": self._resolution_policies,
                }
            elif registration.port == GROUP_ORDER_QUERY_PORT:
                expected_ops = frozenset({"orders.query_group_leg"})
                expected_writes = frozenset()
                expected_policy_ops = {}
            else:
                continue
            if (
                registration.operations != expected_ops
                or registration.write_operations != expected_writes
                or registration.context.scope not in self._targets
            ):
                continue
            context = registration.context
            if not self._identity_is_expected(context.identity, context.principal_ref):
                continue
            policy_match = any(
                policy.instance_id == registration.instance_id
                and policy.principal_ref == context.principal_ref
                and policy.identity == context.identity
                and policy.scope_ref == registration.scope_ref
                and policy.scope == context.scope
                and policy.run_id == registration.strategy_write_run_id
                and policy.port == registration.port
                and policy.operation in expected_ops
                for policies in expected_policy_ops.values()
                for policy in policies
            )
            if registration.port == GROUP_ORDER_QUERY_PORT and not policy_match:
                # Query capability still belongs to the trusted account role;
                # the Registry supplies its own active query-port context and
                # exact read-only grant. Bind that route to the same trusted
                # owner principal/scope/run, while allowing the query port's
                # distinct scope_ref from the entry-port mutation policy.
                policy_match = any(
                    policy.instance_id == registration.instance_id
                    and policy.principal_ref == context.principal_ref
                    and policy.identity == context.identity
                    and policy.scope == context.scope
                    and policy.run_id == registration.strategy_write_run_id
                    and policy.port == GROUP_ORDER_ENTRY_PORT
                    and policy.operation
                    in {"orders.submit_group_leg", "orders.resolve_group_leg"}
                    and getattr(policy, "account_ref", context.scope.account_ref)
                    == context.scope.account_ref
                    for policy in (*self._write_policies, *self._resolution_policies)
                )
            if registration.strategy_write_run_id is None or not policy_match:
                continue
            key = (context.registration_id, registration.port)
            previous = retained.get(key)
            if previous is not None and previous != registration:
                raise ServiceExecutionGroupError("REGISTRATION_SNAPSHOT_CONFLICT")
            retained[key] = registration
        with self._lock:
            if self._closed:
                raise ServiceExecutionGroupError("CLOSED")
            # A fresh Host epoch may start with no active registrations. This
            # replaces only the live request view; accepted bindings and
            # authority holds remain in their durable stores.
            self._registrations = MappingProxyType(retained)

    def handle(
        self, operation: str, *, context: ServiceCallContext, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            registration, execution_scope = self._authorize(operation, context)
            if operation == "orders.query_group_leg":
                return self._query(registration, context, execution_scope, arguments)
            if operation == "orders.resolve_group_leg":
                return self._resolve(registration, context, execution_scope, arguments)
            return self._submit(registration, context, execution_scope, arguments)
        except ServiceExecutionGroupError as error:
            return _failure(error.code)
        except (AcceptedGroupConflictError, AcceptedGroupBindingError):
            return _failure("GROUP_BINDING_UNAVAILABLE")
        except Exception:
            # Provider, Risk, SQLite, source, and authority failures remain
            # finite transport failures; the durable stores retain their
            # UNKNOWN/hold state for exact later query.
            return _failure("GROUP_ORDER_UNAVAILABLE")

    def recover_interrupted_dispatches(self) -> tuple[ExecutionRecord, ...]:
        """Run the existing no-provider startup recovery before readiness."""

        try:
            return self._order_entry.recover_interrupted_dispatches()
        except Exception:
            raise ServiceExecutionGroupError("ORDER_RECOVERY_FAILED") from None

    def close(self) -> None:
        """Drop only this adapter's live registration view."""

        with self._lock:
            self._registrations = MappingProxyType({})
            self._closed = True

    def _authorize(
        self, operation: str, context: ServiceCallContext
    ) -> tuple[ActiveRegistrationContext, ExecutionScope]:
        if type(operation) is not str or operation not in self.SUPPORTED_OPERATIONS:
            raise ServiceExecutionGroupError("UNKNOWN_OPERATION")
        if type(context) is not ServiceCallContext:
            raise ServiceExecutionGroupError("SERVICE_CONTEXT_REQUIRED")
        port = (
            GROUP_ORDER_QUERY_PORT
            if operation == "orders.query_group_leg"
            else GROUP_ORDER_ENTRY_PORT
        )
        if (
            type(context.identity) is not WireIdentity
            or context.identity.role is not WireRole.SERVICE
            or context.identity.principal_ref != context.principal_ref
            or type(context.registration_id) is not str
            or context.scope not in self._targets
        ):
            raise ServiceExecutionGroupError("SERVICE_AUTHORITY_INVALID")
        with self._lock:
            if self._closed:
                raise ServiceExecutionGroupError("CLOSED")
            registration = self._registrations.get((context.registration_id, port))
        if registration is None or not _same_call(registration.context, context):
            raise ServiceExecutionGroupError("REGISTRATION_NOT_ACTIVE")
        if registration.instance_id != self._instance_id or registration.expires_at <= (
            self._clock_ns() / 1_000_000_000
        ):
            raise ServiceExecutionGroupError("REGISTRATION_EXPIRED")
        if operation not in registration.operations or (
            operation in {"orders.submit_group_leg", "orders.resolve_group_leg"}
            and operation not in registration.write_operations
        ):
            raise ServiceExecutionGroupError("OPERATION_NOT_GRANTED")
        if registration.strategy_write_run_id is None or not self._policy_matches(
            registration, operation
        ):
            raise ServiceExecutionGroupError("GROUP_POLICY_MISMATCH")
        execution_scope = self._targets.get(context.scope)
        if execution_scope is None:
            raise ServiceExecutionGroupError("SCOPE_DENIED")
        return registration, execution_scope

    def _identity_is_expected(self, identity: WireIdentity, principal_ref: str) -> bool:
        return (
            type(identity) is WireIdentity
            and identity.role is WireRole.SERVICE
            and identity.principal_ref == principal_ref
            and _REFERENCE.fullmatch(principal_ref) is not None
        )

    def _policy_matches(self, registration: ActiveRegistrationContext, operation: str) -> bool:
        context = registration.context
        if operation == "orders.query_group_leg":
            # The query route is a separately Registry-authorized read port.
            # Its active registration is exact in _authorize; here we bind it
            # to the same durable owner identity, target, and logical run as
            # the trusted mutation policies without conflating port scope_ref.
            return any(
                getattr(policy, "instance_id", None) == registration.instance_id
                and getattr(policy, "principal_ref", None) == context.principal_ref
                and getattr(policy, "identity", None) == context.identity
                and getattr(policy, "scope", None) == context.scope
                and getattr(policy, "run_id", None) == registration.strategy_write_run_id
                and getattr(policy, "port", None) == GROUP_ORDER_ENTRY_PORT
                and getattr(policy, "operation", None)
                in {"orders.submit_group_leg", "orders.resolve_group_leg"}
                and getattr(policy, "account_ref", context.scope.account_ref)
                == context.scope.account_ref
                for policy in (*self._write_policies, *self._resolution_policies)
            )
        policies: Sequence[object]
        if operation == "orders.submit_group_leg":
            policies = self._write_policies
        elif operation == "orders.resolve_group_leg":
            policies = self._resolution_policies
        else:
            return False
        return any(
            getattr(policy, "instance_id", None) == registration.instance_id
            and getattr(policy, "principal_ref", None) == context.principal_ref
            and getattr(policy, "identity", None) == context.identity
            and getattr(policy, "port", None) == GROUP_ORDER_ENTRY_PORT
            and getattr(policy, "operation", None) == operation
            and getattr(policy, "scope_ref", None) == registration.scope_ref
            and getattr(policy, "scope", None) == context.scope
            and getattr(policy, "run_id", None) == registration.strategy_write_run_id
            and getattr(policy, "account_ref", context.scope.account_ref)
            == context.scope.account_ref
            for policy in policies
        )

    def _submit(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "origin_run_id",
            "intent",
            "permit",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        origin_run_id = _reference(arguments["origin_run_id"], "GROUP_RUN_INVALID")
        if origin_run_id != registration.strategy_write_run_id:
            raise ServiceExecutionGroupError("GROUP_RUN_MISMATCH")
        try:
            intent = order_intent_from_payload(arguments["intent"])
            permit = GroupLegPermitReference.from_payload(arguments["permit"])
        except Exception:
            raise ServiceExecutionGroupError("GROUP_REQUEST_INVALID") from None
        if intent.scope != execution_scope:
            raise ServiceExecutionGroupError("GROUP_SCOPE_MISMATCH")
        child_scope = replace(context.scope, group_id=permit.group_id, leg_id=permit.leg_id)
        binding = self._accepted_groups.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=permit.group_id,
            operation="orders.submit_group_leg",
        )
        if binding is None:
            raise ServiceExecutionGroupError("GROUP_NOT_FOUND")
        self._validate_accepted_owner(binding, registration, origin_run_id)
        try:
            binding.permit_for(permit, intent)
        except AcceptedGroupBindingError:
            raise ServiceExecutionGroupError("GROUP_PERMIT_MISMATCH") from None
        reservation_leg = self._reservation_leg(binding, permit.leg_id)
        existing = self._store.get(intent.intent_id, scope=execution_scope)
        if existing is not None:
            # Exact replays are query-only. They never enter the facade again,
            # even if the old result is UNKNOWN or the response was lost.
            receipt = self._read_existing_receipt(
                binding,
                intent,
                permit,
                context,
                child_scope,
            )
            if receipt is None:
                raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE")
            return _success_value(receipt.to_payload())
        if (
            reservation_leg.state != "ACTIVE"
            or reservation_leg.claimed
            or reservation_leg.resolved
            or reservation_leg.expires_at_ns <= self._clock_ns()
        ):
            raise ServiceExecutionGroupError("GROUP_PERMIT_NOT_CLAIMABLE")

        source = self._proof_source
        try:
            permit_binding = source.bind_permit(
                permit=permit,
                intent=intent,
                principal_ref=context.principal_ref,
                identity=context.identity,
                wire_scope=child_scope,
                service_context=context,
            )
            if type(permit_binding) is not GroupPermitBinding:
                raise TypeError
        except Exception:
            raise ServiceExecutionGroupError("GROUP_PERMIT_BINDING_FAILED") from None

        session = None
        session_finished = False
        try:
            service_call_guard = self._authority.bind_service_call(
                context, execution_scope=execution_scope
            )
            if not _is_context_manager(service_call_guard):
                raise TypeError
            with service_call_guard:
                candidate_session = source.bind_dispatch(
                    binding=permit_binding,
                    intent=intent,
                    permit=permit,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    wire_scope=child_scope,
                    service_context=context,
                    active_authority=self._authority,
                )
                if not _valid_dispatch_session(candidate_session):
                    raise TypeError
                session = candidate_session
                reply = self._order_entry.submit_group_leg(
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    scope=child_scope,
                    intent=intent,
                    binding=permit_binding,
                    after_group_claim=session.after_group_claim,
                    provider_dispatcher=session.provider_dispatcher,
                    after_group_result=session.after_group_result,
                )
                if not isinstance(reply, Mapping):
                    raise TypeError
                record = self._store.get(intent.intent_id, scope=execution_scope)
                if record is None:
                    raise ServiceExecutionGroupError("GROUP_ORDER_RECORD_MISSING")
                if (
                    record.group_binding != permit_binding
                    or record.payload_sha256 != intent.fingerprint
                    or record.scope_key != execution_scope.key
                ):
                    raise ServiceExecutionGroupError("GROUP_ORDER_RECORD_MISMATCH")
                if reply.get("ok") is True and reply.get("code") == "OK":
                    receipt = session.finish(record=record)
                    if type(receipt) is not GroupLegOrderReceipt:
                        raise ServiceExecutionGroupError("GROUP_RECEIPT_INVALID")
                    payload = receipt.to_payload()
                    session_finished = True
                    return _success_value(payload)
                receipt = source.read_receipt(
                    binding=permit_binding,
                    intent=intent,
                    permit=permit,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    wire_scope=child_scope,
                    service_context=context,
                    active_authority=self._authority,
                )
                if type(receipt) is not GroupLegOrderReceipt:
                    raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE")
                return _success_value(receipt.to_payload())
        except ServiceExecutionGroupError:
            raise
        except Exception:
            # In particular, do not turn a source/authority callback failure
            # into a second submit. The Execution record and both durable
            # holds remain queryable under the same child id.
            raise ServiceExecutionGroupError("GROUP_ORDER_OUTCOME_UNPROVEN") from None
        finally:
            if session is not None and not session_finished:
                # The proof session owns only a call-local Risk fence/context.
                # Its abort is idempotent and never removes durable UNKNOWN,
                # claim, or coordinator-authority holds.
                session.abort()

    def _query(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "origin_run_id",
            "intent_id",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        origin_run_id = _reference(arguments["origin_run_id"], "GROUP_RUN_INVALID")
        intent_id = _reference(arguments["intent_id"], "GROUP_INTENT_ID_INVALID")
        if origin_run_id != registration.strategy_write_run_id:
            raise ServiceExecutionGroupError("GROUP_RUN_MISMATCH")
        binding = self._accepted_groups.find_binding_by_intent(
            registration,
            origin_run_id=origin_run_id,
            intent_id=intent_id,
            operation="orders.query_group_leg",
        )
        if binding is None:
            # Do not reveal an identically named intent that belongs to a
            # different account/strategy target on this shared local store.
            if self._store.get_intent(intent_id, scope=execution_scope) is not None:
                raise ServiceExecutionGroupError("GROUP_BINDING_MISSING")
            return _success_value(None)
        self._validate_accepted_owner(binding, registration, origin_run_id)
        member = self._member_for_intent(binding, intent_id)
        target_scope = self._target_for_binding(binding)
        if target_scope != execution_scope:
            raise ServiceExecutionGroupError("GROUP_SCOPE_MISMATCH")
        record = self._store.get(intent_id, scope=execution_scope)
        intent = self._store.get_intent(intent_id, scope=execution_scope)
        if intent is None:
            if record is not None:
                raise ServiceExecutionGroupError("GROUP_ORDER_INTENT_MISSING")
            intent = member
        elif not _same_reserved_order_except_quantity(member, intent):
            raise ServiceExecutionGroupError("GROUP_ORDER_INTENT_MISMATCH")
        permit = self._permit_from_binding(binding, intent)
        if record is not None and (
            record.group_binding is None
            or record.group_binding.order_intent_hash != permit.order_intent_hash
            or record.group_binding.group_id != permit.group_id
            or record.group_binding.leg_id != permit.leg_id
        ):
            raise ServiceExecutionGroupError("GROUP_ORDER_RECORD_MISMATCH")
        receipt = self._read_existing_receipt(
            binding,
            intent,
            permit,
            context,
            replace(context.scope, group_id=permit.group_id, leg_id=permit.leg_id),
        )
        if receipt is None:
            if record is None:
                return _success_value(None)
            raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE")
        return _success_value(receipt.to_payload())

    def _resolve(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "origin_run_id",
            "intent",
            "permit",
            "claim",
            "status",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        origin_run_id = _reference(arguments["origin_run_id"], "GROUP_RUN_INVALID")
        if origin_run_id != registration.strategy_write_run_id:
            raise ServiceExecutionGroupError("GROUP_RUN_MISMATCH")
        try:
            intent = order_intent_from_payload(arguments["intent"])
            permit = GroupLegPermitReference.from_payload(arguments["permit"])
            claim = GroupLegClaimReceipt.from_payload(arguments["claim"])
            status = GroupChildOrderStatus.from_payload(arguments["status"])
        except Exception:
            raise ServiceExecutionGroupError("GROUP_REQUEST_INVALID") from None
        if (
            intent.scope != execution_scope
            or claim.permit != permit
            or status.intent_id != intent.intent_id
        ):
            raise ServiceExecutionGroupError("GROUP_RESOLUTION_MISMATCH")
        binding = self._accepted_groups.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=permit.group_id,
            operation="orders.resolve_group_leg",
        )
        if binding is None:
            raise ServiceExecutionGroupError("GROUP_NOT_FOUND")
        self._validate_accepted_owner(binding, registration, origin_run_id)
        try:
            binding.permit_for(permit, intent)
        except AcceptedGroupBindingError:
            raise ServiceExecutionGroupError("GROUP_PERMIT_MISMATCH") from None
        # The public source API deliberately exposes no fresh resolution lease
        # acquisition yet. Existing exact RISK_RESOLVED receipts are returned
        # by query; unresolved and stale cases stay held and fail closed here.
        raise ServiceExecutionGroupError("GROUP_RESOLUTION_UNAVAILABLE")

    def _read_existing_receipt(
        self,
        binding: AcceptedGroupBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        context: ServiceCallContext,
        wire_scope: WireScope,
    ) -> GroupLegOrderReceipt | None:
        execution_scope = self._target_for_binding(binding)
        if execution_scope is None:
            raise ServiceExecutionGroupError("GROUP_SCOPE_MISMATCH")
        try:
            with self._authority.bind_service_call(context, execution_scope=execution_scope):
                receipt = self._proof_source.read_receipt(
                    binding=self._proof_source.bind_permit(
                        permit=permit,
                        intent=intent,
                        principal_ref=context.principal_ref,
                        identity=context.identity,
                        wire_scope=wire_scope,
                        service_context=context,
                    ),
                    intent=intent,
                    permit=permit,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    wire_scope=wire_scope,
                    service_context=context,
                    active_authority=self._authority,
                )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE") from None
        if receipt is not None and type(receipt) is not GroupLegOrderReceipt:
            raise ServiceExecutionGroupError("GROUP_RECEIPT_INVALID")
        return receipt

    def _validate_accepted_owner(
        self,
        binding: AcceptedGroupBinding,
        registration: ActiveRegistrationContext,
        origin_run_id: str,
    ) -> None:
        scope = registration.context.scope
        if (
            binding.principal_ref != registration.context.principal_ref
            or binding.identity != _identity_mapping(registration.context.identity)
            or binding.stable_scope != scope
            or binding.origin_run_id != origin_run_id
            or binding.account_ref != scope.account_ref
            or binding.portfolio_ref != scope.portfolio_ref
            or binding.strategy_id != scope.strategy_ref
        ):
            raise ServiceExecutionGroupError("GROUP_BINDING_SCOPE_MISMATCH")

    @staticmethod
    def _reservation_leg(binding: AcceptedGroupBinding, leg_id: str) -> GroupRiskLegSnapshot:
        if binding.reservation is None:
            raise ServiceExecutionGroupError("GROUP_BINDING_INCOMPLETE")
        matches = [leg for leg in binding.reservation.legs if leg.leg_id == leg_id]
        if len(matches) != 1:
            raise ServiceExecutionGroupError("GROUP_RESERVATION_MISMATCH")
        return matches[0]

    @staticmethod
    def _member_for_intent(binding: AcceptedGroupBinding, intent_id: str) -> OrderIntent:
        matches = [intent for _, intent in binding.members if intent.intent_id == intent_id]
        if len(matches) != 1:
            raise ServiceExecutionGroupError("GROUP_MEMBER_MISMATCH")
        return matches[0]

    def _permit_from_binding(
        self, binding: AcceptedGroupBinding, intent: OrderIntent
    ) -> GroupLegPermitReference:
        if binding.reservation is None:
            raise ServiceExecutionGroupError("GROUP_BINDING_INCOMPLETE")
        leg_id = _leg_id_for_intent(binding, intent.intent_id)
        risk_leg = self._reservation_leg(binding, leg_id)
        reservation = binding.reservation
        try:
            return GroupLegPermitReference(
                group_id=binding.group_id,
                leg_id=leg_id,
                portfolio_ref=binding.portfolio_ref,
                strategy_id=binding.strategy_id,
                account_ref=binding.account_ref,
                coordinator_principal_ref=binding.principal_ref,
                risk_scope_fingerprint=reservation.scope_fingerprint,
                permit_id=risk_leg.permit_id,
                group_fingerprint=reservation.group_fingerprint,
                policy_fingerprint=reservation.policy_fingerprint,
                reserved_intent_hash=risk_leg.reserved_intent_hash,
                exposure_profile_fingerprint=risk_leg.exposure_profile_fingerprint,
                order_intent_hash=intent.fingerprint,
                deadline_ns=binding.deadline_ns,
            )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_PERMIT_INVALID") from None

    def _target_for_binding(self, binding: AcceptedGroupBinding) -> ExecutionScope | None:
        target = self._targets.get(binding.stable_scope)
        if target is None or target.account_ref != binding.account_ref:
            return None
        return target


def _required_coordinator_call(account_ref: str) -> _CoordinatorCall:
    current = _CURRENT_COORDINATOR_CALL.get()
    if current is None or current.binding.request.account_refs.count(account_ref) == 0:
        raise RuntimeError("group coordinator call context is unavailable")
    route = current.target.accounts.get(account_ref)
    if route is None:
        raise RuntimeError("group account route is unavailable")
    return current


def _snapshot_response(response: object) -> GroupAccountReservationSnapshot:
    body = _rpc_result(response)
    try:
        return GroupAccountReservationSnapshot.from_payload(body)
    except Exception:
        raise RuntimeError("account reservation receipt is invalid") from None


def _order_receipt_response(response: object) -> GroupLegOrderReceipt:
    body = _rpc_result(response)
    try:
        receipt = GroupLegOrderReceipt.from_payload(body)
    except Exception:
        raise RuntimeError("account order receipt is invalid") from None
    return receipt


def _optional_order_receipt_response(response: object) -> GroupLegOrderReceipt | None:
    body = _rpc_result(response, allow_none=True)
    if body is None:
        return None
    try:
        return GroupLegOrderReceipt.from_payload(body)
    except Exception:
        raise RuntimeError("account order receipt is invalid") from None


def _rpc_result(response: object, *, allow_none: bool = False) -> object:
    if not isinstance(response, Mapping) or set(response) != {"ok", "code", "result"}:
        raise RuntimeError("group RPC reply is invalid")
    if response["ok"] is not True:
        code = response["code"]
        if type(code) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code):
            raise GroupReservationDeniedError(code)
        raise RuntimeError("group RPC was refused")
    if response["code"] != "OK":
        raise RuntimeError("group RPC success code is invalid")
    result = response["result"]
    if allow_none and result is None:
        return None
    if not isinstance(result, Mapping):
        raise RuntimeError("group RPC result is invalid")
    try:
        return _plain_rpc_json(result)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise RuntimeError("group RPC result is invalid") from None


def _plain_rpc_json(value: object, *, depth: int = 0) -> object:
    """Thaw a validated frozen wire value for strict JSON DTO decoders.

    ``WireEnvelope`` correctly freezes arrays as tuples and objects as mapping
    proxies. Group DTO ``from_payload`` methods consume JSON-native lists, so
    only this authenticated RPC receive boundary restores those containers.
    The DTO decoders still enforce their exact schemas and semantic checks.
    """

    if depth > _MAX_RPC_JSON_DEPTH:
        raise ValueError("group RPC result is too deeply nested")
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("group RPC result contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("group RPC result object keys must be strings")
            key.encode("utf-8", errors="strict")
            result[key] = _plain_rpc_json(item, depth=depth + 1)
        return result
    if type(value) in (tuple, list):
        return [_plain_rpc_json(item, depth=depth + 1) for item in value]
    raise TypeError("group RPC result contains a non-JSON value")


def _success(result: Mapping[str, object]) -> Mapping[str, object]:
    return {"ok": True, "code": "OK", "result": dict(result)}


def _success_value(result: object) -> Mapping[str, object]:
    return {"ok": True, "code": "OK", "result": result}


def _failure(code: str) -> Mapping[str, object]:
    if type(code) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code):
        code = "GROUP_REQUEST_FAILED"
    return {"ok": False, "code": code, "result": {}}


def _has_method(value: object, name: str) -> bool:
    try:
        return callable(getattr(value, name, None))
    except Exception:
        return False


def _is_context_manager(value: object) -> bool:
    return _has_method(value, "__enter__") and _has_method(value, "__exit__")


def _valid_dispatch_session(value: object) -> bool:
    try:
        from .service_execution_group_proofs import GroupProofDispatchSession

        return (
            type(value) is GroupProofDispatchSession
            and _has_method(value, "abort")
            and _has_method(value, "after_group_claim")
            and _has_method(value, "after_group_result")
            and _has_method(value, "finish")
            and _has_method(getattr(value, "provider_dispatcher", None), "submit")
        )
    except Exception:
        return False


def _owner_scope_template(scope: WireScope) -> bool:
    return (
        type(scope) is WireScope
        and type(scope.execution_domain_ref) is str
        and _REFERENCE.fullmatch(scope.execution_domain_ref) is not None
        and type(scope.account_ref) is str
        and _REFERENCE.fullmatch(scope.account_ref) is not None
        and scope.account_refs == ()
        and type(scope.strategy_ref) is str
        and _REFERENCE.fullmatch(scope.strategy_ref) is not None
        and type(scope.portfolio_ref) is str
        and _REFERENCE.fullmatch(scope.portfolio_ref) is not None
        and scope.run_id is None
        and scope.manual_book_ref is None
        and scope.group_id is None
        and scope.leg_id is None
    )


def _policy_key(
    policy: TrustedGroupWritePolicy | TrustedGroupResolutionPolicy,
) -> tuple[object, ...]:
    return (
        policy.instance_id,
        policy.principal_ref,
        policy.identity,
        policy.scope_ref,
        policy.scope,
        policy.run_id,
        policy.scope.account_ref,
    )


def _identity_mapping(identity: WireIdentity) -> Mapping[str, str | None]:
    return MappingProxyType(
        {
            "role": identity.role.value,
            "principal_ref": identity.principal_ref,
            "actor_ref": identity.actor_ref,
            "delegate_ref": identity.delegate_ref,
        }
    )


def _leg_id_for_intent(binding: AcceptedGroupBinding, intent_id: str) -> str:
    matches = [leg_id for leg_id, item in binding.members if item.intent_id == intent_id]
    if len(matches) != 1:
        raise ServiceExecutionGroupError("GROUP_MEMBER_MISMATCH")
    return matches[0]


def _same_reserved_order_except_quantity(reserved: OrderIntent, candidate: OrderIntent) -> bool:
    if type(reserved) is not OrderIntent or type(candidate) is not OrderIntent:
        return False
    if candidate.quantity <= 0 or candidate.quantity > reserved.quantity:
        return False
    try:
        return replace(reserved, quantity=candidate.quantity) == candidate
    except (TypeError, ValueError):
        return False


def _reference(value: object, error_code: str) -> str:
    if type(value) is not str or _REFERENCE.fullmatch(value) is None:
        raise ServiceExecutionGroupError(error_code)
    return value


def _coordinator_stable_scope(scope: WireScope) -> bool:
    return (
        type(scope) is WireScope
        and type(scope.execution_domain_ref) is str
        and _REFERENCE.fullmatch(scope.execution_domain_ref) is not None
        and scope.account_ref is None
        and bool(scope.account_refs)
        and tuple(sorted(set(scope.account_refs))) == tuple(scope.account_refs)
        and all(
            type(value) is str and _REFERENCE.fullmatch(value) is not None
            for value in scope.account_refs
        )
        and type(scope.strategy_ref) is str
        and _REFERENCE.fullmatch(scope.strategy_ref) is not None
        and scope.run_id is None
        and scope.manual_book_ref is None
        and type(scope.portfolio_ref) is str
        and _REFERENCE.fullmatch(scope.portfolio_ref) is not None
        and scope.group_id is None
        and scope.leg_id is None
    )


def _coordinator_call_scope(scope: WireScope) -> bool:
    return (
        _coordinator_stable_scope(replace(scope, run_id=None))
        and type(scope.run_id) is str
        and _REFERENCE.fullmatch(scope.run_id) is not None
    )


def _matches_only_run(template: WireScope, requested: WireScope) -> bool:
    if (
        type(template) is not WireScope
        or type(requested) is not WireScope
        or template.run_id is not None
        or requested.run_id is None
        or _REFERENCE.fullmatch(requested.run_id) is None
    ):
        return False
    return all(
        getattr(template, name) == getattr(requested, name)
        for name in (
            "execution_domain_ref",
            "account_ref",
            "account_refs",
            "strategy_ref",
            "manual_book_ref",
            "portfolio_ref",
            "group_id",
            "leg_id",
        )
    )


def _same_call(expected: ServiceCallContext, actual: ServiceCallContext) -> bool:
    return (
        expected.identity == actual.identity
        and expected.scope == actual.scope
        and expected.registration_id == actual.registration_id
        and expected.principal_ref == actual.principal_ref
    )


__all__ = [
    "GROUP_ACCOUNT_OWNER_OPERATIONS",
    "GROUP_COORDINATOR_OPERATIONS",
    "GroupAccountRoute",
    "GroupCoordinatorService",
    "GroupCoordinatorTarget",
    "GroupInstrumentRule",
    "ExecutionGroupOrderEntryService",
    "ServiceExecutionGroupError",
    "validate_service_options",
]
