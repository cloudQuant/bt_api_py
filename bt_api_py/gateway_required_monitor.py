"""Required Monitor heartbeat, isolated from the native and strategy threads.

The in-memory gate is also checked inside the command journal transaction, so
an owner blocked in provider code cannot extend a heartbeat's deadline.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

# Who released a Monitor cause when the Gateway did it itself on a valid heartbeat
# (automatic_resume), as stored on a journal with the release audit.
MONITOR_RECOVERY_RELEASER = "system:required-monitor"


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
                raise RuntimeError("MONITOR_PROOF_EXPIRED")

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
        self._startup_category = None
        self._owner = self._control = None
        self._initial_cause = "monitor:" + uuid.uuid4().hex
        self._cleanup_uncertain = False

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
        """Return only a classified authenticated observation within the budget."""
        if self._thread is None or type(timeout_seconds) not in (int, float) or timeout_seconds < 0:
            raise ValueError("INVALID_MONITOR_STARTUP")
        if not self._startup_event.wait(timeout_seconds):
            return "TIMEOUT"
        return self._startup_category

    def _record_startup(self, category):
        if not self._startup_event.is_set():
            self._startup_category = category
            self._startup_event.set()

    def close(self, timeout_seconds):
        self.gate.close()
        self._stop.set()
        if self._thread is None:
            return True
        self._thread.join(timeout_seconds)
        return not self._thread.is_alive() and not self._cleanup_uncertain

    def _owner_call(self, callback):
        return self._owner.submit_service_call(callback).result(timeout_seconds=1.0)

    def _persist_outage(self):
        control, origin = self._control, self.origin
        def apply():
            if self.gate():
                return
            if not control.active_system_causes(origin):
                control.apply("monitor:" + uuid.uuid4().hex, "STOP_NEW", scope_kind="account", origin=origin)
        self._owner_call(apply)

    def _restore(self, proof):
        control, origin = self._control, self.origin
        def recheck():
            binding = self._owner.binding()
            if binding is None or binding.generation != self.generation:
                raise RuntimeError("MONITOR_OWNER_CHANGED")
            control._owned()
            self.gate.require_proof(proof)
        def restore():
            recheck()
            causes = control.active_system_causes(origin)
            if any(cause["mode"] != "STOP_NEW" or cause["scope_kind"] != "account"
                   or cause["scope_value"] != "" for cause in causes):
                raise RuntimeError("MONITOR_CAUSE_MISMATCH")
            if self.settings.automatic_resume:
                for cause in causes:
                    control.resume(cause["cause_id"], expected_version=cause["version"],
                        operation_id="monitor-resume:" + uuid.uuid4().hex,
                        before_commit=recheck, releaser=MONITOR_RECOVERY_RELEASER)
                causes = control.active_system_causes(origin)
            if not causes:
                self.gate.activate(proof)
        self._owner_call(restore)

    def _binding(self, runtime):
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory, _RequestNotSentAdapter
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient
        s = self.settings
        if (type(runtime) is not ServiceClientRuntime or type(runtime._factory) is not ZmqTransportFactory
                or runtime.identity != s.gateway_identity or runtime._monotonic_clock is not time.monotonic
                or not runtime.healthy):
            raise RuntimeError("MONITOR_RUNTIME_UNTRUSTED")
        matches = [b for b in runtime.bindings if b.active and b.mount == s.mount
                   and b.scope == s.scope and b.required and "monitor.heartbeat" in b.operations]
        if len(matches) != 1:
            raise RuntimeError("MONITOR_BINDING_UNAVAILABLE")
        binding = matches[0]
        transport = runtime._transports.get((binding.service_id, binding.transport_ref))
        if (runtime._peers.get(binding.service_id) != s.monitor_identity
                or binding.service_id != s.monitor_identity.actor_ref
                or type(transport) is not _RequestNotSentAdapter
                or type(transport._transport) is not ZmqServiceClient
                or transport._transport._identity != s.monitor_identity):
            raise RuntimeError("MONITOR_PEER_UNTRUSTED")
        return binding

    def _precheck_runtime(self, runtime):
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory
        if (type(runtime) is not ServiceClientRuntime or type(runtime._factory) is not ZmqTransportFactory
                or runtime.identity != self.settings.gateway_identity
                or runtime._monotonic_clock is not time.monotonic
                or runtime._requested_operations.get(self.settings.mount) != frozenset({"monitor.heartbeat"})):
            raise RuntimeError("MONITOR_RUNTIME_UNTRUSTED")

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
            if error.code == "UNKNOWN_OPERATION":
                try:
                    self._binding(runtime)
                    return True
                except Exception:
                    return False
        return False

    def _run(self):
        runtime = None
        pending_outage = False
        try:
            while not self._stop.is_set():
                try:
                    if runtime is None:
                        runtime = self.settings.runtime_factory()
                        self._precheck_runtime(runtime)
                        runtime.start()
                    runtime.maintain()
                    binding = self._binding(runtime)
                    owner_binding = self._owner.binding()
                    if owner_binding is None or owner_binding.generation != self.generation:
                        raise RuntimeError("MONITOR_OWNER_CHANGED")
                    sent = time.monotonic_ns()
                    deadline = sent + int(self.settings.timeout_seconds * 1_000_000_000)
                    heartbeat_id = uuid.uuid4().hex
                    request = {"schema_version": 1, "relay_epoch": binding.registration_id,
                               "heartbeat_id": heartbeat_id, "sent_at_ns": sent}
                    response = runtime.call(self.settings.mount, "monitor.heartbeat", request,
                                            account_ref=self.settings.scope.account_ref,
                                            scope_ref=binding.scope_ref)
                    after = self._binding(runtime)
                    if (time.monotonic_ns() >= deadline or after.registration_id != binding.registration_id
                            or after.service_epoch != binding.service_epoch
                            or self._owner.binding() != owner_binding
                            or not isinstance(response, Mapping)
                            or dict(response) != {**request, "monitor_principal": self.settings.monitor_identity.principal_ref}):
                        raise RuntimeError("MONITOR_ACK_INVALID")
                    self._record_startup("COMPATIBLE")
                    proof = (self.generation, binding.registration_id, binding.service_epoch, heartbeat_id)
                    gap = self.gate.acknowledge(proof, deadline)
                    if gap is None:
                        raise RuntimeError("MONITOR_ACK_EXPIRED")
                    if gap or pending_outage:
                        self._persist_outage()
                        pending_outage = False
                    self._restore(proof)
                except Exception as error:
                    self.gate.revoke()
                    pending_outage = True
                    try:
                        self._persist_outage()
                        pending_outage = False
                    except Exception:
                        pass
                    if self._confirmed_incompatible(runtime, error):
                        self._record_startup("INCOMPATIBLE")
                        break
                    code = getattr(error, "code", "")
                    self._record_startup("AUTH_FAILED" if code in {"DENIED", "AUTHENTICATION_FAILED"} else "UNREACHABLE")
                    if runtime is not None and (
                        runtime.state != "registered" or getattr(error, "code", None) in {
                            "STALE_EPOCH", "NOT_REGISTERED", "LEASE_EXPIRED",
                            "REQUIRED_LEASE_EXPIRED", "SESSION_NOT_REGISTERED",
                        }
                    ):
                        try:
                            runtime.close()
                            if runtime.unregister_uncertainties or runtime.unknown_registration_count:
                                self._cleanup_uncertain = True
                                break
                            runtime = None
                        except Exception:
                            self._cleanup_uncertain = True
                            break
                self._stop.wait(self.settings.interval_seconds)
        finally:
            self.gate.close()
            self._record_startup("UNREACHABLE")
            if runtime is not None:
                try:
                    runtime.close()
                    if runtime.unregister_uncertainties:
                        self._cleanup_uncertain = True
                except BaseException:
                    self._cleanup_uncertain = True
