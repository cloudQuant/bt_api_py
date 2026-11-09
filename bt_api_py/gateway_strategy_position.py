"""Authenticated, strategy-scoped Gateway position observations.

These reads are snapshots, not reservations, provider close capacity, or a
trade authorization. Gateway must still check close ownership atomically.
"""
from __future__ import annotations

import datetime
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext

from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireScope

from bt_api_py.gateway_direct_client import GatewayClientRoute, _check_binding_route
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

_OPERATIONS = frozenset({"direct.strategy_position", "direct.strategy_close_capacity"})
_DENIALS = frozenset({"STRATEGY_LEDGER_UNAVAILABLE", "SCOPE_DENIED", "INVALID_REQUEST"})
_UNITS = frozenset({"base", "contracts", "lots", "native"})
_DECIMAL = re.compile(r"[0-9]{1,42}(?:\.[0-9]{1,42})?\Z", re.ASCII)


def _quantity(value: object, *, positive: bool = False) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise ValueError("INVALID_QUANTITY")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("INVALID_QUANTITY") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("INVALID_QUANTITY")
    return result


def _day(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9]{8}", value, re.ASCII) is None:
        raise ValueError("INVALID_TRADING_DAY")
    try:
        datetime.date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError:
        raise ValueError("INVALID_TRADING_DAY") from None
    return value


def _text(value: object) -> bool:
    return (type(value) is str and bool(value) and value == value.strip()
            and len(value) <= 512 and value.isprintable())


@dataclass(frozen=True, slots=True)
class GatewayStrategyPositionObservation:
    """One authenticated server observation, scoped to the original runtime.

    ``provider_close_capacity`` is intentionally UNKNOWN; quantities represent
    the strategy's own attribution only.  The local timestamp records response
    observation, not exchange time or a lease on the position.
    """

    instrument: MarketInstrumentKey
    quantity_unit: str
    trading_day: str
    long_today: Decimal
    long_yesterday: Decimal
    short_today: Decimal
    short_yesterday: Decimal
    projected_buckets: bool
    owner_generation: str
    provider_close_capacity: str
    strategy_close_available: Decimal | None
    strategy_close_reserved: Decimal | None
    observed_monotonic_ns: int
    binding: GatewayCommandBinding = field(repr=False)
    registration_id: str = field(repr=False)
    service_epoch: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class GatewayStrategyPositionResult:
    outcome: str
    observation: GatewayStrategyPositionObservation | None = None
    code: str | None = None


class GatewayStrategyPositionClient:
    """Read only the strategy identified by a bound SDK session."""

    def __init__(self, runtime: ThreadedServiceClientRuntime, *,
                 binding: GatewayCommandBinding, route: GatewayClientRoute) -> None:
        if type(runtime) is not ThreadedServiceClientRuntime or type(binding) is not GatewayCommandBinding:
            raise TypeError("INVALID_CLIENT_BINDING")
        _check_binding_route(binding, route)
        if route.identity.delegate_ref is not None:
            raise ValueError("INVALID_CLIENT_ROUTE")
        self._runtime, self._binding, self._route = runtime, binding, route
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._lock = threading.Lock()
        self._issued = {}
        self._generations = {}

    def _check_owner(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError("GATEWAY_POSITION_WRONG_OWNER")
        _check_binding_route(self._binding, self._route)

    def _registration(self, operation: str):
        snap = self._runtime.status()
        if (snap.state != "running" or not snap.thread_alive or not snap.healthy
                or type(snap.identity) is not WireIdentity or snap.identity != self._route.identity
                or snap.run_id != self._route.scope.run_id):
            raise RuntimeError("GATEWAY_POSITION_ROUTE_UNAVAILABLE")
        matches = [b for b in snap.bindings if b.active is True and b.required is True
                   and b.mount == self._route.mount and b.service_id == self._route.service_id
                   and b.instance_id == self._route.instance_id and b.scope_ref == self._route.scope_ref
                   and type(b.scope) is WireScope and b.scope == self._route.scope
                   and type(b.operations) is frozenset and operation in b.operations]
        if (len(matches) != 1 or not _text(matches[0].registration_id)
                or not _text(matches[0].service_epoch)):
            raise RuntimeError("GATEWAY_POSITION_ROUTE_UNAVAILABLE")
        return matches[0].registration_id, matches[0].service_epoch

    def _read(self, operation, instrument, quantity_unit, payload, timeout_seconds):
        self._check_owner()
        if (type(instrument) is not MarketInstrumentKey or type(quantity_unit) is not str
                or quantity_unit not in _UNITS or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("GATEWAY_POSITION_INVALID_REQUEST")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("GATEWAY_POSITION_BUSY")
        try:
            # A failed fresh read must never leave the previous observation
            # reusable as if it had also passed the latest authorization check.
            self._issued.pop(operation, None)
            try:
                registration = self._registration(operation)
            except Exception:
                return GatewayStrategyPositionResult("UNAVAILABLE", code="ROUTE_UNAVAILABLE")
            arguments = {"instrument": instrument.to_dict(), "payload": {
                "symbol": instrument.native_symbol, "exchange_id": instrument.venue,
                "quantity_unit": quantity_unit, **payload}}
            try:
                response = self._runtime.call(self._route.mount, operation, arguments,
                    account_ref=self._binding.account_scope, scope_ref=self._route.scope_ref,
                    timeout_seconds=timeout_seconds)
                response = _detach_wire(response)
                if self._registration(operation) != registration:
                    raise ValueError("REGISTRATION_CHANGED")
                if type(response) is not dict:
                    raise ValueError("INVALID_REPLY")
                if response.get("status") == "REQUEST_REJECTED":
                    if set(response) != {"status", "code"} or response["code"] not in _DENIALS:
                        raise ValueError("INVALID_REPLY")
                    return GatewayStrategyPositionResult("UNAVAILABLE", code=response["code"])
                if (set(response) != {"status", "schema_version", "instrument", "owner_generation",
                                      "strategy_position", "provider_close_capacity"}
                        or response["status"] != "OBSERVED" or type(response["schema_version"]) is not int
                        or response["schema_version"] != 1
                        or MarketInstrumentKey.from_dict(response["instrument"]) != instrument
                        or not _text(response["owner_generation"])
                        or response["provider_close_capacity"] != "UNKNOWN"):
                    raise ValueError("INVALID_REPLY")
                values = response["strategy_position"]
                required = {"trading_day", "long_today", "long_yesterday", "short_today", "short_yesterday", "projected_buckets"}
                if operation == "direct.strategy_close_capacity":
                    required |= {"strategy_close_available", "strategy_close_reserved"}
                if type(values) is not dict or set(values) != required or type(values["projected_buckets"]) is not bool:
                    raise ValueError("INVALID_REPLY")
                generation = response["owner_generation"]
                previous = self._generations.get(registration)
                if previous is not None and previous != generation:
                    raise ValueError("OWNER_GENERATION_CHANGED")
                observation = GatewayStrategyPositionObservation(
                    instrument, quantity_unit, _day(values["trading_day"]),
                    *(_quantity(values[name]) for name in ("long_today", "long_yesterday", "short_today", "short_yesterday")),
                    values["projected_buckets"], generation, "UNKNOWN",
                    _quantity(values["strategy_close_available"]) if operation == "direct.strategy_close_capacity" else None,
                    _quantity(values["strategy_close_reserved"]) if operation == "direct.strategy_close_capacity" else None,
                    time.monotonic_ns(), self._binding, *registration,
                )
                if operation == "direct.strategy_close_capacity":
                    if (observation.projected_buckets
                            and payload["offset"] in {"close_today", "close_yesterday"}):
                        raise ValueError("INVALID_REPLY")
                    with localcontext() as context:
                        context.prec = 160
                        total = (
                            observation.long_today + observation.long_yesterday
                            if payload["side"] == "sell"
                            else observation.short_today + observation.short_yesterday
                        )
                    if (observation.strategy_close_available > total
                            or observation.strategy_close_reserved > total):
                        raise ValueError("INVALID_REPLY")
                self._generations[registration] = generation
                self._issued[operation] = observation
                return GatewayStrategyPositionResult("OBSERVED", observation)
            except Exception:
                self._issued.pop(operation, None)
                return GatewayStrategyPositionResult("UNAVAILABLE", code="INVALID_OR_UNAVAILABLE_REPLY")
        finally:
            self._lock.release()

    def position(self, instrument: MarketInstrumentKey, *, quantity_unit: str, timeout_seconds=5.0):
        return self._read("direct.strategy_position", instrument, quantity_unit, {}, timeout_seconds)

    def close_capacity(self, instrument: MarketInstrumentKey, *, quantity_unit: str,
                       side: str, offset: str, quantity: Decimal, position_side=None,
                       timeout_seconds=5.0):
        if (type(side) is not str or side not in {"buy", "sell"} or type(offset) is not str
                or offset not in {"close", "close_today", "close_yesterday"}
                or type(quantity) is not Decimal or not quantity.is_finite() or quantity <= 0
                or position_side not in (None, "long", "short")
                or (position_side is not None and position_side != ("long" if side == "sell" else "short"))):
            raise ValueError("GATEWAY_POSITION_INVALID_REQUEST")
        text = format(quantity, "f")
        _quantity(text, positive=True)
        payload = {"side": side, "offset": offset, "quantity": text}
        if position_side is not None:
            payload["position_side"] = position_side
        return self._read("direct.strategy_close_capacity", instrument, quantity_unit, payload, timeout_seconds)

    def is_current(self, observation, *, max_age_seconds):
        """Check local freshness and session binding; not a reservation or authority."""
        self._check_owner()
        if (type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds)
                or not 0 < max_age_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("INVALID_MAX_AGE")
        if type(observation) is not GatewayStrategyPositionObservation:
            return False
        operation = next((op for op, value in self._issued.items() if value is observation), None)
        if operation not in _OPERATIONS:
            return False
        try:
            registration = self._registration(operation)
            now = time.monotonic_ns()
            return (registration == (observation.registration_id, observation.service_epoch)
                    and now >= observation.observed_monotonic_ns
                    and now - observation.observed_monotonic_ns <= max_age_seconds * 1e9)
        except Exception:
            return False
