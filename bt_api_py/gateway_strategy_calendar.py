"""Authenticated, simulation-only Gateway calendar reads for a strategy."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from types import MappingProxyType

from bt_api_py.gateway_calendar_codec import decode_calendar_query, validate_calendar_response
from bt_api_py.service_runtime import RegisteredBinding
from bt_api_py.service_runtime_thread import OwnerOperationError, ThreadedRuntimeError, _timeout, _copy_wire

CALENDAR_OPERATIONS = frozenset({"calendar.query.v1"})


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class GatewayCalendarClientSettings:
    mode: str
    scopes: tuple

    def __post_init__(self):
        if self.mode != "SIMULATION_ONLY" or type(self.scopes) is not tuple or not 1 <= len(self.scopes) <= 256:
            raise ValueError("INVALID_CALENDAR_SETTINGS")
        normalized = []
        try:
            for scope in self.scopes:
                if type(scope) is not dict or set(scope) != {"route", "venue", "kind", "product", "contract"}:
                    raise ValueError
                echo, _ = decode_calendar_query({"mode": self.mode, **scope, "trading_day": "2026-01-01"})
                normalized.append(tuple((key, echo[key]) for key in ("route", "venue", "kind", "product", "contract")))
            if len(set(normalized)) != len(normalized):
                raise ValueError
        except Exception:
            raise ValueError("INVALID_CALENDAR_SETTINGS") from None
        object.__setattr__(self, "scopes", tuple(normalized))

    def permits(self, scope):
        return tuple((key, scope[key]) for key in ("route", "venue", "kind", "product", "contract")) in self.scopes


@dataclass(frozen=True)
class GatewayCalendarQueryResult:
    """Detached simulation response, distinct from the direct calendar contract."""
    schema: str
    scope: object
    registration_id: str
    service_epoch: str
    status: str
    reason: str | None
    binding: object
    venue_day: object
    windows: tuple
    source_authentication: str
    trade_authorization: str

    @classmethod
    def from_wire(cls, wire):
        value = validate_calendar_response(wire)
        return cls(**{key: _freeze(value[key]) for key in cls.__dataclass_fields__})


class GatewayCalendarOwner:
    def __init__(self, settings, *, account_ref, scope_ref, route, peer):
        self._settings, self._account_ref, self._scope_ref = settings, account_ref, scope_ref
        self._route, self._peer = route, peer
        self._runtime = self._thread = None
        self._pid = os.getpid()
        self._closed = False

    @property
    def cleanup_uncertainties(self):
        return 0

    def start(self, runtime):
        if self._closed or self._runtime is not None:
            raise OwnerOperationError("CALENDAR_CLIENT_NOT_NEW")
        self._runtime, self._thread = runtime, threading.current_thread()

    def _binding(self):
        try:
            runtime, route = self._runtime, self._route
            matches = [b for b in runtime.bindings if type(b) is RegisteredBinding and b.active and b.mount == "store.calendar"]
            if len(matches) != 1:
                raise ValueError
            b = matches[0]
            proof = runtime.authenticated_peer_for_binding(b)
            if (proof is None or proof[0] != self._peer or b.remote_lease_inactive
                or runtime._monotonic_now() >= b.lease_deadline
                or b.service_id != route.service_id or b.instance_id != route.instance_id
                or b.scope_ref != self._scope_ref or b.scope.account_ref != self._account_ref
                or b.scope != route.scope
                or runtime.run_id != route.scope.run_id or runtime.identity != route.identity
                or not CALENDAR_OPERATIONS <= b.operations):
                raise ValueError
            return b, proof
        except Exception:
            raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN") from None

    def invoke(self, operation, arguments):
        if (os.getpid() != self._pid or threading.current_thread() is not self._thread
            or self._closed or self._runtime is None):
            raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN")
        if operation == "validate":
            if type(arguments) is not dict or set(arguments) != {"registration_id", "service_epoch"}:
                raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
            binding, _ = self._binding()
            if (binding.registration_id != arguments["registration_id"]
                    or binding.service_epoch != arguments["service_epoch"]):
                raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN")
            return {"valid": True}
        if operation != "query":
            raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
        try:
            echo, _ = decode_calendar_query(arguments)
            if not self._settings.permits(echo):
                raise ValueError
        except Exception:
            raise OwnerOperationError("CALENDAR_SCOPE_NOT_CONFIGURED") from None
        before, proof = self._binding()
        try:
            raw = self._runtime.call("store.calendar", "calendar.query.v1", echo,
                                     account_ref=self._account_ref, scope_ref=self._scope_ref)
            after, proof_after = self._binding()
            if after != before or proof_after != proof:
                raise ValueError
            wire = validate_calendar_response(_copy_wire(raw))
            if (wire["scope"] != echo or wire["registration_id"] != before.registration_id
                or wire["service_epoch"] != before.service_epoch):
                raise ValueError
            return wire
        except Exception:
            raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN") from None

    def close(self):
        self._closed = True
        self._runtime = None


class GatewayStrategyCalendarClient:
    def __init__(self, runtime, settings, *, route, peer):
        self._runtime, self._settings = runtime, settings
        self._route, self._peer = route, peer
        self._pid, self._lock, self._pending = os.getpid(), threading.Lock(), None

    def query(self, *, route, venue, kind, product, contract, trading_day, timeout_seconds=5.0):
        timeout = _timeout(timeout_seconds)
        deadline = time.monotonic() + timeout

        def remaining():
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")
            return budget

        if os.getpid() != self._pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        args, _ = decode_calendar_query(dict(mode="SIMULATION_ONLY", route=route, venue=venue,
                                               kind=kind, product=product, contract=contract,
                                               trading_day=trading_day))
        if not self._settings.permits(args):
            raise ValueError("CALENDAR_SCOPE_NOT_CONFIGURED")
        if not self._lock.acquire(blocking=False):
            raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
        try:
            if self._pending is None:
                remaining()
                self._pending = {"args": args, "query": self._runtime.submit_owner("calendar.query", args),
                                 "wire": None, "validation": None}
            pending = self._pending
            if args != pending["args"]:
                raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
            try:
                if pending["wire"] is None:
                    pending["wire"] = pending["query"].result(timeout_seconds=remaining())
                # A validation that timed out may have completed before a
                # subsequent registration change. Drain it, then validate
                # afresh in this collection attempt.
                if pending["validation"] is not None:
                    pending["validation"].result(timeout_seconds=remaining())
                    pending["validation"] = None
                wire = validate_calendar_response(pending["wire"])
                remaining()
                handle = self._runtime.submit_owner("calendar.validate", {
                    "registration_id": wire["registration_id"], "service_epoch": wire["service_epoch"]})
                pending["validation"] = handle
                if handle.result(timeout_seconds=remaining()) != {"valid": True}:
                    raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN")
                remaining()
                snapshot = self._runtime.status()
                matches = [b for b in snapshot.bindings if b.mount == "store.calendar" and b.active]
                if (snapshot.state != "running" or not snapshot.healthy or len(matches) != 1
                        or matches[0].registration_id != wire["registration_id"]
                        or matches[0].service_epoch != wire["service_epoch"]
                        or matches[0].service_id != self._route.service_id
                        or matches[0].instance_id != self._route.instance_id
                        or matches[0].scope_ref != self._route.scope_ref
                        or matches[0].scope != self._route.scope
                        or matches[0].authenticated_peer != self._peer
                        or not matches[0].server_key_fingerprint
                        or snapshot.identity != self._route.identity
                        or snapshot.run_id != self._route.scope.run_id):
                    raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN")
                result = GatewayCalendarQueryResult.from_wire(wire)
                remaining()
            except ThreadedRuntimeError as exc:
                if exc.code not in {"OWNER_TIMEOUT_QUEUED", "OWNER_TIMEOUT_RUNNING"}:
                    self._pending = None
                    raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN") from None
                raise
            except Exception:
                self._pending = None
                raise OwnerOperationError("CALENDAR_OUTCOME_UNKNOWN") from None
            self._pending = None
            return result
        finally:
            self._lock.release()
