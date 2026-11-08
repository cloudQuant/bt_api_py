"""Provider-independent instrument lookup on the strategy runtime owner."""

from __future__ import annotations

import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._contracts.models import InstrumentSpec
from bt_api_py.gateway_instrument_spec_codec import (
    decode_gateway_instrument_spec,
    encode_gateway_instrument_spec,
)
from bt_api_py.service_runtime import ServiceRuntimeError
from bt_api_py.service_runtime_thread import (
    OwnerOperationContinuation,
    OwnerOperationError,
    ThreadedRuntimeError,
    _timeout,
)

INSTRUMENT_OPERATIONS = frozenset(
    {
        "account.instrument_spec.begin",
        "account.instrument_spec.status",
        "account.instrument_spec.cancel",
    }
)
_ID = re.compile(r"[0-9a-f]{32}\Z")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_REPLY_FIELDS = {"operation_id", "status", "code", "snapshot"}
_SNAPSHOT_FIELDS = {
    "instrument",
    "spec",
    "session_generation",
    "connection_generation",
    "trading_day",
    "remaining_seconds",
}


@dataclass(frozen=True)
class GatewayInstrumentClientSettings:
    instruments: tuple[MarketInstrumentKey, ...]
    requested_operations: frozenset[str]

    def __post_init__(self):
        try:
            if (
                type(self.instruments) is not tuple
                or not 1 <= len(self.instruments) <= 256
                or any(type(key) is not MarketInstrumentKey for key in self.instruments)
                or type(self.requested_operations) is not frozenset
                or self.requested_operations != INSTRUMENT_OPERATIONS
            ):
                raise ValueError
            keys = tuple(
                MarketInstrumentKey.from_dict(key.to_dict()) for key in self.instruments
            )
            if (
                len(set(keys)) != len(keys)
                or any(
                    key.provider != "ctp"
                    or key.product_type != "future"
                    or key.instrument_id != key.native_symbol
                    for key in keys
                )
                or len({key.native_symbol for key in keys}) != len(keys)
            ):
                raise ValueError
            object.__setattr__(self, "instruments", keys)
            object.__setattr__(
                self, "requested_operations", frozenset(self.requested_operations)
            )
        except Exception:
            raise ValueError("INVALID_INSTRUMENT_SETTINGS") from None


@dataclass(frozen=True)
class GatewayInstrumentSpecView:
    instrument: MarketInstrumentKey
    spec: InstrumentSpec
    session_generation: str
    connection_generation: int
    trading_day: str
    fresh_until_monotonic: float
    _operation_id: str = field(repr=False, compare=False)


def _decode_view(raw: object) -> GatewayInstrumentSpecView:
    try:
        if type(raw) is not dict or set(raw) != _SNAPSHOT_FIELDS - {
            "remaining_seconds"
        } | {"fresh_until_monotonic", "operation_id"}:
            raise ValueError
        key = MarketInstrumentKey.from_dict(raw["instrument"])
        spec = decode_gateway_instrument_spec(
            raw["spec"],
            expected_exchange_name="CTP___FUTURE",
            expected_symbol=key.native_symbol,
        )
        session, connection, day, until, operation_id = (
            raw[x]
            for x in (
                "session_generation",
                "connection_generation",
                "trading_day",
                "fresh_until_monotonic",
                "operation_id",
            )
        )
        if (
            type(session) is not str
            or not session
            or len(session) > 128
            or session != session.strip()
            or not session.isprintable()
            or type(connection) is not int
            or not 0 < connection < 2**63
            or type(day) is not str
            or re.fullmatch(r"[0-9]{8}", day) is None
            or datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") != day
            or type(until) not in (int, float)
            or not math.isfinite(until)
            or type(operation_id) is not str
            or _ID.fullmatch(operation_id) is None
        ):
            raise ValueError
        return GatewayInstrumentSpecView(
            key, spec, session, connection, day, float(until), operation_id
        )
    except Exception:
        raise OwnerOperationError("INVALID_INSTRUMENT_REPLY") from None


def _view_wire(view: GatewayInstrumentSpecView) -> dict[str, Any]:
    return {
        "instrument": view.instrument.to_dict(),
        "spec": encode_gateway_instrument_spec(view.spec),
        "session_generation": view.session_generation,
        "connection_generation": view.connection_generation,
        "trading_day": view.trading_day,
        "fresh_until_monotonic": view.fresh_until_monotonic,
        "operation_id": view._operation_id,
    }


class GatewayInstrumentOwner:
    """All remote calls and registration fences execute on the existing owner."""

    def __init__(self, settings, *, account_ref, scope_ref, mount="store.account"):
        if type(settings) is not GatewayInstrumentClientSettings:
            raise ValueError("INVALID_INSTRUMENT_SETTINGS")
        self._settings = GatewayInstrumentClientSettings(
            settings.instruments, settings.requested_operations
        )
        self._account_ref, self._scope_ref, self._mount = account_ref, scope_ref, mount
        self._runtime = None
        self._thread = None
        self._pid = os.getpid()
        self._pending = {}
        self._views = {}
        self._closed = False
        self._uncertain = set()

    @property
    def cleanup_uncertainties(self):
        return len(self._uncertain)

    def _check(self):
        if os.getpid() != self._pid:
            raise OwnerOperationError("WRONG_PROCESS")
        if self._thread is not None and threading.current_thread() is not self._thread:
            raise OwnerOperationError("WRONG_OWNER_THREAD")
        if self._closed or self._runtime is None:
            raise OwnerOperationError("INSTRUMENT_CLIENT_UNAVAILABLE")

    def start(self, runtime):
        if self._closed or self._runtime is not None:
            raise OwnerOperationError("INSTRUMENT_CLIENT_NOT_NEW")
        self._thread, self._runtime = threading.current_thread(), runtime

    def _binding(self):
        try:
            binding = self._runtime.account_binding_view(
                self._mount,
                self._account_ref,
                required_operations=INSTRUMENT_OPERATIONS,
            )
            if (
                binding.scope_ref != self._scope_ref
                or binding.account_ref != self._account_ref
            ):
                raise ValueError
            return (
                binding.mount,
                binding.service_id,
                binding.instance_id,
                binding.scope_ref,
                binding.account_ref,
                binding.registration_id,
                binding.service_epoch,
                binding.rpc_scope,
                binding.transport_ref,
                binding.operations,
            )
        except Exception:
            raise OwnerOperationError("INSTRUMENT_BINDING_UNAVAILABLE") from None

    def _call(self, operation, args, signature):
        if self._binding() != signature:
            raise OwnerOperationError("INSTRUMENT_BINDING_CHANGED")
        sent = time.monotonic()
        try:
            result = self._runtime.call(
                self._mount,
                operation,
                args,
                account_ref=self._account_ref,
                scope_ref=self._scope_ref,
            )
        except ServiceRuntimeError:
            raise OwnerOperationError("INSTRUMENT_OUTCOME_UNKNOWN") from None
        except Exception:
            raise OwnerOperationError("INSTRUMENT_OUTCOME_UNKNOWN") from None
        if self._binding() != signature:
            raise OwnerOperationError("INSTRUMENT_BINDING_CHANGED")
        return result, sent

    def _reply(self, raw, operation_id, key, sent):
        try:
            if type(raw) not in (dict, MappingProxyType):
                raise ValueError
            if set(raw) != _REPLY_FIELDS or raw["operation_id"] != operation_id:
                raise ValueError
            status, code, snap = raw["status"], raw["code"], raw["snapshot"]
            if (
                type(status) is not str
                or status
                not in {"pending", "ready", "failed", "cancelled", "not_found"}
                or (
                    code is not None
                    and (type(code) is not str or _CODE.fullmatch(code) is None)
                )
                or (status in {"pending", "ready"} and code is not None)
                or (status != "ready" and snap is not None)
            ):
                raise ValueError
            if status != "ready":
                return status, code, None
            if type(snap) not in (dict, MappingProxyType):
                raise ValueError
            if (
                set(snap) != _SNAPSHOT_FIELDS
                or MarketInstrumentKey.from_dict(snap["instrument"]) != key
            ):
                raise ValueError
            remaining = snap["remaining_seconds"]
            if (
                type(remaining) not in (int, float)
                or not math.isfinite(remaining)
                or not 0 < remaining <= 35
            ):
                raise ValueError
            wire = {
                name: snap[name] for name in _SNAPSHOT_FIELDS - {"remaining_seconds"}
            }
            wire.update(
                operation_id=operation_id, fresh_until_monotonic=sent + remaining
            )
            view = _decode_view(wire)
            if time.monotonic() >= view.fresh_until_monotonic:
                raise OwnerOperationError("STALE_INSTRUMENT_SPEC")
            return status, code, _view_wire(view)
        except OwnerOperationError:
            raise
        except Exception:
            raise OwnerOperationError("INVALID_INSTRUMENT_REPLY") from None

    def invoke(self, operation, arguments):
        self._check()
        if type(arguments) is not dict:
            raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
        if operation == "cleanup":
            if arguments:
                raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
            if not self._pending:
                return {"remaining": 0, "resolved": True}
            operation_id = next(iter(self._pending))
            key, signature = self._pending[operation_id]
            try:
                raw, sent = self._call(
                    "account.instrument_spec.cancel",
                    {"operation_id": operation_id},
                    signature,
                )
                status, _, _ = self._reply(raw, operation_id, key, sent)
                if status not in {"cancelled", "not_found", "failed", "ready"}:
                    raise ValueError
            except Exception:
                self._uncertain.add(operation_id)
                return {"remaining": len(self._pending), "resolved": False}
            self._uncertain.discard(operation_id)
            self._pending.pop(operation_id)
            return {"remaining": len(self._pending), "resolved": True}
        if operation == "validate":
            if set(arguments) != {"view"}:
                raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
            view = _decode_view(arguments["view"])
            record = self._views.get(view._operation_id)
            if (
                record is None
                or record[0] != arguments["view"]
                or self._binding() != record[1]
                or time.monotonic() >= view.fresh_until_monotonic
            ):
                raise OwnerOperationError("STALE_INSTRUMENT_SPEC")
            return {"valid": True}
        if operation != "lookup" or set(arguments) != {"instrument"}:
            raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
        now = time.monotonic()
        self._views = {
            k: v for k, v in self._views.items() if v[0]["fresh_until_monotonic"] > now
        }
        if len(self._views) + len(self._pending) >= 256 or len(self._pending) >= 64:
            raise OwnerOperationError("INSTRUMENT_CAPACITY_EXCEEDED")
        try:
            key = MarketInstrumentKey.from_dict(arguments["instrument"])
            if key not in self._settings.instruments:
                raise ValueError
        except Exception:
            raise OwnerOperationError("INSTRUMENT_NOT_CONFIGURED") from None
        signature = self._binding()
        operation_id = uuid.uuid4().hex
        self._pending[operation_id] = (key, signature)
        deadline = time.monotonic() + 35

        def step(first=False):
            self._check()
            if time.monotonic() >= deadline:
                raise OwnerOperationError("INSTRUMENT_QUERY_TIMEOUT")
            method = "begin" if first else "status"
            args = {"operation_id": operation_id}
            if first:
                args["instrument"] = key.to_dict()
            raw, sent = self._call("account.instrument_spec." + method, args, signature)
            status, code, wire = self._reply(raw, operation_id, key, sent)
            if status == "pending":
                return OwnerOperationContinuation(step, 0.05)
            self._pending.pop(operation_id, None)
            if status != "ready":
                raise OwnerOperationError(code or "INSTRUMENT_QUERY_FAILED")
            self._views[operation_id] = (wire, signature)
            # Expired views need no owner-side retention.
            now = time.monotonic()
            self._views = {
                k: v
                for k, v in self._views.items()
                if v[0]["fresh_until_monotonic"] > now
            }
            return wire

        return step(True)

    def close(self):
        if self._closed:
            return
        self._check()
        for operation_id, (key, signature) in tuple(self._pending.items()):
            try:
                raw, sent = self._call(
                    "account.instrument_spec.cancel",
                    {"operation_id": operation_id},
                    signature,
                )
                status, _, _ = self._reply(raw, operation_id, key, sent)
                if status not in {"cancelled", "not_found", "failed", "ready"}:
                    raise ValueError
            except Exception:
                self._uncertain.add(operation_id)
            self._pending.pop(operation_id, None)
        self._views.clear()
        self._closed = True


class GatewayStrategyInstrumentClient:
    def __init__(self, runtime, settings):
        self._runtime, self._settings = runtime, settings
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._pending = None
        self._unknown = False
        self._spec_pending = None

    def _invoke(self, operation, arguments, timeout_seconds):
        timeout = _timeout(timeout_seconds)
        if timeout > threading.TIMEOUT_MAX:
            raise ValueError("timeout_seconds exceeds platform limit")
        if os.getpid() != self._pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        if not self._lock.acquire(blocking=False):
            raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
        try:
            if self._pending is None:
                handle = self._runtime.submit_owner(
                    "instrument." + operation, arguments
                )
                self._pending = (operation, arguments, handle)
            else:
                old_operation, old_arguments, handle = self._pending
                if (operation, arguments) != (old_operation, old_arguments):
                    raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
            try:
                result = handle.result(timeout_seconds=timeout_seconds)
            except ThreadedRuntimeError as exc:
                # These codes arise only after the runtime has resolved the
                # handle. Other errors may leave the call queued or running.
                if exc.code in {
                    "OWNER_CANCELLED",
                    "CLOSED_BEFORE_START",
                    "NOT_READY",
                    "OWNER_OUTCOME_UNKNOWN",
                    "INVALID_OWNER_RESULT",
                    "INVALID_OWNER_CONTINUATION",
                }:
                    self._pending = None
                    if operation == "lookup":
                        self._unknown = True
                raise
            except OwnerOperationError:
                self._pending = None
                if operation == "lookup":
                    self._unknown = True
                raise
            self._pending = None
            return result
        finally:
            self._lock.release()

    def lookup(
        self, exchange_name: str, symbol: str, *, timeout_seconds: float = 5.0
    ) -> GatewayInstrumentSpecView:
        if self._unknown:
            raise ThreadedRuntimeError("INSTRUMENT_CLEANUP_REQUIRED")
        if (
            type(exchange_name) is not str
            or exchange_name != "CTP___FUTURE"
            or type(symbol) is not str
        ):
            raise ValueError("INSTRUMENT_NOT_CONFIGURED")
        keys = [
            key for key in self._settings.instruments if key.native_symbol == symbol
        ]
        if len(keys) != 1:
            raise ValueError("INSTRUMENT_NOT_CONFIGURED")
        wire = self._invoke(
            "lookup", {"instrument": keys[0].to_dict()}, timeout_seconds
        )
        try:
            view = _decode_view(wire)
            if (
                view.instrument != keys[0]
                or time.monotonic() >= view.fresh_until_monotonic
            ):
                raise OwnerOperationError("STALE_INSTRUMENT_SPEC")
            return view
        except Exception:
            self._unknown = True
            raise

    def cleanup_pending(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        """Cancel one retained job by its original ID; repeat while remaining is nonzero."""
        result = self._invoke("cleanup", {}, timeout_seconds)
        if (
            type(result) is not dict
            or set(result) != {"remaining", "resolved"}
            or type(result["remaining"]) is not int
            or not 0 <= result["remaining"] <= 64
            or type(result["resolved"]) is not bool
        ):
            raise ThreadedRuntimeError("INVALID_OWNER_RESULT")
        if result["remaining"] == 0 and result["resolved"]:
            self._unknown = False
        return result

    def validate(
        self, view: GatewayInstrumentSpecView, *, timeout_seconds: float = 5.0
    ) -> None:
        if type(view) is not GatewayInstrumentSpecView:
            raise ValueError("INVALID_INSTRUMENT_VIEW")
        # A timed-out validation still owns its handle. Drain that same call
        # even when the view expired in the meantime, then reject the result.
        pending_validation = (
            self._pending is not None and self._pending[0] == "validate"
        )
        if time.monotonic() >= view.fresh_until_monotonic and not pending_validation:
            raise OwnerOperationError("STALE_INSTRUMENT_SPEC")
        result = self._invoke("validate", {"view": _view_wire(view)}, timeout_seconds)
        if (
            type(result) is not dict
            or set(result) != {"valid"}
            or result["valid"] is not True
            or time.monotonic() >= view.fresh_until_monotonic
        ):
            raise OwnerOperationError("STALE_INSTRUMENT_SPEC")

    def get_instrument_spec(
        self, exchange_name: str, symbol: str, *, timeout_seconds: float = 5.0
    ) -> InstrumentSpec:
        if self._spec_pending is None:
            view = self.lookup(exchange_name, symbol, timeout_seconds=timeout_seconds)
            self._spec_pending = (exchange_name, symbol, view)
        else:
            old_exchange, old_symbol, view = self._spec_pending
            if (exchange_name, symbol) != (old_exchange, old_symbol):
                raise ThreadedRuntimeError("OWNER_OPERATION_PENDING")
        try:
            self.validate(view, timeout_seconds=timeout_seconds)
        except Exception:
            if self._pending is None:
                self._spec_pending = None
            raise
        self._spec_pending = None
        return view.spec
