"""Thread-owned authenticated local order-cut observations."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from types import MappingProxyType

from bt_api_base.service_protocol import WireRole, WireScope

from bt_api_py.gateway_account_observer_client import (
    GatewayAccountObserverClient,
    GatewayAccountObserverRoute,
)
from bt_api_py.gateway_order_cut_codec import (
    CURRENT,
    OPERATIONS,
    QUERY_PAGE,
    RECEIPT_PAGE,
    decode_query,
    validate_response,
)
from bt_api_py.service_runtime_thread import (
    BoundCallObservation,
    BoundCallResult,
    BoundCallRoute,
    ThreadedServiceClientRuntime,
    _bound_wire_signature,
    _BoundBindingToken,
    _copy_wire,
)


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class GatewayOrderCutResult:
    outcome: str
    code: str | None = None
    _nonce: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class GatewayOrderCutCopyResult:
    outcome: str
    page: object = None
    code: str | None = None


class GatewayOrderCutClient(GatewayAccountObserverClient):
    """Borrow a started threaded runtime; authenticated pages are local observations.

    No operation here creates a query, ACKs, retries, or establishes a Risk
    ledger commit, venue completeness, or trading authority.
    """

    def __init__(self, runtime, *, route, fingerprint):
        if (
            type(runtime) is not ThreadedServiceClientRuntime
            or type(route) is not BoundCallRoute
            or route.identity.role is not WireRole.SERVICE
            or route.identity.delegate_ref is not None
            or route.peer.role is not WireRole.SERVICE
            or route.peer.delegate_ref is not None
            or route.scope.account_ref is None
            or route.scope
            != WireScope(route.scope.execution_domain_ref, account_ref=route.scope.account_ref)
            or type(fingerprint) is not str
            or re.fullmatch("[0-9a-f]{64}", fingerprint) is None
        ):
            raise ValueError("INVALID_ORDER_CUT_CLIENT")
        observer_route = GatewayAccountObserverRoute(
            "upstream.account",
            route.service_id,
            route.instance_id,
            route.scope_ref,
            route.scope,
            route.identity,
        )
        super().__init__(runtime, route=observer_route, peer=route.peer, fingerprint=fingerprint)
        self._bound_route = route
        self._original_runtime = runtime
        self._issued = []
        self._current_cut = None
        self._closed = False

    def _check_owner(self):
        if (
            self._closed
            or os.getpid() != self._pid
            or threading.current_thread() is not self._thread
            or self._runtime is not self._original_runtime
        ):
            raise ValueError("ORDER_CUT_BINDING_UNAVAILABLE")

    def _check_observation(self, bound, operation, registration):
        before = bound.before
        if (
            type(bound) is not BoundCallResult
            or type(before) is not BoundCallObservation
            or type(bound.after) is not BoundCallObservation
            or before != bound.after
            or (before.registration_id, before.service_epoch) != registration
            or before.identity != self._route.identity
            or before.scope != self._route.scope
            or before.peer != self._bound_route.peer
            or before.fingerprint != self._expected_fingerprint
            or before.mount != "upstream.account"
            or before.service_id != self._route.service_id
            or before.instance_id != self._route.instance_id
            or before.scope_ref != self._route.scope_ref
            or before.operation != operation
            or not before.operations >= OPERATIONS
            or before.active is not True
            or before.required is not True
        ):
            raise ValueError("ORDER_CUT_BINDING_UNAVAILABLE")
        return before

    def read(self, operation, arguments, *, timeout_seconds=5.0):
        args = decode_query(operation, arguments)
        try:
            self._check_owner()
            if not self._lock.acquire(blocking=False):
                raise ValueError
            try:
                if operation != CURRENT and (
                    self._current_cut is None or args["cut_ref"] != self._current_cut[0]
                ):
                    raise ValueError
                registration = self._registration(operation)
                bound = self._runtime.call_bound(
                    "upstream.account",
                    operation,
                    args,
                    route=self._bound_route,
                    timeout_seconds=timeout_seconds,
                    expected_server_key_fingerprint=self._expected_fingerprint,
                )
                before = self._check_observation(bound, operation, registration)
                result = _copy_wire(bound.result)
                if self._registration(operation) != registration:
                    raise ValueError
                if result.get("status") in {"UNAVAILABLE", "UNKNOWN", "REQUEST_REJECTED"}:
                    allowed = {
                        "SOURCE_UNAVAILABLE",
                        "SOURCE_CHANGED",
                        "OWNER_UNAVAILABLE",
                        "RESULT_TOO_LARGE",
                        "CUT_UNAVAILABLE",
                        "INVALID_REQUEST",
                        "SCOPE_DENIED",
                    }
                    if set(result) != {"status", "code"} or result["code"] not in allowed:
                        raise ValueError
                    self._issued.clear()
                    self._current_cut = None
                    return GatewayOrderCutResult(result["status"], result["code"])
                blob = validate_response(
                    operation,
                    result,
                    arguments=args,
                    account_ref=self._route.scope.account_ref,
                    registration_id=before.registration_id,
                    service_epoch=before.service_epoch,
                )
                token = bound._binding_token
                evidence = (_bound_wire_signature(args), _bound_wire_signature(result))
                if type(token) is not _BoundBindingToken or token.order_cut_evidence != evidence:
                    raise ValueError
                binding = (
                    result["cut_ref"],
                    _bound_wire_signature(result["header"]),
                    result["query_manifest_digest"],
                )
                if operation == CURRENT:
                    if self._current_cut != binding:
                        self._issued.clear()
                    self._current_cut = binding
                elif binding != self._current_cut:
                    raise ValueError
                issued = GatewayOrderCutResult("OBSERVED", _nonce=object())
                self._issued.append(
                    (
                        issued,
                        issued._nonce,
                        blob,
                        operation,
                        args,
                        before,
                        token,
                        evidence,
                        registration,
                    )
                )
                if len(self._issued) > 128:
                    self._issued.pop(0)
                return issued
            finally:
                self._lock.release()
        except Exception:
            if (
                os.getpid() == self._pid
                and threading.current_thread() is self._thread
                and self._runtime is self._original_runtime
            ):
                self._issued.clear()
                self._current_cut = None
            return GatewayOrderCutResult("UNKNOWN", "BINDING_OR_REPLY_UNAVAILABLE")

    def current(self, *, timeout_seconds=5.0):
        return self.read(CURRENT, {}, timeout_seconds=timeout_seconds)

    def query_page(
        self, *, cut_ref, after_ordinal, limit, expected_manifest_digest=None, timeout_seconds=5.0
    ):
        return self.read(
            QUERY_PAGE,
            {
                "cut_ref": cut_ref,
                "after_ordinal": after_ordinal,
                "limit": limit,
                "expected_manifest_digest": expected_manifest_digest,
            },
            timeout_seconds=timeout_seconds,
        )

    def receipt_page(
        self,
        *,
        cut_ref,
        after_source_sequence,
        limit,
        expected_manifest_digest=None,
        timeout_seconds=5.0,
    ):
        return self.read(
            RECEIPT_PAGE,
            {
                "cut_ref": cut_ref,
                "after_source_sequence": after_source_sequence,
                "limit": limit,
                "expected_manifest_digest": expected_manifest_digest,
            },
            timeout_seconds=timeout_seconds,
        )

    def validate_copy_issued(self, result, *, timeout_seconds=5.0):
        try:
            self._check_owner()
            if not self._lock.acquire(blocking=False):
                raise ValueError
        except Exception:
            return GatewayOrderCutCopyResult("UNKNOWN", code="ISSUANCE_UNAVAILABLE")
        try:
            for (
                original,
                nonce,
                blob,
                operation,
                args,
                before,
                token,
                evidence,
                registration,
            ) in self._issued:
                if (
                    result is not original
                    or result.outcome != "OBSERVED"
                    or result.code is not None
                    or result._nonce is not nonce
                ):
                    continue

                def check(
                    operation=operation,
                    registration=registration,
                    token=token,
                    evidence=evidence,
                    args=args,
                    before=before,
                ):
                    if (
                        self._registration(operation) != registration
                        or token.order_cut_evidence != evidence
                    ):
                        raise ValueError
                    checked = self._runtime.call_bound(
                        "upstream.account",
                        operation,
                        args,
                        route=self._bound_route,
                        timeout_seconds=timeout_seconds,
                        expected_server_key_fingerprint=self._expected_fingerprint,
                        expected_binding=before,
                        _expected_token=token,
                        _validate_order_cut_only=True,
                    )
                    self._check_observation(checked, operation, registration)
                    if (
                        _copy_wire(checked.result) != {"valid": True}
                        or token.order_cut_evidence != evidence
                    ):
                        raise ValueError

                check()
                frozen = _freeze(json.loads(blob))
                check()
                return GatewayOrderCutCopyResult("OBSERVED", page=frozen)
            raise ValueError
        except Exception:
            return GatewayOrderCutCopyResult("UNKNOWN", code="ISSUANCE_UNAVAILABLE")
        finally:
            self._lock.release()

    def close(self):
        self._check_owner()
        self._closed = True
        self._issued.clear()
        self._current_cut = None
