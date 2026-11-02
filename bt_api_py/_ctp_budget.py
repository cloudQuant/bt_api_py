"""SDK session binding and lazy access to optional Risk budget evaluation.

The session owns the reservation seal and durable state. The Risk package
owns business-policy arithmetic; a missing selected Risk package fails closed.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from types import MappingProxyType
from typing import Any

BUDGET_SCHEMA_VERSION = "ctp-execution-budget-v1"
BUDGET_MONEY_UNIT = "CNY"
_BUDGET_CAPABILITY_SEAL = object()


class CtpBudgetError(ValueError):
    """A deterministic pure-budget validation failure."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(detail or code)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CtpBudgetError("budget_invalid_evidence", "non-canonical evidence") from exc


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _risk_budget_module():
    try:
        return import_module("bt_api_risk.ctp_budget")
    except ImportError:
        raise CtpBudgetError("budget_risk_package_unavailable") from None


def _risk_budget_journal_module():
    try:
        return import_module("bt_api_risk.ctp_budget_journal_projection")
    except ImportError:
        raise CtpBudgetError("budget_risk_package_unavailable") from None


def candidate_budget_limits(min_pnl: Decimal) -> tuple[Decimal, Decimal]:
    return _risk_budget_module().candidate_budget_limits(min_pnl)


def evaluate_ctp_budget(evidence: Mapping[str, Any], *, mode: str = "ordinary", now: datetime | None = None):
    owner = _risk_budget_module()
    try:
        return owner.evaluate_ctp_budget(evidence, mode=mode, now=now)
    except owner.CtpRiskBudgetError as error:
        raise CtpBudgetError(error.code, str(error)) from None


def evaluate_ctp_budget_numbers(
    *, ordinary_peak_cny: Any, recovery_increment_cny: Any = 0,
    historical_cumulative_pnl_cny: Sequence[Any] = (0,), mode: str = "ordinary",
    fresh_available_cny: Any | None = None,
    remaining_unabsorbed_new_obligation_cny: Any = 0,
    unallocated_recovery_headroom_cny: Any = 0,
):
    owner = _risk_budget_module()
    try:
        return owner.evaluate_ctp_budget_numbers(
            ordinary_peak_cny=ordinary_peak_cny, recovery_increment_cny=recovery_increment_cny,
            historical_cumulative_pnl_cny=historical_cumulative_pnl_cny, mode=mode,
            fresh_available_cny=fresh_available_cny,
            remaining_unabsorbed_new_obligation_cny=remaining_unabsorbed_new_obligation_cny,
            unallocated_recovery_headroom_cny=unallocated_recovery_headroom_cny,
        )
    except owner.CtpRiskBudgetError as error:
        raise CtpBudgetError(error.code, str(error)) from None


def __getattr__(name):
    if name == "CtpBudgetEvaluation":
        return _risk_budget_module().CtpBudgetEvaluation
    raise AttributeError(name)


class CtpBudgetReservation:
    """Opaque, session-owned reservation returned after durable commit."""

    __slots__ = (
        "_seal",
        "_owner",
        "_session_token",
        "_state",
    )

    def __init__(
        self,
        *,
        _seal: object,
        _owner: object,
        _session_token: object,
        state: Mapping[str, Any],
    ) -> None:
        if _seal is not _BUDGET_CAPABILITY_SEAL:
            raise TypeError("session-owned budget reservation required")
        self._seal = _seal
        self._owner = _owner
        self._session_token = _session_token
        self._state = MappingProxyType(dict(state))

    @property
    def reservation_id(self) -> str:
        return self._state["reservation_id"]

    @property
    def mode(self) -> str:
        return self._state["mode"]

    @property
    def amount_cny(self) -> Decimal:
        return self._state["amount_cny"]

    @property
    def candidate_id(self) -> str:
        return self._state["candidate_id"]

    @property
    def execution_cycle_id(self) -> str:
        return self._state["execution_cycle_id"]

    @property
    def synthetic(self) -> bool:
        return bool(self._state.get("synthetic"))

    def audit(self) -> dict[str, Any]:
        return _thaw(self._state)


def _new_budget_reservation(
    *, owner: object, session_token: object, state: Mapping[str, Any]
) -> CtpBudgetReservation:
    return CtpBudgetReservation(
        _seal=_BUDGET_CAPABILITY_SEAL,
        _owner=owner,
        _session_token=session_token,
        state=state,
    )


def _is_budget_reservation(value: Any, *, owner: object | None = None) -> bool:
    return bool(
        type(value) is CtpBudgetReservation
        and value._seal is _BUDGET_CAPABILITY_SEAL
        and (owner is None or value._owner is owner)
    )


def budget_evidence_digest(evidence: Mapping[str, Any]) -> str:
    """Digest the exact provider-neutral package for durable binding."""

    return hashlib.sha256(_canonical_json(evidence)).hexdigest()


__all__ = [
    "BUDGET_MONEY_UNIT",
    "BUDGET_SCHEMA_VERSION",
    "CtpBudgetError",
    "CtpBudgetEvaluation",
    "CtpBudgetReservation",
    "budget_evidence_digest",
    "evaluate_ctp_budget",
    "evaluate_ctp_budget_numbers",
]
