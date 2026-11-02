"""Explicit read-only queries over multiple already-composed SDK routes.

This module never creates, configures, or closes provider runtimes. Each route
is supplied by the caller as an exact ``ReadOnlyManagedRuntimeFacade`` and is
kept isolated: order-query results and query receipts are returned per route,
without synthesizing a cross-route account snapshot or receipt.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from bt_api_base.query_protocol import (
    MAX_QUERY_BATCH_REQUESTS,
    QueryBatchReceipt,
    QueryRequest,
    QuerySnapshot,
    QuerySnapshotLease,
)

from ._contracts.models import QueryOrderRequest
from .managed_runtime_composition import ReadOnlyManagedRuntimeFacade
from .query_facade import QueryFacade


_ROUTE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_ERROR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_]{1,96}\Z")
_MAX_MULTI_ROUTE_READ_REQUESTS = MAX_QUERY_BATCH_REQUESTS
_ROUTE_WRAPPER_SEAL = object()

__all__ = [
    "MultiRouteReadAggregator",
    "MultiRouteReadError",
    "RouteOrderQueryOutcome",
    "RouteQueryBatchOutcome",
    "RouteQueryReceipt",
    "RouteReadFacade",
    "RouteSnapshotLease",
    "RouteScopedQueryFacade",
    "query_orders_sync",
]


def query_orders_sync(*, routes, read_facades, query_requests):
    """Query explicit caller-owned routes from a synchronous entry point.

    No clients are created or closed, and results remain route-isolated.
    Call the asynchronous aggregator directly from an existing event loop.
    """
    if type(routes) is not tuple or not routes:
        raise RuntimeError("MULTI_ROUTE_READ_ROUTE_INVALID")
    if not isinstance(read_facades, Mapping) or not isinstance(query_requests, Mapping):
        raise RuntimeError("MULTI_ROUTE_READ_INPUT_INVALID")
    try:
        facade_routes = tuple(read_facades.keys())
        request_routes = tuple(query_requests.keys())
        if (
            len(set(facade_routes)) != len(facade_routes)
            or len(set(request_routes)) != len(request_routes)
            or set(facade_routes) != set(routes)
            or set(request_routes) != set(routes)
        ):
            raise RuntimeError("MULTI_ROUTE_READ_ROUTE_MISMATCH")
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("MULTI_ROUTE_READ_INPUT_INVALID") from None

    aggregator = MultiRouteReadAggregator(read_facades)
    if set(aggregator.routes) != set(routes):
        raise RuntimeError("MULTI_ROUTE_READ_ROUTE_MISMATCH")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("MULTI_ROUTE_READ_EVENT_LOOP_ALREADY_RUNNING")
    return asyncio.run(aggregator.query_orders(query_requests))


class MultiRouteReadError(RuntimeError):
    """Finite non-sensitive error for one explicitly selected read route."""

    _CODES = frozenset(
        {
            "MULTI_ROUTE_READ_INPUT_INVALID",
            "MULTI_ROUTE_READ_ROUTE_INVALID",
            "MULTI_ROUTE_READ_ROUTE_UNAVAILABLE",
            "MULTI_ROUTE_READ_FACADE_INVALID",
            "MULTI_ROUTE_READ_ROUTE_MISMATCH",
            "MULTI_ROUTE_READ_QUERY_FACADE_UNAVAILABLE",
            "MULTI_ROUTE_READ_RECEIPT_ROUTE_MISMATCH",
            "MULTI_ROUTE_READ_LEASE_ROUTE_MISMATCH",
            "MULTI_ROUTE_READ_REQUEST_INVALID",
            "MULTI_ROUTE_READ_FAILED",
            "MANAGED_RUNTIME_READ_FAILED",
            "MANAGED_RUNTIME_READONLY_CAPABILITY_UNAVAILABLE",
            "MANAGED_RUNTIME_FACADE_ROUTE_MISMATCH",
            "PROVIDER_QUERY_FAILED",
            "INVALID_PROVIDER_RECEIPT",
            "INCOMPLETE_RECEIPT",
            "PROVIDER_LEASE_ISSUE_FAILED",
            "INVALID_PROVIDER_LEASE",
            "INVALID_PROVIDER_LEASE_RESULT",
        }
    )

    def __init__(self, code: str, *, route: str | None = None) -> None:
        self.code = code if type(code) is str and code in self._CODES else "MULTI_ROUTE_READ_FAILED"
        self.route = route
        super().__init__(self.code)


@dataclass(frozen=True)
class RouteOrderQueryOutcome:
    """One typed order query's outcome, bound to its explicit route."""

    route: str
    ordinal: int
    returned: bool
    result: Mapping[str, object] | None = field(default=None, repr=False, compare=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_route(self.route)
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise TypeError("ordinal must be a non-negative integer")
        if type(self.returned) is not bool:
            raise TypeError("returned must be a bool")
        if self.returned:
            if not isinstance(self.result, Mapping) or self.error_code is not None:
                raise TypeError("a returned query requires a mapping result only")
        elif self.result is not None or not _valid_error_code(self.error_code):
            raise TypeError("a failed query requires a finite error code only")


@dataclass(frozen=True)
class RouteQueryBatchOutcome:
    """One route's query batch outcome; a receipt is never merged across routes."""

    route: str
    returned: bool
    receipt: RouteQueryReceipt | None = field(default=None, repr=False, compare=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_route(self.route)
        if type(self.returned) is not bool:
            raise TypeError("returned must be a bool")
        if self.returned:
            if type(self.receipt) is not RouteQueryReceipt or self.error_code is not None:
                raise TypeError("a returned batch requires a route receipt only")
            if self.receipt.route != self.route:
                raise ValueError("receipt route does not match the outcome")
        elif self.receipt is not None or not _valid_error_code(self.error_code):
            raise TypeError("a failed batch requires a finite error code only")


class RouteQueryReceipt:
    """Opaque route tag around one exact provider-neutral SDK batch receipt."""

    __slots__ = ("__route", "__owner", "__receipt")

    def __init__(
        self,
        route: str,
        receipt: QueryBatchReceipt,
        *,
        owner: object,
        seal: object,
    ) -> None:
        if seal is not _ROUTE_WRAPPER_SEAL:
            raise TypeError("route receipts are issued by RouteScopedQueryFacade")
        _require_route(route)
        if type(receipt) is not QueryBatchReceipt:
            raise TypeError("receipt must be an exact QueryBatchReceipt")
        object.__setattr__(self, "_RouteQueryReceipt__route", route)
        object.__setattr__(self, "_RouteQueryReceipt__owner", owner)
        object.__setattr__(self, "_RouteQueryReceipt__receipt", receipt)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("route query receipts are immutable")

    @property
    def route(self) -> str:
        return self.__route

    @property
    def complete(self) -> bool:
        return self.__receipt.complete

    @property
    def source_ref(self) -> str | None:
        return self.__receipt.source_ref

    @property
    def generation(self) -> str | None:
        return self.__receipt.generation

    @property
    def request_refs(self) -> tuple[str, ...]:
        return tuple(request.request_ref for request in self.__receipt.requests)

    @property
    def snapshot_refs(self) -> tuple[str, ...]:
        return self.__receipt.complete_snapshot_refs

    @property
    def receipt_digest(self) -> str:
        return self.__receipt.receipt_digest

    @property
    def started_at(self) -> datetime:
        return self.__receipt.started_at

    @property
    def completed_at(self) -> datetime:
        return self.__receipt.completed_at

    def __repr__(self) -> str:
        return "<route query receipt route={!r} complete={!r}>".format(
            self.__route,
            self.complete,
        )


class RouteSnapshotLease:
    """Opaque route tag around one exact provider-issued query lease."""

    __slots__ = ("__route", "__owner", "__lease")

    def __init__(
        self,
        route: str,
        lease: QuerySnapshotLease,
        *,
        owner: object,
        seal: object,
    ) -> None:
        if seal is not _ROUTE_WRAPPER_SEAL:
            raise TypeError("route leases are issued by RouteScopedQueryFacade")
        _require_route(route)
        if type(lease) is not QuerySnapshotLease:
            raise TypeError("lease must be an exact QuerySnapshotLease")
        object.__setattr__(self, "_RouteSnapshotLease__route", route)
        object.__setattr__(self, "_RouteSnapshotLease__owner", owner)
        object.__setattr__(self, "_RouteSnapshotLease__lease", lease)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("route snapshot leases are immutable")

    @property
    def route(self) -> str:
        return self.__route

    @property
    def purpose(self) -> str:
        return self.__lease.purpose

    @property
    def expires_at(self) -> datetime:
        return self.__lease.expires_at

    def __repr__(self) -> str:
        return "<route query lease route={!r} purpose={!r}>".format(
            self.__route,
            self.purpose,
        )


class RouteScopedQueryFacade:
    """Route-bound wrapper which prevents receipts or leases crossing routes."""

    __slots__ = ("__route", "__facade", "__owner")

    def __init__(
        self,
        route: str,
        facade: QueryFacade,
        *,
        seal: object,
    ) -> None:
        if seal is not _ROUTE_WRAPPER_SEAL:
            raise TypeError("route query facades are selected by MultiRouteReadAggregator")
        _require_route(route)
        if type(facade) is not QueryFacade:
            raise TypeError("facade must be an exact QueryFacade")
        object.__setattr__(self, "_RouteScopedQueryFacade__route", route)
        object.__setattr__(self, "_RouteScopedQueryFacade__facade", facade)
        object.__setattr__(self, "_RouteScopedQueryFacade__owner", object())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("route scoped query facades are immutable")

    @property
    def route(self) -> str:
        return self.__route

    def refresh(
        self,
        requests: Sequence[QueryRequest],
        *,
        deadline: datetime,
    ) -> RouteQueryReceipt:
        try:
            receipt = self.__facade.refresh(requests, deadline=deadline)
        except Exception as exc:
            raise _route_error(exc, self.__route, "MULTI_ROUTE_READ_FAILED") from None
        return RouteQueryReceipt(
            self.__route,
            receipt,
            owner=self.__owner,
            seal=_ROUTE_WRAPPER_SEAL,
        )

    def snapshot(
        self,
        receipt: RouteQueryReceipt,
        request_ref: str,
    ) -> QuerySnapshot | None:
        self.__require_receipt(receipt)
        try:
            return self.__facade.snapshot(receipt._RouteQueryReceipt__receipt, request_ref)
        except Exception as exc:
            raise _route_error(exc, self.__route, "MULTI_ROUTE_READ_FAILED") from None

    def acquire_snapshot_lease(
        self,
        receipt: RouteQueryReceipt,
        *,
        purpose: str,
        expires_at: datetime,
    ) -> RouteSnapshotLease:
        self.__require_receipt(receipt)
        try:
            lease = self.__facade.acquire_snapshot_lease(
                receipt._RouteQueryReceipt__receipt,
                purpose=purpose,
                expires_at=expires_at,
            )
        except Exception as exc:
            raise _route_error(exc, self.__route, "MULTI_ROUTE_READ_FAILED") from None
        return RouteSnapshotLease(
            self.__route,
            lease,
            owner=self.__owner,
            seal=_ROUTE_WRAPPER_SEAL,
        )

    def validate_lease(
        self,
        lease: RouteSnapshotLease,
        receipt: RouteQueryReceipt,
        *,
        now: datetime,
    ) -> bool:
        self.__require_receipt(receipt)
        if (
            type(lease) is not RouteSnapshotLease
            or lease.route != self.__route
            or lease._RouteSnapshotLease__owner is not self.__owner
        ):
            raise MultiRouteReadError(
                "MULTI_ROUTE_READ_LEASE_ROUTE_MISMATCH",
                route=self.__route,
            )
        try:
            return self.__facade.validate_lease(
                lease._RouteSnapshotLease__lease,
                receipt._RouteQueryReceipt__receipt,
                now=now,
            )
        except Exception as exc:
            raise _route_error(exc, self.__route, "MULTI_ROUTE_READ_FAILED") from None

    def __require_receipt(self, receipt: RouteQueryReceipt) -> None:
        if (
            type(receipt) is not RouteQueryReceipt
            or receipt.route != self.__route
            or receipt._RouteQueryReceipt__owner is not self.__owner
        ):
            raise MultiRouteReadError(
                "MULTI_ROUTE_READ_RECEIPT_ROUTE_MISMATCH",
                route=self.__route,
            )


class RouteReadFacade:
    """Read-only SDK view for one exact route selected by the aggregator."""

    __slots__ = ("__route", "__facade", "__query_facade")

    def __init__(
        self,
        route: str,
        facade: ReadOnlyManagedRuntimeFacade,
        *,
        seal: object,
    ) -> None:
        if seal is not _ROUTE_WRAPPER_SEAL:
            raise TypeError("route read facades are selected by MultiRouteReadAggregator")
        _require_route(route)
        if type(facade) is not ReadOnlyManagedRuntimeFacade:
            raise TypeError("facade must be an exact ReadOnlyManagedRuntimeFacade")
        object.__setattr__(self, "_RouteReadFacade__route", route)
        object.__setattr__(self, "_RouteReadFacade__facade", facade)
        object.__setattr__(self, "_RouteReadFacade__query_facade", None)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("route read facades are immutable")

    @property
    def route(self) -> str:
        return self.__route

    async def async_query_order(self, request: QueryOrderRequest) -> Mapping[str, object]:
        if type(request) is not QueryOrderRequest:
            raise MultiRouteReadError(
                "MULTI_ROUTE_READ_REQUEST_INVALID",
                route=self.__route,
            )
        try:
            result = await self.__facade.async_query_order(
                self.__route,
                request,
                normalized=True,
            )
        except Exception as exc:
            raise _route_error(exc, self.__route, "MULTI_ROUTE_READ_FAILED") from None
        if not isinstance(result, Mapping):
            raise MultiRouteReadError("MULTI_ROUTE_READ_FAILED", route=self.__route)
        return result

    def query_facade(self) -> RouteScopedQueryFacade:
        selected = self.__query_facade
        if selected is not None:
            return selected
        try:
            facade = self.__facade.query_facade(self.__route)
        except Exception as exc:
            raise _route_error(
                exc,
                self.__route,
                "MULTI_ROUTE_READ_QUERY_FACADE_UNAVAILABLE",
            ) from None
        if type(facade) is not QueryFacade:
            raise MultiRouteReadError(
                "MULTI_ROUTE_READ_QUERY_FACADE_UNAVAILABLE",
                route=self.__route,
            )
        selected = RouteScopedQueryFacade(
            self.__route,
            facade,
            seal=_ROUTE_WRAPPER_SEAL,
        )
        object.__setattr__(self, "_RouteReadFacade__query_facade", selected)
        return selected


class MultiRouteReadAggregator:
    """Query exact routes through caller-supplied read-only SDK facades.

    The constructor validates that every mapping key is the sole route exposed
    by its exact read-only facade. Query operations are requested by exact
    route name; this class never derives a secondary route, opens another
    client, falls back to another provider, merges receipts, or grants write
    methods. Facade lifecycle remains with the caller.
    """

    __slots__ = ("__routes",)

    def __init__(self, facades: Mapping[str, ReadOnlyManagedRuntimeFacade]) -> None:
        if not isinstance(facades, Mapping) or not facades:
            raise MultiRouteReadError("MULTI_ROUTE_READ_INPUT_INVALID")
        selected: dict[str, ReadOnlyManagedRuntimeFacade] = {}
        try:
            entries = tuple(facades.items())
        except Exception:
            raise MultiRouteReadError("MULTI_ROUTE_READ_INPUT_INVALID") from None
        if not entries:
            raise MultiRouteReadError("MULTI_ROUTE_READ_INPUT_INVALID")
        for route, facade in entries:
            _require_route(route)
            if route in selected or type(facade) is not ReadOnlyManagedRuntimeFacade:
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_FACADE_INVALID",
                    route=route,
                )
            try:
                exchanges = facade.list_exchanges()
            except Exception:
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_ROUTE_UNAVAILABLE",
                    route=route,
                ) from None
            if type(exchanges) is not list or exchanges != [route]:
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_ROUTE_MISMATCH",
                    route=route,
                )
            selected[route] = facade
        self.__routes = MappingProxyType(
            {
                route: RouteReadFacade(
                    route,
                    selected[route],
                    seal=_ROUTE_WRAPPER_SEAL,
                )
                for route in sorted(selected)
            }
        )

    @property
    def routes(self) -> tuple[str, ...]:
        return tuple(self.__routes)

    def route(self, exchange_name: str) -> RouteReadFacade:
        _require_route(exchange_name)
        selected = self.__routes.get(exchange_name)
        if selected is None:
            raise MultiRouteReadError(
                "MULTI_ROUTE_READ_ROUTE_UNAVAILABLE",
                route=exchange_name,
            )
        return selected

    async def query_orders(
        self,
        requests_by_route: Mapping[str, Sequence[QueryOrderRequest]],
    ) -> tuple[RouteOrderQueryOutcome, ...]:
        plans = self.__validate_order_queries(requests_by_route)
        outcomes: list[RouteOrderQueryOutcome] = []
        for route in self.routes:
            requests = plans.get(route)
            if requests is None:
                continue
            selected = self.route(route)
            for ordinal, request in enumerate(requests):
                try:
                    result = await selected.async_query_order(request)
                except Exception as exc:
                    outcomes.append(
                        RouteOrderQueryOutcome(
                            route=route,
                            ordinal=ordinal,
                            returned=False,
                            error_code=_exception_code(exc, "MULTI_ROUTE_READ_FAILED"),
                        )
                    )
                else:
                    outcomes.append(
                        RouteOrderQueryOutcome(
                            route=route,
                            ordinal=ordinal,
                            returned=True,
                            result=result,
                        )
                    )
        return tuple(outcomes)

    def refresh_queries(
        self,
        requests_by_route: Mapping[str, Sequence[QueryRequest]],
        *,
        deadline: datetime,
    ) -> tuple[RouteQueryBatchOutcome, ...]:
        plans = self.__validate_snapshot_queries(requests_by_route)
        outcomes: list[RouteQueryBatchOutcome] = []
        for route in self.routes:
            requests = plans.get(route)
            if requests is None:
                continue
            try:
                receipt = self.route(route).query_facade().refresh(
                    requests,
                    deadline=deadline,
                )
            except Exception as exc:
                outcomes.append(
                    RouteQueryBatchOutcome(
                        route=route,
                        returned=False,
                        error_code=_exception_code(exc, "MULTI_ROUTE_READ_FAILED"),
                    )
                )
            else:
                outcomes.append(
                    RouteQueryBatchOutcome(
                        route=route,
                        returned=True,
                        receipt=receipt,
                    )
                )
        return tuple(outcomes)

    def __validate_order_queries(
        self,
        requests_by_route: Mapping[str, Sequence[QueryOrderRequest]],
    ) -> dict[str, tuple[QueryOrderRequest, ...]]:
        if not isinstance(requests_by_route, Mapping) or not requests_by_route:
            raise MultiRouteReadError("MULTI_ROUTE_READ_REQUEST_INVALID")
        try:
            entries = tuple(requests_by_route.items())
        except Exception:
            raise MultiRouteReadError("MULTI_ROUTE_READ_REQUEST_INVALID") from None
        plans: dict[str, tuple[QueryOrderRequest, ...]] = {}
        count = 0
        for route, requests in entries:
            self.route(route)
            if route in plans or not isinstance(requests, Sequence) or isinstance(
                requests,
                (str, bytes, bytearray),
            ):
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_REQUEST_INVALID",
                    route=route,
                )
            selected = tuple(requests)
            if not selected or any(type(item) is not QueryOrderRequest for item in selected):
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_REQUEST_INVALID",
                    route=route,
                )
            count += len(selected)
            if count > _MAX_MULTI_ROUTE_READ_REQUESTS:
                raise MultiRouteReadError("MULTI_ROUTE_READ_REQUEST_INVALID")
            plans[route] = selected
        return plans

    def __validate_snapshot_queries(
        self,
        requests_by_route: Mapping[str, Sequence[QueryRequest]],
    ) -> dict[str, tuple[QueryRequest, ...]]:
        if not isinstance(requests_by_route, Mapping) or not requests_by_route:
            raise MultiRouteReadError("MULTI_ROUTE_READ_REQUEST_INVALID")
        try:
            entries = tuple(requests_by_route.items())
        except Exception:
            raise MultiRouteReadError("MULTI_ROUTE_READ_REQUEST_INVALID") from None
        plans: dict[str, tuple[QueryRequest, ...]] = {}
        count = 0
        for route, requests in entries:
            self.route(route)
            if route in plans or not isinstance(requests, Sequence) or isinstance(
                requests,
                (str, bytes, bytearray),
            ):
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_REQUEST_INVALID",
                    route=route,
                )
            selected = tuple(requests)
            if not selected or any(type(item) is not QueryRequest for item in selected):
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_REQUEST_INVALID",
                    route=route,
                )
            if len(selected) > MAX_QUERY_BATCH_REQUESTS:
                raise MultiRouteReadError(
                    "MULTI_ROUTE_READ_REQUEST_INVALID",
                    route=route,
                )
            count += len(selected)
            if count > _MAX_MULTI_ROUTE_READ_REQUESTS:
                raise MultiRouteReadError("MULTI_ROUTE_READ_REQUEST_INVALID")
            plans[route] = selected
        return plans


def _require_route(value: Any) -> str:
    if type(value) is not str or not _ROUTE_PATTERN.fullmatch(value):
        raise MultiRouteReadError("MULTI_ROUTE_READ_ROUTE_INVALID")
    return value


def _valid_error_code(value: Any) -> bool:
    return type(value) is str and bool(_ERROR_CODE_PATTERN.fullmatch(value))


def _exception_code(exc: BaseException, fallback: str) -> str:
    value = getattr(exc, "code", None)
    if _valid_error_code(value):
        return value
    return fallback


def _route_error(exc: BaseException, route: str, fallback: str) -> MultiRouteReadError:
    return MultiRouteReadError(_exception_code(exc, fallback), route=route)
