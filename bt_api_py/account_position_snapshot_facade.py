"""SDK-owned adapter for full-scope positions and session-bound leases."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from uuid import uuid4

from bt_api_base.account_position_snapshot_protocol import (
    ACCOUNT_POSITION_LEASE_PURPOSE,
    ACCOUNT_POSITION_QUERY_FAMILY,
    AccountPositionSnapshot,
    AccountPositionSnapshotFailureCode,
    AccountPositionSnapshotPort,
)
from bt_api_base.account_protocol import AccountPosition
from bt_api_base.account_session_protocol import AccountSessionPort, AccountSessionSnapshot
from bt_api_base.query_protocol import (
    QueryBatchReceipt,
    QueryRequest,
    QuerySnapshot,
    QuerySnapshotLease,
)

from .query_facade import QueryFacade, QueryFacadeError


_UTC = timezone.utc
_POSITION_SNAPSHOT_LEASE_TTL = timedelta(seconds=2)
_MAX_ISSUED_SNAPSHOTS = 64


class AccountPositionSnapshotFacade(AccountPositionSnapshotPort):
    """Full-scope positions port bound to one selected query and session route."""

    def __init__(
        self,
        exchange_name: str,
        query_facade: QueryFacade | None,
        session_port: AccountSessionPort | None,
        *,
        unavailable_code: AccountPositionSnapshotFailureCode | None = None,
    ) -> None:
        if query_facade is not None and type(query_facade) is not QueryFacade:
            raise TypeError("query_facade must be the exact SDK QueryFacade or None")
        if (
            type(exchange_name) is not str
            or not exchange_name
            or exchange_name != exchange_name.strip()
        ):
            raise ValueError("exchange_name must be a non-empty exact route name")
        if (
            query_facade is None
            and type(unavailable_code) is not AccountPositionSnapshotFailureCode
        ):
            raise TypeError("unavailable facade requires a finite failure code")
        if query_facade is not None and unavailable_code is not None:
            raise ValueError("available facade cannot also declare an unavailable code")
        if session_port is not None and not callable(
            getattr(session_port, "session_snapshot", None)
        ):
            raise TypeError("session_port must expose session_snapshot or be None")
        self._exchange_name = exchange_name
        self._query_facade = query_facade
        self._session_port = session_port
        self._unavailable_code = unavailable_code
        self._lock = RLock()
        self._issued: OrderedDict[int, tuple[AccountPositionSnapshot, str]] = OrderedDict()

    @classmethod
    def unavailable(
        cls,
        exchange_name: str,
        code: AccountPositionSnapshotFailureCode = (
            AccountPositionSnapshotFailureCode.ROUTE_UNAVAILABLE
        ),
    ) -> AccountPositionSnapshotFacade:
        """Create an explicit unavailable port without a legacy client fallback."""

        return cls(exchange_name, None, None, unavailable_code=code)

    def refresh(self, *, deadline: datetime) -> AccountPositionSnapshot:
        """Query every provider-declared account position before ``deadline``."""

        _require_utc(deadline, "deadline")
        request = QueryRequest(
            request_ref="account-positions:" + uuid4().hex,
            family=ACCOUNT_POSITION_QUERY_FAMILY,
            filters={},
            required=True,
            require_complete_scope=True,
        )
        if self._query_facade is None:
            return self._result(request, failure_code=self._unavailable_code)

        session_before = self._read_session_snapshot()
        if (
            session_before is None
            or session_before.source_ref is None
            or session_before.generation is None
            or not _session_ready(session_before, self._exchange_name)
        ):
            return self._result(
                request,
                session_snapshot=session_before,
                failure_code=AccountPositionSnapshotFailureCode.SESSION_UNAVAILABLE,
            )
        session_before_ready = _session_ready(session_before, self._exchange_name)

        try:
            receipt = self._query_facade.refresh((request,), deadline=deadline)
        except QueryFacadeError as error:
            code = (
                AccountPositionSnapshotFailureCode.RECEIPT_INVALID
                if error.code == "INVALID_PROVIDER_RECEIPT"
                else AccountPositionSnapshotFailureCode.QUERY_FAILED
            )
            return self._result(request, session_snapshot=session_before, failure_code=code)
        except (TypeError, ValueError):
            raise
        except Exception:
            return self._result(
                request,
                session_snapshot=session_before,
                failure_code=AccountPositionSnapshotFailureCode.QUERY_FAILED,
            )

        try:
            if type(receipt) is not QueryBatchReceipt:
                raise TypeError("query receipt is not canonical")
            snapshot = self._query_facade.snapshot(receipt, request.request_ref)
        except Exception:
            return self._result(
                request,
                receipt=receipt,
                session_snapshot=session_before,
                failure_code=AccountPositionSnapshotFailureCode.RECEIPT_INVALID,
            )
        if type(snapshot) is not QuerySnapshot:
            return self._result(
                request,
                receipt=receipt,
                session_snapshot=session_before,
                failure_code=AccountPositionSnapshotFailureCode.SNAPSHOT_MISSING,
            )

        session_after = self._read_session_snapshot()
        failure_code = self._check_session_binding(
            session_before,
            session_after,
            receipt,
            snapshot,
            session_before_ready=session_before_ready,
        )
        candidate = self._result(
            request,
            receipt=receipt,
            query_snapshot=snapshot,
            session_snapshot=session_after,
            failure_code=failure_code,
        )
        if not candidate.complete or not candidate.session_bound:
            return candidate

        try:
            lease = self._query_facade.acquire_snapshot_lease(
                receipt,
                purpose=ACCOUNT_POSITION_LEASE_PURPOSE,
                expires_at=datetime.now(_UTC) + _POSITION_SNAPSHOT_LEASE_TTL,
            )
        except Exception:
            return self._result(
                request,
                receipt=receipt,
                query_snapshot=snapshot,
                session_snapshot=session_after,
                failure_code=AccountPositionSnapshotFailureCode.LEASE_UNAVAILABLE,
            )

        latest_session = self._read_session_snapshot()
        if (
            not _same_session(session_after, latest_session, self._exchange_name)
            or latest_session is None
            or latest_session.observed_at is None
            or snapshot.completed_at is None
            or latest_session.observed_at < snapshot.completed_at
        ):
            return self._result(
                request,
                receipt=receipt,
                query_snapshot=snapshot,
                session_snapshot=latest_session,
                failure_code=(
                    AccountPositionSnapshotFailureCode.SESSION_MISMATCH
                    if _session_ready(latest_session, self._exchange_name)
                    else AccountPositionSnapshotFailureCode.SESSION_UNAVAILABLE
                ),
            )

        result = self._result(
            request,
            receipt=receipt,
            query_snapshot=snapshot,
            lease=lease,
            session_snapshot=latest_session,
        )
        if result.usable:
            self._remember(result)
        return result

    def validate_snapshot(self, snapshot: AccountPositionSnapshot) -> bool:
        """Revalidate only this facade's exact rows, session, and query lease."""

        if type(snapshot) is not AccountPositionSnapshot or not snapshot.usable:
            return False
        with self._lock:
            issued = self._issued.get(id(snapshot))
        if issued is None or issued[0] is not snapshot:
            return False
        try:
            if _snapshot_fingerprint(snapshot) != issued[1] or not _rows_match(snapshot):
                return False
        except Exception:
            return False
        if (
            self._query_facade is None
            or snapshot.receipt is None
            or snapshot.lease is None
            or snapshot.query_snapshot is None
        ):
            return False

        current_session = self._read_session_snapshot()
        if (
            not _same_session(snapshot.session_snapshot, current_session, self._exchange_name)
            or current_session is None
            or current_session.observed_at is None
            or snapshot.query_snapshot.completed_at is None
            or current_session.observed_at < snapshot.query_snapshot.completed_at
        ):
            return False
        try:
            current = self._query_facade.validate_lease(
                snapshot.lease,
                snapshot.receipt,
                now=datetime.now(_UTC),
            )
        except Exception:
            return False
        if type(current) is not bool or current is not True:
            return False
        if not _same_session(
            current_session,
            self._read_session_snapshot(),
            self._exchange_name,
        ):
            return False
        try:
            return _snapshot_fingerprint(snapshot) == issued[1] and _rows_match(snapshot)
        except Exception:
            return False

    def _check_session_binding(
        self,
        before: AccountSessionSnapshot,
        after: AccountSessionSnapshot | None,
        receipt: QueryBatchReceipt,
        snapshot: QuerySnapshot,
        *,
        session_before_ready: bool,
    ) -> AccountPositionSnapshotFailureCode | None:
        if not _session_ready(after, self._exchange_name):
            return AccountPositionSnapshotFailureCode.SESSION_UNAVAILABLE
        if (
            before.source_ref != receipt.source_ref
            or before.generation != receipt.generation
            or snapshot.source_ref != receipt.source_ref
            or snapshot.generation != receipt.generation
            or after.source_ref != receipt.source_ref
            or after.generation != receipt.generation
            or snapshot.completed_at is None
            or after.observed_at is None
            or after.observed_at < snapshot.completed_at
        ):
            return AccountPositionSnapshotFailureCode.SESSION_MISMATCH
        if session_before_ready and not _same_session(
            before, after, self._exchange_name
        ):
            return AccountPositionSnapshotFailureCode.SESSION_MISMATCH
        return None

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

    def _result(
        self,
        request: QueryRequest,
        *,
        receipt: QueryBatchReceipt | None = None,
        query_snapshot: QuerySnapshot | None = None,
        lease: QuerySnapshotLease | None = None,
        session_snapshot: AccountSessionSnapshot | None = None,
        failure_code: AccountPositionSnapshotFailureCode | None = None,
    ) -> AccountPositionSnapshot:
        return AccountPositionSnapshot(
            exchange_name=self._exchange_name,
            request=request,
            receipt=receipt,
            query_snapshot=query_snapshot,
            lease=lease,
            session_snapshot=session_snapshot,
            failure_code=failure_code,
        )

    def _remember(self, snapshot: AccountPositionSnapshot) -> None:
        fingerprint = _snapshot_fingerprint(snapshot)
        with self._lock:
            self._issued[id(snapshot)] = (snapshot, fingerprint)
            self._issued.move_to_end(id(snapshot))
            while len(self._issued) > _MAX_ISSUED_SNAPSHOTS:
                self._issued.popitem(last=False)


def _session_ready(
    snapshot: AccountSessionSnapshot | None,
    exchange_name: str,
) -> bool:
    return bool(
        type(snapshot) is AccountSessionSnapshot
        and snapshot.exchange_name == exchange_name
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
    exchange_name: str,
) -> bool:
    return bool(
        _session_ready(expected, exchange_name)
        and _session_ready(observed, exchange_name)
        and expected.source_ref == observed.source_ref
        and expected.generation == observed.generation
        and expected.session_ref == observed.session_ref
    )


def _rows_match(snapshot: AccountPositionSnapshot) -> bool:
    query_snapshot = snapshot.query_snapshot
    if query_snapshot is None:
        return False
    try:
        rows = tuple(AccountPosition.from_dict(row) for row in query_snapshot.records)
    except Exception:
        return False
    return rows == snapshot.positions


def _snapshot_fingerprint(snapshot: AccountPositionSnapshot) -> str:
    request = snapshot.request
    receipt = snapshot.receipt
    query_snapshot = snapshot.query_snapshot
    lease = snapshot.lease
    session = snapshot.session_snapshot
    payload = {
        "exchange_name": snapshot.exchange_name,
        "request": _request_data(request),
        "receipt": None
        if receipt is None
        else {
            "batch_ref": receipt.batch_ref,
            "source_ref": receipt.source_ref,
            "generation": receipt.generation,
            "started_at": receipt.started_at,
            "completed_at": receipt.completed_at,
            "requests": [_request_data(item) for item in receipt.requests],
            "snapshots": [_query_snapshot_data(item) for item in receipt.snapshots],
            "blockers": receipt.blockers,
        },
        "query_snapshot": None
        if query_snapshot is None
        else _query_snapshot_data(query_snapshot),
        "lease": None
        if lease is None
        else {
            "lease_ref": lease.lease_ref,
            "receipt_digest": lease.receipt_digest,
            "source_ref": lease.source_ref,
            "generation": lease.generation,
            "snapshot_refs": lease.snapshot_refs,
            "issued_at": lease.issued_at,
            "expires_at": lease.expires_at,
            "purpose": lease.purpose,
        },
        "session": None
        if session is None
        else {
            "exchange_name": session.exchange_name,
            "source_ref": session.source_ref,
            "generation": session.generation,
            "session_ref": session.session_ref,
            "connected": session.connected,
            "authenticated": session.authenticated,
            "observed_at": session.observed_at,
            "available": session.available,
            "blockers": session.blockers,
        },
        "positions": [position.to_dict() for position in snapshot.positions],
        "failure_code": None
        if snapshot.failure_code is None
        else snapshot.failure_code.value,
        "coverage_blockers": snapshot.coverage_blockers,
        "session_blockers": snapshot.session_blockers,
        "validation_blockers": snapshot.validation_blockers,
    }
    encoded = json.dumps(
        _json_ready(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def _query_snapshot_data(snapshot: QuerySnapshot) -> dict[str, object]:
    return {
        "request": _request_data(snapshot.request),
        "source_ref": snapshot.source_ref,
        "generation": snapshot.generation,
        "snapshot_ref": snapshot.snapshot_ref,
        "completeness": snapshot.completeness.value,
        "started_at": snapshot.started_at,
        "completed_at": snapshot.completed_at,
        "observed_at": snapshot.observed_at,
        "scope_complete": snapshot.scope_complete,
        "records": snapshot.records,
        "records_digest": snapshot.records_digest,
        "blockers": snapshot.blockers,
        "continuation_ref": snapshot.continuation_ref,
    }


def _request_data(request: QueryRequest) -> dict[str, object]:
    return {
        "request_ref": request.request_ref,
        "family": request.family,
        "filters": request.filters,
        "required": request.required,
        "require_complete_scope": request.require_complete_scope,
    }


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _require_utc(value: object, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


__all__ = ["AccountPositionSnapshotFacade"]
