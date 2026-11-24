"""Owner-bound scheduling and exposure of CTP account order cut candidates."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass

from bt_api_py.ctp_gateway_order_cut import CtpGatewayOrderCutCoordinator

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CtpGatewayOrderCutSettings:
    query_timeout_seconds: float
    refresh_interval_seconds: float
    principals: frozenset[str]

    def __post_init__(self):
        if (
            type(self.query_timeout_seconds) not in (int, float)
            or not math.isfinite(self.query_timeout_seconds)
            or not 0.05 <= self.query_timeout_seconds <= 300
            or type(self.refresh_interval_seconds) not in (int, float)
            or not math.isfinite(self.refresh_interval_seconds)
            or not 0.05 <= self.refresh_interval_seconds <= 3600
            or type(self.principals) is not frozenset
            or not 1 <= len(self.principals) <= 256
            or any(
                type(x) is not str
                or not x
                or x != x.strip()
                or not x.isprintable()
                or len(x.encode("utf-8", "strict")) > 128
                for x in self.principals
            )
        ):
            raise ValueError("INVALID_ORDER_CUT_SETTINGS")


class CtpGatewayOrderCutSource:
    """A single owner-local source; no reader can create a native query."""

    def __init__(self, session, maintenance, journal, guard, settings, *, execution_domain_ref):
        if (
            type(settings) is not CtpGatewayOrderCutSettings
            or type(execution_domain_ref) is not str
            or not execution_domain_ref
            or execution_domain_ref != execution_domain_ref.strip()
        ):
            raise ValueError("INVALID_ORDER_CUT_SOURCE")
        if maintenance._ledger.journal is not journal or maintenance._ledger._guard is not guard:
            raise ValueError("INVALID_ORDER_CUT_SOURCE")
        self._session, self._maintenance = session, maintenance
        self._journal, self._guard, self._settings = journal, guard, settings
        self._original_binding = (session, maintenance, journal, guard, settings)
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._principals, self._execution_domain_ref = settings.principals, execution_domain_ref
        self._coordinator = CtpGatewayOrderCutCoordinator(
            session, maintenance, timeout=settings.query_timeout_seconds
        )
        self._next_due = 0.0
        self._closed = False
        self._failed = False

    def _check(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError("ORDER_CUT_WRONG_OWNER")
        if (
            self._closed
            or any(
                current is not original
                for current, original in zip(
                    (self._session, self._maintenance, self._journal, self._guard, self._settings),
                    self._original_binding,
                    strict=True,
                )
            )
            or self._maintenance._session is not self._session
            or self._maintenance._ledger.journal is not self._journal
            or self._maintenance._ledger._guard is not self._guard
        ):
            raise RuntimeError("ORDER_CUT_SOURCE_UNAVAILABLE")

    @property
    def principals(self):
        return self._principals

    @property
    def execution_domain_ref(self):
        return self._execution_domain_ref

    @property
    def journal(self):
        self._check()
        return self._journal

    @property
    def guard(self):
        self._check()
        return self._guard

    def current(self):
        self._check()
        coordinator = self._coordinator
        if self._failed or coordinator is None:
            return None
        if coordinator.expired():
            if coordinator.retire_expired():
                self._coordinator = CtpGatewayOrderCutCoordinator(
                    self._session, self._maintenance, timeout=self._settings.query_timeout_seconds
                )
            if coordinator.status().state in {"GAP", "UNKNOWN"}:
                self._failed = True
            return None
        if coordinator.status().state != "CANDIDATE":
            return None
        candidate = coordinator.live_candidate()
        if coordinator.status().state in {"GAP", "UNKNOWN"}:
            self._failed = True
        return candidate

    def current_binding(self):
        """Return the same original session binding only while a cut is current."""
        self._check()
        coordinator = self._coordinator
        if coordinator is None or self.current() is None or self._coordinator is not coordinator:
            return None
        if (
            self._maintenance._observed_order_source is not coordinator._tracker
            or coordinator._tracker._binding is not coordinator._binding
        ):
            return None
        return coordinator._binding

    def pending(self):
        self._check()
        if self._coordinator is None:
            return False
        status = self._coordinator.status()
        return status.state in {
            "QUERYING",
            "PROJECTING",
            "WAITING_RECEIPTS",
            "VERIFYING_RECEIPTS",
            "COMMITTING",
            "CLOSE_PENDING",
            "ABORTING",
        } or (status.state in {"GAP", "UNKNOWN"} and status.pin_retained is not False)

    def due(self, now):
        self._check()
        if self._failed or self._coordinator is None:
            return False
        self.current()
        return self._coordinator.status().state == "IDLE" and now >= self._next_due

    def begin(self):
        self._check()
        if self._failed or self._coordinator is None:
            return False
        started = self._coordinator.begin()
        self._next_due = time.monotonic() + self._settings.refresh_interval_seconds
        if self._coordinator.status().state in {"GAP", "UNKNOWN"}:
            self._failed = True
        return started

    def advance(self):
        self._check()
        if self._failed or self._coordinator is None:
            return False
        pending = self._coordinator.advance()
        status = self._coordinator.status()
        if status.state in {"GAP", "UNKNOWN"}:
            self._failed = True
        elif status.state == "ABORTED" and status.pin_retained is False:
            _LOG.warning(
                "CTP order cut query aborted cleanly (%s); observed order source intact, will retry",
                status.reason,
            )
            if self._coordinator.close():
                self._coordinator = CtpGatewayOrderCutCoordinator(
                    self._session, self._maintenance, timeout=self._settings.query_timeout_seconds
                )
        return pending

    def pause_for_reconnect(self):
        self._check()
        if self._coordinator is None:
            return True
        state = self._coordinator.status().state
        if not self._coordinator.close():
            return False
        if state in {"GAP", "UNKNOWN"}:
            self._failed = True
        self._coordinator = None
        return True

    def rebind_after_reconnect(self):
        self._check()
        if self._coordinator is None and not self._failed:
            self._coordinator = CtpGatewayOrderCutCoordinator(
                self._session, self._maintenance, timeout=self._settings.query_timeout_seconds
            )
            self._next_due = 0.0

    def close(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError("ORDER_CUT_WRONG_OWNER")
        if self._closed:
            return True
        self._check()
        if self._coordinator is not None and not self._coordinator.close():
            return False
        self._closed = True
        return True
