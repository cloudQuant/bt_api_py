"""Keep the target states of active DRAIN causes current (account owner thread).

A DRAIN freezes the orders that may still be working as its targets. Whether a
target has ended is learned from order returns, and the stored state of a
target changes only when its cause is reconciled. Lease-expiry DRAINs and the
cancel-only run reconcile their own causes; an operator's DRAIN was reconciled
only when the operator asked for it. Until then a listing could show a target
as ``PENDING`` or ``UNKNOWN`` although the venue had long reported the order
cancelled or filled, and an operator without write authority could not make it
current at all.

This watcher reconciles the active DRAIN causes of the account, a bounded
number per pass, at most once per interval. It only observes: it never sends a
cancel, never applies and never releases a cause. A target the reconciliation
finds ended is stored as ``OBSERVED_TERMINAL``; one whose end is contradicted
by a later return goes back to ``UNKNOWN`` (see ``reconcile_targets``).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

_LOG = logging.getLogger(__name__)


class GatewayDrainTargetObserver:
    """Owner-thread watcher; see the module docstring."""

    def __init__(self, *, control: Any, interval_seconds: float = 1.0, causes_per_pass: int = 16,
                 clock: Callable[[], float] = time.monotonic) -> None:
        from bt_api_gateway.direct_control import GatewayDirectControl

        if (type(control) is not GatewayDirectControl or not callable(clock)
                or type(interval_seconds) not in (int, float) or not 0 < interval_seconds <= 60
                or type(causes_per_pass) is not int or not 1 <= causes_per_pass <= 256):
            raise ValueError("INVALID_GATEWAY_DRAIN_OBSERVER")
        self._control, self._clock = control, clock
        self._interval, self._page = float(interval_seconds), causes_per_pass
        self._thread = threading.current_thread()
        self._next = 0.0
        self._after = ""
        self._failed: str | None = None

    def maintain(self) -> None:
        """One pass, at most every ``interval_seconds``. Never raises."""
        if threading.current_thread() is not self._thread:
            return
        now = self._clock()
        if now < self._next:
            return
        self._next = now + self._interval
        try:
            self._pass()
            if self._failed is not None:
                self._failed = None
                _LOG.info("DRAIN targets are being reconciled again")
        except Exception as error:
            # Nothing is lost: the stored states stay as they were and the next pass starts over.
            self._after = ""
            code = getattr(error, "code", None) or str(error) or type(error).__name__
            if code != self._failed:
                self._failed = code
                _LOG.error("DRAIN targets could not be reconciled (their listed states may be out of "
                           "date; an operator's reconcile still works): %s", code)

    def _pass(self) -> None:
        causes = self._control.active_drain_causes(after=self._after, limit=self._page)
        if not causes and self._after:
            # End of the list: start over in this same pass so a short list is seen every pass.
            self._after = ""
            causes = self._control.active_drain_causes(after="", limit=self._page)
        for cause in causes:
            self._after = cause["cause_id"]
            if self._control.reconcile_targets(cause["cause_id"]):
                _LOG.info("DRAIN cause %s (scope %s%s): target states updated from order returns",
                          cause["cause_id"], cause["scope_kind"],
                          " " + cause["scope_value"] if cause["scope_value"] else "")
        if len(causes) < self._page:
            self._after = ""


__all__ = ("GatewayDrainTargetObserver",)
