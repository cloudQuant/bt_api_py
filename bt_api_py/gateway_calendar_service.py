"""Registration-scoped simulated calendar reads from one pinned owner view.

The revision check observes query completion, not later socket transmission.
No source authentication or trade authority is established by this service.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta, timezone
from threading import RLock

from bt_api_base.service_protocol import WireRole
from bt_api_py.gateway_calendar_codec import (decode_calendar_query, validate_calendar_response,
    decode_calendar_windows_query, validate_calendar_windows_response, _id)
from bt_api_py.service_registry import ServiceCallContext, ServiceRegistry, ServiceResultTooLargeError
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from bt_api_py.trading_calendar_store import CalendarReadBudget, TradingCalendarStore
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy, query_simulation_range

CALENDAR_OPERATION = "calendar.query.v1"
CALENDAR_WINDOWS_OPERATION = "calendar.windows.v1"
CALENDAR_MONITOR_WINDOWS_OPERATION = "calendar.monitor.windows.v1"


class ReceiptCheckedCalendarStore:
    """Trusted read-only binding adapter requiring the complete local receipt chain."""

    def __init__(self, store):
        if type(store) is not TradingCalendarStore:
            raise ValueError("INVALID_CALENDAR_STORE")
        self.__store = store
        self.__budget = CalendarReadBudget(8 * 1024 * 1024, 32 * 1024 * 1024, 4096)

    def read_current(self):
        return self.__store.read_current_with_consistent_receipts(budget=self.__budget).snapshot


@dataclass(frozen=True)
class CalendarGrant:
    principal_ref: str
    execution_domain_ref: str
    account_ref: str
    strategy_ref: str
    run_id: str
    route: str
    venue: str
    kind: str
    product: str
    contract: str

    def __post_init__(self):
        if (any(not _id(value) for value in vars(self).values())
                or self.kind not in {"future", "option"}):
            raise ValueError("INVALID_CALENDAR_GRANT")


@dataclass(frozen=True)
class CalendarMonitorGrant:
    principal_ref: str
    actor_ref: str
    execution_domain_ref: str
    account_ref: str
    route: str
    venue: str
    kind: str
    product: str
    contract: str

    def __post_init__(self):
        if (any(not _id(value) for value in vars(self).values())
                or self.kind not in {"future", "option"}):
            raise ValueError("INVALID_CALENDAR_MONITOR_GRANT")


class GatewayCalendarService:
    """Owner-local binding with explicit ACL; bind/rebind are never RPC operations."""

    def __init__(self, *, grants, range_policies=frozenset(), monitor_grants=frozenset()):
        if (type(grants) is not frozenset or len(grants) > 4096
                or any(type(grant) is not CalendarGrant for grant in grants)):
            raise ValueError("INVALID_CALENDAR_SERVICE")
        if (type(monitor_grants) is not frozenset or len(monitor_grants) > 4096
                or any(type(grant) is not CalendarMonitorGrant for grant in monitor_grants)
                or not (grants or monitor_grants)):
            raise ValueError("INVALID_CALENDAR_SERVICE")
        if (type(range_policies) is not frozenset or len(range_policies) > 4096
                or any(type(policy) is not CalendarRangePolicy for policy in range_policies)
                or len({policy.scope_key for policy in range_policies}) != len(range_policies)):
            raise ValueError("INVALID_CALENDAR_RANGE_POLICIES")
        self._range_policies = {policy.scope_key: policy for policy in range_policies}
        self._grants, self._epoch, self._registry = grants, None, None
        self._monitor_grants = monitor_grants
        self._lock = RLock()
        self._closed = False
        self._binding = None
        self._revision = 0
        self._generations = set()

    def bind_registry(self, registry):
        """Bind once after trusted host creates its Registry and epoch."""
        if type(registry) is not ServiceRegistry or registry._closed or not _id(registry.service_epoch):
            raise ValueError("INVALID_CALENDAR_REGISTRY")
        with self._lock:
            if self._closed or self._registry is not None:
                raise ValueError("CALENDAR_REGISTRY_ALREADY_BOUND")
            self._registry, self._epoch = registry, registry.service_epoch

    def rebind(self, binding):
        """Install a new simulated source generation, or detach on owner loss."""
        if binding is not None:
            if (type(binding) is not SimulationCalendarBinding
                    or type(binding._store) is not ReceiptCheckedCalendarStore):
                raise ValueError("INVALID_CALENDAR_BINDING")
            status = binding.status()
            if not _id(status.route) or not _id(status.source_generation):
                raise ValueError("INVALID_CALENDAR_BINDING")
        with self._lock:
            if self._closed:
                raise ValueError("CALENDAR_SERVICE_CLOSED")
            if binding is not None:
                if status.source_generation in self._generations:
                    raise ValueError("CALENDAR_GENERATION_REUSED")
                if len(self._generations) >= 256:
                    raise ValueError("CALENDAR_GENERATION_CAPACITY")
                self._generations.add(status.source_generation)
            self._binding = binding
            self._revision += 1

    def close(self):
        """Irreversibly detach this service instance without closing its store."""
        with self._lock:
            self._closed = True
            self._binding = None
            self._revision += 1

    def handle(self, operation, *, context, arguments):
        if operation not in {CALENDAR_OPERATION, CALENDAR_WINDOWS_OPERATION, CALENDAR_MONITOR_WINDOWS_OPERATION}:
            raise ValueError("INVALID_CALENDAR_REQUEST")
        if operation in {CALENDAR_WINDOWS_OPERATION, CALENDAR_MONITOR_WINDOWS_OPERATION}:
            scope, start_utc, end_utc = decode_calendar_windows_query(arguments)
        else:
            scope, query = decode_calendar_query(arguments)
        if operation == CALENDAR_MONITOR_WINDOWS_OPERATION:
            self._authorize_monitor(context, scope)
        elif (type(context) is not ServiceCallContext or context.identity.role is not WireRole.STRATEGY_CLIENT
                or context.identity.principal_ref != context.principal_ref
                or context.identity.actor_ref != context.scope.strategy_ref
                or context.identity.delegate_ref is not None
                or context.scope.account_refs or context.scope.manual_book_ref is not None
                or context.scope.portfolio_ref is not None or context.scope.group_id is not None
                or context.scope.leg_id is not None or not _id(context.registration_id)):
            raise ValueError("SCOPE_DENIED")
        if operation != CALENDAR_MONITOR_WINDOWS_OPERATION:
            try:
                grant = CalendarGrant(context.principal_ref, context.scope.execution_domain_ref,
                                  context.scope.account_ref, context.scope.strategy_ref, context.scope.run_id,
                                  scope["route"], scope["venue"], scope["kind"], scope["product"], scope["contract"])
            except (TypeError, ValueError):
                raise ValueError("SCOPE_DENIED") from None
            if grant not in self._grants:
                raise ValueError("SCOPE_DENIED")
        if self._registry is None:
            raise ValueError("SERVICE_UNAVAILABLE")
        if operation in {CALENDAR_WINDOWS_OPERATION, CALENDAR_MONITOR_WINDOWS_OPERATION}:
            return self._handle_windows(context, scope, start_utc, end_utc)
        response = {"schema": "sdk.gateway.calendar.query/1", "scope": scope,
                    "registration_id": context.registration_id, "service_epoch": self._epoch,
                    "status": "UNKNOWN", "reason": "SOURCE_UNAVAILABLE", "binding": None,
                    "venue_day": {"status": "UNKNOWN", "day_status": None}, "windows": [],
                    "source_authentication": "NOT_PROVEN", "trade_authorization": "NONE"}
        try:
            with self._lock:
                binding, owner_revision = self._binding, self._revision
            if self._registry._closed or self._registry.service_epoch != self._epoch or binding is None:
                return self._finish(context, response)
            view = binding.capture_read_view(mode=binding.status().mode)
            status = view.status()
            with binding._lock:
                binding_revision = binding._revision
                if binding._status is not status:
                    response["reason"] = "OWNER_CHANGED"
                    return self._finish(context, response)
            if status.route != scope["route"] or not status.calendar_ref:
                return self._finish(context, response)
            coverage = next((item for item in status.declared_coverage if item.venue == scope["venue"]), None)
            if coverage is None:
                response["reason"] = "COVERAGE_UNAVAILABLE"
                return self._finish(context, response)
            response["binding"] = {
                "route": status.route, "source_generation": status.source_generation,
                "bound_trading_day": status.bound_trading_day.isoformat(), "calendar_ref": status.calendar_ref,
                "publication_id": status.publication_id, "content_sha256": status.content_sha256,
                "coverage_manifest_sha256": status.coverage_manifest_sha256,
                "binding_sha256": status.binding_sha256, "version_sha256": status.version_sha256,
                "coverage_status": status.coverage_status, "coverage_start": coverage.start_day.isoformat(),
                "coverage_end": coverage.end_day.isoformat(), "load_status": "STALE" if status.load_error else "CURRENT",
                "data_origin": status.data_origin,
            }
            if not status.bound_trading_day <= query["trading_day"] <= status.bound_trading_day + timedelta(days=366):
                response.update(status="OUT_OF_SCOPE", reason="DAY_OUT_OF_RANGE")
            else:
                result = view.query(**query)
                day = view.query_venue_day(mode=binding.status().mode, venue=scope["venue"], trading_day=query["trading_day"])
                if result.binding is not status or day.binding is not status:
                    raise ValueError("INVALID_RESULT")
                response["status"] = result.result.status
                response["reason"] = {"COMPLETE": "OK", "UNKNOWN": "QUERY_UNAVAILABLE", "OUT_OF_SCOPE": "OUT_OF_SCOPE"}[response["status"]]
                if day.status == "COMPLETE":
                    response["venue_day"] = {"status": "COMPLETE", "day_status": day.day_status}
                if response["status"] == "COMPLETE":
                    if type(result.result.windows) is not tuple or len(result.result.windows) > 128:
                        raise ValueError("INVALID_RESULT")
                    if any(w.start_utc.tzinfo is None or w.end_utc.tzinfo is None
                           or w.start_utc.utcoffset() != timedelta(0)
                           or w.end_utc.utcoffset() != timedelta(0) for w in result.result.windows):
                        raise ValueError("INVALID_RESULT")
                    response["windows"] = [
                        {"window_id": w.window_id, "phase": w.phase,
                         "start_utc": w.start_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                         "end_utc": w.end_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
                        for w in result.result.windows
                    ]
            with self._lock, binding._lock:
                current = (self._binding is binding and self._revision == owner_revision
                           and binding._revision == binding_revision and binding._status is status
                           and binding._status.source_generation == status.source_generation
                           and not self._registry._closed and self._registry.service_epoch == self._epoch)
            if not current:
                response.update(status="UNKNOWN", reason="OWNER_CHANGED", binding=None,
                                venue_day={"status": "UNKNOWN", "day_status": None}, windows=[])
            return self._finish(context, response)
        except ServiceResultTooLargeError:
            raise
        except Exception:
            response.update(status="UNKNOWN", reason="INVALID_RESULT", binding=None,
                            venue_day={"status": "UNKNOWN", "day_status": None}, windows=[])
            return self._finish(context, response)

    @staticmethod
    def _finish(context, response):
        validate_calendar_response(response)
        if not callable(context.result_fits):
            raise ValueError("INVALID_RESULT_BUDGET")
        fits = context.result_fits(response)
        if type(fits) is not bool:
            raise ValueError("INVALID_RESULT_BUDGET")
        if not fits:
            raise ServiceResultTooLargeError()
        return response

    def _authorize_monitor(self, context, scope):
        if (type(context) is not ServiceCallContext or context.identity.role is not WireRole.SERVICE
                or context.identity.principal_ref != context.principal_ref
                or context.identity.delegate_ref is not None
                or context.scope.account_ref is None or context.scope.account_refs
                or context.scope.strategy_ref is not None or context.scope.run_id is not None
                or context.scope.manual_book_ref is not None or context.scope.portfolio_ref is not None
                or context.scope.group_id is not None or context.scope.leg_id is not None
                or not _id(context.registration_id)):
            raise ValueError("SCOPE_DENIED")
        try:
            grant = CalendarMonitorGrant(context.principal_ref, context.identity.actor_ref,
                context.scope.execution_domain_ref, context.scope.account_ref, scope["route"],
                scope["venue"], scope["kind"], scope["product"], scope["contract"])
        except (TypeError, ValueError):
            raise ValueError("SCOPE_DENIED") from None
        if grant not in self._monitor_grants:
            raise ValueError("SCOPE_DENIED")

    def _handle_windows(self, context, scope, start_utc, end_utc):
        response = {"schema": "sdk.gateway.calendar.windows/1", "scope": scope,
                    "registration_id": context.registration_id, "service_epoch": self._epoch,
                    "status": "UNKNOWN", "reason": "SOURCE_UNAVAILABLE", "binding": None,
                    "policy": None, "windows": [], "gaps": [], "source_authentication": "NOT_PROVEN",
                    "trade_authorization": "NONE"}

        def finish():
            if len(json.dumps(response, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) > 32768:
                raise ServiceResultTooLargeError()
            validate_calendar_windows_response(response)
            if not callable(context.result_fits):
                raise ValueError("INVALID_RESULT_BUDGET")
            fits = context.result_fits(response)
            if type(fits) is not bool:
                raise ValueError("INVALID_RESULT_BUDGET")
            if not fits:
                raise ServiceResultTooLargeError()
            return response

        key = tuple(scope[name] for name in ("route", "venue", "kind", "product", "contract"))
        policy = self._range_policies.get(key)
        if policy is None:
            response["reason"] = "POLICY_UNAVAILABLE"
            return finish()
        response["policy"] = {"policy_id": policy.policy_id, "declaration": "DEPLOYMENT_DECLARED_UNVERIFIED",
            "max_advance_days": policy.max_advance_days, "max_spill_days": policy.max_spill_days,
            "first_owner_day": None, "last_owner_day": None}
        try:
            with self._lock:
                binding, revision = self._binding, self._revision
            if self._registry._closed or self._registry.service_epoch != self._epoch or binding is None:
                return finish()
            view = binding.capture_read_view(mode=binding.status().mode)
            status = view.status()
            with binding._lock:
                binding_revision = binding._revision
                if binding._status is not status:
                    response["reason"] = "OWNER_CHANGED"
                    return finish()
            if status.route != scope["route"] or not status.calendar_ref:
                return finish()
            coverage = next((item for item in status.declared_coverage if item.venue == scope["venue"]), None)
            if coverage is None:
                response["reason"] = "COVERAGE_UNAVAILABLE"
                return finish()
            response["binding"] = {
                "route": status.route, "source_generation": status.source_generation,
                "bound_trading_day": status.bound_trading_day.isoformat(), "calendar_ref": status.calendar_ref,
                "publication_id": status.publication_id, "content_sha256": status.content_sha256,
                "coverage_manifest_sha256": status.coverage_manifest_sha256, "binding_sha256": status.binding_sha256,
                "version_sha256": status.version_sha256, "coverage_status": status.coverage_status,
                "coverage_start": coverage.start_day.isoformat(), "coverage_end": coverage.end_day.isoformat(),
                "load_status": "STALE" if status.load_error else "CURRENT", "data_origin": status.data_origin}
            result = query_simulation_range(view, policy, start_utc, end_utc)
            if result.reason == "RESULT_TOO_LARGE":
                raise ServiceResultTooLargeError()
            response.update(status=result.status, reason=result.reason)
            response["gaps"] = [{"trading_day": day.isoformat(), "reason": reason} for day, reason in result.gaps]
            if result.first_owner_day is not None and result.last_owner_day is not None:
                response["policy"]["first_owner_day"] = result.first_owner_day.isoformat()
                response["policy"]["last_owner_day"] = result.last_owner_day.isoformat()
            if result.status == "COMPLETE":
                response["windows"] = [{"window_id": w.window_id, "phase": w.phase,
                    "trading_day": w.trading_day.isoformat(),
                    "start_utc": w.start_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "end_utc": w.end_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
                    for w in result.windows]
            with self._lock, binding._lock:
                current = (self._binding is binding and self._revision == revision
                    and binding._revision == binding_revision and binding._status is status
                    and binding._status.source_generation == status.source_generation
                    and not self._registry._closed and self._registry.service_epoch == self._epoch)
            if not current:
                response.update(status="UNKNOWN", reason="OWNER_CHANGED", binding=None, windows=[], gaps=[])
            return finish()
        except ServiceResultTooLargeError:
            raise
        except Exception:
            response.update(status="UNKNOWN", reason="INVALID_RESULT", binding=None, windows=[], gaps=[])
            response["policy"]["first_owner_day"] = response["policy"]["last_owner_day"] = None
            return finish()
