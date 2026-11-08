"""Volatile, registration-bound CTP instrument queries on the existing owner.

A ready reply represents an owner-thread validation completed during this
status cycle, not an atomic assertion about the later socket transmission.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireRole
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_gateway.service_component import GatewayReadService

from bt_api_py._contracts.models import Freshness, InstrumentSpec
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.gateway_instrument_spec_codec import encode_gateway_instrument_spec
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    ServiceResultTooLargeError,
)

INSTRUMENT_OPERATIONS = frozenset({"account.instrument_spec.begin", "account.instrument_spec.status", "account.instrument_spec.cancel"})
_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)


@dataclass
class _Job:
    context: ServiceCallContext
    operation_id: str
    instrument: MarketInstrumentKey | None
    created: float
    state: str = "pending"
    code: str | None = None
    terminal_at: float | None = None
    reader: object = None
    handle: object = None
    observation: object = None
    call: object = None
    action: str | None = None
    check_requested: bool = False
    checked_at: float | None = None
    cancel_requested: bool = False


class GatewayInstrumentService:
    """A single volatile query service using an exact CTP session and read ACL."""

    def __init__(self, owner, session, journal, read_service, universe, principals):
        if (type(owner) is not GatewayDirectOwner or type(session) is not CtpGatewaySession
                or type(read_service) is not GatewayReadService or not owner.is_bound_to(journal)
                or owner.binding() is None or owner.binding().account_scope != journal.account_scope
                or session._owner_thread is not owner._thread
                or session._account_key.account_key_id != journal.account_scope
                or session._service_generation != owner.binding().generation
                or set(read_service.accounts) != {journal.account_scope}
                or type(universe) is not tuple or not universe
                or any(type(key) is not MarketInstrumentKey for key in universe)
                or len(set(universe)) != len(universe)
                or type(principals) is not frozenset or not principals
                or any(type(p) is not str or not p for p in principals)):
            raise ValueError("INVALID_INSTRUMENT_SERVICE")
        self._owner, self._session, self._journal, self._read = owner, session, journal, read_service
        self._generation = owner.binding().generation
        self._universe, self._principals = frozenset(universe), principals
        self._lock = threading.RLock()
        self._jobs = {}
        self._closed = False
        self._cursor = 0
        owner.claim_service_port("instrument_spec", self)

    def matches(self, binding, read_service):
        return (getattr(binding, "_owner", None) is self._owner
                and getattr(binding, "_journal", None) is self._journal
                and read_service is self._read and self._owner.is_bound_to(self._journal))

    @staticmethod
    def _same(a, b):
        return (a.registration_id == b.registration_id and a.principal_ref == b.principal_ref
                and a.identity == b.identity and a.scope == b.scope)

    def _authorize(self, context):
        if (type(context) is not ServiceCallContext or context.principal_ref not in self._principals
                or context.identity.role is not WireRole.STRATEGY_CLIENT
                or context.identity.principal_ref != context.principal_ref
                or context.identity.actor_ref != context.scope.strategy_ref
                or context.identity.delegate_ref is not None
                or not context.scope.run_id or context.scope.account_ref != self._journal.account_scope):
            raise ValueError("SCOPE_DENIED")
        self._read.handle("account.head", principal_ref=context.principal_ref,
                          identity=context.identity, scope=context.scope, arguments={})

    def _reply(self, context, job, operation_id):
        state, code, snapshot = ("not_found", None, None) if job is None else (job.state, job.code, None)
        if job is not None and state == "ready":
            obs = job.observation
            remaining = obs.expires_monotonic - time.monotonic()
            if remaining <= 0 or self._owner.binding() is None or self._owner.binding().generation != self._generation:
                self._finish(job, "failed", "SOURCE_EXPIRED")
                state, code = job.state, job.code
            elif job.checked_at is None:
                job.check_requested = True
                state = "pending"
            else:
                spec = InstrumentSpec(**dict(obs.rules), freshness=Freshness("live", obs.completed_at_utc, False), raw={})
                snapshot = {"instrument": job.instrument.to_dict(), "spec": encode_gateway_instrument_spec(spec),
                            "session_generation": self._generation,
                            "connection_generation": obs.connection_generation, "trading_day": obs.trading_day,
                            "remaining_seconds": remaining}
        response = {"operation_id": operation_id, "status": state, "code": code, "snapshot": snapshot}
        self._check_reply_budget(context, response)
        if snapshot is not None:
            # The owner check may have completed before a later reconnect.
            # This is a nonblocking, read-only provider fence, not an atomic
            # guarantee about subsequent transport publication.
            try:
                current = job.reader.observation_current(obs)
            except Exception:
                current = False
            if current is None:
                job.checked_at = None
                job.check_requested = True
                response = {"operation_id": operation_id, "status": "pending",
                            "code": None, "snapshot": None}
                self._check_reply_budget(context, response)
                return response
            if current is not True:
                self._finish(job, "failed", "SOURCE_UNAVAILABLE")
                response = {"operation_id": operation_id, "status": "failed",
                            "code": job.code, "snapshot": None}
                self._check_reply_budget(context, response)
                return response
            remaining = obs.expires_monotonic - time.monotonic()
            if remaining <= 0:
                self._finish(job, "failed", "SOURCE_EXPIRED")
                response = {"operation_id": operation_id, "status": "failed",
                            "code": job.code, "snapshot": None}
                self._check_reply_budget(context, response)
                return response
            snapshot["remaining_seconds"] = remaining
            self._check_reply_budget(context, response)
            job.checked_at = None  # each release consumes one fresh validation cycle
        return response

    @staticmethod
    def _check_reply_budget(context, response):
        if not callable(context.result_fits):
            raise RuntimeError("INVALID_RESULT_BUDGET")
        fits = context.result_fits(response)
        if type(fits) is not bool:
            raise RuntimeError("INVALID_RESULT_BUDGET")
        if not fits:
            raise ServiceResultTooLargeError()

    @staticmethod
    def _finish(job, state, code=None):
        job.state, job.code = state, code
        if job.terminal_at is None:
            job.terminal_at = time.monotonic()
        job.checked_at = None

    def _has_capacity(self, registration_id, *, pending):
        registered = [job for (reg, _), job in self._jobs.items() if reg == registration_id]
        if len(self._jobs) >= 256 or len(registered) >= 64:
            return False
        return not pending or (
            sum(job.state == "pending" for job in self._jobs.values()) < 64
            and sum(job.state == "pending" for job in registered) < 8
        )

    def handle(self, operation, *, context, arguments):
        self._authorize(context)
        if (operation not in INSTRUMENT_OPERATIONS or not isinstance(arguments, Mapping)
                or set(arguments) != ({"operation_id", "instrument"} if operation.endswith(".begin") else {"operation_id"})
                or type(arguments.get("operation_id")) is not str or not _ID.fullmatch(arguments["operation_id"])):
            raise ValueError("INVALID_REQUEST")
        operation_id = arguments["operation_id"]
        instrument = None
        if operation.endswith(".begin"):
            instrument = MarketInstrumentKey.from_dict(arguments["instrument"])
            if instrument not in self._universe:
                raise ValueError("INSTRUMENT_DENIED")
        key = (context.registration_id, operation_id)
        with self._lock:
            if self._closed:
                raise ValueError("SERVICE_CLOSED")
            job = self._jobs.get(key)
            if job is not None and not self._same(job.context, context):
                raise ValueError("SCOPE_DENIED")
            if operation.endswith(".begin"):
                if job is not None and job.instrument is not None and job.instrument != instrument:
                    raise ValueError("OPERATION_CONFLICT")
                if job is None:
                    if not self._has_capacity(context.registration_id, pending=True):
                        raise ValueError("CAPACITY_EXCEEDED")
                    job = _Job(context, operation_id, instrument, time.monotonic())
                    # Check before enqueuing or storing any new job.
                    self._reply(context, job, operation_id)
                    self._jobs[key] = job
            elif operation.endswith(".cancel"):
                if job is None:
                    if not self._has_capacity(context.registration_id, pending=False):
                        raise ValueError("CAPACITY_EXCEEDED")
                    job = _Job(context, operation_id, None, time.monotonic())
                    self._finish(job, "cancelled")
                    self._reply(context, job, operation_id)
                    self._jobs[key] = job
                elif job.state not in {"cancelled", "failed"}:
                    response = {"operation_id": operation_id, "status": "cancelled",
                                "code": None, "snapshot": None}
                    self._check_reply_budget(context, response)
                    job.cancel_requested = True
                    self._finish(job, "cancelled")
                    return response
            return self._reply(context, job, operation_id)

    def _perform(self, job, action):
        if action == "begin":
            reader = self._session.create_instrument_query_reader()
            handle = reader.begin(job.instrument, timeout_seconds=5.0)
            # Keep custody even if owner shutdown drops the completed service
            # callback before Registry maintenance consumes its result.
            with self._lock:
                job.reader, job.handle = reader, handle
            return reader, handle
        reader = job.reader
        if action == "poll":
            return reader.poll(job.handle)
        if action == "check":
            reader.assert_current(job.observation)
            return time.monotonic()
        if action == "cleanup":
            # Both operations only attempt the provider's local state lock;
            # false means retry, not native cancellation or completed cleanup.
            reader.cancel(job.handle)
            return reader.release(job.handle)
        if action == "release":
            return reader.release(job.handle)
        raise RuntimeError("INVALID_ACTION")

    def maintain_registrations(self, contexts):
        active = {item.context.registration_id: item for item in contexts
                  if type(item) is ActiveRegistrationContext and item.port == "service.account_read"
                  and item.operations >= INSTRUMENT_OPERATIONS}
        with self._lock:
            jobs = list(self._jobs.items())
            submitted = 0
            if jobs:
                offset = self._cursor % len(jobs)
                jobs = jobs[offset:] + jobs[:offset]
                self._cursor += 1
            for key, job in jobs:
                current = active.get(key[0])
                if current is None or not self._same(job.context, current.context):
                    job.cancel_requested = True
                    if job.state not in {"failed", "cancelled"}:
                        self._finish(job, "cancelled")
                if job.state == "pending" and time.monotonic() - job.created >= 5:
                    self._finish(job, "failed", "QUERY_TIMEOUT")
                    job.cancel_requested = True
                if job.call is not None:
                    if not job.call.done:
                        if job.cancel_requested:
                            job.call.cancel()
                        continue
                    action = job.action
                    try:
                        result = job.call.result(timeout_seconds=0)
                        if action == "begin":
                            job.reader, job.handle = result
                        elif action == "poll" and result is not None:
                            job.observation = result
                            if not job.cancel_requested:
                                self._finish(job, "ready")
                        elif action == "check":
                            job.checked_at = result
                        elif action in {"release", "cleanup"} and result is True:
                            job.handle = None
                    except Exception:
                        if not job.cancel_requested:
                            self._finish(job, "failed", "SOURCE_UNAVAILABLE")
                        job.cancel_requested = True
                    job.call = job.action = None
                if job.observation is not None and time.monotonic() >= job.observation.expires_monotonic and job.state == "ready":
                    self._finish(job, "failed", "SOURCE_EXPIRED")
                if job.handle is not None and (job.cancel_requested or job.state == "failed"):
                    action = "cleanup"
                elif job.state == "ready" and job.check_requested and job.checked_at is None:
                    action = "check"
                elif job.handle is not None and job.state == "ready":
                    action = "release"
                elif job.state == "pending":
                    action = "begin" if job.handle is None else "poll"
                else:
                    action = None
                if action == "check":
                    job.check_requested = False
                if action is not None and submitted < 8:
                    try:
                        job.action = action
                        job.call = self._owner.submit_service_call(lambda j=job, a=action: self._perform(j, a))
                        submitted += 1
                    except GatewayOwnerServiceCallError as error:
                        job.action = job.call = None
                        if error.code == "SERVICE_CALL_QUEUE_FULL":
                            if action == "check":
                                job.check_requested = True
                        elif not job.cancel_requested:
                            self._finish(job, "failed", "SOURCE_UNAVAILABLE")
                            job.cancel_requested = True
                elif action == "check":
                    job.check_requested = True
                if (job.terminal_at is not None and time.monotonic() - job.terminal_at >= 60
                        and job.handle is None and job.call is None):
                    del self._jobs[key]

    def begin_close(self):
        with self._lock:
            self._closed = True
            for job in self._jobs.values():
                job.cancel_requested = True
                if job.state not in {"failed", "cancelled"}:
                    self._finish(job, "cancelled")

    def cleanup_on_owner(self):
        """Attempt nonblocking local handle cleanup before the session closes.

        Unconfirmed handles remain retained on their jobs; session shutdown
        still owns the native transport and its outstanding requests.
        """
        confirmed = True
        with self._lock:
            for job in self._jobs.values():
                if job.handle is None:
                    continue
                try:
                    if self._perform(job, "cleanup") is True:
                        job.handle = None
                    else:
                        confirmed = False
                except Exception:
                    confirmed = False
        return confirmed
