"""Owner-thread wrapper for one service client and its registrations.

The factory is trusted application wiring and is invoked only by the worker.
A timeout after dispatch is an unknown outcome; callers must use the original
request identity to resolve it and must not automatically resend it.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from bt_api_base.service_protocol import WireIdentity, WireScope

from bt_api_py.service_runtime import (
    RegisteredBinding, ServiceClientRuntime, ServiceRuntimeError,
    _bound_binding_values, _bound_runtime_values,
)

# Permanent in-process roots for uncertain resources, including when a host
# callback fails or an owner thread is interrupted. Never cleared here.
_STICKY_CLEANUP_ROOTS: list[Any] = []
_STICKY_CLEANUP_ROOTS_LOCK = threading.Lock()
# Deadlines use the system clock rather than the externally supplied lease
# clock, which may reenter this runtime.
_NATIVE_MONOTONIC = time.monotonic
_DEADLINE_CLOCK = _NATIVE_MONOTONIC


class ThreadedRuntimeError(RuntimeError):
    """Finite wrapper diagnostic; no transport or remote error text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class OwnerOperationError(RuntimeError):
    """Finite diagnostic deliberately emitted by a trusted owner extension."""

    def __init__(self, code: str):
        if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None:
            raise ValueError("INVALID_OWNER_ERROR_CODE")
        self.code = code
        super().__init__(code)


class _StickyCleanupSignal(BaseException):
    """Transfer an owner observation to the finally block without cleanup."""

    def __init__(self, cause: BaseException):
        self.cause = cause


class OwnerRuntimeExtension(Protocol):
    """Trusted extension whose entire lifecycle runs on the runtime owner."""

    def start(self, runtime: Any) -> None: ...
    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any: ...
    def close(self) -> None: ...

    @property
    def cleanup_uncertainties(self) -> int: ...


@dataclass(frozen=True)
class OwnerOperationContinuation:
    """A trusted owner's next bounded step, never a wire or JSON value."""

    resume: Callable[[], Any]
    delay_seconds: float = 0.01

    def __post_init__(self) -> None:
        if (
            not callable(self.resume)
            or type(self.delay_seconds) not in (int, float)
            or not 0 < self.delay_seconds <= 60
            or not math.isfinite(self.delay_seconds)
        ):
            raise ValueError("INVALID_OWNER_CONTINUATION")


@dataclass(frozen=True)
class BindingSnapshot:
    registration_id: str
    service_epoch: str
    active: bool
    mount: str | None = None
    service_id: str | None = None
    instance_id: str | None = None
    scope_ref: str | None = None
    scope: WireScope | None = None
    operations: frozenset[str] | None = None
    required: bool | None = None
    authenticated_peer: WireIdentity | None = None
    server_key_fingerprint: str | None = None


@dataclass(frozen=True)
class RuntimeSnapshot:
    state: str
    thread_alive: bool
    healthy: bool
    error_code: str | None
    bindings: tuple[BindingSnapshot, ...]
    cleanup_failures: int | None
    unregister_uncertainties: int | None
    identity: WireIdentity | None = None
    run_id: str | None = None
    owner_cleanup_uncertainties: int | None = 0
    # Appended to preserve older positional construction; missing evidence is unknown.
    unknown_registration_count: int | None = None
    strict_unregister_ack: bool | None = None


class _Call:
    def __init__(self, mount: str, operation: str, arguments: Any, kwargs: dict[str, Any]):
        self.mount, self.operation, self.arguments, self.kwargs = (
            mount,
            operation,
            arguments,
            kwargs,
        )
        self.state = "queued"
        self.result: Any = None
        self.error: Exception | None = None


@dataclass(frozen=True)
class BoundCallRoute:
    """Local routing expectations, not a grant or proof of account authority."""

    identity: WireIdentity
    scope: WireScope
    scope_ref: str
    service_id: str
    instance_id: str
    peer: WireIdentity

    def __post_init__(self) -> None:
        if type(self.identity) is not WireIdentity or type(self.scope) is not WireScope or type(self.peer) is not WireIdentity:
            raise ValueError("INVALID_BOUND_ROUTE")
        try:
            identity = WireIdentity(**vars(self.identity))
            scope = WireScope(**vars(self.scope))
            peer = WireIdentity(**vars(self.peer))
            for value in (self.scope_ref, self.service_id, self.instance_id):
                if (type(value) is not str or not value or value != value.strip()
                        or not value.isprintable() or len(value.encode("utf-8", errors="strict")) > 256):
                    raise ValueError
        except Exception:
            raise ValueError("INVALID_BOUND_ROUTE") from None
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "peer", peer)


@dataclass(frozen=True)
class BoundCallObservation:
    identity: WireIdentity
    run_id: str
    scope: WireScope
    registration_id: str
    service_epoch: str
    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    operations: frozenset[str]
    peer: WireIdentity
    fingerprint: str
    lease_deadline: float
    active: bool
    required: bool
    operation: str


@dataclass(frozen=True)
class BoundCallResult:
    """Result and observations from one owner execution, not an authority token."""

    result: Any
    before: BoundCallObservation
    after: BoundCallObservation
    _binding_token: Any = None


_BOUND_BINDING_ISSUER = object()


@dataclass(frozen=True, eq=False)
class _BoundBindingToken:
    """Owner-local continuity evidence, not a remote authorization token."""
    issuer: object
    runtime: object
    binding: object
    transport: object
    binding_values: tuple
    runtime_values: tuple
    observation: BoundCallObservation
    lineage: object = None


def _copy_bound_observation(observation: BoundCallObservation) -> BoundCallObservation:
    if type(observation) is not BoundCallObservation:
        raise ValueError("INVALID_EXPECTED_BINDING")
    return replace(observation,
        identity=WireIdentity(**vars(observation.identity)),
        scope=WireScope(**vars(observation.scope)),
        peer=WireIdentity(**vars(observation.peer)))


class _BoundCall(_Call):
    def __init__(self, mount: str, operation: str, arguments: Any, kwargs: dict[str, Any], route: BoundCallRoute,
                 expected_binding: BoundCallObservation | None = None, expected_token: object = None):
        super().__init__(mount, operation, arguments, kwargs)
        self.route = route
        if expected_binding is not None:
            expected_binding = _copy_bound_observation(expected_binding)
        self.expected_binding = expected_binding
        self.expected_token = expected_token
        self.original_expected_token = expected_token
        self.raw_started = False
        self.original_target = (mount, operation, kwargs["account_ref"], kwargs["portfolio_ref"],
                                kwargs["scope_ref"], kwargs["max_reply_message_bytes"], BoundCallRoute(**vars(route)))
        self.original_arguments = _bound_wire_signature(arguments)
        self.original_expected_binding = (None if expected_binding is None
                                          else _copy_bound_observation(expected_binding))


@dataclass(frozen=True)
class _BoundOwnerResult:
    result: Any
    binding: RegisteredBinding
    before: BoundCallObservation
    binding_values: tuple
    runtime_values: tuple
    transport: object
    lineage: object = None


def _bound_ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _bound_wire_signature(value: Any) -> tuple:
    """Compare copied wire data without bool/int or signed-zero aliasing."""
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("BOUND_ROUTE_UNAVAILABLE")
        return ("float", value.hex())
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return ("str", value)
    if type(value) is list:
        return ("list", tuple(_bound_wire_signature(item) for item in value))
    if type(value) is dict and all(type(key) is str for key in value):
        for key in value:
            key.encode("utf-8", errors="strict")
        return ("dict", tuple((key, _bound_wire_signature(value[key])) for key in sorted(value)))
    raise ValueError("BOUND_ROUTE_UNAVAILABLE")


def _check_bound_call(call: _BoundCall) -> None:
    if (type(call) is not _BoundCall or type(call.kwargs) is not dict
            or set(call.kwargs) != {"account_ref", "portfolio_ref", "scope_ref", "max_reply_message_bytes"}
            or not _bound_ref(call.mount) or not _bound_ref(call.operation)
            or type(call.route) is not BoundCallRoute
            or call.expected_binding != call.original_expected_binding
            or call.expected_token is not call.original_expected_token):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    target = (call.mount, call.operation, call.kwargs["account_ref"], call.kwargs["portfolio_ref"],
              call.kwargs["scope_ref"], call.kwargs["max_reply_message_bytes"], BoundCallRoute(**vars(call.route)))
    if target != call.original_target or _bound_wire_signature(call.arguments) != call.original_arguments:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")


def _bound_observation(runtime: ServiceClientRuntime, call: _BoundCall,
                       expected_binding: RegisteredBinding | None = None
                       ) -> tuple[RegisteredBinding, BoundCallObservation, tuple, tuple, object]:
    """Read owner-local routing state directly, never a cached RuntimeSnapshot."""
    if type(runtime) is not ServiceClientRuntime or type(call.route) is not BoundCallRoute:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    _check_bound_call(call)
    runtime._assert_owner_thread()
    if (runtime._observation_owner_pid != os.getpid()
            or runtime._observation_owner_thread is not threading.current_thread()
            or runtime._monotonic_clock is not _NATIVE_MONOTONIC
            or runtime.state != "registered"):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    route = call.route
    # Use exactly the selection predicates in ServiceClientRuntime.call; an
    # extra candidate must not be hidden by the stricter expected-route filter.
    matches = [b for b in runtime.bindings if b.active and b.mount == call.mount
               and call.operation in b.operations
               and (call.kwargs["account_ref"] is None or b.scope.account_ref == call.kwargs["account_ref"])
               and (call.kwargs["portfolio_ref"] is None or b.scope.portfolio_ref == call.kwargs["portfolio_ref"])
               and (call.kwargs["scope_ref"] is None or b.scope_ref == call.kwargs["scope_ref"])]
    if len(matches) != 1 or type(matches[0]) is not RegisteredBinding:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    binding = matches[0]
    original_binding = _bound_binding_values(binding)
    original_runtime = _bound_runtime_values(runtime)
    if expected_binding is not None and binding is not expected_binding:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    if (binding.active is not True or binding.required is not True
            or binding.remote_lease_inactive is not False
            or binding.service_id != route.service_id or binding.instance_id != route.instance_id
            or binding.scope_ref != route.scope_ref or type(binding.scope) is not WireScope
            or type(binding.operations) is not frozenset
            or any(type(value) is not str for value in binding.operations)
            or not all(_bound_ref(value) for value in (runtime.run_id, binding.registration_id,
                                                        binding.service_epoch, binding.mount,
                                                        binding.service_id, binding.instance_id,
                                                        binding.scope_ref))):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    identity = WireIdentity(**vars(runtime.identity)) if type(runtime.identity) is WireIdentity else None
    scope = WireScope(**vars(binding.scope))
    if identity != route.identity or scope != route.scope:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    proof = runtime.authenticated_peer_for_binding(binding)
    if (type(proof) is not tuple or len(proof) != 2 or type(proof[0]) is not WireIdentity
            or type(proof[1]) is not str or re.fullmatch(r"[0-9a-f]{64}", proof[1]) is None):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    peer = WireIdentity(**vars(proof[0]))
    transport = runtime._transports.get((binding.service_id, binding.transport_ref))
    proof_key = (binding.service_id, binding.transport_ref, binding.registration_id, binding.service_epoch)
    if (peer != route.peer or transport is None or runtime._peers.get(binding.service_id) != peer
            or runtime._authenticated_registration_peers.get(proof_key) != (peer, proof[1])):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    deadline = binding.lease_deadline
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    now = runtime._monotonic_now()
    if (now >= deadline or runtime.state != "registered" or not any(b is binding for b in runtime.bindings)
            or _bound_binding_values(binding) != original_binding
            or _bound_runtime_values(runtime) != original_runtime
            or runtime._transports.get((binding.service_id, binding.transport_ref)) is not transport
            or runtime._peers.get(binding.service_id) != peer
            or runtime._authenticated_registration_peers.get(proof_key) != (peer, proof[1])):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    _check_bound_call(call)
    return (binding, BoundCallObservation(identity, runtime.run_id, scope, binding.registration_id,
                                          binding.service_epoch, binding.mount, binding.service_id,
                                          binding.instance_id, binding.scope_ref, binding.operations,
                                          peer, proof[1], deadline, binding.active, binding.required, call.operation),
            original_binding, original_runtime, transport)


def _cancel_stream_lineage(runtime: ServiceClientRuntime, binding: RegisteredBinding, transport: object) -> tuple:
    """Read the current registered local socket lineage, not remote continuity."""
    if not runtime._has_current_registration_observation(binding):
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    proof = runtime._registration_lineage.get(id(binding))
    if type(proof) is not tuple or proof[2][1] is not transport:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    client = proof[2][2]
    receiver = client._receiver
    if receiver is None or getattr(receiver, "_socket", None) is not proof[2][3]:
        raise ValueError("BOUND_ROUTE_UNAVAILABLE")
    return proof, receiver


def _execute_bound_call(runtime: Any, call: _BoundCall) -> _BoundOwnerResult:
    # Before the raw call begins, refusal is known not to have sent this call.
    try:
        binding, before, binding_values, runtime_values, transport = _bound_observation(runtime, call)
        cancel_stream = call.operation in {"account.cancel_attempts.read_after", "account.cancel_attempts.ack"}
        lineage = _cancel_stream_lineage(runtime, binding, transport) if cancel_stream else None
        if call.operation == "account.cancel_attempts.ack" and call.expected_binding is None:
            raise ValueError("BOUND_ROUTE_UNAVAILABLE")
        if call.expected_binding is not None:
            token = call.expected_token
            if (type(token) is not _BoundBindingToken or token.issuer is not _BOUND_BINDING_ISSUER
                    or token.runtime is not runtime or token.binding is not binding
                    or token.transport is not transport or token.binding_values != binding_values
                    or token.runtime_values != runtime_values or token.observation != call.expected_binding
                    or replace(before, operation=call.expected_binding.operation) != call.expected_binding):
                raise ValueError("BOUND_ROUTE_UNAVAILABLE")
            if cancel_stream and (type(token.lineage) is not tuple or len(token.lineage) != 2
                    or token.lineage[0] is not lineage[0] or token.lineage[1] is not lineage[1]
                    or (call.operation == "account.cancel_attempts.ack"
                        and token.observation.operation != "account.cancel_attempts.read_after")):
                raise ValueError("BOUND_ROUTE_UNAVAILABLE")
        elif call.expected_token is not None:
            raise ValueError("BOUND_ROUTE_UNAVAILABLE")
        mount, operation = call.mount, call.operation
        arguments, kwargs = _copy_wire(call.arguments), dict(call.kwargs)
        _check_bound_call(call)
    except Exception:
        raise ThreadedRuntimeError("BOUND_ROUTE_UNAVAILABLE") from None
    try:
        call.raw_started = True
        reply = runtime.call(mount, operation, arguments,
                             _expected_binding=binding, _expected_binding_values=binding_values,
                             _expected_runtime_values=runtime_values, _expected_transport=transport,
                             _expected_peer=before.peer, _expected_fingerprint=before.fingerprint,
                             **kwargs)
    except Exception:
        # Once execution of the raw path has begun, do not infer NOT_SENT from
        # an arbitrary transport or post-observation exception.
        raise ThreadedRuntimeError("BOUND_OUTCOME_UNKNOWN") from None
    return _BoundOwnerResult(reply, binding, before, binding_values, runtime_values, transport, lineage)


def _finish_bound_call(runtime: Any, call: _BoundCall, result: _BoundOwnerResult) -> BoundCallResult:
    """Recheck after wrapper evidence callbacks and before publishing _Call."""
    try:
        if type(result) is not _BoundOwnerResult:
            raise ValueError
        if not isinstance(result.result, Mapping):
            raise ValueError
        # Detach the response before the last owner observation. A mapping's
        # copy hooks can themselves change local state or fail after the RPC.
        copied_result = _copy_wire(result.result)
        _bound_wire_signature(copied_result)
        _, after, binding_values, runtime_values, transport = _bound_observation(runtime, call, result.binding)
        if (after != result.before or binding_values != result.binding_values
                or runtime_values != result.runtime_values or transport is not result.transport):
            raise ValueError
        if result.lineage is not None:
            current = _cancel_stream_lineage(runtime, result.binding, transport)
            if current[0] is not result.lineage[0] or current[1] is not result.lineage[1]:
                raise ValueError
    except Exception:
        raise ThreadedRuntimeError("BOUND_OUTCOME_UNKNOWN") from None
    token = _BoundBindingToken(_BOUND_BINDING_ISSUER, runtime, result.binding, result.transport,
                               result.binding_values, result.runtime_values,
                               _copy_bound_observation(result.before), result.lineage)
    return BoundCallResult(copied_result, result.before, after, token)


class _OwnerCall:
    def __init__(self, operation: str, arguments: dict[str, Any]):
        self.operation = operation
        self.arguments = arguments
        self.state = "queued"
        self.result: Any = None
        self.error: Exception | None = None
        self.continuation: OwnerOperationContinuation | None = None
        self.due = 0.0


def _copy_owner_json(value: Any, *, error_code: str) -> Any:
    """Copy exact JSON types while bounding nesting, nodes and canonical bytes."""
    remaining = [100_000]
    active: set[int] = set()
    max_bytes = 8 * 1024 * 1024
    used_bytes = [0]

    def consume(size: int) -> None:
        used_bytes[0] += size
        if used_bytes[0] > max_bytes:
            raise ValueError

    def copy(item: Any, depth: int) -> Any:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 32:
            raise ValueError
        if item is None or type(item) in (bool, int):
            consume(len(json.dumps(item, allow_nan=False)))
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError
            consume(len(json.dumps(item, allow_nan=False)))
            return item
        if type(item) is str:
            if len(item) > max_bytes:
                raise ValueError
            consume(2)  # quotation marks
            for index in range(0, len(item), 4096):
                chunk = item[index : index + 4096]
                chunk.encode("utf-8", errors="strict")
                consume(len(json.dumps(chunk, ensure_ascii=True)) - 2)
            return item
        if type(item) not in (list, dict) or id(item) in active:
            raise ValueError
        active.add(id(item))
        try:
            consume(2 + max(0, len(item) - 1))  # brackets and commas
            if type(item) is list:
                return [copy(child, depth + 1) for child in item]
            if any(type(key) is not str for key in item):
                raise ValueError
            consume(len(item))  # colons
            return {copy(key, depth + 1): copy(child, depth + 1) for key, child in item.items()}
        finally:
            active.remove(id(item))

    try:
        result = copy(value, 0)
        return result
    except Exception:
        raise ThreadedRuntimeError(error_code) from None


class OwnerCallHandle:
    """One owner call retained by its caller across wait timeouts."""

    def __init__(self, runtime: ThreadedServiceClientRuntime, call: _OwnerCall):
        self._runtime, self._call = runtime, call

    def result(self, *, timeout_seconds: float = 5.0) -> Any:
        runtime, call = self._runtime, self._call
        runtime._check_reentry()
        deadline = _deadline(timeout_seconds)
        with runtime._condition:
            while call.state in ("queued", "running"):
                remaining = deadline - _DEADLINE_CLOCK()
                if remaining <= 0:
                    raise ThreadedRuntimeError(
                        "OWNER_TIMEOUT_QUEUED" if call.state == "queued" else "OWNER_TIMEOUT_RUNNING"
                    )
                runtime._condition.wait(min(remaining, threading.TIMEOUT_MAX))
            if call.error is not None:
                if type(call.error) is OwnerOperationError:
                    raise OwnerOperationError(call.error.code)
                raise ThreadedRuntimeError(call.error.code)
            value = call.result
        return _copy_owner_json(value, error_code="INVALID_OWNER_RESULT")

    def cancel(self) -> bool:
        runtime, call = self._runtime, self._call
        runtime._check_reentry()
        with runtime._condition:
            if call.state != "queued":
                return False
            runtime._queue.remove(call)
            call.state = "done"
            call.error = ThreadedRuntimeError("OWNER_CANCELLED")
            runtime._condition.notify_all()
            return True


def _copy_wire(value: Any) -> Any:
    # Restrict to by-value wire data: deepcopy alone permits custom objects to
    # return themselves and thus allows mutation after submission.
    if value is None or type(value) in (str, bool, int, float):
        return value
    if type(value) in (list, tuple):
        return [_copy_wire(v) for v in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ThreadedRuntimeError("INVALID_ARGUMENTS")
            result[key] = _copy_wire(item)
        return result
    raise ThreadedRuntimeError("INVALID_ARGUMENTS")


def _timeout(value: float) -> float:
    if type(value) not in (int, float):
        raise ValueError("timeout_seconds must be a finite positive number")
    try:
        timeout = float(value)
    except (OverflowError, ValueError):
        raise ValueError("timeout_seconds must be a finite positive number") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be a finite positive number")
    if timeout > threading.TIMEOUT_MAX:
        raise ValueError("timeout_seconds exceeds platform limit")
    return timeout


def _deadline(value: float) -> float:
    timeout = _timeout(value)
    try:
        now = _DEADLINE_CLOCK()
        deadline = now + timeout
        if (not math.isfinite(now) or not math.isfinite(deadline)
                or deadline <= now):
            raise ValueError
    except Exception:
        raise ValueError("timeout_seconds cannot form a deadline") from None
    return deadline


class ThreadedServiceClientRuntime:
    """Own and maintain a runtime on a single dedicated thread.

    ``start`` timeout requests cleanup, but a blocked factory/start may still
    be running. Inspect ``status`` and call ``close`` again to wait for it.
    ``close`` cannot cancel an already-running transport request.
    With sticky cleanup enabled, caller-side health for a finite required
    lease requires the exact SDK raw runtime and wrapper to use the same native
    monotonic clock. Unproven clocks fail closed for finite leases. This local
    check is not a cross-process time proof. The default mode retains its
    original trusted injected-clock and condition-lock behavior.
    Queued calls and deferred continuations share ``queue_size`` capacity.
    One already-running call is outside that waiting capacity, so if it
    yields while the queue is full the total temporarily reaches
    ``queue_size + 1``; no new calls enter until capacity is available.
    Shutdown resolves unfinished yielded continuations as unknown. An initial
    synchronous invoke already in flight can still return its final JSON;
    if it yields after shutdown begins, that continuation is unknown instead.

    The optional ``cleanup_unknown`` and ``on_cleanup_sticky`` callbacks must
    be supplied together. Only an exact False permits each cleanup step. A
    failed or uncertain cleanup retains the wrapper and its resources in a
    process-local root; callbacks run outside the condition. The raw runtime
    must explicitly report ``sticky_cleanup_enabled is True``. These checks
    cannot make a subsequent cleanup operation atomic with the observation.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        queue_size: int = 32,
        monotonic_clock: Callable[[], float] = time.monotonic,
        owner_extension_factory: Callable[[], OwnerRuntimeExtension] | None = None,
        require_unregister_ack: bool = False,
        cleanup_unknown: Callable[[], bool] | None = None,
        on_cleanup_sticky: Callable[[ThreadedServiceClientRuntime], None] | None = None,
    ):
        if not callable(factory):
            raise TypeError("factory must be callable")
        if type(queue_size) is not int or not 1 <= queue_size <= 1024:
            raise ValueError("invalid queue_size")
        if not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        if owner_extension_factory is not None and not callable(owner_extension_factory):
            raise TypeError("owner_extension_factory must be callable")
        if type(require_unregister_ack) is not bool:
            raise ValueError("invalid cleanup policy")
        if (cleanup_unknown is None) != (on_cleanup_sticky is None) or (
            cleanup_unknown is not None
            and (not callable(cleanup_unknown) or not callable(on_cleanup_sticky))
        ):
            raise ValueError("invalid sticky cleanup policy")
        self._factory = factory
        self._cleanup_unknown = cleanup_unknown
        self._on_cleanup_sticky = on_cleanup_sticky
        self._cleanup_sticky = False
        self._sticky_resources: tuple[Any, Any] | None = None
        self._sticky_errors: list[BaseException] = []
        self._require_unregister_ack = require_unregister_ack
        self._owner_extension_factory = owner_extension_factory
        # Trusted clock must share the runtime's monotonic clock domain.
        self._monotonic_clock = monotonic_clock
        self._creator_pid = os.getpid()
        self._queue_size = queue_size
        self._condition = threading.Condition()
        self._queue: deque[_Call | _OwnerCall] = deque()
        self._continuations: deque[_OwnerCall] = deque()
        self._active_continuation: _OwnerCall | None = None
        self._thread: threading.Thread | None = None
        self._state = "new"
        self._stop = False
        self._error: str | None = None
        self._healthy = False
        self._lease_deadline: float | None = None
        self._lease_clock_native_proven = False
        self._lease_evidence_invalid = False
        self._bindings: tuple[BindingSnapshot, ...] = ()
        self._identity: WireIdentity | None = None
        self._run_id: str | None = None
        self._cleanup_failures = 0
        self._unregister_uncertainties = 0
        self._unknown_registration_count: int | None = 0
        self._strict_unregister_ack: bool | None = None
        self._owner_cleanup_uncertainties = 0
        self._cleanup_retry = 0

    def status(self) -> RuntimeSnapshot:
        if os.getpid() != self._creator_pid:
            return RuntimeSnapshot("wrong_process", False, False, "WRONG_PROCESS", (), 0, 0)
        if self._cleanup_unknown is None:
            with self._condition:
                return RuntimeSnapshot(
                    self._state, bool(self._thread and self._thread.is_alive()),
                    self._healthy and self._lease_current_at(self._sample_lease_clock()),
                    self._error, self._bindings, self._cleanup_failures,
                    self._unregister_uncertainties, self._identity, self._run_id,
                    self._owner_cleanup_uncertainties, self._unknown_registration_count,
                    self._strict_unregister_ack,
                )
        with self._condition:
            healthy = self._healthy
        lease_now = self._sample_lease_clock() if healthy else None
        with self._condition:
            return RuntimeSnapshot(
                self._state,
                bool(self._thread and self._thread.is_alive()),
                self._healthy and self._lease_current_at(lease_now),
                self._error,
                self._bindings,
                self._cleanup_failures,
                self._unregister_uncertainties,
                self._identity,
                self._run_id,
                self._owner_cleanup_uncertainties,
                self._unknown_registration_count,
                self._strict_unregister_ack,
            )

    @property
    def strict_cleanup_required(self) -> bool:
        return self._require_unregister_ack

    def _sample_lease_clock(self) -> float | None:
        try:
            now = self._monotonic_clock()
            return now if type(now) in (int, float) and math.isfinite(now) else None
        except Exception:
            return None

    def _lease_current_at(self, now: float | None) -> bool:
        if now is None:
            return False
        deadline = self._lease_deadline
        if self._cleanup_unknown is None:
            return deadline is None or now < deadline
        if self._lease_evidence_invalid:
            return False
        if deadline is None:
            return True
        # Only the exact SDK runtime and wrapper with the same native clock
        # prove a local comparison domain. Custom clocks have no proven rate.
        if (not self._lease_clock_native_proven
                or self._monotonic_clock is not _NATIVE_MONOTONIC
                or type(deadline) not in (int, float)):
            return False
        try:
            return math.isfinite(deadline) and _NATIVE_MONOTONIC() < deadline
        except (OverflowError, ValueError):
            return False

    def _check_reentry(self) -> None:
        if os.getpid() != self._creator_pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        if threading.current_thread() is self._thread:
            raise ThreadedRuntimeError("OWNER_THREAD_REENTRY")

    def start(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        self._check_reentry()
        deadline = _deadline(timeout_seconds)
        with self._condition:
            if self._state != "new":
                raise ThreadedRuntimeError("NOT_NEW")
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("START_TIMEOUT")
            self._state = "starting"
            self._thread = threading.Thread(
                target=self._run, name="service-client-owner", daemon=True
            )
            self._thread.start()
            while self._state == "starting":
                remaining = deadline - _DEADLINE_CLOCK()
                if remaining <= 0:
                    self._stop = True
                    self._healthy = False
                    self._state = "stopping"
                    self._error = "START_TIMEOUT_CLEANUP_PENDING"
                    self._condition.notify_all()
                    raise ThreadedRuntimeError(self._error)
                self._condition.wait(min(remaining, threading.TIMEOUT_MAX))
            if self._state != "running":
                raise ThreadedRuntimeError(self._error or "START_FAILED")
        return self.status()

    def call(
        self,
        mount: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
        timeout_seconds: float = 5.0,
        _bound_route: BoundCallRoute | None = None,
        _expected_binding: BoundCallObservation | None = None,
        _expected_token: object = None,
    ) -> Any:
        self._check_reentry()
        deadline = _deadline(timeout_seconds)
        if not isinstance(arguments, Mapping):
            raise ThreadedRuntimeError("INVALID_ARGUMENTS")
        if _bound_route is not None:
            if type(_bound_route) is not BoundCallRoute:
                raise ThreadedRuntimeError("INVALID_ARGUMENTS")
            try:
                _bound_route = BoundCallRoute(**vars(_bound_route))
            except Exception:
                raise ThreadedRuntimeError("INVALID_ARGUMENTS") from None
        elif _expected_binding is not None or _expected_token is not None:
            raise ThreadedRuntimeError("INVALID_ARGUMENTS")
        try:
            call_arguments = (
                _copy_wire(mount),
                _copy_wire(operation),
                _copy_wire(arguments),
                _copy_wire(
                    {
                        "account_ref": account_ref,
                        "portfolio_ref": portfolio_ref,
                        "scope_ref": scope_ref,
                        "max_reply_message_bytes": max_reply_message_bytes,
                    }
                ),
            )
            call = (_Call(*call_arguments) if _bound_route is None
                    else _BoundCall(*call_arguments, _bound_route, _expected_binding, _expected_token))
        except Exception:
            if _bound_route is None:
                raise
            raise ThreadedRuntimeError("INVALID_ARGUMENTS") from None
        lease_now = None
        if self._cleanup_unknown is not None or _bound_route is not None:
            with self._condition:
                if _DEADLINE_CLOCK() >= deadline:
                    raise ThreadedRuntimeError("TIMEOUT_BEFORE_START")
                if self._state != "running" or self._stop or not self._healthy:
                    raise ThreadedRuntimeError("NOT_READY")
            lease_now = self._sample_lease_clock()
        with self._condition:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("TIMEOUT_BEFORE_START")
            if (
                self._state != "running"
                or self._stop
                or not self._healthy
                or not self._lease_current_at(
                    self._sample_lease_clock()
                    if self._cleanup_unknown is None and _bound_route is None else lease_now
                )
            ):
                raise ThreadedRuntimeError("NOT_READY")
            # A trusted legacy clock is called under this reentrant lock and
            # can close the runtime before returning.
            if self._state != "running" or self._stop or not self._healthy:
                raise ThreadedRuntimeError("NOT_READY")
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("TIMEOUT_BEFORE_START")
            if len(self._queue) + len(self._continuations) >= self._queue_size:
                raise ThreadedRuntimeError("QUEUE_FULL")
            self._queue.append(call)
            self._condition.notify_all()
            while call.state in ("queued", "running"):
                remaining = deadline - _DEADLINE_CLOCK()
                if remaining <= 0:
                    if call.state == "queued":
                        self._queue.remove(call)
                        call.state = "cancelled"
                        raise ThreadedRuntimeError("TIMEOUT_BEFORE_START")
                    raise ThreadedRuntimeError("TIMEOUT_AFTER_START")
                self._condition.wait(min(remaining, threading.TIMEOUT_MAX))
            if call.error is not None:
                raise call.error
            return call.result

    def call_bound(self, mount: str, operation: str, arguments: Mapping[str, Any], *,
                   route: BoundCallRoute, timeout_seconds: float = 5.0,
                   expected_binding: BoundCallObservation | None = None,
                   _expected_token: object = None) -> BoundCallResult:
        """Run one call with owner-local binding observations, never retrying it."""
        if type(route) is not BoundCallRoute:
            raise ThreadedRuntimeError("INVALID_ARGUMENTS")
        result = self.call(mount, operation, arguments, account_ref=route.scope.account_ref,
                           portfolio_ref=route.scope.portfolio_ref, scope_ref=route.scope_ref,
                           timeout_seconds=timeout_seconds, _bound_route=route,
                           _expected_binding=expected_binding, _expected_token=_expected_token)
        if type(result) is not BoundCallResult:
            raise ThreadedRuntimeError("BOUND_OUTCOME_UNKNOWN")
        return result

    def submit_owner(self, operation: str, arguments: Mapping[str, Any]) -> OwnerCallHandle:
        self._check_reentry()
        if self._owner_extension_factory is None:
            raise ThreadedRuntimeError("OWNER_UNAVAILABLE")
        if type(operation) is not str or not operation:
            raise ThreadedRuntimeError("INVALID_OWNER_ARGUMENTS")
        try:
            if len(operation.encode("utf-8", errors="strict")) > 256:
                raise ValueError
        except (ValueError, UnicodeError):
            raise ThreadedRuntimeError("INVALID_OWNER_ARGUMENTS") from None
        copied = _copy_owner_json(arguments, error_code="INVALID_OWNER_ARGUMENTS")
        if type(copied) is not dict:
            raise ThreadedRuntimeError("INVALID_OWNER_ARGUMENTS")
        call = _OwnerCall(operation, copied)
        lease_now = None
        if self._cleanup_unknown is not None:
            with self._condition:
                if self._state != "running" or self._stop or not self._healthy:
                    raise ThreadedRuntimeError("NOT_READY")
            lease_now = self._sample_lease_clock()
        with self._condition:
            if (
                self._state != "running" or self._stop or not self._healthy
                or not self._lease_current_at(
                    self._sample_lease_clock() if self._cleanup_unknown is None else lease_now
                )
            ):
                raise ThreadedRuntimeError("NOT_READY")
            if self._state != "running" or self._stop or not self._healthy:
                raise ThreadedRuntimeError("NOT_READY")
            if len(self._queue) + len(self._continuations) >= self._queue_size:
                raise ThreadedRuntimeError("QUEUE_FULL")
            self._queue.append(call)
            self._condition.notify_all()
        return OwnerCallHandle(self, call)

    def close(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        self._check_reentry()
        deadline = _deadline(timeout_seconds)
        with self._condition:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("CLOSE_TIMEOUT_BEFORE_START")
            if self._state == "new":
                self._state = "closed"
                return self.status()
            if self._cleanup_sticky:
                raise ThreadedRuntimeError("CLEANUP_FAILED")
            self._stop = True
            self._healthy = False
            self._cleanup_retry += 1
            if self._state in ("starting", "running") or self._state == "close_failed":
                self._state = "stopping"
            self._reject_pending("CLOSED_BEFORE_START")
            self._condition.notify_all()
            thread = self._thread
        with self._condition:
            while thread is not None and thread.is_alive() and self._state != "close_failed":
                remaining = deadline - _DEADLINE_CLOCK()
                if remaining <= 0:
                    raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
                self._condition.wait(min(remaining, 0.05))
            if self._state == "close_failed":
                raise ThreadedRuntimeError("CLEANUP_FAILED")
        snapshot = self.status()
        if snapshot.state == "close_failed":
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        return snapshot

    def _cleanup_observation_locked(self) -> tuple[RuntimeSnapshot, bool]:
        """Capture cleanup fields without invoking a supplied lease clock."""
        thread = self._thread
        if thread is not None and type(thread) is not threading.Thread:
            raise ThreadedRuntimeError("CLEANUP_STATE_UNCERTAIN")
        return RuntimeSnapshot(
            self._state, bool(thread and thread.is_alive()), False, self._error,
            self._bindings, self._cleanup_failures, self._unregister_uncertainties,
            self._identity, self._run_id, self._owner_cleanup_uncertainties,
            self._unknown_registration_count, self._strict_unregister_ack,
        ), thread is not None

    def cleanup_observation(
        self, *, timeout_seconds: float = 5.0
    ) -> tuple[RuntimeSnapshot, bool]:
        """Read cleanup state within a bounded wait; this is not health evidence."""
        self._check_reentry()
        deadline = _deadline(timeout_seconds)
        remaining = deadline - _DEADLINE_CLOCK()
        if remaining <= 0 or not self._condition.acquire(timeout=min(remaining, threading.TIMEOUT_MAX)):
            raise ThreadedRuntimeError("CLEANUP_SNAPSHOT_TIMEOUT")
        try:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("CLEANUP_SNAPSHOT_TIMEOUT")
            return self._cleanup_observation_locked()
        finally:
            self._condition.release()

    def _bounded_health_snapshot_locked(self) -> RuntimeSnapshot:
        thread = self._thread
        if thread is not None and type(thread) is not threading.Thread:
            raise ThreadedRuntimeError("CLEANUP_STATE_UNCERTAIN")
        # A supplied clock is arbitrary callback code and cannot be bounded.
        # Do not claim health if its clock domain cannot be sampled here.
        healthy = False
        if self._healthy and self._monotonic_clock is _NATIVE_MONOTONIC:
            healthy = self._lease_current_at(_NATIVE_MONOTONIC())
        return RuntimeSnapshot(
            self._state, bool(thread and thread.is_alive()), healthy, self._error,
            self._bindings, self._cleanup_failures, self._unregister_uncertainties,
            self._identity, self._run_id, self._owner_cleanup_uncertainties,
            self._unknown_registration_count, self._strict_unregister_ack,
        )

    def bounded_status(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        """Read health with a bounded lock wait and no supplied clock callback."""
        self._check_reentry()
        deadline = _deadline(timeout_seconds)
        remaining = deadline - _DEADLINE_CLOCK()
        if remaining <= 0 or not self._condition.acquire(timeout=min(remaining, threading.TIMEOUT_MAX)):
            raise ThreadedRuntimeError("STATUS_TIMEOUT")
        try:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("STATUS_TIMEOUT")
            snapshot = self._bounded_health_snapshot_locked()
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("STATUS_TIMEOUT")
            return snapshot
        finally:
            self._condition.release()

    def start_bounded_once(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        """Start once with one deadline for the lock, wait and health capture.

        The operating system's thread-start call itself cannot be interrupted.
        """
        self._check_reentry()
        deadline = _deadline(timeout_seconds)
        remaining = deadline - _DEADLINE_CLOCK()
        if remaining <= 0 or not self._condition.acquire(timeout=min(remaining, threading.TIMEOUT_MAX)):
            raise ThreadedRuntimeError("START_TIMEOUT")
        try:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("START_TIMEOUT")
            if self._state != "new":
                raise ThreadedRuntimeError("NOT_NEW")
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="service-client-owner", daemon=True)
            self._thread.start()
            while self._state == "starting":
                remaining = deadline - _DEADLINE_CLOCK()
                if remaining <= 0:
                    self._stop = True
                    self._healthy = False
                    self._state = "stopping"
                    self._error = "START_TIMEOUT_CLEANUP_PENDING"
                    self._condition.notify_all()
                    raise ThreadedRuntimeError(self._error)
                self._condition.wait(min(remaining, threading.TIMEOUT_MAX))
            if self._state != "running":
                raise ThreadedRuntimeError(self._error or "START_FAILED")
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("START_TIMEOUT_CLEANUP_PENDING")
            snapshot = self._bounded_health_snapshot_locked()
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("START_TIMEOUT_CLEANUP_PENDING")
            return snapshot
        finally:
            self._condition.release()

    def close_once_if_state(
        self, *, expected_state: str, timeout_seconds: float = 5.0
    ) -> RuntimeSnapshot:
        """Atomically start one close only if the owner is still untouched.

        This does not retry an owner cleanup that began after a start failure.
        Existing ``close`` retains its separate retry semantics for its callers.
        """
        self._check_reentry()
        if type(expected_state) is not str or expected_state not in ("new", "running"):
            raise ThreadedRuntimeError("INVALID_ARGUMENTS")
        deadline = _deadline(timeout_seconds)
        remaining = deadline - _DEADLINE_CLOCK()
        if remaining <= 0 or not self._condition.acquire(timeout=min(remaining, threading.TIMEOUT_MAX)):
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_BEFORE_START")
        try:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("CLOSE_TIMEOUT_BEFORE_START")
            if (self._state != expected_state or self._stop or self._cleanup_sticky
                    or type(self._cleanup_retry) is not int or self._cleanup_retry != 0):
                raise ThreadedRuntimeError("CLEANUP_STATE_UNCERTAIN")
            if expected_state == "new":
                if self._thread is not None:
                    raise ThreadedRuntimeError("CLEANUP_STATE_UNCERTAIN")
                self._state = "closed"
                snapshot = self._cleanup_observation_locked()[0]
                if _DEADLINE_CLOCK() >= deadline:
                    raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
                return snapshot
            if self._thread is not None and type(self._thread) is not threading.Thread:
                raise ThreadedRuntimeError("CLEANUP_STATE_UNCERTAIN")
            self._stop = True
            self._healthy = False
            self._cleanup_retry += 1
            self._state = "stopping"
            self._reject_pending("CLOSED_BEFORE_START")
            self._condition.notify_all()
            thread = self._thread
        finally:
            self._condition.release()
        remaining = deadline - _DEADLINE_CLOCK()
        if remaining <= 0 or not self._condition.acquire(timeout=min(remaining, threading.TIMEOUT_MAX)):
            raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
        try:
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
            while thread is not None and thread.is_alive() and self._state != "close_failed":
                remaining = deadline - _DEADLINE_CLOCK()
                if remaining <= 0:
                    raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
                self._condition.wait(min(remaining, 0.05))
            if self._state == "close_failed":
                raise ThreadedRuntimeError("CLEANUP_FAILED")
            snapshot, _ = self._cleanup_observation_locked()
            if _DEADLINE_CLOCK() >= deadline:
                raise ThreadedRuntimeError("CLOSE_TIMEOUT_WORKER_ALIVE")
        finally:
            self._condition.release()
        if snapshot.state == "close_failed":
            raise ThreadedRuntimeError("CLEANUP_FAILED")
        return snapshot

    def _reject_pending(self, code: str) -> None:
        while self._queue:
            call = self._queue.popleft()
            call.error = ThreadedRuntimeError(code)
            call.state = "done"
        while self._continuations:
            call = self._continuations.popleft()
            call.continuation = None
            call.error = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
            call.state = "done"
        if self._active_continuation is not None and self._active_continuation.state != "done":
            call = self._active_continuation
            call.error = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
            call.state = "done"
        self._condition.notify_all()

    def _capture(self, runtime: Any) -> bool | None:
        if self._cleanup_unknown is None:
            self._capture_impl(runtime)
            return
        try:
            return self._capture_strict(runtime)
        except _StickyCleanupSignal:
            raise
        except BaseException as error:
            raise _StickyCleanupSignal(error) from error

    def _observe_external(self, reader: Callable[[], Any]) -> Any:
        """Check global uncertainty around each individual foreign read."""
        def check() -> None:
            try:
                self._strict_external_guard()
            except _StickyCleanupSignal:
                raise
            except BaseException as error:
                raise _StickyCleanupSignal(error) from error
        check()
        try:
            return reader()
        finally:
            check()

    def _capture_strict(self, runtime: Any) -> bool:
        from bt_api_py.service_runtime import ServiceClientRuntime

        with self._condition:
            if self._lease_evidence_invalid:
                return False
        read = self._observe_external

        def items():
            iterator = read(lambda: iter(read(lambda: runtime.bindings)))
            while True:
                try:
                    yield read(lambda: next(iterator))
                except StopIteration:
                    return

        snapshots = []
        lease_evidence_invalid = False
        for binding in items():
            proof = None
            if type(runtime) is ServiceClientRuntime:
                try:
                    proof = read(lambda: runtime.authenticated_peer_for_binding(binding))
                except Exception:
                    # The guarded read checks global UNKNOWN before returning
                    # control here, including when the peer callback raises.
                    pass
            registration_id = read(lambda: binding.registration_id)
            service_epoch = read(lambda: binding.service_epoch)
            active = read(lambda: getattr(binding, "active", None))
            if type(active) is not bool:
                lease_evidence_invalid = True
                break
            required = read(lambda: getattr(binding, "required", None))
            if type(required) is not bool:
                lease_evidence_invalid = True
                break
            mount = read(lambda: getattr(binding, "mount", None))
            service_id = read(lambda: getattr(binding, "service_id", None))
            instance_id = read(lambda: getattr(binding, "instance_id", None))
            scope_ref = read(lambda: getattr(binding, "scope_ref", None))
            scope = read(lambda: getattr(binding, "scope", None))
            operations = read(lambda: getattr(binding, "operations", None))
            peer = read(lambda: proof[0]) if proof is not None else None
            fingerprint = read(lambda: proof[1]) if proof is not None else None
            snapshots.append(BindingSnapshot(
                registration_id, service_epoch, active, mount, service_id,
                instance_id, scope_ref, scope if type(scope) is WireScope else None,
                operations if type(operations) is frozenset else None,
                required if type(required) is bool else None, peer, fingerprint,
            ))
        if lease_evidence_invalid:
            with self._condition:
                self._lease_evidence_invalid = True
                self._healthy = False
                self._condition.notify_all()
            return False
        identity = read(lambda: getattr(runtime, "identity", None))
        run_id = read(lambda: getattr(runtime, "run_id", None))
        deadlines = []
        if not lease_evidence_invalid:
            for binding in items():
                active = read(lambda: getattr(binding, "active", None))
                required = read(lambda: getattr(binding, "required", None))
                if type(active) is not bool or type(required) is not bool:
                    lease_evidence_invalid = True
                    break
                if active and required:
                    value = read(lambda: getattr(binding, "lease_deadline", None))
                    try:
                        valid = type(value) in (int, float) and math.isfinite(value) and value > 0
                    except (OverflowError, ValueError):
                        valid = False
                    if not valid:
                        lease_evidence_invalid = True
                        break
                    deadlines.append(value)
        if lease_evidence_invalid:
            with self._condition:
                self._lease_evidence_invalid = True
                self._healthy = False
                self._condition.notify_all()
            return False
        cleanup_counts = self._capture_raw_uncertainties(runtime)
        healthy = read(lambda: bool(runtime.healthy))
        strict_ack = read(lambda: runtime._require_unregister_ack) if type(runtime) is ServiceClientRuntime else None
        strict_ack = strict_ack if type(strict_ack) is bool else None
        lease_deadline = read(lambda: min(deadlines)) if deadlines else None
        native_domain = (
            type(runtime) is ServiceClientRuntime
            and self._monotonic_clock is _NATIVE_MONOTONIC
            and read(lambda: runtime._monotonic_clock) is _NATIVE_MONOTONIC
        )
        with self._condition:
            self._bindings = tuple(snapshots)
            self._identity = identity if type(identity) is WireIdentity else None
            self._run_id = run_id if type(run_id) is str else None
            self._lease_deadline = lease_deadline
            self._lease_clock_native_proven = native_domain
            self._lease_evidence_invalid = lease_evidence_invalid
            self._healthy = healthy and not self._stop
            (self._cleanup_failures, self._unregister_uncertainties,
             self._unknown_registration_count) = cleanup_counts
            self._strict_unregister_ack = strict_ack
        return True

    def _capture_impl(self, runtime: Any) -> None:
        from bt_api_py.service_runtime import ServiceClientRuntime
        def proven_peer(binding):
            if type(runtime) is not ServiceClientRuntime:
                return None
            try:
                return runtime.authenticated_peer_for_binding(binding)
            except Exception:
                return None
        bindings = tuple(
            BindingSnapshot(
                b.registration_id,
                b.service_epoch,
                b.active,
                getattr(b, "mount", None),
                getattr(b, "service_id", None),
                getattr(b, "instance_id", None),
                getattr(b, "scope_ref", None),
                getattr(b, "scope", None) if type(getattr(b, "scope", None)) is WireScope else None,
                getattr(b, "operations", None)
                if type(getattr(b, "operations", None)) is frozenset
                else None,
                getattr(b, "required", None)
                if type(getattr(b, "required", None)) is bool
                else None,
                proof[0] if proof is not None else None,
                proof[1] if proof is not None else None,
            )
            for b in runtime.bindings
            for proof in (proven_peer(b),)
        )
        identity = getattr(runtime, "identity", None)
        run_id = getattr(runtime, "run_id", None)
        identity = identity if type(identity) is WireIdentity else None
        run_id = run_id if type(run_id) is str else None
        deadlines = [b.lease_deadline for b in runtime.bindings if b.active and b.required]
        try:
            native_domain = (type(runtime) is ServiceClientRuntime
                             and self._monotonic_clock is _NATIVE_MONOTONIC
                             and runtime._monotonic_clock is _NATIVE_MONOTONIC)
        except Exception:
            native_domain = False
        if self._cleanup_unknown is not None:
            cleanup_counts = self._capture_raw_uncertainties(runtime)
            healthy = bool(runtime.healthy)
            strict_ack = (
                runtime._require_unregister_ack if type(runtime) is ServiceClientRuntime
                and type(runtime._require_unregister_ack) is bool else None
            )
        with self._condition:
            self._bindings = bindings
            self._identity = identity
            self._run_id = run_id
            self._lease_deadline = min(deadlines) if deadlines else None
            self._lease_clock_native_proven = native_domain
            if self._cleanup_unknown is not None:
                self._healthy = healthy and not self._stop
                (self._cleanup_failures, self._unregister_uncertainties,
                 self._unknown_registration_count) = cleanup_counts
                self._strict_unregister_ack = strict_ack
            else:
                self._healthy = bool(runtime.healthy) and not self._stop
                self._cleanup_failures = runtime.cleanup_failures
                self._unregister_uncertainties = runtime.unregister_uncertainties
                # Missing source evidence must not be projected as a clean zero.
                self._unknown_registration_count = getattr(runtime, "unknown_registration_count", None)
                self._strict_unregister_ack = (
                    runtime._require_unregister_ack if type(runtime) is ServiceClientRuntime
                    and type(runtime._require_unregister_ack) is bool else None
                )

    def _capture_raw_uncertainties(self, runtime: Any) -> tuple[int, int, int]:
        try:
            if self._observe_external(lambda: runtime.sticky_cleanup_enabled) is not True:
                raise ValueError("RUNTIME_STICKY_POLICY_UNPROVEN")
            counts = []
            for field in ("cleanup_failures", "unregister_uncertainties",
                          "unknown_registration_count"):
                value = self._observe_external(lambda: getattr(runtime, field))
                if type(value) is not int or value != 0:
                    raise ValueError("RUNTIME_CLEANUP_UNKNOWN")
                counts.append(value)
            return counts[0], counts[1], counts[2]
        except _StickyCleanupSignal:
            raise
        except BaseException as error:
            raise _StickyCleanupSignal(error) from error

    def _check_cleanup_evidence(self, runtime: Any, extension: Any) -> None:
        if extension is not None:
            self._capture_owner_uncertainties(extension)
        if runtime is not None:
            self._capture_raw_uncertainties(runtime)

    def _capture_owner_uncertainties(self, extension: OwnerRuntimeExtension) -> None:
        try:
            count = (extension.cleanup_uncertainties if self._cleanup_unknown is None
                     else self._observe_external(lambda: extension.cleanup_uncertainties))
            if type(count) is not int or not 0 <= count < 2**63:
                raise ValueError("INVALID_OWNER_CLEANUP_COUNT")
            with self._condition:
                self._owner_cleanup_uncertainties = max(self._owner_cleanup_uncertainties, count)
            if self._cleanup_unknown is not None and count != 0:
                raise ValueError("OWNER_CLEANUP_UNKNOWN")
        except _StickyCleanupSignal:
            raise
        except BaseException as error:
            if self._cleanup_unknown is not None:
                raise _StickyCleanupSignal(error) from error
            raise

    def _sticky_cleanup_guard(self) -> None:
        # A successful check does not make the following action atomic with it.
        if self._cleanup_unknown() is not False:
            raise RuntimeError("EXTERNAL_CLEANUP_UNKNOWN")

    def _check_local_lease_evidence(self) -> None:
        with self._condition:
            invalid = self._lease_evidence_invalid
        if invalid:
            raise _StickyCleanupSignal(RuntimeError("RUNTIME_LEASE_EVIDENCE_UNKNOWN"))

    def _strict_external_guard(self) -> None:
        self._check_local_lease_evidence()
        self._sticky_cleanup_guard()
        self._check_local_lease_evidence()

    def _check_running_evidence(self, runtime: Any, extension: Any) -> None:
        if self._cleanup_unknown is None:
            return
        try:
            self._strict_external_guard()
            self._check_local_lease_evidence()
            self._check_cleanup_evidence(runtime, extension)
            self._strict_external_guard()
        except _StickyCleanupSignal:
            raise
        except BaseException as error:
            raise _StickyCleanupSignal(error) from error

    def _checked_action(self, runtime: Any, extension: Any, action: Callable[[], Any]) -> Any:
        if self._cleanup_unknown is None:
            return action()
        self._check_running_evidence(runtime, extension)
        try:
            return action()
        finally:
            self._check_running_evidence(runtime, extension)

    def _run_sticky_cleanup(self, runtime: Any, extension: Any) -> None:
        """Perform at most one cleanup attempt; retain on any unknown outcome."""
        try:
            with self._condition:
                if self._lease_evidence_invalid:
                    raise RuntimeError("RUNTIME_LEASE_EVIDENCE_UNKNOWN")
            self._strict_external_guard()
            self._check_cleanup_evidence(runtime, extension)
            self._strict_external_guard()
            if extension is not None:
                extension.close()
                self._strict_external_guard()
                self._check_cleanup_evidence(runtime, extension)
                with self._condition:
                    if self._owner_cleanup_uncertainties != 0:
                        raise RuntimeError("OWNER_CLEANUP_UNKNOWN")
                self._strict_external_guard()
            if runtime is not None:
                runtime.close()
                self._check_running_evidence(runtime, extension)
                captured = self._capture(runtime)
                if captured is not True:
                    raise RuntimeError("RUNTIME_CLEANUP_UNKNOWN")
                with self._condition:
                    counts = (self._cleanup_failures, self._unregister_uncertainties,
                              self._unknown_registration_count)
                    bindings = self._bindings
                    strict_ack = self._strict_unregister_ack
                    lease_evidence_invalid = self._lease_evidence_invalid
                if lease_evidence_invalid:
                    raise RuntimeError("RUNTIME_CLEANUP_UNKNOWN")
                state = self._observe_external(lambda: runtime._state)
                self._check_running_evidence(runtime, extension)
                if (any(type(value) is not int or value != 0 for value in counts)
                        or bindings or type(state) is not str or state != "closed"
                        or (self._require_unregister_ack and strict_ack is not True)):
                    raise RuntimeError("RUNTIME_CLEANUP_UNKNOWN")
                self._strict_external_guard()
        except BaseException as error:
            self._enter_sticky(runtime, extension, error)

    def _enter_sticky(self, runtime: Any, extension: Any, error: BaseException) -> None:
        if isinstance(error, _StickyCleanupSignal):
            error = error.cause
        with self._condition:
            self._cleanup_sticky = True
            self._sticky_resources = (runtime, extension)
            self._sticky_errors.append(error)
            # None explicitly means that no clean count can be established.
            self._cleanup_failures = None
            self._unregister_uncertainties = None
            self._owner_cleanup_uncertainties = None
            self._unknown_registration_count = None
            with _STICKY_CLEANUP_ROOTS_LOCK:
                _STICKY_CLEANUP_ROOTS.append(self)
            self._stop = True
            self._state, self._error, self._healthy = "close_failed", "CLEANUP_FAILED", False
            self._reject_pending("NOT_READY")
            self._condition.notify_all()
        try:
            self._on_cleanup_sticky(self)
        except BaseException as callback_error:
            with self._condition:
                self._sticky_errors.append(callback_error)
                self._condition.notify_all()
        # Do not retry or release resources automatically; the independent
        # process-local root remains even if this wait is interrupted.
        with self._condition:
            while True:
                self._condition.wait()

    def _run(self) -> None:
        runtime = None
        extension = None
        creating_extension = False
        failed = False
        try:
            self._check_running_evidence(None, None)
            runtime = self._factory()
            if self._cleanup_unknown is not None:
                self._check_running_evidence(runtime, None)
            if self._require_unregister_ack:
                from bt_api_py.service_runtime import ServiceClientRuntime
                if type(runtime) is not ServiceClientRuntime or runtime._require_unregister_ack is not True:
                    raise ThreadedRuntimeError("CLEANUP_POLICY_UNPROVEN")
            with self._condition:
                if self._stop:
                    return
            if self._owner_extension_factory is not None:
                creating_extension = True
                self._check_running_evidence(runtime, None)
                extension = self._owner_extension_factory()
                if extension is None:
                    raise ValueError("INVALID_OWNER_EXTENSION")
                creating_extension = False
                if self._cleanup_unknown is not None:
                    self._check_running_evidence(runtime, extension)
            with self._condition:
                if self._stop:
                    return
            self._checked_action(runtime, extension, runtime.start)
            if self._cleanup_unknown is not None:
                self._check_running_evidence(runtime, extension)
            if extension is not None:
                self._checked_action(runtime, extension, lambda: extension.start(runtime))
                self._capture_owner_uncertainties(extension)
            self._checked_action(runtime, extension, lambda: self._capture(runtime))
            with self._condition:
                if self._cleanup_unknown is not None and not self._stop and not self._healthy:
                    raise ThreadedRuntimeError("NOT_READY")
            if not self._checked_action(runtime, extension, lambda: bool(runtime.healthy)):
                raise ThreadedRuntimeError("NOT_READY")
            with self._condition:
                if self._stop:
                    return
                if self._cleanup_unknown is not None and not self._healthy:
                    raise ThreadedRuntimeError("NOT_READY")
                if not self._stop:
                    self._state = "running"
                self._condition.notify_all()
            prefer_queue = True
            while True:
                with self._condition:
                    if self._stop:
                        break
                self._checked_action(runtime, extension, runtime.maintain)
                self._checked_action(runtime, extension, lambda: self._capture(runtime))
                with self._condition:
                    if self._cleanup_unknown is not None and not self._stop and not self._healthy:
                        raise ThreadedRuntimeError("NOT_READY")
                if not self._checked_action(runtime, extension, lambda: bool(runtime.healthy)):
                    raise ThreadedRuntimeError("NOT_READY")
                with self._condition:
                    if self._stop:
                        break
                    if self._cleanup_unknown is not None and not self._healthy:
                        raise ThreadedRuntimeError("NOT_READY")
                    now = time.monotonic()
                    ready = next(
                        (pending for pending in self._continuations if pending.due <= now),
                        None,
                    )
                    if self._queue and (ready is None or prefer_queue):
                        call = self._queue.popleft()
                        call.state = "running"
                        resuming = False
                        prefer_queue = False
                    elif ready is not None:
                        self._continuations.remove(ready)
                        call = ready
                        resuming = True
                        self._active_continuation = call
                        prefer_queue = True
                    else:
                        if self._cleanup_unknown is None:
                            delay = runtime.next_maintenance_delay()
                        else:
                            # Foreign callbacks and evidence readers must never
                            # run while the condition is held.
                            self._condition.release()
                            try:
                                delay = self._checked_action(
                                    runtime, extension, runtime.next_maintenance_delay
                                )
                            finally:
                                self._condition.acquire()
                            now = time.monotonic()
                            if self._stop or self._queue or any(
                                c.due <= now for c in self._continuations
                            ):
                                continue
                        lease_now = None
                        if delay is not None and self._lease_deadline is not None:
                            if self._cleanup_unknown is None:
                                lease_now = self._monotonic_clock()
                            else:
                                # The injected clock may reenter this runtime.
                                self._condition.release()
                                try:
                                    lease_now = self._monotonic_clock()
                                finally:
                                    self._condition.acquire()
                                now = time.monotonic()
                                if self._stop or self._queue or any(
                                    c.due <= now for c in self._continuations
                                ):
                                    continue
                        if delay is None:
                            wait = 1.0
                        elif delay > 0:
                            wait = min(1.0, delay)
                            if self._lease_deadline is not None:
                                wait = min(
                                    wait, max(0.0, self._lease_deadline - lease_now)
                                )
                        else:
                            # Renewal retry may remain due after a timeout. Keep
                            # its backoff strictly within the required lease.
                            remaining = (
                                None
                                if self._lease_deadline is None
                                else self._lease_deadline - lease_now
                            )
                            wait = (
                                0.05 if remaining is None else min(0.05, max(0.0, remaining) * 0.25)
                            )
                        if self._continuations:
                            wait = min(wait, max(0.0, min(c.due for c in self._continuations) - now))
                        self._condition.wait(wait)
                        continue
                try:
                    self._check_running_evidence(runtime, extension)
                    if type(call) is _BoundCall:
                        # Closing after dequeue must not begin a new raw call.
                        # The lock is released before any runtime/transport work.
                        with self._condition:
                            if self._stop:
                                raise ThreadedRuntimeError("BOUND_ROUTE_UNAVAILABLE")
                    if type(call) is _OwnerCall:
                        try:
                            if resuming:
                                continuation = call.continuation
                                if type(continuation) is not OwnerOperationContinuation:
                                    raise ThreadedRuntimeError("INVALID_OWNER_CONTINUATION")
                                try:
                                    OwnerOperationContinuation.__post_init__(continuation)
                                except Exception:
                                    raise ThreadedRuntimeError("INVALID_OWNER_CONTINUATION") from None
                                result = self._checked_action(runtime, extension, continuation.resume)
                            else:
                                result = self._checked_action(
                                    runtime, extension,
                                    lambda: extension.invoke(call.operation, call.arguments),
                                )
                            if type(result) is OwnerOperationContinuation:
                                try:
                                    OwnerOperationContinuation.__post_init__(result)
                                except Exception:
                                    raise ThreadedRuntimeError("INVALID_OWNER_CONTINUATION") from None
                            else:
                                result = _copy_owner_json(result, error_code="INVALID_OWNER_RESULT")
                        finally:
                            self._capture_owner_uncertainties(extension)
                            if self._cleanup_unknown is not None:
                                self._checked_action(runtime, extension, lambda: self._capture(runtime))
                    else:
                        try:
                            result = self._checked_action(
                                runtime, extension,
                                lambda: (_execute_bound_call(runtime, call)
                                         if type(call) is _BoundCall else
                                         runtime.call(call.mount, call.operation, call.arguments, **call.kwargs)),
                            )
                        finally:
                            if self._cleanup_unknown is not None:
                                self._checked_action(runtime, extension, lambda: self._capture(runtime))
                        if type(call) is _BoundCall:
                            result = _finish_bound_call(runtime, call, result)
                except Exception as error:
                    result = None
                    # Runtime errors have finite public codes; arbitrary errors
                    # must not expose application or transport details.
                    if type(call) is _OwnerCall:
                        if type(error) is OwnerOperationError:
                            try:
                                failure = OwnerOperationError(error.code)
                            except Exception:
                                failure = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
                        elif type(error) is ThreadedRuntimeError and error.code == "INVALID_OWNER_RESULT":
                            failure = ThreadedRuntimeError(error.code)
                        else:
                            failure = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
                    else:
                        failure = (
                            error if (isinstance(error, ServiceRuntimeError)
                                      or (type(call) is _BoundCall and type(error) is ThreadedRuntimeError
                                          and error.code == "BOUND_ROUTE_UNAVAILABLE"))
                            else ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN")
                        )
                except BaseException:
                    with self._condition:
                        if resuming:
                            self._active_continuation = None
                            call.continuation = None
                        call.error, call.state = (
                            ThreadedRuntimeError(
                                "OWNER_OUTCOME_UNKNOWN" if type(call) is _OwnerCall
                                else "CALL_OUTCOME_UNKNOWN"
                            ),
                            "done",
                        )
                        self._condition.notify_all()
                    raise
                else:
                    failure = None
                due = None
                invalid_due = False
                if type(call) is _OwnerCall and type(result) is OwnerOperationContinuation:
                    try:
                        due = _deadline(result.delay_seconds)
                    except BaseException:
                        # The invoke/resume already ran; an unrepresentable
                        # continuation is an unknown outcome, not a rejection.
                        invalid_due = True
                with self._condition:
                    if resuming:
                        self._active_continuation = None
                    if type(call) is _BoundCall and self._stop and call.raw_started:
                        # A running call cannot be cancelled; if shutdown raced
                        # it, retain UNKNOWN even after a syntactically valid reply.
                        result, failure = None, ThreadedRuntimeError("CALL_OUTCOME_UNKNOWN")
                    if call.state == "done":
                        # A close may have resolved a running continuation as
                        # unknown while its owner callback was still active.
                        call.continuation = None
                    elif type(call) is _OwnerCall and type(result) is OwnerOperationContinuation:
                        if self._stop or invalid_due:
                            call.continuation = None
                            call.error = ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN")
                            call.state = "done"
                        else:
                            call.continuation = result
                            call.due = due
                            self._continuations.append(call)
                    else:
                        if type(call) is _OwnerCall:
                            call.continuation = None
                        call.result, call.error, call.state = result, failure, "done"
                    self._condition.notify_all()
                del call, result, failure
        except BaseException as error:
            failed = True
            if self._cleanup_unknown is not None and (
                isinstance(error, _StickyCleanupSignal) or runtime is None or creating_extension
            ):
                # No intervening cleanup or retry is safe after this observation.
                self._enter_sticky(runtime, extension, error)
            with self._condition:
                if runtime is None or creating_extension:
                    self._error = "FACTORY_FAILED_CLEANUP_UNPROVEN"
                elif self._error != "START_TIMEOUT_CLEANUP_PENDING":
                    self._error = (
                        "START_FAILED_CLEANUP_PENDING"
                        if self._state == "starting"
                        else "MAINTENANCE_FAILED"
                    )
                self._state = "failed"
                self._healthy = False
                self._stop = True
                self._reject_pending("NOT_READY")
                self._condition.notify_all()
        finally:
            with self._condition:
                original_error = self._error
                if self._cleanup_sticky:
                    return
            if self._cleanup_unknown is not None:
                self._run_sticky_cleanup(runtime, extension)
            elif runtime is not None or extension is not None:
                extension_closed = extension is None
                runtime_closed = runtime is None
                while True:
                    with self._condition:
                        attempt = self._cleanup_retry
                    close_failed = False
                    if not extension_closed:
                        try:
                            extension.close()
                            extension_closed = True
                        except BaseException:
                            close_failed = True
                    if extension is not None:
                        try:
                            self._capture_owner_uncertainties(extension)
                        except BaseException:
                            close_failed = True
                    if not runtime_closed:
                        try:
                            runtime.close()
                            runtime_closed = True
                        except BaseException:
                            close_failed = True
                    if runtime is not None:
                        try:
                            self._capture(runtime)
                        except BaseException:
                            close_failed = True
                    if not close_failed:
                        break
                    with self._condition:
                        self._state, self._error = "close_failed", "CLEANUP_FAILED"
                        self._condition.notify_all()
                        while attempt == self._cleanup_retry:
                            self._condition.wait()
            with self._condition:
                self._healthy = False
                self._error = original_error
                if not failed:
                    self._state = "closed"
                    if self._error == "START_TIMEOUT_CLEANUP_PENDING":
                        self._error = "START_TIMEOUT"
                else:
                    self._state = "failed"
                    if self._error == "START_FAILED_CLEANUP_PENDING":
                        self._error = "START_FAILED"
                    elif self._error == "START_TIMEOUT_CLEANUP_PENDING":
                        self._error = "START_TIMEOUT"
                self._condition.notify_all()
