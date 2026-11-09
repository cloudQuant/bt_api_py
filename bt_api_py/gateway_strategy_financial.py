"""Authenticated single-strategy financial snapshot client.

Results reflect the Gateway's verified financial cut, not account cash,
provider authority, or a reservation. Each snapshot call re-reads the owner.
"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.gateway_strategy_position import GatewayStrategyPositionClient

_OPERATION = "direct.strategy_financial"
_DENIALS = frozenset({"STRATEGY_FINANCIAL_UNAVAILABLE", "SCOPE_DENIED", "INVALID_REQUEST"})
_AMOUNT = re.compile(r"-?(?:0|[1-9][0-9]{0,43})(?:\.[0-9]{1,42})?\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_CURRENCIES = 256


def _ref(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256
            and value == value.strip() and value.isprintable())


def _amount(value: object) -> Decimal:
    if type(value) is not str or _AMOUNT.fullmatch(value) is None:
        raise ValueError("INVALID_FINANCIAL_AMOUNT")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("INVALID_FINANCIAL_AMOUNT") from None
    if not number.is_finite():
        raise ValueError("INVALID_FINANCIAL_AMOUNT")
    return number


@dataclass(frozen=True, slots=True)
class GatewayStrategyFinancialObservation:
    """A service-verified cut, not an exchange-level account observation."""
    currency: str
    model: str
    revision: int
    cut_ref: str = field(repr=False)
    owner_generation: str = field(repr=False)
    snapshot_digest: str = field(repr=False)
    value: Decimal
    cash: Decimal
    observed_monotonic_ns: int
    binding: GatewayCommandBinding = field(repr=False)
    registration_id: str = field(repr=False)
    service_epoch: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class GatewayStrategyFinancialResult:
    outcome: str
    observation: GatewayStrategyFinancialObservation | None = None
    code: str | None = None


class GatewayStrategyFinancialClient(GatewayStrategyPositionClient):
    """Read a single currency for exactly the strategy of the bound session."""

    def __init__(self, runtime, *, binding, route):
        super().__init__(runtime, binding=binding, route=route)
        self._financial_history = {}
        self._financial_issued = {}

    def snapshot(self, currency: str, *, timeout_seconds=5.0):
        self._check_owner()
        if (not _ref(currency) or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("GATEWAY_FINANCIAL_INVALID_REQUEST")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("GATEWAY_FINANCIAL_BUSY")
        try:
            # A failed new read cannot validate an older observation.
            self._financial_issued.pop(currency, None)
            if currency not in self._financial_history and len(self._financial_history) >= _MAX_CURRENCIES:
                return GatewayStrategyFinancialResult("UNAVAILABLE", code="CAPACITY_EXCEEDED")
            try:
                registration = self._registration(_OPERATION)
            except Exception:
                return GatewayStrategyFinancialResult("UNAVAILABLE", code="ROUTE_UNAVAILABLE")
            try:
                reply = self._runtime.call(
                    self._route.mount, _OPERATION, {"currency": currency},
                    account_ref=self._binding.account_scope, scope_ref=self._route.scope_ref,
                    timeout_seconds=timeout_seconds,
                )
                reply = _detach_wire(reply)
                if self._registration(_OPERATION) != registration:
                    raise ValueError("REGISTRATION_CHANGED")
                if type(reply) is not dict:
                    raise ValueError("INVALID_REPLY")
                if reply.get("status") == "REQUEST_REJECTED":
                    if set(reply) != {"status", "code"} or reply["code"] not in _DENIALS:
                        raise ValueError("INVALID_REPLY")
                    return GatewayStrategyFinancialResult("UNAVAILABLE", code=reply["code"])
                required = {"status", "schema_version", "currency", "model", "revision", "cut_ref",
                            "owner_generation", "snapshot_digest", "value", "cash"}
                if (set(reply) != required or reply["status"] != "OBSERVED"
                        or type(reply["schema_version"]) is not int or reply["schema_version"] != 1
                        or reply["currency"] != currency or reply["model"] != "futures_linear_v1"
                        or type(reply["revision"]) is not int or not 0 < reply["revision"] < 2**63
                        or not _ref(reply["cut_ref"]) or not _ref(reply["owner_generation"])
                        or type(reply["snapshot_digest"]) is not str
                        or _DIGEST.fullmatch(reply["snapshot_digest"]) is None):
                    raise ValueError("INVALID_REPLY")
                generation = reply["owner_generation"]
                previous_generation = self._generations.get(registration)
                if previous_generation is not None and previous_generation != generation:
                    raise ValueError("OWNER_GENERATION_CHANGED")
                value, cash = _amount(reply["value"]), _amount(reply["cash"])
                history = (reply["revision"], reply["snapshot_digest"], reply["cut_ref"], value, cash)
                previous = self._financial_history.get(currency)
                if previous is not None and (history[0] < previous[0]
                        or (history[0] == previous[0] and history != previous)):
                    raise ValueError("FINANCIAL_REVISION_CONFLICT")
                observation = GatewayStrategyFinancialObservation(
                    currency, reply["model"], history[0], history[2], generation, history[1],
                    value, cash, time.monotonic_ns(), self._binding, *registration,
                )
                self._generations[registration] = generation
                self._financial_history[currency] = history
                self._financial_issued[currency] = observation
                return GatewayStrategyFinancialResult("OBSERVED", observation)
            except Exception:
                return GatewayStrategyFinancialResult("UNAVAILABLE", code="INVALID_OR_UNAVAILABLE_REPLY")
        finally:
            self._lock.release()

    def is_current(self, observation, *, max_age_seconds):
        """Check only local receipt age and registration, not source continuity."""
        self._check_owner()
        if (type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds)
                or not 0 < max_age_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("INVALID_MAX_AGE")
        if (type(observation) is not GatewayStrategyFinancialObservation
                or self._financial_issued.get(observation.currency) is not observation):
            return False
        try:
            registration = self._registration(_OPERATION)
            now = time.monotonic_ns()
            return (registration == (observation.registration_id, observation.service_epoch)
                    and now >= observation.observed_monotonic_ns
                    and now - observation.observed_monotonic_ns <= max_age_seconds * 1e9)
        except Exception:
            return False
