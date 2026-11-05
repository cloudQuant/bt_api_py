"""SDK-owned adapter from generic query and account-session facades.

The adapter requests the full unfiltered ``account`` family and optional
``account_valuation`` family in one exact query batch. It binds the result to
the selected session port, retains source/generation, scope completeness,
digests and the provider-issued lease. It performs no provider-specific field
mapping and never aggregates amounts across currencies.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from bt_api_base.account_balance_snapshot_protocol import (
    ACCOUNT_BALANCE_LEASE_PURPOSE,
    ACCOUNT_BALANCE_QUERY_FAMILY,
    ACCOUNT_VALUATION_QUERY_FAMILY,
    AccountBalanceSnapshot,
    AccountBalanceSnapshotFailureCode,
    AccountBalanceSnapshotPort,
)
from bt_api_base.account_session_protocol import AccountSessionPort, AccountSessionSnapshot
from bt_api_base.query_protocol import QueryBatchReceipt, QueryRequest, QuerySnapshot

from .query_facade import QueryFacade, QueryFacadeError


_UTC = timezone.utc
# Venues with ~1 query/second throttles and large reference catalogs (CTP
# positions bind the full instrument catalog) cannot revalidate within 2s;
# 15s covers lease issue plus two revalidations and stays inside the evidence
# window a provider has left after a slow query (CTP: 35s from completion).
_BALANCE_SNAPSHOT_LEASE_TTL = timedelta(seconds=15)
_MAX_ISSUED_SNAPSHOTS = 64


class AccountBalanceSnapshotFacade(AccountBalanceSnapshotPort):
    """Full-scope balance port bound to one selected SDK query facade."""

    def __init__(
        self,
        exchange_name: str,
        query_facade: QueryFacade | None,
        session_port: AccountSessionPort | None,
        *,
        unavailable_code: AccountBalanceSnapshotFailureCode | None = None,
    ) -> None:
        if query_facade is not None and type(query_facade) is not QueryFacade:
            raise TypeError("query_facade must be the exact SDK QueryFacade or None")
        if (
            type(exchange_name) is not str
            or not exchange_name
            or exchange_name != exchange_name.strip()
        ):
            raise ValueError("exchange_name must be a non-empty exact route name")
        if query_facade is None and type(unavailable_code) is not AccountBalanceSnapshotFailureCode:
            raise TypeError("unavailable facade requires a finite failure code")
        if query_facade is not None and unavailable_code is not None:
            raise ValueError("available facade cannot also declare an unavailable code")
        if session_port is not None and not callable(
            getattr(session_port, "session_snapshot", None)
        ):
            raise TypeError("session_port must expose session_snapshot or be None")
        self._query_facade = query_facade
        self._exchange_name = exchange_name
        self._session_port = session_port
        self._unavailable_code = unavailable_code
        self._lock = RLock()
        self._issued: OrderedDict[int, AccountBalanceSnapshot] = OrderedDict()

    @classmethod
    def unavailable(
        cls,
        exchange_name: str,
        code: AccountBalanceSnapshotFailureCode = (
            AccountBalanceSnapshotFailureCode.ROUTE_UNAVAILABLE
        ),
    ) -> AccountBalanceSnapshotFacade:
        """Create an explicit unavailable port without direct-client fallback."""

        return cls(exchange_name, None, None, unavailable_code=code)

    def refresh(self, *, deadline: datetime) -> AccountBalanceSnapshot:
        """Request balances and optional valuation in one selected SDK batch."""

        request = QueryRequest(
            request_ref="account-balance:" + uuid4().hex,
            family=ACCOUNT_BALANCE_QUERY_FAMILY,
            filters={},
            required=True,
            require_complete_scope=True,
        )
        valuation_request = QueryRequest(
            request_ref="account-valuation:" + uuid4().hex,
            family=ACCOUNT_VALUATION_QUERY_FAMILY,
            filters={},
            required=False,
            require_complete_scope=True,
        )
        if self._query_facade is None:
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=None,
                query_snapshot=None,
                valuation_snapshot=None,
                lease=None,
                session_snapshot=None,
                failure_code=self._unavailable_code,
            )

        session_before = self._read_session_snapshot()
        if (
            session_before is None
            or session_before.source_ref is None
            or session_before.generation is None
        ):
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=None,
                query_snapshot=None,
                valuation_snapshot=None,
                lease=None,
                session_snapshot=session_before,
                failure_code=AccountBalanceSnapshotFailureCode.SESSION_UNAVAILABLE,
            )
        session_before_ready = _session_ready(session_before)

        try:
            receipt = self._query_facade.refresh(
                (request, valuation_request), deadline=deadline
            )
        except QueryFacadeError as error:
            failure_code = (
                AccountBalanceSnapshotFailureCode.RECEIPT_INVALID
                if error.code == "INVALID_PROVIDER_RECEIPT"
                else AccountBalanceSnapshotFailureCode.QUERY_FAILED
            )
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=None,
                query_snapshot=None,
                valuation_snapshot=None,
                lease=None,
                session_snapshot=session_before,
                failure_code=failure_code,
            )
        except (TypeError, ValueError):
            # Invalid consumer deadlines/arguments remain caller errors.
            raise
        except Exception:
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=None,
                query_snapshot=None,
                valuation_snapshot=None,
                lease=None,
                session_snapshot=session_before,
                failure_code=AccountBalanceSnapshotFailureCode.QUERY_FAILED,
            )

        try:
            if type(receipt) is not QueryBatchReceipt:
                raise TypeError("query receipt is not canonical")
            snapshot = self._query_facade.snapshot(receipt, request.request_ref)
            valuation_snapshot = self._query_facade.snapshot(
                receipt, valuation_request.request_ref
            )
        except Exception:
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=receipt,
                query_snapshot=None,
                valuation_snapshot=None,
                lease=None,
                session_snapshot=session_before,
                failure_code=AccountBalanceSnapshotFailureCode.RECEIPT_INVALID,
            )
        if type(snapshot) is not QuerySnapshot:
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=receipt,
                query_snapshot=None,
                valuation_snapshot=(
                    valuation_snapshot if type(valuation_snapshot) is QuerySnapshot else None
                ),
                lease=None,
                session_snapshot=session_before,
                failure_code=AccountBalanceSnapshotFailureCode.SNAPSHOT_MISSING,
            )
        if valuation_snapshot is not None and type(valuation_snapshot) is not QuerySnapshot:
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=receipt,
                query_snapshot=snapshot,
                valuation_snapshot=None,
                lease=None,
                session_snapshot=session_before,
                failure_code=AccountBalanceSnapshotFailureCode.RECEIPT_INVALID,
            )

        session_after = self._read_session_snapshot()
        if not _session_ready(session_after):
            failure_code = AccountBalanceSnapshotFailureCode.SESSION_UNAVAILABLE
        elif (
            session_before.source_ref != receipt.source_ref
            or session_before.generation != receipt.generation
            or snapshot.source_ref != receipt.source_ref
            or snapshot.generation != receipt.generation
            or session_after.source_ref != receipt.source_ref
            or session_after.generation != receipt.generation
            or snapshot.completed_at is None
            or session_after.observed_at is None
            or session_after.observed_at < snapshot.completed_at
        ):
            failure_code = AccountBalanceSnapshotFailureCode.SESSION_MISMATCH
        elif session_before_ready and not _same_session(session_before, session_after):
            failure_code = AccountBalanceSnapshotFailureCode.SESSION_MISMATCH
        else:
            failure_code = None

        candidate = AccountBalanceSnapshot(
            request=request,
            valuation_request=valuation_request,
            receipt=receipt,
            query_snapshot=snapshot,
            valuation_snapshot=valuation_snapshot,
            lease=None,
            session_snapshot=session_after,
            failure_code=failure_code,
        )
        if not candidate.complete or not candidate.session_bound:
            return candidate

        expires_at = datetime.now(_UTC) + _BALANCE_SNAPSHOT_LEASE_TTL
        try:
            lease = self._query_facade.acquire_snapshot_lease(
                receipt,
                purpose=ACCOUNT_BALANCE_LEASE_PURPOSE,
                expires_at=expires_at,
            )
        except Exception:
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=receipt,
                query_snapshot=snapshot,
                valuation_snapshot=valuation_snapshot,
                lease=None,
                session_snapshot=session_after,
                failure_code=AccountBalanceSnapshotFailureCode.LEASE_UNAVAILABLE,
            )

        latest_session = self._read_session_snapshot()
        if (
            not _same_session(session_after, latest_session)
            or latest_session is None
            or latest_session.observed_at is None
            or snapshot.completed_at is None
            or latest_session.observed_at < snapshot.completed_at
        ):
            return AccountBalanceSnapshot(
                request=request,
                valuation_request=valuation_request,
                receipt=receipt,
                query_snapshot=snapshot,
                valuation_snapshot=valuation_snapshot,
                lease=None,
                session_snapshot=latest_session,
                failure_code=(
                    AccountBalanceSnapshotFailureCode.SESSION_MISMATCH
                    if _session_ready(latest_session)
                    else AccountBalanceSnapshotFailureCode.SESSION_UNAVAILABLE
                ),
            )

        result = AccountBalanceSnapshot(
            request=request,
            valuation_request=valuation_request,
            receipt=receipt,
            query_snapshot=snapshot,
            valuation_snapshot=valuation_snapshot,
            lease=lease,
            session_snapshot=latest_session,
        )
        if result.usable:
            self._remember(result)
        return result

    def validate_snapshot(self, snapshot: AccountBalanceSnapshot) -> bool:
        """Revalidate only this facade's exact snapshot and provider lease."""

        if type(snapshot) is not AccountBalanceSnapshot or not snapshot.usable:
            return False
        with self._lock:
            if self._issued.get(id(snapshot)) is not snapshot:
                return False
        if self._query_facade is None or snapshot.receipt is None or snapshot.lease is None:
            return False
        current_session = self._read_session_snapshot()
        if (
            not _same_session(snapshot.session_snapshot, current_session)
            or current_session is None
            or current_session.observed_at is None
            or snapshot.query_snapshot is None
            or snapshot.query_snapshot.completed_at is None
            or current_session.observed_at < snapshot.query_snapshot.completed_at
        ):
            return False
        try:
            result = self._query_facade.validate_lease(
                snapshot.lease,
                snapshot.receipt,
                now=datetime.now(_UTC),
            )
        except Exception:
            return False
        if type(result) is not bool or result is not True:
            return False
        return _same_session(current_session, self._read_session_snapshot())

    def _read_session_snapshot(self) -> AccountSessionSnapshot | None:
        if self._session_port is None:
            return None
        try:
            snapshot = self._session_port.session_snapshot()
        except Exception:
            return None
        if (
            type(snapshot) is not AccountSessionSnapshot
            or snapshot.exchange_name != self._exchange_name
        ):
            return None
        return snapshot

    def _remember(self, snapshot: AccountBalanceSnapshot) -> None:
        with self._lock:
            key = id(snapshot)
            self._issued[key] = snapshot
            self._issued.move_to_end(key)
            while len(self._issued) > _MAX_ISSUED_SNAPSHOTS:
                self._issued.popitem(last=False)


__all__ = ["AccountBalanceSnapshotFacade"]


def _session_ready(snapshot: AccountSessionSnapshot | None) -> bool:
    return bool(
        type(snapshot) is AccountSessionSnapshot
        and snapshot.available is True
        and snapshot.connected is True
        and snapshot.authenticated is True
        and snapshot.source_ref
        and snapshot.generation
        and snapshot.session_ref
    )


def _same_session(
    expected: AccountSessionSnapshot | None,
    observed: AccountSessionSnapshot | None,
) -> bool:
    return bool(
        _session_ready(expected)
        and _session_ready(observed)
        and expected.exchange_name == observed.exchange_name
        and expected.source_ref == observed.source_ref
        and expected.generation == observed.generation
        and expected.session_ref == observed.session_ref
    )
