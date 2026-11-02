"""Read a currently validated neutral account-position snapshot from the SDK.

This adapter owns only the SDK read boundary: port selection,
bounded refresh, snapshot shape checks, and SDK lease/currentness validation.
Feed identity, instrument routing, quantity-unit mapping, and framework row
projection remain with the caller.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable, Tuple, Type


_UTC = _dt.timezone.utc


@dataclass(frozen=True)
class SdkPositionRead:
    """Minimal neutral position data released after SDK snapshot validation.

    The SDK receipt, session snapshot, lease, query snapshot, and port are kept
    inside the adapter. The caller receives only the exact selected route, typed
    neutral rows, the SDK's empty-result proof, and DTO classes needed for its
    caller projection.
    """

    exchange_name: str
    positions: Tuple[Any, ...]
    empty_result_proven: bool
    account_position_type: Type[Any]
    market_instrument_key_type: Type[Any]


@dataclass(frozen=True)
class SdkPositionReadBatch:
    """Independently validated route results in configured route order."""

    routes: Tuple[SdkPositionRead, ...]


def validate_position_read_batch(batch: Any, *, unavailable: Callable[[str], None]) -> None:
    """Validate the neutral batch envelope before framework projection."""
    if (
        type(batch) is not SdkPositionReadBatch
        or type(batch.routes) is not tuple
        or not batch.routes
        or any(type(route_read) is not SdkPositionRead for route_read in batch.routes)
    ):
        unavailable("account_position_snapshot_batch_invalid")


def position_read_has_positions(read: SdkPositionRead, *, unavailable: Callable[[str], None]) -> bool:
    """Check the route's explicit empty-result evidence before projecting rows."""
    if not read.positions:
        if read.empty_result_proven is not True:
            unavailable("account_position_snapshot_empty_unproven")
        return False
    if read.empty_result_proven is not False:
        unavailable("account_position_snapshot_invalid")
    return True


def validate_position_read_row(
    row: Any,
    *,
    account_position_type: Type[Any],
    market_instrument_key_type: Type[Any],
    unavailable: Callable[[str], None],
) -> None:
    """Validate neutral position and instrument types before framework binding."""
    if (
        type(row) is not account_position_type
        or type(row.instrument) is not market_instrument_key_type
    ):
        unavailable("account_position_snapshot_record_invalid")
    if row.side not in {"net", "long", "short"}:
        unavailable("account_position_snapshot_direction_ambiguous")


class SdkPositionReadAdapter:
    """Validate one complete SDK position snapshot before exposing its rows."""

    def __init__(
        self,
        *,
        error_type: Type[Exception],
        missing_dependency_error_type: Type[Exception],
    ) -> None:
        self._error_type = error_type
        self._missing_dependency_error_type = missing_dependency_error_type

    @staticmethod
    def _raise_unavailable(error_type: Type[Exception], code: str) -> None:
        error = error_type("SDK account position snapshot is unavailable")
        error.code = code
        raise error from None

    def read(
        self,
        configured_routes: Tuple[str, ...],
        *,
        ensure_api_ready: Callable[[], Any],
        resolve_route_name: Callable[[Any, str], str],
        api_is_current: Callable[[Any], bool],
    ) -> SdkPositionReadBatch:
        """Read and validate every explicitly configured route independently.

        Caller callbacks retain API lifecycle and exact route resolution. The
        adapter never selects or unwraps a provider client, and does not expose
        the SDK port or its source/session/receipt/lease evidence to the caller. A
        route failure aborts the whole call; no partial batch is returned.
        """

        try:
            from bt_api_py.account_position_contracts import (
                AccountPosition,
                AccountPositionSnapshot,
                AccountPositionSnapshotFailureCode,
                AccountPositionSnapshotPort,
                AccountSessionSnapshot,
                QueryBatchReceipt,
                QuerySnapshot,
                QuerySnapshotLease,
            )
            from bt_api_py.facade_protocol import (
                AccountPositionSnapshotSelector,
                MarketInstrumentKey,
            )
        except ImportError as exc:
            raise self._missing_dependency_error_type(
                "bt_api_py account-position snapshot contracts are required"
            ) from exc

        def unavailable(code: str) -> None:
            self._raise_unavailable(self._error_type, code)

        if not configured_routes or any(
            type(route) is not str or not route or route != route.strip()
            for route in configured_routes
        ):
            unavailable("account_position_snapshot_route_unavailable")
        if len(set(configured_routes)) != len(configured_routes):
            unavailable("account_position_snapshot_route_unavailable")

        api = ensure_api_ready()
        retained = []

        for configured_route in configured_routes:
            try:
                route = resolve_route_name(api, configured_route)
            except Exception:
                unavailable("account_position_snapshot_route_unavailable")

            if not isinstance(api, AccountPositionSnapshotSelector):
                unavailable("account_position_snapshot_capability_unavailable")
            selector = getattr(api, "account_position_snapshot", None)
            if not callable(selector):
                unavailable("account_position_snapshot_capability_unavailable")
            try:
                port = selector(route)
            except Exception:
                unavailable("account_position_snapshot_route_unavailable")
            if not isinstance(port, AccountPositionSnapshotPort):
                unavailable("account_position_snapshot_port_invalid")

            deadline = _dt.datetime.now(_UTC) + _dt.timedelta(seconds=5.0)
            try:
                snapshot = port.refresh(deadline=deadline)
            except Exception:
                unavailable("account_position_snapshot_query_unavailable")
            if api_is_current(api) is not True:
                unavailable("account_position_snapshot_api_changed")
            if type(snapshot) is not AccountPositionSnapshot:
                unavailable("account_position_snapshot_invalid")
            if snapshot.exchange_name != route:
                unavailable("account_position_snapshot_route_mismatch")
            if snapshot.failure_code is not None:
                failure_code = snapshot.failure_code
                if type(failure_code) is AccountPositionSnapshotFailureCode:
                    unavailable(failure_code.value)
                unavailable("account_position_snapshot_invalid")
            if (
                snapshot.complete is not True
                or snapshot.scope_complete is not True
                or snapshot.session_bound is not True
                or snapshot.usable is not True
                or type(snapshot.session_snapshot) is not AccountSessionSnapshot
                or type(snapshot.lease) is not QuerySnapshotLease
                or type(snapshot.receipt) is not QueryBatchReceipt
                or type(snapshot.query_snapshot) is not QuerySnapshot
                or type(snapshot.positions) is not tuple
            ):
                unavailable("account_position_snapshot_incomplete")
            try:
                resolved_route = resolve_route_name(api, route)
            except Exception:
                unavailable("account_position_snapshot_route_changed")
            if resolved_route != route:
                unavailable("account_position_snapshot_route_changed")
            try:
                valid = port.validate_snapshot(snapshot)
            except Exception:
                unavailable("account_position_snapshot_validation_unavailable")
            if (
                type(valid) is not bool
                or valid is not True
                or api_is_current(api) is not True
            ):
                unavailable("account_position_snapshot_not_current")

            if not snapshot.positions:
                if snapshot.empty_result_proven is not True:
                    unavailable("account_position_snapshot_empty_unproven")
            elif snapshot.empty_result_proven is not False:
                unavailable("account_position_snapshot_invalid")

            retained.append((route, port, snapshot))

        # Earlier route leases may age while later routes are read. Recheck all
        # exact route/port pairs at the batch release boundary, and expose rows
        # only if every SDK lease is still current.
        for route, port, snapshot in retained:
            if api_is_current(api) is not True:
                unavailable("account_position_snapshot_not_current")
            try:
                resolved_route = resolve_route_name(api, route)
            except Exception:
                unavailable("account_position_snapshot_route_changed")
            if resolved_route != route:
                unavailable("account_position_snapshot_route_changed")
            try:
                valid = port.validate_snapshot(snapshot)
            except Exception:
                unavailable("account_position_snapshot_validation_unavailable")
            if (
                type(valid) is not bool
                or valid is not True
                or api_is_current(api) is not True
            ):
                unavailable("account_position_snapshot_not_current")

        return SdkPositionReadBatch(
            routes=tuple(
                SdkPositionRead(
                    exchange_name=route,
                    positions=snapshot.positions,
                    empty_result_proven=snapshot.empty_result_proven,
                    account_position_type=AccountPosition,
                    market_instrument_key_type=MarketInstrumentKey,
                )
                for route, _port, snapshot in retained
            )
        )


__all__ = (
    "SdkPositionRead", "SdkPositionReadBatch", "SdkPositionReadAdapter",
    "validate_position_read_batch", "position_read_has_positions", "validate_position_read_row",
)
