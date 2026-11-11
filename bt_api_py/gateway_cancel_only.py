"""Cancel-only start of the Gateway (D63-58).

An operator starts the Gateway in this mode when it cannot serve normally but
the account still has working orders. The Gateway takes the same account lock,
identity and journal as a normal start. Before the host serves anything, this
run stores an account-scope DRAIN cause, so no new order is admitted. It then
cancels, one per maintenance step, every order of the journal that is still
working, and reports when all of them are observed terminal.

Limits, stated rather than worked around:

* It needs journal schema 17 (cancellation on a control cause) and the cancel
  path. Below that it reports ``UNAVAILABLE`` and cancels nothing.
* It never guesses: an order whose dispatch outcome is unknown is not
  cancelled blindly, and a cancel that was sent is not sent twice. Such targets
  keep the run ``INCOMPLETE`` when its deadline passes.
* The cause stays active after the run. A later normal start admits no new
  order until an operator resumes it.
* It only knows orders of this journal. Orders placed outside the Gateway are
  not touched; the written procedure covers them.

Everything that touches the journal runs on the account owner thread. The host
thread only asks for one step at a time and never queues behind a busy owner.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from contextlib import contextmanager

_LOG = logging.getLogger(__name__)

_OWNER_PICKUP_SECONDS = 0.05
_OWNER_RETRY_SECONDS = 0.25
_OWNER_RUN_SECONDS = 2.0
_PAGE = 256
_STATES = ("STARTING", "WAITING_FOR_RETURNS", "CANCELLING", "COMPLETE", "INCOMPLETE", "UNAVAILABLE")


def _text(value):
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


class GatewayCancelOnlyRun:
    """One cancel-only pass over the journal's working orders."""

    def __init__(self, *, instance_id, account_ref, owner, control, operator, reason, service_identity,
                 returns_ready, deadline_seconds=120.0, clock=time.monotonic):
        from bt_api_base.service_protocol import WireIdentity, WireRole
        from bt_api_gateway.direct_control import GatewayDirectControl

        from bt_api_py.gateway_direct_owner import GatewayDirectOwner

        if (not _text(instance_id) or not _text(account_ref) or not _text(operator) or not _text(reason)
                or type(owner) is not GatewayDirectOwner or type(control) is not GatewayDirectControl
                or owner.binding() is None or owner.binding().account_scope != account_ref
                or not owner.is_bound_to(control._journal) or control._journal.account_scope != account_ref
                or type(service_identity) is not WireIdentity or service_identity.role is not WireRole.SERVICE
                or service_identity.delegate_ref is not None or not callable(returns_ready) or not callable(clock)
                or type(deadline_seconds) not in (int, float) or not 1 <= deadline_seconds <= 3600):
            raise ValueError("INVALID_GATEWAY_CANCEL_ONLY")
        self.instance_id, self.account_ref = instance_id, account_ref
        self.owner, self.control = owner, control
        self.operator, self.reason, self.service_identity = operator, reason, service_identity
        self._returns_ready, self._clock = returns_ready, clock
        self._deadline_seconds = float(deadline_seconds)
        self.run_id = uuid.uuid4().hex
        self.cause_id = "cancel-only:" + self.run_id
        self.origin = "system:cancel_only:" + hashlib.sha256(
            (instance_id + "\0" + account_ref).encode()).hexdigest()
        self._state, self._code = "STARTING", None
        self._counts = {"targets": 0, "pending": 0, "unknown": 0, "observed_terminal": 0, "cancel_dispatched": 0}
        self._deadline = None
        self._owner_retry_at = 0.0

    # -- owner thread ---------------------------------------------------

    def apply_on_owner(self):
        """Store the account DRAIN cause. Call on the owner thread before the host serves."""
        journal = self.control._journal
        if journal._schema_version < 17 or not self.owner.supports_cancel:
            self._state, self._code = "UNAVAILABLE", (
                "CANCEL_ONLY_REQUIRES_JOURNAL_17" if journal._schema_version < 17 else "CANCEL_PATH_UNAVAILABLE")
            # No new order either way: the cause itself needs only schema 10.
        self.control.apply(self.cause_id, "DRAIN", scope_kind="account", scope_value="", origin=self.origin)
        _LOG.warning("Cancel-only start by %s (%s): account DRAIN cause %s stored, no new order is admitted",
                     self.operator, self.reason, self.cause_id)
        if self._state == "STARTING":
            self._state = "WAITING_FOR_RETURNS"
        self._deadline = self._clock() + self._deadline_seconds

    def _count(self):
        counts = {"targets": 0, "pending": 0, "unknown": 0, "observed_terminal": 0}
        after = ""
        while True:
            page = self.control.targets(self.cause_id, after=after, limit=_PAGE)
            for target in page:
                after = target["reservation_id"]
                counts["targets"] += 1
                key = {"PENDING": "pending", "OBSERVED_TERMINAL": "observed_terminal"}.get(target["state"], "unknown")
                counts[key] += 1
            if len(page) < _PAGE:
                break
        counts["cancel_dispatched"] = self._counts["cancel_dispatched"]
        self._counts = counts

    @contextmanager
    def _fence(self, _record):
        # The journal itself rechecks, inside its own transaction, that the
        # cause is still an active DRAIN and that the target is unchanged. This
        # fence only adds that the run is still the one that claimed the
        # target; it must not open the journal again.
        def check():
            if self._state != "CANCELLING":
                raise ValueError("CANCEL_ONLY_NOT_RUNNING")
        check()
        yield check

    def _step_on_owner(self):
        if self._state == "WAITING_FOR_RETURNS":
            if self._returns_ready() is not True:
                return
            self._state = "CANCELLING"
        self.control.reconcile_targets(self.cause_id)
        after = ""
        while True:
            page = self.control.targets(self.cause_id, after=after, limit=_PAGE)
            for target in page:
                after = target["reservation_id"]
                if target["state"] != "PENDING":
                    continue
                claimed, token = self.control.claim_target(self.cause_id, target["reservation_id"])
                if token is not None:
                    self.control.dispatch_claimed_cancel(
                        claimed, self.owner._cancel_dispatcher, token, request_fence=self._fence,
                        requester=self.service_identity.principal_ref,
                        authority_source={"kind": "operator", "actor": self.operator,
                                          "registration": "cancel-only:" + self.run_id,
                                          "epoch": self.control._journal.generation, "event_id": None})
                    self._counts["cancel_dispatched"] += 1
                    _LOG.warning("Cancel-only: cancel dispatched for reservation %s of strategy %s",
                                 target["reservation_id"], target.get("strategy"))
                # One target per step: the owner also has returns to ingest.
                self._count()
                return
            if len(page) < _PAGE:
                break
        self._count()
        if self._counts["pending"] == 0 and self._counts["unknown"] == 0:
            self._state = "COMPLETE"

    # -- host thread ----------------------------------------------------

    def maintain(self):
        """One step; returns at once when the owner is busy or the run has ended."""
        if self._state in ("COMPLETE", "INCOMPLETE", "UNAVAILABLE", "STARTING"):
            return
        from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError

        now = self._clock()
        if now < self._owner_retry_at:
            return
        handle = self.owner.submit_service_call(self._step_on_owner)
        try:
            try:
                handle.result(timeout_seconds=_OWNER_PICKUP_SECONDS)
            except GatewayOwnerServiceCallError as error:
                if error.code == "SERVICE_CALL_TIMEOUT_QUEUED" and handle.cancel():
                    self._owner_retry_at = self._clock() + _OWNER_RETRY_SECONDS
                elif error.code in ("SERVICE_CALL_TIMEOUT_QUEUED", "SERVICE_CALL_TIMEOUT_RUNNING"):
                    handle.result(timeout_seconds=_OWNER_RUN_SECONDS)
                else:
                    raise
        except Exception as error:
            # A failed step is reported, never retried blindly: a cancel may be out.
            _LOG.warning("Cancel-only step failed: %s", getattr(error, "code", type(error).__name__))
            self._code = "STEP_FAILED"
        if self._state not in ("COMPLETE",) and self._deadline is not None and self._clock() >= self._deadline:
            self._state = "INCOMPLETE"
            self._code = self._code or "DEADLINE_PASSED"

    def status(self):
        return {"state": self._state, "code": self._code, "cause_id": self.cause_id,
                "operator": self.operator, **self._counts}

    @property
    def finished(self):
        return self._state in ("COMPLETE", "INCOMPLETE", "UNAVAILABLE")


__all__ = ("GatewayCancelOnlyRun",)
