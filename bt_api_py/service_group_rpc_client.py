"""Typed client for registered service group-order and residual routes.

This module owns the SDK-side wire boundary shared by host frameworks.  It
validates exact registered group/Monitor bindings, projects typed execution
requests onto ``ServiceClientRuntime.call``, classifies definite-not-sent and
unknown outcomes, and parses the remote Monitor residual page contract.

Frameworks remain responsible for binding their own Strategy/Broker objects
and for starting/closing the runtimes that they pass in these routes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Optional

__all__ = [
    "ServiceGroupError",
    "ServiceGroupOutcomeUnknownError",
    "ServiceGroupRecovery",
    "ServiceGroupResidual",
    "ServiceGroupResidualCursor",
    "ServiceGroupResidualPage",
    "ServiceGroupRequestNotSentError",
    "ServiceGroupRollExitUnknownOutcomeError",
    "ServiceGroupRoute",
    "ServiceGroupRpcClient",
]

_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_EVENT_ID = re.compile(r"^service-event:[0-9a-f]{64}$")
_RPC_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")
_DECIMAL_TEXT = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_GROUP_MOUNT = "broker.order_groups"
_GROUP_OPERATIONS = frozenset({"groups.submit", "groups.query"})
_GROUP_ROLL_EXIT_OPERATIONS = _GROUP_OPERATIONS | frozenset(
    {"groups.roll_exit", "groups.query_roll_exit"}
)
_DEFINITE_PRE_DISPATCH_GROUP_SUBMIT_REJECTIONS = frozenset(
    {"GROUP_ACCEPTANCE_REJECTED"}
)
_DEFINITE_PRE_DISPATCH_ROLL_EXIT_REJECTIONS = frozenset(
    {
        "INVALID_REQUEST",
        "GROUP_ID_INVALID",
        "GROUP_LEG_INVALID",
        "GROUP_ROLL_EXIT_PRICE_INVALID",
        "GROUP_ROLL_EXIT_NOT_AUTHORIZED",
        "GROUP_ROLL_EXIT_SOURCE_LEG_INVALID",
        "GROUP_ROLL_EXIT_TARGET_UNAVAILABLE",
        "GROUP_ROLL_EXIT_PREPARE_UNAVAILABLE",
        "OPERATION_NOT_GRANTED",
        "REGISTRATION_EXPIRED",
    }
)
_ROLL_EXIT_PHASES = frozenset(
    {"PREPARED", "SUBMIT_STARTED", "STATUS_OBSERVED", "UNKNOWN", "RESOLVED"}
)
_ROLL_EXIT_CLOSE_STATES = frozenset(
    {
        "PENDING_ADMISSION",
        "PENDING_DISPATCH",
        "DISPATCHING",
        "ACKED",
        "PARTIALLY_FILLED",
        "UNKNOWN",
        "FILLED",
        "CANCELLED",
        "REJECTED",
    }
)
_MONITOR_MOUNT = "broker.group_monitor"
_MONITOR_OPERATIONS = frozenset({"monitor.read_after"})
_RESIDUAL_EVENT_TYPE = "group.residual"
_RESIDUAL_SCHEMA = "sdk.group_monitor_projection.v1"
_RESIDUAL_STATE = "RECOVERY_REQUIRED"
_PAIR_RESIDUAL_REASON = "hedge_residual_unresolved"
_CURSOR_SCHEMA = "backtrader.service_group_residual_cursor.v1"
_MAX_RESIDUAL_PAGE_SIZE = 100
_RESIDUAL_DATA_FIELDS = frozenset(
    {
        "schema",
        "journal_generation",
        "source_sequence",
        "source_event_sha256",
        "source_revision",
        "source_created_at_ns",
        "portfolio_ref",
        "group_id",
        "strategy_id",
        "account_refs",
        "state",
        "reason",
        "residual_base_units",
        "first_leg_id",
        "second_leg_id",
        "first_filled_quantity",
        "second_filled_quantity",
    }
)


class ServiceGroupError(RuntimeError):
    """Finite diagnostics for rejected group RPCs or residual pages."""

    def __init__(self, code: str, *, group_id: Optional[str] = None) -> None:
        self.code = code
        self.group_id = group_id
        super().__init__(code)


class ServiceGroupRequestNotSentError(ServiceGroupError):
    """The SDK transport proved the request was not queued for delivery."""

    def __init__(self, *, group_id: str) -> None:
        super().__init__("REQUEST_NOT_SENT", group_id=group_id)


class ServiceGroupOutcomeUnknownError(ServiceGroupError):
    """The RPC may have reached the coordinator; recover by the original ID."""

    def __init__(self, *, group_id: str) -> None:
        super().__init__("REQUEST_OUTCOME_UNKNOWN", group_id=group_id)


class ServiceGroupRollExitUnknownOutcomeError(ServiceGroupOutcomeUnknownError):
    """A roll-exit RPC may have advanced and needs identical-request recovery."""

    def __init__(
        self,
        *,
        group_id: str,
        source_leg_id: str,
        detail_code: Optional[str] = None,
    ) -> None:
        super().__init__(group_id=group_id)
        self.source_leg_id = source_leg_id
        self.detail_code = detail_code


@dataclass(frozen=True)
class ServiceGroupRoute:
    """One Strategy/portfolio's exact coordinator and Monitor route.

    ``owned`` is consumed only by the host framework's runtime lifecycle. The
    SDK client validates the service identity and scopes, but never starts or
    closes the runtime.
    """

    strategy_ref: str
    portfolio_ref: str
    account_refs: tuple[str, ...]
    runtime: Any
    group_scope_ref: str
    monitor_scope_ref: str
    owned: bool = True
    monitor_producer_principal_ref: Optional[str] = None
    monitor_producer_actor_ref: Optional[str] = None
    monitor_producer_delegate_ref: Optional[str] = None

    def __post_init__(self) -> None:
        for name in (
            "strategy_ref",
            "portfolio_ref",
            "group_scope_ref",
            "monitor_scope_ref",
        ):
            _require_reference(getattr(self, name), "INVALID_GROUP_ROUTE")
        if (
            type(self.account_refs) is not tuple
            or not self.account_refs
            or any(not _valid_reference(value) for value in self.account_refs)
            or tuple(sorted(set(self.account_refs))) != self.account_refs
            or type(self.owned) is not bool
        ):
            raise ServiceGroupError("INVALID_GROUP_ROUTE")
        if self.runtime is None:
            raise ServiceGroupError("INVALID_GROUP_ROUTE")
        if not _valid_monitor_producer_binding(self):
            raise ServiceGroupError("GROUP_MONITOR_PRODUCER_IDENTITY_INVALID")


@dataclass(frozen=True)
class ServiceGroupResidualCursor:
    """Caller-persistable Monitor cursor bound to one Strategy and portfolio."""

    strategy_ref: str
    portfolio_ref: str
    sequence: int = 0
    event_id: Optional[str] = None
    journal_generation: Optional[str] = None

    def __post_init__(self) -> None:
        _require_reference(self.strategy_ref, "INVALID_RESIDUAL_CURSOR")
        _require_reference(self.portfolio_ref, "INVALID_RESIDUAL_CURSOR")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ServiceGroupError("INVALID_RESIDUAL_CURSOR")
        if self.sequence == 0:
            if self.event_id is not None or self.journal_generation is not None:
                raise ServiceGroupError("INVALID_RESIDUAL_CURSOR")
        elif (
            not _valid_text(self.event_id)
            or type(self.journal_generation) is not str
            or _SHA256.fullmatch(self.journal_generation) is None
        ):
            raise ServiceGroupError("INVALID_RESIDUAL_CURSOR")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _CURSOR_SCHEMA,
            "strategy_ref": self.strategy_ref,
            "portfolio_ref": self.portfolio_ref,
            "sequence": self.sequence,
            "event_id": self.event_id,
            "journal_generation": self.journal_generation,
        }

    @classmethod
    def from_payload(cls, value: object) -> "ServiceGroupResidualCursor":
        expected = {
            "schema",
            "strategy_ref",
            "portfolio_ref",
            "sequence",
            "event_id",
            "journal_generation",
        }
        if (
            not isinstance(value, Mapping)
            or frozenset(value) != frozenset(expected)
            or value.get("schema") != _CURSOR_SCHEMA
        ):
            raise ServiceGroupError("INVALID_RESIDUAL_CURSOR")
        return cls(
            strategy_ref=value["strategy_ref"],
            portfolio_ref=value["portfolio_ref"],
            sequence=value["sequence"],
            event_id=value["event_id"],
            journal_generation=value["journal_generation"],
        )


@dataclass(frozen=True)
class ServiceGroupResidual:
    """One validated, immutable durable group residual fact."""

    sequence: int
    event_id: str
    journal_generation: str
    portfolio_ref: str
    group_id: str
    strategy_ref: str
    account_refs: tuple[str, ...]
    state: str
    reason: str
    residual_base_units: Optional[str]
    first_leg_id: Optional[str]
    second_leg_id: Optional[str]
    first_filled_quantity: Optional[str]
    second_filled_quantity: Optional[str]
    source_event_sha256: str
    source_revision: int
    source_created_at_ns: int


@dataclass(frozen=True)
class ServiceGroupResidualPage:
    """One repeatable Monitor page and the cursor the caller may persist."""

    events: tuple[ServiceGroupResidual, ...]
    cursor: ServiceGroupResidualCursor
    has_more: bool


@dataclass(frozen=True)
class ServiceGroupRecovery:
    """Read-only recovery projection for an original group ID."""

    snapshot: Any
    residual_page: ServiceGroupResidualPage


class ServiceGroupRpcClient:
    """Registered SDK client for group RPCs and Monitor residual reads.

    The client owns route validation and wire contracts. It does not own the
    runtime, the Backtrader session, or any Strategy/Broker object.
    """

    def __init__(self, routes: tuple[ServiceGroupRoute, ...]) -> None:
        if type(routes) is not tuple or any(
            type(route) is not ServiceGroupRoute for route in routes
        ):
            raise ServiceGroupError("INVALID_GROUP_ROUTES")
        self._routes = routes
        self._route_by_key = {
            (route.strategy_ref, route.portfolio_ref): route for route in routes
        }
        self._roll_exit_route_keys: frozenset[tuple[str, str]] = frozenset()
        self._registered = False
        self._owner_thread_id = threading.get_ident()

    def validate_registered_routes(self) -> None:
        """Validate exact runtime identities, registered bindings and scopes."""

        from bt_api_py.service_contracts import WireIdentity, WireRole, WireScope
        from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

        resolved = set()
        roll_exit_route_keys = set()
        runtime_owner: dict[int, str] = {}
        for route in self._routes:
            runtime = route.runtime
            if not isinstance(runtime, ServiceClientRuntime):
                raise ServiceGroupError("GROUP_RUNTIME_TYPE_INVALID")
            if not _valid_monitor_producer_binding(route):
                raise ServiceGroupError("GROUP_MONITOR_PRODUCER_IDENTITY_INVALID")
            if getattr(runtime, "state", None) != "registered":
                raise ServiceGroupError("GROUP_RUNTIME_NOT_REGISTERED")
            identity = getattr(runtime, "identity", None)
            if (
                type(identity) is not WireIdentity
                or identity.role is not WireRole.STRATEGY_CLIENT
                or identity.actor_ref != route.strategy_ref
                or not _valid_text(identity.principal_ref)
            ):
                raise ServiceGroupError("GROUP_STRATEGY_IDENTITY_MISMATCH")
            prior_strategy = runtime_owner.get(id(runtime))
            if prior_strategy is not None and prior_strategy != route.strategy_ref:
                raise ServiceGroupError("GROUP_RUNTIME_STRATEGY_AMBIGUOUS")
            runtime_owner[id(runtime)] = route.strategy_ref
            try:
                bindings = runtime.bindings
            except Exception:
                raise ServiceGroupError("GROUP_RUNTIME_BINDINGS_UNAVAILABLE") from None
            if type(bindings) is not tuple or any(
                type(binding) is not RegisteredBinding for binding in bindings
            ):
                raise ServiceGroupError("GROUP_RUNTIME_BINDINGS_INVALID")
            group_binding = _unique_group_binding(
                bindings, _GROUP_MOUNT, route.group_scope_ref
            )
            monitor_binding = _unique_binding(
                bindings,
                _MONITOR_MOUNT,
                route.monitor_scope_ref,
                _MONITOR_OPERATIONS,
            )
            group_scope = getattr(group_binding, "scope", None)
            monitor_scope = getattr(monitor_binding, "scope", None)
            if type(group_scope) is not WireScope or type(monitor_scope) is not WireScope:
                raise ServiceGroupError("GROUP_RUNTIME_SCOPE_INVALID")
            if not _valid_group_scope(route, runtime, group_scope):
                raise ServiceGroupError("GROUP_SCOPE_MISMATCH")
            if not _valid_monitor_scope(route, runtime, group_scope, monitor_scope):
                raise ServiceGroupError("GROUP_MONITOR_SCOPE_MISMATCH")
            key = (route.strategy_ref, route.portfolio_ref)
            if key in resolved:
                raise ServiceGroupError("GROUP_ROUTE_AMBIGUOUS")
            resolved.add(key)
            if group_binding.operations == _GROUP_ROLL_EXIT_OPERATIONS:
                roll_exit_route_keys.add(key)
        self._roll_exit_route_keys = frozenset(roll_exit_route_keys)
        self._registered = True

    def deactivate(self) -> None:
        """Fence new calls before the external runtime owner closes routes."""

        self._registered = False

    def route_for(self, strategy_ref: str, portfolio_ref: str) -> ServiceGroupRoute:
        """Return the exact route selected by the validated SDK registry."""

        self._assert_active()
        _require_reference(strategy_ref, "STRATEGY_GROUP_IDENTITY_INCOMPLETE")
        _require_reference(portfolio_ref, "STRATEGY_GROUP_IDENTITY_INCOMPLETE")
        route = self._route_by_key.get((strategy_ref, portfolio_ref))
        if route is None:
            raise ServiceGroupError("GROUP_ROUTE_UNAVAILABLE")
        return route

    def submit(self, route: ServiceGroupRoute, intent: Any) -> Any:
        route = self._require_route(route)
        from bt_api_py.service_group_contracts import GroupOrderIntent

        if type(intent) is not GroupOrderIntent:
            raise ServiceGroupError("GROUP_INTENT_TYPE_INVALID")
        if (
            intent.version not in {1, 3}
            or intent.budget_quote is not None
            or intent.strategy_id != route.strategy_ref
            or intent.portfolio_ref != route.portfolio_ref
            or intent.account_refs != route.account_refs
        ):
            raise ServiceGroupError("P5A_GROUP_SCOPE_MISMATCH", group_id=intent.group_id)
        arguments: dict[str, Any] = {
            "group_id": intent.group_id,
            "deadline_ns": intent.deadline_ns,
            "first_leg_id": intent.first_leg_id,
        }
        if intent.version == 3:
            arguments["version"] = 3
        arguments["legs"] = []
        for leg in intent.legs:
            leg_payload = {
                "leg_id": leg.leg_id,
                "account_ref": leg.account_ref,
                "intent": leg.maximum_intent.to_payload(),
            }
            if intent.version == 3:
                if leg.ratio is None:
                    raise ServiceGroupError(
                        "GROUP_INTENT_RATIO_INVALID", group_id=intent.group_id
                    )
                leg_payload["ratio"] = format(leg.ratio, "f")
            arguments["legs"].append(leg_payload)
        try:
            result = route.runtime.call(
                _GROUP_MOUNT,
                "groups.submit",
                arguments,
                portfolio_ref=route.portfolio_ref,
                scope_ref=route.group_scope_ref,
            )
        except Exception as error:
            _raise_submit_error(error, intent.group_id)
        try:
            body = _group_reply_result(result, group_id=intent.group_id)
            return _snapshot_from_reply(body, expected_intent=intent)
        except ServiceGroupError as error:
            if error.code in _DEFINITE_PRE_DISPATCH_GROUP_SUBMIT_REJECTIONS:
                raise
            raise ServiceGroupOutcomeUnknownError(group_id=intent.group_id) from None
        except Exception:
            raise ServiceGroupOutcomeUnknownError(group_id=intent.group_id) from None

    def query(self, route: ServiceGroupRoute, group_id: str) -> Any:
        route = self._require_route(route)
        _require_reference(group_id, "INVALID_GROUP_ID")
        try:
            result = route.runtime.call(
                _GROUP_MOUNT,
                "groups.query",
                {"group_id": group_id},
                portfolio_ref=route.portfolio_ref,
                scope_ref=route.group_scope_ref,
            )
        except Exception:
            raise ServiceGroupError("GROUP_QUERY_FAILED", group_id=group_id) from None
        try:
            body = _group_reply_result(result, group_id=group_id)
            if (
                not isinstance(body, Mapping)
                or frozenset(body) != frozenset({"found", "snapshot"})
                or type(body["found"]) is not bool
            ):
                raise ValueError
            if body["found"] is False:
                if body["snapshot"] is not None:
                    raise ValueError
                return None
            return _snapshot_from_reply(
                body, expected_identity=route, group_id=group_id
            )
        except ServiceGroupError:
            raise
        except Exception:
            raise ServiceGroupError(
                "GROUP_QUERY_RESPONSE_INVALID", group_id=group_id
            ) from None

    def roll_exit(
        self,
        route: ServiceGroupRoute,
        group_id: str,
        source_leg_id: str,
        limit_price: Decimal,
    ) -> Mapping[str, Any]:
        """Submit only a source-bound group close request through its route."""

        route = self._require_route(route)
        route_key = (route.strategy_ref, route.portfolio_ref)
        if route_key not in self._roll_exit_route_keys:
            raise ServiceGroupError("GROUP_ROLL_EXIT_ROUTE_UNAVAILABLE", group_id=group_id)
        _require_reference(group_id, "INVALID_GROUP_ID")
        _require_reference(source_leg_id, "INVALID_GROUP_LEG_ID")
        try:
            from bt_api_py.service_group_roll_exit import GroupRollExitRequestV1
        except ImportError:
            raise ServiceGroupError(
                "GROUP_ROLL_EXIT_CONTRACT_UNAVAILABLE", group_id=group_id
            ) from None
        try:
            request = GroupRollExitRequestV1(
                portfolio_ref=route.portfolio_ref,
                group_id=group_id,
                source_leg_id=source_leg_id,
                limit_price=limit_price,
            )
        except Exception:
            raise ServiceGroupError(
                "GROUP_ROLL_EXIT_REQUEST_INVALID", group_id=group_id
            ) from None
        arguments = {
            "group_id": request.group_id,
            "source_leg_id": request.source_leg_id,
            "limit_price": format(request.limit_price, "f"),
        }
        try:
            result = route.runtime.call(
                _GROUP_MOUNT,
                "groups.roll_exit",
                arguments,
                portfolio_ref=route.portfolio_ref,
                scope_ref=route.group_scope_ref,
            )
        except Exception as error:
            _raise_roll_exit_call_error(error, group_id, source_leg_id)
        try:
            body = _group_reply_result(result, group_id=group_id)
            return _roll_exit_reply(
                body,
                group_id=group_id,
                source_leg_id=source_leg_id,
                action_id=None,
                allow_not_found=False,
            )
        except ServiceGroupError as error:
            if error.code in _DEFINITE_PRE_DISPATCH_ROLL_EXIT_REJECTIONS:
                raise
            raise ServiceGroupRollExitUnknownOutcomeError(
                group_id=group_id,
                source_leg_id=source_leg_id,
                detail_code=error.code,
            ) from None
        except Exception:
            raise ServiceGroupRollExitUnknownOutcomeError(
                group_id=group_id, source_leg_id=source_leg_id
            ) from None

    def query_roll_exit(
        self, route: ServiceGroupRoute, action_id: str
    ) -> Mapping[str, Any]:
        """Read one persisted roll-exit projection without dispatching work."""

        route = self._require_route(route)
        route_key = (route.strategy_ref, route.portfolio_ref)
        if route_key not in self._roll_exit_route_keys:
            raise ServiceGroupError("GROUP_ROLL_EXIT_ROUTE_UNAVAILABLE")
        _require_reference(action_id, "INVALID_GROUP_ROLL_EXIT_ACTION_ID")
        try:
            result = route.runtime.call(
                _GROUP_MOUNT,
                "groups.query_roll_exit",
                {"action_id": action_id},
                portfolio_ref=route.portfolio_ref,
                scope_ref=route.group_scope_ref,
            )
        except Exception:
            raise ServiceGroupError("GROUP_ROLL_EXIT_QUERY_FAILED") from None
        try:
            body = _group_reply_result(result, group_id=action_id)
            return _roll_exit_reply(
                body,
                group_id=None,
                source_leg_id=None,
                action_id=action_id,
                allow_not_found=True,
            )
        except ServiceGroupError:
            raise
        except Exception:
            raise ServiceGroupError("GROUP_ROLL_EXIT_QUERY_RESPONSE_INVALID") from None

    def read_residuals(
        self,
        route: ServiceGroupRoute,
        cursor: Optional[ServiceGroupResidualCursor] = None,
        *,
        limit: int = 32,
    ) -> ServiceGroupResidualPage:
        route = self._require_route(route)
        if type(limit) is not int or not 1 <= limit <= _MAX_RESIDUAL_PAGE_SIZE:
            raise ServiceGroupError("INVALID_RESIDUAL_LIMIT")
        if cursor is not None and type(cursor) is not ServiceGroupResidualCursor:
            raise ServiceGroupError("INVALID_RESIDUAL_CURSOR")
        if cursor is not None and (
            cursor.strategy_ref != route.strategy_ref
            or cursor.portfolio_ref != route.portfolio_ref
        ):
            raise ServiceGroupError("RESIDUAL_CURSOR_SCOPE_MISMATCH")
        after_sequence = 0 if cursor is None else cursor.sequence
        if cursor is not None and cursor.sequence:
            anchor = self._read_monitor_page(route, cursor.sequence - 1, 1)
            if (
                len(anchor) != 1
                or anchor[0].sequence != cursor.sequence
                or anchor[0].event_id != cursor.event_id
                or anchor[0].journal_generation != cursor.journal_generation
            ):
                raise ServiceGroupError("RESIDUAL_CURSOR_ANCHOR_MISMATCH")
        events, has_more, returned_cursor = self._read_monitor_page(
            route, after_sequence, limit, include_page=True
        )
        if cursor is not None and cursor.journal_generation is not None and any(
            item.journal_generation != cursor.journal_generation for item in events
        ):
            raise ServiceGroupError("RESIDUAL_JOURNAL_GENERATION_CHANGED")
        if events:
            last = events[-1]
            next_cursor = ServiceGroupResidualCursor(
                strategy_ref=route.strategy_ref,
                portfolio_ref=route.portfolio_ref,
                sequence=last.sequence,
                event_id=last.event_id,
                journal_generation=last.journal_generation,
            )
        elif cursor is not None:
            next_cursor = cursor
        else:
            next_cursor = ServiceGroupResidualCursor(
                route.strategy_ref, route.portfolio_ref
            )
        if returned_cursor != (events[-1].sequence if events else after_sequence):
            raise ServiceGroupError("RESIDUAL_MONITOR_CURSOR_INVALID")
        return ServiceGroupResidualPage(tuple(events), next_cursor, has_more)

    def recover(
        self,
        route: ServiceGroupRoute,
        group_id: str,
        cursor: Optional[ServiceGroupResidualCursor] = None,
        *,
        limit: int = 32,
    ) -> ServiceGroupRecovery:
        """Recover by querying the original ID, then resuming the Monitor cursor."""

        snapshot = self.query(route, group_id)
        residual_page = self.read_residuals(route, cursor, limit=limit)
        return ServiceGroupRecovery(snapshot, residual_page)

    def _read_monitor_page(
        self,
        route: ServiceGroupRoute,
        after_sequence: int,
        limit: int,
        *,
        include_page: bool = False,
    ) -> Any:
        try:
            result = route.runtime.call(
                _MONITOR_MOUNT,
                "monitor.read_after",
                {"after_sequence": after_sequence, "limit": limit},
                portfolio_ref=route.portfolio_ref,
                scope_ref=route.monitor_scope_ref,
            )
        except Exception:
            raise ServiceGroupError("RESIDUAL_MONITOR_READ_FAILED") from None
        try:
            if not isinstance(result, Mapping) or frozenset(result) != frozenset(
                {"requested_cursor", "next_cursor", "has_more", "events"}
            ):
                raise ValueError
            if (
                type(result["requested_cursor"]) is not int
                or result["requested_cursor"] != after_sequence
                or type(result["next_cursor"]) is not int
                or type(result["has_more"]) is not bool
                or not isinstance(result["events"], (list, tuple))
                or (result["has_more"] and len(result["events"]) != limit)
            ):
                raise ValueError
            events = tuple(
                _parse_residual_event(event, route) for event in result["events"]
            )
            sequences = tuple(item.sequence for item in events)
            if any(value <= after_sequence for value in sequences) or any(
                left >= right for left, right in zip(sequences, sequences[1:])
            ):
                raise ValueError
            if len(events) > limit:
                raise ValueError
            expected_next = events[-1].sequence if events else after_sequence
            if result["next_cursor"] != expected_next:
                raise ValueError
            if include_page:
                return events, result["has_more"], result["next_cursor"]
            return events
        except ServiceGroupError:
            raise
        except Exception:
            raise ServiceGroupError("RESIDUAL_MONITOR_PAGE_INVALID") from None

    def _require_route(self, route: ServiceGroupRoute) -> ServiceGroupRoute:
        self._assert_active()
        if type(route) is not ServiceGroupRoute:
            raise ServiceGroupError("GROUP_ROUTE_UNAVAILABLE")
        registered = self._route_by_key.get((route.strategy_ref, route.portfolio_ref))
        if registered is not route:
            raise ServiceGroupError("GROUP_ROUTE_UNAVAILABLE")
        return route

    def _assert_active(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceGroupError("WRONG_RUNTIME_THREAD")
        if not self._registered:
            raise ServiceGroupError("GROUP_CLIENT_NOT_ACTIVE")


def _unique_binding(
    bindings: tuple[Any, ...], mount: str, scope_ref: str, operations: frozenset[str]
) -> Any:
    matches = tuple(
        binding
        for binding in bindings
        if binding.active is True
        and binding.mount == mount
        and binding.scope_ref == scope_ref
        and binding.operations == operations
    )
    if len(matches) != 1:
        raise ServiceGroupError("GROUP_ROUTE_MISSING_OR_AMBIGUOUS")
    return matches[0]


def _unique_group_binding(
    bindings: tuple[Any, ...], mount: str, scope_ref: str
) -> Any:
    matches = tuple(
        binding
        for binding in bindings
        if binding.active is True
        and binding.mount == mount
        and binding.scope_ref == scope_ref
    )
    if len(matches) != 1 or matches[0].operations not in {
        _GROUP_OPERATIONS,
        _GROUP_ROLL_EXIT_OPERATIONS,
    }:
        raise ServiceGroupError("GROUP_ROUTE_MISSING_OR_AMBIGUOUS")
    return matches[0]


def _valid_group_scope(route: ServiceGroupRoute, runtime: Any, scope: Any) -> bool:
    return (
        scope.account_ref is None
        and scope.account_refs == route.account_refs
        and scope.strategy_ref == route.strategy_ref
        and scope.portfolio_ref == route.portfolio_ref
        and scope.run_id == runtime.run_id
        and _valid_reference(scope.execution_domain_ref)
        and scope.manual_book_ref is None
        and scope.group_id is None
        and scope.leg_id is None
    )


def _valid_monitor_scope(
    route: ServiceGroupRoute, runtime: Any, group_scope: Any, monitor_scope: Any
) -> bool:
    return (
        monitor_scope.account_ref is None
        and monitor_scope.account_refs == ()
        and monitor_scope.strategy_ref == route.strategy_ref
        and monitor_scope.portfolio_ref == route.portfolio_ref
        and monitor_scope.run_id == runtime.run_id
        and monitor_scope.execution_domain_ref == group_scope.execution_domain_ref
        and monitor_scope.manual_book_ref is None
        and monitor_scope.group_id is None
        and monitor_scope.leg_id is None
    )


def _snapshot_from_reply(
    value: object,
    *,
    expected_intent: Any = None,
    expected_identity: Any = None,
    group_id: Optional[str] = None,
) -> Any:
    from bt_api_py.service_group_contracts import parse_group_order_snapshot

    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"found", "snapshot"}
    ):
        raise ValueError
    if value["found"] is not True or not isinstance(value["snapshot"], Mapping):
        raise ValueError
    snapshot = parse_group_order_snapshot(_thaw_json_tree(value["snapshot"]))
    request = snapshot.request
    if expected_intent is not None:
        accepted_version_matches = request.version == expected_intent.version or (
            expected_intent.version == 1
            and request.version == 2
            and request.budget_quote is not None
        )
        if (
            request.group_id != expected_intent.group_id
            or request.strategy_id != expected_intent.strategy_id
            or request.portfolio_ref != expected_intent.portfolio_ref
            or request.deadline_ns != expected_intent.deadline_ns
            or request.first_leg_id != expected_intent.first_leg_id
            or request.recovery_policy != expected_intent.recovery_policy
            or not accepted_version_matches
            or (request.version == 3 and request.budget_quote is None)
            or tuple(
                (
                    item.leg_id,
                    item.account_ref,
                    item.maximum_intent.to_payload(),
                    item.base_units_per_order_unit,
                    item.quantity_step,
                    item.metadata_valid_until_ns,
                    item.ratio,
                )
                for item in request.legs
            )
            != tuple(
                (
                    item.leg_id,
                    item.account_ref,
                    item.maximum_intent.to_payload(),
                    item.base_units_per_order_unit,
                    item.quantity_step,
                    item.metadata_valid_until_ns,
                    item.ratio,
                )
                for item in expected_intent.legs
            )
        ):
            raise ValueError
    if expected_identity is not None and (
        request.strategy_id != expected_identity.strategy_ref
        or request.portfolio_ref != expected_identity.portfolio_ref
        or request.account_refs != expected_identity.account_refs
    ):
        raise ValueError
    if group_id is not None and request.group_id != group_id:
        raise ValueError
    return snapshot


def _group_reply_result(value: object, *, group_id: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"ok", "code", "result"}
    ):
        raise ValueError("group service reply envelope is invalid")
    ok = value["ok"]
    code = value["code"]
    result = value["result"]
    if type(ok) is not bool or not isinstance(result, Mapping):
        raise ValueError("group service reply envelope is invalid")
    if ok is False:
        if type(code) is not str or _RPC_ERROR_CODE.fullmatch(code) is None or code == "OK":
            raise ValueError("group service rejection code is invalid")
        raise ServiceGroupError(code, group_id=group_id)
    if code != "OK":
        raise ValueError("group service success code is invalid")
    return result


def _roll_exit_reply(
    value: object,
    *,
    group_id: Optional[str],
    source_leg_id: Optional[str],
    action_id: Optional[str],
    allow_not_found: bool,
) -> Mapping[str, Any]:
    if (
        not isinstance(value, Mapping)
        or frozenset(value) != frozenset({"found", "action"})
        or type(value["found"]) is not bool
    ):
        raise ValueError("roll-exit reply result is invalid")
    if value["found"] is False:
        if not allow_not_found or value["action"] is not None:
            raise ValueError("roll-exit action was not found")
        result = MappingProxyType({"found": False, "action": None})
        return MappingProxyType({"ok": True, "code": "OK", "result": result})

    action = value["action"]
    fields = frozenset(
        {
            "action_id",
            "group_id",
            "source_leg_id",
            "phase",
            "close_state",
            "requested_quantity",
            "filled_quantity",
            "order_revision",
            "detail_code",
            "updated_at_ns",
        }
    )
    if not isinstance(action, Mapping) or frozenset(action) != fields:
        raise ValueError("roll-exit action projection is invalid")
    for name in ("action_id", "group_id", "source_leg_id"):
        _require_reference(action[name], "GROUP_ROLL_EXIT_ACTION_INVALID")
    if (
        action_id is not None
        and action["action_id"] != action_id
        or group_id is not None
        and action["group_id"] != group_id
        or source_leg_id is not None
        and action["source_leg_id"] != source_leg_id
    ):
        raise ValueError("roll-exit action identity is inconsistent")

    phase = action["phase"]
    close_state = action["close_state"]
    if type(phase) is not str or phase not in _ROLL_EXIT_PHASES or (
        close_state is not None
        and (
            type(close_state) is not str
            or close_state not in _ROLL_EXIT_CLOSE_STATES
        )
    ):
        raise ValueError("roll-exit action phase is invalid")
    requested = _roll_exit_decimal(action["requested_quantity"], positive=True)
    filled = _roll_exit_decimal(action["filled_quantity"], positive=False)
    if filled > requested:
        raise ValueError("roll-exit action fill exceeds requested quantity")

    revision = action["order_revision"]
    if revision is not None and (type(revision) is not int or revision <= 0):
        raise ValueError("roll-exit action revision is invalid")
    detail_code = action["detail_code"]
    if detail_code is not None and (
        type(detail_code) is not str or _RPC_ERROR_CODE.fullmatch(detail_code) is None
    ):
        raise ValueError("roll-exit action detail code is invalid")
    updated_at_ns = action["updated_at_ns"]
    if type(updated_at_ns) is not int or updated_at_ns <= 0:
        raise ValueError("roll-exit action timestamp is invalid")

    if phase == "PREPARED":
        valid_state = (
            close_state is None
            and filled == 0
            and revision is None
            and detail_code is None
        )
    elif phase == "SUBMIT_STARTED":
        valid_state = (
            close_state in {None, "PENDING_ADMISSION", "PENDING_DISPATCH", "DISPATCHING"}
            and filled == 0
        )
    elif phase == "STATUS_OBSERVED":
        valid_state = (
            close_state
            in {
                "PENDING_ADMISSION",
                "PENDING_DISPATCH",
                "DISPATCHING",
                "ACKED",
                "PARTIALLY_FILLED",
            }
            and revision is not None
            and (
                (close_state == "PARTIALLY_FILLED" and 0 < filled < requested)
                or (close_state != "PARTIALLY_FILLED" and filled == 0)
            )
        )
    elif phase == "UNKNOWN":
        valid_state = close_state == "UNKNOWN"
    else:
        valid_state = (
            close_state in {"FILLED", "CANCELLED", "REJECTED"}
            and revision is not None
            and (close_state != "FILLED" or filled == requested)
            and (close_state != "REJECTED" or filled == 0)
        )
    if not valid_state:
        raise ValueError("roll-exit action phase conflicts with its close status")

    projection = MappingProxyType(
        {
            "action_id": action["action_id"],
            "group_id": action["group_id"],
            "source_leg_id": action["source_leg_id"],
            "phase": phase,
            "close_state": close_state,
            "requested_quantity": action["requested_quantity"],
            "filled_quantity": action["filled_quantity"],
            "order_revision": revision,
            "detail_code": detail_code,
            "updated_at_ns": updated_at_ns,
        }
    )
    result = MappingProxyType({"found": True, "action": projection})
    return MappingProxyType({"ok": True, "code": "OK", "result": result})


def _roll_exit_decimal(value: object, *, positive: bool) -> Decimal:
    if (
        type(value) is not str
        or len(value) > 256
        or _DECIMAL_TEXT.fullmatch(value) is None
    ):
        raise ValueError("roll-exit quantity is invalid")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("roll-exit quantity is invalid") from None
    if (
        not parsed.is_finite()
        or (positive and parsed <= 0)
        or (not positive and parsed < 0)
        or format(parsed, "f") != value
    ):
        raise ValueError("roll-exit quantity is invalid")
    return parsed


def _raise_roll_exit_call_error(
    error: BaseException, group_id: str, source_leg_id: str
) -> None:
    try:
        from bt_api_py.service_runtime import (
            ServiceCallOutcomeUnknownError,
            ServiceRuntimeError,
            ServiceRuntimeRequestNotSentError,
        )
    except ImportError:
        raise ServiceGroupRollExitUnknownOutcomeError(
            group_id=group_id, source_leg_id=source_leg_id
        ) from None
    if isinstance(error, ServiceRuntimeRequestNotSentError):
        raise ServiceGroupRequestNotSentError(group_id=group_id) from None
    if isinstance(error, ServiceCallOutcomeUnknownError):
        raise ServiceGroupRollExitUnknownOutcomeError(
            group_id=group_id, source_leg_id=source_leg_id
        ) from None
    if isinstance(error, ServiceRuntimeError):
        raise ServiceGroupError("GROUP_REQUEST_REJECTED", group_id=group_id) from None
    raise ServiceGroupRollExitUnknownOutcomeError(
        group_id=group_id, source_leg_id=source_leg_id
    ) from None


def _parse_residual_event(
    value: object, route: ServiceGroupRoute
) -> ServiceGroupResidual:
    if not isinstance(value, Mapping) or frozenset(value) != frozenset(
        {"sequence", "event_id", "event_type", "occurred_at", "data"}
    ):
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    data = value["data"]
    source_event_id = None
    if isinstance(data, Mapping) and frozenset(data) == _RESIDUAL_DATA_FIELDS:
        pass
    elif isinstance(data, Mapping) and frozenset(data) == {
        "producer",
        "source_event_id",
        "payload",
    }:
        producer = data.get("producer")
        source_event_id = data.get("source_event_id")
        projection = data.get("payload")
        if (
            not isinstance(producer, Mapping)
            or frozenset(producer)
            != {"role", "principal_ref", "actor_ref", "delegate_ref"}
            or type(producer.get("role")) is not str
            or producer.get("role") != "service"
            or not _valid_reference(producer.get("principal_ref"))
            or not _valid_reference(producer.get("actor_ref"))
            or (
                producer.get("delegate_ref") is not None
                and not _valid_reference(producer.get("delegate_ref"))
            )
            or not _valid_monitor_producer_binding(route)
            or producer.get("principal_ref") != route.monitor_producer_principal_ref
            or producer.get("actor_ref") != route.monitor_producer_actor_ref
            or producer.get("delegate_ref") != route.monitor_producer_delegate_ref
            or not isinstance(projection, Mapping)
            or frozenset(projection) != _RESIDUAL_DATA_FIELDS
            or type(source_event_id) is not str
            or _SERVICE_EVENT_ID.fullmatch(source_event_id) is None
            or type(value.get("event_id")) is not str
            or _SERVICE_EVENT_ID.fullmatch(value["event_id"]) is None
        ):
            raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
        data = projection
    else:
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")

    if (
        value["event_type"] != _RESIDUAL_EVENT_TYPE
        or type(value["sequence"]) is not int
        or value["sequence"] <= 0
        or not _valid_text(value["event_id"])
        or not isinstance(value["occurred_at"], (int, float))
        or isinstance(value["occurred_at"], bool)
        or not math.isfinite(float(value["occurred_at"]))
        or not isinstance(data, Mapping)
        or frozenset(data) != _RESIDUAL_DATA_FIELDS
        or data.get("schema") != _RESIDUAL_SCHEMA
        or type(data.get("source_sequence")) is not int
        or data["source_sequence"] <= 0
        or type(data.get("source_revision")) is not int
        or data["source_revision"] < 0
        or type(data.get("source_created_at_ns")) is not int
        or data["source_created_at_ns"] <= 0
        or data.get("strategy_id") != route.strategy_ref
        or data.get("portfolio_ref") != route.portfolio_ref
        or data.get("state") != _RESIDUAL_STATE
    ):
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    generation = data.get("journal_generation")
    group_id = data.get("group_id")
    reason = data.get("reason")
    source_digest = data.get("source_event_sha256")
    account_refs = data.get("account_refs")
    if (
        not isinstance(generation, str)
        or _SHA256.fullmatch(generation) is None
        or not _valid_reference(group_id)
        or not _valid_text(reason)
        or type(source_digest) is not str
        or _SHA256.fullmatch(source_digest) is None
        or type(account_refs) not in (list, tuple)
        or not account_refs
        or any(not _valid_reference(item) for item in account_refs)
        or tuple(sorted(set(account_refs))) != tuple(account_refs)
        or not set(account_refs).issubset(route.account_refs)
    ):
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    if source_event_id is not None and source_event_id != _projected_residual_event_id(data):
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    residual_base_units = _optional_decimal_text(data.get("residual_base_units"))
    first_leg_id = _optional_reference(data.get("first_leg_id"))
    second_leg_id = _optional_reference(data.get("second_leg_id"))
    first_filled_quantity = _optional_decimal_text(data.get("first_filled_quantity"))
    second_filled_quantity = _optional_decimal_text(data.get("second_filled_quantity"))
    pair_values = (
        first_leg_id,
        second_leg_id,
        first_filled_quantity,
        second_filled_quantity,
    )
    if residual_base_units is None:
        if any(item is not None for item in pair_values) or reason == _PAIR_RESIDUAL_REASON:
            raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    elif any(item is None for item in pair_values) or reason != _PAIR_RESIDUAL_REASON:
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    return ServiceGroupResidual(
        sequence=value["sequence"],
        event_id=value["event_id"],
        journal_generation=generation,
        portfolio_ref=route.portfolio_ref,
        group_id=group_id,
        strategy_ref=route.strategy_ref,
        account_refs=tuple(account_refs),
        state=_RESIDUAL_STATE,
        reason=reason,
        residual_base_units=residual_base_units,
        first_leg_id=first_leg_id,
        second_leg_id=second_leg_id,
        first_filled_quantity=first_filled_quantity,
        second_filled_quantity=second_filled_quantity,
        source_event_sha256=source_digest,
        source_revision=data["source_revision"],
        source_created_at_ns=data["source_created_at_ns"],
    )


def _valid_monitor_producer_binding(route: ServiceGroupRoute) -> bool:
    principal_ref = route.monitor_producer_principal_ref
    actor_ref = route.monitor_producer_actor_ref
    delegate_ref = route.monitor_producer_delegate_ref
    if principal_ref is None and actor_ref is None and delegate_ref is None:
        return True
    return (
        _valid_reference(principal_ref)
        and _valid_reference(actor_ref)
        and (delegate_ref is None or _valid_reference(delegate_ref))
    )


def _projected_residual_event_id(data: Mapping[str, Any]) -> str:
    digest_input = {
        "schema": _RESIDUAL_SCHEMA,
        "generation": data["journal_generation"],
        "portfolio_ref": data["portfolio_ref"],
        "group_id": data["group_id"],
        "source_event_sha256": data["source_event_sha256"],
    }
    canonical = json.dumps(
        digest_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return "service-event:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _raise_submit_error(error: BaseException, group_id: str) -> None:
    try:
        from bt_api_py.service_runtime import (
            ServiceCallOutcomeUnknownError,
            ServiceRuntimeError,
            ServiceRuntimeRequestNotSentError,
        )
    except ImportError:
        raise ServiceGroupOutcomeUnknownError(group_id=group_id) from None
    if isinstance(error, ServiceRuntimeRequestNotSentError):
        raise ServiceGroupRequestNotSentError(group_id=group_id) from None
    if isinstance(error, ServiceCallOutcomeUnknownError):
        raise ServiceGroupOutcomeUnknownError(group_id=group_id) from None
    if isinstance(error, ServiceRuntimeError):
        raise ServiceGroupError("GROUP_REQUEST_REJECTED", group_id=group_id) from None
    raise ServiceGroupOutcomeUnknownError(group_id=group_id) from None


def _thaw_json_tree(value: object) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("wire object keys must be strings")
        return {key: _thaw_json_tree(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_thaw_json_tree(item) for item in value]
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("unsupported value in wire JSON tree")


def _optional_decimal_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if type(value) is not str or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID") from None
    if not parsed.is_finite():
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    return value


def _optional_reference(value: object) -> Optional[str]:
    if value is None:
        return None
    if not _valid_reference(value):
        raise ServiceGroupError("RESIDUAL_EVENT_INVALID")
    return value


def _require_reference(value: object, code: str) -> str:
    if not _valid_reference(value):
        raise ServiceGroupError(code)
    return value


def _valid_reference(value: object) -> bool:
    return type(value) is str and _REFERENCE.fullmatch(value) is not None


def _valid_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )
