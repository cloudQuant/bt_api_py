"""Exact route composition for opaque SDK reconciliation handles.

The BT-facing object never accepts a caller-built ``ReconciliationRequest``.
An SDK trusted runtime resolver binds an order correlation or an authorized
account request to private scope data. The route facade keeps that request in
an identity-checked registry and returns only SDK-issued opaque handles.
Provider authorities and request resolvers remain inside SDK composition.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bt_api_base.reconciliation_protocol import (
    ProviderReconciliationAuthority,
    ReconciliationEvidence,
    ReconciliationRequest,
    ReconciliationRequestResolver,
)
from bt_api_base.execution_correlation_protocol import OrderCorrelation

from bt_api_py.facade_protocol import (
    ReconciliationFacadePort,
    ReconciliationRequestHandle,
)
from bt_api_py.reconciliation import (
    ReconciliationFacade,
    ReconciliationResult,
    _result,
)

_MAX_ISSUED_EVIDENCE = 32
_MAX_ISSUED_HANDLES = 128
_MAX_ISSUED_RESULTS = 256


class ReconciliationRouteError(ValueError):
    """Finite public failure from reconciliation route preparation or use."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ReconciliationRoute:
    """One exact provider route and its SDK-owned scope resolver."""

    venue_ref: str
    authority: ProviderReconciliationAuthority
    request_resolver: ReconciliationRequestResolver | None = None


class _RouteBoundAuthority:
    """SDK wrapper that constrains one provider authority to one exact route."""

    def __init__(
        self,
        exchange_name: str,
        venue_ref: str,
        authority: ProviderReconciliationAuthority,
    ) -> None:
        issuer_ref = getattr(authority, "issuer_ref", None)
        if (
            not _valid_text(exchange_name)
            or not _valid_text(venue_ref)
            or not _valid_text(issuer_ref)
        ):
            raise ReconciliationRouteError("reconciliation_route_configuration_invalid")
        required = (
            "query_reconciliation",
            "verify_reconciliation_evidence",
            "validate_reconciliation_lease",
            "verify_position_consistency",
        )
        if any(not callable(getattr(authority, name, None)) for name in required):
            raise ReconciliationRouteError("reconciliation_authority_invalid")
        self.issuer_ref = issuer_ref
        self._venue_ref = venue_ref
        self._authority = authority
        self._issued: dict[int, ReconciliationEvidence] = {}
        self._lock = threading.RLock()

    def query_reconciliation(
        self,
        request: ReconciliationRequest,
        *,
        deadline: datetime,
    ) -> ReconciliationEvidence:
        if type(request) is not ReconciliationRequest:
            raise ReconciliationRouteError("reconciliation_request_invalid")
        if request.venue_ref != self._venue_ref:
            evidence = ReconciliationEvidence(
                request=request,
                issuer_ref=self.issuer_ref,
                blockers=("reconciliation_route_scope_mismatch",),
            )
        else:
            evidence = self._authority.query_reconciliation(request, deadline=deadline)
        if type(evidence) is not ReconciliationEvidence:
            raise ReconciliationRouteError("reconciliation_evidence_invalid")
        with self._lock:
            self._issued[id(evidence)] = evidence
            while len(self._issued) > _MAX_ISSUED_EVIDENCE:
                self._issued.pop(next(iter(self._issued)))
        return evidence

    def verify_reconciliation_evidence(
        self,
        evidence: ReconciliationEvidence,
        request: ReconciliationRequest,
    ) -> bool:
        if not self._is_issued(evidence) or request.venue_ref != self._venue_ref:
            return False
        return self._authority.verify_reconciliation_evidence(evidence, request)

    def validate_reconciliation_lease(
        self,
        evidence: ReconciliationEvidence,
        *,
        now: datetime,
    ) -> bool:
        if not self._is_issued(evidence):
            return False
        return self._authority.validate_reconciliation_lease(evidence, now=now)

    def verify_position_consistency(self, evidence: ReconciliationEvidence) -> bool:
        if not self._is_issued(evidence):
            return False
        return self._authority.verify_position_consistency(evidence)

    def _is_issued(self, evidence: Any) -> bool:
        if type(evidence) is not ReconciliationEvidence:
            return False
        with self._lock:
            return self._issued.get(id(evidence)) is evidence


class _RouteBoundFacade(ReconciliationFacadePort):
    """BT-facing port with opaque, route-local request/result identities."""

    def __init__(
        self,
        exchange_name: str,
        route: ReconciliationRoute,
    ) -> None:
        if not _valid_text(exchange_name) or type(route) is not ReconciliationRoute:
            raise ReconciliationRouteError("reconciliation_route_configuration_invalid")
        if not _valid_text(route.venue_ref):
            raise ReconciliationRouteError("reconciliation_route_configuration_invalid")
        if route.request_resolver is not None and any(
            not callable(getattr(route.request_resolver, name, None))
            for name in (
                "issue_order_request",
                "issue_authorized_account_request",
            )
        ):
            raise ReconciliationRouteError("reconciliation_request_resolver_invalid")

        authority = _RouteBoundAuthority(exchange_name, route.venue_ref, route.authority)
        self._exchange_name = exchange_name
        self._venue_ref = route.venue_ref
        self._resolver = route.request_resolver
        self._authority = authority
        self._facade = ReconciliationFacade(authority)
        self._handles: dict[
            int,
            tuple[
                ReconciliationRequestHandle,
                ReconciliationRequest,
                OrderCorrelation | None,
            ],
        ] = {}
        self._results: dict[
            int,
            tuple[
                ReconciliationResult,
                ReconciliationRequestHandle,
                ReconciliationRequest,
                OrderCorrelation | None,
            ],
        ] = {}
        self._lock = threading.RLock()

    def prepare_order_reconciliation(
        self,
        correlation: OrderCorrelation,
    ) -> ReconciliationRequestHandle:
        if type(correlation) is not OrderCorrelation:
            raise ReconciliationRouteError("reconciliation_correlation_invalid")
        try:
            correlation.__post_init__()
        except Exception:
            raise ReconciliationRouteError("reconciliation_correlation_invalid") from None
        if correlation.exchange_name != self._exchange_name:
            raise ReconciliationRouteError("reconciliation_route_scope_mismatch")
        resolver = self._require_resolver()
        try:
            request = resolver.issue_order_request(correlation)
        except Exception:
            raise ReconciliationRouteError("reconciliation_request_unavailable") from None
        if type(request) is not ReconciliationRequest:
            raise ReconciliationRouteError("reconciliation_request_unavailable")
        if (
            request.venue_ref != self._venue_ref
            or len(request.intent_refs) != 1
            or len(request.instrument_refs) != 1
        ):
            raise ReconciliationRouteError("reconciliation_request_scope_unbound")
        return self._issue_handle(request, correlation)

    def prepare_authorized_account_reconciliation(self) -> ReconciliationRequestHandle:
        resolver = self._require_resolver()
        try:
            request = resolver.issue_authorized_account_request()
        except Exception:
            raise ReconciliationRouteError("reconciliation_request_unavailable") from None
        if type(request) is not ReconciliationRequest:
            raise ReconciliationRouteError("reconciliation_request_unavailable")
        if request.venue_ref != self._venue_ref:
            raise ReconciliationRouteError("reconciliation_request_scope_unbound")
        return self._issue_handle(request, None)

    def snapshot(
        self,
        handle: ReconciliationRequestHandle,
        *,
        timeout: float = 5.0,
    ) -> ReconciliationResult:
        issued = self._lookup_handle(handle)
        if issued is None:
            raise ReconciliationRouteError("reconciliation_request_handle_unrecognized")
        _, request, correlation = issued
        result = self._facade.snapshot(request, timeout=timeout)
        if correlation is not None and result.safe_to_resume is True:
            if not _correlation_matches(result, request, correlation):
                result = _result(
                    result.evidence,
                    False,
                    "reconciliation_correlation_binding_mismatch",
                )
        if type(result) is not ReconciliationResult:
            raise ReconciliationRouteError("reconciliation_result_invalid")
        with self._lock:
            self._results[id(result)] = (result, handle, request, correlation)
            while len(self._results) > _MAX_ISSUED_RESULTS:
                self._results.pop(next(iter(self._results)))
        return result

    def validate_snapshot(
        self,
        handle: ReconciliationRequestHandle,
        result: ReconciliationResult,
        *,
        now: datetime,
    ) -> bool:
        if (
            type(handle) is not ReconciliationRequestHandle
            or type(result) is not ReconciliationResult
            or type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            return False
        issued_handle = self._lookup_handle(handle)
        if issued_handle is None:
            return False
        issued_result = self._lookup_result(result)
        if issued_result is None:
            return False
        registered_result, registered_handle, request, correlation = issued_result
        if (
            registered_result is not result
            or registered_handle is not handle
            or request is not issued_handle[1]
            or correlation != issued_handle[2]
        ):
            return False
        if correlation is not None and not _correlation_matches(result, request, correlation):
            return False
        verified = self._facade.validate_snapshot(request, result, now=now)
        return type(verified) is bool and verified is True

    def _require_resolver(self) -> ReconciliationRequestResolver:
        if self._resolver is None:
            raise ReconciliationRouteError("reconciliation_request_unavailable")
        return self._resolver

    def _issue_handle(
        self,
        request: ReconciliationRequest,
        correlation: OrderCorrelation | None,
    ) -> ReconciliationRequestHandle:
        handle = ReconciliationRequestHandle()
        with self._lock:
            self._handles[id(handle)] = (handle, request, correlation)
            while len(self._handles) > _MAX_ISSUED_HANDLES:
                stale_id = next(iter(self._handles))
                self._handles.pop(stale_id)
                for result_id, registered in tuple(self._results.items()):
                    if id(registered[1]) == stale_id:
                        self._results.pop(result_id, None)
        return handle

    def _lookup_handle(
        self,
        handle: Any,
    ) -> tuple[ReconciliationRequestHandle, ReconciliationRequest, OrderCorrelation | None] | None:
        if type(handle) is not ReconciliationRequestHandle:
            return None
        with self._lock:
            issued = self._handles.get(id(handle))
            if issued is None or issued[0] is not handle:
                return None
            return issued

    def _lookup_result(
        self,
        result: Any,
    ) -> tuple[
        ReconciliationResult,
        ReconciliationRequestHandle,
        ReconciliationRequest,
        OrderCorrelation | None,
    ] | None:
        if type(result) is not ReconciliationResult:
            return None
        with self._lock:
            issued = self._results.get(id(result))
            if issued is None or issued[0] is not result:
                return None
            return issued


class ReconciliationRouteComposer:
    """Select opaque-handle facades from explicitly configured routes.

    ``routes`` maps an exact exchange name to a ``ReconciliationRoute``. A
    route without a trusted request resolver remains query-unavailable even
    when a provider authority is configured.
    """

    def __init__(self, routes: Mapping[str, ReconciliationRoute]) -> None:
        if not isinstance(routes, Mapping) or not routes:
            raise ReconciliationRouteError("reconciliation_routes_required")
        selected: dict[str, ReconciliationFacadePort] = {}
        for exchange_name, route in routes.items():
            if not _valid_text(exchange_name):
                raise ReconciliationRouteError("reconciliation_route_name_invalid")
            selected[exchange_name] = _RouteBoundFacade(exchange_name, route)
        self._facades = selected

    def reconciliation(self, exchange_name: str) -> ReconciliationFacadePort:
        """Implement the UnifiedBtApi optional route selector contract."""

        return self.select(exchange_name)

    def select(self, exchange_name: str) -> ReconciliationFacadePort:
        """Return the facade for one exact route key."""

        if not _valid_text(exchange_name):
            raise ReconciliationRouteError("reconciliation_route_name_invalid")
        facade = self._facades.get(exchange_name)
        if facade is None:
            raise ReconciliationRouteError("reconciliation_route_unavailable")
        return facade


def _correlation_matches(
    result: ReconciliationResult,
    request: ReconciliationRequest,
    correlation: OrderCorrelation,
) -> bool:
    if (
        result.complete is not True
        or result.safe_to_resume is not True
        or result.evidence is None
        or type(result.evidence) is not ReconciliationEvidence
        or result.evidence.request != request
        or result.evidence.orders is None
        or len(result.evidence.orders) != 1
        or len(request.intent_refs) != 1
        or len(request.instrument_refs) != 1
    ):
        return False
    order = result.evidence.orders[0]
    return (
        order.client_order_id == correlation.client_order_id
        and order.intent_ref == request.intent_refs[0]
        and order.order.instrument_ref == request.instrument_refs[0]
    )


def _valid_text(value: Any) -> bool:
    return bool(
        type(value) is str
        and value
        and value == value.strip()
        and value.isprintable()
        and len(value.encode("utf-8", errors="strict")) <= 256
    )


__all__ = [
    "ReconciliationRoute",
    "ReconciliationRouteComposer",
    "ReconciliationRouteError",
]
