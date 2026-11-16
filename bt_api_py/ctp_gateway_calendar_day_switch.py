"""Local simulated calendar version selection from one CTP owner session.

The observed day is not a settlement, account recovery, or write grant.
"""

from __future__ import annotations

import threading
from datetime import datetime
from uuid import uuid4

from bt_api_ctp.registered_sim_session import CtpQuerySessionBinding

from bt_api_py.ctp_gateway_calendar_config import CtpCalendarDaySwitchSettings
from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError
from bt_api_py.trading_calendar_binding import GateDecision, SimulationCalendarBinding


class _Gate:
    def __init__(self):
        self.event = None

    def issue(self, status, day):
        event = object()
        decision = GateDecision(
            True, status.route, status.source_generation, day, status.last_sequence + 1, uuid4().hex
        )
        self.event = (event, decision)
        return event

    def verify(self, event, context):
        pair, self.event = self.event, None
        if pair is None or pair[0] is not event:
            return None
        return pair[1]


class CtpGatewayCalendarDaySwitch:
    """One non-daemon worker with at most one owner call and prepared candidate."""

    def __init__(
        self,
        *,
        owner,
        session,
        session_is_original,
        is_closing=None,
        service,
        store,
        snapshot_factory,
        route,
        settings,
    ):
        if (
            type(settings) is not CtpCalendarDaySwitchSettings
            or not callable(session_is_original)
            or (is_closing is not None and not callable(is_closing))
        ):
            raise ValueError("INVALID_CALENDAR_DAY_SWITCH")
        self._owner, self._session, self._service = owner, session, service
        self._session_is_original = session_is_original
        self._is_closing = is_closing or (lambda: False)
        self._store, self._factory, self._route, self._settings = (
            store,
            snapshot_factory,
            route,
            settings,
        )
        self._generation = "ctp-calendar-" + uuid4().hex
        self._gate = _Gate()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._state, self._reason = "NEW", "NOT_STARTED"
        self._handle = None
        self._binding = None
        self._highest_day = None
        self._thread = threading.Thread(
            target=self._run, name="ctp-calendar-day-switch", daemon=False
        )
        self._started = False

    def status(self):
        with self._lock:
            return {
                "state": self._state,
                "reason": self._reason,
                "pending_owner_call": self._handle is not None,
                "bound": self._binding is not None,
            }

    def _set(self, state, reason):
        with self._lock:
            self._state, self._reason = state, reason

    def start(self):
        with self._lock:
            if self._started or self._stop:
                raise RuntimeError("CALENDAR_DAY_SWITCH_ALREADY_STARTED")
            self._owner.claim_service_port("gateway.calendar_day_switch", self)
            self._started = True
            self._state, self._reason = "STARTING", "SOURCE_UNAVAILABLE"
            try:
                self._thread.start()
            except BaseException:
                self._started = False
                self._state, self._reason = "FAILED", "START_FAILED"
                raise

    def close(self):
        with self._lock:
            self._stop = True
            started = self._started
        self._wake.set()
        if started:
            self._thread.join(self._settings.close_timeout_seconds)
        with self._lock:
            return not (started and self._thread.is_alive()) and self._handle is None

    def _call(self, callback):
        with self._lock:
            if self._stop or self._is_closing():
                return None
            try:
                self._handle = self._owner.submit_service_call(callback)
            except Exception:
                self._state, self._reason = "DEGRADED", "OWNER_UNAVAILABLE"
                return None
            handle = self._handle
        while True:
            with self._lock:
                stopping = self._stop
            if stopping:
                try:
                    if handle.cancel():
                        with self._lock:
                            self._handle = None
                        return None
                except Exception:
                    self._set("DEGRADED", "OWNER_UNCONFIRMED")
            try:
                result = handle.result(timeout_seconds=self._settings.owner_wait_seconds)
            except GatewayOwnerServiceCallError as exc:
                if exc.code in ("SERVICE_CALL_TIMEOUT_QUEUED", "SERVICE_CALL_TIMEOUT_RUNNING"):
                    self._set("DEGRADED", "OWNER_TIMEOUT")
                    continue
                with self._lock:
                    self._handle = None
                self._set("DEGRADED", "SOURCE_UNAVAILABLE")
                return None
            except Exception:
                # Outcome remains uncertain: retain this exact handle and never resubmit.
                self._set("DEGRADED", "OWNER_UNCONFIRMED")
                self._wake.wait(self._settings.poll_interval_seconds)
                self._wake.clear()
                continue
            with self._lock:
                self._handle = None
            return result

    def _capture(self):
        if self._session_is_original() is not True:
            return None
        token = self._session.capture_calendar_session()
        if type(token) is not CtpQuerySessionBinding:
            return None
        return token

    def _commit(self, token, candidate, prepared, day):
        with self._lock:
            if (
                self._stop
                or self._is_closing()
                or self._session_is_original() is not True
                or self._session.calendar_session_is_current(token) is not True
            ):
                return False
            if self._binding is None:
                self._service.rebind(candidate)
                self._binding = candidate
                return True
            event = self._gate.issue(self._binding.status(), day)
            return self._binding.activate_prepared(event, prepared).outcome == "ACTIVATED"

    def _run(self):
        try:
            self._work()
        except BaseException:
            self._set("FAILED", "INTERNAL_FAILURE")

    def _work(self):
        while True:
            with self._lock:
                if self._stop:
                    self._state, self._reason = "CLOSED", "STOPPED"
                    return
            token = self._call(self._capture)
            if token is None:
                self._set("DEGRADED", "SOURCE_UNAVAILABLE")
            else:
                try:
                    day = datetime.strptime(token.trading_day, "%Y%m%d").date()
                    if day.strftime("%Y%m%d") != token.trading_day:
                        raise ValueError
                except Exception:
                    self._set("DEGRADED", "SOURCE_UNAVAILABLE")
                else:
                    if self._highest_day is not None and day < self._highest_day:
                        self._set("DEGRADED", "DAY_REGRESSION")
                    else:
                        self._highest_day = day
                        current = self._binding
                        if current is not None and day == current.status().bound_trading_day:
                            self._set("READY", "SAME_DAY")
                        else:
                            try:
                                if current is None:
                                    candidate = SimulationCalendarBinding(
                                        mode="SIMULATION_ONLY",
                                        store=self._store,
                                        snapshot_factory=self._factory,
                                        gate=self._gate,
                                        route=self._route,
                                        source_generation=self._generation,
                                        trading_day=day,
                                        last_sequence=0,
                                    )
                                    if candidate.status().calendar_ref is None:
                                        raise ValueError
                                    prepared = None
                                else:
                                    candidate, prepared = current, current.prepare_update()
                            except Exception:
                                self._set("DEGRADED", "SNAPSHOT_UNAVAILABLE")
                            else:
                                committed = self._call(
                                    lambda token=token, candidate=candidate, prepared=prepared, day=day: (
                                        self._commit(token, candidate, prepared, day)
                                    )
                                )
                                if committed is True:
                                    self._set("READY", "BOUND")
                                else:
                                    self._set("DEGRADED", "SOURCE_CHANGED")
            self._wake.wait(self._settings.poll_interval_seconds)
            self._wake.clear()
