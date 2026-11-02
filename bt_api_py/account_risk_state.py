"""SDK-owned neutral account-risk cache, refresh reservation and projection.

Each instance has one cache and lock; callers inject their own lifecycle,
identity fences and redaction. This state does not prove account or risk facts.
"""

from __future__ import annotations

import copy
import math
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


def public_venue_key(venue: Any) -> str:
    """Return the stable strategy-facing provider key for one SDK route."""

    return str(venue or "").partition("___")[0].strip().lower()


def unavailable_account_risk_snapshot(
    configured_routes: Sequence[str],
    *,
    configured_loss_limit: Any,
    error_code: str,
    errors: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Create the explicit fail-closed public projection for missing evidence."""

    routes = sorted(str(route).strip() for route in configured_routes if str(route).strip())
    if type(error_code) is not str or not error_code:
        error_code = "account_risk_snapshot_unavailable"
    selected_errors = list(errors or (error_code,))
    return {
        "schema_version": 1,
        "baseline_equity": None,
        "current_equity": None,
        "realized_net": None,
        "configured_venues": sorted({public_venue_key(route) for route in routes}),
        "configured_venue_routes": routes,
        "baseline_equity_by_venue": None,
        "current_equity_by_venue": None,
        "currency": None,
        "generation": 0,
        "fencing_epoch": 0,
        "as_of_monotonic_ns": 0,
        "owner_pid": None,
        "clock_domain_id": "",
        "identity_binding_sha256": "",
        "durable": False,
        "trading_blocked": True,
        "loss_limit_bps": configured_loss_limit,
        "loss_limit_breached": False,
        "loss_breached_at": None,
        "loss_amount": None,
        "loss_limit_amount": None,
        "loss_bps_observed": None,
        "peak_loss_bps": None,
        "evidence_complete": False,
        "evidence_errors": selected_errors,
        "error_code": error_code,
    }


def project_account_risk_snapshot(
    risk_snapshot: Any,
    identity_binding: Any,
    *,
    configured_routes: Sequence[str],
    configured_loss_limit: Any = None,
    owner_pid: int,
    read_started_ns: int,
    read_finished_ns: int,
) -> Dict[str, Any]:
    """Bind Store-facing projection callbacks to SDK-neutral risk validation."""
    try:
        from bt_api_py.account_risk_projection import project_account_risk_snapshot as project
    except ImportError:
        return unavailable_account_risk_snapshot(
            configured_routes,
            configured_loss_limit=configured_loss_limit,
            error_code="account_risk_snapshot_unavailable",
        )
    return project(
        risk_snapshot,
        identity_binding,
        configured_routes=configured_routes,
        configured_loss_limit=configured_loss_limit,
        owner_pid=owner_pid,
        read_started_ns=read_started_ns,
        read_finished_ns=read_finished_ns,
        unavailable_account_risk_snapshot=unavailable_account_risk_snapshot,
        public_venue_key=public_venue_key,
    )


def is_expected_prebaseline_account_risk(snapshot: Any) -> bool:
    """Delegate the neutral missing-baseline classification to the SDK."""
    from bt_api_py.account_risk_projection import (
        is_expected_prebaseline_account_risk as classify,
    )

    return classify(snapshot)


class AccountRiskState:
    """Own account-risk reads, projection, cache, and callback refresh state."""

    def __init__(
        self,
        refresh_interval_seconds: float,
        *,
        redact_snapshot: Callable[[Mapping[str, Any]], Any],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(refresh_interval_seconds, bool)
            or not isinstance(refresh_interval_seconds, (int, float))
            or not math.isfinite(float(refresh_interval_seconds))
            or float(refresh_interval_seconds) < 0
        ):
            raise ValueError("refresh interval must be finite and nonnegative")
        if not callable(redact_snapshot) or not callable(monotonic):
            raise TypeError("account-risk state callbacks must be callable")
        self._refresh_interval = max(float(refresh_interval_seconds), 0.05)
        self._redact_snapshot = redact_snapshot
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_generation: Optional[int] = None
        self._last_refresh_requested = 0.0
        self._refresh_pending = False
        self._refresh_token = 0

    @property
    def refresh_pending(self) -> bool:
        with self._lock:
            return bool(self._refresh_pending)

    def reset_generation(self) -> None:
        """Drop stale evidence and invalidate any older refresh reservation."""

        with self._lock:
            self._snapshot = None
            self._snapshot_generation = None
            self._last_refresh_requested = 0.0
            self._refresh_pending = False
            self._refresh_token += 1

    def cache_snapshot(self, snapshot: Mapping[str, Any], generation: int) -> Dict[str, Any]:
        """Redact then atomically publish a snapshot for one Store generation."""

        if not isinstance(snapshot, Mapping):
            raise TypeError("account-risk snapshot must be a mapping")
        safe_snapshot = self._redact_snapshot(dict(snapshot))
        if not isinstance(safe_snapshot, Mapping):
            raise TypeError("account-risk redactor must return a mapping")
        retained = copy.deepcopy(dict(safe_snapshot))
        with self._lock:
            self._snapshot = retained
            self._snapshot_generation = generation
            self._last_refresh_requested = float(self._monotonic())
        return copy.deepcopy(retained)

    def cached_snapshot(self, generation: int) -> Optional[Dict[str, Any]]:
        """Return a copy only when the cached Store generation still matches."""

        with self._lock:
            if self._snapshot is None or self._snapshot_generation != generation:
                return None
            snapshot = copy.deepcopy(self._snapshot)
        return snapshot

    def has_cached_snapshot(self, generation: int) -> bool:
        with self._lock:
            return bool(
                self._snapshot is not None and self._snapshot_generation == generation
            )

    def read_snapshot(
        self,
        api: Any,
        *,
        generation: int,
        configured_routes: Sequence[str],
        configured_loss_limit: Any,
        initialize_baseline: bool,
        identity_expectations: Tuple[Any, ...],
        capture_identity_binding: Callable[[Any, Tuple[Any, ...]], Any],
        validate_identity_binding: Callable[[Any, Any, Tuple[Any, ...]], Any],
        get_snapshot_reader: Callable[..., Any],
        validate_account_risk_snapshot: Callable[[Any, Any, Any], Any],
        project_typed_snapshot: Callable[..., Mapping[str, Any]],
        exception_code: Callable[[Exception, str], str],
        sanitize_exception: Callable[[BaseException], Any],
        observe_error: Callable[[BaseException], Any],
        owner_pid: int,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> Dict[str, Any]:
        """Delegate SDK identity and risk reads while retaining Store cache ownership."""
        try:
            from bt_api_py.account_risk_projection import read_account_risk_snapshot
        except ImportError:
            callbacks = (
                capture_identity_binding, validate_identity_binding, get_snapshot_reader,
                validate_account_risk_snapshot, project_typed_snapshot, exception_code,
                sanitize_exception, observe_error, monotonic_ns,
            )
            if any(not callable(callback) for callback in callbacks):
                raise TypeError("account-risk read callbacks must be callable")
            routes = sorted(str(route).strip() for route in configured_routes if str(route).strip())
            code = (
                "account_risk_identity_unproven"
                if not routes or len(routes) != len(set(routes))
                else "account_risk_identity_binding_unavailable"
            )
            snapshot = unavailable_account_risk_snapshot(
                routes, configured_loss_limit=configured_loss_limit, error_code=code,
            )
            return self.cache_snapshot(snapshot, generation)
        return read_account_risk_snapshot(
            api,
            generation=generation,
            configured_routes=configured_routes,
            configured_loss_limit=configured_loss_limit,
            initialize_baseline=initialize_baseline,
            identity_expectations=identity_expectations,
            capture_identity_binding=capture_identity_binding,
            validate_identity_binding=validate_identity_binding,
            get_snapshot_reader=get_snapshot_reader,
            validate_account_risk_snapshot=validate_account_risk_snapshot,
            project_typed_snapshot=project_typed_snapshot,
            exception_code=exception_code,
            sanitize_exception=sanitize_exception,
            observe_error=observe_error,
            owner_pid=owner_pid,
            monotonic_ns=monotonic_ns,
            cache_snapshot=self.cache_snapshot,
            unavailable_account_risk_snapshot=unavailable_account_risk_snapshot,
        )

    def prepare_refresh(
        self,
        *,
        required: bool,
        store_started: bool,
        store_connected: bool,
    ) -> Tuple[bool, Dict[str, Any], Optional[int]]:
        """Atomically reserve at most one callback-safe refresh dispatch."""

        with self._lock:
            return self._prepare_refresh_locked(
                bool(required), bool(store_started), bool(store_connected), float(self._monotonic())
            )

    def clear_refresh_pending(self, refresh_token: Any) -> None:
        """Clear only the current one-shot refresh reservation."""

        if type(refresh_token) is not int:
            return
        with self._lock:
            if refresh_token == self._refresh_token:
                self._refresh_pending = False

    def callback_state(
        self,
        *,
        generation: int,
        required: bool,
        store_started: bool,
        store_connected: bool,
        dispatch_refresh: Callable[[int], Any],
        unavailable_snapshot: Callable[[], Mapping[str, Any]],
        on_dispatch_error: Optional[Callable[[Exception], None]] = None,
    ) -> Dict[str, Any]:
        """Return one atomic cache/reservation view and dispatch outside lock."""

        if not callable(dispatch_refresh) or not callable(unavailable_snapshot):
            raise TypeError("account-risk callback functions must be callable")
        with self._lock:
            snapshot = (
                copy.deepcopy(self._snapshot)
                if self._snapshot is not None and self._snapshot_generation == generation
                else None
            )
            should_dispatch, reservation, refresh_token = self._prepare_refresh_locked(
                bool(required),
                bool(store_started),
                bool(store_connected),
                float(self._monotonic()),
            )
            refresh_status = str(reservation.get("status") or "refresh_unavailable")
            if should_dispatch or refresh_status == "already_pending":
                refresh_state = "refresh_pending"
            elif refresh_status in {"not_required", "refresh_interval"}:
                refresh_state = "current"
            else:
                refresh_state = "refresh_unavailable"

        if should_dispatch:
            if refresh_token is None:
                refresh_state = "refresh_failed"
            else:
                try:
                    receipt = dispatch_refresh(refresh_token)
                except Exception as error:
                    if on_dispatch_error is not None:
                        try:
                            on_dispatch_error(error)
                        except Exception:
                            pass
                    self.clear_refresh_pending(refresh_token)
                    refresh_state = "refresh_failed"
                else:
                    if not isinstance(receipt, Mapping) or receipt.get("queued") is not True:
                        self.clear_refresh_pending(refresh_token)
                        refresh_state = "refresh_rejected"

        safe_snapshot = snapshot if snapshot is not None else dict(unavailable_snapshot())
        return {
            "snapshot": safe_snapshot,
            "refresh_state": refresh_state,
            "entry_allowed": bool(
                refresh_state == "current"
                and safe_snapshot.get("evidence_complete") is True
                and safe_snapshot.get("trading_blocked") is False
            ),
        }

    def _prepare_refresh_locked(
        self,
        required: bool,
        store_started: bool,
        store_connected: bool,
        now: float,
    ) -> Tuple[bool, Dict[str, Any], Optional[int]]:
        if not required:
            return False, {"queued": False, "status": "not_required"}, None
        if not store_started or not store_connected:
            return False, {"queued": False, "status": "store_not_running"}, None
        if self._refresh_pending:
            return False, {"queued": True, "status": "already_pending"}, None
        if (
            self._last_refresh_requested
            and now - self._last_refresh_requested < self._refresh_interval
        ):
            return False, {"queued": False, "status": "refresh_interval"}, None
        self._refresh_pending = True
        self._last_refresh_requested = now
        self._refresh_token += 1
        return True, {"queued": True, "status": "refresh_reserved"}, self._refresh_token


__all__ = (
    "AccountRiskState", "is_expected_prebaseline_account_risk",
    "project_account_risk_snapshot", "public_venue_key", "unavailable_account_risk_snapshot",
)
