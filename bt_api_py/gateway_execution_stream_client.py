"""Strategy-side authenticated Gateway execution stream client.

This port does not persist a projection or cursor, retry calls, acknowledge on
read, or establish provider provenance. The caller must durably apply events
and its cursor before issuing an explicit acknowledgement. A lost reply leaves
an unknown outcome, including for an acknowledgement that may have committed.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.gateway_execution_stream_protocol import (
    GatewayExecutionStreamPage,
    GatewayExecutionStreamProgress,
    gateway_execution_consumer_ref,
)
from bt_api_base.service_protocol import (
    MAX_WIRE_MESSAGE_BYTES,
    WireIdentity,
    WireScope,
    _freeze_payload,
    _thaw_json_value,
)

from bt_api_py.gateway_direct_client import (
    _NOT_STARTED_ERRORS,
    GatewayClientRoute,
    _check_binding_route,
)
from bt_api_py.service_runtime import ServiceRuntimeError, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime

_REQUEST_DENIALS = frozenset(
    {
        "INVALID_REQUEST",
        "SCOPE_DENIED",
        "EVENTS_UNAVAILABLE",
        "INVALID_SEQUENCE",
        "EPOCH_MISMATCH",
        "CONSUMER_CAPACITY_EXCEEDED",
        "REPLY_TOO_LARGE",
    }
)


@dataclass(frozen=True, slots=True)
class GatewayExecutionReadContext:
    """Local request parameters for cursor matching, not provider or authorization proof."""

    binding: GatewayCommandBinding
    consumer_ref: str
    after_sequence: int
    expected_epoch: str | None
    limit: int

    def __post_init__(self) -> None:
        if type(self.binding) is not GatewayCommandBinding:
            raise ValueError("INVALID_READ_CONTEXT")
        GatewayCommandBinding(
            self.binding.journal_id,
            self.binding.account_scope,
            self.binding.strategy_scope,
            self.binding.principal,
        )
        if (
            type(self.consumer_ref) is not str
            or not self.consumer_ref.startswith("gexec:")
            or not _epoch(self.consumer_ref[6:])
            or not _sequence(self.after_sequence)
            or type(self.limit) is not int
            or not 1 <= self.limit <= 256
            or (self.expected_epoch is not None and not _epoch(self.expected_epoch))
            or (self.after_sequence > 0 and self.expected_epoch is None)
        ):
            raise ValueError("INVALID_READ_CONTEXT")


@dataclass(frozen=True, slots=True)
class GatewayExecutionStreamClientResult:
    outcome: str
    page: GatewayExecutionStreamPage | None = None
    progress: GatewayExecutionStreamProgress | None = None
    code: str | None = None
    request_context: GatewayExecutionReadContext | None = None


def _not_started(code: str) -> GatewayExecutionStreamClientResult:
    return GatewayExecutionStreamClientResult("TRANSPORT_NOT_STARTED", code=code)


def _sequence(value: object) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _epoch(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _valid_timeout(value: object) -> bool:
    return (
        type(value) in (int, float) and 0 < value <= threading.TIMEOUT_MAX and math.isfinite(value)
    )


def _detach_wire(value: object) -> Any:
    """Turn the runtime's frozen wire containers into bounded DTO input."""
    remaining = [100_000, MAX_WIRE_MESSAGE_BYTES]

    def check(item: object, depth: int) -> None:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 16:
            raise ValueError
        if type(item) is str:
            if len(item) > remaining[1]:
                raise ValueError
            remaining[1] -= len(item.encode("utf-8", errors="strict"))
            if remaining[1] < 0:
                raise ValueError
            return
        if item is None or type(item) in (bool, int, float):
            return
        if type(item) in (list, tuple):
            if len(item) > 256:
                raise ValueError
            for child in item:
                check(child, depth + 1)
            return
        if type(item) in (dict, MappingProxyType):
            if len(item) > 256:
                raise ValueError
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError
                check(key, depth + 1)
                check(child, depth + 1)
            return
        raise ValueError

    check(value, 0)
    return _thaw_json_value(_freeze_payload(value))


class GatewayExecutionStreamClient:
    """Use an existing started runtime; explicit operations are independently granted."""

    def __init__(
        self,
        runtime: ThreadedServiceClientRuntime,
        *,
        binding: GatewayCommandBinding,
        route: GatewayClientRoute,
    ) -> None:
        if (
            type(runtime) is not ThreadedServiceClientRuntime
            or type(binding) is not GatewayCommandBinding
        ):
            raise TypeError("INVALID_CLIENT_BINDING")
        _check_binding_route(binding, route)
        if route.identity.delegate_ref is not None:
            raise ValueError("INVALID_CLIENT_ROUTE")
        self._consumer_ref = gateway_execution_consumer_ref(
            principal_ref=route.identity.principal_ref,
            actor_ref=route.identity.actor_ref,
            execution_domain_ref=route.scope.execution_domain_ref,
            account_scope=binding.account_scope,
            strategy_scope=binding.strategy_scope,
        )
        self._runtime, self._binding, self._route = runtime, binding, route
        self._pid = os.getpid()
        self._lock = threading.RLock()

    def _route_ready(self, operation: str) -> bool:
        try:
            snapshot = self._runtime.status()
            if (
                snapshot.state != "running"
                or not snapshot.thread_alive
                or not snapshot.healthy
                or type(snapshot.identity) is not WireIdentity
                or snapshot.identity != self._route.identity
                or snapshot.run_id != self._route.scope.run_id
            ):
                return False
            matches = [
                b
                for b in snapshot.bindings
                if b.active is True
                and b.required is True
                and b.mount == self._route.mount
                and b.service_id == self._route.service_id
                and b.instance_id == self._route.instance_id
                and b.scope_ref == self._route.scope_ref
                and type(b.scope) is WireScope
                and b.scope == self._route.scope
                and type(b.operations) is frozenset
                and operation in b.operations
            ]
            return len(matches) == 1
        except Exception:
            return False

    def _rpc(
        self,
        operation: str,
        arguments: dict[str, Any],
        timeout_seconds: float,
        *,
        request_context: GatewayExecutionReadContext | None = None,
    ) -> GatewayExecutionStreamClientResult:
        if not self._route_ready(operation):
            return _not_started("ROUTE_UNAVAILABLE")
        try:
            result = self._runtime.call(
                self._route.mount,
                operation,
                arguments,
                account_ref=self._binding.account_scope,
                scope_ref=self._route.scope_ref,
                max_reply_message_bytes=MAX_WIRE_MESSAGE_BYTES,
                timeout_seconds=timeout_seconds,
            )
        except ServiceRuntimeRequestNotSentError:
            return _not_started("REQUEST_NOT_SENT")
        except ThreadedRuntimeError as error:
            return (
                _not_started(error.code)
                if error.code in _NOT_STARTED_ERRORS
                else GatewayExecutionStreamClientResult("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
            )
        except ServiceRuntimeError as error:
            if type(error) is ServiceRuntimeError and error.code == "REPLY_TOO_LARGE":
                return GatewayExecutionStreamClientResult("REQUEST_DENIED", code="REPLY_TOO_LARGE")
            return GatewayExecutionStreamClientResult("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
        except Exception:
            return GatewayExecutionStreamClientResult("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
        try:
            result = _detach_wire(result)
            if type(result) is not dict or type(result.get("status")) is not str:
                raise ValueError
            if result.get("status") == "REQUEST_REJECTED":
                if (
                    set(result) != {"status", "code"}
                    or type(result["code"]) is not str
                    or result["code"] not in _REQUEST_DENIALS
                ):
                    raise ValueError
                return GatewayExecutionStreamClientResult("REQUEST_DENIED", code=result["code"])
            field = "page" if operation == "direct.events.read_after" else "progress"
            if set(result) != {"status", field} or result.get("status") != "OK":
                raise ValueError
            if field == "page":
                page = GatewayExecutionStreamPage.from_mapping(
                    result[field],
                    expected_binding=self._binding,
                    expected_consumer_ref=self._consumer_ref,
                )
                if request_context is None:
                    raise ValueError
                after = request_context.after_sequence
                if (
                    (
                        request_context.expected_epoch is not None
                        and page.epoch != request_context.expected_epoch
                    )
                    or page.offered_sequence < after
                    or len(page.events) > request_context.limit
                    or any(
                        event.sequence <= after or event.sequence > page.offered_sequence
                        for event in page.events
                    )
                ):
                    raise ValueError
                return GatewayExecutionStreamClientResult(
                    "OBSERVED", page=page, request_context=request_context
                )
            progress = GatewayExecutionStreamProgress.from_mapping(
                result[field],
                expected_binding=self._binding,
                expected_consumer_ref=self._consumer_ref,
            )
            if operation == "direct.events.ack" and (
                progress.epoch != arguments["expected_epoch"]
                or progress.acknowledged_sequence < arguments["sequence"]
                or progress.acknowledged_sequence > progress.offered_sequence
            ):
                raise ValueError
            return GatewayExecutionStreamClientResult("OBSERVED", progress=progress)
        except Exception:
            return GatewayExecutionStreamClientResult("UNKNOWN", code="INVALID_REPLY")

    def read_after(
        self,
        after_sequence: int = 0,
        expected_epoch: str | None = None,
        limit: int = 256,
        *,
        timeout_seconds: float = 5.0,
    ) -> GatewayExecutionStreamClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            if (
                not _sequence(after_sequence)
                or type(limit) is not int
                or not 1 <= limit <= 256
                or (expected_epoch is not None and not _epoch(expected_epoch))
                or (after_sequence > 0 and expected_epoch is None)
                or not _valid_timeout(timeout_seconds)
            ):
                return _not_started("INVALID_ARGUMENTS")
            request_context = GatewayExecutionReadContext(
                self._binding, self._consumer_ref, after_sequence, expected_epoch, limit
            )
            return self._rpc(
                "direct.events.read_after",
                {
                    "after_sequence": after_sequence,
                    "expected_epoch": expected_epoch,
                    "limit": limit,
                },
                timeout_seconds,
                request_context=request_context,
            )

    def progress(self, *, timeout_seconds: float = 5.0) -> GatewayExecutionStreamClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            if not _valid_timeout(timeout_seconds):
                return _not_started("INVALID_ARGUMENTS")
            return self._rpc("direct.events.progress", {}, timeout_seconds)

    def ack(
        self, sequence: int, expected_epoch: str, *, timeout_seconds: float = 5.0
    ) -> GatewayExecutionStreamClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            if (
                not _sequence(sequence)
                or not _epoch(expected_epoch)
                or not _valid_timeout(timeout_seconds)
            ):
                return _not_started("INVALID_ARGUMENTS")
            return self._rpc(
                "direct.events.ack",
                {"sequence": sequence, "expected_epoch": expected_epoch},
                timeout_seconds,
            )
