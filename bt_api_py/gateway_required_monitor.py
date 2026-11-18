"""Required Monitor heartbeat, isolated from the native and strategy threads.

The in-memory gate is also checked inside the command journal transaction, so
an owner blocked in provider code cannot extend a heartbeat's deadline.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.monitor_heartbeat_wire import (
    MONITOR_HEARTBEAT_OPERATIONS,
    build_monitor_heartbeat_request,
    monitor_heartbeat_ack_matches,
)

_LOG = logging.getLogger(__name__)

# Who released a Monitor cause when the Gateway did it itself on a valid heartbeat
# (automatic_resume), as stored on a journal with the release audit.
MONITOR_RECOVERY_RELEASER = "system:required-monitor"

# Startup observations are finite categories. A confirmed incompatible Monitor
# is terminal and refuses the Gateway start (D68-02). Only an authenticated
# current heartbeat is COMPATIBLE; a timeout, an unproven reply or an
# unclassifiable failure stays UNKNOWN and never releases the cause.
MONITOR_STARTUP_COMPATIBLE = "COMPATIBLE"
MONITOR_STARTUP_INCOMPATIBLE = "INCOMPATIBLE"
MONITOR_STARTUP_AUTH_FAILED = "AUTH_FAILED"
MONITOR_STARTUP_UNREACHABLE = "UNREACHABLE"
MONITOR_STARTUP_UNKNOWN = "UNKNOWN"
MONITOR_STARTUP_TIMEOUT = "TIMEOUT"

_STARTUP_TERMINAL = frozenset(
    {
        MONITOR_STARTUP_COMPATIBLE,
        MONITOR_STARTUP_INCOMPATIBLE,
        MONITOR_STARTUP_AUTH_FAILED,
    }
)
# Among provisional observations the most cautious one wins; none of them may
# be reported as COMPATIBLE.
_STARTUP_PROVISIONAL_RANK = {
    MONITOR_STARTUP_TIMEOUT: 0,
    MONITOR_STARTUP_UNREACHABLE: 1,
    MONITOR_STARTUP_UNKNOWN: 2,
}
_AUTH_FAILURE_CODES = frozenset(
    {"DENIED", "AUTHENTICATION_FAILED", "SCOPE_DENIED", "UNAUTHORIZED_RELAY"}
)
# Cumulative bound for the shutdown-phase cleanup retries. A Monitor that
# stays unreachable cannot hold the close forever: after this budget the
# worker exits while retaining the runtime, so the cleanup verdict stays
# explicitly uncertain instead of blocking the owner thread without limit.
_SHUTDOWN_CLEANUP_BUDGET_SECONDS = 60.0
# The frozen operation set is exactly one passive operation; the worker calls
# that name and nothing else.
if len(MONITOR_HEARTBEAT_OPERATIONS) != 1:
    raise RuntimeError("MONITOR_HEARTBEAT_OPERATION_SET_INVALID")
_HEARTBEAT_OPERATION = next(iter(MONITOR_HEARTBEAT_OPERATIONS))
# A request may be authenticated yet unproven: a handler failure, a reply that
# is not the exact ACK, or a lost local proof. None of these is reachability,
# compatibility or liveness evidence.
_UNKNOWN_CODES = frozenset(
    {
        "BUSINESS_OUTCOME_UNKNOWN",
        "HANDLER_FAILED",
        "INVALID_HEARTBEAT",
        "MONITOR_ACK_EXPIRED",
        "MONITOR_ACK_INVALID",
        "MONITOR_BINDING_UNAVAILABLE",
        "MONITOR_CAUSE_MISMATCH",
        "MONITOR_OWNER_CHANGED",
        "MONITOR_PEER_UNTRUSTED",
        "MONITOR_PROOF_EXPIRED",
        "MONITOR_RUNTIME_UNTRUSTED",
        "UNSUPPORTED_OPERATION",
        # Owner-queue contention is a local, retryable condition: it says the
        # native owner was busy, not that the Monitor is unreachable. Keeping
        # these out of the UNREACHABLE catch-all avoids a false reachability
        # verdict while the Gateway is doing a long venue query.
        "SERVICE_CALL_TIMEOUT_QUEUED",
        "SERVICE_CALL_TIMEOUT_RUNNING",
        "SERVICE_CALL_QUEUE_FULL",
        "SERVICE_CALL_NOT_READY",
    }
)


class _MonitorWorkerError(RuntimeError):
    """A finite local worker failure that is never a liveness proof."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _startup_category_for(error: BaseException) -> str:
    """Classify one failed heartbeat without ever claiming compatibility."""
    code = getattr(error, "code", "")
    if code in _AUTH_FAILURE_CODES:
        return MONITOR_STARTUP_AUTH_FAILED
    if code in _UNKNOWN_CODES:
        return MONITOR_STARTUP_UNKNOWN
    return MONITOR_STARTUP_UNREACHABLE



@dataclass(frozen=True)
class RequiredMonitorSettings:
    runtime_factory: Callable[[], object] = field(repr=False, compare=False)
    gateway_identity: WireIdentity
    monitor_identity: WireIdentity
    scope: WireScope
    mount: str = "upstream.telemetry"
    interval_seconds: float = 1.0
    timeout_seconds: float = 3.0
    automatic_resume: bool = True

    def __post_init__(self):
        if (not callable(self.runtime_factory) or type(self.gateway_identity) is not WireIdentity
                or self.gateway_identity.role is not WireRole.SERVICE
                or self.gateway_identity.delegate_ref is not None
                or type(self.monitor_identity) is not WireIdentity
                or self.monitor_identity.role is not WireRole.SERVICE
                or self.monitor_identity.delegate_ref is not None
                or type(self.scope) is not WireScope or self.scope.account_ref is None
                or self.scope != WireScope(self.scope.execution_domain_ref, account_ref=self.scope.account_ref)
                or self.mount != "upstream.telemetry"
                or type(self.automatic_resume) is not bool
                or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 < x <= 300
                       for x in (self.interval_seconds, self.timeout_seconds))
                or self.interval_seconds >= self.timeout_seconds):
            raise ValueError("INVALID_REQUIRED_MONITOR")


class _Gate:
    def __init__(self, generation):
        self._pid = os.getpid()
        self._generation = generation
        self._lock = threading.Lock()
        self._closed = False
        self._live = False
        self._deadline = 0
        self._proof = None
        self._owner = None

    def __call__(self):
        with self._lock:
            valid = (os.getpid() == self._pid and not self._closed and self._live
                     and time.monotonic_ns() < self._deadline)
            owner = self._owner
        if not valid or owner is None:
            return False
        try:
            binding = owner.binding()
            return binding is not None and binding.generation == self._generation and owner.status().thread_alive
        except Exception:
            return False

    def bind_owner(self, owner):
        with self._lock:
            if self._owner is not None or self._closed:
                raise RuntimeError("MONITOR_OWNER_CHANGED")
            self._owner = owner

    def revoke(self):
        with self._lock:
            self._live = False
            self._proof = None

    def close(self):
        with self._lock:
            self._closed, self._live, self._proof = True, False, None

    def acknowledge(self, proof, deadline):
        with self._lock:
            now = time.monotonic_ns()
            if self._closed or os.getpid() != self._pid or now >= deadline:
                return None
            gap = self._live and now >= self._deadline
            if gap:
                self._live = False
            self._proof, self._deadline = proof, deadline
            return gap

    def require_proof(self, proof):
        with self._lock:
            if (self._closed or os.getpid() != self._pid or self._proof != proof
                    or time.monotonic_ns() >= self._deadline):
                raise _MonitorWorkerError("MONITOR_PROOF_EXPIRED")

    def activate(self, proof):
        with self._lock:
            if (self._closed or os.getpid() != self._pid or self._proof != proof
                    or time.monotonic_ns() >= self._deadline):
                return False
            self._live = True
            return True


class RequiredMonitor:
    """One single-use heartbeat worker for one journal/owner generation."""

    def __init__(self, settings: RequiredMonitorSettings, generation: str):
        if type(settings) is not RequiredMonitorSettings or type(generation) is not str or not generation:
            raise ValueError("INVALID_REQUIRED_MONITOR")
        self.settings = settings
        self.generation = generation
        self.gate = _Gate(generation)
        encoded = (settings.scope.account_ref + "\0" + settings.monitor_identity.principal_ref
                   + "\0" + settings.monitor_identity.actor_ref).encode("utf-8")
        self.origin = "system:monitor:" + hashlib.sha256(encoded).hexdigest()
        self._stop = threading.Event()
        self._thread = None
        self._startup_event = threading.Event()
        self._startup_lock = threading.Lock()
        self._startup_category = None
        self._owner = self._control = None
        self._initial_cause = "monitor:" + uuid.uuid4().hex
        # Worker-local knowledge that the STOP_NEW outage cause is already
        # applied on the journal. It avoids re-submitting an owner-queued
        # persist call on every failed heartbeat while the Monitor is away:
        # each such call blocks the worker for the heartbeat timeout when the
        # native owner is busy, which slows the restart recovery loop.
        self._outage_persisted = False
        self._cleanup_uncertain = False
        self._cleanup_retry = threading.Event()
        self._diagnostic_lock = threading.Lock()
        self._diagnostic = ("STARTING", "NONE", "NONE")

    @property
    def diagnostic(self):
        """Finite worker state; it contains no account, route or exception text."""
        with self._diagnostic_lock:
            phase, failure, cleanup = self._diagnostic
            return {"phase": phase, "failure_category": failure, "cleanup_kind": cleanup}

    def _set_diagnostic(self, phase, failure, cleanup):
        value = (phase, failure, cleanup)
        with self._diagnostic_lock:
            if self._diagnostic == value:
                return
            self._diagnostic = value
        log = _LOG.warning if failure != "NONE" or cleanup not in {"NONE", "COMPLETE"} else _LOG.info
        log("Required Monitor phase=%s failure=%s cleanup=%s", *value)

    def initialize_on_owner(self, journal, control, guard):
        if journal.generation != self.generation or control._journal is not journal:
            raise ValueError("MONITOR_OWNER_MISMATCH")
        journal.install_monitor_gate(self.gate, guard=guard)
        control.apply(self._initial_cause, "STOP_NEW", scope_kind="account", origin=self.origin)

    def start(self, owner, control):
        if self._thread is not None or owner.binding() is None or owner.binding().generation != self.generation:
            raise ValueError("MONITOR_OWNER_MISMATCH")
        self._owner, self._control = owner, control
        self.gate.bind_owner(owner)
        self._thread = threading.Thread(target=self._run, name="gateway-required-monitor", daemon=True)
        self._thread.start()

    def startup_probe(self, timeout_seconds):
        """Return only a classified authenticated observation within the budget.

        A provisional observation (unreachable, unknown, timeout) never ends
        the probe: a confirmed incompatible Monitor seen later in the startup
        grace must still refuse the start (D68-02). Only a terminal category
        (``COMPATIBLE``, ``INCOMPATIBLE``, ``AUTH_FAILED``) ends it early.
        """
        if (
            self._thread is None
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise ValueError("INVALID_MONITOR_STARTUP")
        if not self._startup_event.wait(timeout_seconds):
            with self._startup_lock:
                return self._startup_category or MONITOR_STARTUP_TIMEOUT
        with self._startup_lock:
            return self._startup_category

    def _record_startup(self, category):
        """Record one observation; a provisional one must not mask a terminal one."""
        if category not in _STARTUP_TERMINAL and category not in _STARTUP_PROVISIONAL_RANK:
            raise ValueError("INVALID_MONITOR_STARTUP")
        with self._startup_lock:
            current = self._startup_category
            if category in _STARTUP_TERMINAL:
                if current in _STARTUP_TERMINAL:
                    return
                self._startup_category = category
                self._startup_event.set()
                return
            if current in _STARTUP_TERMINAL:
                return
            if current is None or (
                _STARTUP_PROVISIONAL_RANK[category] > _STARTUP_PROVISIONAL_RANK[current]
            ):
                self._startup_category = category

    def close(self, timeout_seconds):
        self.gate.close()
        self._stop.set()
        self._cleanup_retry.set()
        if self._thread is None:
            return True
        self._thread.join(timeout_seconds)
        return not self._thread.is_alive() and not self._cleanup_uncertain

    def _owner_call(self, callback):
        # The native owner may be completing one bounded venue query when the
        # heartbeat ACK arrives. Wait up to the heartbeat timeout (never less
        # than the historical 1 s) instead of declaring the Monitor unreachable
        # on a one-second queue wait.
        return self._owner.submit_service_call(callback).result(
            timeout_seconds=max(1.0, float(self.settings.timeout_seconds))
        )

    def _persist_outage(self):
        control, origin = self._control, self.origin
        def apply():
            if self.gate():
                return
            if not control.active_system_causes(origin):
                control.apply("monitor:" + uuid.uuid4().hex, "STOP_NEW", scope_kind="account", origin=origin)
        self._owner_call(apply)
        self._outage_persisted = True

    def _restore(self, proof):
        control, origin = self._control, self.origin
        def recheck():
            binding = self._owner.binding()
            if binding is None or binding.generation != self.generation:
                raise _MonitorWorkerError("MONITOR_OWNER_CHANGED")
            control._owned()
            self.gate.require_proof(proof)
        def restore():
            recheck()
            causes = control.active_system_causes(origin)
            if any(cause["mode"] != "STOP_NEW" or cause["scope_kind"] != "account"
                   or cause["scope_value"] != "" for cause in causes):
                raise _MonitorWorkerError("MONITOR_CAUSE_MISMATCH")
            if self.settings.automatic_resume:
                for cause in causes:
                    control.resume(cause["cause_id"], expected_version=cause["version"],
                        operation_id="monitor-resume:" + uuid.uuid4().hex,
                        before_commit=recheck, releaser=MONITOR_RECOVERY_RELEASER)
                causes = control.active_system_causes(origin)
            if not causes:
                self.gate.activate(proof)
                # The journal no longer holds this worker's outage cause (or it
                # was never applied); a later heartbeat gap must persist again.
                self._outage_persisted = False
        self._owner_call(restore)

    def _binding(self, runtime):
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory, _RequestNotSentAdapter
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient
        s = self.settings
        if (type(runtime) is not ServiceClientRuntime or type(runtime._factory) is not ZmqTransportFactory
                or runtime.identity != s.gateway_identity or runtime._monotonic_clock is not time.monotonic
                or not runtime.healthy):
            raise _MonitorWorkerError("MONITOR_RUNTIME_UNTRUSTED")
        matches = [b for b in runtime.bindings if b.active and b.mount == s.mount
                   and b.scope == s.scope and b.required
                   and MONITOR_HEARTBEAT_OPERATIONS <= b.operations]
        if len(matches) != 1:
            raise _MonitorWorkerError("MONITOR_BINDING_UNAVAILABLE")
        binding = matches[0]
        transport = runtime._transports.get((binding.service_id, binding.transport_ref))
        if (runtime._peers.get(binding.service_id) != s.monitor_identity
                or binding.service_id != s.monitor_identity.actor_ref
                or type(transport) is not _RequestNotSentAdapter
                or type(transport._transport) is not ZmqServiceClient
                or transport._transport._identity != s.monitor_identity):
            raise _MonitorWorkerError("MONITOR_PEER_UNTRUSTED")
        return binding

    def _precheck_runtime(self, runtime):
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory
        if (type(runtime) is not ServiceClientRuntime or type(runtime._factory) is not ZmqTransportFactory
                or runtime.identity != self.settings.gateway_identity
                or runtime._monotonic_clock is not time.monotonic
                or runtime._requested_operations.get(self.settings.mount)
                != MONITOR_HEARTBEAT_OPERATIONS):
            raise _MonitorWorkerError("MONITOR_RUNTIME_UNTRUSTED")

    def _confirmed_incompatible(self, runtime, error):
        from bt_api_py.service_runtime import ServiceRuntimeError
        marker = getattr(error, "confirmed_missing_monitor_heartbeat", None)
        missing = (type(marker) is tuple and len(marker) == 3
                and marker[0] == self.settings.monitor_identity.actor_ref
                and marker[2] == self.settings.monitor_identity
                and runtime is not None)
        if missing:
            return True
        if (type(error) is ServiceRuntimeError and runtime is not None
                and runtime._peers.get(self.settings.monitor_identity.actor_ref) == self.settings.monitor_identity):
            if error.code == "PROTOCOL_VERSION_MISMATCH":
                return True
            if error.code in {"UNKNOWN_OPERATION", "UNSUPPORTED_OPERATION"}:
                try:
                    self._binding(runtime)
                    return True
                except Exception:
                    return False
        return False

    def _run(self):
        runtime = None
        pending_outage = False
        retiring = False
        terminating = False
        shutdown_deadline = None
        # The native owner rebinding to another generation (or entering its
        # final cleanup) is permanent for this single-use worker: its journal
        # generation no longer exists, so no heartbeat retry can ever succeed.
        # The worker retires its Monitor session once and stops instead of
        # retrying every interval (which produced a per-second
        # MONITOR_OWNER_CHANGED failure storm during owner close/rebind).
        owner_detached = False
        failure_category = "NONE"
        try:
            while True:
                terminating = terminating or self._stop.is_set()
                if terminating or retiring:
                    if runtime is None:
                        if terminating or owner_detached:
                            break
                        retiring = False
                        continue
                    if terminating:
                        self._cleanup_retry.clear()
                    self._set_diagnostic("STOPPING" if terminating else "RECONCILING",
                                         failure_category, "REMOTE_PENDING")
                    try:
                        complete = runtime.retire_required_monitor_session()
                        if complete and (runtime.unregister_uncertainties != 0
                                         or runtime.unknown_registration_count != 0
                                         or runtime.open_transport_count != 0):
                            raise _MonitorWorkerError("MONITOR_CLEANUP_UNCERTAIN")
                    except Exception as error:
                        complete = False
                        code = getattr(error, "code", None)
                        cleanup = ("UNKNOWN_REGISTRATION" if code == "MONITOR_CLEANUP_REGISTRATION_UNKNOWN"
                                   or getattr(runtime, "unknown_registration_count", 0)
                                   else "LOCAL_PENDING" if code == "CLOSE_FAILED"
                                   else "CLEANUP_UNCERTAIN")
                    else:
                        cleanup = "COMPLETE" if complete else "REMOTE_PENDING"
                    self._cleanup_uncertain = not complete
                    self._set_diagnostic("STOPPING" if terminating else "RECONCILING",
                                         failure_category, cleanup)
                    if complete:
                        runtime = None
                        retiring = False
                        if terminating or owner_detached:
                            break
                        self._set_diagnostic("STARTING", failure_category, "COMPLETE")
                        continue
                    # On shutdown do one bounded attempt per explicit close;
                    # the same owner thread and resources survive for retry.
                    # The cumulative budget bounds the whole shutdown phase:
                    # an unreachable Monitor cannot block the close forever.
                    # After the budget the worker exits and the retained
                    # runtime keeps the cleanup verdict explicitly uncertain
                    # (see the finally block); nothing is reported as cleaned.
                    if terminating:
                        if shutdown_deadline is None:
                            shutdown_deadline = (
                                time.monotonic() + _SHUTDOWN_CLEANUP_BUDGET_SECONDS
                            )
                        remaining = shutdown_deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._cleanup_retry.wait(
                            timeout=min(self.settings.timeout_seconds, remaining)
                        )
                    else:
                        self._stop.wait(self.settings.interval_seconds)
                    continue
                try:
                    if runtime is None:
                        self._set_diagnostic("STARTING", failure_category, "NONE")
                        runtime = self.settings.runtime_factory()
                        self._precheck_runtime(runtime)
                        runtime.start()
                    runtime.maintain()
                    binding = self._binding(runtime)
                    owner_binding = self._owner.binding()
                    if owner_binding is None or owner_binding.generation != self.generation:
                        raise _MonitorWorkerError("MONITOR_OWNER_CHANGED")
                    sent = time.monotonic_ns()
                    deadline = sent + int(self.settings.timeout_seconds * 1_000_000_000)
                    heartbeat_id = uuid.uuid4().hex
                    # The frozen wire contract (bt_api_gateway.monitor_heartbeat_wire):
                    # an exact request field set, a versioned schema and an ACK that
                    # repeats it plus this Monitor's configured principal.
                    request = build_monitor_heartbeat_request(
                        relay_epoch=binding.registration_id,
                        heartbeat_id=heartbeat_id,
                        sent_at_ns=sent,
                    )
                    response = runtime.call(self.settings.mount, _HEARTBEAT_OPERATION,
                                            request,
                                            account_ref=self.settings.scope.account_ref,
                                            scope_ref=binding.scope_ref)
                    after = self._binding(runtime)
                    if (time.monotonic_ns() >= deadline or after.registration_id != binding.registration_id
                            or after.service_epoch != binding.service_epoch
                            or self._owner.binding() != owner_binding
                            or not monitor_heartbeat_ack_matches(
                                request, response,
                                self.settings.monitor_identity.principal_ref)):
                        raise _MonitorWorkerError("MONITOR_ACK_INVALID")
                    self._record_startup(MONITOR_STARTUP_COMPATIBLE)
                    proof = (self.generation, binding.registration_id, binding.service_epoch, heartbeat_id)
                    gap = self.gate.acknowledge(proof, deadline)
                    if gap is None:
                        raise _MonitorWorkerError("MONITOR_ACK_EXPIRED")
                    if gap or pending_outage:
                        self._persist_outage()
                        pending_outage = False
                    self._restore(proof)
                    failure_category = "NONE"
                    self._set_diagnostic("MONITORING", "NONE", "NONE")
                except Exception as error:
                    _LOG.warning(
                        "Required Monitor heartbeat failed: code=%s type=%s",
                        getattr(error, "code", None), type(error).__name__,
                    )
                    self.gate.revoke()
                    pending_outage = True
                    # An already-persisted outage cause must not be re-submitted
                    # through the congested owner queue on every failed
                    # heartbeat: each attempt blocks this worker for the
                    # heartbeat timeout and delays the recovery loop while the
                    # Monitor is restarting. The revoked in-memory gate keeps
                    # the journal fail-closed in the meantime.
                    if not self._outage_persisted:
                        try:
                            self._persist_outage()
                            pending_outage = False
                        except Exception:
                            pass
                    if self._confirmed_incompatible(runtime, error):
                        self._record_startup(MONITOR_STARTUP_INCOMPATIBLE)
                        failure_category = "INCOMPATIBLE"
                        terminating = True
                        continue
                    failure_category = _startup_category_for(error)
                    self._record_startup(failure_category)
                    if getattr(error, "code", None) == "MONITOR_OWNER_CHANGED":
                        # Permanent for this worker (see owner_detached): stop
                        # heartbeating and retire the Monitor session once.
                        owner_detached = True
                        retiring = True
                    elif runtime is not None and (
                        runtime.state != "registered" or getattr(error, "code", None) in {
                            "STALE_EPOCH", "NOT_REGISTERED", "LEASE_EXPIRED",
                            "REQUIRED_LEASE_EXPIRED", "SESSION_NOT_REGISTERED",
                        }
                    ):
                        retiring = True
                    self._set_diagnostic("RECONCILING" if retiring else "MONITORING",
                                         failure_category, "REMOTE_PENDING" if retiring else "NONE")
                self._stop.wait(self.settings.interval_seconds)
        finally:
            self.gate.close()
            self._record_startup(MONITOR_STARTUP_UNREACHABLE)
            if runtime is not None:
                # Never destroy an unconfirmed old registration here. Retain
                # the same object instead of inventing successful cleanup.
                self._cleanup_uncertain = True
                self._retained_runtime = runtime
                self._set_diagnostic("BLOCKED", failure_category, "CLEANUP_UNCERTAIN")
            else:
                self._set_diagnostic("STOPPED", failure_category, "COMPLETE")
