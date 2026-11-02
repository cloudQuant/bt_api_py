"""Read and revalidate one complete provider account query batch.

This module consumes an existing ``QueryFacade`` receipt and lease.  It does
not issue a query, acquire a lease, read a legacy account mapping, or assign
Backtrader Strategy ownership to provider rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final

from bt_api_base.account_execution_snapshot_protocol import AccountExecutionSnapshot
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountOpenOrder,
    AccountPosition,
)
from bt_api_base.account_session_protocol import AccountSessionSnapshot
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.query_protocol import (
    QueryBatchReceipt,
    QueryRequest,
    QuerySnapshot,
    QuerySnapshotLease,
)

from .query_facade import QueryFacade


_UTC: Final = timezone.utc
_FAMILIES: Final = frozenset({"account", "positions", "open_orders", "instruments"})
_ERROR_CODES: Final = frozenset(
    {
        "QUERY_FACADE_UNAVAILABLE",
        "QUERY_RECEIPT_INVALID",
        "QUERY_REQUEST_SET_INVALID",
        "QUERY_REQUEST_SCOPE_INVALID",
        "QUERY_SNAPSHOT_INVALID",
        "QUERY_SNAPSHOT_INCOMPLETE",
        "ACCOUNT_SESSION_UNAVAILABLE",
        "ACCOUNT_SESSION_RECEIPT_MISMATCH",
        "ACCOUNT_SESSION_ROUTE_BINDING_UNAVAILABLE",
        "QUERY_SNAPSHOT_LEASE_INVALID",
        "QUERY_SNAPSHOT_LEASE_STALE",
        "ACCOUNT_EXECUTION_ROWS_INVALID",
        "ACCOUNT_EXECUTION_SNAPSHOT_INVALID",
        "ACCOUNT_EXECUTION_SNAPSHOT_SCOPE_MISMATCH",
        "VALIDATION_TIME_INVALID",
    }
)


class AccountExecutionSnapshotError(RuntimeError):
    """A finite failure while validating a typed account query snapshot."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError("code is not a published account execution snapshot error")
        self.code = code
        super().__init__(code)


def read_account_execution_snapshot(
    *,
    query_facade: QueryFacade,
    receipt: QueryBatchReceipt,
    lease: QuerySnapshotLease,
    account_session_snapshot: AccountSessionSnapshot,
    now: datetime,
) -> AccountExecutionSnapshot:
    """Project one already-collected complete batch into exact neutral rows.

    ``now`` is a caller time-order assertion only.  Lease currentness is
    evaluated with the SDK process's UTC clock, both before and after DTO
    projection, so passing an old timestamp cannot extend an expired lease.
    The provider lease remains the freshness authority; no independent age or
    readiness claim is introduced here.
    """

    if type(query_facade) is not QueryFacade:
        raise AccountExecutionSnapshotError("QUERY_FACADE_UNAVAILABLE")
    _require_exact_inputs(receipt, lease, account_session_snapshot)
    hint = _time_hint(now)
    trusted_now = datetime.now(_UTC)
    if hint > trusted_now:
        raise AccountExecutionSnapshotError("VALIDATION_TIME_INVALID")

    _validate_batch_and_session(receipt, lease, account_session_snapshot, trusted_now)
    _validate_account_session_route_binding(
        query_facade,
        account_session_snapshot,
        receipt=receipt,
        lease=lease,
    )
    _validate_lease(query_facade, lease, receipt, trusted_now)
    rows = _project_rows(query_facade, receipt, trusted_now)
    _validate_lease(query_facade, lease, receipt, datetime.now(_UTC))
    _validate_account_session_route_binding(
        query_facade,
        account_session_snapshot,
        receipt=receipt,
        lease=lease,
    )
    validated_at = datetime.now(_UTC)
    if not _validated_at_covers_evidence(
        receipt,
        account_session_snapshot,
        rows,
        validated_at,
    ):
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_SNAPSHOT_INVALID")
    try:
        return AccountExecutionSnapshot(
            receipt=receipt,
            lease=lease,
            account_session_snapshot=account_session_snapshot,
            validated_at=validated_at,
            account=rows["account"],
            positions=rows["positions"],
            open_orders=rows["open_orders"],
            instruments=rows["instruments"],
        )
    except Exception:
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_SNAPSHOT_INVALID") from None


def require_account_execution_snapshot_scope(
    snapshot, *, receipt, lease, account_session_snapshot,
) -> None:
    """Require an exact snapshot and its caller-retained evidence identities.

    This checks bindings and row container shape only; it does not revalidate
    the provider lease or establish current account state.
    """
    if type(snapshot) is not AccountExecutionSnapshot:
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_SNAPSHOT_INVALID")
    if (
        snapshot.receipt is not receipt
        or snapshot.lease is not lease
        or snapshot.account_session_snapshot is not account_session_snapshot
    ):
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_SNAPSHOT_SCOPE_MISMATCH")
    if any(
        type(rows) is not tuple
        for rows in (snapshot.account, snapshot.positions, snapshot.open_orders, snapshot.instruments)
    ):
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_SNAPSHOT_INVALID")


def target_instrument_coverage_blockers(target_instruments, instrument_rows):
    """Identify targets missing exactly one matching neutral instrument row.

    The caller supplies already validated ``(target_id, instrument)`` pairs.
    This comparison does not establish provider authenticity or authorization.
    """
    target_by_instrument = {instrument: target_id for target_id, instrument in target_instruments}
    instrument_counts = {target_id: 0 for target_id, _instrument in target_instruments}
    for instrument in instrument_rows:
        target_id = target_by_instrument.get(instrument)
        if target_id is not None:
            instrument_counts[target_id] += 1
    return [
        "target_instrument_coverage_invalid:" + target_id
        for target_id, count in instrument_counts.items()
        if count != 1
    ]


def validate_account_execution_snapshot(
    *,
    query_facade: QueryFacade,
    snapshot: AccountExecutionSnapshot,
    now: datetime,
) -> bool:
    """Revalidate exact rows and provider lease using the SDK's current clock.

    ``now`` cannot be used to backdate lease validation.  It is checked only
    as an aware-UTC, non-future assertion.  Provider lease validation always
    receives a fresh SDK UTC timestamp, regardless of the caller's value.
    """

    if type(query_facade) is not QueryFacade or type(snapshot) is not AccountExecutionSnapshot:
        return False
    try:
        hint = _time_hint(now)
        trusted_now = datetime.now(_UTC)
        if hint > trusted_now:
            return False
        _require_exact_inputs(snapshot.receipt, snapshot.lease, snapshot.account_session_snapshot)
        _validate_batch_and_session(
            snapshot.receipt,
            snapshot.lease,
            snapshot.account_session_snapshot,
            trusted_now,
        )
        _validate_account_session_route_binding(
            query_facade,
            snapshot.account_session_snapshot,
            receipt=snapshot.receipt,
            lease=snapshot.lease,
        )
        if not _validated_at_covers_evidence(
            snapshot.receipt,
            snapshot.account_session_snapshot,
            {
                "account": snapshot.account,
                "positions": snapshot.positions,
                "open_orders": snapshot.open_orders,
                "instruments": snapshot.instruments,
            },
            snapshot.validated_at,
        ) or snapshot.validated_at > trusted_now:
            return False
        rows = _project_rows(query_facade, snapshot.receipt, trusted_now)
        if (
            rows["account"] != snapshot.account
            or rows["positions"] != snapshot.positions
            or rows["open_orders"] != snapshot.open_orders
            or rows["instruments"] != snapshot.instruments
        ):
            return False
        _validate_account_session_route_binding(
            query_facade,
            snapshot.account_session_snapshot,
            receipt=snapshot.receipt,
            lease=snapshot.lease,
        )
        _validate_lease(query_facade, snapshot.lease, snapshot.receipt, datetime.now(_UTC))
        return True
    except AccountExecutionSnapshotError:
        return False
    except Exception:
        return False


def validate_account_execution_snapshot_binding(
    *,
    query_facade: QueryFacade,
    snapshot: object,
    receipt: object,
    lease: object,
    account_session_snapshot: object,
    account: object,
    positions: object,
    open_orders: object,
    instruments: object,
    now: datetime,
) -> bool:
    """Revalidate a consumer's exact references to one SDK snapshot.

    Object identity is a local binding check, not proof of provider authority;
    the SDK's existing route, lease and source validation remains mandatory.
    """
    if type(snapshot) is not AccountExecutionSnapshot:
        return False
    if (
        receipt is not snapshot.receipt
        or lease is not snapshot.lease
        or account_session_snapshot is not snapshot.account_session_snapshot
        or account is not snapshot.account
        or positions is not snapshot.positions
        or open_orders is not snapshot.open_orders
        or instruments is not snapshot.instruments
    ):
        return False
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() != timedelta(0):
        return False
    try:
        valid = validate_account_execution_snapshot(
            query_facade=query_facade, snapshot=snapshot, now=now
        )
        return type(valid) is bool and valid is True
    except Exception:
        return False


def _require_exact_inputs(
    receipt: object,
    lease: object,
    account_session_snapshot: object,
) -> None:
    if type(receipt) is not QueryBatchReceipt:
        raise AccountExecutionSnapshotError("QUERY_RECEIPT_INVALID")
    if type(lease) is not QuerySnapshotLease:
        raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_LEASE_INVALID")
    if type(account_session_snapshot) is not AccountSessionSnapshot:
        raise AccountExecutionSnapshotError("ACCOUNT_SESSION_UNAVAILABLE")


def _validate_batch_and_session(
    receipt: QueryBatchReceipt,
    lease: QuerySnapshotLease,
    session: AccountSessionSnapshot,
    now: datetime,
) -> None:
    if (
        not receipt.source_ref
        or not receipt.generation
        or receipt.completed_at > now
        or receipt.started_at > receipt.completed_at
    ):
        raise AccountExecutionSnapshotError("QUERY_RECEIPT_INVALID")
    if receipt.complete is not True:
        raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_INCOMPLETE")

    if (
        session.available is not True
        or session.connected is not True
        or session.authenticated is not True
        or not session.session_ref
        or not session.source_ref
        or not session.generation
    ):
        raise AccountExecutionSnapshotError("ACCOUNT_SESSION_UNAVAILABLE")
    receipt_session_ref = getattr(receipt, "session_ref", None)
    if type(receipt_session_ref) is not str or not receipt_session_ref:
        raise AccountExecutionSnapshotError(
            "ACCOUNT_SESSION_ROUTE_BINDING_UNAVAILABLE"
        )
    if (
        session.source_ref != receipt.source_ref
        or session.generation != receipt.generation
        or session.session_ref != receipt_session_ref
        or session.observed_at is None
        or session.observed_at < receipt.completed_at
        or session.observed_at > now
    ):
        raise AccountExecutionSnapshotError("ACCOUNT_SESSION_RECEIPT_MISMATCH")

    expected_families: dict[str, QueryRequest] = {}
    for request in receipt.requests:
        if (
            request.family not in _FAMILIES
            or request.filters
            or request.required is not True
            or request.require_complete_scope is not True
            or request.family in expected_families
        ):
            raise AccountExecutionSnapshotError("QUERY_REQUEST_SCOPE_INVALID")
        expected_families[request.family] = request
    if set(expected_families) != _FAMILIES or len(receipt.requests) != len(_FAMILIES):
        raise AccountExecutionSnapshotError("QUERY_REQUEST_SET_INVALID")
    if len(receipt.snapshots) != len(_FAMILIES) or len(receipt.complete_snapshot_refs) != len(
        _FAMILIES
    ):
        raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_INCOMPLETE")

    if (
        lease.receipt_digest != receipt.receipt_digest
        or lease.source_ref != receipt.source_ref
        or lease.generation != receipt.generation
        or lease.snapshot_refs != receipt.complete_snapshot_refs
    ):
        raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_LEASE_INVALID")


def _project_rows(
    query_facade: QueryFacade,
    receipt: QueryBatchReceipt,
    now: datetime,
) -> dict[str, tuple]:
    by_ref = {snapshot.request.request_ref: snapshot for snapshot in receipt.snapshots}
    result: dict[str, tuple] = {}
    row_types = {
        "account": AccountBalance,
        "positions": AccountPosition,
        "open_orders": AccountOpenOrder,
        "instruments": MarketInstrumentKey,
    }
    for family in ("account", "positions", "open_orders", "instruments"):
        request = next(item for item in receipt.requests if item.family == family)
        try:
            snapshot = query_facade.snapshot(receipt, request.request_ref)
        except Exception:
            raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_INVALID") from None
        if (
            type(snapshot) is not QuerySnapshot
            or by_ref.get(request.request_ref) is not snapshot
            or snapshot.request != request
        ):
            raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_INVALID")
        if (
            snapshot.is_complete is not True
            or snapshot.scope_complete is not True
            or snapshot.source_ref != receipt.source_ref
            or snapshot.generation != receipt.generation
            or snapshot.snapshot_ref not in receipt.complete_snapshot_refs
            or snapshot.started_at is None
            or snapshot.completed_at is None
            or snapshot.observed_at is None
            or snapshot.started_at < receipt.started_at
            or snapshot.completed_at > receipt.completed_at
            or snapshot.observed_at < snapshot.completed_at
            or snapshot.observed_at > now
        ):
            raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_INCOMPLETE")
        projected = []
        row_type = row_types[family]
        for raw in snapshot.records:
            try:
                row = row_type.from_dict(raw)
            except Exception:
                raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_ROWS_INVALID") from None
            observed_at = getattr(row, "observed_at_utc", None)
            if observed_at is not None and _parse_observed_at(observed_at) > now:
                raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_ROWS_INVALID")
            projected.append(row)
        result[family] = tuple(projected)
    return result


def _validate_lease(
    query_facade: QueryFacade,
    lease: QuerySnapshotLease,
    receipt: QueryBatchReceipt,
    now: datetime,
) -> None:
    try:
        current = query_facade.validate_lease(lease, receipt, now=now)
    except Exception:
        raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_LEASE_STALE") from None
    if type(current) is not bool or current is not True:
        raise AccountExecutionSnapshotError("QUERY_SNAPSHOT_LEASE_STALE")


def _validate_account_session_route_binding(
    query_facade: QueryFacade,
    session: AccountSessionSnapshot,
    *,
    receipt: QueryBatchReceipt,
    lease: QuerySnapshotLease,
) -> None:
    """Require the SDK-issued query facade to validate this exact session DTO."""

    try:
        valid = query_facade.validate_account_session_snapshot(
            session,
            receipt=receipt,
            lease=lease,
        )
    except Exception:
        valid = False
    if type(valid) is not bool or valid is not True:
        raise AccountExecutionSnapshotError(
            "ACCOUNT_SESSION_ROUTE_BINDING_UNAVAILABLE"
        )


def _validated_at_covers_evidence(
    receipt: QueryBatchReceipt,
    session: AccountSessionSnapshot,
    rows: dict[str, tuple],
    validated_at: datetime,
) -> bool:
    evidence_times = [receipt.completed_at]
    if session.observed_at is None:
        return False
    evidence_times.append(session.observed_at)
    evidence_times.extend(
        snapshot.observed_at for snapshot in receipt.snapshots if snapshot.observed_at is not None
    )
    for family_rows in rows.values():
        for row in family_rows:
            observed_at = getattr(row, "observed_at_utc", None)
            if observed_at is not None:
                try:
                    evidence_times.append(_parse_observed_at(observed_at))
                except AccountExecutionSnapshotError:
                    return False
    return all(value <= validated_at for value in evidence_times)


def _time_hint(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AccountExecutionSnapshotError("VALIDATION_TIME_INVALID")
    return value


def _parse_observed_at(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_ROWS_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_ROWS_INVALID") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AccountExecutionSnapshotError("ACCOUNT_EXECUTION_ROWS_INVALID")
    return parsed


__all__ = [
    "AccountExecutionSnapshot",
    "AccountExecutionSnapshotError",
    "read_account_execution_snapshot",
    "require_account_execution_snapshot_scope",
    "target_instrument_coverage_blockers",
    "validate_account_execution_snapshot",
    "validate_account_execution_snapshot_binding",
]
