"""Cache validated, route-bound SDK account-balance snapshots.

The calling owner controls lifecycle and account-level policy. This adapter
owns one route-keyed cache of SDK ports and their exact validated snapshots.
It has no provider-specific field or transport logic and never projects or
aggregates account amounts.
"""

from __future__ import annotations

import datetime as _dt
import inspect
import math
import threading
import time
from contextlib import ExitStack
from typing import Any, Callable, Dict, Iterable, Type


_UTC = _dt.timezone.utc


def validate_venue_balance_snapshot(
    route: Any,
    snapshot: Any,
    *,
    account_balance_type: type,
    snapshot_type: type,
    valuation_type: type,
    unavailable: Callable[[str], None],
):
    """Validate neutral route, balance, and valuation shape before projection."""
    if type(route) is not str or not route or route != route.strip():
        unavailable("account_balance_route_unavailable")
    if type(snapshot) is not snapshot_type:
        unavailable("account_balance_snapshot_invalid")
    if (
        type(snapshot.complete) is not bool
        or snapshot.complete is not True
        or type(snapshot.usable) is not bool
        or snapshot.usable is not True
        or type(snapshot.balances) is not tuple
        or type(snapshot.currencies) is not tuple
        or type(snapshot.valuation_available) is not bool
        or type(snapshot.valuation_blockers) is not tuple
        or any(type(item) is not account_balance_type for item in snapshot.balances)
        or any(
            type(currency) is not str
            or not currency
            or currency != currency.strip()
            for currency in snapshot.currencies
        )
        or any(
            type(blocker) is not str or not blocker or blocker != blocker.strip()
            for blocker in snapshot.valuation_blockers
        )
    ):
        unavailable("account_balance_snapshot_invalid")
    valuation = snapshot.valuation
    if valuation is not None and type(valuation) is not valuation_type:
        unavailable("account_balance_valuation_invalid")
    valuation_available = snapshot.valuation_available
    if valuation_available:
        if valuation is None or snapshot.valuation_blockers:
            unavailable("account_balance_valuation_invalid")
    return valuation, valuation_available


class _RouteAccountReadState:
    """One route's exact SDK port and latest validated snapshot."""

    __slots__ = (
        "port",
        "api_generation",
        "snapshot",
        "snapshot_updated_at",
        "refresh_generation",
        "lock",
    )

    def __init__(self, port: Any, api_generation: int) -> None:
        self.port = port
        self.api_generation = api_generation
        self.snapshot = None
        self.snapshot_updated_at = 0.0
        self.refresh_generation = 0
        self.lock = threading.RLock()


class SdkAccountReadAdapter:
    """Own route-specific balance ports, validation, and one cache.

    ``error_type`` and ``missing_dependency_error_type`` are supplied by the
    caller so this module remains independent of its implementation details.
    ``sanitize_exception`` is the caller's redaction boundary. SDK
    contract imports are local to the read methods until a read is requested.

    Every route state retains its own exact port and snapshot. Switching
    routes does not evict another route; replacing the API object or calling
    ``clear`` revokes the entire adapter generation.
    """

    def __init__(
        self,
        *,
        error_type: Type[Exception],
        missing_dependency_error_type: Type[Exception],
        sanitize_exception: Callable[[BaseException], Any],
    ) -> None:
        self._error_type = error_type
        self._missing_dependency_error_type = missing_dependency_error_type
        self._sanitize_exception = sanitize_exception
        self._lock = threading.RLock()
        self._api: Any = None
        self._api_generation = 0
        self._route_states: Dict[str, _RouteAccountReadState] = {}

    def clear(self) -> None:
        """Revoke all retained route ports and snapshots for the old session."""

        with self._lock:
            self._api = None
            self._route_states.clear()
            self._api_generation += 1

    @staticmethod
    def _raise_error(error_type: Type[Exception], message: str, code: str) -> None:
        error = error_type(message)
        error.code = code
        raise error from None

    def single_configured_route(self, live_names, configured_names):
        """Require one exact configured and currently exposed route name."""
        if (
            len(live_names) != 1
            or len(configured_names) != 1
            or live_names[0] != configured_names[0]
        ):
            self._raise_error(
                self._error_type,
                "SDK account balance requires one exact configured route",
                "account_balance_route_unavailable",
            )
        return live_names[0]

    def snapshots_for_live_routes(
        self, api, routes, live_names, *, force=False, cache_ttl=0.0
    ):
        """Read a complete route batch after matching the live route set.

        Route names alone are configuration evidence; each snapshot is still
        subject to the adapter's normal port and lease validation.
        """
        if (
            not routes
            or len(live_names) != len(routes)
            or len(set(live_names)) != len(live_names)
            or set(live_names) != set(routes)
        ):
            self._raise_error(
                self._error_type,
                "SDK venue balances require every configured exact route",
                "account_balance_route_unavailable",
            )
        snapshots = self.snapshots_for_routes(
            api, routes, force=force, cache_ttl=cache_ttl
        )
        if type(snapshots) is not dict or set(snapshots) != set(routes):
            self._raise_error(
                self._error_type,
                "SDK venue balance snapshot batch is incomplete",
                "account_balance_snapshot_invalid",
            )
        return snapshots

    def _require_contracts(self):
        try:
            from bt_api_py.account_balance_contracts import (
                AccountBalanceSnapshot,
                AccountBalanceSnapshotFailureCode,
                AccountBalanceSnapshotPort,
            )
        except ImportError as exc:
            raise self._missing_dependency_error_type(
                "bt_api_py.account_balance_contracts is required for SDK balance reads"
            ) from exc
        return AccountBalanceSnapshot, AccountBalanceSnapshotFailureCode, AccountBalanceSnapshotPort

    @staticmethod
    def _validate_route(route: Any) -> bool:
        return (
            type(route) is str
            and bool(route)
            and route == route.strip()
        )

    @staticmethod
    def _validate_cache_options(force: Any, cache_ttl: Any) -> bool:
        if type(force) is not bool or type(cache_ttl) not in (int, float):
            return False
        try:
            return math.isfinite(cache_ttl) and cache_ttl >= 0.0
        except (OverflowError, TypeError, ValueError):
            return False

    def _state_for_route(
        self,
        api: Any,
        route: str,
        port_type: type,
    ) -> tuple[_RouteAccountReadState, int]:
        """Select one exact route port inside the current API generation."""

        with self._lock:
            if self._api is not api:
                self._api = api
                self._route_states.clear()
                self._api_generation += 1
            api_generation = self._api_generation
            state = self._route_states.get(route)
            if state is not None:
                return state, api_generation

            selector = getattr(api, "account_balance_snapshot", None)
            if not callable(selector):
                self._raise_error(
                    self._error_type,
                    "SDK account balance snapshot port is unavailable",
                    "account_balance_port_unavailable",
                )
            try:
                port = selector(route)
            except Exception as exc:
                self._sanitize_exception(exc)
                self._raise_error(
                    self._error_type,
                    "SDK account balance snapshot port is unavailable",
                    "account_balance_port_unavailable",
                )
            if inspect.isawaitable(port):
                close = getattr(port, "close", None)
                if callable(close):
                    close()
                self._raise_error(
                    self._error_type,
                    "SDK account balance selector must be synchronous",
                    "account_balance_port_invalid",
                )
            if not isinstance(port, port_type):
                self._raise_error(
                    self._error_type,
                    "SDK returned an invalid account balance port",
                    "account_balance_port_invalid",
                )

            state = _RouteAccountReadState(port, api_generation)
            self._route_states[route] = state
            return state, api_generation

    def _is_current_route(
        self,
        api: Any,
        route: str,
        state: _RouteAccountReadState,
        api_generation: int,
    ) -> bool:
        with self._lock:
            return bool(
                self._api is api
                and self._api_generation == api_generation
                and self._route_states.get(route) is state
                and state.api_generation == api_generation
            )

    def _validate_snapshot(self, port: Any, snapshot: Any) -> bool:
        try:
            valid = port.validate_snapshot(snapshot)
        except Exception as exc:
            self._sanitize_exception(exc)
            return False
        if inspect.isawaitable(valid):
            close = getattr(valid, "close", None)
            if callable(close):
                close()
            return False
        return type(valid) is bool and valid is True

    def _invalidate_snapshot(
        self,
        state: _RouteAccountReadState,
        snapshot: Any,
    ) -> None:
        with self._lock:
            if state.snapshot is snapshot:
                state.snapshot = None
                state.snapshot_updated_at = 0.0

    def _raise_route_changed(self) -> None:
        self._raise_error(
            self._error_type,
            "SDK account balance route changed during refresh",
            "account_balance_route_changed",
        )

    def _raise_lease_invalid(self) -> None:
        self._raise_error(
            self._error_type,
            "SDK account balance snapshot lease is invalid",
            "account_balance_snapshot_lease_invalid",
        )

    def snapshot_for_route(
        self,
        api: Any,
        route: str,
        *,
        force: bool = False,
        cache_ttl: float = 0.0,
    ) -> Any:
        """Return one exact, currently validated snapshot for ``route``."""

        (
            AccountBalanceSnapshot,
            AccountBalanceSnapshotFailureCode,
            AccountBalanceSnapshotPort,
        ) = self._require_contracts()
        if api is None or not self._validate_route(route):
            self._raise_error(
                self._error_type,
                "SDK account balance route is invalid",
                "account_balance_route_invalid",
            )
        if not self._validate_cache_options(force, cache_ttl):
            self._raise_error(
                self._error_type,
                "SDK account balance cache options are invalid",
                "account_balance_cache_options_invalid",
            )

        state, api_generation = self._state_for_route(
            api,
            route,
            AccountBalanceSnapshotPort,
        )
        with state.lock:
            return self._snapshot_for_route_state(
                api,
                route,
                state,
                api_generation,
                AccountBalanceSnapshot,
                AccountBalanceSnapshotFailureCode,
                force=force,
                cache_ttl=cache_ttl,
            )

    def _snapshot_for_route_state(
        self,
        api: Any,
        route: str,
        state: _RouteAccountReadState,
        api_generation: int,
        snapshot_type: type,
        failure_code_type: type,
        *,
        force: bool,
        cache_ttl: float,
    ) -> Any:
        with self._lock:
            if not self._is_current_route(api, route, state, api_generation):
                self._raise_route_changed()
            cached = state.snapshot
            cached_at = state.snapshot_updated_at

        cache_candidate = bool(
            not force
            and type(cached) is snapshot_type
            and cache_ttl > 0.0
            and cached_at > 0.0
            and (time.monotonic() - cached_at) < cache_ttl
        )
        if cache_candidate:
            if self._validate_snapshot(state.port, cached):
                # Recheck immediately before returning cached evidence. The
                # SDK verifier owns its lease clock and session re-read.
                if self._validate_snapshot(state.port, cached):
                    with self._lock:
                        if not self._is_current_route(api, route, state, api_generation):
                            self._raise_route_changed()
                        if state.snapshot is not cached:
                            self._raise_error(
                                self._error_type,
                                "SDK account balance snapshot was superseded",
                                "account_balance_snapshot_superseded",
                            )
                        return cached
            self._invalidate_snapshot(state, cached)

        with self._lock:
            if not self._is_current_route(api, route, state, api_generation):
                self._raise_route_changed()
            state.refresh_generation += 1
            refresh_generation = state.refresh_generation

        deadline = _dt.datetime.now(_UTC) + _dt.timedelta(seconds=5.0)
        try:
            snapshot = state.port.refresh(deadline=deadline)
        except Exception as exc:
            self._sanitize_exception(exc)
            self._raise_error(
                self._error_type,
                "SDK account balance snapshot query failed",
                "account_balance_snapshot_unavailable",
            )
        if inspect.isawaitable(snapshot):
            close = getattr(snapshot, "close", None)
            if callable(close):
                close()
            self._raise_error(
                self._error_type,
                "SDK account balance refresh must be synchronous",
                "account_balance_snapshot_invalid",
            )
        if type(snapshot) is not snapshot_type:
            self._raise_error(
                self._error_type,
                "SDK returned an invalid account balance snapshot",
                "account_balance_snapshot_invalid",
            )
        if type(snapshot.usable) is not bool or snapshot.usable is not True:
            failure = snapshot.failure_code
            code = (
                failure.value
                if type(failure) is failure_code_type
                else "account_balance_snapshot_unusable"
            )
            self._raise_error(
                self._error_type,
                "SDK account balance evidence is unavailable",
                code,
            )
        if not self._validate_snapshot(state.port, snapshot):
            self._raise_lease_invalid()

        with self._lock:
            if not self._is_current_route(api, route, state, api_generation):
                self._raise_route_changed()
            if state.refresh_generation != refresh_generation:
                self._raise_error(
                    self._error_type,
                    "SDK account balance refresh was superseded",
                    "account_balance_snapshot_superseded",
                )

        # This second same-port validation is the final SDK currentness check
        # before publishing this route's exact snapshot to a caller.
        if not self._validate_snapshot(state.port, snapshot):
            self._raise_lease_invalid()

        with self._lock:
            if not self._is_current_route(api, route, state, api_generation):
                self._raise_route_changed()
            if state.refresh_generation != refresh_generation:
                self._raise_error(
                    self._error_type,
                    "SDK account balance refresh was superseded",
                    "account_balance_snapshot_superseded",
                )
            state.snapshot = snapshot
            state.snapshot_updated_at = time.monotonic()
        return snapshot

    def cached_snapshot_for_route(
        self,
        api: Any,
        route: str,
        *,
        cache_ttl: float = 0.0,
    ) -> Any:
        """Fail closed because SDK lease revalidation may perform network I/O.

        The SDK currently exposes no proof-preserving, local-only cache check.
        Calling ``port.validate_snapshot`` from this accessor could initiate
        account/session reads, so a cache-only request is explicitly
        unavailable until the SDK provides such a capability.
        """

        del api, route, cache_ttl
        self._raise_error(
            self._error_type,
            "SDK account balance cache-only snapshot is unavailable",
            "account_balance_snapshot_unavailable",
        )

    def snapshots_for_routes(
        self,
        api: Any,
        routes: Iterable[str],
        *,
        force: bool = False,
        cache_ttl: float = 0.0,
    ) -> Dict[str, Any]:
        """Return snapshots for every exact route or fail the batch closed.

        The method uses this adapter's same route-keyed cache and the
        single-route API above. Results are accumulated locally and no partial
        mapping is returned. Every route is revalidated a second time after
        collection so an earlier route cannot pass only because it was checked
        before later route queries completed. The SDK exposes no common
        multi-account receipt, so this does not claim cross-route simultaneity.
        """

        (
            AccountBalanceSnapshot,
            _,
            _,
        ) = self._require_contracts()
        if api is None or isinstance(routes, (str, bytes)):
            self._raise_error(
                self._error_type,
                "SDK account balance routes are invalid",
                "account_balance_routes_invalid",
            )
        if not self._validate_cache_options(force, cache_ttl):
            self._raise_error(
                self._error_type,
                "SDK account balance cache options are invalid",
                "account_balance_cache_options_invalid",
            )
        try:
            route_tuple = tuple(routes)
        except Exception:
            self._raise_error(
                self._error_type,
                "SDK account balance routes are invalid",
                "account_balance_routes_invalid",
            )
        if (
            not route_tuple
            or any(not self._validate_route(route) for route in route_tuple)
            or len(set(route_tuple)) != len(route_tuple)
        ):
            self._raise_error(
                self._error_type,
                "SDK account balance routes must be unique exact route names",
                "account_balance_routes_invalid",
            )

        with self._lock:
            batch_generation = self._api_generation
            if self._api is not api:
                self._api = api
                self._route_states.clear()
                self._api_generation += 1
            batch_generation = self._api_generation

        snapshots: Dict[str, Any] = {}
        route_states: Dict[str, _RouteAccountReadState] = {}
        for route in route_tuple:
            snapshot = self.snapshot_for_route(
                api,
                route,
                force=force,
                cache_ttl=cache_ttl,
            )
            if type(snapshot) is not AccountBalanceSnapshot:
                self._raise_error(
                    self._error_type,
                    "SDK returned an invalid account balance snapshot",
                    "account_balance_snapshot_invalid",
                )
            with self._lock:
                state = self._route_states.get(route)
                if (
                    self._api is not api
                    or self._api_generation != batch_generation
                    or state is None
                    or state.api_generation != batch_generation
                ):
                    self._raise_route_changed()
                route_states[route] = state
            snapshots[route] = snapshot

        # The final barrier serializes against refreshes for these exact
        # routes and holds the adapter generation stable while every SDK lease
        # is revalidated. No partial mapping escapes on any failure.
        with ExitStack() as stack:
            for route in sorted(route_tuple):
                stack.enter_context(route_states[route].lock)
            with self._lock:
                if self._api is not api or self._api_generation != batch_generation:
                    self._raise_route_changed()
                if any(
                    self._route_states.get(route) is not route_states[route]
                    for route in route_tuple
                ):
                    self._raise_route_changed()
                for route in route_tuple:
                    snapshot = snapshots[route]
                    state = route_states[route]
                    if state.snapshot is not snapshot:
                        self._raise_error(
                            self._error_type,
                            "SDK account balance snapshot was superseded",
                            "account_balance_snapshot_superseded",
                        )
                    if (
                        cache_ttl > 0.0
                        and time.monotonic() - state.snapshot_updated_at >= cache_ttl
                    ):
                        self._raise_error(
                            self._error_type,
                            "SDK account balance snapshot expired during batch collection",
                            "account_balance_snapshot_unavailable",
                        )
                    if type(snapshot.usable) is not bool or snapshot.usable is not True:
                        self._raise_error(
                            self._error_type,
                            "SDK account balance evidence is unavailable",
                            "account_balance_snapshot_unusable",
                        )
            for route in route_tuple:
                state = route_states[route]
                snapshot = snapshots[route]
                if not self._validate_snapshot(state.port, snapshot):
                    self._invalidate_snapshot(state, snapshot)
                    self._raise_lease_invalid()
            with self._lock:
                if self._api is not api or self._api_generation != batch_generation:
                    self._raise_route_changed()
                if any(
                    self._route_states.get(route) is not route_states[route]
                    or route_states[route].snapshot is not snapshots[route]
                    for route in route_tuple
                ):
                    self._raise_error(
                        self._error_type,
                        "SDK account balance snapshot was superseded",
                        "account_balance_snapshot_superseded",
                    )
        return snapshots


__all__ = ("SdkAccountReadAdapter", "validate_venue_balance_snapshot")
