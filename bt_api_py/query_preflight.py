"""Provider-neutral bounded account/query preflight over SDK contracts.

The result is a projection of supplied provider evidence and grants no order
authority. Callers choose the request namespace and lease purpose explicitly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


class ReadOnlyPreflightError(RuntimeError):
    """Finite value-free preflight failure."""

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code.isascii() and code.replace("_", "").isalnum() else "READONLY_PREFLIGHT_FAILED"
        super().__init__(self.code)


def collect_readonly_preflight(
    api: Any,
    configuration: Any,
    *,
    timeout_seconds: float = 15.0,
    lease_seconds: float = 10.0,
    request_ref_prefix: str,
    lease_purpose: str,
) -> dict[str, Any]:
    """Collect exact neutral account/position/order/instrument snapshots.

    The provider facade performs the reads and issues the lease. This function
    checks one complete, single-source batch and emits only bounded counts and
    booleans; it never returns account values or raw provider records.
    """

    from bt_api_py.facade_protocol import AccountSessionPort, AccountSessionSnapshot
    from bt_api_py.query_contracts import (
        MarketInstrumentKey,
        QueryBatchReceipt,
        QueryRequest,
        QuerySnapshotLease,
    )
    from bt_api_py.query_facade import QueryFacade

    if (
        isinstance(timeout_seconds, bool)
        or type(timeout_seconds) not in (int, float)
        or not 0 < float(timeout_seconds) <= 30.0
        or isinstance(lease_seconds, bool)
        or type(lease_seconds) not in (int, float)
        or not 0 < float(lease_seconds) <= 30.0
    ):
        raise ReadOnlyPreflightError("QUERY_PREFLIGHT_BOUNDS_INVALID")

    route = getattr(configuration, "exchange_name", None)
    instrument = getattr(configuration, "instrument", None)
    if type(route) is not str or type(instrument) is not MarketInstrumentKey:
        raise ReadOnlyPreflightError("PROVIDER_CONFIGURATION_RESULT_INVALID")
    routes = getattr(api, "list_exchanges", None)
    selector = getattr(api, "query_facade", None)
    session_selector = getattr(api, "account_session", None)
    if not callable(routes) or not callable(selector) or not callable(session_selector):
        raise ReadOnlyPreflightError("QUERY_FACADE_UNAVAILABLE")
    try:
        selected_routes = routes()
        if type(selected_routes) is not list or selected_routes != [route]:
            raise ReadOnlyPreflightError("MANAGED_RUNTIME_ROUTE_MISMATCH")
        query = selector(route)
        session_port = session_selector(route)
    except ReadOnlyPreflightError:
        raise
    except Exception as exc:  # noqa: BLE001 - safe SDK finite codes only
        code = getattr(exc, "code", None)
        if type(code) is str and code.isascii() and code.replace("_", "").isalnum():
            raise ReadOnlyPreflightError(code) from None
        raise ReadOnlyPreflightError("QUERY_FACADE_UNAVAILABLE") from None
    if not isinstance(session_port, AccountSessionPort):
        raise ReadOnlyPreflightError("ACCOUNT_SESSION_FACADE_UNAVAILABLE")
    if type(query) is not QueryFacade:
        raise ReadOnlyPreflightError("QUERY_FACADE_UNAVAILABLE")

    session_before = session_port.session_snapshot()
    if (
        type(session_before) is not AccountSessionSnapshot
        or session_before.exchange_name != route
        or session_before.available is not True
        or type(session_before.source_ref) is not str
        or not session_before.source_ref
        or type(session_before.generation) is not str
        or not session_before.generation
        or type(session_before.session_ref) is not str
        or not session_before.session_ref
    ):
        raise ReadOnlyPreflightError("ACCOUNT_SESSION_SNAPSHOT_UNAVAILABLE")

    requests = (
        QueryRequest(
            request_ref=request_ref_prefix + uuid.uuid4().hex,
            family="account",
            filters={},
            required=True,
            require_complete_scope=True,
        ),
        QueryRequest(
            request_ref=request_ref_prefix + uuid.uuid4().hex,
            family="positions",
            filters={},
            required=True,
            require_complete_scope=True,
        ),
        QueryRequest(
            request_ref=request_ref_prefix + uuid.uuid4().hex,
            family="open_orders",
            filters={},
            required=True,
            require_complete_scope=True,
        ),
        QueryRequest(
            request_ref=request_ref_prefix + uuid.uuid4().hex,
            family="instruments",
            filters={"symbol": instrument.native_symbol},
            required=True,
            require_complete_scope=True,
        ),
    )
    started = datetime.now(timezone.utc)
    deadline = started + timedelta(seconds=float(timeout_seconds))
    try:
        receipt = query.refresh(requests, deadline=deadline)
    except Exception as exc:  # noqa: BLE001 - safe SDK finite codes only
        code = getattr(exc, "code", None)
        if type(code) is str and code.isascii() and code.replace("_", "").isalnum():
            raise ReadOnlyPreflightError(code) from None
        raise ReadOnlyPreflightError("QUERY_REFRESH_FAILED") from None

    if (
        type(receipt) is not QueryBatchReceipt
        or receipt.requests != requests
        or type(receipt.source_ref) is not str
        or not receipt.source_ref
        or type(receipt.generation) is not str
        or not receipt.generation
        or receipt.complete is not True
        or receipt.started_at < started
        or receipt.completed_at > deadline
    ):
        raise ReadOnlyPreflightError("QUERY_RECEIPT_INCOMPLETE")
    snapshots: dict[str, Any] = {}
    for request in requests:
        snapshot = query.snapshot(receipt, request.request_ref)
        if (
            snapshot is None
            or not any(item is snapshot for item in receipt.snapshots)
            or snapshot.request != request
            or snapshot.is_complete is not True
            or snapshot.scope_complete is not True
            or snapshot.source_ref != receipt.source_ref
            or snapshot.generation != receipt.generation
            or type(snapshot.snapshot_ref) is not str
            or not snapshot.snapshot_ref
        ):
            raise ReadOnlyPreflightError("QUERY_SNAPSHOT_INCOMPLETE")
        snapshots[request.family] = snapshot

    lease_expires = datetime.now(timezone.utc) + timedelta(seconds=float(lease_seconds))
    try:
        lease = query.acquire_snapshot_lease(
            receipt,
            purpose=lease_purpose,
            expires_at=lease_expires,
        )
        if (
            type(lease) is not QuerySnapshotLease
            or query.validate_lease(lease, receipt, now=datetime.now(timezone.utc)) is not True
        ):
            raise ReadOnlyPreflightError("QUERY_LEASE_INVALID")
        instrument_rows = snapshots["instruments"].records
        exact_instrument = tuple(
            MarketInstrumentKey.from_dict(row) for row in instrument_rows
        )
        instrument_match = exact_instrument == (instrument,)
        account_count = len(snapshots["account"].records)
        positions_empty = not snapshots["positions"].records
        open_orders_empty = not snapshots["open_orders"].records
        session_after = session_port.session_snapshot()
        if (
            type(session_after) is not AccountSessionSnapshot
            or session_after.exchange_name != route
        ):
            raise ReadOnlyPreflightError("ACCOUNT_SESSION_SNAPSHOT_UNAVAILABLE")
        session_identity_stable = (
            session_before.source_ref == receipt.source_ref
            and session_before.generation == receipt.generation
            and session_after.available is True
            and session_after.source_ref == receipt.source_ref
            and session_after.generation == receipt.generation
            and session_after.session_ref == session_before.session_ref
        )
        session_observation_current = (
            type(session_after) is AccountSessionSnapshot
            and type(session_after.observed_at) is datetime
            and session_after.observed_at.tzinfo is not None
            and session_after.observed_at.utcoffset() == timedelta(0)
            and started <= session_after.observed_at <= deadline
        )
        if query.validate_lease(lease, receipt, now=datetime.now(timezone.utc)) is not True:
            raise ReadOnlyPreflightError("QUERY_LEASE_INVALID")
    except ReadOnlyPreflightError:
        raise
    except Exception as exc:  # noqa: BLE001 - no rows or provider detail
        code = getattr(exc, "code", None)
        if type(code) is str and code.isascii() and code.replace("_", "").isalnum():
            raise ReadOnlyPreflightError(code) from None
        raise ReadOnlyPreflightError("QUERY_SNAPSHOT_VALIDATION_FAILED") from None

    return {
        "schema_version": 1,
        "complete": True,
        "source_bound": True,
        "generation_bound": True,
        "lease_validated_before_and_after_projection": True,
        "account_session": {
            "available": session_after.available is True,
            "connected": session_after.connected,
            "authenticated": session_after.authenticated,
            "trading_enabled": session_after.trading_enabled,
            "settlement_status": session_after.settlement_status,
            "identity_stable_and_receipt_bound": session_identity_stable,
            "observation_within_preflight_window": session_observation_current,
        },
        "families": {
            family: {
                "complete": True,
                "scope_complete": True,
                "record_count": len(snapshot.records),
            }
            for family, snapshot in snapshots.items()
        },
        "account_rows": account_count,
        "positions_empty": positions_empty,
        "open_orders_empty": open_orders_empty,
        "instrument_match": instrument_match,
        "flat_and_instrument_ready": bool(
            account_count > 0
            and positions_empty
            and open_orders_empty
            and instrument_match
            and session_identity_stable
            and session_observation_current
            and session_after.connected is True
            and session_after.authenticated is True
            and session_after.trading_enabled is True
            and session_after.settlement_status in {"confirmed", "not_required"}
        ),
    }


__all__ = ("ReadOnlyPreflightError", "collect_readonly_preflight")
