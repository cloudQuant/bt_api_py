"""Provider-neutral query and managed execution client orchestration.

The caller supplies the facade references and its boundary error type. Provider
authority, query evidence, single-use claims and outcome semantics remain owned
by the SDK facades and selected provider. This client retains no trading state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ManagedExecutionClient:
    """Adapt neutral facade operations without owning framework objects."""

    def __init__(self, *, query_facade: Any, managed_execution_facade: Any, error_type: type):
        self.query_facade = query_facade
        self.managed_execution_facade = managed_execution_facade
        self._error_type = error_type

    def query(self, requests, *, deadline: datetime):
        """Run one neutral query batch through the SDK-owned QueryFacade."""

        facade = self.require_query_facade()
        try:
            return facade.refresh(requests, deadline=deadline)
        except Exception as error:
            code = getattr(error, "code", None)
            raise self._error_type(
                code if _finite_code(code) else "PROVIDER_QUERY_UNAVAILABLE"
            ) from error

    def snapshot(self, receipt: Any, request_ref: str):
        """Select an existing receipt snapshot without performing I/O."""

        facade = self.require_query_facade()
        try:
            return facade.snapshot(receipt, request_ref)
        except Exception as error:
            code = getattr(error, "code", None)
            raise self._error_type(
                code if _finite_code(code) else "PROVIDER_SNAPSHOT_UNAVAILABLE"
            ) from error

    def acquire_snapshot_lease(
        self,
        receipt: Any,
        *,
        purpose: str,
        expires_at: datetime,
    ):
        facade = self.require_query_facade()
        try:
            return facade.acquire_snapshot_lease(
                receipt,
                purpose=purpose,
                expires_at=expires_at,
            )
        except Exception as error:
            code = getattr(error, "code", None)
            raise self._error_type(
                code if _finite_code(code) else "PROVIDER_SNAPSHOT_LEASE_UNAVAILABLE"
            ) from error

    def validate_snapshot_lease(self, lease: Any, receipt: Any, *, now: datetime) -> bool:
        facade = self.require_query_facade()
        try:
            return facade.validate_lease(lease, receipt, now=now) is True
        except Exception:
            return False

    def claim(self, operation: str, request: Any, *, authorization: Any):
        """Delegate one explicit provider claim to the SDK facade."""

        facade = self._require_managed_execution_facade()
        if operation not in ("submit", "cancel"):
            raise self._error_type("MANAGED_EXECUTION_OPERATION_INVALID")
        self._validate_request(operation, request)
        self._require_authorization(authorization)
        try:
            claim = facade.claim(operation, request, authorization=authorization)
        except Exception as error:
            raise self._sdk_error(error, "MANAGED_EXECUTION_CLAIM_UNAVAILABLE") from error
        self._require_opaque(claim, "MANAGED_EXECUTION_CLAIM_UNAVAILABLE")
        return claim

    def submit(self, request: Any, *, claim: Any, authorization: Any):
        """Delegate a single submit attempt; provider execution stays in the SDK."""

        facade = self._require_managed_execution_facade()
        self._validate_request("submit", request)
        self._require_authorization(authorization)
        self._require_opaque(claim, "MANAGED_EXECUTION_CLAIM_REQUIRED")
        try:
            result = facade.submit(request, claim=claim, authorization=authorization)
        except Exception as error:
            raise self._sdk_error(error, "MANAGED_EXECUTION_OUTCOME_UNKNOWN") from error
        self._require_opaque(result, "MANAGED_EXECUTION_OUTCOME_UNKNOWN")
        return result

    def cancel(self, request: Any, *, claim: Any, authorization: Any):
        """Delegate a single cancel attempt; provider execution stays in the SDK."""

        facade = self._require_managed_execution_facade()
        self._validate_request("cancel", request)
        self._require_authorization(authorization)
        self._require_opaque(claim, "MANAGED_EXECUTION_CLAIM_REQUIRED")
        try:
            result = facade.cancel(request, claim=claim, authorization=authorization)
        except Exception as error:
            raise self._sdk_error(error, "MANAGED_EXECUTION_OUTCOME_UNKNOWN") from error
        self._require_opaque(result, "MANAGED_EXECUTION_OUTCOME_UNKNOWN")
        return result

    def resolve_unknown(
        self,
        request: Any,
        *,
        receipt: Any,
        lease: Any,
        now: datetime,
        authorization: Any,
    ):
        """Delegate UNKNOWN resolution with receipt and lease revalidation."""

        facade = self._require_managed_execution_facade()
        try:
            from bt_api_py import CancelOrderRequest, OrderRequest
        except Exception as error:
            raise self._error_type("SDK_ORDER_REQUEST_SCHEMA_UNAVAILABLE") from error
        if type(request) not in (OrderRequest, CancelOrderRequest):
            raise self._error_type("SDK_ORDER_REQUEST_SCHEMA_INVALID")
        if (
            type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timezone.utc.utcoffset(now)
        ):
            raise self._error_type("QUERY_VALIDATION_TIME_INVALID")
        self._require_authorization(authorization)
        if not self.validate_snapshot_lease(lease, receipt, now=now):
            raise self._error_type("QUERY_SNAPSHOT_LEASE_STALE")
        try:
            result = facade.resolve_unknown(
                request,
                receipt=receipt,
                lease=lease,
                now=now,
                authorization=authorization,
            )
        except Exception as error:
            raise self._sdk_error(error, "MANAGED_EXECUTION_OUTCOME_UNKNOWN") from error
        self._require_opaque(result, "MANAGED_EXECUTION_OUTCOME_UNKNOWN")
        return result

    def require_query_facade(self):
        try:
            from bt_api_py.query_facade import QueryFacade
        except Exception as error:
            raise self._error_type("QUERY_FACADE_UNAVAILABLE") from error
        if type(self.query_facade) is not QueryFacade:
            raise self._error_type("QUERY_FACADE_REQUIRED")
        return self.query_facade

    def _require_managed_execution_facade(self):
        try:
            from bt_api_py.execution import ManagedExecutionFacade
        except Exception as error:
            raise self._error_type("MANAGED_EXECUTION_UNAVAILABLE") from error
        if type(self.managed_execution_facade) is not ManagedExecutionFacade:
            raise self._error_type("MANAGED_EXECUTION_REQUIRED")
        return self.managed_execution_facade

    def _validate_request(self, operation: str, request: Any) -> None:
        try:
            from bt_api_py import CancelOrderRequest, OrderRequest
        except Exception as error:
            raise self._error_type("SDK_ORDER_REQUEST_SCHEMA_UNAVAILABLE") from error
        expected = OrderRequest if operation == "submit" else CancelOrderRequest
        if type(request) is not expected:
            raise self._error_type("SDK_ORDER_REQUEST_SCHEMA_INVALID")

    def _sdk_error(self, error: BaseException, fallback: str) -> Exception:
        code = getattr(error, "code", None)
        return self._error_type(code if _finite_code(code) else fallback)

    def _require_authorization(self, value: Any) -> None:
        if value is None or type(value) is bool:
            raise self._error_type("MANAGED_EXECUTION_AUTHORIZATION_REQUIRED")

    def _require_opaque(self, value: Any, code: str) -> None:
        if value is None or type(value) is bool:
            raise self._error_type(code)


def _finite_code(value: Any) -> bool:
    if type(value) is not str or not value or value != value.strip() or len(value) > 96:
        return False
    return all(character.isalnum() or character in "_.-" for character in value)


__all__ = ("ManagedExecutionClient",)
