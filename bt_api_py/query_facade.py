"""Provider-neutral facade for bounded query snapshots and their leases.

Exchange packages implement :class:`ProviderQueryPort`; callers interact with
this facade using only the versioned ``bt_api_base`` query contracts. The
facade owns no provider session, native handle, credentials, or snapshot data.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from bt_api_base.account_session_protocol import AccountSessionSnapshot
from bt_api_base.query_provider_protocol import SelectedQueryRoute
from bt_api_base.query_protocol import (
    MAX_QUERY_BATCH_REQUESTS,
    ProviderQueryPort,
    QueryBatchReceipt,
    QueryRequest,
    QuerySnapshot,
    QuerySnapshotLease,
)

class QueryFacadeError(RuntimeError):
    """A provider-neutral query boundary failure with a finite public code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class QueryFacade:
    """Delegate bounded query and lease operations to one provider query port.

    ``refresh`` performs a provider query batch. ``snapshot`` only selects an
    immutable result from a previously returned receipt and never starts a new
    provider request. A lease is issued and validated by the provider port;
    this facade only checks that the returned descriptor is bound to the
    supplied receipt.
    """

    __slots__ = (
        "__weakref__",
        "__provider",
        "__selected_route",
        "__account_session_snapshot_validator",
        "__composition_issuer",
        "__issued_receipt_ref",
        "__issued_lease_ref",
    )

    def __init__(
        self,
        provider: ProviderQueryPort,
        *,
        selected_route: SelectedQueryRoute | None = None,
        account_session_snapshot_validator: (
            Callable[[SelectedQueryRoute, AccountSessionSnapshot | None], bool] | None
        ) = None,
        _composition_issuer: object | None = None,
    ) -> None:
        if provider is None:
            raise TypeError("provider must implement ProviderQueryPort")
        if selected_route is not None and type(selected_route) is not SelectedQueryRoute:
            raise TypeError("selected_route must be an exact SelectedQueryRoute or None")
        if account_session_snapshot_validator is not None and not callable(
            account_session_snapshot_validator
        ):
            raise TypeError("account session validator must be callable or None")
        if account_session_snapshot_validator is not None and selected_route is None:
            raise ValueError("account session validator requires a selected route")
        if account_session_snapshot_validator is not None:
            # This cross-capability binding is installed only by BtApi's
            # selected-route composition. Arbitrary callbacks would let a
            # directly constructed facade bless caller-created session DTOs.
            from .bt_api import BtApi

            if (
                type(getattr(account_session_snapshot_validator, "__self__", None))
                is not BtApi
                or getattr(account_session_snapshot_validator, "__func__", None)
                is not BtApi._validate_query_account_session_snapshot
            ):
                raise TypeError("account session validator must be SDK-owned")
        for method_name in ("query_batch", "acquire_snapshot_lease", "validate_lease"):
            if not callable(getattr(provider, method_name, None)):
                raise TypeError("provider must implement ProviderQueryPort")
        self.__provider = provider
        self.__selected_route = selected_route
        self.__account_session_snapshot_validator = account_session_snapshot_validator
        self.__composition_issuer = _composition_issuer
        self.__issued_receipt_ref = None
        self.__issued_lease_ref = None

    def validate_account_session_snapshot(
        self,
        snapshot: AccountSessionSnapshot,
        *,
        receipt: QueryBatchReceipt,
        lease: QuerySnapshotLease,
    ) -> bool:
        """Validate that an exact SDK-issued session snapshot remains bound to this route.

        The exact receipt and lease must have been issued by this facade for its
        currently selected route. Directly constructed facades without the
        SDK-owned route validator, or snapshots from a different facade, return
        false and cannot establish account/session binding.
        """

        route = self.__selected_route
        validator = self.__account_session_snapshot_validator
        issuer = self.__composition_issuer
        receipt_session_ref = (
            getattr(receipt, "session_ref", None)
            if type(receipt) is QueryBatchReceipt
            else None
        )
        try:
            from .query_composition import (
                QueryProviderRegistry,
                default_query_provider_registry,
            )

            issued_by_default_registry = (
                type(issuer) is QueryProviderRegistry
                and issuer is default_query_provider_registry()
                and issuer._owns_facade(self, route)
            )
        except Exception:
            issued_by_default_registry = False
        if (
            route is None
            or validator is None
            or issued_by_default_registry is not True
            or type(snapshot) is not AccountSessionSnapshot
            or snapshot.exchange_name != route.exchange_name
            or snapshot.available is not True
            or not snapshot.source_ref
            or not snapshot.generation
            or not snapshot.session_ref
            or not self._owns_receipt_lease(receipt, lease)
            or snapshot.source_ref != receipt.source_ref
            or snapshot.generation != receipt.generation
            or snapshot.session_ref != receipt_session_ref
        ):
            return False
        try:
            current = self.validate_lease(
                lease,
                receipt,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            return False
        if type(current) is not bool or current is not True:
            return False
        try:
            result = validator(route, snapshot)
        except Exception:
            return False
        if type(result) is not bool or result is not True:
            return False
        # Recheck the provider lease after the account-route read so route or
        # source changes during the cross-capability check cannot validate an
        # earlier state.
        try:
            current = self.validate_lease(
                lease,
                receipt,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            return False
        return type(current) is bool and current is True

    def refresh(
        self,
        requests: Sequence[QueryRequest],
        *,
        deadline: datetime,
    ) -> QueryBatchReceipt:
        """Run one bounded query batch and validate its request/source binding."""

        selected = _validate_requests(requests)
        _validate_utc(deadline, "deadline")
        if not self._selected_route_is_current():
            raise QueryFacadeError("SELECTED_QUERY_ROUTE_UNAVAILABLE")
        try:
            receipt = self.__provider.query_batch(selected, deadline=deadline)
        except Exception:
            raise QueryFacadeError("PROVIDER_QUERY_FAILED") from None
        if type(receipt) is not QueryBatchReceipt or receipt.requests != selected:
            raise QueryFacadeError("INVALID_PROVIDER_RECEIPT")
        # QueryBatchReceipt checks exact request membership and one source and
        # generation. Repeat these boundary checks so a substituted provider
        # cannot return an incompatible subclass or bypass the shared contract.
        request_by_ref = {request.request_ref: request for request in selected}
        seen: set[str] = set()
        for snapshot in receipt.snapshots:
            if type(snapshot) is not QuerySnapshot:
                raise QueryFacadeError("INVALID_PROVIDER_RECEIPT")
            request_ref = snapshot.request.request_ref
            if request_by_ref.get(request_ref) != snapshot.request or request_ref in seen:
                raise QueryFacadeError("INVALID_PROVIDER_RECEIPT")
            seen.add(request_ref)
            if snapshot.source_ref is not None and snapshot.source_ref != receipt.source_ref:
                raise QueryFacadeError("INVALID_PROVIDER_RECEIPT")
            if snapshot.generation is not None and snapshot.generation != receipt.generation:
                raise QueryFacadeError("INVALID_PROVIDER_RECEIPT")
        if not self._selected_route_is_current():
            raise QueryFacadeError("SELECTED_QUERY_ROUTE_NOT_CURRENT")
        try:
            receipt_ref = weakref.ref(receipt)
        except TypeError:
            raise QueryFacadeError("INVALID_PROVIDER_RECEIPT") from None
        self.__issued_receipt_ref = receipt_ref
        self.__issued_lease_ref = None
        return receipt

    def snapshot(self, receipt: QueryBatchReceipt, request_ref: str) -> QuerySnapshot | None:
        """Return one request's snapshot from ``receipt`` without querying again."""

        _validate_receipt(receipt)
        if not self._owns_receipt(receipt):
            raise QueryFacadeError("RECEIPT_NOT_ISSUED_BY_FACADE")
        if not self._selected_route_is_current():
            raise QueryFacadeError("SELECTED_QUERY_ROUTE_NOT_CURRENT")
        if type(request_ref) is not str or not request_ref or request_ref != request_ref.strip():
            raise ValueError("request_ref must be a non-empty trimmed string")
        for snapshot in receipt.snapshots:
            if snapshot.request.request_ref == request_ref:
                return snapshot
        return None

    def acquire_snapshot_lease(
        self,
        receipt: QueryBatchReceipt,
        *,
        purpose: str,
        expires_at: datetime,
    ) -> QuerySnapshotLease:
        """Ask the provider to issue a lease over this receipt's complete results."""

        _validate_receipt(receipt)
        if not self._owns_receipt(receipt):
            raise QueryFacadeError("RECEIPT_NOT_ISSUED_BY_FACADE")
        if not receipt.complete or not receipt.complete_snapshot_refs:
            raise QueryFacadeError("INCOMPLETE_RECEIPT")
        if type(purpose) is not str or not purpose or purpose != purpose.strip():
            raise ValueError("purpose must be a non-empty trimmed string")
        _validate_utc(expires_at, "expires_at")
        if not self._selected_route_is_current():
            raise QueryFacadeError("SELECTED_QUERY_ROUTE_NOT_CURRENT")
        try:
            lease = self.__provider.acquire_snapshot_lease(
                receipt,
                purpose=purpose,
                expires_at=expires_at,
            )
        except Exception:
            raise QueryFacadeError("PROVIDER_LEASE_ISSUE_FAILED") from None
        if (
            type(lease) is not QuerySnapshotLease
            or lease.receipt_digest != receipt.receipt_digest
            or lease.source_ref != receipt.source_ref
            or lease.generation != receipt.generation
            or lease.snapshot_refs != receipt.complete_snapshot_refs
            or lease.purpose != purpose
            or lease.expires_at > expires_at
        ):
            raise QueryFacadeError("INVALID_PROVIDER_LEASE")
        if not self._selected_route_is_current():
            raise QueryFacadeError("SELECTED_QUERY_ROUTE_NOT_CURRENT")
        try:
            self.__issued_lease_ref = weakref.ref(lease)
        except TypeError:
            raise QueryFacadeError("INVALID_PROVIDER_LEASE") from None
        return lease

    def validate_lease(
        self,
        lease: QuerySnapshotLease,
        receipt: QueryBatchReceipt,
        *,
        now: datetime,
    ) -> bool:
        """Return true only while the provider confirms this exact receipt lease."""

        if type(lease) is not QuerySnapshotLease:
            raise TypeError("lease must be a QuerySnapshotLease")
        _validate_receipt(receipt)
        if not self._owns_receipt_lease(receipt, lease):
            return False
        _validate_utc(now, "now")
        refs = receipt.complete_snapshot_refs
        if (
            not receipt.complete
            or not refs
            or lease.receipt_digest != receipt.receipt_digest
            or lease.source_ref != receipt.source_ref
            or lease.generation != receipt.generation
            or lease.snapshot_refs != refs
            or now < lease.issued_at
            or now >= lease.expires_at
        ):
            return False
        if not self._selected_route_is_current():
            return False
        try:
            result = self.__provider.validate_lease(lease, receipt, now=now)
        except Exception:
            # The contract treats inability to confirm currentness as false.
            return False
        if type(result) is not bool:
            raise QueryFacadeError("INVALID_PROVIDER_LEASE_RESULT")
        return result and self._selected_route_is_current()

    def _selected_route_is_current(self) -> bool:
        route = self.__selected_route
        validator = self.__account_session_snapshot_validator
        if route is None or validator is None:
            return True
        try:
            result = validator(route, None)
        except Exception:
            return False
        return type(result) is bool and result is True

    def _owns_receipt(self, receipt: QueryBatchReceipt) -> bool:
        if type(receipt) is not QueryBatchReceipt:
            return False
        reference = self.__issued_receipt_ref
        try:
            return reference is not None and reference() is receipt
        except Exception:
            return False

    def _owns_receipt_lease(
        self,
        receipt: QueryBatchReceipt,
        lease: QuerySnapshotLease,
    ) -> bool:
        if type(receipt) is not QueryBatchReceipt or type(lease) is not QuerySnapshotLease:
            return False
        reference = self.__issued_lease_ref
        try:
            return (
                self._owns_receipt(receipt)
                and reference is not None
                and reference() is lease
            )
        except Exception:
            return False


def _validate_requests(requests: Sequence[QueryRequest]) -> tuple[QueryRequest, ...]:
    if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes, bytearray)):
        raise TypeError("requests must be a sequence of QueryRequest values")
    selected = tuple(requests)
    if not 1 <= len(selected) <= MAX_QUERY_BATCH_REQUESTS:
        raise ValueError("requests must contain between 1 and the batch limit")
    if any(type(request) is not QueryRequest for request in selected):
        raise TypeError("requests must contain QueryRequest values")
    refs = tuple(request.request_ref for request in selected)
    if len(set(refs)) != len(refs):
        raise ValueError("request_ref values must be unique within a batch")
    return selected


def _validate_receipt(receipt: QueryBatchReceipt) -> None:
    if type(receipt) is not QueryBatchReceipt:
        raise TypeError("receipt must be a QueryBatchReceipt")


def _validate_utc(value: datetime, field_name: str) -> None:
    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


__all__ = ["QueryFacade", "QueryFacadeError"]
