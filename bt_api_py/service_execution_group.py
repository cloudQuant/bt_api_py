"""ServiceHost adapters for the durable Execution group workflow.

The coordinator accepts only authenticated strategy registrations and derives
run authority from the Registry snapshot.  The wire keeps the frozen two-leg
body and additionally accepts an explicit version-3 three-leg body (one
same-account Put/Future/Call chain with per-leg ratios).  Account-owner RPCs
are routed over the Host's borrowed upstream client; each account owner
independently binds a permit to its accepted reservation, current writer
lease, and durable group proof source.  Query operations never advance or
dispatch a group.
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
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    CancelDispatchClaimReceiptV1,
    CancelIntent,
    CancelRecord,
    ContractValidationError,
    ExecutionRecord,
    ExecutionState,
    ExecutionScope,
    GroupAccountReservationSnapshot,
    GroupBudgetReservationState,
    GroupBudgetQuote,
    GroupBudgetQuoteBuilder,
    GroupBudgetUnsupportedOrderError,
    GroupChildOrderStatus,
    GroupCompensationBudgetPolicy,
    GroupReservationPhase,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    GroupOrderCoordinator,
    GroupOrderIntent,
    GroupOrderLegPlan,
    GroupOrderSnapshot,
    GroupOrderState,
    GroupPermitBinding,
    GroupReservationDeniedError,
    GroupRiskLegSnapshot,
    ManagedCancellationFacade,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    PositionEffect,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    TimeInForce,
    group_leg_intent_id,
    order_intent_from_payload,
    payload_sha256,
)
from bt_api_execution.group_roll_exit_contracts import (
    GroupRollExitBudgetReleaseV1,
    GroupRollExitCloseActionV1,
    GroupRollExitCloseFeeEvidenceV1 as ExecutionGroupRollExitCloseFeeEvidenceV1,
    GroupRollExitCloseFeeFillV1 as ExecutionGroupRollExitCloseFeeFillV1,
    GroupRollExitClosedProofV1,
    GroupRollExitLegClosedV1,
    GroupRollExitSourceFeeEvidenceV1,
)
from bt_api_execution.group_roll_exit_validation import group_event_chain_sha256
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
    verify_accepted_group_projection,
)
from .service_execution_group_cancel import (
    GroupCancelPlanLeg,
    GroupCancelPlanError,
    GroupCancelPlanStore,
    group_cancel_leg_id,
)
from .service_group_compensation_contracts import (
    GroupCompensationActionPhase,
    GroupCompensationCloseAuthorization,
)
from .service_group_roll_exit import (
    GroupRollExitActionRecordV1,
    GroupRollExitActionStore,
    GroupRollExitAuthorizationV1,
    GroupRollExitCloseFeeEvidenceV1,
    GroupRollExitError,
    GroupRollExitObservationV1,
    GroupRollExitPhase,
    GroupRollExitRequestV1,
)
from .service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)

_GROUP_COORDINATOR_BASE_OPERATIONS = frozenset({"groups.submit", "groups.query"})
_GROUP_COORDINATOR_FULL_OPERATIONS = _GROUP_COORDINATOR_BASE_OPERATIONS | {"groups.cancel"}
_GROUP_COORDINATOR_ROLL_EXIT_OPERATIONS = frozenset({"groups.roll_exit", "groups.query_roll_exit"})
_GROUP_ROLL_EXIT_EVENT_TYPES = frozenset(
    {
        "GROUP_ROLL_EXIT_PLAN_RECORDED",
        "GROUP_ROLL_EXIT_SUBMIT_STARTED",
        "GROUP_ROLL_EXIT_STATUS_OBSERVED",
        "GROUP_ROLL_EXIT_RESOLVED",
        "GROUP_ROLL_EXIT_CLOSED",
    }
)
GROUP_COORDINATOR_OPERATIONS = _GROUP_COORDINATOR_FULL_OPERATIONS | _GROUP_COORDINATOR_ROLL_EXIT_OPERATIONS
_GROUP_COORDINATOR_WRITE_OPERATIONS = frozenset(
    {"groups.submit", "groups.cancel", "groups.roll_exit"}
)
_GROUP_ACCOUNT_OWNER_BASE_OPERATIONS = frozenset(
    {"orders.submit_group_leg", "orders.query_group_leg", "orders.resolve_group_leg"}
)
GROUP_ACCOUNT_OWNER_OPERATIONS = frozenset(
    {
        "orders.submit_group_leg",
        "orders.query_group_leg",
        "orders.resolve_group_leg",
        "orders.cancel_group_leg",
        "orders.query_group_cancel",
    }
)

GROUP_COORDINATOR_PORT = "service.order_group_entry"
GROUP_ORDER_ENTRY_PORT = "service.order_entry"
GROUP_ORDER_QUERY_PORT = "service.order_query"
_RESERVATION_MOUNT = "upstream.account_reservations"
_ORDERS_MOUNT = "upstream.orders"
_ORDER_QUERY_MOUNT = "upstream.order_query"
_COMPENSATION_ORDERS_MOUNT = "upstream.group_compensation_orders"
_COMPENSATION_QUERY_MOUNT = "upstream.group_compensation_order_query"
# Roll-exit operations use dedicated mounts so their exact service grants and
# scope bindings cannot be reached through ordinary orders/query bindings.
_ROLL_EXIT_ORDERS_MOUNT = "upstream.group_roll_exit_orders"
_ROLL_EXIT_QUERY_MOUNT = "upstream.group_roll_exit_order_query"
_MAX_REPLY_BYTES = 1_000_000
_MAX_RPC_JSON_DEPTH = 64
_MAX_SQLITE_INTEGER = (1 << 63) - 1
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_LEASE_TTL_NS = 5_000_000_000
_OWNER_RECOVERY_INTERVAL_NS = 1_000_000_000
_LEGACY_SUBMIT_KEYS = frozenset({"group_id", "deadline_ns", "first_leg_id", "legs"})
_VERSIONED_SUBMIT_KEYS = _LEGACY_SUBMIT_KEYS | {"version"}
_LEGACY_SUBMIT_LEG_KEYS = frozenset({"leg_id", "account_ref", "intent"})
_THREE_LEG_SUBMIT_LEG_KEYS = _LEGACY_SUBMIT_LEG_KEYS | {"ratio"}
_THREE_LEG_GROUP_VERSION = 3
_RATIO_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


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
    compensation_order_scope_ref: str | None = None
    compensation_query_scope_ref: str | None = None
    roll_exit_order_scope_ref: str | None = None
    roll_exit_query_scope_ref: str | None = None

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
        if (self.compensation_order_scope_ref is None) != (
            self.compensation_query_scope_ref is None
        ) or any(
            value is not None
            and (type(value) is not str or _REFERENCE.fullmatch(value) is None)
            for value in (
                self.compensation_order_scope_ref,
                self.compensation_query_scope_ref,
            )
        ):
            raise ServiceExecutionGroupError("GROUP_ROUTE_INVALID")
        if (self.roll_exit_order_scope_ref is None) != (
            self.roll_exit_query_scope_ref is None
        ) or any(
            value is not None
            and (type(value) is not str or _REFERENCE.fullmatch(value) is None)
            for value in (
                self.roll_exit_order_scope_ref,
                self.roll_exit_query_scope_ref,
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
    compensation_budget_policy: GroupCompensationBudgetPolicy | None = None

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
        if self.compensation_budget_policy is not None and (
            type(self.compensation_budget_policy) is not GroupCompensationBudgetPolicy
        ):
            raise ServiceExecutionGroupError("GROUP_COMPENSATION_BUDGET_CONFIGURATION_INVALID")
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
    registration: ActiveRegistrationContext | None
    target_scope: WireScope
    target: GroupCoordinatorTarget
    binding: GroupOriginRunBinding


class _AcceptedGroupCancelAdmissionGate:
    """Bind cancellation admission to one current accepted group child.

    Group cancellation reduces an already accepted OPEN order obligation. Its
    authority comes from the immutable account-owner group binding and exact
    local child record; this gate adds no caller-selected Risk permit.
    """

    def __init__(
        self,
        intent: CancelIntent,
        permit_reference: str,
        assert_current: Callable[[], None],
    ) -> None:
        self._intent = intent
        self._permit_reference = permit_reference
        self._assert_current = assert_current

    def _check(self, intent: CancelIntent, permit_reference: str | None = None) -> None:
        if intent != self._intent or (
            permit_reference is not None and permit_reference != self._permit_reference
        ):
            raise PermissionError("accepted group cancellation identity changed")
        self._assert_current()

    def reserve(self, intent: CancelIntent) -> str:
        self._check(intent)
        return self._permit_reference

    def validate(self, permit_reference: str, intent: CancelIntent) -> None:
        self._check(intent, permit_reference)

    def claim_before_dispatch(
        self, permit_reference: str, intent: CancelIntent
    ) -> CancelDispatchClaimReceiptV1:
        self._check(intent, permit_reference)
        return CancelDispatchClaimReceiptV1(
            permit_reference=permit_reference,
            scope_key=intent.scope.key,
            cancel_id=intent.cancel_id,
            intent_fingerprint=intent.fingerprint,
        )

    def settle(self, permit_reference: str) -> None:
        if permit_reference != self._permit_reference:
            raise PermissionError("accepted group cancellation permit changed")

    def release(self, permit_reference: str, _reason: str) -> None:
        if permit_reference != self._permit_reference:
            raise PermissionError("accepted group cancellation permit changed")


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

    def cancel_group_leg(
        self, *, group_id: str, cancel_request_id: str, leg_id: str
    ) -> Mapping[str, Any]:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _ORDERS_MOUNT,
            "orders.cancel_group_leg",
            {
                "origin_run_id": current.binding.origin_run_id,
                "group_id": group_id,
                "cancel_request_id": cancel_request_id,
                "leg_id": leg_id,
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.order_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _cancel_result_response(response)

    def query_group_cancel(
        self, *, group_id: str, cancel_request_id: str, cancel_id: str, leg_id: str
    ) -> Mapping[str, Any]:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        response = self._upstream.call(
            _ORDER_QUERY_MOUNT,
            "orders.query_group_cancel",
            {
                "origin_run_id": current.binding.origin_run_id,
                "group_id": group_id,
                "cancel_request_id": cancel_request_id,
                "cancel_id": cancel_id,
                "leg_id": leg_id,
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.query_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _cancel_result_response(response)

    def prepare_group_compensation_close(
        self, group_id: str, source_leg_id: str
    ) -> Mapping[str, Any]:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        if route.compensation_query_scope_ref is None:
            raise RuntimeError("compensation query route is not configured")
        response = self._upstream.call(
            _COMPENSATION_QUERY_MOUNT,
            "orders.prepare_group_compensation_close",
            {"group_id": group_id, "source_leg_id": source_leg_id},
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.compensation_query_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        body = _rpc_result(response)
        if set(body) != {"phase", "authorization", "status", "close_receipt"}:
            raise RuntimeError("compensation prepare reply is invalid")
        return body

    def submit_group_compensation_close(
        self, authorization: GroupCompensationCloseAuthorization
    ) -> Mapping[str, Any]:
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        if route.compensation_order_scope_ref is None:
            raise RuntimeError("compensation entry route is not configured")
        response = self._upstream.call(
            _COMPENSATION_ORDERS_MOUNT,
            "orders.submit_group_compensation_close",
            {"authorization": authorization.to_payload()},
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.compensation_order_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        body = _rpc_result(response)
        if set(body) != {"phase", "status", "close_receipt"}:
            raise RuntimeError("compensation submit reply is invalid")
        return body

    def query_group_compensation_close(
        self,
        *,
        authorization: GroupCompensationCloseAuthorization | None = None,
        group_id: str | None = None,
    ) -> Mapping[str, Any]:
        if (authorization is None) == (group_id is None):
            raise ValueError("exactly one compensation query selector is required")
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        if route.compensation_query_scope_ref is None:
            raise RuntimeError("compensation query route is not configured")
        if authorization is not None:
            arguments = {"authorization": authorization.to_payload()}
        else:
            arguments = {"group_id": group_id}
        response = self._upstream.call(
            _COMPENSATION_QUERY_MOUNT,
            "orders.query_group_compensation_close",
            arguments,
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.compensation_query_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        body = _rpc_result(response)
        return body

    def prepare_group_roll_exit(
        self, request: GroupRollExitRequestV1
    ) -> tuple[GroupRollExitPhase, GroupRollExitAuthorizationV1, GroupRollExitObservationV1 | None]:
        if type(request) is not GroupRollExitRequestV1:
            raise RuntimeError("typed roll-exit request is required")
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        if route.roll_exit_query_scope_ref is None:
            raise RuntimeError("roll-exit query route is not configured")
        response = self._upstream.call(
            _ROLL_EXIT_QUERY_MOUNT,
            "orders.prepare_group_roll_exit",
            request.to_payload(),
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.roll_exit_query_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        body = _rpc_result(response)
        if set(body) != {"phase", "authorization", "observation"}:
            raise RuntimeError("roll-exit prepare reply is invalid")
        try:
            phase = GroupRollExitPhase(body["phase"])
            authorization = GroupRollExitAuthorizationV1.from_payload(body["authorization"])
            observation = (
                None
                if body["observation"] is None
                else GroupRollExitObservationV1.from_payload(body["observation"])
            )
        except Exception:
            raise RuntimeError("roll-exit prepare reply is invalid") from None
        if (
            phase is GroupRollExitPhase.NOT_FOUND
            or authorization.request != request
            or (phase is GroupRollExitPhase.PREPARED and observation is not None)
            or (phase is not GroupRollExitPhase.PREPARED and observation is None)
            or (
                observation is not None
                and not _roll_exit_observation_matches(authorization, phase, observation)
            )
        ):
            raise RuntimeError("roll-exit prepare identity is inconsistent")
        return phase, authorization, observation

    def submit_group_roll_exit(
        self, authorization: GroupRollExitAuthorizationV1
    ) -> GroupRollExitObservationV1:
        if type(authorization) is not GroupRollExitAuthorizationV1:
            raise RuntimeError("typed roll-exit authorization is required")
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        if route.roll_exit_order_scope_ref is None:
            raise RuntimeError("roll-exit entry route is not configured")
        response = self._upstream.call(
            _ROLL_EXIT_ORDERS_MOUNT,
            "orders.submit_group_roll_exit",
            {"authorization": authorization.to_payload()},
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.roll_exit_order_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        return _roll_exit_observation_response(response, authorization)

    def query_group_roll_exit(
        self, authorization: GroupRollExitAuthorizationV1
    ) -> GroupRollExitObservationV1 | None:
        if type(authorization) is not GroupRollExitAuthorizationV1:
            raise RuntimeError("typed roll-exit authorization is required")
        current = _required_coordinator_call(self._account_ref)
        route = current.target.accounts[self._account_ref]
        if route.roll_exit_query_scope_ref is None:
            raise RuntimeError("roll-exit query route is not configured")
        response = self._upstream.call(
            _ROLL_EXIT_QUERY_MOUNT,
            "orders.query_group_roll_exit",
            {
                "action_id": authorization.action_id,
                "authorization_sha256": authorization.authorization_sha256,
            },
            account_ref=self._account_ref,
            portfolio_ref=current.binding.request.portfolio_ref,
            scope_ref=route.roll_exit_query_scope_ref,
            max_reply_message_bytes=_MAX_REPLY_BYTES,
        )
        body = _rpc_result(response)
        if set(body) != {"phase", "observation"}:
            raise RuntimeError("roll-exit query reply is invalid")
        try:
            phase = GroupRollExitPhase(body["phase"])
            observation = (
                None
                if body["observation"] is None
                else GroupRollExitObservationV1.from_payload(body["observation"])
            )
        except Exception:
            raise RuntimeError("roll-exit query reply is invalid") from None
        if phase is GroupRollExitPhase.NOT_FOUND:
            if observation is not None:
                raise RuntimeError("roll-exit NOT_FOUND reply carried an observation")
            return None
        if phase is GroupRollExitPhase.PREPARED and observation is None:
            # A Risk action may exist durably before any submit observation.
            # Project that read-only state from the exact authorization sent
            # with the query; do not turn it into NOT_FOUND. Any guarded submit
            # belongs to the separate explicit recovery path, not this query.
            return GroupRollExitObservationV1(
                action_id=authorization.action_id,
                phase=phase,
                source_digest=authorization.source.digest,
                request_fingerprint=authorization.request.fingerprint,
                authorization_sha256=authorization.authorization_sha256,
                intent_id=authorization.intent.intent_id,
                close_state=None,
                requested_quantity=authorization.intent.quantity,
                filled_quantity=Decimal(0),
                order_revision=None,
                close_receipt_sha256=None,
                detail_code=None,
                risk_intent_id=authorization.risk_intent_id,
            )
        if observation is None or not _roll_exit_observation_matches(
            authorization, phase, observation
        ):
            raise RuntimeError("roll-exit query identity is inconsistent")
        return observation


def _roll_exit_observation_matches(
    authorization: GroupRollExitAuthorizationV1,
    phase: GroupRollExitPhase,
    observation: GroupRollExitObservationV1,
) -> bool:
    return (
        type(authorization) is GroupRollExitAuthorizationV1
        and type(observation) is GroupRollExitObservationV1
        and observation.phase is phase
        and observation.action_id == authorization.action_id
        and observation.source_digest == authorization.source.digest
        and observation.request_fingerprint == authorization.request.fingerprint
        and observation.authorization_sha256 == authorization.authorization_sha256
        and observation.intent_id == authorization.intent.intent_id
        and observation.risk_intent_id == authorization.risk_intent_id
        and observation.requested_quantity == authorization.intent.quantity
    )


def _execution_roll_exit_close_fee_evidence(
    authorization: GroupRollExitAuthorizationV1,
    observation: GroupRollExitObservationV1,
) -> ExecutionGroupRollExitCloseFeeEvidenceV1 | None:
    """Adapt the raw Risk fee projection to Execution's canonical proof DTO."""

    fee = observation.close_fee_evidence
    if (
        type(authorization) is not GroupRollExitAuthorizationV1
        or type(observation) is not GroupRollExitObservationV1
        or type(fee) is not GroupRollExitCloseFeeEvidenceV1
        or authorization.risk_intent_id is None
        or authorization.intent.intent_id != authorization.action_id
        or fee.intent_id != authorization.action_id
    ):
        return None
    try:
        return ExecutionGroupRollExitCloseFeeEvidenceV1(
            intent_id=authorization.action_id,
            journal_revision=fee.journal_revision,
            fill_observation_sha256=fee.fill_observation_sha256,
            fee_amount=fee.fee_amount,
            fee_currency=fee.fee_currency,
            fills=tuple(
                ExecutionGroupRollExitCloseFeeFillV1(
                    fill_id=item.fill_id,
                    fill_fingerprint=item.fill_fingerprint,
                    source_row_sha256=item.source_row_sha256,
                    amount=item.amount,
                    currency=item.currency,
                )
                for item in fee.fills
            ),
        )
    except Exception:
        return None


def _roll_exit_action_has_closure_evidence(action: Mapping[str, Any]) -> bool:
    """Return whether a terminal action can be included in a CLOSED proof."""

    authorization = action.get("authorization")
    observation = action.get("observation")
    if (
        action.get("phase") != "RESOLVED"
        or type(authorization) is not GroupRollExitAuthorizationV1
        or type(observation) is not GroupRollExitObservationV1
        or observation.phase is not GroupRollExitPhase.RESOLVED
        or observation.close_state is not ExecutionState.FILLED
        or observation.close_receipt is None
        or observation.filled_quantity != authorization.intent.quantity
        or not _roll_exit_observation_matches(
            authorization, observation.phase, observation
        )
        or type(authorization.source.source_terminal_status) is not GroupChildOrderStatus
    ):
        return False
    fee = _execution_roll_exit_close_fee_evidence(authorization, observation)
    source_status = authorization.source.source_terminal_status
    return (
        fee is not None
        and source_status.cumulative_commission_currency is not None
        and fee.fee_currency == source_status.cumulative_commission_currency
    )


def _roll_exit_source_matches_snapshot(
    authorization: GroupRollExitAuthorizationV1,
    snapshot: GroupOrderSnapshot,
    *,
    expected_plan_revision: int,
) -> bool:
    """Bind a Risk-built action to the accepted, unchanged source leg."""

    if (
        type(authorization) is not GroupRollExitAuthorizationV1
        or type(snapshot) is not GroupOrderSnapshot
        or type(expected_plan_revision) is not int
        or expected_plan_revision < 0
        or authorization.risk_intent_id is None
        or snapshot.request.version not in {1, 2}
        or len(snapshot.request.legs) != 2
        or snapshot.request.compensation_budget_policy is not None
        or authorization.request.portfolio_ref != snapshot.request.portfolio_ref
        or authorization.request.group_id != snapshot.request.group_id
        or authorization.source.portfolio_ref != snapshot.request.portfolio_ref
        or authorization.source.group_id != snapshot.request.group_id
        or authorization.source.group_version != snapshot.request.version
        or authorization.source.group_revision != expected_plan_revision
        or authorization.source.group_fingerprint != snapshot.request.fingerprint
        or authorization.source.group_request != snapshot.request
        or authorization.source.strategy_id != snapshot.request.strategy_id
        or authorization.request.source_leg_id != authorization.source.source_leg_id
    ):
        return False
    matches = tuple(
        plan
        for plan in snapshot.request.legs
        if plan.leg_id == authorization.source.source_leg_id
    )
    if len(matches) != 1:
        return False
    plan = matches[0]
    progress_matches = tuple(item for item in snapshot.legs if item.leg_id == plan.leg_id)
    if len(progress_matches) != 1:
        return False
    progress = progress_matches[0]
    status = progress.child_status
    source = authorization.source
    risk_reservation_fingerprint = getattr(
        source, "source_risk_reservation_fingerprint", None
    )
    risk_intent_fingerprint = getattr(source, "source_risk_intent_fingerprint", None)
    matching_reservations = tuple(
        reservation
        for reservation in snapshot.reservations
        if type(reservation) is GroupAccountReservationSnapshot
        and reservation.account_ref == source.account_ref
        and reservation.group_id == snapshot.request.group_id
    )
    if len(matching_reservations) != 1:
        return False
    reservation = matching_reservations[0]
    expected_account_leg_ids = tuple(
        item.leg_id
        for item in snapshot.request.legs
        if item.account_ref == source.account_ref
    )
    reservation_leg_ids = tuple(item.leg_id for item in reservation.legs)
    if (
        not expected_account_leg_ids
        or len(set(expected_account_leg_ids)) != len(expected_account_leg_ids)
        or len(reservation_leg_ids) != len(expected_account_leg_ids)
        or len(set(reservation_leg_ids)) != len(reservation_leg_ids)
        or reservation_leg_ids != tuple(sorted(expected_account_leg_ids))
    ):
        return False
    matching_risk_legs = tuple(
        risk_leg
        for risk_leg in reservation.legs
        if type(risk_leg) is GroupRiskLegSnapshot
        and risk_leg.leg_id == source.source_leg_id
    )
    if len(matching_risk_legs) != 1:
        return False
    risk_leg = matching_risk_legs[0]
    claim = source.source_claim
    if (
        type(risk_reservation_fingerprint) is not str
        or type(risk_intent_fingerprint) is not str
        or reservation.group_fingerprint != risk_reservation_fingerprint
        or risk_leg.actual_intent_hash != risk_intent_fingerprint
        or risk_leg.permit_id != claim.permit.permit_id
        or risk_leg.intent_id != source.source_child_intent.intent_id
        or risk_leg.reserved_intent_hash != claim.permit.reserved_intent_hash
        or risk_leg.exposure_profile_fingerprint
        != claim.permit.exposure_profile_fingerprint
        or not risk_leg.claimed
        or not risk_leg.resolved
        or claim.actual_child_intent_hash != risk_intent_fingerprint
        or claim.permit.group_fingerprint != risk_reservation_fingerprint
        or claim.permit.order_intent_hash != source.source_child_intent.fingerprint
        or claim.permit.group_id != snapshot.request.group_id
        or claim.permit.portfolio_ref != snapshot.request.portfolio_ref
        or claim.permit.strategy_id != snapshot.request.strategy_id
        or claim.permit.coordinator_principal_ref
        != snapshot.request.coordinator_principal_ref
        or claim.permit.account_ref != source.account_ref
        or claim.permit.leg_id != source.source_leg_id
    ):
        return False
    return (
        plan.account_ref == source.account_ref
        and plan.maximum_intent.intent_id == source.source_intent_id
        and progress.phase.value == "OBSERVED"
        and type(progress.submitted_intent) is OrderIntent
        and progress.submitted_intent == source.source_child_intent
        and progress.submitted_intent.intent_id == source.source_intent_id
        and progress.submitted_intent.fingerprint == source.source_request_fingerprint
        and type(status) is GroupChildOrderStatus
        and status == source.source_terminal_status
        and status.cumulative_commission is not None
        and status.cumulative_commission_currency is not None
        and progress.claim_receipt == source.source_claim
        and progress.resolution_receipt == source.source_risk_resolution
        and source.source_filled_quantity == status.filled_quantity
        and source.source_event_digest is not None
    )


def _roll_exit_authorization_matches_snapshot(
    authorization: GroupRollExitAuthorizationV1,
    snapshot: GroupOrderSnapshot,
    binding: GroupOriginRunBinding,
    target_route: Any,
    *,
    expected_plan_revision: int,
) -> bool:
    if (
        type(binding) is not GroupOriginRunBinding
        or not _roll_exit_source_matches_snapshot(
            authorization,
            snapshot,
            expected_plan_revision=expected_plan_revision,
        )
    ):
        return False
    source = authorization.source
    route_scope = getattr(target_route, "execution_scope", None)
    risk_reservations = tuple(
        reservation
        for reservation in snapshot.reservations
        if type(reservation) is GroupAccountReservationSnapshot
        and reservation.account_ref == source.account_ref
        and reservation.group_id == snapshot.request.group_id
    )
    return (
        type(target_route) is GroupAccountRoute
        and len(risk_reservations) == 1
        and risk_reservations[0].scope_fingerprint
        == target_route.risk_scope_fingerprint
        and source.origin_run_id == binding.origin_run_id
        and source.service_write_run_id == binding.origin_run_id
        and source.strategy_id == binding.request.strategy_id
        and source.account_ref in binding.request.account_refs
        and source.account_ref == authorization.source.source_execution_scope.account_ref
        and source.source_execution_scope == route_scope
        and authorization.request == GroupRollExitRequestV1(
            portfolio_ref=snapshot.request.portfolio_ref,
            group_id=snapshot.request.group_id,
            source_leg_id=source.source_leg_id,
            limit_price=authorization.request.limit_price,
        )
    )


def _roll_exit_observation_response(
    response: object, authorization: GroupRollExitAuthorizationV1
) -> GroupRollExitObservationV1:
    body = _rpc_result(response)
    if set(body) != {"phase", "observation"}:
        raise RuntimeError("roll-exit submit reply is invalid")
    try:
        phase = GroupRollExitPhase(body["phase"])
        observation = GroupRollExitObservationV1.from_payload(body["observation"])
    except Exception:
        raise RuntimeError("roll-exit submit reply is invalid") from None
    if phase in {GroupRollExitPhase.PREPARED, GroupRollExitPhase.NOT_FOUND} or not _roll_exit_observation_matches(
        authorization, phase, observation
    ):
        raise RuntimeError("roll-exit submit identity is inconsistent")
    return observation


class GroupCoordinatorService:
    """Normal ServiceHost entry for durable, ordered two-leg and v3 group requests."""

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
        try:
            self._roll_exit_actions = GroupRollExitActionStore(run_bindings)
        except GroupRollExitError:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None
        self._cancel_plans = GroupCancelPlanStore(run_bindings)
        self._upstream = borrowed_upstream
        self._identity = service_identity
        self._instance_id = instance_id
        self._group_admission = group_admission
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._closed = False
        self._registrations: Mapping[str, ActiveRegistrationContext] = MappingProxyType({})
        self._owner_recovery_cursor: tuple[int, str, str] | None = None
        self._owner_recovery_last_tick_ns = 0
        self._owner_recovery_lock = threading.Lock()
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
        self._reservation_ports = MappingProxyType(reservations)
        self._order_ports = MappingProxyType(orders)
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
        self._order_ports = MappingProxyType(orders)
        self._targets = MappingProxyType(target_copy)

    @property
    def supported_operations(self) -> frozenset[str]:
        return self.SUPPORTED_OPERATIONS

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._closed and bool(self._targets)

    def maintain_registrations(
        self,
        contexts: tuple[ActiveRegistrationContext, ...],
        *,
        recover: bool = True,
    ) -> None:
        if type(recover) is not bool:
            raise ServiceExecutionGroupError("REGISTRATION_RECOVERY_MODE_INVALID")
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
        if recover:
            self._maintain_one_recoverable_group()

    def _maintain_one_recoverable_group(self) -> None:
        """Advance at most one durably accepted group from the Host owner loop."""

        if not self._owner_recovery_lock.acquire(blocking=False):
            return
        try:
            now_ns = time.monotonic_ns()
            with self._lock:
                if self._closed or now_ns - self._owner_recovery_last_tick_ns < (
                    _OWNER_RECOVERY_INTERVAL_NS
                ):
                    return
                self._owner_recovery_last_tick_ns = now_ns
                after = self._owner_recovery_cursor

            page = self._store.list_recoverable_page(after=after, limit=1)
            if type(page) is not tuple or len(page) > 1:
                raise ServiceExecutionGroupError("GROUP_RECOVERY_PAGE_INVALID")
            if not page:
                # An earlier key may become UNWINDING after this cursor passed
                # it. Wrap on the next throttled owner tick so that transition
                # is eventually discovered without scanning the full table.
                with self._lock:
                    self._owner_recovery_cursor = None
                return

            listed = page[0]
            if type(listed) is not GroupOrderSnapshot:
                raise ServiceExecutionGroupError("GROUP_RECOVERY_PAGE_INVALID")
            request = listed.request
            key = (listed.created_at_ns, request.portfolio_ref, request.group_id)
            with self._lock:
                self._owner_recovery_cursor = key

            # Re-read the one selected row after paging. State can change
            # between the bounded list and this point; terminal rows need no
            # work, while every other state still uses the stored group id.
            snapshot = self._store.get(request.portfolio_ref, request.group_id)
            if type(snapshot) is not GroupOrderSnapshot or snapshot.created_at_ns != key[0]:
                return
            if snapshot.state in {
                GroupOrderState.PAIRED,
                GroupOrderState.CLOSED,
                GroupOrderState.REJECTED_BEFORE_DISPATCH,
            }:
                return

            binding = self._run_bindings.get(request.portfolio_ref, request.group_id)
            if type(binding) is not GroupOriginRunBinding:
                # A pre-accept or corrupt/incomplete row cannot create service
                # authority. Leave its durable state for diagnosis/retry.
                return
            target_scope = self._target_scope_for(binding.strategy_scope)
            if target_scope is None:
                return
            target = self._targets.get(target_scope)
            if target is None:
                return
            binding = self._bind_verified_accepted_snapshot(binding, snapshot)
            if binding is None or not self._recovery_binding_matches(
                binding, snapshot, target_scope, target
            ):
                return

            # This is continuation of an accepted snapshot under its stored
            # origin binding. Stored Registry policy/requirements versions and
            # its lease epoch are provenance; expiry does not revoke this
            # accepted obligation or create a new write authorization.
            call = _CoordinatorCall(None, target_scope, target, binding)
            token = _CURRENT_COORDINATOR_CALL.set(call)
            try:
                history = self._store.events(request.portfolio_ref, request.group_id)
                if any(
                    event.event_type == "GROUP_COMPENSATION_PLAN_RECORDED"
                    for event in history
                ):
                    # Once the immutable close plan is durable, this group is
                    # owned by the compensation state machine.  Re-running the
                    # source coordinator can overwrite its UNWINDING progress
                    # with RECOVERY_REQUIRED and prevent a verified receipt
                    # from being projected.  Resume only the same persisted
                    # action (or plan the next bounded action) through its
                    # dedicated query/submit rules.
                    self._maintain_group_compensation(snapshot)
                    return
                if any(event.event_type in _GROUP_ROLL_EXIT_EVENT_TYPES for event in history):
                    # A durable roll-exit plan owns this legacy two-leg group
                    # until its terminal CLOSE proof is committed. Normal
                    # advance_group recovery would otherwise rewrite the
                    # state-machine progress before the same Risk action is
                    # queried and resolved.
                    self._maintain_group_roll_exit(
                        snapshot, binding, target_scope, target
                    )
                    return
                advanced = self._coordinator.advance_group(
                    request.portfolio_ref, request.group_id
                )
                if (
                    type(advanced) is GroupOrderSnapshot
                    and advanced.state
                    in {GroupOrderState.RECOVERY_REQUIRED, GroupOrderState.UNWINDING}
                    and advanced.request.version == 3
                    and advanced.request.compensation_budget_policy is not None
                ):
                    current = self._store.get(request.portfolio_ref, request.group_id)
                    if type(current) is GroupOrderSnapshot:
                        self._maintain_group_compensation(current)
            finally:
                _CURRENT_COORDINATOR_CALL.reset(token)
        finally:
            self._owner_recovery_lock.release()

    def _recovery_binding_matches(
        self,
        binding: GroupOriginRunBinding,
        snapshot: GroupOrderSnapshot,
        target_scope: WireScope,
        target: GroupCoordinatorTarget,
    ) -> bool:
        request = binding.request
        scope = binding.strategy_scope
        identity = binding.strategy_identity
        accepted_fingerprint = binding.accepted_request_fingerprint
        if accepted_fingerprint is None and snapshot.request.fingerprint == binding.request_fingerprint:
            # Unquoted legacy rows already carry the full accepted request in
            # the original binding. Quoted groups require a separate full
            # fingerprint recovered from their durable accept event.
            accepted_fingerprint = binding.request_fingerprint
        return (
            type(binding) is GroupOriginRunBinding
            and type(snapshot) is GroupOrderSnapshot
            and type(request) is GroupOrderIntent
            and type(scope) is WireScope
            and self._target_scope_for(scope) == target_scope
            and _coordinator_call_scope(scope)
            and request.fingerprint == binding.request_fingerprint
            and snapshot.request.unquoted_fingerprint == request.unquoted_fingerprint
            and accepted_fingerprint is not None
            and snapshot.request.fingerprint == accepted_fingerprint
            and snapshot.request.portfolio_ref == request.portfolio_ref
            and snapshot.request.group_id == request.group_id
            and snapshot.request.coordinator_principal_ref == self._identity.principal_ref
            and binding.coordinator_principal_ref == self._identity.principal_ref
            and binding.instance_id == self._instance_id
            and binding.scope_ref == target.scope_ref
            and binding.origin_run_id == scope.run_id
            and scope.run_id is not None
            and scope.portfolio_ref == request.portfolio_ref
            and scope.strategy_ref == request.strategy_id
            and tuple(scope.account_refs) == request.account_refs
            and all(account_ref in target.accounts for account_ref in request.account_refs)
            and all(leg.account_ref in target.accounts for leg in request.legs)
            and target.portfolio_budget_policy is not None
            and target.budget_quote_builder is not None
            and identity.get("role") == WireRole.STRATEGY_CLIENT.value
            and identity.get("principal_ref") == binding.strategy_principal_ref
            and identity.get("actor_ref") == request.strategy_id
            and (
                identity.get("delegate_ref") is None
                or (
                    type(identity.get("delegate_ref")) is str
                    and _REFERENCE.fullmatch(identity["delegate_ref"]) is not None
                )
            )
        )

    def _bind_verified_accepted_snapshot(
        self,
        binding: GroupOriginRunBinding,
        snapshot: GroupOrderSnapshot,
    ) -> GroupOriginRunBinding | None:
        """Persist server-owned quote identity from the hashed accept event."""

        if type(binding) is not GroupOriginRunBinding or type(snapshot) is not GroupOrderSnapshot:
            return None
        try:
            history = self._store.events(
                snapshot.request.portfolio_ref, snapshot.request.group_id
            )
            accepted_fingerprint = verify_accepted_group_projection(binding, snapshot, history)
        except Exception:
            return None
        try:
            return self._run_bindings.bind_accepted_request_fingerprint(
                binding, accepted_fingerprint
            )
        except (AcceptedGroupBindingError, AcceptedGroupConflictError):
            return None

    def _maintain_group_compensation(self, snapshot: GroupOrderSnapshot) -> None:
        """Advance one accepted v3 compensation action from the owner tick.

        The group cursor already limits this method to one accepted group per
        tick.  Its durable marker is written before the only submit route; all
        later phases use the dedicated query route.  Any incomplete or
        contradictory evidence leaves the snapshot nonterminal for a later
        bounded retry.
        """

        request = snapshot.request
        if (
            snapshot.state not in {GroupOrderState.RECOVERY_REQUIRED, GroupOrderState.UNWINDING}
            or request.version != 3
            or request.compensation_budget_policy is None
            or len(request.legs) != 3
            or not snapshot.reservations
        ):
            return
        account_ref = request.account_refs[0]
        order_port = self._order_ports.get(account_ref)
        reservation_port = self._reservation_ports.get(account_ref)
        if order_port is None or reservation_port is None:
            return
        try:
            history = self._store.events(request.portfolio_ref, request.group_id)
            actions = _owner_compensation_actions(history)
            pending = [
                item
                for item in actions
                if item["phase"] is not GroupCompensationActionPhase.RESOLVED
            ]
            if len(pending) > 1:
                return
            if pending:
                self._advance_pending_compensation(snapshot, order_port, pending[0])
                return

            # The source chain must be terminal before we cancel the original
            # obligation and start CLOSE work. If a leg was never submitted,
            # allow the normal chain to progress until its original deadline.
            if not _group_source_is_compensation_ready(snapshot):
                return
            exposure = _owner_signed_filled_exposure(snapshot, actions)
            if exposure is None:
                return
            if any(value != 0 for value in exposure.values()):
                selected_leg_id = next(
                    plan.leg_id
                    for plan in request.legs
                    if exposure[plan.leg_id] != 0
                )
                prepared = order_port.prepare_group_compensation_close(
                    request.group_id, selected_leg_id
                )
                if (
                    prepared.get("phase") != "PREPARED"
                    or not isinstance(prepared.get("authorization"), Mapping)
                    or prepared.get("status") is not None
                    or prepared.get("close_receipt") is not None
                ):
                    return
                authorization = GroupCompensationCloseAuthorization.from_payload(
                    prepared["authorization"]
                )
                if (
                    authorization.group_id != request.group_id
                    or authorization.group_fingerprint != request.fingerprint
                    or authorization.source_leg_id != selected_leg_id
                    or Fraction(authorization.source_uncompensated_position_base_units)
                    != exposure[selected_leg_id]
                    or tuple(authorization.source_filled_position_exposure_vector_base_units)
                    != tuple(
                        (plan.leg_id, _fraction_decimal(exposure[plan.leg_id]))
                        for plan in request.legs
                    )
                ):
                    return
                payload = authorization.to_payload()
                self._store.update(
                    snapshot,
                    event_type="GROUP_COMPENSATION_PLAN_RECORDED",
                    event_payload={
                        "action_id": authorization.action_id,
                        "authorization_digest": authorization.digest,
                        "authorization": payload,
                    },
                    state=GroupOrderState.UNWINDING,
                    error_code=None,
                )
                return

            if not actions:
                # This route is reserved for proven CLOSE compensation; the
                # separate no-exposure terminal event owns no-fill groups.
                return
            if not _group_has_filled_source(snapshot):
                return
            snapshot = self._release_compensation_reservation(
                snapshot, reservation_port
            )
            if snapshot is None or snapshot.reservations[0].phase is not GroupReservationPhase.RELEASED:
                return
            closed = order_port.query_group_compensation_close(group_id=request.group_id)
            receipt = closed.get("closure_receipt")
            facts_digest = closed.get("closure_facts_digest")
            receipt_digest = closed.get("closure_receipt_sha256")
            if (
                closed.get("phase") != "CLOSED"
                or not isinstance(receipt, Mapping)
                or type(facts_digest) is not str
                or _SHA256.fullmatch(facts_digest) is None
                or receipt.get("closure_facts_digest") != facts_digest
                or type(receipt_digest) is not str
                or _SHA256.fullmatch(receipt_digest) is None
                or payload_sha256(dict(receipt)) != receipt_digest
            ):
                return
            self._store.update(
                snapshot,
                event_type="GROUP_COMPENSATION_CLOSED",
                event_payload=receipt,
                state=GroupOrderState.CLOSED,
                error_code=None,
            )
        except Exception:
            # A missing route, uncertain reply, malformed receipt, or stale
            # source projection never becomes a dispatch retry or a release.
            return

    def _maintain_group_roll_exit(
        self,
        snapshot: GroupOrderSnapshot,
        binding: GroupOriginRunBinding,
        target_scope: WireScope,
        target: GroupCoordinatorTarget,
    ) -> None:
        """Recover one persisted v1/v2 roll-exit action without normal dispatch."""

        request = snapshot.request
        if (
            snapshot.state not in {GroupOrderState.UNWINDING, GroupOrderState.RECOVERY_REQUIRED}
            or request.version not in {1, 2}
            or len(request.legs) != 2
        ):
            return
        try:
            if not self._recovery_binding_matches(binding, snapshot, target_scope, target):
                return
            history = self._store.events(request.portfolio_ref, request.group_id)
            actions = _owner_roll_exit_actions(snapshot, history)
            pending = [item for item in actions if item["phase"] != "RESOLVED"]
            if len(pending) > 1:
                return
            if pending:
                action = pending[0]
                authorization = action["authorization"]
                account_ref = authorization.source.account_ref
                if (
                    account_ref not in target.accounts
                    or authorization.source.origin_run_id != binding.origin_run_id
                    or authorization.source.strategy_id != snapshot.request.strategy_id
                    or authorization.source.group_request != snapshot.request
                    or authorization.source.group_fingerprint != snapshot.request.fingerprint
                    or authorization.source.service_write_run_id != binding.origin_run_id
                    or authorization.source.source_execution_scope
                    != target.accounts[account_ref].execution_scope
                ):
                    return
                record = self._roll_exit_actions.get(authorization.action_id)
                if record is None:
                    # The group event is the durable authority. Rebuild only
                    # the local query index from that verified plan; this does
                    # not cross the Risk submit boundary.
                    record = self._roll_exit_actions.prepare(authorization)
                if (
                    record is None
                    or record.authorization.to_payload() != authorization.to_payload()
                    or record.authorization.authorization_sha256
                    != action["authorization_sha256"]
                ):
                    return
                if account_ref not in self._order_ports:
                    return
                record = self._recover_roll_exit(
                    snapshot,
                    None,
                    target_scope,
                    target,
                    binding,
                    account_ref,
                    record,
                )
                current = self._store.get(request.portfolio_ref, request.group_id)
                if type(current) is GroupOrderSnapshot and record.observation is not None:
                    current = self._record_roll_exit_group_observation(
                        current, record.authorization, record.observation
                    )
                    if current is not None and record.phase is GroupRollExitPhase.RESOLVED:
                        self._try_close_roll_exit_group(current)
                return
            self._try_close_roll_exit_group(snapshot)
        except Exception:
            # Recovery never converts incomplete evidence into another action.
            return

    def _record_roll_exit_plan(
        self,
        snapshot: GroupOrderSnapshot,
        authorization: GroupRollExitAuthorizationV1,
    ) -> GroupOrderSnapshot | None:
        if (
            type(snapshot) is not GroupOrderSnapshot
            or type(authorization) is not GroupRollExitAuthorizationV1
            or authorization.risk_intent_id is None
            or authorization.source.group_revision != snapshot.revision
            or authorization.source.group_fingerprint != snapshot.request.fingerprint
            or authorization.source.group_request != snapshot.request
            or snapshot.state not in {
                GroupOrderState.PAIRED,
                GroupOrderState.UNWINDING,
                GroupOrderState.RECOVERY_REQUIRED,
            }
        ):
            return None
        history = self._store.events(snapshot.request.portfolio_ref, snapshot.request.group_id)
        actions = _owner_roll_exit_actions(snapshot, history)
        existing = next(
            (item for item in actions if item["action_id"] == authorization.action_id),
            None,
        )
        if existing is not None:
            if (
                existing["authorization_sha256"] != authorization.authorization_sha256
                or existing["authorization"].to_payload() != authorization.to_payload()
            ):
                return None
            return snapshot
        if any(item["phase"] != "RESOLVED" for item in actions):
            return None
        try:
            return self._store.update(
                snapshot,
                event_type="GROUP_ROLL_EXIT_PLAN_RECORDED",
                event_payload={
                    "action_id": authorization.action_id,
                    "authorization_sha256": authorization.authorization_sha256,
                    "authorization": authorization.to_payload(),
                },
                state=GroupOrderState.UNWINDING,
                error_code=None,
            )
        except Exception:
            return None

    def _record_roll_exit_submit_started(
        self,
        snapshot: GroupOrderSnapshot,
        authorization: GroupRollExitAuthorizationV1,
    ) -> GroupOrderSnapshot | None:
        if type(snapshot) is not GroupOrderSnapshot or authorization.risk_intent_id is None:
            return None
        history = self._store.events(snapshot.request.portfolio_ref, snapshot.request.group_id)
        actions = _owner_roll_exit_actions(snapshot, history)
        action = next((item for item in actions if item["action_id"] == authorization.action_id), None)
        if action is None or action["authorization_sha256"] != authorization.authorization_sha256:
            return None
        if action["phase"] in {"STARTED", "OBSERVED", "RESOLVED"}:
            return snapshot
        try:
            return self._store.update(
                snapshot,
                event_type="GROUP_ROLL_EXIT_SUBMIT_STARTED",
                event_payload={
                    "action_id": authorization.action_id,
                    "authorization_sha256": authorization.authorization_sha256,
                    "risk_intent_id": authorization.risk_intent_id,
                    "intent_id": authorization.intent.intent_id,
                    "client_order_id": authorization.client_order_id,
                },
                state=GroupOrderState.UNWINDING,
                error_code=None,
            )
        except Exception:
            return None

    def _record_roll_exit_group_observation(
        self,
        snapshot: GroupOrderSnapshot,
        authorization: GroupRollExitAuthorizationV1,
        observation: GroupRollExitObservationV1,
    ) -> GroupOrderSnapshot | None:
        if (
            type(snapshot) is not GroupOrderSnapshot
            or not _roll_exit_observation_matches(authorization, observation.phase, observation)
        ):
            return None
        history = self._store.events(snapshot.request.portfolio_ref, snapshot.request.group_id)
        actions = _owner_roll_exit_actions(snapshot, history)
        action = next((item for item in actions if item["action_id"] == authorization.action_id), None)
        if action is None or action["authorization_sha256"] != authorization.authorization_sha256:
            return None
        if action["phase"] == "RESOLVED":
            return snapshot if action["observation"] == observation else None
        if observation.phase is GroupRollExitPhase.RESOLVED:
            if action["phase"] not in {"STARTED", "OBSERVED"}:
                return None
            event_type = "GROUP_ROLL_EXIT_RESOLVED"
            receipt = observation.close_receipt
            fee = observation.close_fee_evidence
            payload = {
                "action_id": authorization.action_id,
                "authorization_sha256": authorization.authorization_sha256,
                "risk_intent_id": authorization.risk_intent_id,
                "observation": observation.to_payload(),
                "risk_receipt": None if receipt is None else _plain_rpc_json(receipt),
                "close_fee_evidence": None if fee is None else fee.to_payload(),
            }
            target_state = GroupOrderState.UNWINDING
        else:
            if observation.phase is GroupRollExitPhase.PREPARED:
                if action["phase"] not in {"PLANNED", "PREPARED"}:
                    return None
            elif action["phase"] not in {"STARTED", "OBSERVED"}:
                return None
            latest = next(
                (
                    item
                    for item in reversed(history)
                    if item.event_type in {
                        "GROUP_ROLL_EXIT_STATUS_OBSERVED",
                        "GROUP_ROLL_EXIT_RESOLVED",
                    }
                    and item.payload.get("action_id") == authorization.action_id
                ),
                None,
            )
            payload = {
                "action_id": authorization.action_id,
                "authorization_sha256": authorization.authorization_sha256,
                "risk_intent_id": authorization.risk_intent_id,
                "observation": observation.to_payload(),
            }
            if latest is not None and latest.event_type == "GROUP_ROLL_EXIT_STATUS_OBSERVED":
                if _plain_rpc_json(latest.payload) == payload:
                    return snapshot
            event_type = "GROUP_ROLL_EXIT_STATUS_OBSERVED"
            target_state = (
                GroupOrderState.RECOVERY_REQUIRED
                if observation.phase is GroupRollExitPhase.UNKNOWN
                else GroupOrderState.UNWINDING
            )
        try:
            return self._store.update(
                snapshot,
                event_type=event_type,
                event_payload=payload,
                state=target_state,
                error_code=None,
            )
        except Exception:
            return None

    def _release_roll_exit_reservations(
        self, snapshot: GroupOrderSnapshot
    ) -> GroupOrderSnapshot | None:
        request = snapshot.request
        if len(snapshot.reservations) != len(request.account_refs):
            return None
        for account_ref in request.account_refs:
            port = self._reservation_ports.get(account_ref)
            if port is None:
                return None
            try:
                current = self._store.get(request.portfolio_ref, request.group_id)
                if type(current) is not GroupOrderSnapshot:
                    return None
                remote = self._coordinator._query_reservation(current, account_ref)
                current = self._store.get(request.portfolio_ref, request.group_id)
                if type(current) is not GroupOrderSnapshot:
                    return None
                verified = self._coordinator._reservation_from_receipt(
                    current, account_ref, remote
                )
                plans = {plan.leg_id: plan for plan in request.legs_for_account(account_ref)}
                local = next(
                    (item for item in current.reservations if item.account_ref == account_ref),
                    None,
                )
                if (
                    local is None
                    or verified.group_id != request.group_id
                    or verified.account_ref != account_ref
                    or verified.group_fingerprint != local.group_fingerprint
                    or verified.scope_fingerprint != local.scope_fingerprint
                    or verified.deadline_ns != request.deadline_ns
                    or {item.leg_id for item in verified.legs} != set(plans)
                ):
                    return None
                progress_by_leg = {item.leg_id: item for item in current.legs}
                if len(progress_by_leg) != len(current.legs):
                    return None
                for item in verified.legs:
                    plan = plans.get(item.leg_id)
                    progress = progress_by_leg.get(item.leg_id)
                    if progress is None:
                        return None
                    submitted = progress.submitted_intent
                    status = progress.child_status
                    claim = progress.claim_receipt
                    resolution = progress.resolution_receipt
                    if (
                        plan is None
                        or not item.claimed
                        or not item.resolved
                        or item.state != "SETTLED"
                        or item.intent_id != plan.maximum_intent.intent_id
                        or type(submitted) is not OrderIntent
                        or submitted.intent_id != item.intent_id
                        or type(status) is not GroupChildOrderStatus
                        or status.intent_id != item.intent_id
                        or type(claim) is not GroupLegClaimReceipt
                        or type(resolution) is not GroupDispatchResolutionReceipt
                        or item.actual_intent_hash != claim.actual_child_intent_hash
                        or item.permit_id != claim.permit.permit_id
                        or item.reserved_intent_hash != claim.permit.reserved_intent_hash
                        or item.exposure_profile_fingerprint
                        != claim.permit.exposure_profile_fingerprint
                        or claim.permit.group_id != request.group_id
                        or claim.permit.leg_id != item.leg_id
                        or claim.permit.portfolio_ref != request.portfolio_ref
                        or claim.permit.strategy_id != request.strategy_id
                        or claim.permit.account_ref != account_ref
                        or claim.permit.coordinator_principal_ref
                        != request.coordinator_principal_ref
                        or claim.permit.group_fingerprint != local.group_fingerprint
                        or claim.permit.order_intent_hash != submitted.fingerprint
                        or resolution.claim != claim
                        or resolution.final_state != status.state
                        or resolution.requested_quantity != submitted.quantity
                        or resolution.filled_quantity != status.filled_quantity
                        or (
                            status.order_revision is not None
                            and resolution.order_revision != status.order_revision
                        )
                        or (
                            status.trade_count is not None
                            and resolution.trade_count != status.trade_count
                        )
                    ):
                        return None
                if local.phase is not GroupReservationPhase.RELEASED:
                    current = self._coordinator._replace_reservation(
                        current,
                        replace(verified, phase=GroupReservationPhase.RELEASED),
                        event_type="RESERVATION_RELEASED",
                        payload={"account_ref": account_ref, "roll_exit_settled": True},
                        phase=GroupReservationPhase.RELEASED,
                    )
                snapshot = current
            except Exception:
                return None
        return snapshot

    def _try_close_roll_exit_group(self, snapshot: GroupOrderSnapshot) -> GroupOrderSnapshot | None:
        """Atomically close only after both source legs and all close evidence reconcile."""

        try:
            if (
                type(snapshot) is not GroupOrderSnapshot
                or snapshot.state not in {
                    GroupOrderState.UNWINDING,
                    GroupOrderState.RECOVERY_REQUIRED,
                }
                or snapshot.request.version not in {1, 2}
                or len(snapshot.request.legs) != 2
            ):
                return None
            request = snapshot.request
            history = self._store.events(request.portfolio_ref, request.group_id)
            actions = _owner_roll_exit_actions(snapshot, history)
            if not actions or any(item["phase"] != "RESOLVED" for item in actions):
                return None
            by_leg: dict[str, list[dict[str, Any]]] = {plan.leg_id: [] for plan in request.legs}
            for action in actions:
                source_leg_id = action["authorization"].source.source_leg_id
                if source_leg_id not in by_leg:
                    return None
                by_leg[source_leg_id].append(action)
            if any(not items for items in by_leg.values()):
                return None
            progress_by_leg = {item.leg_id: item for item in snapshot.legs}
            if len(progress_by_leg) != len(snapshot.legs) or set(progress_by_leg) != set(by_leg):
                return None
            for plan in request.legs:
                progress = progress_by_leg.get(plan.leg_id)
                if progress is None:
                    return None
                status = progress.child_status
                if (
                    progress.submitted_intent is None
                    or progress.claim_receipt is None
                    or progress.resolution_receipt is None
                    or type(status) is not GroupChildOrderStatus
                    or status.state not in {ExecutionState.FILLED, ExecutionState.CANCELLED}
                    or status.filled_quantity <= 0
                    or status.cumulative_commission is None
                    or status.cumulative_commission_currency is None
                    or any(
                        item["authorization"].source.source_intent_id
                        != progress.submitted_intent.intent_id
                        or item["authorization"].source.source_terminal_status != status
                        or item["authorization"].source.source_claim != progress.claim_receipt
                        or item["authorization"].source.source_risk_resolution
                        != progress.resolution_receipt
                        or item["authorization"].source.source_filled_quantity
                        != status.filled_quantity
                        or _SHA256.fullmatch(
                            item["authorization"].source.source_event_digest
                        ) is None
                        for item in by_leg[plan.leg_id]
                    )
                ):
                    return None
            # A reservation is released only after every action is backed by a
            # complete full-fill receipt and its independent fee evidence.
            execution_fees: dict[str, ExecutionGroupRollExitCloseFeeEvidenceV1] = {}
            for plan in request.legs:
                leg_actions = by_leg[plan.leg_id]
                progress = progress_by_leg.get(plan.leg_id)
                if progress is None:
                    return None
                status = progress.child_status
                source = leg_actions[0]["authorization"].source
                if type(status) is not GroupChildOrderStatus:
                    return None
                for item in leg_actions:
                    authorization = item["authorization"]
                    observation = item["observation"]
                    if (
                        item["phase"] != "RESOLVED"
                        or type(observation) is not GroupRollExitObservationV1
                        or observation.phase is not GroupRollExitPhase.RESOLVED
                        or observation.close_state is not ExecutionState.FILLED
                        or observation.close_receipt is None
                        or authorization.source.source_event_digest
                        != source.source_event_digest
                    ):
                        return None
                    execution_fee = _execution_roll_exit_close_fee_evidence(
                        authorization, observation
                    )
                    if (
                        execution_fee is None
                        or execution_fee.fee_currency
                        != status.cumulative_commission_currency
                    ):
                        return None
                    execution_fees[authorization.action_id] = execution_fee
                if sum(
                    (Fraction(item["observation"].filled_quantity) for item in leg_actions),
                    Fraction(0),
                ) != Fraction(status.filled_quantity):
                    return None
            snapshot = self._release_roll_exit_reservations(snapshot)
            if snapshot is None:
                return None
            history = self._store.events(request.portfolio_ref, request.group_id)
            actions = _owner_roll_exit_actions(snapshot, history)
            progress_by_leg = {item.leg_id: item for item in snapshot.legs}
            legs: list[GroupRollExitLegClosedV1] = []
            for plan in request.legs:
                progress = progress_by_leg[plan.leg_id]
                status = progress.child_status
                claim = progress.claim_receipt
                resolution = progress.resolution_receipt
                if (
                    status is None
                    or claim is None
                    or resolution is None
                    or progress.submitted_intent is None
                ):
                    return None
                leg_actions: list[GroupRollExitCloseActionV1] = []
                for action in actions:
                    authorization = action["authorization"]
                    if authorization.source.source_leg_id != plan.leg_id:
                        continue
                    observation = action["observation"]
                    receipt = observation.close_receipt
                    fee = execution_fees.get(authorization.action_id)
                    if (
                        observation.phase is not GroupRollExitPhase.RESOLVED
                        or observation.close_state is not ExecutionState.FILLED
                        or receipt is None
                        or fee is None
                        or authorization.risk_intent_id is None
                    ):
                        return None
                    leg_actions.append(
                        GroupRollExitCloseActionV1(
                            action_id=authorization.action_id,
                            authorization_sha256=authorization.authorization_sha256,
                            risk_intent_id=authorization.risk_intent_id,
                            receipt_sha256=observation.close_receipt_sha256,
                            close_fee_evidence_sha256=fee.evidence_sha256,
                            close_quantity=observation.filled_quantity,
                            close_fee_amount=fee.fee_amount,
                            close_fee_currency=fee.fee_currency,
                        )
                    )
                source_event_sha256 = next(
                    action["authorization"].source.source_event_digest
                    for action in actions
                    if action["authorization"].source.source_leg_id == plan.leg_id
                )
                legs.append(
                    GroupRollExitLegClosedV1(
                        leg_id=plan.leg_id,
                        source_intent_id=progress.submitted_intent.intent_id,
                        source_event_sha256=source_event_sha256,
                        source_claim_digest=claim.claim_digest,
                        source_resolution_sha256=payload_sha256(resolution.to_payload()),
                        source_filled_quantity=status.filled_quantity,
                        source_fee=GroupRollExitSourceFeeEvidenceV1(
                            intent_id=status.intent_id,
                            amount=status.cumulative_commission,
                            currency=status.cumulative_commission_currency,
                            source_event_sha256=source_event_sha256,
                        ),
                        close_actions=tuple(leg_actions),
                    )
                )
            budget_release = None
            if request.version == 2:
                budget = snapshot.budget_reservation
                if budget is None or budget.state is not GroupBudgetReservationState.HELD:
                    return None
                budget_release = GroupRollExitBudgetReleaseV1(
                    policy_fingerprint=budget.policy_fingerprint,
                    quote_fingerprint=budget.quote_fingerprint,
                    valuation_unit=budget.valuation_unit,
                    gross_notional=budget.gross_notional,
                    deadline_ns=budget.deadline_ns,
                )
            proof = GroupRollExitClosedProofV1(
                portfolio_ref=request.portfolio_ref,
                group_id=request.group_id,
                group_version=request.version,
                group_fingerprint=request.fingerprint,
                expected_revision=snapshot.revision,
                prior_group_event_chain_sha256=group_event_chain_sha256(snapshot, history),
                legs=tuple(legs),
                reservation_sha256s=tuple(
                    payload_sha256(
                        next(
                            item.to_payload()
                            for item in snapshot.reservations
                            if item.account_ref == account_ref
                        )
                    )
                    for account_ref in request.account_refs
                ),
                budget_release=budget_release,
            )
            return self._store.close_roll_exit(snapshot, proof)
        except Exception:
            return None

    def _advance_pending_compensation(
        self,
        snapshot: GroupOrderSnapshot,
        order_port: _OrdersRpcPort,
        action: Mapping[str, Any],
    ) -> None:
        authorization = action["authorization"]
        if type(authorization) is not GroupCompensationCloseAuthorization:
            return
        phase = action["phase"]
        if phase is GroupCompensationActionPhase.PLAN_RECORDED:
            started = self._store.update(
                snapshot,
                event_type="GROUP_COMPENSATION_SUBMIT_STARTED",
                event_payload={
                    "action_id": authorization.action_id,
                    "authorization_digest": authorization.digest,
                    "intent_id": authorization.intent_id,
                    "client_order_id": authorization.client_order_id,
                },
                state=GroupOrderState.UNWINDING,
            )
            # This is the unique provider-write path. Persisting SUBMIT_STARTED
            # first means every restart and lost reply must query the same ID.
            reply = order_port.submit_group_compensation_close(authorization)
            self._record_compensation_reply(started, authorization, reply)
            return
        if phase not in {
            GroupCompensationActionPhase.SUBMIT_STARTED,
            GroupCompensationActionPhase.STATUS_OBSERVED,
        }:
            return
        reply = order_port.query_group_compensation_close(
            authorization=authorization
        )
        self._record_compensation_reply(snapshot, authorization, reply)

    def _record_compensation_reply(
        self,
        snapshot: GroupOrderSnapshot,
        authorization: GroupCompensationCloseAuthorization,
        reply: Mapping[str, Any],
    ) -> None:
        if reply.get("phase") not in {
            GroupCompensationActionPhase.SUBMIT_STARTED.value,
            GroupCompensationActionPhase.STATUS_OBSERVED.value,
            GroupCompensationActionPhase.RESOLVED.value,
        }:
            return
        status_payload = reply.get("status")
        status = None
        if status_payload is not None:
            if not isinstance(status_payload, Mapping):
                return
            status = GroupChildOrderStatus.from_payload(status_payload)
            if (
                status.intent_id != authorization.intent_id
                or Fraction(status.filled_quantity) > Fraction(authorization.close_quantity)
            ):
                return
            events = self._store.events(
                snapshot.request.portfolio_ref, snapshot.request.group_id
            )
            latest = next(
                (
                    event.payload.get("status")
                    for event in reversed(events)
                    if event.event_type == "GROUP_COMPENSATION_STATUS_OBSERVED"
                    and event.payload.get("action_id") == authorization.action_id
                ),
                None,
            )
            if latest != status.to_payload():
                snapshot = self._store.update(
                    snapshot,
                    event_type="GROUP_COMPENSATION_STATUS_OBSERVED",
                    event_payload={
                        "action_id": authorization.action_id,
                        "authorization_digest": authorization.digest,
                        "status": status.to_payload(),
                    },
                    state=GroupOrderState.UNWINDING,
                )
        receipt = reply.get("close_receipt")
        if status is None or not isinstance(receipt, Mapping):
            return
        if (
            status.state.value != "FILLED"
            or status.filled_quantity != authorization.close_quantity
            or receipt.get("status") != "TERMINAL_CLOSE_RESOLVED"
            or receipt.get("final_state") != "FILLED"
            or receipt.get("position_effect") != "CLOSE"
            or receipt.get("dispatch_latch_active") is not False
            or receipt.get("has_other_active_freezes") is not False
        ):
            return
        self._store.update(
            snapshot,
            event_type="GROUP_COMPENSATION_RESOLVED",
            event_payload={
                "action_id": authorization.action_id,
                "authorization_digest": authorization.digest,
                "authorization": authorization.to_payload(),
                "close_receipt": dict(receipt),
            },
            state=GroupOrderState.UNWINDING,
        )

    def _release_compensation_reservation(
        self,
        snapshot: GroupOrderSnapshot,
        reservation_port: _ReservationsRpcPort,
    ) -> GroupOrderSnapshot | None:
        request = snapshot.request
        if len(snapshot.reservations) != 1:
            return None
        account_ref = request.account_refs[0]
        try:
            current = self._coordinator._query_reservation(snapshot, account_ref)
            snapshot = self._store.get(request.portfolio_ref, request.group_id)
            if type(snapshot) is not GroupOrderSnapshot:
                return None
            reservation = self._coordinator._reservation_from_receipt(
                snapshot, account_ref, current
            )
            if not all(
                (leg.claimed and leg.resolved and leg.state in {"SETTLED", "NO_FILL_TERMINAL"})
                or (not leg.claimed and leg.state in {"ACTIVE", "RELEASED", "EXPIRED"})
                for leg in reservation.legs
            ):
                return None
            if any(not leg.claimed and leg.state == "ACTIVE" for leg in reservation.legs):
                local = snapshot.reservations[0]
                if local.phase is not GroupReservationPhase.RELEASE_REQUESTED:
                    snapshot = self._coordinator._record_reservation_event(
                        snapshot,
                        account_ref,
                        "RESERVATION_RELEASE_STARTED",
                        phase=GroupReservationPhase.RELEASE_REQUESTED,
                    )
                released = reservation_port.release_group_unclaimed(
                    request.group_id, "group_compensation_resolved"
                )
                snapshot = self._store.get(request.portfolio_ref, request.group_id)
                if type(snapshot) is not GroupOrderSnapshot:
                    return None
                reservation = self._coordinator._reservation_from_receipt(
                    snapshot, account_ref, released
                )
            if not all(
                (leg.claimed and leg.resolved and leg.state in {"SETTLED", "NO_FILL_TERMINAL"})
                or (not leg.claimed and leg.state in {"RELEASED", "EXPIRED"})
                for leg in reservation.legs
            ):
                return None
            reservation = replace(reservation, phase=GroupReservationPhase.RELEASED)
            snapshot = self._coordinator._replace_reservation(
                snapshot,
                reservation,
                event_type="RESERVATION_RELEASED",
                payload={"account_ref": account_ref, "compensation_settled": True},
                phase=GroupReservationPhase.RELEASED,
            )
            return snapshot
        except Exception:
            return None

    def handle(
        self, operation: str, *, context: ServiceCallContext, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            registration, target_scope, target = self._authorize(operation, context)
            if operation == "groups.query":
                return self._query(registration, target, arguments)
            if operation == "groups.cancel":
                return self._cancel(registration, target_scope, target, arguments)
            if operation == "groups.roll_exit":
                return self._roll_exit(registration, target_scope, target, arguments)
            if operation == "groups.query_roll_exit":
                return self._query_roll_exit(registration, target_scope, target, arguments)
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
                    accepted_snapshot = self._coordinator.accept(run_binding.request)
                except ContractValidationError:
                    return _failure("GROUP_ACCEPTANCE_REJECTED")
                run_binding = self._bind_verified_accepted_snapshot(
                    run_binding, accepted_snapshot
                )
                if run_binding is None:
                    return _failure("GROUP_ACCEPTED_BINDING_UNAVAILABLE")
                _CURRENT_COORDINATOR_CALL.set(replace(call, binding=run_binding))
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
        except GroupCancelPlanError as error:
            return _failure(str(error))
        except GroupRollExitError:
            return _failure("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
        except (TypeError, ValueError, KeyError):
            return _failure("INVALID_REQUEST")

    def _existing_submit_binding(
        self,
        registration: ActiveRegistrationContext,
        arguments: Mapping[str, Any],
    ) -> GroupOriginRunBinding | None:
        """Read an existing same-ID submit binding under the current grant."""

        if not isinstance(arguments, Mapping):
            return None
        keys = frozenset(arguments)
        if keys != _LEGACY_SUBMIT_KEYS and keys != _VERSIONED_SUBMIT_KEYS:
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

        if not isinstance(arguments, Mapping):
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        declared_version = _submit_declared_version(arguments)
        if declared_version != accepted.version:
            raise AcceptedGroupConflictError("submit replay changed its accepted group version")
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
        leg_keys = (
            _THREE_LEG_SUBMIT_LEG_KEYS
            if declared_version == _THREE_LEG_GROUP_VERSION
            else _LEGACY_SUBMIT_LEG_KEYS
        )
        for raw_leg, planned in zip(raw_legs, accepted.legs, strict=True):
            if not isinstance(raw_leg, Mapping) or set(raw_leg) != leg_keys:
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
            ratio_bound = (
                declared_version != _THREE_LEG_GROUP_VERSION
                or _parse_ratio(raw_leg["ratio"]) == planned.ratio
            )
            if (
                leg_id != planned.leg_id
                or account_ref != planned.account_ref
                or intent.to_payload() != planned.maximum_intent.to_payload()
                or not ratio_bound
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
        self._roll_exit_actions.close()

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
            operation in _GROUP_COORDINATOR_WRITE_OPERATIONS
            and operation not in registration.write_operations
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
        coordinator_operation_sets = {
            _GROUP_COORDINATOR_BASE_OPERATIONS,
            _GROUP_COORDINATOR_FULL_OPERATIONS,
            _GROUP_COORDINATOR_BASE_OPERATIONS | _GROUP_COORDINATOR_ROLL_EXIT_OPERATIONS,
            self.SUPPORTED_OPERATIONS,
        }
        write_operations = registration.write_operations
        full_write_view = (
            registration.operations in coordinator_operation_sets
            and "groups.submit" in write_operations
            and write_operations
            <= registration.operations & _GROUP_COORDINATOR_WRITE_OPERATIONS
        )
        query_only_view = (
            registration.operations
            in {
                frozenset({"groups.query"}),
                frozenset({"groups.query", "groups.query_roll_exit"}),
            }
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
        if not isinstance(arguments, Mapping):
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        version = _submit_declared_version(arguments)
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
        if len(raw_legs) != (_THREE_LEG_GROUP_VERSION if version == _THREE_LEG_GROUP_VERSION else 2):
            raise ServiceExecutionGroupError("GROUP_LEGS_INVALID")
        leg_keys = (
            _THREE_LEG_SUBMIT_LEG_KEYS
            if version == _THREE_LEG_GROUP_VERSION
            else _LEGACY_SUBMIT_LEG_KEYS
        )
        scope = registration.context.scope
        plans: list[GroupOrderLegPlan] = []
        used_leg_ids: set[str] = set()
        for raw_leg in raw_legs:
            if not isinstance(raw_leg, Mapping) or set(raw_leg) != leg_keys:
                raise ServiceExecutionGroupError("GROUP_LEG_INVALID")
            leg_id = _reference(raw_leg["leg_id"], "GROUP_LEG_INVALID")
            account_ref = _reference(raw_leg["account_ref"], "GROUP_ACCOUNT_INVALID")
            if leg_id in used_leg_ids or account_ref not in target.accounts:
                raise ServiceExecutionGroupError("GROUP_LEG_INVALID")
            used_leg_ids.add(leg_id)
            ratio = (
                _parse_ratio(raw_leg["ratio"])
                if version == _THREE_LEG_GROUP_VERSION
                else None
            )
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
                    ratio=ratio,
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
                version=version,
                # The strategy wire is intentionally unable to supply this
                # cap. It is copied only from the trusted target configuration
                # and then persisted with the accepted v3 group request.
                compensation_budget_policy=(
                    target.compensation_budget_policy if version == 3 else None
                ),
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

    def _roll_exit(
        self,
        registration: ActiveRegistrationContext,
        target_scope: WireScope,
        target: GroupCoordinatorTarget,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "group_id",
            "source_leg_id",
            "limit_price",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        source_leg_id = _reference(arguments["source_leg_id"], "GROUP_LEG_INVALID")
        request = GroupRollExitRequestV1(
            portfolio_ref=registration.context.scope.portfolio_ref,
            group_id=group_id,
            source_leg_id=source_leg_id,
            limit_price=_parse_roll_exit_price(arguments["limit_price"]),
        )
        binding = self._run_bindings.get_for_registration(
            registration,
            group_id,
            coordinator_principal_ref=self._identity.principal_ref,
            operation="groups.roll_exit",
        )
        if binding is None:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
        snapshot = self._store.get(request.portfolio_ref, group_id)
        if (
            type(snapshot) is not GroupOrderSnapshot
            or not self._recovery_binding_matches(binding, snapshot, target_scope, target)
            or snapshot.request.version not in {1, 2}
            or len(snapshot.request.legs) != 2
            or snapshot.request.compensation_budget_policy is not None
            or snapshot.state not in {
                GroupOrderState.PAIRED,
                GroupOrderState.UNWINDING,
                GroupOrderState.RECOVERY_REQUIRED,
                GroupOrderState.CLOSED,
            }
        ):
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
        source_plans = tuple(
            plan for plan in snapshot.request.legs if plan.leg_id == source_leg_id
        )
        if len(source_plans) != 1:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_SOURCE_LEG_INVALID")
        account_ref = source_plans[0].account_ref
        if account_ref not in target.accounts:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_TARGET_UNAVAILABLE")
        history = self._store.events(request.portfolio_ref, group_id)
        actions = _owner_roll_exit_actions(snapshot, history)
        pending = tuple(item for item in actions if item["phase"] != "RESOLVED")
        if len(pending) > 1:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")
        if pending:
            action = pending[0]
            authorization = action["authorization"]
            if (
                authorization.request != request
                or authorization.source.source_leg_id != source_leg_id
                or authorization.source.account_ref != account_ref
            ):
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")
            if not _roll_exit_authorization_matches_snapshot(
                authorization,
                snapshot,
                binding,
                target.accounts[account_ref],
                expected_plan_revision=action["plan_index"] - 1,
            ):
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
            try:
                record = self._roll_exit_actions.get(authorization.action_id)
                if record is None:
                    record = self._roll_exit_actions.prepare(authorization)
                if record.authorization.to_payload() != authorization.to_payload():
                    raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
            except GroupRollExitError:
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None
            record = self._recover_roll_exit(
                snapshot,
                registration,
                target_scope,
                target,
                binding,
                account_ref,
                record,
            )
            current = self._store.get(request.portfolio_ref, group_id)
            if type(current) is GroupOrderSnapshot and record.observation is not None:
                current = self._record_roll_exit_group_observation(
                    current, authorization, record.observation
                )
                if current is not None and record.phase is GroupRollExitPhase.RESOLVED:
                    call = _CoordinatorCall(registration, target_scope, target, binding)
                    token = _CURRENT_COORDINATOR_CALL.set(call)
                    try:
                        self._try_close_roll_exit_group(current)
                    finally:
                        _CURRENT_COORDINATOR_CALL.reset(token)
            return _success({"found": True, "action": _roll_exit_action_projection(record)})

        prior_same_request = next(
            (
                item
                for item in reversed(actions)
                if item["authorization"].source.source_leg_id == source_leg_id
                and item["authorization"].request == request
            ),
            None,
        )
        if prior_same_request is not None:
            authorization = prior_same_request["authorization"]
            if prior_same_request["phase"] != "RESOLVED":
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")
            try:
                record = self._roll_exit_actions.get(authorization.action_id)
                if record is None:
                    record = self._roll_exit_actions.prepare(authorization)
                    if prior_same_request["observation"] is None:
                        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
                    record = self._roll_exit_actions.record_observation(
                        prior_same_request["observation"]
                    )
            except GroupRollExitError:
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None
            # Replaying the exact accepted action may finish an aggregate
            # close whose prior durable write failed. Reservation readback in
            # that close is still a trusted coordinator call, so bind only
            # this recovery step to the already-validated registration.
            call = _CoordinatorCall(registration, target_scope, target, binding)
            token = _CURRENT_COORDINATOR_CALL.set(call)
            try:
                self._try_close_roll_exit_group(snapshot)
            finally:
                _CURRENT_COORDINATOR_CALL.reset(token)
            return _success({"found": True, "action": _roll_exit_action_projection(record)})

        if any(
            not _roll_exit_action_has_closure_evidence(item)
            for item in actions
            if item["phase"] == "RESOLVED"
        ):
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")

        # A closed group may only replay an exact action already present in
        # the verified journal.  Never ask Risk for a fresh authorization or
        # create another action after aggregate closure.
        if snapshot.state is GroupOrderState.CLOSED:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")

        call = _CoordinatorCall(registration, target_scope, target, binding)
        token = _CURRENT_COORDINATOR_CALL.set(call)
        try:
            try:
                risk_phase, authorization, observation = self._order_ports[
                    account_ref
                ].prepare_group_roll_exit(request)
            except Exception:
                raise ServiceExecutionGroupError(
                    "GROUP_ROLL_EXIT_PREPARE_UNAVAILABLE"
                ) from None
            if not _roll_exit_authorization_matches_snapshot(
                authorization,
                snapshot,
                binding,
                target.accounts[account_ref],
                expected_plan_revision=snapshot.revision,
            ):
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
            try:
                planned = self._record_roll_exit_plan(snapshot, authorization)
                if planned is None:
                    raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
                record = self._roll_exit_actions.prepare(authorization)
            except GroupRollExitError:
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None
            except ServiceExecutionGroupError:
                raise
            current = self._store.get(request.portfolio_ref, group_id)
            if type(current) is not GroupOrderSnapshot:
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
            started = self._record_roll_exit_submit_started(current, authorization)
            if started is None:
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")

            if risk_phase is GroupRollExitPhase.PREPARED and observation is None:
                if not self._roll_exit_actions.claim_submit(
                    authorization.action_id, authorization.authorization_sha256
                ):
                    record = self._roll_exit_actions.get(authorization.action_id)
                    if record is None:
                        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
                    record = self._recover_roll_exit(
                        started,
                        registration,
                        target_scope,
                        target,
                        binding,
                        account_ref,
                        record,
                    )
                else:
                    try:
                        observation = self._order_ports[account_ref].submit_group_roll_exit(
                            authorization
                        )
                    except Exception:
                        observation = _roll_exit_unknown_observation(
                            authorization, "RISK_SUBMIT_RESPONSE_UNAVAILABLE"
                        )
                    record = self._roll_exit_actions.record_observation(observation)
            else:
                if observation is None:
                    raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_QUERY_INVALID")
                if record.phase is GroupRollExitPhase.PREPARED:
                    if not self._roll_exit_actions.claim_submit(
                        authorization.action_id, authorization.authorization_sha256
                    ):
                        refreshed = self._roll_exit_actions.get(authorization.action_id)
                        if refreshed is None:
                            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
                        record = refreshed
                    else:
                        record = self._roll_exit_actions.get(authorization.action_id)
                record = self._roll_exit_actions.record_observation(observation)

            current = self._store.get(request.portfolio_ref, group_id)
            if type(current) is GroupOrderSnapshot and record.observation is not None:
                current = self._record_roll_exit_group_observation(
                    current, authorization, record.observation
                )
                if current is not None and record.phase is GroupRollExitPhase.RESOLVED:
                    self._try_close_roll_exit_group(current)
            return _success({"found": True, "action": _roll_exit_action_projection(record)})
        finally:
            _CURRENT_COORDINATOR_CALL.reset(token)

    def _query_roll_exit(
        self,
        registration: ActiveRegistrationContext,
        target_scope: WireScope,
        target: GroupCoordinatorTarget,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {"action_id"}:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        action_id = _reference(arguments["action_id"], "GROUP_ROLL_EXIT_ACTION_INVALID")
        record = self._roll_exit_actions.get(action_id)
        if record is None:
            return _success({"found": False, "action": None})
        request = record.authorization.request
        binding = self._run_bindings.get_for_registration(
            registration,
            request.group_id,
            coordinator_principal_ref=self._identity.principal_ref,
            operation="groups.query_roll_exit",
        )
        if (
            binding is None
            or binding.request.portfolio_ref != request.portfolio_ref
            or record.authorization.source.origin_run_id != binding.origin_run_id
            or record.authorization.source.strategy_id != binding.request.strategy_id
            or record.authorization.source.account_ref not in target.accounts
        ):
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
        snapshot = self._store.get(request.portfolio_ref, request.group_id)
        if (
            type(snapshot) is not GroupOrderSnapshot
            or not self._recovery_binding_matches(binding, snapshot, target_scope, target)
            or snapshot.request.version not in {1, 2}
            or len(snapshot.request.legs) != 2
            or record.authorization.source.group_fingerprint != snapshot.request.fingerprint
            or record.authorization.source.group_request != snapshot.request
        ):
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
        source_plans = tuple(
            plan for plan in snapshot.request.legs if plan.leg_id == request.source_leg_id
        )
        if len(source_plans) != 1 or source_plans[0].account_ref != record.authorization.source.account_ref:
            raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_NOT_AUTHORIZED")
        if record.phase is GroupRollExitPhase.RESOLVED:
            return _success({"found": True, "action": _roll_exit_action_projection(record)})
        call = _CoordinatorCall(registration, target_scope, target, binding)
        token = _CURRENT_COORDINATOR_CALL.set(call)
        try:
            try:
                observation = self._order_ports[
                    source_plans[0].account_ref
                ].query_group_roll_exit(record.authorization)
            except Exception:
                observation = _roll_exit_unknown_observation(
                    record.authorization,
                    "RISK_QUERY_UNAVAILABLE",
                    previous=record.observation,
                )
            if observation is None:
                observation = _roll_exit_unknown_observation(
                    record.authorization,
                    "RISK_ACTION_NOT_FOUND",
                    previous=record.observation,
                )
            if not _roll_exit_observation_matches(
                record.authorization, observation.phase, observation
            ):
                raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_QUERY_INVALID")
            # A query may read the Risk projection but never claim or submit a
            # prepared action, and it does not mutate the local action journal.
            observation = _roll_exit_query_observation(record, observation)
            return _success(
                {
                    "found": True,
                    "action": _roll_exit_action_projection(
                        record, observation=observation
                    ),
                }
            )
        finally:
            _CURRENT_COORDINATOR_CALL.reset(token)

    def _recover_roll_exit(
        self,
        snapshot: GroupOrderSnapshot,
        registration: ActiveRegistrationContext | None,
        target_scope: WireScope,
        target: GroupCoordinatorTarget,
        binding: GroupOriginRunBinding,
        account_ref: str,
        record: GroupRollExitActionRecordV1,
    ) -> GroupRollExitActionRecordV1:
        if (
            type(snapshot) is not GroupOrderSnapshot
            or type(record) is not GroupRollExitActionRecordV1
            or account_ref != record.authorization.source.account_ref
            or account_ref not in target.accounts
        ):
            return record
        authorization = record.authorization
        try:
            history = self._store.events(
                snapshot.request.portfolio_ref, snapshot.request.group_id
            )
            actions = _owner_roll_exit_actions(snapshot, history)
            durable = next(
                (
                    item
                    for item in actions
                    if item["authorization"].action_id == authorization.action_id
                ),
                None,
            )
            if (
                durable is None
                or durable["authorization_sha256"]
                != authorization.authorization_sha256
                or durable["authorization"].to_payload()
                != authorization.to_payload()
                or not _roll_exit_authorization_matches_snapshot(
                    authorization,
                    snapshot,
                    binding,
                    target.accounts[account_ref],
                    expected_plan_revision=durable["plan_index"] - 1,
                )
                or durable["phase"] == "RESOLVED"
            ):
                return record
        except Exception:
            return record
        call = _CoordinatorCall(registration, target_scope, target, binding)
        token = _CURRENT_COORDINATOR_CALL.set(call)
        try:
            try:
                observation = self._order_ports[account_ref].query_group_roll_exit(
                    record.authorization
                )
            except Exception:
                observation = _roll_exit_unknown_observation(
                    record.authorization,
                    "RISK_QUERY_UNAVAILABLE",
                    previous=record.observation,
                )
            if observation is None:
                observation = _roll_exit_unknown_observation(
                    record.authorization,
                    "RISK_ACTION_NOT_FOUND",
                    previous=record.observation,
                )
            if observation.phase not in {
                GroupRollExitPhase.PREPARED,
                GroupRollExitPhase.SUBMIT_STARTED,
                GroupRollExitPhase.STATUS_OBSERVED,
                GroupRollExitPhase.UNKNOWN,
                GroupRollExitPhase.RESOLVED,
            }:
                return record
            if observation.phase is GroupRollExitPhase.PREPARED:
                if durable["phase"] == "OBSERVED" or record.phase in {
                    GroupRollExitPhase.STATUS_OBSERVED,
                    GroupRollExitPhase.RESOLVED,
                }:
                    # A previously observed order cannot regress to PREPARED.
                    return record
                if durable["phase"] in {"PLANNED", "PREPARED"}:
                    current_snapshot = self._store.get(
                        snapshot.request.portfolio_ref, snapshot.request.group_id
                    )
                    if type(current_snapshot) is not GroupOrderSnapshot:
                        return record
                    current_snapshot = self._record_roll_exit_submit_started(
                        current_snapshot, authorization
                    )
                    if current_snapshot is None:
                        return record
                    durable_phase = "STARTED"
                else:
                    durable_phase = durable["phase"]
                if durable_phase != "STARTED":
                    return record
                if record.phase is GroupRollExitPhase.PREPARED:
                    try:
                        record = self._roll_exit_actions.record_observation(observation)
                    except GroupRollExitError:
                        return record
                    if not self._roll_exit_actions.claim_submit(
                        authorization.action_id,
                        authorization.authorization_sha256,
                    ):
                        refreshed = self._roll_exit_actions.get(authorization.action_id)
                        return record if refreshed is None else refreshed
                elif record.phase not in {
                    GroupRollExitPhase.SUBMIT_STARTED,
                    GroupRollExitPhase.UNKNOWN,
                }:
                    return record
                # For a local SUBMIT_STARTED or UNKNOWN action, this exact
                # Risk PREPARED reply proves no provider dispatch was started.
                # The Risk endpoint owns the persistent CAS and is idempotent:
                # it may start once and all concurrent retries only query.
                try:
                    observation = self._order_ports[account_ref].submit_group_roll_exit(
                        record.authorization
                    )
                except Exception:
                    observation = _roll_exit_unknown_observation(
                        record.authorization,
                        "RISK_SUBMIT_RESPONSE_UNAVAILABLE",
                        previous=record.observation,
                    )
            elif durable["phase"] not in {"STARTED", "OBSERVED"}:
                # A Risk observation after submit cannot be reconciled unless
                # the group journal already contains the pre-submit marker.
                return record
            try:
                return self._roll_exit_actions.record_observation(observation)
            except GroupRollExitError:
                return record
        finally:
            _CURRENT_COORDINATOR_CALL.reset(token)

    def _cancel(
        self,
        registration: ActiveRegistrationContext,
        target_scope: WireScope,
        target: GroupCoordinatorTarget,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "group_id",
            "cancel_request_id",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        cancel_request_id = _reference(
            arguments["cancel_request_id"], "GROUP_CANCEL_REQUEST_ID_INVALID"
        )
        binding = self._run_bindings.get_for_registration(
            registration,
            group_id,
            coordinator_principal_ref=self._identity.principal_ref,
            operation="groups.cancel",
        )
        if binding is None:
            return _success({"found": False, "group_id": group_id, "legs": []})
        snapshot = self._store.get(binding.request.portfolio_ref, group_id)
        if (
            type(snapshot) is not GroupOrderSnapshot
            or snapshot.request.unquoted_fingerprint != binding.request_fingerprint
            or not self._recovery_binding_matches(binding, snapshot, target_scope, target)
            or snapshot.state
            not in {
                GroupOrderState.PREPARING,
                GroupOrderState.FIRST_PENDING,
                GroupOrderState.HEDGE_PENDING,
                GroupOrderState.RECOVERY_REQUIRED,
            }
            or any(item.phase is not GroupReservationPhase.ACTIVE for item in snapshot.reservations)
            or (
                snapshot.request.version != 1
                and (
                    snapshot.budget_reservation is None
                    or snapshot.budget_reservation.state is not GroupBudgetReservationState.HELD
                )
            )
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_NOT_AUTHORIZED")

        manifest: list[dict[str, str]] = []
        for plan, progress in zip(snapshot.request.legs, snapshot.legs, strict=True):
            intent = progress.submitted_intent
            status = progress.child_status
            if intent is None:
                continue
            if snapshot.state is GroupOrderState.RECOVERY_REQUIRED:
                # Recovery cancellation is query-first: re-read every already
                # submitted child by its original business id. An UNKNOWN or
                # missing fact blocks the whole action and keeps all holds.
                try:
                    receipt = self._order_ports[plan.account_ref].query(intent.intent_id)
                except Exception:
                    raise ServiceExecutionGroupError(
                        "GROUP_CANCEL_FACTS_UNCONFIRMED"
                    ) from None
                if receipt is None:
                    raise ServiceExecutionGroupError("GROUP_CANCEL_FACTS_UNCONFIRMED")
                status = receipt.status
            if (
                status is None
                or progress.claim_receipt is None
                or intent.intent_id != status.intent_id
                or intent.intent_id != progress.claim_receipt.permit.intent_id
                or status.state is ExecutionState.UNKNOWN
            ):
                raise ServiceExecutionGroupError("GROUP_CANCEL_FACTS_UNCONFIRMED")
            if status.state in {ExecutionState.ACKED, ExecutionState.PARTIALLY_FILLED}:
                if (
                    type(status.provider_order_id) is not str
                    or not status.provider_order_id
                    or plan.account_ref not in target.accounts
                ):
                    raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_UNAVAILABLE")
            else:
                # Previously terminal children are facts, not cancel targets.
                continue
            manifest.append(
                {
                    "leg_id": plan.leg_id,
                    "account_ref": plan.account_ref,
                    "cancel_id": group_cancel_leg_id(
                        binding.request.portfolio_ref,
                        group_id,
                        binding.origin_run_id,
                        plan.leg_id,
                        plan.account_ref,
                        target.accounts[plan.account_ref].execution_scope.key,
                        intent.intent_id,
                        status.provider_order_id,
                    ),
                    "target_intent_id": intent.intent_id,
                    "provider_order_id": status.provider_order_id,
                }
            )
        if not manifest:
            raise ServiceExecutionGroupError("GROUP_CANCEL_NO_ACTIVE_LEGS")
        try:
            self._cancel_plans.create_or_get(
                portfolio_ref=binding.request.portfolio_ref,
                group_id=group_id,
                cancel_request_id=cancel_request_id,
                origin_run_id=binding.origin_run_id,
                legs=manifest,
            )
        except GroupCancelPlanError:
            raise

        call = _CoordinatorCall(registration, target_scope, target, binding)
        token = _CURRENT_COORDINATOR_CALL.set(call)
        try:
            for leg in manifest:
                dispatch_claimed = self._cancel_plans.claim_dispatch(
                    binding.request.portfolio_ref,
                    group_id,
                    cancel_request_id,
                    leg["leg_id"],
                )
                if dispatch_claimed:
                    try:
                        result = self._order_ports[leg["account_ref"]].cancel_group_leg(
                            group_id=group_id,
                            cancel_request_id=cancel_request_id,
                            leg_id=leg["leg_id"],
                        )
                    except Exception:
                        self._cancel_plans.mark_unknown(
                            binding.request.portfolio_ref,
                            group_id,
                            cancel_request_id,
                            leg["leg_id"],
                        )
                    else:
                        self._cancel_plans.record_result(
                            binding.request.portfolio_ref,
                            group_id,
                            cancel_request_id,
                            leg["leg_id"],
                            result,
                        )
                    # A first-attempt response loss is returned as UNKNOWN.
                    # Recovery is an explicit same-request replay below; it
                    # never performs an inline query and cannot redispatch.
                    continue
                # A replay or concurrent claimant can only query the exact
                # durable account receipt under the already-reserved ID.
                try:
                    result = self._order_ports[leg["account_ref"]].query_group_cancel(
                        group_id=group_id,
                        cancel_request_id=cancel_request_id,
                        cancel_id=leg["cancel_id"],
                        leg_id=leg["leg_id"],
                    )
                except Exception:
                    self._cancel_plans.mark_unknown(
                        binding.request.portfolio_ref,
                        group_id,
                        cancel_request_id,
                        leg["leg_id"],
                    )
                else:
                    self._cancel_plans.record_result(
                        binding.request.portfolio_ref,
                        group_id,
                        cancel_request_id,
                        leg["leg_id"],
                        result,
                    )
        finally:
            _CURRENT_COORDINATOR_CALL.reset(token)
        persisted = self._cancel_plans.get(
            binding.request.portfolio_ref, group_id, cancel_request_id
        )
        if persisted is None:
            raise ServiceExecutionGroupError("GROUP_CANCEL_PLAN_MISSING")
        return _success({
            "found": True,
            "group_id": group_id,
            "cancel_request_id": persisted.cancel_request_id,
            "legs": [_cancel_plan_leg_payload(item) for item in persisted.legs],
        })


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
        trusted_cancel_policies: tuple[TrustedGroupWritePolicy, ...] = (),
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
            or not _has_method(group_authority, "bind_reconciliation_source")
            or not _has_method(group_authority, "read_reconciliation_hold")
            or not _has_method(group_authority, "reconciliation_hold_state")
            or not _has_method(group_authority, "reconcile_hold")
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
            or type(trusted_cancel_policies) is not tuple
            or any(type(item) is not TrustedGroupWritePolicy for item in trusted_cancel_policies)
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
                    for name in (
                        "bind_permit",
                        "bind_dispatch",
                        "read_receipt",
                        "resolve_receipt",
                        "reconcile_unknown",
                        "verify_authority_reconciliation",
                    )
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
        cancel_keys: set[tuple[object, ...]] = set()
        for policy in trusted_cancel_policies:
            if (
                policy.port != GROUP_ORDER_ENTRY_PORT
                or policy.operation != "orders.cancel_group_leg"
                or policy.scope not in target_copy
                or getattr(policy, "account_ref", policy.scope.account_ref)
                != policy.scope.account_ref
            ):
                raise ServiceExecutionGroupError("GROUP_POLICIES_INVALID")
            key = _policy_key(policy)
            if key in cancel_keys or key not in write_by_key:
                raise ServiceExecutionGroupError("GROUP_POLICIES_MISMATCH")
            cancel_keys.add(key)

        self._store = store
        self._targets = MappingProxyType(target_copy)
        self._accepted_groups = accepted_group_store
        self._authority = group_authority
        self._proof_source = proof_source
        self._write_policies = tuple(trusted_write_policies)
        self._cancel_policies = tuple(trusted_cancel_policies)
        self._resolution_policies = tuple(trusted_resolution_policies)
        self._dispatch = dispatch
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
        try:
            group_authority.bind_reconciliation_source(proof_source)
        except Exception:
            raise ServiceExecutionGroupError("GROUP_AUTHORITY_INVALID") from None

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
                expected_ops = frozenset(
                    {"orders.submit_group_leg", "orders.resolve_group_leg"}
                )
                expected_policy_ops = {
                    "orders.submit_group_leg": self._write_policies,
                    "orders.resolve_group_leg": self._resolution_policies,
                }
                if self._cancel_policies:
                    expected_ops = expected_ops | {"orders.cancel_group_leg"}
                    expected_policy_ops["orders.cancel_group_leg"] = self._cancel_policies
                expected_writes = expected_ops
            elif registration.port == GROUP_ORDER_QUERY_PORT:
                expected_ops = frozenset({"orders.query_group_leg"})
                if self._cancel_policies:
                    expected_ops = expected_ops | {"orders.query_group_cancel"}
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
                    in {
                        "orders.submit_group_leg",
                        "orders.resolve_group_leg",
                        "orders.cancel_group_leg",
                    }
                    and getattr(policy, "account_ref", context.scope.account_ref)
                    == context.scope.account_ref
                    for policy in (
                        *self._write_policies,
                        *self._resolution_policies,
                        *self._cancel_policies,
                    )
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
            if operation == "orders.query_group_cancel":
                return self._query_group_cancel(
                    registration, context, execution_scope, arguments
                )
            if operation == "orders.resolve_group_leg":
                return self._resolve(registration, context, execution_scope, arguments)
            if operation == "orders.cancel_group_leg":
                return self._cancel_group_leg(
                    registration, context, execution_scope, arguments
                )
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
            if operation in {"orders.query_group_leg", "orders.query_group_cancel"}
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
            operation
            in {
                "orders.submit_group_leg",
                "orders.resolve_group_leg",
                "orders.cancel_group_leg",
            }
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
        if operation in {"orders.query_group_leg", "orders.query_group_cancel"}:
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
                in {
                    "orders.submit_group_leg",
                    "orders.resolve_group_leg",
                    "orders.cancel_group_leg",
                }
                and getattr(policy, "account_ref", context.scope.account_ref)
                == context.scope.account_ref
                for policy in (
                    *self._write_policies,
                    *self._resolution_policies,
                    *self._cancel_policies,
                )
            )
        policies: Sequence[object]
        if operation == "orders.submit_group_leg":
            policies = self._write_policies
        elif operation == "orders.cancel_group_leg":
            policies = self._cancel_policies
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
        try:
            reconciliation_registrations = self._reconciliation_registration_pair(
                registration,
                context,
                execution_scope,
                origin_run_id=origin_run_id,
            )
            with self._authority.bind_service_call(
                context,
                execution_scope=execution_scope,
                reconciliation_registrations=reconciliation_registrations,
            ):
                wire_scope = replace(
                    context.scope, group_id=permit.group_id, leg_id=permit.leg_id
                )
                permit_binding = self._proof_source.bind_permit(
                    permit=permit,
                    intent=intent,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    wire_scope=wire_scope,
                    service_context=context,
                )
                receipt = self._proof_source.reconcile_unknown(
                    binding=permit_binding,
                    intent=intent,
                    permit=permit,
                    principal_ref=context.principal_ref,
                    identity=context.identity,
                    wire_scope=wire_scope,
                    service_context=context,
                    active_authority=self._authority,
                    acquire_writer_lease=lambda: self._order_entry._facade_pair(
                        execution_scope
                    )[0].acquire_writer_lease(),
                )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE") from None
        return _success_value(receipt.to_payload())

    def _reconciliation_registration_pair(
        self,
        query_registration: ActiveRegistrationContext,
        query_context: ServiceCallContext,
        execution_scope: ExecutionScope,
        *,
        origin_run_id: str,
    ) -> tuple[ActiveRegistrationContext, ActiveRegistrationContext]:
        """Pair the current Registry read route with its exact owner entry route.

        A query registration has a distinct Registry registration ID.  The
        pair is accepted only from one current Registry snapshot and one
        service epoch; the durable coordinator still requires the original
        entry registration ID and generation when it reads the hold.
        """

        if (
            type(query_registration) is not ActiveRegistrationContext
            or type(query_context) is not ServiceCallContext
            # The live Registry dispatch context includes the transport-only
            # result_fits callback; the immutable registration snapshot does
            # not. Match the authenticated identity fields exactly, as
            # _authorize already does, rather than comparing that callback.
            or not _same_call(query_registration.context, query_context)
            or query_registration.port != GROUP_ORDER_QUERY_PORT
            or query_registration.strategy_write_run_id != origin_run_id
        ):
            raise ServiceExecutionGroupError("GROUP_QUERY_REGISTRATION_INVALID")
        now = self._clock_ns() / 1_000_000_000
        with self._lock:
            current_query = self._registrations.get(
                (query_context.registration_id, GROUP_ORDER_QUERY_PORT)
            )
            registrations = tuple(self._registrations.values())
        if current_query != query_registration or query_registration.expires_at <= now:
            raise ServiceExecutionGroupError("GROUP_QUERY_REGISTRATION_STALE")
        entry_operations = frozenset(
            {"orders.submit_group_leg", "orders.resolve_group_leg"}
        )
        query_operations = frozenset({"orders.query_group_leg"})
        if self._cancel_policies:
            entry_operations = entry_operations | {"orders.cancel_group_leg"}
            query_operations = query_operations | {"orders.query_group_cancel"}

        candidates = tuple(
            candidate
            for candidate in registrations
            if candidate.port == GROUP_ORDER_ENTRY_PORT
            and candidate.instance_id == query_registration.instance_id
            and candidate.context.identity == query_context.identity
            and candidate.context.principal_ref == query_context.principal_ref
            and candidate.context.scope == query_context.scope
            and candidate.service_epoch == query_registration.service_epoch
            and candidate.policy_version == query_registration.policy_version
            and candidate.requirements_version
            == query_registration.requirements_version
            and candidate.strategy_write_run_id == origin_run_id
            and candidate.context.registration_id != query_context.registration_id
            and candidate.expires_at > now
            and candidate.operations == entry_operations
            and candidate.write_operations == candidate.operations
            and self._policy_matches(candidate, "orders.submit_group_leg")
            and self._policy_matches(candidate, "orders.resolve_group_leg")
            and (
                not self._cancel_policies
                or self._policy_matches(candidate, "orders.cancel_group_leg")
            )
            and self._targets.get(candidate.context.scope) == execution_scope
        )
        if len(candidates) != 1:
            raise ServiceExecutionGroupError("GROUP_OWNER_REGISTRATION_UNAVAILABLE")
        if (
            query_registration.operations != query_operations
            or query_registration.write_operations
            or not self._policy_matches(query_registration, "orders.query_group_leg")
        ):
            raise ServiceExecutionGroupError("GROUP_QUERY_REGISTRATION_INVALID")
        return query_registration, candidates[0]

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

    def _cancel_group_leg(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "origin_run_id",
            "group_id",
            "cancel_request_id",
            "leg_id",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        origin_run_id = _reference(arguments["origin_run_id"], "GROUP_RUN_INVALID")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        cancel_request_id = _reference(
            arguments["cancel_request_id"], "GROUP_CANCEL_REQUEST_ID_INVALID"
        )
        leg_id = _reference(arguments["leg_id"], "GROUP_LEG_ID_INVALID")
        target = self._accepted_group_cancel_target(
            registration,
            context,
            execution_scope,
            origin_run_id=origin_run_id,
            group_id=group_id,
            leg_id=leg_id,
            require_open_order=False,
            allow_resolved=True,
        )
        if target is None:
            return _success({"found": False, "record": None})
        binding, cancel_intent, order_record = target
        order_intent = self._store.get_intent(order_record.intent_id, scope=execution_scope)
        if (
            type(order_intent) is not OrderIntent
            or order_intent.intent_id != cancel_intent.target_intent_id
            or order_intent.fingerprint != order_record.payload_sha256
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_UNAVAILABLE")
        existing_record = self._store.get_cancel(
            cancel_intent.cancel_id, scope=execution_scope
        )
        existing_intent = self._store.get_cancel_intent(
            cancel_intent.cancel_id, scope=execution_scope
        )
        if (existing_record is None) != (existing_intent is None) or (
            existing_record is not None
            and (
                type(existing_record) is not CancelRecord
                or existing_intent != cancel_intent
            )
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_RECORD_MISMATCH")

        # Re-read the original order and complete fill history before any new
        # cancel dispatch.  A lost/ambiguous cancel is never retried: its
        # deterministic cancel ID returns the existing record below.
        source_receipt = self._reconcile_accepted_group_order(
            registration,
            context,
            execution_scope,
            binding=binding,
            intent=order_intent,
            origin_run_id=origin_run_id,
        )
        if source_receipt.status.state not in {
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
        }:
            if existing_record is not None:
                return _success(
                    {"found": True, "record": _cancel_record_payload(existing_record)}
                )
            raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_NOT_OPEN")
        order_permit = self._permit_from_binding(binding, order_intent)
        if (
            source_receipt.claim is None
            or source_receipt.claim.permit != order_permit
            or source_receipt.resolution is not None
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_CLAIM_NOT_ACTIVE")

        active_target = self._accepted_group_cancel_target(
            registration,
            context,
            execution_scope,
            origin_run_id=origin_run_id,
            group_id=group_id,
            leg_id=leg_id,
            require_open_order=True,
        )
        if (
            active_target is None
            or active_target[0] != binding
            or active_target[1] != cancel_intent
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_CHANGED")

        def assert_current(intent: CancelIntent | None = None) -> None:
            if intent is not None and intent != cancel_intent:
                raise PermissionError("accepted group cancellation intent changed")
            current = self._accepted_group_cancel_target(
                registration,
                context,
                execution_scope,
                origin_run_id=origin_run_id,
                group_id=group_id,
                leg_id=leg_id,
                require_open_order=True,
            )
            if (
                current is None
                or current[0] != binding
                or current[1] != cancel_intent
            ):
                raise PermissionError("accepted group cancellation target changed")

        permit_reference = "group-cancel-" + cancel_intent.cancel_id
        try:
            facade = ManagedCancellationFacade(
                self._store,
                execution_scope,
                acquire_writer_lease=lambda: self._order_entry._facade_pair(
                    execution_scope
                )[0].acquire_writer_lease(),
                admission_gate=_AcceptedGroupCancelAdmissionGate(
                    cancel_intent, permit_reference, assert_current
                ),
                allow_unprotected=False,
            )
            record = facade.cancel(
                cancel_intent,
                self._dispatch,
                before_dispatch=assert_current,
            )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_CANCEL_OUTCOME_UNPROVEN") from None
        if type(record) is not CancelRecord:
            raise ServiceExecutionGroupError("GROUP_CANCEL_RECORD_INVALID")
        # The cancel record is independent of the original order claim.  In
        # particular, UNKNOWN stays query-only here; the explicit original
        # orders.query_group_leg route (or owner recovery tick) obtains the
        # order/fill source receipt that may resolve the original submit hold.
        return _success({"found": True, "record": _cancel_record_payload(record)})

    def _query_group_cancel(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {
            "origin_run_id",
            "group_id",
            "cancel_request_id",
            "cancel_id",
            "leg_id",
        }:
            raise ServiceExecutionGroupError("INVALID_REQUEST")
        origin_run_id = _reference(arguments["origin_run_id"], "GROUP_RUN_INVALID")
        group_id = _reference(arguments["group_id"], "GROUP_ID_INVALID")
        cancel_request_id = _reference(
            arguments["cancel_request_id"], "GROUP_CANCEL_REQUEST_ID_INVALID"
        )
        cancel_id = _reference(arguments["cancel_id"], "GROUP_CANCEL_ID_INVALID")
        leg_id = _reference(arguments["leg_id"], "GROUP_LEG_ID_INVALID")
        target = self._accepted_group_cancel_target(
            registration,
            context,
            execution_scope,
            origin_run_id=origin_run_id,
            group_id=group_id,
            leg_id=leg_id,
            require_open_order=False,
            allow_resolved=True,
            binding_operation="orders.query_group_cancel",
        )
        if target is None:
            return _success({"found": False, "record": None})
        _binding, cancel_intent, order_record = target
        if cancel_intent.cancel_id != cancel_id:
            raise ServiceExecutionGroupError("GROUP_CANCEL_ID_MISMATCH")
        order_intent = self._store.get_intent(order_record.intent_id, scope=execution_scope)
        if (
            type(order_intent) is not OrderIntent
            or order_intent.intent_id != cancel_intent.target_intent_id
            or order_intent.fingerprint != order_record.payload_sha256
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_UNAVAILABLE")
        record = self._store.get_cancel(cancel_id, scope=execution_scope)
        stored_intent = self._store.get_cancel_intent(cancel_id, scope=execution_scope)
        if record is None and stored_intent is None:
            return _success({"found": False, "record": None})
        if (
            type(record) is not CancelRecord
            or stored_intent != cancel_intent
            or record.scope_key != execution_scope.key
            or record.cancel_id != cancel_id
            or record.target_intent_id != cancel_intent.target_intent_id
            or record.provider_order_id != cancel_intent.provider_order_id
            or record.payload_sha256 != cancel_intent.fingerprint
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_RECORD_MISMATCH")
        # A cancellation query is read-only with respect to the target order.
        # Its independent source reconciliation is exposed by the original
        # orders.query_group_leg route and retains the original Risk hold until
        # complete order + fill evidence is available.
        return _success({"found": True, "record": _cancel_record_payload(record)})

    def _reconcile_accepted_group_order(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        *,
        binding: AcceptedGroupBinding,
        intent: OrderIntent,
        origin_run_id: str,
    ) -> GroupLegOrderReceipt:
        """Query the original accepted order through its live read/owner pair."""

        query_registration = registration
        if registration.port != GROUP_ORDER_QUERY_PORT:
            now = self._clock_ns() / 1_000_000_000
            query_operations = frozenset({"orders.query_group_leg"})
            if self._cancel_policies:
                query_operations = query_operations | {"orders.query_group_cancel"}
            with self._lock:
                registrations = tuple(self._registrations.values())
            candidates = tuple(
                candidate
                for candidate in registrations
                if candidate.port == GROUP_ORDER_QUERY_PORT
                and candidate.instance_id == registration.instance_id
                and candidate.context.identity == context.identity
                and candidate.context.principal_ref == context.principal_ref
                and candidate.context.scope == context.scope
                and candidate.service_epoch == registration.service_epoch
                and candidate.policy_version == registration.policy_version
                and candidate.requirements_version == registration.requirements_version
                and candidate.strategy_write_run_id == origin_run_id
                and candidate.operations == query_operations
                and not candidate.write_operations
                and candidate.expires_at > now
                and self._targets.get(candidate.context.scope) == execution_scope
                and self._policy_matches(candidate, "orders.query_group_leg")
            )
            if len(candidates) != 1:
                raise ServiceExecutionGroupError("GROUP_QUERY_REGISTRATION_UNAVAILABLE")
            query_registration = candidates[0]
        elif not _same_call(registration.context, context):
            raise ServiceExecutionGroupError("GROUP_QUERY_REGISTRATION_INVALID")
        query_context = query_registration.context
        reconciliation_registrations = self._reconciliation_registration_pair(
            query_registration,
            query_context,
            execution_scope,
            origin_run_id=origin_run_id,
        )
        if registration.port == GROUP_ORDER_ENTRY_PORT and (
            reconciliation_registrations[1] != registration
        ):
            raise ServiceExecutionGroupError("GROUP_OWNER_REGISTRATION_CHANGED")
        permit = self._permit_from_binding(binding, intent)
        wire_scope = replace(
            query_context.scope, group_id=permit.group_id, leg_id=permit.leg_id
        )
        try:
            with self._authority.bind_service_call(
                query_context,
                execution_scope=execution_scope,
                reconciliation_registrations=reconciliation_registrations,
            ):
                permit_binding = self._proof_source.bind_permit(
                    permit=permit,
                    intent=intent,
                    principal_ref=query_context.principal_ref,
                    identity=query_context.identity,
                    wire_scope=wire_scope,
                    service_context=query_context,
                )
                receipt = self._proof_source.reconcile_unknown(
                    binding=permit_binding,
                    intent=intent,
                    permit=permit,
                    principal_ref=query_context.principal_ref,
                    identity=query_context.identity,
                    wire_scope=wire_scope,
                    service_context=query_context,
                    active_authority=self._authority,
                    acquire_writer_lease=lambda: self._order_entry._facade_pair(
                        execution_scope
                    )[0].acquire_writer_lease(),
                )
        except ServiceExecutionGroupError:
            raise
        except Exception:
            raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE") from None
        if (
            type(receipt) is not GroupLegOrderReceipt
            or receipt.status.intent_id != intent.intent_id
        ):
            raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE")
        record = self._store.get(intent.intent_id, scope=execution_scope)
        if (
            type(record) is not ExecutionRecord
            or record.dispatch_attempts != 1
            or record.payload_sha256 != intent.fingerprint
            or record.scope_key != execution_scope.key
            or record.provider_order_id is None
            or receipt.status.provider_order_id != record.provider_order_id
        ):
            raise ServiceExecutionGroupError("GROUP_EVIDENCE_UNAVAILABLE")
        return receipt

    def _accepted_group_cancel_target(
        self,
        registration: ActiveRegistrationContext,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
        *,
        origin_run_id: str,
        group_id: str,
        leg_id: str,
        require_open_order: bool,
        allow_resolved: bool = False,
        binding_operation: str = "orders.cancel_group_leg",
    ) -> tuple[AcceptedGroupBinding, CancelIntent, ExecutionRecord] | None:
        if origin_run_id != registration.strategy_write_run_id:
            raise ServiceExecutionGroupError("GROUP_RUN_MISMATCH")
        binding = self._accepted_groups.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            operation=binding_operation,
        )
        if binding is None:
            return None
        self._validate_accepted_owner(binding, registration, origin_run_id)
        if binding.group_id != group_id or self._target_for_binding(binding) != execution_scope:
            raise ServiceExecutionGroupError("GROUP_SCOPE_MISMATCH")
        members = [(item_id, item) for item_id, item in binding.members if item_id == leg_id]
        if len(members) != 1:
            raise ServiceExecutionGroupError("GROUP_MEMBER_MISMATCH")
        _member_leg_id, reserved_intent = members[0]
        reservation = binding.reservation
        if type(reservation) is not GroupAccountReservationSnapshot:
            raise ServiceExecutionGroupError("GROUP_BINDING_INCOMPLETE")
        risk_legs = [item for item in reservation.legs if item.leg_id == leg_id]
        if len(risk_legs) != 1:
            raise ServiceExecutionGroupError("GROUP_RESERVATION_MISMATCH")
        risk_leg = risk_legs[0]
        if (
            reservation.group_id != group_id
            or reservation.account_ref != binding.account_ref
            or risk_leg.intent_id != reserved_intent.intent_id
            or (
                not allow_resolved
                and (
                    reservation.phase is not GroupReservationPhase.ACTIVE
                    or risk_leg.state != "ACTIVE"
                    or risk_leg.resolved
                )
            )
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_NOT_AUTHORIZED")
        intent = self._store.get_intent(reserved_intent.intent_id, scope=execution_scope)
        record = self._store.get(reserved_intent.intent_id, scope=execution_scope)
        if (
            type(intent) is not OrderIntent
            or type(record) is not ExecutionRecord
            or not _same_reserved_order_except_quantity(reserved_intent, intent)
            or record.scope_key != execution_scope.key
            or record.payload_sha256 != intent.fingerprint
            or type(record.group_binding) is not GroupPermitBinding
            or record.provider_order_id is None
            or not record.provider_order_id
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_UNAVAILABLE")
        permit = self._permit_from_binding(binding, intent)
        try:
            binding.permit_for(permit, intent)
            expected_group_binding = self._proof_source.bind_permit(
                permit=permit,
                intent=intent,
                principal_ref=context.principal_ref,
                identity=context.identity,
                wire_scope=replace(context.scope, group_id=group_id, leg_id=leg_id),
                service_context=context,
            )
        except Exception:
            raise ServiceExecutionGroupError("GROUP_CANCEL_BINDING_INVALID") from None
        if (
            type(expected_group_binding) is not GroupPermitBinding
            or record.group_binding != expected_group_binding
            or record.group_binding.group_id != group_id
            or record.group_binding.leg_id != leg_id
            or record.group_binding.order_intent_hash != intent.fingerprint
        ):
            raise ServiceExecutionGroupError("GROUP_CANCEL_RECORD_MISMATCH")
        if require_open_order and record.state not in {
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
        }:
            raise ServiceExecutionGroupError("GROUP_CANCEL_TARGET_NOT_OPEN")
        cancel_intent = CancelIntent(
            cancel_id=group_cancel_leg_id(
                binding.portfolio_ref,
                group_id,
                binding.origin_run_id,
                leg_id,
                binding.account_ref,
                execution_scope.key,
                intent.intent_id,
                record.provider_order_id,
            ),
            scope=execution_scope,
            target_intent_id=intent.intent_id,
            provider_order_id=record.provider_order_id,
            metadata_version="group-cancel-v1",
            tags={
                "group_id": group_id,
                "leg_id": leg_id,
                "group_order_sha256": intent.fingerprint,
            },
        )
        return binding, cancel_intent, record

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


_CANCEL_RECORD_WIRE_FIELDS = frozenset(
    {
        "cancel_id",
        "scope_key",
        "target_intent_id",
        "provider_order_id",
        "payload_sha256",
        "state",
        "dispatch_attempts",
        "review_required",
        "created_at_ns",
        "updated_at_ns",
        "is_terminal",
    }
)
_CANCEL_RECORD_WIRE_STATES = frozenset(
    {
        "PENDING_ADMISSION",
        "PENDING_DISPATCH",
        "DISPATCHING",
        "UNKNOWN",
        "ACKED",
        "CANCELLED",
        "REJECTED",
        "BLOCKED",
    }
)


def _cancel_record_payload(record: CancelRecord) -> dict[str, object]:
    """Project only identity and outcome fields from the local cancel journal."""

    if type(record) is not CancelRecord or type(record.state) is not ExecutionState:
        raise RuntimeError("cancellation record is invalid")
    payload = {
        "cancel_id": record.cancel_id,
        "scope_key": record.scope_key,
        "target_intent_id": record.target_intent_id,
        "provider_order_id": record.provider_order_id,
        "payload_sha256": record.payload_sha256,
        "state": record.state.value,
        "dispatch_attempts": record.dispatch_attempts,
        "review_required": record.review_required,
        "created_at_ns": record.created_at_ns,
        "updated_at_ns": record.updated_at_ns,
        "is_terminal": record.is_terminal,
    }
    return _validate_cancel_record_wire_payload(payload)


def _cancel_result_response(response: object) -> Mapping[str, Any]:
    body = _rpc_result(response)
    if not isinstance(body, Mapping) or set(body) != {"found", "record"}:
        raise RuntimeError("cancellation RPC reply is invalid")
    if type(body["found"]) is not bool:
        raise RuntimeError("cancellation RPC reply is invalid")
    if body["found"] is False:
        if body["record"] is not None:
            raise RuntimeError("cancellation RPC reply is invalid")
        return {"found": False, "record": None}
    return {
        "found": True,
        "record": _validate_cancel_record_wire_payload(body["record"]),
    }


def _validate_cancel_record_wire_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _CANCEL_RECORD_WIRE_FIELDS:
        raise RuntimeError("cancellation record reply is invalid")
    record = dict(value)
    if (
        any(
            type(record[name]) is not str or not record[name]
            for name in (
                "cancel_id",
                "scope_key",
                "target_intent_id",
                "provider_order_id",
            )
        )
        or type(record["payload_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", record["payload_sha256"]) is None
        or type(record["state"]) is not str
        or record["state"] not in _CANCEL_RECORD_WIRE_STATES
        or type(record["dispatch_attempts"]) is not int
        or record["dispatch_attempts"] < 0
        or type(record["review_required"]) is not bool
        or type(record["created_at_ns"]) is not int
        or record["created_at_ns"] <= 0
        or type(record["updated_at_ns"]) is not int
        or record["updated_at_ns"] < record["created_at_ns"]
        or type(record["is_terminal"]) is not bool
        or record["is_terminal"]
        != (record["state"] in {"CANCELLED", "REJECTED", "BLOCKED"})
    ):
        raise RuntimeError("cancellation record reply is invalid")
    return record


def _cancel_plan_leg_payload(item: GroupCancelPlanLeg) -> dict[str, object]:
    if type(item) is not GroupCancelPlanLeg:
        raise RuntimeError("group cancellation plan leg is invalid")
    return {
        "leg_id": item.leg_id,
        "account_ref": item.account_ref,
        "cancel_id": item.cancel_id,
        "target_intent_id": item.target_intent_id,
        "provider_order_id": item.provider_order_id,
        "phase": item.phase,
        "result": None if item.result is None else _plain_rpc_json(item.result),
        "updated_at_ns": item.updated_at_ns,
    }


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


def _submit_declared_version(arguments: Mapping[str, Any]) -> int:
    """Resolve one groups.submit body version under the frozen key whitelist.

    The legacy body carries no version discriminator and keeps its two-leg
    semantics. Version 3 is the only widened wire version: it must arrive with
    the explicit ``version`` key, and every other declared value fails closed.
    """

    keys = frozenset(arguments)
    if keys == _LEGACY_SUBMIT_KEYS:
        return 1
    if keys == _VERSIONED_SUBMIT_KEYS:
        version = arguments["version"]
        if type(version) is not int or version != _THREE_LEG_GROUP_VERSION:
            raise ServiceExecutionGroupError("GROUP_VERSION_INVALID")
        return _THREE_LEG_GROUP_VERSION
    raise ServiceExecutionGroupError("INVALID_REQUEST")


def _parse_ratio(value: object) -> Decimal:
    """Parse one version-3 wire ratio as an exact positive Decimal."""

    if type(value) is not str or _RATIO_DECIMAL.fullmatch(value) is None:
        raise ServiceExecutionGroupError("GROUP_LEG_TERMS_INVALID")
    try:
        ratio = Decimal(value)
    except Exception:
        raise ServiceExecutionGroupError("GROUP_LEG_TERMS_INVALID") from None
    if not ratio.is_finite() or ratio <= 0:
        raise ServiceExecutionGroupError("GROUP_LEG_TERMS_INVALID")
    return ratio


def _parse_roll_exit_price(value: object) -> Decimal:
    """Require a bounded canonical positive decimal from the strategy wire."""

    if (
        type(value) is not str
        or len(value) > 256
        or _RATIO_DECIMAL.fullmatch(value) is None
    ):
        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_PRICE_INVALID")
    try:
        price = Decimal(value)
    except Exception:
        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_PRICE_INVALID") from None
    if not price.is_finite() or price <= 0:
        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_PRICE_INVALID")
    try:
        # The typed contract enforces the same decimal magnitude/digit caps as
        # its persisted representation before the request can be fingerprinted.
        GroupRollExitRequestV1(
            portfolio_ref="price-check",
            group_id="price-check",
            source_leg_id="price-check",
            limit_price=price,
        )
    except (TypeError, ValueError):
        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_PRICE_INVALID") from None
    if format(price, "f") != value:
        raise ServiceExecutionGroupError("GROUP_ROLL_EXIT_PRICE_INVALID")
    return price


def _roll_exit_unknown_observation(
    authorization: GroupRollExitAuthorizationV1,
    detail_code: str,
    *,
    previous: GroupRollExitObservationV1 | None = None,
) -> GroupRollExitObservationV1:
    if previous is not None and not _roll_exit_observation_matches(
        authorization, previous.phase, previous
    ):
        previous = None
    return GroupRollExitObservationV1(
        action_id=authorization.action_id,
        phase=GroupRollExitPhase.UNKNOWN,
        source_digest=authorization.source.digest,
        request_fingerprint=authorization.request.fingerprint,
        authorization_sha256=authorization.authorization_sha256,
        intent_id=authorization.intent.intent_id,
        close_state=ExecutionState.UNKNOWN,
        requested_quantity=authorization.intent.quantity,
        filled_quantity=(Decimal(0) if previous is None else previous.filled_quantity),
        order_revision=None if previous is None else previous.order_revision,
        close_receipt_sha256=None,
        detail_code=detail_code,
        risk_intent_id=authorization.risk_intent_id,
    )


def _roll_exit_query_observation(
    record: GroupRollExitActionRecordV1,
    observed: GroupRollExitObservationV1,
) -> GroupRollExitObservationV1 | None:
    """Keep a read-only Risk query from projecting an older status as newer."""

    allowed = {
        GroupRollExitPhase.PREPARED: {
            GroupRollExitPhase.PREPARED,
            GroupRollExitPhase.SUBMIT_STARTED,
            GroupRollExitPhase.UNKNOWN,
            GroupRollExitPhase.RESOLVED,
        },
        GroupRollExitPhase.SUBMIT_STARTED: {
            GroupRollExitPhase.SUBMIT_STARTED,
            GroupRollExitPhase.STATUS_OBSERVED,
            GroupRollExitPhase.UNKNOWN,
            GroupRollExitPhase.RESOLVED,
        },
        GroupRollExitPhase.STATUS_OBSERVED: {
            GroupRollExitPhase.STATUS_OBSERVED,
            GroupRollExitPhase.UNKNOWN,
            GroupRollExitPhase.RESOLVED,
        },
        GroupRollExitPhase.UNKNOWN: {
            GroupRollExitPhase.UNKNOWN,
            GroupRollExitPhase.STATUS_OBSERVED,
            GroupRollExitPhase.RESOLVED,
        },
        GroupRollExitPhase.RESOLVED: {GroupRollExitPhase.RESOLVED},
    }
    if observed.phase not in allowed[record.phase]:
        return record.observation
    previous = record.observation
    if previous is None:
        return observed
    if (
        previous.order_revision is not None
        and observed.order_revision is not None
        and observed.order_revision < previous.order_revision
    ):
        return previous
    if observed.filled_quantity < previous.filled_quantity:
        if observed.phase is not GroupRollExitPhase.UNKNOWN:
            return previous
        observed = replace(observed, filled_quantity=previous.filled_quantity)
    if previous.order_revision is not None and observed.order_revision is None:
        if observed.phase is not GroupRollExitPhase.UNKNOWN:
            return previous
        observed = replace(observed, order_revision=previous.order_revision)
    return observed


def _roll_exit_observation_progresses(
    previous: GroupRollExitObservationV1,
    current: GroupRollExitObservationV1,
) -> bool:
    """Check monotonic status facts while replaying durable roll-exit events."""

    phase_rank = {
        GroupRollExitPhase.PREPARED: 0,
        GroupRollExitPhase.SUBMIT_STARTED: 1,
        GroupRollExitPhase.STATUS_OBSERVED: 2,
        GroupRollExitPhase.UNKNOWN: 2,
        GroupRollExitPhase.RESOLVED: 3,
    }
    if phase_rank[current.phase] < phase_rank[previous.phase]:
        return False
    if (
        previous.order_revision is not None
        and current.order_revision is not None
        and current.order_revision < previous.order_revision
    ):
        return False
    if current.filled_quantity < previous.filled_quantity:
        return False
    if (
        previous.order_revision is not None
        and current.order_revision == previous.order_revision
        and (
            current.close_state is not previous.close_state
            or current.filled_quantity != previous.filled_quantity
        )
    ):
        return False
    allowed = {
        None: {
            ExecutionState.PENDING_ADMISSION,
            ExecutionState.PENDING_DISPATCH,
            ExecutionState.DISPATCHING,
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.PENDING_ADMISSION: {
            ExecutionState.PENDING_ADMISSION,
            ExecutionState.PENDING_DISPATCH,
            ExecutionState.DISPATCHING,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.PENDING_DISPATCH: {
            ExecutionState.PENDING_DISPATCH,
            ExecutionState.DISPATCHING,
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.DISPATCHING: {
            ExecutionState.DISPATCHING,
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.ACKED: {
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.PARTIALLY_FILLED: {
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.UNKNOWN,
        },
        ExecutionState.UNKNOWN: {
            ExecutionState.UNKNOWN,
            ExecutionState.ACKED,
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
        },
        ExecutionState.FILLED: {ExecutionState.FILLED},
        ExecutionState.CANCELLED: {ExecutionState.CANCELLED},
        ExecutionState.REJECTED: {ExecutionState.REJECTED},
    }
    return current.close_state in allowed[previous.close_state]


def _owner_roll_exit_actions(
    snapshot: GroupOrderSnapshot,
    history: Sequence[object],
) -> tuple[Mapping[str, Any], ...]:
    """Rebuild the Risk action index from the exact hash-verified group journal."""

    if type(snapshot) is not GroupOrderSnapshot:
        raise ValueError("invalid roll-exit group snapshot")
    events = tuple(history)
    # This also checks revision count and strictly increasing positive global
    # SQLite event sequence values; interleaved groups may leave sequence gaps.
    group_event_chain_sha256(snapshot, events)
    actions: dict[str, dict[str, Any]] = {}
    closed = False
    for index, event in enumerate(events):
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            raise ValueError("invalid group roll-exit event payload")
        if closed:
            raise ValueError("group event follows roll-exit closure")
        if event_type == "GROUP_ROLL_EXIT_CLOSED":
            if (
                set(payload) != {"schema", "proof", "proof_sha256"}
                or payload.get("schema") != "group-roll-exit-closed-event-v1"
                or not actions
                or any(action["phase"] != "RESOLVED" for action in actions.values())
                or not isinstance(payload.get("proof"), Mapping)
            ):
                raise ValueError("invalid roll-exit closure event")
            proof = GroupRollExitClosedProofV1.from_payload(
                _plain_rpc_json(payload["proof"])
            )
            if payload.get("proof_sha256") != proof.proof_sha256:
                raise ValueError("roll-exit closure proof digest mismatch")
            if (
                snapshot.state is not GroupOrderState.CLOSED
                or proof.portfolio_ref != snapshot.request.portfolio_ref
                or proof.group_id != snapshot.request.group_id
                or proof.group_version != snapshot.request.version
                or proof.group_fingerprint != snapshot.request.fingerprint
                or proof.expected_revision + 1 != snapshot.revision
                or tuple(item.leg_id for item in proof.legs)
                != tuple(item.leg_id for item in snapshot.request.legs)
            ):
                raise ValueError("roll-exit closure proof differs from the current group")
            closed = True
            continue
        if event_type == "GROUP_ROLL_EXIT_PLAN_RECORDED":
            if set(payload) != {"action_id", "authorization_sha256", "authorization"}:
                raise ValueError("invalid roll-exit plan event")
            authorization = GroupRollExitAuthorizationV1.from_payload(
                _plain_rpc_json(payload["authorization"])
            )
            action_id = authorization.action_id
            if (
                action_id in actions
                or payload.get("action_id") != action_id
                or payload.get("authorization_sha256")
                != authorization.authorization_sha256
                or len(actions) >= 64
                or any(action["phase"] != "RESOLVED" for action in actions.values())
                or not _roll_exit_source_matches_snapshot(
                    authorization,
                    snapshot,
                    expected_plan_revision=index - 1,
                )
            ):
                raise ValueError("unbound or concurrent roll-exit plan")
            actions[action_id] = {
                "action_id": action_id,
                "plan_index": index,
                "authorization": authorization,
                "authorization_sha256": authorization.authorization_sha256,
                "phase": "PLANNED",
                "observation": None,
            }
            continue

        if event_type not in {
            "GROUP_ROLL_EXIT_SUBMIT_STARTED",
            "GROUP_ROLL_EXIT_STATUS_OBSERVED",
            "GROUP_ROLL_EXIT_RESOLVED",
        }:
            continue
        action_id = payload.get("action_id")
        action = actions.get(action_id) if type(action_id) is str else None
        if action is None:
            raise ValueError("roll-exit event has no durable plan")
        authorization = action["authorization"]
        if event_type == "GROUP_ROLL_EXIT_SUBMIT_STARTED":
            if (
                set(payload)
                != {
                    "action_id",
                    "authorization_sha256",
                    "risk_intent_id",
                    "intent_id",
                    "client_order_id",
                }
                or action["phase"] not in {"PLANNED", "PREPARED"}
                or payload.get("authorization_sha256")
                != authorization.authorization_sha256
                or payload.get("risk_intent_id") != authorization.risk_intent_id
                or payload.get("intent_id") != authorization.intent.intent_id
                or payload.get("client_order_id") != authorization.client_order_id
            ):
                raise ValueError("unbound roll-exit submit-start event")
            action["phase"] = "STARTED"
            continue

        if event_type == "GROUP_ROLL_EXIT_STATUS_OBSERVED":
            if (
                set(payload)
                != {
                    "action_id",
                    "authorization_sha256",
                    "risk_intent_id",
                    "observation",
                }
                or action["phase"] == "RESOLVED"
                or payload.get("authorization_sha256")
                != authorization.authorization_sha256
                or payload.get("risk_intent_id") != authorization.risk_intent_id
            ):
                raise ValueError("unbound roll-exit status event")
            observation = GroupRollExitObservationV1.from_payload(
                _plain_rpc_json(payload["observation"])
            )
            if (
                observation.phase is GroupRollExitPhase.RESOLVED
                or not _roll_exit_observation_matches(
                    authorization, observation.phase, observation
                )
            ):
                raise ValueError("invalid roll-exit status observation")
            if observation.phase is GroupRollExitPhase.PREPARED:
                if action["phase"] not in {"PLANNED", "PREPARED"}:
                    raise ValueError("prepared roll-exit status followed submit")
                action["phase"] = "PREPARED"
            else:
                if action["phase"] not in {"STARTED", "OBSERVED"}:
                    raise ValueError("roll-exit status preceded submit start")
                previous = action["observation"]
                if previous is not None and not _roll_exit_observation_progresses(
                    previous, observation
                ):
                    raise ValueError("roll-exit status regressed")
                action["phase"] = "OBSERVED"
            action["observation"] = observation
            continue

        if (
            set(payload)
            != {
                "action_id",
                "authorization_sha256",
                "risk_intent_id",
                "observation",
                "risk_receipt",
                "close_fee_evidence",
            }
            or action["phase"] not in {"STARTED", "OBSERVED"}
            or payload.get("authorization_sha256")
            != authorization.authorization_sha256
            or payload.get("risk_intent_id") != authorization.risk_intent_id
        ):
            raise ValueError("unbound roll-exit resolution event")
        observation = GroupRollExitObservationV1.from_payload(
            _plain_rpc_json(payload["observation"])
        )
        if (
            observation.phase is not GroupRollExitPhase.RESOLVED
            or not _roll_exit_observation_matches(
                authorization, observation.phase, observation
            )
            or _plain_rpc_json(payload.get("risk_receipt"))
            != _plain_rpc_json(observation.close_receipt)
            or _plain_rpc_json(payload.get("close_fee_evidence"))
            != (
                None
                if observation.close_fee_evidence is None
                else observation.close_fee_evidence.to_payload()
            )
            or (
                action["observation"] is not None
                and not _roll_exit_observation_progresses(
                    action["observation"], observation
                )
            )
        ):
            raise ValueError("roll-exit resolution proof differs from observation")
        action["phase"] = "RESOLVED"
        action["observation"] = observation

    if closed is not (snapshot.state is GroupOrderState.CLOSED):
        raise ValueError("roll-exit closure event and group state differ")
    return tuple(sorted(actions.values(), key=lambda action: action["plan_index"]))


def _roll_exit_action_projection(
    record: GroupRollExitActionRecordV1,
    *,
    observation: GroupRollExitObservationV1 | None = None,
) -> Mapping[str, object]:
    """Expose a bounded action status without leaking the internal authorization."""

    observation = record.observation if observation is None else observation
    return {
        "action_id": record.authorization.action_id,
        "group_id": record.authorization.request.group_id,
        "source_leg_id": record.authorization.request.source_leg_id,
        "phase": observation.phase.value if observation is not None else record.phase.value,
        "close_state": (
            None
            if observation is None or observation.close_state is None
            else observation.close_state.value
        ),
        "requested_quantity": format(record.authorization.intent.quantity, "f"),
        "filled_quantity": (
            "0" if observation is None else format(observation.filled_quantity, "f")
        ),
        "order_revision": None if observation is None else observation.order_revision,
        "detail_code": None if observation is None else observation.detail_code,
        "updated_at_ns": record.updated_at_ns,
    }


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


_GROUP_COMPENSATION_TERMINAL = frozenset(
    {ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}
)


def _owner_compensation_actions(history: Sequence[object]) -> tuple[Mapping[str, Any], ...]:
    """Validate and project the monotonic action phases from the hash-checked group chain."""

    actions: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(history):
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            raise ValueError("invalid group compensation event payload")
        if event_type == "GROUP_COMPENSATION_PLAN_RECORDED":
            if set(payload) != {"action_id", "authorization_digest", "authorization"}:
                raise ValueError("invalid group compensation plan event")
            authorization = GroupCompensationCloseAuthorization.from_payload(
                payload["authorization"]
            )
            action_id = authorization.action_id
            if (
                action_id in actions
                or payload["action_id"] != action_id
                or payload["authorization_digest"] != authorization.digest
            ):
                raise ValueError("conflicting group compensation plan event")
            actions[action_id] = {
                "plan_index": index,
                "authorization": authorization,
                "phase": GroupCompensationActionPhase.PLAN_RECORDED,
                "statuses": [],
                "receipt": None,
            }
        elif event_type == "GROUP_COMPENSATION_SUBMIT_STARTED":
            if set(payload) != {
                "action_id",
                "authorization_digest",
                "intent_id",
                "client_order_id",
            }:
                raise ValueError("invalid group compensation start event")
            action = actions.get(payload["action_id"])
            if (
                action is None
                or action["phase"] is not GroupCompensationActionPhase.PLAN_RECORDED
                or payload["authorization_digest"] != action["authorization"].digest
                or payload["intent_id"] != action["authorization"].intent_id
                or payload["client_order_id"] != action["authorization"].client_order_id
            ):
                raise ValueError("unbound group compensation start event")
            action["phase"] = GroupCompensationActionPhase.SUBMIT_STARTED
        elif event_type == "GROUP_COMPENSATION_STATUS_OBSERVED":
            if set(payload) != {"action_id", "authorization_digest", "status"}:
                raise ValueError("invalid group compensation status event")
            action = actions.get(payload["action_id"])
            if (
                action is None
                or action["phase"]
                not in {
                    GroupCompensationActionPhase.SUBMIT_STARTED,
                    GroupCompensationActionPhase.STATUS_OBSERVED,
                }
                or payload["authorization_digest"] != action["authorization"].digest
                or not isinstance(payload["status"], Mapping)
            ):
                raise ValueError("unbound group compensation status event")
            status = GroupChildOrderStatus.from_payload(payload["status"])
            authorization = action["authorization"]
            prior = action["statuses"]
            previous = (
                None if not prior else GroupChildOrderStatus.from_payload(prior[-1])
            )
            if (
                status.intent_id != authorization.intent_id
                or Fraction(status.filled_quantity) > Fraction(authorization.close_quantity)
                or (
                    previous is not None
                    and (
                        previous.state in _GROUP_COMPENSATION_TERMINAL
                        or Fraction(status.filled_quantity)
                        < Fraction(previous.filled_quantity)
                    )
                )
            ):
                raise ValueError("regressing group compensation status")
            prior.append(dict(payload["status"]))
            action["phase"] = GroupCompensationActionPhase.STATUS_OBSERVED
        elif event_type == "GROUP_COMPENSATION_RESOLVED":
            if set(payload) != {
                "action_id",
                "authorization_digest",
                "authorization",
                "close_receipt",
            }:
                raise ValueError("invalid group compensation resolution event")
            action = actions.get(payload["action_id"])
            if (
                action is None
                or action["phase"]
                not in {
                    GroupCompensationActionPhase.SUBMIT_STARTED,
                    GroupCompensationActionPhase.STATUS_OBSERVED,
                }
                or payload["authorization_digest"] != action["authorization"].digest
                or payload["authorization"] != action["authorization"].to_payload()
                or not isinstance(payload["close_receipt"], Mapping)
                or not action["statuses"]
            ):
                raise ValueError("unbound group compensation resolution event")
            authorization = action["authorization"]
            status = GroupChildOrderStatus.from_payload(action["statuses"][-1])
            receipt = dict(payload["close_receipt"])
            receipt_body = dict(receipt)
            receipt_digest = receipt_body.pop("receipt_sha256", None)
            if (
                status.state is not ExecutionState.FILLED
                or status.filled_quantity != authorization.close_quantity
                or type(receipt_digest) is not str
                or _SHA256.fullmatch(receipt_digest) is None
                or payload_sha256(receipt_body) != receipt_digest
                or receipt.get("status") != "TERMINAL_CLOSE_RESOLVED"
                or receipt.get("final_state") != ExecutionState.FILLED.value
                or receipt.get("position_effect") != PositionEffect.CLOSE.value
                or receipt.get("claim", {}).get("intent_id") != authorization.risk_intent_id
                or receipt.get("requested_quantity") != format(authorization.close_quantity, "f")
                or receipt.get("filled_quantity") != format(authorization.close_quantity, "f")
                or receipt.get("dispatch_latch_active") is not False
                or receipt.get("has_other_active_freezes") is not False
            ):
                raise ValueError("unverified group compensation close receipt")
            action["phase"] = GroupCompensationActionPhase.RESOLVED
            action["receipt"] = receipt
        elif event_type == "GROUP_COMPENSATION_CLOSED":
            # Closure is a separate terminal event and cannot be followed by
            # another action in a valid snapshot.
            if set(actions) == set():
                raise ValueError("compensation closure has no resolved action")

    pending = [
        item
        for item in actions.values()
        if item["phase"] is not GroupCompensationActionPhase.RESOLVED
    ]
    if len(pending) > 1 or len(actions) > 9:
        raise ValueError("unbounded group compensation action history")
    return tuple(sorted(actions.values(), key=lambda item: item["plan_index"]))


def _group_source_is_compensation_ready(snapshot: GroupOrderSnapshot) -> bool:
    """Require terminal, proof-resolved source legs before any compensating write."""

    request = snapshot.request
    if request.version != 3 or len(request.legs) != 3:
        return False
    progress_by_id = {item.leg_id: item for item in snapshot.legs}
    now_ns = time.time_ns()
    for plan in request.legs:
        progress = progress_by_id.get(plan.leg_id)
        if progress is None:
            return False
        intent = progress.submitted_intent
        if intent is None:
            if (
                progress.phase.value != "READY"
                or progress.child_status is not None
                or progress.claim_receipt is not None
                or progress.resolution_receipt is not None
                or now_ns <= request.deadline_ns
            ):
                return False
            continue
        status = progress.child_status
        claim = progress.claim_receipt
        resolution = progress.resolution_receipt
        if (
            status is None
            or claim is None
            or resolution is None
            or progress.phase.value != "OBSERVED"
            or intent.intent_id != plan.maximum_intent.intent_id
            or intent.position_effect is not PositionEffect.OPEN
            or intent.quantity <= 0
            or intent.quantity > plan.maximum_intent.quantity
            or status.intent_id != intent.intent_id
            or status.state not in _GROUP_COMPENSATION_TERMINAL
            or status.filled_quantity < 0
            or status.filled_quantity > intent.quantity
            or claim.permit.group_id != request.group_id
            or claim.permit.leg_id != plan.leg_id
            or claim.permit.order_intent_hash != intent.fingerprint
            or resolution.claim != claim
            or resolution.final_state is not status.state
            or resolution.filled_quantity != status.filled_quantity
            or resolution.dispatch_latch_active
            or (
                status.filled_quantity > 0
                and (
                    resolution.resolution_kind != "TERMINAL_EXPOSURE_RESOLVED"
                    or resolution.trade_count <= 0
                )
            )
            or (
                status.filled_quantity == 0
                and resolution.resolution_kind != "TERMINAL_NO_FILL_RESOLVED"
            )
        ):
            return False
    return True


def _owner_signed_filled_exposure(
    snapshot: GroupOrderSnapshot,
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Fraction] | None:
    """Compute source fills minus only fully verified, resolved CLOSE fills."""

    request = snapshot.request
    progress_by_id = {item.leg_id: item for item in snapshot.legs}
    exposure: dict[str, Fraction] = {}
    for plan in request.legs:
        progress = progress_by_id[plan.leg_id]
        status = progress.child_status
        intent = progress.submitted_intent
        if intent is None or status is None:
            exposure[plan.leg_id] = Fraction(0)
            continue
        if status.state not in _GROUP_COMPENSATION_TERMINAL:
            return None
        sign = 1 if intent.side is Side.BUY else -1
        exposure[plan.leg_id] = (
            sign * Fraction(status.filled_quantity) * Fraction(plan.base_units_per_order_unit)
        )

    for action in actions:
        if action.get("phase") is not GroupCompensationActionPhase.RESOLVED:
            return None
        authorization = action.get("authorization")
        receipt = action.get("receipt")
        if (
            type(authorization) is not GroupCompensationCloseAuthorization
            or not isinstance(receipt, Mapping)
            or authorization.source_leg_id not in exposure
        ):
            return None
        selected = authorization.source_leg_id
        plan = next(plan for plan in request.legs if plan.leg_id == selected)
        vector = tuple(
            (plan_item.leg_id, _fraction_decimal(exposure[plan_item.leg_id]))
            for plan_item in request.legs
        )
        if (
            tuple(authorization.source_filled_position_exposure_vector_base_units) != vector
            or Fraction(authorization.source_uncompensated_position_base_units)
            != exposure[selected]
            or exposure[selected] == 0
            or authorization.source_base_units_per_order_unit != plan.base_units_per_order_unit
            or Fraction(authorization.close_quantity)
            * Fraction(plan.base_units_per_order_unit)
            > abs(exposure[selected])
        ):
            return None
        close_sign = 1 if authorization.closing_side is Side.BUY else -1
        exposure[selected] += (
            close_sign
            * Fraction(authorization.close_quantity)
            * Fraction(plan.base_units_per_order_unit)
        )
    return exposure


def _group_has_filled_source(snapshot: GroupOrderSnapshot) -> bool:
    return any(
        progress.submitted_intent is not None
        and progress.child_status is not None
        and progress.child_status.filled_quantity > 0
        for progress in snapshot.legs
    )


def _fraction_decimal(value: Fraction) -> Decimal:
    """Convert a finite exact rational into Decimal without context rounding."""

    if type(value) is not Fraction:
        raise ValueError("fraction required")
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    scale = max(twos, fives)
    if denominator != 1 or scale > 512 or len(str(abs(value.numerator))) > 1024:
        raise ValueError("fraction is not a bounded finite decimal")
    unscaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if unscaled < 0 else ""
    digits = str(abs(unscaled))
    if scale:
        digits = digits.rjust(scale + 1, "0")
        text = sign + digits[:-scale] + "." + digits[-scale:]
    else:
        text = sign + digits
    result = Decimal(text)
    if Fraction(result) != value:
        raise ValueError("fraction conversion was not exact")
    return result


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
