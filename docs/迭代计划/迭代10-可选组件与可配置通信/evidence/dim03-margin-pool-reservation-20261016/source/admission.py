"""Durable, provider-independent pre-trade admission primitives.

The older :mod:`bt_api_risk` classes calculate and describe risk, but they do
not own an account-scoped, durable decision at the dispatch boundary.  This
module provides that small boundary without importing a provider, Backtrader,
or the execution package.  An execution adapter can reserve a permit before it
performs provider I/O and must validate the permit again immediately before
dispatch.

SQLite is deliberately used here because it is part of the Python standard
library, provides an atomic ``BEGIN IMMEDIATE`` transaction across local
processes, and keeps the first implementation usable on Windows and Linux.
The default mode remains reservation-only; explicit ``LIVE_GROSS`` mode also
persists conservative, source-verified per-order occupancy, not a full position
book or execution engine.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional, Protocol, Union

from bt_api_base.account_protocol import AccountResourceKey

from .exposure import (
    ExposureAccountingMode,
    GrossExposureAuthority,
    GrossExposureBaselineProof,
    OrderExposureObservationProof,
    OrderExposureSnapshot,
    OrderExposureState,
    VerifiedGrossExposureBaseline,
    fill_ids_sha256,
    fill_ids_sha256_from_ids,
    verified_baseline_matches,
    verified_observation_matches,
)
from .freeze_activation import (
    FreezeActivationResult,
    FreezeActivationStatus,
)
from .freeze_activation import (
    activate_freeze_once as _activate_freeze_once,
)


class RiskGateError(RuntimeError):
    """Base error raised by the durable admission gate."""


class RiskDeniedError(RiskGateError):
    """A deterministic policy or safety condition rejected an intent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PermitInvalidError(RiskGateError):
    """A permit cannot be used at the execution boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_NOTIONAL_UNIT = re.compile(r"^[A-Z][A-Z0-9]{2,11}$")
_ALLOCATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ALLOCATION_INSTRUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$")
_RISK_GROUP_MAX_LEGS = 3


def _require_sha256(value: object, name: str) -> str:
    if type(value) is not str or not _SHA256_HEX.fullmatch(value):
        raise ValueError("invalid " + name)
    return value


class IntentAction(str, Enum):
    """The risk meaning of an execution request."""

    INCREASE = "increase"
    REDUCE = "reduce"
    CANCEL = "cancel"
    UNKNOWN_EFFECT = "unknown_effect"


@dataclass(frozen=True)
class AccountScope:
    """The immutable account/environment boundary of a permit."""

    provider: str
    account_id: str
    environment: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.account_id.strip() or not self.environment.strip():
            raise ValueError("provider, account_id, and environment are required")

    @property
    def key(self) -> str:
        return _canonical_json(
            {
                "account_id": self.account_id,
                "environment": self.environment,
                "provider": self.provider,
            }
        )


@dataclass(frozen=True)
class RiskPolicy:
    """Fail-closed limits used for one account scope.

    ``max_increase_notional`` and ``max_increase_count`` always apply to
    outstanding reservations.  The default ``RESERVATIONS_ONLY`` mode keeps
    the legacy behavior and does not infer filled positions.  ``LIVE_GROSS``
    is a separate explicit opt-in with independent gross occupancy caps and
    requires a trusted account baseline plus complete current order evidence.
    Once the baseline expires, new increases remain blocked.  Fresh order
    observations may still reconcile or release verified open leaves, but a
    baseline cannot be refreshed while any filled or open Risk-owned exposure
    remains.  This version has no position-absorption or reduction-proof API;
    an account with persistent filled exposure can therefore remain read-only
    until a future trusted exposure-reconciliation path is added.
    """

    policy_id: str
    max_increase_notional: Decimal
    max_increase_count: int
    permit_ttl_seconds: float = 30.0
    require_strategy_allocation: bool = False
    notional_unit: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    exposure_accounting_mode: ExposureAccountingMode = ExposureAccountingMode.RESERVATIONS_ONLY
    max_gross_occupancy_notional: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
    max_gross_occupancy_intent_count: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    max_exposure_facts_age_seconds: Optional[float] = None  # noqa: UP045 -- Python 3.9 is supported.

    def __post_init__(self) -> None:
        try:
            limit = _as_decimal(self.max_increase_notional)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("max_increase_notional must be a non-negative decimal") from exc
        if not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if limit < Decimal("0"):
            raise ValueError("max_increase_notional cannot be negative")
        if type(self.max_increase_count) is not int or self.max_increase_count < 0:
            raise ValueError("max_increase_count must be a non-negative integer")
        if (
            isinstance(self.permit_ttl_seconds, bool)
            or not math.isfinite(self.permit_ttl_seconds)
            or self.permit_ttl_seconds <= 0
        ):
            raise ValueError("permit_ttl_seconds must be positive and finite")
        if type(self.require_strategy_allocation) is not bool:
            raise ValueError("require_strategy_allocation must be a boolean")
        if self.notional_unit is not None and (
            not isinstance(self.notional_unit, str)
            or not _NOTIONAL_UNIT.fullmatch(self.notional_unit)
        ):
            raise ValueError("notional_unit must be a canonical uppercase unit code")
        if type(self.exposure_accounting_mode) is not ExposureAccountingMode:
            raise ValueError("exposure_accounting_mode must be an ExposureAccountingMode")
        gross_cap = None
        if self.max_gross_occupancy_notional is not None:
            try:
                gross_cap = _as_decimal(self.max_gross_occupancy_notional)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("max_gross_occupancy_notional must be non-negative") from exc
            if gross_cap < 0:
                raise ValueError("max_gross_occupancy_notional must be non-negative")
        if self.max_gross_occupancy_intent_count is not None and (
            type(self.max_gross_occupancy_intent_count) is not int
            or self.max_gross_occupancy_intent_count < 0
        ):
            raise ValueError("max_gross_occupancy_intent_count must be non-negative")
        if self.max_exposure_facts_age_seconds is not None and (
            isinstance(self.max_exposure_facts_age_seconds, bool)
            or not math.isfinite(self.max_exposure_facts_age_seconds)
            or self.max_exposure_facts_age_seconds <= 0
        ):
            raise ValueError("max_exposure_facts_age_seconds must be positive and finite")
        if self.exposure_accounting_mode is ExposureAccountingMode.RESERVATIONS_ONLY:
            if any(
                value is not None
                for value in (
                    gross_cap,
                    self.max_gross_occupancy_intent_count,
                    self.max_exposure_facts_age_seconds,
                )
            ):
                raise ValueError("gross occupancy caps require LIVE_GROSS accounting")
        else:
            if (
                self.notional_unit is None
                or not self.require_strategy_allocation
                or gross_cap is None
                or self.max_gross_occupancy_intent_count is None
                or self.max_exposure_facts_age_seconds is None
            ):
                raise ValueError(
                    "LIVE_GROSS requires a fixed unit, strategy allocations, and explicit caps/freshness"
                )
        object.__setattr__(self, "max_increase_notional", limit)
        if gross_cap is not None:
            object.__setattr__(self, "max_gross_occupancy_notional", gross_cap)

    @property
    def fingerprint(self) -> str:
        payload: dict[str, object] = {
            "max_increase_count": self.max_increase_count,
            "max_increase_notional": _decimal_text(self.max_increase_notional),
            "permit_ttl_seconds": self.permit_ttl_seconds,
            "policy_id": self.policy_id,
        }
        if self.require_strategy_allocation:
            payload["require_strategy_allocation"] = True
        if self.notional_unit is not None:
            payload["notional_unit"] = self.notional_unit
        if self.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS:
            payload.update(
                {
                    "exposure_accounting_mode": ExposureAccountingMode.LIVE_GROSS.value,
                    "exposure_schema_version": 2,
                    "max_gross_occupancy_notional": _decimal_text(
                        self.max_gross_occupancy_notional  # type: ignore[arg-type]
                    ),
                    "max_gross_occupancy_intent_count": self.max_gross_occupancy_intent_count,
                    "max_exposure_facts_age_seconds": format(
                        float(self.max_exposure_facts_age_seconds),
                        ".17g",  # type: ignore[arg-type]
                    ),
                }
            )
        return _sha256(_canonical_json(payload))


@dataclass(frozen=True)
class RiskIntent:
    """A provider-neutral request after the account owner derived its effect.

    ``REDUCE`` is a trusted classification, never a client's reduce-only flag.
    This primitive has no position book and cannot prove that classification.
    """

    intent_id: str
    scope: AccountScope
    action: IntentAction
    notional: Decimal = Decimal("0")
    payload_fingerprint: str = ""
    strategy_id: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    allocation_version: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    instrument: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    quantity: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
    quantity_unit: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    notional_unit: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    exposure_profile_fingerprint: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    instrument_facts_observed_at_ns: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    instrument_facts_valid_until_ns: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    resource: Optional[AccountResourceKey] = None  # noqa: UP045 -- Python 3.9 is supported.

    def __post_init__(self) -> None:
        if not self.intent_id.strip():
            raise ValueError("intent_id is required")
        if not isinstance(self.action, IntentAction):
            raise ValueError("action must be an IntentAction")
        if not isinstance(self.scope, AccountScope):
            raise ValueError("scope must be an AccountScope")
        if not isinstance(self.payload_fingerprint, str):
            raise ValueError("payload_fingerprint must be an immutable string")
        if self.notional_unit is not None and (
            not isinstance(self.notional_unit, str)
            or not _NOTIONAL_UNIT.fullmatch(self.notional_unit)
        ):
            raise ValueError("notional_unit must be a canonical uppercase unit code")
        if self.exposure_profile_fingerprint is not None:
            _require_sha256(self.exposure_profile_fingerprint, "exposure_profile_fingerprint")
        if self.resource is not None and not isinstance(self.resource, AccountResourceKey):
            raise ValueError("resource must be an AccountResourceKey")
        if any(
            value is not None
            for value in (
                self.instrument_facts_observed_at_ns,
                self.instrument_facts_valid_until_ns,
            )
        ) and (
            type(self.instrument_facts_observed_at_ns) is not int
            or type(self.instrument_facts_valid_until_ns) is not int
            or self.instrument_facts_observed_at_ns <= 0
            or self.instrument_facts_valid_until_ns <= self.instrument_facts_observed_at_ns
        ):
            raise ValueError("instrument fact timestamps must be a positive ordered pair")
        if (self.strategy_id is None) != (self.allocation_version is None):
            raise ValueError("strategy_id and allocation_version must be supplied together")
        for name in ("strategy_id", "allocation_version"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not _ALLOCATION_ID.fullmatch(value)
            ):
                raise ValueError("invalid " + name)
        position_fields = (self.instrument, self.quantity, self.quantity_unit)
        if any(value is not None for value in position_fields) and not all(
            value is not None for value in position_fields
        ):
            raise ValueError("instrument, quantity, and quantity_unit must be supplied together")
        if self.instrument is not None and (
            not isinstance(self.instrument, str)
            or not _ALLOCATION_INSTRUMENT.fullmatch(self.instrument)
        ):
            raise ValueError("invalid instrument")
        if self.quantity_unit is not None and (
            not isinstance(self.quantity_unit, str)
            or not _ALLOCATION_ID.fullmatch(self.quantity_unit)
        ):
            raise ValueError("invalid quantity_unit")
        quantity = None
        if self.quantity is not None:
            try:
                quantity = _as_decimal(self.quantity)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("quantity must be a positive finite decimal") from exc
            if quantity <= Decimal("0"):
                raise ValueError("quantity must be a positive finite decimal")
        try:
            notional = _as_decimal(self.notional)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("notional must be a non-negative decimal") from exc
        if notional < Decimal("0"):
            raise ValueError("notional cannot be negative")
        if self.action is IntentAction.INCREASE and notional <= Decimal("0"):
            raise ValueError("increase intents require positive notional")
        object.__setattr__(self, "notional", notional)
        if quantity is not None:
            object.__setattr__(self, "quantity", quantity)

    @property
    def fingerprint(self) -> str:
        payload: dict[str, object] = {
            "action": self.action.value,
            "notional": _decimal_text(self.notional),
            "payload_fingerprint": self.payload_fingerprint,
            "scope": self.scope.key,
        }
        if self.strategy_id is not None:
            payload["strategy_id"] = self.strategy_id
            payload["allocation_version"] = self.allocation_version
        if (
            self.instrument is not None
            and self.quantity is not None
            and self.quantity_unit is not None
        ):
            payload["instrument"] = self.instrument
            payload["quantity"] = _decimal_text(self.quantity)
            payload["quantity_unit"] = self.quantity_unit
        if self.notional_unit is not None:
            payload["notional_unit"] = self.notional_unit
        if self.exposure_profile_fingerprint is not None:
            payload["exposure_profile_fingerprint"] = self.exposure_profile_fingerprint
        if self.resource is not None:
            payload["resource"] = {
                "margin_pool": self.resource.margin_pool,
                "product_type": self.resource.product_type,
            }
        return _sha256(_canonical_json(payload))


@dataclass(frozen=True)
class RiskPermit:
    """An opaque, account-bound admission artifact returned by ``reserve``."""

    permit_id: str
    intent_id: str
    scope: AccountScope
    action: IntentAction
    notional: Decimal
    policy_fingerprint: str
    generation: int
    issued_at: float
    expires_at: float
    strategy_id: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    allocation_version: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    instrument: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    quantity: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
    quantity_unit: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    notional_unit: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.


@dataclass(frozen=True)
class StrategyAllocationSnapshot:
    """Immutable local-ledger view of one installed strategy allowance.

    Completeness describes only this risk package's SQLite reservation ledger.
    It is not provider-account, cash, margin, or position-book evidence.
    """

    scope: AccountScope
    strategy_id: str
    revision: str
    allocated_notional: Optional[Decimal]  # noqa: UP045 -- Python 3.9 is supported.
    used_notional: Optional[Decimal]  # noqa: UP045 -- Python 3.9 is supported.
    reserved_notional: Optional[Decimal]  # noqa: UP045 -- Python 3.9 is supported.
    available_notional: Optional[Decimal]  # noqa: UP045 -- Python 3.9 is supported.
    notional_unit: Optional[str]  # noqa: UP045 -- Python 3.9 is supported.
    max_position: Optional[Decimal]  # noqa: UP045 -- Python 3.9 is supported.
    position_instrument: Optional[str]  # noqa: UP045 -- Python 3.9 is supported.
    position_unit: Optional[str]  # noqa: UP045 -- Python 3.9 is supported.
    as_of: float
    max_gross_occupancy_notional: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
    max_gross_occupancy_intent_count: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    source: str = field(default="local_risk_reservation_ledger", init=False)
    completeness: str = "LOCAL_LEDGER_COMPLETE"
    reason: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.


class FreezeResolutionStatus(str, Enum):
    """Result of resolving one exact ordinary freeze cause."""

    CLEARED = "CLEARED"
    ALREADY_RESOLVED = "ALREADY_RESOLVED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class FreezeResolution:
    """Typed outcome and same-scope postcondition for exact freeze resolution."""

    status: FreezeResolutionStatus
    has_other_active_freezes: bool

    def __post_init__(self) -> None:
        if type(self.status) is not FreezeResolutionStatus:
            raise ValueError("status must be a FreezeResolutionStatus")
        if type(self.has_other_active_freezes) is not bool:
            raise ValueError("has_other_active_freezes must be a boolean")


class DispatchTerminalState(str, Enum):
    """Only no-fill terminal outcomes that can release a dispatch latch."""

    REJECTED_NO_FILL = "REJECTED_NO_FILL"
    CANCELED_NO_FILL = "CANCELED_NO_FILL"


class DispatchEvidenceClass(str, Enum):
    """Distinguish local simulation evidence from native provider evidence."""

    SIMULATION_JOURNAL = "SIMULATION_JOURNAL"
    NATIVE_PROVIDER_JOURNAL = "NATIVE_PROVIDER_JOURNAL"


class CancelDispatchTerminalState(str, Enum):
    """Terminal outcome of one cancellation action, not of its target order."""

    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class CancelDispatchSource(str, Enum):
    """Source label on the immutable cancellation observation."""

    PROVIDER = "provider"
    RECONCILE = "reconcile"


class CancelTargetPostcondition(str, Enum):
    """What the exact target-order snapshot says after the cancel action."""

    TARGET_TERMINAL = "TARGET_TERMINAL"
    TARGET_REMAINS_OPEN = "TARGET_REMAINS_OPEN"


def _validate_dispatch_evidence_class(
    scope: AccountScope, evidence_class: DispatchEvidenceClass
) -> None:
    if type(evidence_class) is not DispatchEvidenceClass:
        raise ValueError("invalid dispatch evidence class")
    simulation_providers = {"fake", "fixture"}
    if evidence_class is DispatchEvidenceClass.SIMULATION_JOURNAL:
        if scope.provider not in simulation_providers:
            raise ValueError("simulation evidence cannot bind a non-simulation provider scope")
    elif scope.provider in simulation_providers:
        raise ValueError("native evidence cannot bind a fake or fixture provider scope")


@dataclass(frozen=True)
class DispatchClaimBinding:
    """Read-only identity of one durable dispatch claim."""

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claimed_at: float
    claim_digest: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid dispatch claim scope")
        for name in ("permit_id", "intent_id", "cause_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid dispatch claim " + name)
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("dispatch claim cause does not match intent")
        _require_sha256(self.intent_hash, "intent_hash")
        _require_sha256(self.claim_digest, "claim_digest")
        if not isinstance(self.claimed_at, (int, float)) or not math.isfinite(self.claimed_at):
            raise ValueError("invalid dispatch claim timestamp")


class RiskGroupReservationState(str, Enum):
    """Durable state of one member in an atomic account reservation vector."""

    ACTIVE = "active"
    SETTLED = "settled"
    RELEASED = "released"
    EXPIRED = "expired"
    NO_FILL_TERMINAL = "no_fill_terminal"


@dataclass(frozen=True)
class RiskGroupLegIntent:
    """One trusted, maximum-size increase reserved as part of a full vector."""

    leg_id: str
    intent: RiskIntent

    def __post_init__(self) -> None:
        if type(self.leg_id) is not str or not _ALLOCATION_ID.fullmatch(self.leg_id):
            raise ValueError("invalid group leg_id")
        if type(self.intent) is not RiskIntent:
            raise ValueError("group leg requires a RiskIntent")


@dataclass(frozen=True)
class RiskGroupReservationLeg:
    """Read-only durable status and identity of one reserved group leg.

    ``reserved_intent_hash`` identifies the immutable maximum exposure
    reservation. ``actual_intent_hash`` is populated only after a one-shot
    dispatch claim and identifies the trusted mapper's actual child intent.
    The execution request fingerprint is a separate journal-owned value.
    """

    leg_id: str
    permit_id: str
    intent_id: str
    reserved_intent_hash: str
    exposure_profile_fingerprint: str
    actual_intent_hash: Optional[str]  # noqa: UP045 -- Python 3.9 is supported.
    state: RiskGroupReservationState
    claimed: bool
    resolved: bool
    expires_at_ns: int

    def __post_init__(self) -> None:
        if type(self.leg_id) is not str or not _ALLOCATION_ID.fullmatch(self.leg_id):
            raise ValueError("invalid group leg_id")
        for name in ("permit_id", "intent_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid group leg " + name)
        _require_sha256(self.reserved_intent_hash, "reserved_intent_hash")
        _require_sha256(self.exposure_profile_fingerprint, "exposure_profile_fingerprint")
        if self.actual_intent_hash is not None:
            _require_sha256(self.actual_intent_hash, "actual_intent_hash")
        if type(self.state) is not RiskGroupReservationState:
            raise ValueError("invalid group reservation state")
        if type(self.claimed) is not bool or type(self.resolved) is not bool:
            raise ValueError("group claim/resolution flags must be boolean")
        if type(self.expires_at_ns) is not int or self.expires_at_ns <= 0:
            raise ValueError("invalid group permit expiry")
        if self.resolved and not self.claimed:
            raise ValueError("an unclaimed group leg cannot be resolved")
        if self.claimed != (self.actual_intent_hash is not None):
            raise ValueError("group actual intent hash must match claim presence")


@dataclass(frozen=True)
class RiskGroupReservationReceipt:
    """Consistent SQLite snapshot of a complete same-account leg vector."""

    scope: AccountScope
    group_id: str
    group_fingerprint: str
    policy_fingerprint: str
    deadline_ns: int
    legs: tuple[RiskGroupReservationLeg, ...]

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid group reservation scope")
        if type(self.group_id) is not str or not _ALLOCATION_ID.fullmatch(self.group_id):
            raise ValueError("invalid group_id")
        _require_sha256(self.group_fingerprint, "group_fingerprint")
        _require_sha256(self.policy_fingerprint, "policy_fingerprint")
        if type(self.deadline_ns) is not int or self.deadline_ns <= 0:
            raise ValueError("invalid group deadline")
        if type(self.legs) is not tuple or not 1 <= len(self.legs) <= _RISK_GROUP_MAX_LEGS:
            raise ValueError("invalid group reservation leg vector")
        if any(type(leg) is not RiskGroupReservationLeg for leg in self.legs):
            raise ValueError("invalid group reservation leg")
        if tuple(sorted(leg.leg_id for leg in self.legs)) != tuple(
            leg.leg_id for leg in self.legs
        ) or len({leg.leg_id for leg in self.legs}) != len(self.legs):
            raise ValueError("group reservation legs must be uniquely sorted")


@dataclass(frozen=True)
class RiskGroupLegClaimReceipt:
    """Exact one-shot claim for a bounded child order of a reserved group leg."""

    scope: AccountScope
    group_id: str
    group_fingerprint: str
    policy_fingerprint: str
    deadline_ns: int
    leg_id: str
    permit: RiskPermit
    reserved_intent_hash: str
    actual_intent_hash: str
    exposure_profile_fingerprint: str
    actual_quantity: Decimal
    actual_notional: Decimal
    claim: DispatchClaimBinding
    resolved: bool = False

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope or type(self.permit) is not RiskPermit:
            raise ValueError("invalid group leg claim scope or permit")
        if type(self.group_id) is not str or not _ALLOCATION_ID.fullmatch(self.group_id):
            raise ValueError("invalid group_id")
        if type(self.leg_id) is not str or not _ALLOCATION_ID.fullmatch(self.leg_id):
            raise ValueError("invalid group leg_id")
        _require_sha256(self.group_fingerprint, "group_fingerprint")
        _require_sha256(self.policy_fingerprint, "policy_fingerprint")
        _require_sha256(self.reserved_intent_hash, "reserved_intent_hash")
        _require_sha256(self.actual_intent_hash, "actual_intent_hash")
        _require_sha256(self.exposure_profile_fingerprint, "exposure_profile_fingerprint")
        if type(self.deadline_ns) is not int or self.deadline_ns <= 0:
            raise ValueError("invalid group deadline")
        if type(self.claim) is not DispatchClaimBinding:
            raise ValueError("invalid group dispatch claim")
        if type(self.resolved) is not bool:
            raise ValueError("group claim resolution flag must be boolean")
        if self.claim.scope != self.scope or self.permit.scope != self.scope:
            raise ValueError("group claim scope mismatch")
        if (
            self.permit.intent_id != self.claim.intent_id
            or self.permit.permit_id != self.claim.permit_id
            or self.claim.intent_hash != self.reserved_intent_hash
        ):
            raise ValueError("group claim does not bind its reserved permit")
        try:
            quantity = _as_decimal(self.actual_quantity)
            notional = _as_decimal(self.actual_notional)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("invalid group child quantity/notional") from exc
        if quantity <= 0 or notional <= 0:
            raise ValueError("group child quantity/notional must be positive")
        object.__setattr__(self, "actual_quantity", quantity)
        object.__setattr__(self, "actual_notional", notional)

    @property
    def permit_reference(self) -> str:
        """Return the account owner's opaque durable permit reference."""

        return self.permit.permit_id

    @property
    def claimed(self) -> bool:
        """A returned claim receipt always represents the consumed one-shot claim."""

        return True


@dataclass(frozen=True)
class DispatchTerminalProof:
    """Immutable journal evidence proposed for one no-fill dispatch resolution.

    Constructing this value does not make it authoritative. The injected
    journal authority must verify its immutable row and hold the account-wide
    writer fence while the risk gate records the one-time resolution.
    """

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claim_digest: str
    evidence_class: DispatchEvidenceClass
    terminal_state: DispatchTerminalState
    dispatch_attempt_count: int
    journal_revision: int
    journal_record_sha256: str
    reconciliation_evidence_sha256: str
    writer_fence_sha256: str
    fee_record_count: int
    fee_history_complete: bool
    filled_quantity: int = 0
    trade_count: int = 0

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid terminal proof scope")
        _validate_dispatch_evidence_class(self.scope, self.evidence_class)
        for name in ("permit_id", "intent_id", "cause_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid terminal proof " + name)
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("terminal proof cause does not match intent")
        for name in (
            "intent_hash",
            "claim_digest",
            "journal_record_sha256",
            "reconciliation_evidence_sha256",
            "writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.terminal_state) is not DispatchTerminalState:
            raise ValueError("terminal proof must name an allowed no-fill terminal state")
        if type(self.dispatch_attempt_count) is not int or self.dispatch_attempt_count != 1:
            raise ValueError("terminal proof must establish exactly one dispatch attempt")
        if type(self.journal_revision) is not int or self.journal_revision <= 0:
            raise ValueError("invalid terminal proof journal revision")
        if type(self.filled_quantity) is not int or self.filled_quantity != 0:
            raise ValueError("terminal proof requires zero filled quantity")
        if type(self.trade_count) is not int or self.trade_count != 0:
            raise ValueError("terminal proof requires zero verified trades")
        if type(self.fee_record_count) is not int or self.fee_record_count != 0:
            raise ValueError("terminal proof requires an explicit zero-fee record count")
        if type(self.fee_history_complete) is not bool or self.fee_history_complete is not True:
            raise ValueError("terminal proof requires complete explicit fee history")

    @property
    def fingerprint(self) -> str:
        """Stable digest of every immutable terminal-proof field."""
        return _dispatch_resolution_proof_sha256(self)


@dataclass(frozen=True)
class DispatchTrackedOrderProof:
    """Proof that an ACKED order moved into a durable exposure reservation.

    This clears dispatch uncertainty only. The settled risk reservation stays
    counted until a separate reviewed exposure lifecycle can account for later
    fills and terminal order state.
    """

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claim_digest: str
    evidence_class: DispatchEvidenceClass
    dispatch_attempt_count: int
    journal_revision: int
    journal_record_sha256: str
    reconciliation_evidence_sha256: str
    writer_fence_sha256: str
    provider_order_id: str
    accepted_request_sha256: str
    exposure_reservation_id: str
    exposure_reservation_sha256: str
    filled_quantity: int = 0
    trade_count: int = 0

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid tracked-order proof scope")
        _validate_dispatch_evidence_class(self.scope, self.evidence_class)
        for name in ("permit_id", "intent_id", "cause_id", "provider_order_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid tracked-order proof " + name)
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("tracked-order proof cause does not match intent")
        if (
            type(self.exposure_reservation_id) is not str
            or not self.exposure_reservation_id
            or self.exposure_reservation_id != self.exposure_reservation_id.strip()
        ):
            raise ValueError("invalid tracked-order exposure reservation id")
        for name in (
            "intent_hash",
            "claim_digest",
            "journal_record_sha256",
            "reconciliation_evidence_sha256",
            "writer_fence_sha256",
            "accepted_request_sha256",
            "exposure_reservation_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.dispatch_attempt_count) is not int or self.dispatch_attempt_count != 1:
            raise ValueError("tracked-order proof must establish exactly one dispatch attempt")
        if type(self.journal_revision) is not int or self.journal_revision <= 0:
            raise ValueError("invalid tracked-order journal revision")
        if type(self.filled_quantity) is not int or self.filled_quantity != 0:
            raise ValueError("tracked-order transfer cannot use synthetic or partial fills")
        if type(self.trade_count) is not int or self.trade_count != 0:
            raise ValueError("tracked-order transfer requires zero verified trades")

    @property
    def fingerprint(self) -> str:
        """Stable digest of every immutable tracked-order proof field."""
        return _dispatch_resolution_proof_sha256(self)


@dataclass(frozen=True)
class DispatchExposureResolutionProof:
    """Resolve a dispatch latch from a complete first terminal observation.

    This path is intentionally distinct from ``DispatchTrackedOrderProof``:
    it accepts a positive-fill FILLED/CANCELLED observation with no open
    leaves, while the existing tracked-order proof continues to require zero
    fills.  An injected journal authority must verify the exact source events
    and hold the current account fence through the Risk transaction.
    """

    claim: DispatchClaimBinding
    evidence_class: DispatchEvidenceClass
    dispatch_attempt_count: int
    reconciliation_evidence_sha256: str
    observation: OrderExposureObservationProof

    def __post_init__(self) -> None:
        if type(self.claim) is not DispatchClaimBinding:
            raise ValueError("dispatch exposure proof requires an exact claim")
        _validate_dispatch_evidence_class(self.claim.scope, self.evidence_class)
        if type(self.dispatch_attempt_count) is not int or self.dispatch_attempt_count != 1:
            raise ValueError("dispatch exposure proof requires exactly one attempt")
        _require_sha256(self.reconciliation_evidence_sha256, "reconciliation_evidence_sha256")
        if type(self.observation) is not OrderExposureObservationProof:
            raise ValueError("dispatch exposure proof requires a typed order observation")
        observation = self.observation
        if observation.dispatch_proof_sha256 is not None:
            raise ValueError("initial terminal observation cannot reuse an ACKED proof")
        if not all(
            (
                observation.scope == self.claim.scope,
                observation.permit_id == self.claim.permit_id,
                observation.intent_id == self.claim.intent_id,
                observation.intent_hash == self.claim.intent_hash,
                observation.dispatch_claim_digest == self.claim.claim_digest,
            )
        ):
            raise ValueError("terminal observation does not bind the exact dispatch claim")
        if observation.state not in (OrderExposureState.FILLED, OrderExposureState.CANCELLED):
            raise ValueError("dispatch exposure proof requires a known terminal order state")
        if observation.cumulative_filled_quantity <= 0 or observation.leaves_quantity != 0:
            raise ValueError("terminal exposure proof requires positive fills and zero leaves")
        if observation.fill_trade_count <= 0 or not observation.fills:
            raise ValueError("terminal exposure proof requires immutable positive-fill facts")

    @property
    def scope(self) -> AccountScope:
        return self.claim.scope

    @property
    def permit_id(self) -> str:
        return self.claim.permit_id

    @property
    def intent_id(self) -> str:
        return self.claim.intent_id

    @property
    def intent_hash(self) -> str:
        return self.claim.intent_hash

    @property
    def cause_id(self) -> str:
        return self.claim.cause_id

    @property
    def claim_digest(self) -> str:
        return self.claim.claim_digest

    @property
    def journal_revision(self) -> int:
        return self.observation.journal_revision

    @property
    def journal_record_sha256(self) -> str:
        return self.observation.journal_record_sha256

    @property
    def writer_fence_sha256(self) -> str:
        return self.observation.writer_fence_sha256

    @property
    def fingerprint(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "claim_digest": self.claim.claim_digest,
                    "dispatch_attempt_count": self.dispatch_attempt_count,
                    "evidence_class": self.evidence_class.value,
                    "journal_record_sha256": self.journal_record_sha256,
                    "journal_revision": self.journal_revision,
                    "observation_sha256": self.observation.fingerprint,
                    "permit_id": self.permit_id,
                    "reconciliation_evidence_sha256": self.reconciliation_evidence_sha256,
                    "scope_key": self.scope.key,
                    "schema": "bt-api-risk-dispatch-exposure-resolution-v1",
                    "writer_fence_sha256": self.writer_fence_sha256,
                }
            )
        )


class DispatchExposureResolutionReceiptStatus(str, Enum):
    """Durable state of one positive-fill terminal dispatch resolution."""

    UNRESOLVED_ACTIVE = "UNRESOLVED_ACTIVE"
    TERMINAL_EXPOSURE_RESOLVED = "TERMINAL_EXPOSURE_RESOLVED"


@dataclass(frozen=True)
class DispatchExposureResolutionReceipt:
    """Single-snapshot Risk receipt for a positive-fill terminal resolution."""

    status: DispatchExposureResolutionReceiptStatus
    claim: DispatchClaimBinding
    proof_sha256: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    exposure_proof_sha256: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    final_state: Optional[OrderExposureState] = None  # noqa: UP045 -- Python 3.9 is supported.
    requested_quantity: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
    filled_quantity: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
    trade_count: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    order_revision: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    journal_revision: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    exposure_reservation_id: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    exposure_reservation_sha256: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    dispatch_latch_active: bool = True
    has_other_active_freezes: bool = False

    def __post_init__(self) -> None:
        if type(self.status) is not DispatchExposureResolutionReceiptStatus:
            raise ValueError("invalid dispatch exposure receipt status")
        if type(self.claim) is not DispatchClaimBinding:
            raise ValueError("invalid dispatch exposure receipt claim")
        if type(self.dispatch_latch_active) is not bool or type(
            self.has_other_active_freezes
        ) is not bool:
            raise ValueError("invalid dispatch exposure receipt freeze state")
        optional_fields = (
            self.proof_sha256,
            self.exposure_proof_sha256,
            self.final_state,
            self.requested_quantity,
            self.filled_quantity,
            self.trade_count,
            self.order_revision,
            self.journal_revision,
            self.exposure_reservation_id,
            self.exposure_reservation_sha256,
        )
        if self.status is DispatchExposureResolutionReceiptStatus.UNRESOLVED_ACTIVE:
            if any(value is not None for value in optional_fields) or not self.dispatch_latch_active:
                raise ValueError("unresolved exposure receipt cannot carry terminal evidence")
            return
        if any(value is None for value in optional_fields) or self.dispatch_latch_active:
            raise ValueError("resolved exposure receipt is incomplete or still latched")
        _require_sha256(self.proof_sha256, "proof_sha256")
        _require_sha256(self.exposure_proof_sha256, "exposure_proof_sha256")
        _require_sha256(self.exposure_reservation_sha256, "exposure_reservation_sha256")
        if (
            type(self.exposure_reservation_id) is not str
            or not self.exposure_reservation_id
            or self.exposure_reservation_id != self.exposure_reservation_id.strip()
        ):
            raise ValueError("invalid exposure reservation id")
        if self.final_state not in (OrderExposureState.FILLED, OrderExposureState.CANCELLED):
            raise ValueError("resolved exposure receipt requires a terminal state")
        requested = _as_decimal(self.requested_quantity)
        filled = _as_decimal(self.filled_quantity)
        if requested <= 0 or filled <= 0 or filled > requested:
            raise ValueError("invalid terminal exposure quantities")
        object.__setattr__(self, "requested_quantity", requested)
        object.__setattr__(self, "filled_quantity", filled)
        if (
            type(self.trade_count) is not int
            or self.trade_count <= 0
            or type(self.order_revision) is not int
            or self.order_revision <= 0
            or type(self.journal_revision) is not int
            or self.journal_revision <= 0
        ):
            raise ValueError("invalid terminal exposure evidence counters")
        if self.final_state is OrderExposureState.FILLED and filled != requested:
            raise ValueError("filled receipt must cover the requested quantity")

    @property
    def scope(self) -> AccountScope:
        return self.claim.scope

    @property
    def permit_id(self) -> str:
        return self.claim.permit_id

    @property
    def intent_id(self) -> str:
        return self.claim.intent_id


@dataclass(frozen=True)
class VerifiedDispatchExposureResolution:
    """Authority receipt binding terminal source facts to the current fence."""

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claim_digest: str
    proof_sha256: str
    exposure_proof_sha256: str
    provider_order_id: str
    accepted_request_sha256: str
    exposure_reservation_id: str
    exposure_reservation_sha256: str
    final_state: OrderExposureState
    requested_quantity: Decimal
    filled_quantity: Decimal
    trade_count: int
    order_revision: int
    journal_revision: int
    journal_record_sha256: str
    writer_owner_id: str
    writer_fencing_token: int
    writer_fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid verified exposure resolution scope")
        for name in (
            "permit_id",
            "intent_id",
            "cause_id",
            "provider_order_id",
            "exposure_reservation_id",
            "writer_owner_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid verified exposure resolution " + name)
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("verified exposure cause does not match intent")
        for name in (
            "intent_hash",
            "claim_digest",
            "proof_sha256",
            "exposure_proof_sha256",
            "accepted_request_sha256",
            "exposure_reservation_sha256",
            "journal_record_sha256",
            "writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if self.final_state not in (OrderExposureState.FILLED, OrderExposureState.CANCELLED):
            raise ValueError("verified exposure resolution requires a terminal state")
        object.__setattr__(
            self, "requested_quantity", _as_decimal(self.requested_quantity)
        )
        object.__setattr__(self, "filled_quantity", _as_decimal(self.filled_quantity))
        if (
            self.requested_quantity <= 0
            or self.filled_quantity <= 0
            or self.filled_quantity > self.requested_quantity
        ):
            raise ValueError("invalid verified exposure quantities")
        for name in ("trade_count", "order_revision", "journal_revision", "writer_fencing_token"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError("invalid verified exposure " + name)
        if self.final_state is OrderExposureState.FILLED and (
            self.filled_quantity != self.requested_quantity
        ):
            raise ValueError("verified filled observation is incomplete")


class VerifiedDispatchExposureJournalAuthority(Protocol):
    """Authority for complete first terminal order/fill evidence under a fence."""

    def dispatch_exposure_resolution_guard(
        self,
        proof: DispatchExposureResolutionProof,
        *,
        claim: DispatchClaimBinding,
    ) -> AbstractContextManager[
        Optional[VerifiedDispatchExposureResolution]  # noqa: UP045 -- Python 3.9 is supported.
    ]: ...


def _verified_dispatch_exposure_matches(
    verified: object,
    *,
    proof: DispatchExposureResolutionProof,
    claim: DispatchClaimBinding,
) -> bool:
    observation = proof.observation
    return bool(
        type(verified) is VerifiedDispatchExposureResolution
        and verified.scope == proof.scope == claim.scope == observation.scope
        and verified.permit_id == proof.permit_id == claim.permit_id == observation.permit_id
        and verified.intent_id == proof.intent_id == claim.intent_id == observation.intent_id
        and verified.intent_hash == proof.intent_hash == claim.intent_hash == observation.intent_hash
        and verified.cause_id == proof.cause_id == claim.cause_id
        and verified.claim_digest == proof.claim_digest == claim.claim_digest
        and verified.proof_sha256 == proof.fingerprint
        and verified.exposure_proof_sha256 == observation.fingerprint
        and verified.provider_order_id == observation.provider_order_id
        and verified.accepted_request_sha256 == observation.accepted_request_sha256
        and verified.exposure_reservation_id == observation.exposure_reservation_id
        and verified.exposure_reservation_sha256 == observation.exposure_reservation_sha256
        and verified.final_state is observation.state
        and verified.requested_quantity == observation.requested_quantity
        and verified.filled_quantity == observation.cumulative_filled_quantity
        and verified.trade_count == observation.fill_trade_count
        and verified.order_revision == observation.order_revision
        and verified.journal_revision == observation.journal_revision
        and verified.journal_record_sha256 == observation.journal_record_sha256
        and verified.writer_owner_id == observation.writer_owner_id
        and verified.writer_fencing_token == observation.writer_fencing_token
        and verified.writer_fence_sha256 == observation.writer_fence_sha256
    )


DispatchResolutionProof = Union[DispatchTerminalProof, DispatchTrackedOrderProof]


class DispatchResolutionReceiptStatus(str, Enum):
    """Durable state of one exact dispatch claim as observed by the risk gate."""

    UNRESOLVED_ACTIVE = "UNRESOLVED_ACTIVE"
    ACKED_TRACKED_RESOLVED = "ACKED_TRACKED_RESOLVED"


@dataclass(frozen=True)
class DispatchRecoveryAttestation:
    """New-fence journal evidence for recovering an immutable PROOF_READY event.

    This value carries identities only. It authorizes nothing by itself; the
    injected journal authority must verify the old event chain and the new
    account-wide writer fence while :meth:`DurableRiskGate.resolve_dispatch_freeze_recovered`
    commits the matching resolution.
    """

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claim_digest: str
    proof_sha256: str
    proof_ready_revision: int
    proof_ready_record_sha256: str
    original_writer_owner_id: str
    original_writer_fencing_token: int
    original_writer_fence_sha256: str
    order_observation_revision: int
    order_observation_record_sha256: str
    fill_observation_revision: int
    fill_observation_record_sha256: str
    reconciliation_evidence_sha256: str
    accepted_request_sha256: str
    provider_order_id: str
    exposure_reservation_id: str
    exposure_reservation_sha256: str
    recovery_event_id: str
    recovery_event_revision: int
    recovery_event_sha256: str
    current_writer_owner_id: str
    current_writer_fencing_token: int
    current_writer_fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid dispatch recovery scope")
        for name in (
            "permit_id",
            "intent_id",
            "cause_id",
            "provider_order_id",
            "exposure_reservation_id",
            "recovery_event_id",
            "original_writer_owner_id",
            "current_writer_owner_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid dispatch recovery " + name)
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("dispatch recovery cause does not match intent")
        for name in (
            "intent_hash",
            "claim_digest",
            "proof_sha256",
            "proof_ready_record_sha256",
            "original_writer_fence_sha256",
            "order_observation_record_sha256",
            "fill_observation_record_sha256",
            "reconciliation_evidence_sha256",
            "accepted_request_sha256",
            "exposure_reservation_sha256",
            "recovery_event_id",
            "recovery_event_sha256",
            "current_writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        for name in (
            "proof_ready_revision",
            "order_observation_revision",
            "fill_observation_revision",
            "recovery_event_revision",
            "original_writer_fencing_token",
            "current_writer_fencing_token",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError("invalid dispatch recovery " + name)
        if not (
            self.order_observation_revision < self.proof_ready_revision
            and self.fill_observation_revision < self.proof_ready_revision
            and self.recovery_event_revision > self.proof_ready_revision
        ):
            raise ValueError("dispatch recovery journal revisions are not ordered")
        if self.current_writer_fencing_token <= self.original_writer_fencing_token:
            raise ValueError("dispatch recovery requires a newer writer fence")
        if self.current_writer_fence_sha256 == self.original_writer_fence_sha256:
            raise ValueError("dispatch recovery must bind a distinct newer writer fence")

    @property
    def fingerprint(self) -> str:
        """Stable digest of the original evidence and the current recovery fence."""
        return _sha256(_canonical_json(_dispatch_recovery_attestation_payload(self)))


@dataclass(frozen=True)
class DispatchResolutionReceipt:
    """Read-only, single-snapshot receipt for one durable dispatch claim."""

    status: DispatchResolutionReceiptStatus
    claim: DispatchClaimBinding
    proof: Optional[DispatchTrackedOrderProof] = None  # noqa: UP045 -- Python 3.9 is supported.
    proof_sha256: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    dispatch_latch_active: bool = True
    has_other_active_freezes: bool = False
    recovery_attestation: Optional[  # noqa: UP045 -- Python 3.9 is supported.
        DispatchRecoveryAttestation
    ] = None

    def __post_init__(self) -> None:
        if type(self.status) is not DispatchResolutionReceiptStatus:
            raise ValueError("invalid dispatch resolution receipt status")
        if type(self.claim) is not DispatchClaimBinding:
            raise ValueError("invalid dispatch resolution receipt claim")
        if (
            type(self.dispatch_latch_active) is not bool
            or type(self.has_other_active_freezes) is not bool
        ):
            raise ValueError("invalid dispatch resolution receipt freeze state")
        if self.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE:
            if (
                self.proof is not None
                or self.proof_sha256 is not None
                or self.recovery_attestation is not None
                or self.dispatch_latch_active is not True
            ):
                raise ValueError("unresolved receipt cannot carry a resolution proof")
            return
        if (
            type(self.proof) is not DispatchTrackedOrderProof
            or self.proof_sha256 != self.proof.fingerprint
            or self.dispatch_latch_active is not False
        ):
            raise ValueError("resolved receipt must carry its exact tracked-order proof")
        if self.recovery_attestation is not None and not _recovery_attestation_matches_proof(
            self.recovery_attestation, self.proof
        ):
            raise ValueError("recovery receipt does not match its tracked-order proof")

    @property
    def scope(self) -> AccountScope:
        return self.claim.scope

    @property
    def permit_id(self) -> str:
        return self.claim.permit_id

    @property
    def intent_id(self) -> str:
        return self.claim.intent_id

    @property
    def intent_hash(self) -> str:
        return self.claim.intent_hash

    @property
    def cause_id(self) -> str:
        return self.claim.cause_id

    @property
    def claim_digest(self) -> str:
        return self.claim.claim_digest


class DispatchTerminalResolutionReceiptStatus(str, Enum):
    """Durable state of one exact no-fill terminal dispatch decision."""

    UNRESOLVED_ACTIVE = "UNRESOLVED_ACTIVE"
    NO_FILL_TERMINAL_RESOLVED = "NO_FILL_TERMINAL_RESOLVED"


@dataclass(frozen=True)
class DispatchTerminalResolutionReceipt:
    """Same-snapshot Risk receipt for an exact no-fill terminal proof."""

    status: DispatchTerminalResolutionReceiptStatus
    claim: DispatchClaimBinding
    proof: Optional[DispatchTerminalProof] = None  # noqa: UP045 -- Python 3.9 is supported.
    proof_sha256: Optional[str] = None  # noqa: UP045 -- Python 3.9 is supported.
    group_claim: Optional[RiskGroupLegClaimReceipt] = None  # noqa: UP045
    dispatch_latch_active: bool = True
    has_other_active_freezes: bool = False
    fee_record_count: Optional[int] = None  # noqa: UP045 -- Python 3.9 is supported.
    fee_history_complete: bool = False

    def __post_init__(self) -> None:
        if type(self.status) is not DispatchTerminalResolutionReceiptStatus:
            raise ValueError("invalid terminal dispatch receipt status")
        if type(self.claim) is not DispatchClaimBinding:
            raise ValueError("invalid terminal dispatch receipt claim")
        if type(self.dispatch_latch_active) is not bool or type(
            self.has_other_active_freezes
        ) is not bool:
            raise ValueError("invalid terminal dispatch receipt freeze state")
        if type(self.fee_history_complete) is not bool:
            raise ValueError("invalid terminal dispatch fee-history flag")
        if self.group_claim is not None and (
            type(self.group_claim) is not RiskGroupLegClaimReceipt
            or self.group_claim.claim != self.claim
        ):
            raise ValueError("terminal receipt group claim does not match the dispatch claim")

        if self.status is DispatchTerminalResolutionReceiptStatus.UNRESOLVED_ACTIVE:
            if (
                self.proof is not None
                or self.proof_sha256 is not None
                or self.dispatch_latch_active is not True
                or self.fee_record_count is not None
                or self.fee_history_complete is not False
                or (self.group_claim is not None and self.group_claim.resolved)
            ):
                raise ValueError("unresolved terminal receipt cannot carry resolution evidence")
            return

        if (
            type(self.proof) is not DispatchTerminalProof
            or self.proof_sha256 != self.proof.fingerprint
            or self.dispatch_latch_active is not False
            or type(self.fee_record_count) is not int
            or self.fee_record_count != 0
            or self.fee_history_complete is not True
            or (self.group_claim is not None and not self.group_claim.resolved)
        ):
            raise ValueError("resolved terminal receipt lacks exact complete no-fill evidence")
        if (
            self.proof.scope != self.claim.scope
            or self.proof.permit_id != self.claim.permit_id
            or self.proof.intent_id != self.claim.intent_id
            or self.proof.intent_hash != self.claim.intent_hash
            or self.proof.cause_id != self.claim.cause_id
            or self.proof.claim_digest != self.claim.claim_digest
        ):
            raise ValueError("terminal receipt proof does not match its exact claim")

    @property
    def scope(self) -> AccountScope:
        return self.claim.scope

    @property
    def permit_id(self) -> str:
        return self.claim.permit_id

    @property
    def intent_id(self) -> str:
        return self.claim.intent_id

    @property
    def terminal_state(self) -> Optional[DispatchTerminalState]:  # noqa: UP045 -- Python 3.9 is supported.
        return None if self.proof is None else self.proof.terminal_state

    @property
    def journal_revision(self) -> Optional[int]:  # noqa: UP045 -- Python 3.9 is supported.
        return None if self.proof is None else self.proof.journal_revision


@dataclass(frozen=True)
class VerifiedDispatchRecoveryResolution:
    """Typed proof that the old journal evidence and new fence were verified."""

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claim_digest: str
    proof_sha256: str
    attestation_sha256: str
    recovery_event_id: str
    recovery_event_revision: int
    recovery_event_sha256: str
    original_writer_owner_id: str
    original_writer_fencing_token: int
    original_writer_fence_sha256: str
    current_writer_owner_id: str
    current_writer_fencing_token: int
    current_writer_fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid verified dispatch recovery scope")
        for name in (
            "permit_id",
            "intent_id",
            "cause_id",
            "recovery_event_id",
            "original_writer_owner_id",
            "current_writer_owner_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid verified dispatch recovery " + name)
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("verified recovery cause does not match intent")
        for name in (
            "intent_hash",
            "claim_digest",
            "proof_sha256",
            "attestation_sha256",
            "recovery_event_id",
            "recovery_event_sha256",
            "original_writer_fence_sha256",
            "current_writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.recovery_event_revision) is not int or self.recovery_event_revision <= 0:
            raise ValueError("invalid verified dispatch recovery event revision")
        if type(self.original_writer_fencing_token) is not int or (
            self.original_writer_fencing_token <= 0
        ):
            raise ValueError("invalid original dispatch recovery fence token")
        if type(self.current_writer_fencing_token) is not int or (
            self.current_writer_fencing_token <= self.original_writer_fencing_token
        ):
            raise ValueError("invalid current dispatch recovery fence token")
        if self.current_writer_fence_sha256 == self.original_writer_fence_sha256:
            raise ValueError("recovery verification must bind distinct old and new fences")


class VerifiedRecoveredExecutionJournalAuthority(Protocol):
    """Authority for replaying original proof evidence under a newer fence.

    The returned context manager must validate the complete immutable
    PROOF_READY/order/fill journal chain, exposure reference, original event
    fence, and append-only recovery event. It must prove that the supplied
    recovery event belongs to the exact settled Risk claim and hold the current
    account-wide writer fence until the context exits after Risk commits. It
    yields no receipt for missing, conflicting, stale, or incomplete evidence.
    """

    def recovered_dispatch_resolution_guard(
        self,
        proof: DispatchTrackedOrderProof,
        attestation: DispatchRecoveryAttestation,
        *,
        claim: DispatchClaimBinding,
    ) -> AbstractContextManager[
        Optional[VerifiedDispatchRecoveryResolution]  # noqa: UP045 -- Python 3.9 is supported.
    ]: ...


@dataclass(frozen=True)
class CancelDispatchResolutionProof:
    """Typed evidence for resolving only a cancellation-action dispatch latch.

    This is intentionally separate from :class:`DispatchTerminalProof`: a
    cancelled order may already have partial fills, and a rejected cancel
    leaves the target exposure open.  The configured journal authority must
    reread and attest the immutable cancel event, exact target postcondition,
    and current account writer fence before the gate consumes this proof.
    Constructing this value alone never authorizes resolution.
    """

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    cause_id: str
    claim_digest: str
    evidence_class: DispatchEvidenceClass
    dispatch_attempt_count: int
    cancel_intent_fingerprint: str
    execution_scope_key: str
    cancel_id: str
    target_intent_id: str
    provider_order_id: str
    terminal_state: CancelDispatchTerminalState
    cancel_event_id: str
    cancel_event_sequence: int
    cancel_event_sha256: str
    cancel_source: CancelDispatchSource
    source_evidence_sha256: str
    target_postcondition: CancelTargetPostcondition
    target_state: str
    target_filled_quantity: str
    target_updated_at_ns: int
    target_record_sha256: str
    target_event_id: str
    target_event_sequence: int
    target_event_type: str
    target_event_sha256: str
    writer_owner_id: str
    writer_fencing_token: int
    writer_fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid cancellation proof risk scope")
        _validate_dispatch_evidence_class(self.scope, self.evidence_class)
        for name in (
            "permit_id",
            "intent_id",
            "cause_id",
            "execution_scope_key",
            "cancel_id",
            "target_intent_id",
            "provider_order_id",
            "cancel_event_id",
            "target_event_id",
            "target_event_type",
            "writer_owner_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid cancellation proof " + name)
        if self.intent_id != (
            "cancel-admission:" + self.execution_scope_key + ":" + self.cancel_id
        ):
            raise ValueError("cancellation proof risk intent does not match cancel identity")
        if self.cause_id != "dispatch-inflight:" + self.intent_id:
            raise ValueError("cancellation proof cause does not match risk intent")
        for name in (
            "intent_hash",
            "claim_digest",
            "cancel_intent_fingerprint",
            "cancel_event_sha256",
            "source_evidence_sha256",
            "target_record_sha256",
            "target_event_sha256",
            "writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.terminal_state) is not CancelDispatchTerminalState:
            raise ValueError("invalid terminal cancellation-action state")
        if type(self.cancel_source) is not CancelDispatchSource:
            raise ValueError("invalid cancellation observation source")
        if type(self.target_postcondition) is not CancelTargetPostcondition:
            raise ValueError("invalid cancellation target postcondition")
        if type(self.dispatch_attempt_count) is not int or self.dispatch_attempt_count != 1:
            raise ValueError("cancellation resolution requires exactly one dispatch attempt")
        for name in ("cancel_event_sequence", "target_event_sequence", "target_updated_at_ns"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError("invalid cancellation proof " + name)
        if type(self.writer_fencing_token) is not int or self.writer_fencing_token <= 0:
            raise ValueError("invalid cancellation proof writer fencing token")
        if type(self.target_state) is not str or not self.target_state:
            raise ValueError("invalid cancellation target state")
        if type(self.target_filled_quantity) is not str:
            raise ValueError("invalid cancellation target filled quantity")
        try:
            filled_quantity = _as_decimal(self.target_filled_quantity)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("invalid cancellation target filled quantity") from exc
        if (
            filled_quantity < Decimal("0")
            or _decimal_text(filled_quantity) != self.target_filled_quantity
        ):
            raise ValueError("cancellation target filled quantity is not canonical")
        if self.terminal_state is CancelDispatchTerminalState.CANCELLED:
            if (
                self.target_postcondition is not CancelTargetPostcondition.TARGET_TERMINAL
                or self.target_state != "CANCELLED"
                or self.target_event_type != "cancelled_by_cancel_intent"
            ):
                raise ValueError(
                    "cancelled action requires its exact terminal target postcondition"
                )
        elif (
            self.target_postcondition is not CancelTargetPostcondition.TARGET_REMAINS_OPEN
            or self.target_state not in {"ACKED", "PARTIALLY_FILLED"}
        ):
            raise ValueError("rejected cancel action must retain a known open target")

    @property
    def fingerprint(self) -> str:
        """Stable digest of every cancellation action and target binding."""
        return _cancel_dispatch_resolution_proof_sha256(self)


def _cancel_dispatch_resolution_proof_sha256(
    proof: CancelDispatchResolutionProof,
) -> str:
    return _sha256(
        _canonical_json(
            {
                "cancel_event_id": proof.cancel_event_id,
                "cancel_event_sequence": proof.cancel_event_sequence,
                "cancel_event_sha256": proof.cancel_event_sha256,
                "cancel_id": proof.cancel_id,
                "cancel_intent_fingerprint": proof.cancel_intent_fingerprint,
                "cancel_source": proof.cancel_source.value,
                "cause_id": proof.cause_id,
                "claim_digest": proof.claim_digest,
                "dispatch_attempt_count": proof.dispatch_attempt_count,
                "evidence_class": proof.evidence_class.value,
                "execution_scope_key": proof.execution_scope_key,
                "intent_hash": proof.intent_hash,
                "intent_id": proof.intent_id,
                "permit_id": proof.permit_id,
                "provider_order_id": proof.provider_order_id,
                "scope_key": proof.scope.key,
                "source_evidence_sha256": proof.source_evidence_sha256,
                "target_event_id": proof.target_event_id,
                "target_event_sequence": proof.target_event_sequence,
                "target_event_sha256": proof.target_event_sha256,
                "target_event_type": proof.target_event_type,
                "target_filled_quantity": proof.target_filled_quantity,
                "target_intent_id": proof.target_intent_id,
                "target_postcondition": proof.target_postcondition.value,
                "target_record_sha256": proof.target_record_sha256,
                "target_state": proof.target_state,
                "target_updated_at_ns": proof.target_updated_at_ns,
                "terminal_state": proof.terminal_state.value,
                "writer_fence_sha256": proof.writer_fence_sha256,
                "writer_fencing_token": proof.writer_fencing_token,
                "writer_owner_id": proof.writer_owner_id,
                "schema": "bt-api-risk-cancel-dispatch-resolution-v1",
            }
        )
    )


@dataclass(frozen=True)
class VerifiedCancelDispatchResolution:
    """Exact cancellation evidence attested under the external writer fence."""

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    claim_digest: str
    proof_sha256: str
    cancel_event_id: str
    cancel_event_sequence: int
    cancel_event_sha256: str
    source_evidence_sha256: str
    target_postcondition: CancelTargetPostcondition
    target_record_sha256: str
    target_event_id: str
    target_event_sequence: int
    target_event_sha256: str
    writer_owner_id: str
    writer_fencing_token: int
    writer_fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid verified cancellation resolution scope")
        for name in ("permit_id", "intent_id", "writer_owner_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid verified cancellation resolution " + name)
        for name in (
            "intent_hash",
            "claim_digest",
            "proof_sha256",
            "cancel_event_sha256",
            "source_evidence_sha256",
            "target_record_sha256",
            "target_event_sha256",
            "writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.target_postcondition) is not CancelTargetPostcondition:
            raise ValueError("invalid verified cancellation target postcondition")
        for name in ("cancel_event_sequence", "target_event_sequence"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError("invalid verified cancellation resolution " + name)
        if type(self.writer_fencing_token) is not int or self.writer_fencing_token <= 0:
            raise ValueError("invalid verified cancellation writer fencing token")


class VerifiedCancellationJournalAuthority(Protocol):
    """Authority that rereads a terminal cancel event and target under its fence."""

    def cancel_dispatch_resolution_guard(
        self,
        proof: CancelDispatchResolutionProof,
        *,
        claim: DispatchClaimBinding,
    ) -> AbstractContextManager[
        Optional[VerifiedCancelDispatchResolution]  # noqa: UP045 -- Python 3.9 is supported.
    ]: ...


def _dispatch_resolution_proof_sha256(proof: DispatchResolutionProof) -> str:
    payload: dict[str, object] = {
        "cause_id": proof.cause_id,
        "claim_digest": proof.claim_digest,
        "dispatch_attempt_count": proof.dispatch_attempt_count,
        "evidence_class": proof.evidence_class.value,
        "filled_quantity": proof.filled_quantity,
        "intent_hash": proof.intent_hash,
        "intent_id": proof.intent_id,
        "journal_record_sha256": proof.journal_record_sha256,
        "journal_revision": proof.journal_revision,
        "permit_id": proof.permit_id,
        "reconciliation_evidence_sha256": proof.reconciliation_evidence_sha256,
        "scope_key": proof.scope.key,
        "trade_count": proof.trade_count,
        "writer_fence_sha256": proof.writer_fence_sha256,
    }
    if type(proof) is DispatchTerminalProof:
        payload.update(
            kind="TERMINAL",
            terminal_state=proof.terminal_state.value,
            fee_record_count=proof.fee_record_count,
            fee_history_complete=proof.fee_history_complete,
        )
    else:
        payload.update(
            kind="ACKED_TRACKED",
            accepted_request_sha256=proof.accepted_request_sha256,
            exposure_reservation_id=proof.exposure_reservation_id,
            exposure_reservation_sha256=proof.exposure_reservation_sha256,
            provider_order_id=proof.provider_order_id,
        )
    return _sha256(_canonical_json(payload))


def _dispatch_recovery_attestation_payload(
    attestation: DispatchRecoveryAttestation,
) -> dict[str, object]:
    """Return the versioned canonical payload stored for a recovery audit."""
    return {
        "accepted_request_sha256": attestation.accepted_request_sha256,
        "cause_id": attestation.cause_id,
        "claim_digest": attestation.claim_digest,
        "current_writer_fence_sha256": attestation.current_writer_fence_sha256,
        "current_writer_fencing_token": attestation.current_writer_fencing_token,
        "current_writer_owner_id": attestation.current_writer_owner_id,
        "exposure_reservation_id": attestation.exposure_reservation_id,
        "exposure_reservation_sha256": attestation.exposure_reservation_sha256,
        "fill_observation_record_sha256": attestation.fill_observation_record_sha256,
        "fill_observation_revision": attestation.fill_observation_revision,
        "intent_hash": attestation.intent_hash,
        "intent_id": attestation.intent_id,
        "order_observation_record_sha256": attestation.order_observation_record_sha256,
        "order_observation_revision": attestation.order_observation_revision,
        "original_writer_fence_sha256": attestation.original_writer_fence_sha256,
        "original_writer_fencing_token": attestation.original_writer_fencing_token,
        "original_writer_owner_id": attestation.original_writer_owner_id,
        "permit_id": attestation.permit_id,
        "proof_ready_record_sha256": attestation.proof_ready_record_sha256,
        "proof_ready_revision": attestation.proof_ready_revision,
        "proof_sha256": attestation.proof_sha256,
        "provider_order_id": attestation.provider_order_id,
        "reconciliation_evidence_sha256": attestation.reconciliation_evidence_sha256,
        "recovery_event_id": attestation.recovery_event_id,
        "recovery_event_revision": attestation.recovery_event_revision,
        "recovery_event_sha256": attestation.recovery_event_sha256,
        "scope": {
            "account_id": attestation.scope.account_id,
            "environment": attestation.scope.environment,
            "provider": attestation.scope.provider,
        },
        "schema": "bt-api-risk-dispatch-recovery-attestation-v1",
    }


def _recovery_attestation_matches_proof(
    attestation: DispatchRecoveryAttestation,
    proof: DispatchTrackedOrderProof,
) -> bool:
    return bool(
        type(attestation) is DispatchRecoveryAttestation
        and type(proof) is DispatchTrackedOrderProof
        and attestation.scope == proof.scope
        and attestation.permit_id == proof.permit_id
        and attestation.intent_id == proof.intent_id
        and attestation.intent_hash == proof.intent_hash
        and attestation.cause_id == proof.cause_id
        and attestation.claim_digest == proof.claim_digest
        and attestation.proof_sha256 == proof.fingerprint
        and attestation.proof_ready_revision == proof.journal_revision
        and attestation.proof_ready_record_sha256 == proof.journal_record_sha256
        and attestation.original_writer_fence_sha256 == proof.writer_fence_sha256
        and attestation.reconciliation_evidence_sha256 == proof.reconciliation_evidence_sha256
        and attestation.accepted_request_sha256 == proof.accepted_request_sha256
        and attestation.provider_order_id == proof.provider_order_id
        and attestation.exposure_reservation_id == proof.exposure_reservation_id
        and attestation.exposure_reservation_sha256 == proof.exposure_reservation_sha256
    )


def _recovery_attestation_matches_claim_proof(
    attestation: DispatchRecoveryAttestation,
    claim: DispatchClaimBinding,
    proof: DispatchTrackedOrderProof,
) -> bool:
    return bool(
        _recovery_attestation_matches_proof(attestation, proof)
        and attestation.scope == claim.scope
        and attestation.permit_id == claim.permit_id
        and attestation.intent_id == claim.intent_id
        and attestation.intent_hash == claim.intent_hash
        and attestation.cause_id == claim.cause_id
        and attestation.claim_digest == claim.claim_digest
    )


def _verified_dispatch_recovery_matches(
    verified: object,
    *,
    proof: DispatchTrackedOrderProof,
    attestation: DispatchRecoveryAttestation,
    claim: DispatchClaimBinding,
) -> bool:
    return bool(
        type(verified) is VerifiedDispatchRecoveryResolution
        and verified.scope == claim.scope == proof.scope == attestation.scope
        and verified.permit_id == claim.permit_id == proof.permit_id == attestation.permit_id
        and verified.intent_id == claim.intent_id == proof.intent_id == attestation.intent_id
        and verified.intent_hash
        == claim.intent_hash
        == proof.intent_hash
        == attestation.intent_hash
        and verified.cause_id == claim.cause_id == proof.cause_id == attestation.cause_id
        and verified.claim_digest
        == claim.claim_digest
        == proof.claim_digest
        == attestation.claim_digest
        and verified.proof_sha256 == proof.fingerprint == attestation.proof_sha256
        and verified.attestation_sha256 == attestation.fingerprint
        and verified.recovery_event_id == attestation.recovery_event_id
        and verified.recovery_event_revision == attestation.recovery_event_revision
        and verified.recovery_event_sha256 == attestation.recovery_event_sha256
        and verified.original_writer_owner_id == attestation.original_writer_owner_id
        and verified.original_writer_fencing_token == attestation.original_writer_fencing_token
        and verified.original_writer_fence_sha256 == attestation.original_writer_fence_sha256
        and verified.current_writer_owner_id == attestation.current_writer_owner_id
        and verified.current_writer_fencing_token == attestation.current_writer_fencing_token
        and verified.current_writer_fence_sha256 == attestation.current_writer_fence_sha256
    )


@dataclass(frozen=True)
class VerifiedDispatchResolution:
    """Typed attestation yielded while the trusted account writer fence is held.

    The risk gate compares this receipt with the exact proof and durable claim.
    It is not a signature and cannot establish journal truth by itself; only the
    injected authority can yield one after verifying its journal and fence.
    """

    scope: AccountScope
    permit_id: str
    intent_id: str
    intent_hash: str
    claim_digest: str
    proof_sha256: str
    journal_revision: int
    journal_record_sha256: str
    writer_fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid verified dispatch resolution scope")
        for name in ("permit_id", "intent_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError("invalid verified dispatch resolution " + name)
        for name in (
            "intent_hash",
            "claim_digest",
            "proof_sha256",
            "journal_record_sha256",
            "writer_fence_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.journal_revision) is not int or self.journal_revision <= 0:
            raise ValueError("invalid verified dispatch resolution revision")


class VerifiedExecutionJournalAuthority(Protocol):
    """Trusted application authority for a current dispatch resolution row.

    The context manager must verify the exact immutable journal row, prove one
    dispatch attempt and either a zero-fill terminal result or an ACKED order
    transferred to a durable per-order exposure reservation. It must hold the
    external account-wide writer fence for the entire context and reject stale
    or unknown revisions, duplicate attempts, ambiguous native query evidence,
    missing exposure reservations, and any scope mismatch.
    """

    def dispatch_resolution_guard(
        self,
        proof: DispatchResolutionProof,
        *,
        claim: DispatchClaimBinding,
    ) -> AbstractContextManager[
        Optional[VerifiedDispatchResolution]  # noqa: UP045 -- Python 3.9 is supported.
    ]: ...


class DurableRiskGate:
    """SQLite-backed account admission gate with fail-closed semantics.

    The gate intentionally has no provider client and never performs network
    I/O.  Callers own the provider dispatch, but must call
    :meth:`validate_permit` immediately before it. The gate does not allow a
    previously issued increase permit to bypass a subsequently raised freeze.
    Dispatch latches can be cleared only through an injected execution-journal
    authority that holds the account writer fence and proves an exact no-fill
    terminal result. No authority is installed by default.

    Freshness comparisons use an integer epoch-nanosecond clock. ``clock_ns``
    overrides that source; the legacy seconds-based ``clock`` remains available
    for persisted timestamps and, when supplied alone, is converted with its
    established seconds-to-nanoseconds behavior. Callers that supply both
    clocks are responsible for keeping the two time sources aligned.
    """

    _ACTIVE = "active"
    _SETTLED = "settled"
    _NO_FILL = "no_fill_terminal"
    _RELEASED = "released"
    _EXPIRED = "expired"

    def __init__(
        self,
        database_path: Union[Path, str],  # noqa: UP007 -- Python 3.9 is supported.
        policy: RiskPolicy,
        clock: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            Callable[[], float]
        ] = None,
        timeout_seconds: float = 5.0,
        *,
        execution_journal_authority: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            VerifiedExecutionJournalAuthority
        ] = None,
        gross_exposure_authority: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            GrossExposureAuthority
        ] = None,
        clock_ns: Optional[Callable[[], int]] = None,  # noqa: UP045 -- Python 3.9 is supported.
    ) -> None:
        self._database_path = Path(database_path)
        self._policy = policy
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")
        if clock_ns is not None and not callable(clock_ns):
            raise ValueError("clock_ns must be callable")
        self._clock = _wall_clock if clock is None else clock
        if clock_ns is not None:
            self._clock_ns = clock_ns
        elif clock is None:
            self._clock_ns = _wall_clock_ns
        else:
            # Preserve deterministic callers of the established seconds clock
            # API. Production defaults use time.time_ns directly; a caller
            # supplying both clocks owns their alignment.
            self._clock_ns = lambda: int(float(clock()) * 1_000_000_000)
        self._timeout_seconds = timeout_seconds
        self._execution_journal_authority = execution_journal_authority
        self._gross_exposure_authority = gross_exposure_authority
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def policy(self) -> RiskPolicy:
        """Return the immutable policy configured for this gate instance."""
        return self._policy

    def _now_ns(self) -> int:
        """Read an exact integer epoch-nanosecond clock for freshness checks."""
        value = self._clock_ns()
        if type(value) is not int or value < 0:
            raise ValueError("clock_ns must return a non-negative integer")
        return value

    def set_strategy_allocation(
        self,
        scope: AccountScope,
        strategy_id: str,
        allocation_version: str,
        *,
        max_notional: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            Decimal
        ] = None,
        max_position: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            Decimal
        ] = None,
        position_instrument: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            str
        ] = None,
        position_unit: Optional[str] = None,  # noqa: UP045 -- Python 3.9 is supported.
        notional_unit: Optional[str] = None,  # noqa: UP045 -- Python 3.9 is supported.
        max_gross_occupancy_notional: Optional[Decimal] = None,  # noqa: UP045 -- Python 3.9 is supported.
        max_gross_occupancy_intent_count: Optional[int] = None,  # noqa: UP045 -- Python 3.9 is supported.
    ) -> None:
        """Install one local allocation revision for a strategy in this account.

        This is an administrative storage seam, not an authenticated account
        authority.  A deployment must keep it out of strategy-facing objects
        and bind it to its own reviewed owner.  Versions are immutable and
        cannot be replayed after a later version has replaced them.  Position
        limits are reserved quantity bounds for one exact instrument/unit;
        this gate has no live position book and does not call them holdings.
        """

        if not isinstance(scope, AccountScope):
            raise ValueError("scope must be an AccountScope")
        for name, value in (
            ("strategy_id", strategy_id),
            ("allocation_version", allocation_version),
        ):
            if not isinstance(value, str) or not _ALLOCATION_ID.fullmatch(value):
                raise ValueError("invalid " + name)
        if notional_unit is None and self._policy.notional_unit is not None:
            notional_unit = self._policy.notional_unit

        normalized_notional = None
        if max_notional is not None:
            try:
                normalized_notional = _as_decimal(max_notional)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("max_notional must be a non-negative finite decimal") from exc
            if normalized_notional < Decimal("0"):
                raise ValueError("max_notional must be a non-negative finite decimal")
            if not isinstance(notional_unit, str) or not _NOTIONAL_UNIT.fullmatch(notional_unit):
                raise ValueError("max_notional requires a canonical notional_unit")
        elif notional_unit is not None and (
            not isinstance(notional_unit, str) or not _NOTIONAL_UNIT.fullmatch(notional_unit)
        ):
            raise ValueError("notional_unit must be a canonical uppercase unit code")
        if self._policy.notional_unit is not None and notional_unit != self._policy.notional_unit:
            raise ValueError("allocation notional_unit must match the account policy")
        if normalized_notional is not None and self._policy.notional_unit is None:
            raise ValueError("notional allocation requires a fixed account policy notional_unit")
        if self._policy.require_strategy_allocation and self._policy.notional_unit is None:
            raise ValueError("strategy allocation policy needs a notional_unit")

        normalized_position = None
        if max_position is not None:
            try:
                normalized_position = _as_decimal(max_position)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("max_position must be a positive finite decimal") from exc
            if normalized_position <= Decimal("0"):
                raise ValueError("max_position must be a positive finite decimal")
            if not isinstance(position_instrument, str) or not _ALLOCATION_INSTRUMENT.fullmatch(
                position_instrument
            ):
                raise ValueError("max_position requires an exact position_instrument")
            if not isinstance(position_unit, str) or not _ALLOCATION_ID.fullmatch(position_unit):
                raise ValueError("max_position requires an exact position_unit")
        elif position_instrument is not None or position_unit is not None:
            raise ValueError("position instrument/unit require max_position")
        normalized_gross_notional = None
        if max_gross_occupancy_notional is not None:
            try:
                normalized_gross_notional = _as_decimal(max_gross_occupancy_notional)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("max_gross_occupancy_notional must be non-negative") from exc
            if normalized_gross_notional < 0:
                raise ValueError("max_gross_occupancy_notional must be non-negative")
            if notional_unit != self._policy.notional_unit:
                raise ValueError("gross occupancy allocation unit must match the account policy")
        if max_gross_occupancy_intent_count is not None and (
            type(max_gross_occupancy_intent_count) is not int
            or max_gross_occupancy_intent_count < 0
        ):
            raise ValueError("max_gross_occupancy_intent_count must be non-negative")
        if self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS and (
            normalized_gross_notional is None or max_gross_occupancy_intent_count is None
        ):
            raise ValueError("LIVE_GROSS strategy allocation requires explicit gross caps")
        if (
            normalized_notional is None
            and normalized_position is None
            and normalized_gross_notional is None
        ):
            raise ValueError("an allocation needs at least one supported limit")

        now = self._clock()
        with self._transaction() as connection:
            self._ensure_policy_binding(connection, scope.key)
            current = connection.execute(
                "SELECT * FROM risk_strategy_allocations WHERE scope_key = ? AND strategy_id = ?",
                (scope.key, strategy_id),
            ).fetchone()
            if current is not None and current["allocation_version"] == allocation_version:
                same_facts = (
                    current["max_notional"]
                    == (None if normalized_notional is None else _decimal_text(normalized_notional))
                    and current["max_position"]
                    == (None if normalized_position is None else _decimal_text(normalized_position))
                    and current["position_instrument"] == position_instrument
                    and current["position_unit"] == position_unit
                    and current["notional_unit"] == notional_unit
                    and current["max_gross_occupancy_notional"]
                    == (
                        None
                        if normalized_gross_notional is None
                        else _decimal_text(normalized_gross_notional)
                    )
                    and current["max_gross_occupancy_intent_count"]
                    == max_gross_occupancy_intent_count
                )
                if same_facts:
                    return
                raise ValueError("allocation version cannot be reused with changed limits")

            prior_version = connection.execute(
                """SELECT 1 FROM risk_strategy_allocation_versions
                   WHERE scope_key = ? AND strategy_id = ? AND allocation_version = ?""",
                (scope.key, strategy_id, allocation_version),
            ).fetchone()
            if prior_version is not None:
                raise ValueError("allocation version cannot be reused")

            unresolved_claim = connection.execute(
                """SELECT 1 FROM risk_reservations AS reservation
                   JOIN risk_dispatch_claims AS claim
                     ON claim.permit_id = reservation.permit_id
                   LEFT JOIN risk_dispatch_resolutions AS resolution
                     ON resolution.permit_id = reservation.permit_id
                   WHERE reservation.scope_key = ? AND reservation.strategy_id = ?
                     AND reservation.status IN (?, ?) AND resolution.permit_id IS NULL
                   LIMIT 1""",
                (scope.key, strategy_id, self._ACTIVE, self._SETTLED),
            ).fetchone()
            if unresolved_claim is not None:
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_DISPATCH_IN_FLIGHT",
                    "allocation cannot change while a strategy dispatch is unresolved",
                )

            # When applying a position limit to prior strategy reservations,
            # prove they all used the same quantity unit.  Never add unlike
            # instrument units or silently reset their occupied budget.
            if normalized_position is not None:
                occupied = connection.execute(
                    """SELECT instrument, quantity_unit, quantity FROM risk_reservations
                       WHERE scope_key = ? AND strategy_id = ? AND action = ?
                         AND status IN (?, ?)""",
                    (
                        scope.key,
                        strategy_id,
                        IntentAction.INCREASE.value,
                        self._ACTIVE,
                        self._SETTLED,
                    ),
                ).fetchall()
                for row in occupied:
                    if (
                        row["instrument"] != position_instrument
                        or row["quantity_unit"] != position_unit
                        or row["quantity"] is None
                    ):
                        raise RiskDeniedError(
                            "STRATEGY_POSITION_USAGE_UNAVAILABLE",
                            "existing strategy reservations cannot be valued in this position unit",
                        )

            # Earlier account-only reservations carry no strategy identity, so
            # there is no safe way to decide whether they already consume this
            # strategy's allowance.  Do not silently start the strategy meter
            # at zero while such account exposure is outstanding.
            unallocated_usage = connection.execute(
                """SELECT 1 FROM risk_reservations
                   WHERE scope_key = ? AND strategy_id IS NULL AND action = ?
                     AND status IN (?, ?) LIMIT 1""",
                (scope.key, IntentAction.INCREASE.value, self._ACTIVE, self._SETTLED),
            ).fetchone()
            if unallocated_usage is not None:
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_USAGE_UNAVAILABLE",
                    "account has active reservations without a strategy identity",
                )

            connection.execute(
                """INSERT INTO risk_strategy_allocation_versions (
                       scope_key, strategy_id, allocation_version, max_notional,
                       max_position, position_instrument, position_unit, notional_unit,
                       max_gross_occupancy_notional, max_gross_occupancy_intent_count, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope.key,
                    strategy_id,
                    allocation_version,
                    None if normalized_notional is None else _decimal_text(normalized_notional),
                    None if normalized_position is None else _decimal_text(normalized_position),
                    position_instrument,
                    position_unit,
                    notional_unit,
                    None
                    if normalized_gross_notional is None
                    else _decimal_text(normalized_gross_notional),
                    max_gross_occupancy_intent_count,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO risk_strategy_allocations (
                       scope_key, strategy_id, allocation_version, max_notional,
                       max_position, position_instrument, position_unit, notional_unit,
                       max_gross_occupancy_notional, max_gross_occupancy_intent_count, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scope_key, strategy_id) DO UPDATE SET
                       allocation_version = excluded.allocation_version,
                       max_notional = excluded.max_notional,
                       max_position = excluded.max_position,
                       position_instrument = excluded.position_instrument,
                       position_unit = excluded.position_unit,
                       notional_unit = excluded.notional_unit,
                       max_gross_occupancy_notional = excluded.max_gross_occupancy_notional,
                       max_gross_occupancy_intent_count = excluded.max_gross_occupancy_intent_count,
                       updated_at = excluded.updated_at""",
                (
                    scope.key,
                    strategy_id,
                    allocation_version,
                    None if normalized_notional is None else _decimal_text(normalized_notional),
                    None if normalized_position is None else _decimal_text(normalized_position),
                    position_instrument,
                    position_unit,
                    notional_unit,
                    None
                    if normalized_gross_notional is None
                    else _decimal_text(normalized_gross_notional),
                    max_gross_occupancy_intent_count,
                    now,
                ),
            )

    def get_strategy_allocation(
        self, scope: AccountScope, strategy_id: str
    ) -> StrategyAllocationSnapshot:
        """Read an immutable allowance and local reservation-ledger snapshot.

        Numeric ``available_notional`` is returned only when the strategy
        ceiling, account ceiling, and every active/settled reservation share
        the exact fixed policy unit. This is never cash, margin, or provider
        account availability. The read does not expire reservations or mutate
        allocation/claim state; an expired but still ACTIVE reservation is
        conservatively shown as reserved until a separate write operation
        performs the existing TTL transition.
        """

        if not isinstance(scope, AccountScope):
            raise ValueError("scope must be an AccountScope")
        if not isinstance(strategy_id, str) or not _ALLOCATION_ID.fullmatch(strategy_id):
            raise ValueError("invalid strategy_id")
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                policy_error = self._policy_binding_read_error(connection, scope.key)
                allocation = connection.execute(
                    """SELECT * FROM risk_strategy_allocations
                   WHERE scope_key = ? AND strategy_id = ?""",
                    (scope.key, strategy_id),
                ).fetchone()
                if allocation is None:
                    raise RiskDeniedError(
                        "STRATEGY_ALLOCATION_UNAVAILABLE",
                        "strategy has no installed allocation",
                    )
                strategy_rows = connection.execute(
                    """SELECT notional, notional_unit, status FROM risk_reservations
                   WHERE scope_key = ? AND strategy_id = ? AND action = ?
                     AND status IN (?, ?)""",
                    (
                        scope.key,
                        strategy_id,
                        IntentAction.INCREASE.value,
                        self._ACTIVE,
                        self._SETTLED,
                    ),
                ).fetchall()
                account_rows = connection.execute(
                    """SELECT notional, notional_unit, status FROM risk_reservations
                   WHERE scope_key = ? AND action = ? AND status IN (?, ?)""",
                    (
                        scope.key,
                        IntentAction.INCREASE.value,
                        self._ACTIVE,
                        self._SETTLED,
                    ),
                ).fetchall()
                as_of = float(self._clock())
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

        policy_unit = self._policy.notional_unit
        allocation_unit = allocation["notional_unit"]
        strategy_units_match = (
            policy_unit is not None
            and allocation_unit == policy_unit
            and all(row["notional_unit"] == policy_unit for row in strategy_rows)
        )
        account_units_match = policy_unit is not None and all(
            row["notional_unit"] == policy_unit for row in account_rows
        )

        used: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
        reserved: Optional[Decimal] = None  # noqa: UP045 -- Python 3.9 is supported.
        account_used: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            Decimal
        ] = None
        account_reserved: Optional[  # noqa: UP045 -- Python 3.9 is supported.
            Decimal
        ] = None
        if strategy_units_match:
            used = _sum_decimal_exact(
                _as_decimal(row["notional"])
                for row in strategy_rows
                if row["status"] == self._SETTLED
            )
            reserved = _sum_decimal_exact(
                _as_decimal(row["notional"])
                for row in strategy_rows
                if row["status"] == self._ACTIVE
            )
        if account_units_match:
            account_used = _sum_decimal_exact(
                _as_decimal(row["notional"])
                for row in account_rows
                if row["status"] == self._SETTLED
            )
            account_reserved = _sum_decimal_exact(
                _as_decimal(row["notional"])
                for row in account_rows
                if row["status"] == self._ACTIVE
            )

        allocated = (
            None if allocation["max_notional"] is None else _as_decimal(allocation["max_notional"])
        )
        available = None
        reason = None
        if policy_unit is None:
            reason = "NOTIONAL_UNIT_UNBOUND"
        elif policy_error is not None:
            reason = policy_error
        elif allocation_unit != policy_unit:
            reason = "ALLOCATION_NOTIONAL_UNIT_MISMATCH"
        elif not strategy_units_match:
            reason = "STRATEGY_NOTIONAL_USAGE_UNIT_MISMATCH"
        elif not account_units_match:
            reason = "ACCOUNT_NOTIONAL_USAGE_UNIT_MISMATCH"
        elif allocated is None:
            reason = "STRATEGY_NOTIONAL_LIMIT_UNAVAILABLE"
        else:
            assert used is not None and reserved is not None
            assert account_used is not None and account_reserved is not None
            strategy_remaining = max(
                Fraction(0),
                Fraction(allocated) - Fraction(used) - Fraction(reserved),
            )
            account_remaining = max(
                Fraction(0),
                Fraction(self._policy.max_increase_notional)
                - Fraction(account_used)
                - Fraction(account_reserved),
            )
            available = _decimal_from_fraction(min(strategy_remaining, account_remaining))

        return StrategyAllocationSnapshot(
            scope=scope,
            strategy_id=strategy_id,
            revision=str(allocation["allocation_version"]),
            allocated_notional=allocated,
            used_notional=used,
            reserved_notional=reserved,
            available_notional=available,
            notional_unit=allocation_unit,
            max_position=(
                None
                if allocation["max_position"] is None
                else _as_decimal(allocation["max_position"])
            ),
            position_instrument=allocation["position_instrument"],
            position_unit=allocation["position_unit"],
            as_of=as_of,
            max_gross_occupancy_notional=(
                None
                if allocation["max_gross_occupancy_notional"] is None
                else _as_decimal(allocation["max_gross_occupancy_notional"])
            ),
            max_gross_occupancy_intent_count=allocation["max_gross_occupancy_intent_count"],
            completeness="LOCAL_LEDGER_COMPLETE"
            if available is not None
            else "LOCAL_LEDGER_INCOMPLETE",
            reason=reason,
        )

    def set_margin_pool_limit(
        self,
        scope: AccountScope,
        product_type: str,
        margin_pool: str,
        allocation_version: str,
        *,
        max_notional: Decimal,
    ) -> None:
        """Install one local notional ceiling for an exact margin-pool resource.

        The resource key is the Base package ``AccountResourceKey`` pair
        ``(product_type, margin_pool)`` so two different contracts/products
        can share one declared pool.  This is an administrative storage seam,
        not an authenticated collateral authority: it never reads provider
        collateral, never distributes one account's or pool's credit to
        another key, and its ceiling is checked against the exact trusted
        reservation notional inside each admission transaction.  Versions are
        immutable and cannot be replayed once a later version replaced them.
        A limit revision gates future reservations only; outstanding
        reservations keep their face-value occupation.
        """

        if not isinstance(scope, AccountScope):
            raise ValueError("scope must be an AccountScope")
        try:
            resource = AccountResourceKey(product_type=product_type, margin_pool=margin_pool)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid margin-pool resource key") from exc
        if type(allocation_version) is not str or not _ALLOCATION_ID.fullmatch(allocation_version):
            raise ValueError("invalid allocation_version")
        try:
            normalized_notional = _as_decimal(max_notional)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("max_notional must be a non-negative finite decimal") from exc
        if normalized_notional < Decimal("0"):
            raise ValueError("max_notional must be a non-negative finite decimal")
        notional_unit = self._policy.notional_unit
        if notional_unit is None:
            raise ValueError("margin-pool limits require a fixed account policy notional_unit")

        now = self._clock()
        with self._transaction() as connection:
            self._ensure_policy_binding(connection, scope.key)
            current = connection.execute(
                """SELECT * FROM risk_margin_pool_limits
                   WHERE scope_key = ? AND product_type = ? AND margin_pool = ?""",
                (scope.key, resource.product_type, resource.margin_pool),
            ).fetchone()
            if current is not None and current["allocation_version"] == allocation_version:
                same_facts = (
                    current["max_notional"] == _decimal_text(normalized_notional)
                    and current["notional_unit"] == notional_unit
                )
                if same_facts:
                    return
                raise ValueError("pool limit version cannot be reused with changed limits")
            prior_version = connection.execute(
                """SELECT 1 FROM risk_margin_pool_limit_versions
                   WHERE scope_key = ? AND product_type = ? AND margin_pool = ?
                     AND allocation_version = ?""",
                (scope.key, resource.product_type, resource.margin_pool, allocation_version),
            ).fetchone()
            if prior_version is not None:
                raise ValueError("pool limit version cannot be reused")

            connection.execute(
                """INSERT INTO risk_margin_pool_limit_versions (
                       scope_key, product_type, margin_pool, allocation_version,
                       max_notional, notional_unit, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope.key,
                    resource.product_type,
                    resource.margin_pool,
                    allocation_version,
                    _decimal_text(normalized_notional),
                    notional_unit,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO risk_margin_pool_limits (
                       scope_key, product_type, margin_pool, allocation_version,
                       max_notional, notional_unit, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scope_key, product_type, margin_pool) DO UPDATE SET
                       allocation_version = excluded.allocation_version,
                       max_notional = excluded.max_notional,
                       notional_unit = excluded.notional_unit,
                       updated_at = excluded.updated_at""",
                (
                    scope.key,
                    resource.product_type,
                    resource.margin_pool,
                    allocation_version,
                    _decimal_text(normalized_notional),
                    notional_unit,
                    now,
                ),
            )

    def reserve(self, intent: RiskIntent) -> RiskPermit:
        """Atomically reserve an account-scoped permit for ``intent``.

        Calling this method repeatedly with an identical pending intent is
        idempotent.  Reusing an intent id with another payload, or after it has
        settled/released/expired, is rejected rather than producing a second
        provider dispatch opportunity.
        """
        now = self._clock()
        live_gross = self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS
        with self._transaction() as connection:
            self._assert_current_instrument_facts(intent)
            if live_gross:
                # BEGIN IMMEDIATE can wait behind another process.  LIVE_GROSS
                # freshness is checked at the point where this transaction
                # owns the writer lock, never with a pre-wait timestamp.
                now = self._clock()
            self._ensure_policy_binding(connection, intent.scope.key)
            self._expire_active(connection, now)
            existing = connection.execute(
                "SELECT * FROM risk_reservations WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()
            if existing is not None:
                if existing["group_id"] is not None:
                    raise RiskDeniedError(
                        "GROUP_PERMIT_REQUIRED",
                        "group permit must be used through its explicit group operation",
                    )
                permit = self._existing_permit_or_raise(existing, intent, now)
                self._assert_reservation_allocation_current(connection, existing)
                if permit.action is IntentAction.INCREASE:
                    self._assert_not_frozen(connection, permit.scope)
                    if live_gross:
                        self._assert_live_gross_limits(connection, intent, include_intent=False)
                        final_now = self._clock()
                        self._expire_active(connection, final_now)
                        self._existing_permit_or_raise(existing, intent, final_now)
                        self._assert_live_gross_limits(connection, intent, include_intent=False)
                self._assert_current_instrument_facts(intent)
                return permit

            if intent.action is IntentAction.UNKNOWN_EFFECT:
                raise RiskDeniedError(
                    "UNKNOWN_EFFECT",
                    "intents with unknown effect are not eligible for admission",
                )
            if intent.action is IntentAction.INCREASE:
                self._assert_not_frozen(connection, intent.scope)
                self._assert_intent_notional_unit(intent)
                self._assert_increase_limit(connection, intent)
                self._assert_strategy_allocation_limit(connection, intent)
                self._assert_margin_pool_limit(connection, intent)
                if live_gross:
                    self._assert_live_gross_limits(connection, intent)

            generation = self._next_generation(connection, intent.scope.key)
            permit = RiskPermit(
                permit_id=str(uuid.uuid4()),
                intent_id=intent.intent_id,
                scope=intent.scope,
                action=intent.action,
                notional=intent.notional,
                policy_fingerprint=self._policy.fingerprint,
                generation=generation,
                issued_at=now,
                expires_at=now + self._policy.permit_ttl_seconds,
                strategy_id=intent.strategy_id,
                allocation_version=intent.allocation_version,
                instrument=intent.instrument,
                quantity=intent.quantity,
                quantity_unit=intent.quantity_unit,
                notional_unit=intent.notional_unit,
            )
            self._assert_current_instrument_facts(intent)
            connection.execute(
                """
                INSERT INTO risk_reservations (
                    permit_id, intent_id, intent_hash, scope_key, provider, account_id, environment,
                    action, notional, policy_hash, generation, issued_at, expires_at, status, reason,
                    strategy_id, allocation_version, instrument, quantity, quantity_unit,
                    notional_unit, group_id, group_fingerprint, group_leg_id,
                    group_deadline_ns, group_expires_at_ns, group_leg_count,
                    group_exposure_profile_fingerprint, resource_product_type, resource_margin_pool
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?,
                    NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    permit.permit_id,
                    permit.intent_id,
                    intent.fingerprint,
                    intent.scope.key,
                    intent.scope.provider,
                    intent.scope.account_id,
                    intent.scope.environment,
                    intent.action.value,
                    _decimal_text(intent.notional),
                    permit.policy_fingerprint,
                    permit.generation,
                    permit.issued_at,
                    permit.expires_at,
                    self._ACTIVE,
                    permit.strategy_id,
                    permit.allocation_version,
                    permit.instrument,
                    None if permit.quantity is None else _decimal_text(permit.quantity),
                    permit.quantity_unit,
                    permit.notional_unit,
                    None if intent.resource is None else intent.resource.product_type,
                    None if intent.resource is None else intent.resource.margin_pool,
                ),
            )
            if live_gross and intent.action is IntentAction.INCREASE:
                # Re-read the clock immediately before COMMIT.  The new permit
                # is already in this transaction, so the usage projection
                # counts it exactly once while the final freshness/cap check
                # verifies that it is still safe to admit.
                final_now = self._clock()
                self._expire_active(connection, final_now)
                if permit.expires_at <= final_now:
                    raise RiskDeniedError(
                        "INTENT_EXPIRED", "permit TTL elapsed before LIVE_GROSS admission committed"
                    )
                self._assert_live_gross_limits(connection, intent, include_intent=False)
            self._assert_current_instrument_facts(intent)
            return permit

    def reserve_group_vector(
        self,
        scope: AccountScope,
        group_id: str,
        legs: Sequence[RiskGroupLegIntent],
        *,
        deadline_ns: int,
    ) -> RiskGroupReservationReceipt:
        """Atomically reserve a complete same-account vector of opening legs.

        The immutable group identity includes the full, mapper-produced
        maximum-size intent for every leg and one absolute UTC deadline.  A
        retry with the same identity returns the existing receipt; it never
        renews permit TTLs or creates replacement permits.  All budget checks
        and all inserts share one ``BEGIN IMMEDIATE`` transaction.
        """

        if type(scope) is not AccountScope:
            raise RiskDeniedError("GROUP_SCOPE_MISMATCH", "a trusted account scope is required")
        if type(group_id) is not str or not _ALLOCATION_ID.fullmatch(group_id):
            raise RiskDeniedError("GROUP_INVALID_LEG", "group_id is invalid")
        if type(deadline_ns) is not int or not 0 < deadline_ns <= 9_223_372_036_854_775_807:
            raise RiskDeniedError("GROUP_DEADLINE_INVALID", "group deadline must be UTC epoch ns")
        if isinstance(legs, (str, bytes)) or not isinstance(legs, Sequence):
            raise RiskDeniedError("GROUP_INVALID_LEG", "a complete leg sequence is required")
        if not 1 <= len(legs) <= _RISK_GROUP_MAX_LEGS:
            raise RiskDeniedError(
                "GROUP_INVALID_LEG", "group vector leg count is outside its bound"
            )
        ordered_legs = tuple(sorted(legs, key=lambda leg: getattr(leg, "leg_id", "")))
        if any(type(leg) is not RiskGroupLegIntent for leg in ordered_legs):
            raise RiskDeniedError(
                "GROUP_INVALID_LEG", "every member must be a typed RiskGroupLegIntent"
            )
        leg_ids = [leg.leg_id for leg in ordered_legs]
        intent_ids = [leg.intent.intent_id for leg in ordered_legs]
        if len(set(leg_ids)) != len(leg_ids) or len(set(intent_ids)) != len(intent_ids):
            raise RiskDeniedError("GROUP_INVALID_LEG", "group leg and intent IDs must be unique")
        for leg in ordered_legs:
            intent = leg.intent
            if intent.scope != scope:
                raise RiskDeniedError(
                    "GROUP_SCOPE_MISMATCH", "every group leg must use one account scope"
                )
            if intent.action is not IntentAction.INCREASE:
                raise RiskDeniedError(
                    "GROUP_INVALID_LEG", "group reservations only admit increases"
                )
            if (
                intent.strategy_id is None
                or intent.allocation_version is None
                or intent.instrument is None
                or intent.quantity is None
                or intent.quantity_unit is None
                or intent.notional_unit is None
                or intent.exposure_profile_fingerprint is None
                or intent.instrument_facts_observed_at_ns is None
                or intent.instrument_facts_valid_until_ns is None
            ):
                raise RiskDeniedError(
                    "GROUP_INVALID_LEG",
                    "group legs require strategy, instrument, quantity, unit, and profile bindings",
                )
            if not _SHA256_HEX.fullmatch(intent.payload_fingerprint):
                raise RiskDeniedError(
                    "GROUP_INVALID_LEG", "group leg payload fingerprint must be SHA-256"
                )
            if self._policy.require_strategy_allocation and intent.strategy_id is None:
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_REQUIRED", "group leg lacks strategy allocation identity"
                )

        group_fingerprint = _risk_group_fingerprint(
            scope,
            group_id,
            self._policy.fingerprint,
            deadline_ns,
            tuple(
                (
                    leg.leg_id,
                    leg.intent.intent_id,
                    leg.intent.fingerprint,
                    str(leg.intent.exposure_profile_fingerprint),
                )
                for leg in ordered_legs
            ),
        )
        live_gross = self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS
        with self._transaction() as connection:
            now = self._clock()
            now_ns = self._now_ns()
            self._ensure_policy_binding(connection, scope.key)
            self._expire_active(connection, now)
            existing_rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (scope.key, group_id),
            ).fetchall()
            if existing_rows:
                if not self._stored_group_matches(
                    existing_rows,
                    scope=scope,
                    group_id=group_id,
                    group_fingerprint=group_fingerprint,
                    policy_fingerprint=self._policy.fingerprint,
                    deadline_ns=deadline_ns,
                    expected_legs=tuple(
                        (
                            leg.leg_id,
                            leg.intent.intent_id,
                            leg.intent.fingerprint,
                            str(leg.intent.exposure_profile_fingerprint),
                        )
                        for leg in ordered_legs
                    ),
                ):
                    raise RiskDeniedError(
                        "GROUP_ID_REUSED", "group ID is already bound to another vector or deadline"
                    )
                return self._group_receipt_in_transaction(
                    connection, scope, group_id, existing_rows
                )
            if now_ns >= deadline_ns:
                raise RiskDeniedError("GROUP_DEADLINE_ELAPSED", "group deadline already elapsed")

            for leg in ordered_legs:
                prior = connection.execute(
                    "SELECT group_id FROM risk_reservations WHERE intent_id = ?",
                    (leg.intent.intent_id,),
                ).fetchone()
                if prior is not None:
                    raise RiskDeniedError(
                        "GROUP_INTENT_REUSED", "a group leg intent ID is already reserved"
                    )

            ttl_ns = int(math.ceil(self._policy.permit_ttl_seconds * 1_000_000_000))
            facts_expiry_ns = min(
                int(leg.intent.instrument_facts_valid_until_ns) for leg in ordered_legs
            )
            expires_at_ns = min(deadline_ns, now_ns + ttl_ns, facts_expiry_ns)
            if expires_at_ns <= now_ns:
                raise RiskDeniedError(
                    "GROUP_EXPOSURE_FACTS_STALE", "group instrument facts have no live interval"
                )
            expires_at = expires_at_ns / 1_000_000_000
            for leg in ordered_legs:
                intent = leg.intent
                self._assert_group_exposure_facts(intent, now_ns)
                self._assert_not_frozen(connection, scope)
                self._assert_intent_notional_unit(intent)
                self._assert_increase_limit(connection, intent)
                self._assert_strategy_allocation_limit(connection, intent)
                self._assert_margin_pool_limit(connection, intent)
                if live_gross:
                    self._assert_live_gross_limits(connection, intent)
                generation = self._next_generation(connection, scope.key)
                permit = RiskPermit(
                    permit_id=str(uuid.uuid4()),
                    intent_id=intent.intent_id,
                    scope=scope,
                    action=IntentAction.INCREASE,
                    notional=intent.notional,
                    policy_fingerprint=self._policy.fingerprint,
                    generation=generation,
                    issued_at=now_ns / 1_000_000_000,
                    expires_at=expires_at,
                    strategy_id=intent.strategy_id,
                    allocation_version=intent.allocation_version,
                    instrument=intent.instrument,
                    quantity=intent.quantity,
                    quantity_unit=intent.quantity_unit,
                    notional_unit=intent.notional_unit,
                )
                connection.execute(
                    """INSERT INTO risk_reservations (
                           permit_id, intent_id, intent_hash, scope_key, provider, account_id,
                           environment, action, notional, policy_hash, generation, issued_at,
                           expires_at, status, reason, strategy_id, allocation_version,
                           instrument, quantity, quantity_unit, notional_unit, group_id,
                           group_fingerprint, group_leg_id, group_deadline_ns,
                           group_expires_at_ns, group_leg_count,
                           group_exposure_profile_fingerprint,
                           resource_product_type, resource_margin_pool
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?,
                                 ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        permit.permit_id,
                        permit.intent_id,
                        intent.fingerprint,
                        scope.key,
                        scope.provider,
                        scope.account_id,
                        scope.environment,
                        IntentAction.INCREASE.value,
                        _decimal_text(intent.notional),
                        self._policy.fingerprint,
                        generation,
                        permit.issued_at,
                        permit.expires_at,
                        self._ACTIVE,
                        intent.strategy_id,
                        intent.allocation_version,
                        intent.instrument,
                        _decimal_text(intent.quantity),
                        intent.quantity_unit,
                        intent.notional_unit,
                        group_id,
                        group_fingerprint,
                        leg.leg_id,
                        deadline_ns,
                        expires_at_ns,
                        len(ordered_legs),
                        intent.exposure_profile_fingerprint,
                        None if intent.resource is None else intent.resource.product_type,
                        None if intent.resource is None else intent.resource.margin_pool,
                    ),
                )
            commit_now_ns = self._now_ns()
            for leg in ordered_legs:
                self._assert_group_exposure_facts(leg.intent, commit_now_ns)
            if commit_now_ns >= deadline_ns:
                raise RiskDeniedError(
                    "GROUP_DEADLINE_ELAPSED", "group deadline elapsed before commit"
                )
            if commit_now_ns >= now_ns + ttl_ns:
                raise RiskDeniedError("GROUP_PERMIT_EXPIRED", "group TTL elapsed before commit")
            if live_gross:
                commit_now = commit_now_ns / 1_000_000_000
                self._expire_active(connection, commit_now)
                for leg in ordered_legs:
                    self._assert_live_gross_limits(connection, leg.intent, include_intent=False)
            rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (scope.key, group_id),
            ).fetchall()
            result = self._group_receipt_in_transaction(connection, scope, group_id, rows)
            linearized_at_ns = self._now_ns()
            for leg in ordered_legs:
                self._assert_group_exposure_facts(leg.intent, linearized_at_ns)
            if linearized_at_ns >= deadline_ns:
                raise RiskDeniedError(
                    "GROUP_DEADLINE_ELAPSED", "group deadline elapsed before commit"
                )
            if linearized_at_ns >= now_ns + ttl_ns:
                raise RiskDeniedError("GROUP_PERMIT_EXPIRED", "group TTL elapsed before commit")
            if live_gross:
                for leg in ordered_legs:
                    self._assert_live_gross_limits(
                        connection,
                        leg.intent,
                        include_intent=False,
                        now_ns=linearized_at_ns,
                    )
            return result

    def read_group_vector_snapshot(
        self, scope: AccountScope, group_id: str
    ) -> RiskGroupReservationReceipt:
        """Read exact persisted group state without changing any Risk rows.

        The receipt describes lifecycle, claim, and resolution state as stored
        in one SQLite read snapshot. In particular, an elapsed group deadline
        or permit expiry does not rewrite an ``ACTIVE`` row here. Consumers
        must treat the returned state as historical facts, not dispatch
        authorization. A leg is only a time-eligible claim candidate when its
        persisted state is ACTIVE, it is unclaimed and unresolved, and the
        caller's current UTC epoch-ns is before both ``deadline_ns`` and that
        leg's ``expires_at_ns``. ``claim_group_leg`` remains authoritative and
        rechecks current deadlines and policy on its write path.
        """

        self._validate_group_key(scope, group_id)
        with self._connection() as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            try:
                rows = connection.execute(
                    """SELECT * FROM risk_reservations
                       WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                    (scope.key, group_id),
                ).fetchall()
                if not rows:
                    raise RiskDeniedError("GROUP_NOT_FOUND", "group reservation does not exist")
                receipt = self._group_receipt_in_transaction(connection, scope, group_id, rows)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
                return receipt

    def query_group_vector(self, scope: AccountScope, group_id: str) -> RiskGroupReservationReceipt:
        """Return one transactional projection of every member in a group."""

        self._validate_group_key(scope, group_id)
        with self._transaction() as connection:
            now = self._clock()
            self._ensure_policy_binding(connection, scope.key)
            self._expire_active(connection, now)
            rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (scope.key, group_id),
            ).fetchall()
            if not rows:
                raise RiskDeniedError("GROUP_NOT_FOUND", "group reservation does not exist")
            return self._group_receipt_in_transaction(connection, scope, group_id, rows)

    def query_group_leg_claim(
        self, scope: AccountScope, group_id: str, leg_id: str
    ) -> Optional[RiskGroupLegClaimReceipt]:  # noqa: UP045 -- Python 3.9 is supported.
        """Read one group's exact claim from a read-only SQLite snapshot.

        ``None`` means only that the validated target member is still ACTIVE,
        unclaimed, and within both its persisted TTL and group deadline. A
        missing, expired, released, corrupt, or otherwise non-claimable member
        is a typed refusal rather than an empty result.
        """

        self._validate_group_key(scope, group_id)
        if type(leg_id) is not str or not _ALLOCATION_ID.fullmatch(leg_id):
            raise PermitInvalidError("GROUP_MEMBER_MISMATCH", "group leg identity is invalid")
        with self._connection() as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            try:
                policy_error = self._policy_binding_read_error(connection, scope.key)
                if policy_error is not None:
                    raise PermitInvalidError(
                        policy_error, "risk database is bound to another or unknown policy"
                    )
                rows = connection.execute(
                    """SELECT * FROM risk_reservations
                       WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                    (scope.key, group_id),
                ).fetchall()
                if not rows:
                    raise RiskDeniedError("GROUP_NOT_FOUND", "group reservation does not exist")
                group_receipt = self._group_receipt_in_transaction(
                    connection, scope, group_id, rows
                )
                leg = next((item for item in group_receipt.legs if item.leg_id == leg_id), None)
                row = next((item for item in rows if item["group_leg_id"] == leg_id), None)
                if leg is None or row is None:
                    raise PermitInvalidError(
                        "GROUP_MEMBER_MISMATCH", "group leg is not part of the durable vector"
                    )
                claim_row = connection.execute(
                    "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?",
                    (leg.permit_id,),
                ).fetchone()
                if not leg.claimed:
                    if leg.state is not RiskGroupReservationState.ACTIVE or claim_row is not None:
                        raise PermitInvalidError(
                            "GROUP_MEMBER_NOT_CLAIMABLE",
                            "only a valid active unclaimed member has no claim receipt",
                        )
                    unresolved_markers = connection.execute(
                        """SELECT
                               (SELECT COUNT(*) FROM risk_dispatch_resolutions
                                WHERE permit_id = ?) AS resolutions,
                               (SELECT COUNT(*) FROM risk_freezes
                                WHERE scope_key = ? AND cause_id = ?) AS latches""",
                        (
                            leg.permit_id,
                            scope.key,
                            self._dispatch_freeze_cause(leg.intent_id),
                        ),
                    ).fetchone()
                    if (
                        unresolved_markers is None
                        or int(unresolved_markers["resolutions"]) != 0
                        or int(unresolved_markers["latches"]) != 0
                    ):
                        raise PermitInvalidError(
                            "GROUP_STATE_CONFLICT",
                            "an unclaimed member cannot carry a claim resolution or dispatch latch",
                        )
                    now_ns = self._now_ns()
                    if now_ns >= group_receipt.deadline_ns:
                        raise PermitInvalidError(
                            "GROUP_DEADLINE_ELAPSED", "group deadline has elapsed"
                        )
                    if now_ns >= leg.expires_at_ns:
                        raise PermitInvalidError(
                            "GROUP_PERMIT_EXPIRED", "group permit TTL has elapsed"
                        )
                    result = None
                else:
                    if claim_row is None:
                        raise PermitInvalidError(
                            "GROUP_STATE_CONFLICT", "claimed vector member has no claim row"
                        )
                    result = self._group_leg_claim_receipt_in_transaction(
                        row, claim_row, resolved=leg.resolved
                    )
                    if leg.resolved:
                        resolution = connection.execute(
                            "SELECT proof_kind FROM risk_dispatch_resolutions WHERE permit_id = ?",
                            (leg.permit_id,),
                        ).fetchone()
                        if resolution is None:
                            raise PermitInvalidError(
                                "GROUP_STATE_CONFLICT", "resolved claim has no exact resolution"
                            )
                        proof_kind = str(resolution["proof_kind"])
                        if proof_kind in {item.value for item in DispatchTerminalState}:
                            terminal = self._dispatch_terminal_resolution_receipt_in_transaction(
                                connection, leg.permit_id
                            )
                            valid_resolution = (
                                terminal.status
                                is DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
                                and terminal.claim == result.claim
                                and terminal.group_claim == result
                            )
                        elif proof_kind == "ACKED_TRACKED":
                            tracked = self._dispatch_resolution_receipt_in_transaction(
                                connection, leg.permit_id
                            )
                            valid_resolution = (
                                tracked.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
                                and tracked.claim == result.claim
                                and tracked.dispatch_latch_active is False
                            )
                        elif proof_kind == "TERMINAL_EXPOSURE":
                            exposure = self._dispatch_exposure_resolution_receipt_in_transaction(
                                connection, leg.permit_id
                            )
                            valid_resolution = (
                                exposure.status
                                is DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED
                                and exposure.claim == result.claim
                                and exposure.dispatch_latch_active is False
                            )
                        else:
                            valid_resolution = False
                        if not valid_resolution:
                            raise PermitInvalidError(
                                "GROUP_STATE_CONFLICT",
                                "group claim does not have a verified exact resolution receipt",
                            )
                    result = self._group_leg_claim_receipt_in_transaction(
                        row, claim_row, resolved=leg.resolved
                    )
                connection.execute("COMMIT")
                return result
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def release_group_unclaimed(
        self, scope: AccountScope, group_id: str, reason: str
    ) -> RiskGroupReservationReceipt:
        """Release only unclaimed ACTIVE members after claimed members resolve.

        Claimed permits remain occupied forever in this group ledger; this
        operation can only release never-claimed sibling permits.  An
        unresolved claim prevents even those siblings from being released.
        """

        self._validate_group_key(scope, group_id)
        if type(reason) is not str or not reason.strip():
            raise ValueError("release reason is required")
        with self._transaction() as connection:
            now = self._clock()
            self._ensure_policy_binding(connection, scope.key)
            self._expire_active(connection, now)
            rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (scope.key, group_id),
            ).fetchall()
            if not rows:
                raise RiskDeniedError("GROUP_NOT_FOUND", "group reservation does not exist")
            receipt = self._group_receipt_in_transaction(connection, scope, group_id, rows)
            if any(leg.claimed and not leg.resolved for leg in receipt.legs):
                raise RiskDeniedError(
                    "GROUP_CLAIM_UNRESOLVED",
                    "unclaimed group members cannot release while a claimed member is unresolved",
                )
            connection.execute(
                """UPDATE risk_reservations SET status = ?, reason = ?
                   WHERE scope_key = ? AND group_id = ? AND status = ?
                     AND NOT EXISTS (
                       SELECT 1 FROM risk_dispatch_claims
                       WHERE risk_dispatch_claims.permit_id = risk_reservations.permit_id
                     )""",
                (self._RELEASED, reason, scope.key, group_id, self._ACTIVE),
            )
            rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (scope.key, group_id),
            ).fetchall()
            return self._group_receipt_in_transaction(connection, scope, group_id, rows)

    def claim_group_leg(
        self,
        scope: AccountScope,
        group_id: str,
        leg_id: str,
        permit_id: str,
        actual_intent: RiskIntent,
        *,
        expected_group_fingerprint: str,
        expected_deadline_ns: int,
    ) -> RiskGroupLegClaimReceipt:
        """Claim one bounded child order under an exact persisted group leg.

        The reservation's maximum-size intent hash is never replaced.  The
        trusted mapper supplies a separate actual child intent, whose immutable
        profile must match and whose quantity and notional may not exceed the
        reserved maxima.  Its risk-intent fingerprint and normalized facts are
        stored in the same one-shot dispatch claim row.
        """

        self._validate_group_key(scope, group_id)
        if type(leg_id) is not str or not _ALLOCATION_ID.fullmatch(leg_id):
            raise PermitInvalidError("GROUP_MEMBER_MISMATCH", "group leg identity is invalid")
        if type(permit_id) is not str or not permit_id or permit_id != permit_id.strip():
            raise PermitInvalidError("GROUP_MEMBER_MISMATCH", "permit identity is invalid")
        try:
            _require_sha256(expected_group_fingerprint, "group_fingerprint")
        except ValueError as exc:
            raise PermitInvalidError(
                "GROUP_MEMBER_MISMATCH", "group fingerprint is invalid"
            ) from exc
        if type(expected_deadline_ns) is not int or expected_deadline_ns <= 0:
            raise PermitInvalidError("GROUP_MEMBER_MISMATCH", "group deadline is invalid")
        if type(actual_intent) is not RiskIntent:
            raise PermitInvalidError(
                "GROUP_INVALID_LEG", "actual child must be a mapped RiskIntent"
            )
        if actual_intent.scope != scope:
            raise PermitInvalidError(
                "GROUP_SCOPE_MISMATCH", "actual child belongs to another account"
            )

        live_gross = self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS
        with self._transaction() as connection:
            now = self._clock()
            now_ns = self._now_ns()
            self._ensure_policy_binding(connection, scope.key)
            self._expire_active(connection, now)
            rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (scope.key, group_id),
            ).fetchall()
            if not rows:
                raise PermitInvalidError("GROUP_NOT_FOUND", "group reservation does not exist")
            receipt = self._group_receipt_in_transaction(connection, scope, group_id, rows)
            if (
                receipt.group_fingerprint != expected_group_fingerprint
                or receipt.deadline_ns != expected_deadline_ns
            ):
                raise PermitInvalidError(
                    "GROUP_MEMBER_MISMATCH", "caller binding does not match the durable group"
                )
            reservation = next((row for row in rows if row["group_leg_id"] == leg_id), None)
            if reservation is None or reservation["permit_id"] != permit_id:
                raise PermitInvalidError(
                    "GROUP_MEMBER_MISMATCH", "permit does not identify this durable group leg"
                )
            self._validate_group_child_intent(reservation, actual_intent)

            existing_claim = connection.execute(
                "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if existing_claim is not None:
                self._validate_group_claim_row(reservation, existing_claim)
                if existing_claim["actual_intent_hash"] != actual_intent.fingerprint:
                    raise PermitInvalidError(
                        "GROUP_CLAIM_REPLAY_MISMATCH",
                        "group leg already claimed with a different actual child intent",
                    )
                raise PermitInvalidError(
                    "DISPATCH_CLAIM_ALREADY_ISSUED",
                    "a group dispatch claim is single-use and cannot authorize a retry",
                )

            if reservation["status"] != self._ACTIVE:
                raise PermitInvalidError(
                    "PERMIT_NOT_ACTIVE", "group leg permit is no longer active"
                )
            deadline_ns = int(reservation["group_deadline_ns"])
            expires_at_ns = int(reservation["group_expires_at_ns"])
            if now_ns >= deadline_ns:
                raise PermitInvalidError("GROUP_DEADLINE_ELAPSED", "group deadline has elapsed")
            if now_ns >= expires_at_ns:
                raise PermitInvalidError("GROUP_PERMIT_EXPIRED", "group permit TTL has elapsed")
            self._assert_group_exposure_facts(actual_intent, now_ns, invalid=True)
            self._assert_reservation_allocation_current(connection, reservation, invalid=True)
            self._assert_intent_notional_unit(actual_intent)
            self._assert_not_frozen(connection, scope)
            if live_gross:
                self._assert_live_gross_limits(connection, actual_intent, include_intent=False)

            cause_id = self._dispatch_freeze_cause(str(reservation["intent_id"]))
            connection.execute(
                """INSERT INTO risk_dispatch_claims (
                       permit_id, intent_id, intent_hash, scope_key, cause_id, claimed_at,
                       group_id, group_leg_id, group_fingerprint, group_deadline_ns,
                       reserved_intent_hash, actual_intent_hash, actual_intent_json,
                       actual_quantity, actual_notional, actual_exposure_profile_fingerprint,
                       actual_instrument_facts_observed_at_ns,
                       actual_instrument_facts_valid_until_ns
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    permit_id,
                    reservation["intent_id"],
                    reservation["intent_hash"],
                    scope.key,
                    cause_id,
                    now_ns / 1_000_000_000,
                    group_id,
                    leg_id,
                    reservation["group_fingerprint"],
                    deadline_ns,
                    reservation["intent_hash"],
                    actual_intent.fingerprint,
                    _risk_intent_json(actual_intent),
                    _decimal_text(actual_intent.quantity),
                    _decimal_text(actual_intent.notional),
                    actual_intent.exposure_profile_fingerprint,
                    actual_intent.instrument_facts_observed_at_ns,
                    actual_intent.instrument_facts_valid_until_ns,
                ),
            )
            self._freeze_in_transaction(connection, scope, cause_id, cause_id, now_ns / 1e9)

            commit_now_ns = self._now_ns()
            self._assert_group_exposure_facts(actual_intent, commit_now_ns, invalid=True)
            if commit_now_ns >= deadline_ns:
                raise PermitInvalidError(
                    "GROUP_DEADLINE_ELAPSED", "group deadline elapsed before claim committed"
                )
            if commit_now_ns >= expires_at_ns:
                raise PermitInvalidError(
                    "GROUP_PERMIT_EXPIRED", "group permit expired before claim committed"
                )
            if live_gross:
                self._assert_live_gross_limits(
                    connection,
                    actual_intent,
                    include_intent=False,
                )
            claim = connection.execute(
                "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            assert claim is not None
            permit = self._permit_from_row(reservation)
            binding = self._dispatch_claim_binding(reservation, claim)
            return RiskGroupLegClaimReceipt(
                scope=scope,
                group_id=group_id,
                group_fingerprint=str(reservation["group_fingerprint"]),
                policy_fingerprint=str(reservation["policy_hash"]),
                deadline_ns=deadline_ns,
                leg_id=leg_id,
                permit=permit,
                reserved_intent_hash=str(reservation["intent_hash"]),
                actual_intent_hash=actual_intent.fingerprint,
                exposure_profile_fingerprint=str(reservation["group_exposure_profile_fingerprint"]),
                actual_quantity=actual_intent.quantity,
                actual_notional=actual_intent.notional,
                claim=binding,
            )

    def install_gross_exposure_baseline(
        self, proof: GrossExposureBaselineProof
    ) -> VerifiedGrossExposureBaseline:
        """Install one authority-verified, fully attributed LIVE_GROSS baseline.

        A new baseline generation is accepted only when every previous
        Risk-owned exposure is zero and there are no pending increase permits
        or unresolved dispatch claims.  This prevents a refresh from silently
        double-counting or dropping filled lots that the baseline cannot yet
        reconcile by stable order identity.  When an installed baseline later
        expires, fresh order observations can still advance the order ledger,
        but new increases stay blocked and an explicit stale-baseline freeze
        remains until a clean fresh baseline is accepted.
        """
        if self._policy.exposure_accounting_mode is not ExposureAccountingMode.LIVE_GROSS:
            raise RiskDeniedError(
                "LIVE_GROSS_NOT_CONFIGURED", "policy does not enable LIVE_GROSS accounting"
            )
        if type(proof) is not GrossExposureBaselineProof:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_PROOF_REQUIRED", "a typed baseline proof is required"
            )
        if proof.notional_unit != self._policy.notional_unit:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_UNIT_MISMATCH", "baseline does not use the fixed policy unit"
            )
        now_ns = self._now_ns()
        if not self._gross_facts_are_fresh(proof.observed_at_ns, proof.valid_until_ns, now_ns):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_STALE", "account baseline is stale or not yet observed"
            )
        authority = self._gross_exposure_authority
        guard_factory = getattr(authority, "baseline_guard", None)
        if not callable(guard_factory):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_AUTHORITY_REQUIRED",
                "a current account baseline authority is required",
            )
        try:
            with guard_factory(proof) as receipt:
                if not verified_baseline_matches(proof, receipt):
                    raise RiskDeniedError(
                        "GROSS_EXPOSURE_BASELINE_REJECTED",
                        "the authority did not verify this exact baseline and writer fence",
                    )
                with self._transaction() as connection:
                    committed_at_ns = self._now_ns()
                    if not self._gross_facts_are_fresh(
                        proof.observed_at_ns, proof.valid_until_ns, committed_at_ns
                    ):
                        raise RiskDeniedError(
                            "GROSS_EXPOSURE_BASELINE_STALE",
                            "account baseline expired while acquiring the writer fence",
                        )
                    self._ensure_policy_binding(connection, proof.scope.key)
                    existing = connection.execute(
                        "SELECT * FROM risk_gross_exposure_baselines WHERE scope_key = ?",
                        (proof.scope.key,),
                    ).fetchone()
                    if existing is not None and existing["proof_sha256"] == proof.fingerprint:
                        replay_at_ns = self._now_ns()
                        if not self._gross_facts_are_fresh(
                            proof.observed_at_ns, proof.valid_until_ns, replay_at_ns
                        ):
                            raise RiskDeniedError(
                                "GROSS_EXPOSURE_BASELINE_STALE",
                                "account baseline expired before idempotent replay completed",
                            )
                        return receipt
                    latest = connection.execute(
                        "SELECT journal_revision FROM risk_gross_scope_revisions WHERE scope_key = ?",
                        (proof.scope.key,),
                    ).fetchone()
                    if latest is not None and proof.account_revision <= int(
                        latest["journal_revision"]
                    ):
                        raise RiskDeniedError(
                            "GROSS_EXPOSURE_BASELINE_REVISION_STALE",
                            "baseline revision must advance the durable account journal",
                        )
                    if existing is not None:
                        self._assert_gross_baseline_refresh_safe(connection, proof.scope)
                        if proof.account_revision <= int(existing["account_revision"]):
                            raise RiskDeniedError(
                                "GROSS_EXPOSURE_BASELINE_REVISION_STALE",
                                "replacement baseline revision must strictly increase",
                            )
                        connection.execute(
                            "DELETE FROM risk_gross_baseline_strategies WHERE scope_key = ?",
                            (proof.scope.key,),
                        )
                        connection.execute(
                            "DELETE FROM risk_gross_exposure_baselines WHERE scope_key = ?",
                            (proof.scope.key,),
                        )
                    else:
                        occupied = connection.execute(
                            """SELECT 1 FROM risk_reservations
                               WHERE scope_key = ? AND action = ? AND status IN (?, ?) LIMIT 1""",
                            (
                                proof.scope.key,
                                IntentAction.INCREASE.value,
                                self._ACTIVE,
                                self._SETTLED,
                            ),
                        ).fetchone()
                        if occupied is not None:
                            raise RiskDeniedError(
                                "GROSS_EXPOSURE_BASELINE_NOT_CLEAN",
                                "install the baseline before admitting increase intents",
                            )
                    if proof.account_gross_notional > self._policy.max_gross_occupancy_notional:
                        raise RiskDeniedError(
                            "GROSS_EXPOSURE_ACCOUNT_LIMIT_EXCEEDED",
                            "baseline already exceeds the configured account gross cap",
                        )
                    if (
                        proof.account_active_increase_intent_count
                        > self._policy.max_gross_occupancy_intent_count
                    ):
                        raise RiskDeniedError(
                            "GROSS_EXPOSURE_ACCOUNT_COUNT_EXCEEDED",
                            "baseline already exceeds the configured occupied-intent cap",
                        )
                    for strategy in proof.strategy_baselines:
                        allocation = connection.execute(
                            """SELECT * FROM risk_strategy_allocations
                               WHERE scope_key = ? AND strategy_id = ?""",
                            (proof.scope.key, strategy.strategy_id),
                        ).fetchone()
                        if (
                            allocation is None
                            or allocation["notional_unit"] != self._policy.notional_unit
                            or allocation["max_gross_occupancy_notional"] is None
                            or allocation["max_gross_occupancy_intent_count"] is None
                        ):
                            raise RiskDeniedError(
                                "GROSS_EXPOSURE_STRATEGY_BASELINE_UNBOUND",
                                "every baseline strategy requires an explicit gross allocation",
                            )
                        if strategy.gross_notional > _as_decimal(
                            allocation["max_gross_occupancy_notional"]
                        ):
                            raise RiskDeniedError(
                                "GROSS_EXPOSURE_STRATEGY_LIMIT_EXCEEDED",
                                "strategy baseline exceeds its gross allocation",
                            )
                        if strategy.active_increase_intent_count > int(
                            allocation["max_gross_occupancy_intent_count"]
                        ):
                            raise RiskDeniedError(
                                "GROSS_EXPOSURE_STRATEGY_COUNT_EXCEEDED",
                                "strategy baseline exceeds its occupied-intent cap",
                            )
                    connection.execute(
                        """INSERT INTO risk_gross_exposure_baselines (
                               scope_key, policy_hash, proof_sha256, account_revision,
                               notional_unit, account_gross_notional,
                               account_active_increase_intent_count, source_snapshot_sha256,
                               positions_sha256, open_orders_sha256, valuation_facts_sha256,
                               source_schema_version, observed_at_ns, valid_until_ns,
                               writer_owner_id, writer_fencing_token, writer_fence_sha256
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            proof.scope.key,
                            self._policy.fingerprint,
                            proof.fingerprint,
                            proof.account_revision,
                            proof.notional_unit,
                            _decimal_text(proof.account_gross_notional),
                            proof.account_active_increase_intent_count,
                            proof.source_snapshot_sha256,
                            proof.positions_sha256,
                            proof.open_orders_sha256,
                            proof.valuation_facts_sha256,
                            proof.source_schema_version,
                            proof.observed_at_ns,
                            proof.valid_until_ns,
                            proof.writer_owner_id,
                            proof.writer_fencing_token,
                            proof.writer_fence_sha256,
                        ),
                    )
                    connection.executemany(
                        """INSERT INTO risk_gross_baseline_strategies (
                               scope_key, strategy_id, gross_notional,
                               active_increase_intent_count
                           ) VALUES (?, ?, ?, ?)""",
                        [
                            (
                                proof.scope.key,
                                item.strategy_id,
                                _decimal_text(item.gross_notional),
                                item.active_increase_intent_count,
                            )
                            for item in proof.strategy_baselines
                        ],
                    )
                    connection.execute(
                        """INSERT INTO risk_gross_scope_revisions(scope_key, journal_revision)
                           VALUES (?, ?) ON CONFLICT(scope_key) DO UPDATE SET
                           journal_revision = excluded.journal_revision""",
                        (proof.scope.key, proof.account_revision),
                    )
                    commit_at_ns = self._now_ns()
                    if not self._gross_facts_are_fresh(
                        proof.observed_at_ns, proof.valid_until_ns, commit_at_ns
                    ):
                        raise RiskDeniedError(
                            "GROSS_EXPOSURE_BASELINE_STALE",
                            "account baseline expired before the Risk transaction committed",
                        )
                    connection.execute(
                        "UPDATE risk_freezes SET active = 0, updated_at = ? "
                        "WHERE scope_key = ? AND cause_id IN "
                        "('gross-exposure-baseline-stale', 'gross-exposure-over-cap') AND active = 1",
                        (self._clock(), proof.scope.key),
                    )
                    commit_at_ns = self._now_ns()
                    if not self._gross_facts_are_fresh(
                        proof.observed_at_ns, proof.valid_until_ns, commit_at_ns
                    ):
                        raise RiskDeniedError(
                            "GROSS_EXPOSURE_BASELINE_STALE",
                            "account baseline expired at the transaction commit boundary",
                        )
                return receipt
        except RiskDeniedError:
            raise
        except Exception as exc:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_REJECTED",
                "the baseline authority could not verify the account snapshot",
            ) from exc

    def apply_order_exposure_observation(
        self, proof: OrderExposureObservationProof
    ) -> OrderExposureSnapshot:
        """Atomically convert a settled ACKED reservation from order to fill occupancy.

        Incomplete/unknown facts preserve the prior gross amount and add a
        durable freeze.  A complete cancel releases only the verified leaves;
        filled gross is monotone and this API contains no client-driven reduce
        or position-netting path.  Fresh order evidence can reconcile after
        the account baseline expires, but stale baseline facts are never used
        to admit an increase or clear the durable stale-baseline freeze.  A
        filled position cannot be absorbed into a refreshed baseline here.
        """
        if self._policy.exposure_accounting_mode is not ExposureAccountingMode.LIVE_GROSS:
            raise RiskDeniedError(
                "LIVE_GROSS_NOT_CONFIGURED", "policy does not enable LIVE_GROSS accounting"
            )
        if type(proof) is not OrderExposureObservationProof:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_PROOF_REQUIRED", "a typed order observation proof is required"
            )
        if proof.dispatch_proof_sha256 is None:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_TRACKED_PROOF_REQUIRED",
                "ordinary exposure updates require the exact prior ACKED_TRACKED proof",
            )
        if (
            proof.notional_unit != self._policy.notional_unit
            or proof.valuation_unit != self._policy.notional_unit
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_UNIT_MISMATCH", "order valuation must use the fixed policy unit"
            )
        now_ns = self._now_ns()
        self._assert_order_exposure_freshness(proof, now_ns)
        authority = self._gross_exposure_authority
        guard_factory = getattr(authority, "order_observation_guard", None)
        if not callable(guard_factory):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_AUTHORITY_REQUIRED", "a current order-journal authority is required"
            )

        reservation, claim, tracked = self._tracked_order_binding(proof.permit_id)
        self._assert_order_exposure_proof_matches_binding(proof, claim, tracked)
        try:
            with guard_factory(proof, claim=claim, tracked_order=tracked) as receipt:
                if not verified_observation_matches(proof, receipt):
                    raise RiskDeniedError(
                        "ORDER_EXPOSURE_AUTHORITY_REJECTED",
                        "the authority did not verify this exact observation and current fence",
                    )
                with self._transaction() as connection:
                    committed_at_ns = self._now_ns()
                    self._assert_order_exposure_freshness(proof, committed_at_ns)
                    self._ensure_policy_binding(connection, proof.scope.key)
                    reservation, current_claim, current_tracked = self._tracked_order_binding_in(
                        connection, proof.permit_id
                    )
                    self._assert_order_exposure_proof_matches_binding(
                        proof, current_claim, current_tracked
                    )
                    baseline = self._assert_live_baseline_bound(connection, proof.scope)
                    baseline_fresh = self._gross_facts_are_fresh(
                        int(baseline["observed_at_ns"]),
                        int(baseline["valid_until_ns"]),
                        committed_at_ns,
                    )
                    baseline_fence = int(baseline["writer_fencing_token"])
                    if proof.writer_fencing_token < baseline_fence or (
                        proof.writer_fencing_token == baseline_fence
                        and (
                            proof.writer_owner_id != baseline["writer_owner_id"]
                            or proof.writer_fence_sha256 != baseline["writer_fence_sha256"]
                        )
                    ):
                        raise RiskDeniedError(
                            "ORDER_EXPOSURE_WRITER_FENCE_STALE",
                            "order observation writer fence predates the accepted account baseline",
                        )
                    prior = connection.execute(
                        "SELECT * FROM risk_order_exposures WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if prior is not None and proof.order_revision == int(prior["order_revision"]):
                        if proof.fingerprint == prior["proof_sha256"]:
                            replay_at_ns = self._now_ns()
                            self._assert_order_exposure_freshness(proof, replay_at_ns)
                            if not self._gross_facts_are_fresh(
                                int(baseline["observed_at_ns"]),
                                int(baseline["valid_until_ns"]),
                                replay_at_ns,
                            ):
                                self._freeze_in_transaction(
                                    connection,
                                    proof.scope,
                                    "gross-exposure-baseline-stale",
                                    "gross_exposure_baseline_stale",
                                    self._clock(),
                                )
                            else:
                                connection.execute(
                                    "UPDATE risk_freezes SET active = 0, updated_at = ? "
                                    "WHERE scope_key = ? AND cause_id = "
                                    "'gross-exposure-baseline-stale' AND active = 1",
                                    (self._clock(), proof.scope.key),
                                )
                            self._assert_order_exposure_freshness(proof, self._now_ns())
                            return self._order_exposure_snapshot(prior, proof.scope)
                        if (
                            proof.state is not OrderExposureState.UNKNOWN
                            and prior["state"] != OrderExposureState.UNKNOWN.value
                            and not self._same_order_revision_valuation_refresh(proof, prior)
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_REVISION_CONFLICT",
                                "one provider order revision changed order or fill facts",
                            )
                    if (
                        prior is not None
                        and proof.state is not OrderExposureState.UNKNOWN
                        and proof.order_revision < int(prior["order_revision"])
                    ):
                        raise RiskDeniedError(
                            "ORDER_EXPOSURE_REVISION_STALE", "provider order revision regressed"
                        )
                    if prior is not None:
                        if (
                            proof.state is not OrderExposureState.UNKNOWN
                            and proof.fill_watermark_revision
                            < int(prior["fill_watermark_revision"])
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_FILL_WATERMARK_REGRESSION",
                                "complete fill-history watermark regressed",
                            )
                        if prior["fill_trade_count"] is None or prior["fill_ids_sha256"] is None:
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_SCHEMA_UNPROVEN",
                                "prior row lacks durable fill identity facts",
                            )
                        previous_fill_count = int(prior["fill_trade_count"])
                        previous_fill_quantity = _as_decimal(prior["cumulative_filled_quantity"])
                        if (
                            proof.state is not OrderExposureState.UNKNOWN
                            and proof.fill_trade_count < previous_fill_count
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_FILL_HISTORY_REGRESSION",
                                "complete fill-trade count regressed",
                            )
                        if (
                            proof.state is not OrderExposureState.UNKNOWN
                            and proof.cumulative_filled_quantity == previous_fill_quantity
                        ):
                            if (
                                proof.fill_trade_count != previous_fill_count
                                or proof.fill_ids_sha256 != prior["fill_ids_sha256"]
                            ):
                                raise RiskDeniedError(
                                    "ORDER_EXPOSURE_FILL_IDENTITY_CONFLICT",
                                    "fill identities changed without a cumulative quantity increase",
                                )
                        elif proof.state is not OrderExposureState.UNKNOWN and (
                            proof.fill_trade_count <= previous_fill_count
                            or proof.fill_ids_sha256 == prior["fill_ids_sha256"]
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_FILL_HISTORY_INCOMPLETE",
                                "new cumulative fills require new immutable fill identities",
                            )
                        if (
                            proof.state is not OrderExposureState.UNKNOWN
                            and prior["state"] != OrderExposureState.UNKNOWN.value
                        ):
                            self._assert_order_contract_stable(proof, prior)
                        prior_fence = int(prior["writer_fencing_token"])
                        if proof.writer_fencing_token < prior_fence or (
                            proof.writer_fencing_token == prior_fence
                            and (
                                proof.writer_owner_id != prior["writer_owner_id"]
                                or proof.writer_fence_sha256 != prior["writer_fence_sha256"]
                            )
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_WRITER_FENCE_STALE",
                                "order observation writer fence regressed or changed without fencing",
                            )
                    if proof.state is not OrderExposureState.UNKNOWN:
                        self._assert_order_fills_extend_prior(connection, proof, prior)
                    latest = connection.execute(
                        "SELECT journal_revision FROM risk_gross_scope_revisions WHERE scope_key = ?",
                        (proof.scope.key,),
                    ).fetchone()
                    if latest is None or proof.journal_revision <= int(latest["journal_revision"]):
                        raise RiskDeniedError(
                            "ORDER_EXPOSURE_JOURNAL_REVISION_STALE",
                            "account journal revision must advance monotonically",
                        )
                    claim_row = connection.execute(
                        "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    self._assert_order_observation_identity(
                        proof, reservation, claim_row=claim_row
                    )
                    if self._dispatch_latch_active(connection, proof.scope, current_claim.cause_id):
                        raise RiskDeniedError(
                            "ORDER_EXPOSURE_DISPATCH_UNRESOLVED",
                            "ACKED tracking must resolve the dispatch latch before exposure updates",
                        )
                    snapshot = self._apply_order_exposure_in_transaction(
                        connection, proof, reservation, prior, committed_at_ns
                    )
                    if proof.state is not OrderExposureState.UNKNOWN:
                        self._persist_order_fill_facts(connection, proof)
                    connection.execute(
                        """UPDATE risk_gross_scope_revisions SET journal_revision = ?
                           WHERE scope_key = ?""",
                        (proof.journal_revision, proof.scope.key),
                    )
                    unknown_cause = self._gross_unknown_cause(proof.permit_id)
                    if proof.state is OrderExposureState.UNKNOWN:
                        self._freeze_in_transaction(
                            connection,
                            proof.scope,
                            unknown_cause,
                            "order_exposure_unknown",
                            self._clock(),
                        )
                    else:
                        connection.execute(
                            """UPDATE risk_freezes SET active = 0, updated_at = ?
                               WHERE scope_key = ? AND cause_id = ? AND active = 1""",
                            (self._clock(), proof.scope.key, unknown_cause),
                        )
                    if self._gross_scope_over_limit(connection, proof.scope):
                        self._freeze_in_transaction(
                            connection,
                            proof.scope,
                            "gross-exposure-over-cap",
                            "gross_exposure_limit_exceeded",
                            self._clock(),
                        )
                    elif baseline_fresh:
                        connection.execute(
                            "UPDATE risk_freezes SET active = 0, updated_at = ? "
                            "WHERE scope_key = ? AND cause_id = 'gross-exposure-over-cap' AND active = 1",
                            (self._clock(), proof.scope.key),
                        )
                    commit_at_ns = self._now_ns()
                    self._assert_order_exposure_freshness(proof, commit_at_ns)
                    baseline_fresh_at_commit = self._gross_facts_are_fresh(
                        int(baseline["observed_at_ns"]),
                        int(baseline["valid_until_ns"]),
                        commit_at_ns,
                    )
                    if baseline_fresh_at_commit:
                        connection.execute(
                            "UPDATE risk_freezes SET active = 0, updated_at = ? "
                            "WHERE scope_key = ? AND cause_id = 'gross-exposure-baseline-stale' "
                            "AND active = 1",
                            (self._clock(), proof.scope.key),
                        )
                    else:
                        self._freeze_in_transaction(
                            connection,
                            proof.scope,
                            "gross-exposure-baseline-stale",
                            "gross_exposure_baseline_stale",
                            self._clock(),
                        )
                    linearized_at_ns = self._now_ns()
                    self._assert_order_exposure_freshness(proof, linearized_at_ns)
                    if not self._gross_facts_are_fresh(
                        int(baseline["observed_at_ns"]),
                        int(baseline["valid_until_ns"]),
                        linearized_at_ns,
                    ):
                        self._freeze_in_transaction(
                            connection,
                            proof.scope,
                            "gross-exposure-baseline-stale",
                            "gross_exposure_baseline_stale",
                            self._clock(),
                        )
                    return snapshot
        except RiskDeniedError:
            raise
        except Exception as exc:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_AUTHORITY_REJECTED",
                "the order journal authority could not verify this observation",
            ) from exc

    def gross_exposure_snapshot(self, scope: AccountScope) -> dict[str, object]:
        """Return the local durable gross projection without provider I/O."""
        with self._connection() as connection:
            baseline = connection.execute(
                "SELECT * FROM risk_gross_exposure_baselines WHERE scope_key = ?",
                (scope.key,),
            ).fetchone()
            strategies = connection.execute(
                "SELECT strategy_id, gross_notional, active_increase_intent_count "
                "FROM risk_gross_baseline_strategies "
                "WHERE scope_key = ? ORDER BY strategy_id",
                (scope.key,),
            ).fetchall()
            rows = connection.execute(
                "SELECT * FROM risk_order_exposures WHERE scope_key = ? ORDER BY permit_id",
                (scope.key,),
            ).fetchall()
            revision = connection.execute(
                "SELECT journal_revision FROM risk_gross_scope_revisions WHERE scope_key = ?",
                (scope.key,),
            ).fetchone()
        return {
            "account_baseline_notional": (
                None if baseline is None else _as_decimal(baseline["account_gross_notional"])
            ),
            "notional_unit": None if baseline is None else str(baseline["notional_unit"]),
            "baseline_revision": None if baseline is None else int(baseline["account_revision"]),
            "baseline_active_increase_intent_count": (
                None
                if baseline is None or baseline["account_active_increase_intent_count"] is None
                else int(baseline["account_active_increase_intent_count"])
            ),
            "journal_revision": None if revision is None else int(revision["journal_revision"]),
            "strategy_baselines": {
                str(row["strategy_id"]): {
                    "gross_notional": _as_decimal(row["gross_notional"]),
                    "active_increase_intent_count": (
                        None
                        if row["active_increase_intent_count"] is None
                        else int(row["active_increase_intent_count"])
                    ),
                }
                for row in strategies
            },
            "orders": [self._order_exposure_snapshot(row, scope) for row in rows],
            "active_freeze_reasons": self.active_freeze_reasons(scope),
        }

    def validate_permit(
        self,
        permit_id: str,
        intent: Optional[RiskIntent] = None,  # noqa: UP045 -- Python 3.9 is supported.
    ) -> RiskPermit:
        """Validate a pending permit before dispatch; this is not a dispatch claim.

        Execution still needs its own durable single-use claim. This local
        transaction cannot make risk, intent and outbox writes atomic across
        independently owned connections or databases.
        """
        now = self._clock()
        live_gross = self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS
        with self._transaction() as connection:
            if live_gross:
                now = self._clock()
            row = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if row is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            if row["group_id"] is not None:
                raise PermitInvalidError(
                    "GROUP_PERMIT_REQUIRED",
                    "group permit must be validated through its explicit group operation",
                )
            permit = self._permit_from_row(row)
            if permit.policy_fingerprint != self._policy.fingerprint:
                raise PermitInvalidError("POLICY_CHANGED", "permit was issued under another policy")
            self._ensure_policy_binding(connection, permit.scope.key)
            self._expire_active(connection, now)
            row = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            assert row is not None
            permit = self._permit_from_row(row)
            if row["status"] != self._ACTIVE:
                raise PermitInvalidError("PERMIT_NOT_ACTIVE", "permit is no longer active")
            self._assert_reservation_allocation_current(connection, row, invalid=True)
            if intent is not None:
                if intent.intent_id != permit.intent_id or intent.scope != permit.scope:
                    raise PermitInvalidError(
                        "PERMIT_SCOPE_MISMATCH", "permit does not match intent scope"
                    )
                if intent.fingerprint != row["intent_hash"]:
                    raise PermitInvalidError(
                        "PERMIT_INTENT_MISMATCH", "permit does not match intent payload"
                    )
            if permit.action is IntentAction.INCREASE:
                self._assert_not_frozen(connection, permit.scope)
                if live_gross:
                    live_intent = intent or RiskIntent(
                        intent_id=permit.intent_id,
                        scope=permit.scope,
                        action=permit.action,
                        notional=permit.notional,
                        strategy_id=permit.strategy_id,
                        allocation_version=permit.allocation_version,
                        instrument=permit.instrument,
                        quantity=permit.quantity,
                        quantity_unit=permit.quantity_unit,
                        notional_unit=permit.notional_unit,
                    )
                    self._assert_live_gross_limits(
                        connection, live_intent, include_intent=False
                    )
            if live_gross and permit.action is IntentAction.INCREASE:
                final_now = self._clock()
                self._expire_active(connection, final_now)
                current = connection.execute(
                    "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
                ).fetchone()
                assert current is not None
                if current["status"] != self._ACTIVE:
                    raise PermitInvalidError("PERMIT_NOT_ACTIVE", "permit is no longer active")
                self._assert_reservation_allocation_current(connection, current, invalid=True)
                self._assert_not_frozen(connection, permit.scope)
                final_intent = intent or RiskIntent(
                    intent_id=permit.intent_id,
                    scope=permit.scope,
                    action=permit.action,
                    notional=permit.notional,
                    strategy_id=permit.strategy_id,
                    allocation_version=permit.allocation_version,
                    instrument=permit.instrument,
                    quantity=permit.quantity,
                    quantity_unit=permit.quantity_unit,
                    notional_unit=permit.notional_unit,
                )
                self._assert_live_gross_limits(
                    connection, final_intent, include_intent=False
                )
            return permit

    def claim_for_dispatch(self, permit_id: str, intent: RiskIntent) -> RiskPermit:
        """Atomically validate a permit and install its dispatch safety latch.

        ``validate_permit`` is intentionally a read/validation boundary.  It
        cannot prevent two independent processes from both validating an
        opening permit just before one of them installs a freeze.  A provider
        capable composition must instead use this method immediately before
        its dispatch port: one ``BEGIN IMMEDIATE`` transaction validates the
        permit, rejects any already-active account freeze for an increase, and
        persists ``dispatch-inflight:<intent_id>`` before the provider may be
        called.

        A dispatch claim is single-use.  Repeating it is rejected even when
        the arguments match, so callers cannot mistake an idempotent claim
        result for permission to make another provider attempt.  The latch is
        not cleared when a caller later releases a permit: release only proves
        a local admission failure, while an independent reconciliation path
        owns any safety-latch resolution.
        """

        if not isinstance(intent, RiskIntent):
            raise ValueError("intent must be a RiskIntent")
        cause_id = self._dispatch_freeze_cause(intent.intent_id)
        now = self._clock()
        live_gross = self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS
        with self._transaction() as connection:
            self._assert_current_instrument_facts(intent, invalid=True)
            if live_gross:
                now = self._clock()
            reservation_row = connection.execute(
                "SELECT group_id FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if reservation_row is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            if reservation_row["group_id"] is not None:
                raise PermitInvalidError(
                    "GROUP_CLAIM_REQUIRED",
                    "group permit can only claim through claim_group_leg",
                )
            self._ensure_policy_binding(connection, intent.scope.key)
            self._expire_active(connection, now)
            claim = connection.execute(
                "SELECT intent_id, intent_hash, scope_key, cause_id "
                "FROM risk_dispatch_claims WHERE permit_id = ?",
                (permit_id,),
            ).fetchone()
            if claim is not None:
                if (
                    claim["intent_id"] != intent.intent_id
                    or claim["intent_hash"] != intent.fingerprint
                    or claim["scope_key"] != intent.scope.key
                    or claim["cause_id"] != cause_id
                ):
                    raise PermitInvalidError(
                        "DISPATCH_CLAIM_CONFLICT",
                        "permit is already bound to another dispatch claim",
                    )
                raise PermitInvalidError(
                    "DISPATCH_CLAIM_ALREADY_ISSUED",
                    "a dispatch claim is single-use and cannot authorize a retry",
                )

            permit = self._validate_active_permit(connection, permit_id, intent)

            if permit.action is IntentAction.INCREASE:
                self._assert_not_frozen(connection, permit.scope)
                if live_gross:
                    self._assert_live_gross_limits(connection, intent, include_intent=False)
                    # The permit, baseline, and prior order valuations may
                    # expire while this transaction is doing its checks.  Run
                    # the authoritative freshness check again immediately
                    # before installing the dispatch claim.
                    now = self._clock()
                    self._expire_active(connection, now)
                    permit = self._validate_active_permit(connection, permit_id, intent)
                    if permit.expires_at <= now:
                        raise PermitInvalidError(
                            "PERMIT_NOT_ACTIVE", "permit expired before dispatch claim"
                        )
                    self._assert_not_frozen(connection, permit.scope)
                    self._assert_live_gross_limits(connection, intent, include_intent=False)
            self._assert_current_instrument_facts(intent, invalid=True)
            connection.execute(
                """
                INSERT INTO risk_dispatch_claims (
                    permit_id, intent_id, intent_hash, scope_key, cause_id, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    permit_id,
                    intent.intent_id,
                    intent.fingerprint,
                    intent.scope.key,
                    cause_id,
                    now,
                ),
            )
            self._freeze_in_transaction(connection, permit.scope, cause_id, cause_id, now)
            self._assert_current_instrument_facts(intent, invalid=True)
            if live_gross and permit.action is IntentAction.INCREASE:
                commit_now = self._clock()
                if permit.expires_at <= commit_now:
                    raise PermitInvalidError(
                        "PERMIT_NOT_ACTIVE", "permit expired before dispatch claim committed"
                    )
                self._assert_live_gross_limits(connection, intent, include_intent=False)
            # The LIVE_GROSS scan can take long enough for the instrument or
            # FX interval to expire.  Keep this check at the end of the write
            # transaction so an expired fact cannot authorize a committed
            # dispatch claim.
            self._assert_current_instrument_facts(intent, invalid=True)
            return permit

    def dispatch_claim_binding(self, permit_id: str) -> DispatchClaimBinding:
        """Return the exact durable claim identity while its latch is active.

        This read-only value helps an injected journal authority bind its
        evidence to the claim.  It grants no dispatch permission and becomes
        unusable for resolution if the claim, permit, or active latch differs.
        """
        with self._connection() as connection:
            reservation = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if reservation is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            policy_error = self._policy_binding_read_error(
                connection, str(reservation["scope_key"])
            )
            if policy_error is not None:
                raise PermitInvalidError(
                    policy_error, "risk database is bound to another or unknown policy"
                )
            claim = self._require_dispatch_claim(connection, reservation)
            return self._dispatch_claim_binding(reservation, claim)

    def dispatch_resolution_receipt(self, permit_id: str) -> DispatchResolutionReceipt:
        """Read the exact durable dispatch state from one SQLite snapshot.

        This API is for recovery reconciliation only. It never changes Risk
        state and returns a resolved receipt only for an exact settled
        INCREASE whose immutable result is ``ACKED_TRACKED``. A terminal
        no-fill resolution, an absent permit, or any mixed/corrupt latch,
        claim, reservation, resolution, or recovery-audit state is not an ACK.
        """
        if type(permit_id) is not str or not permit_id or permit_id != permit_id.strip():
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                receipt = self._dispatch_resolution_receipt_in_transaction(connection, permit_id)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
                return receipt

    def _dispatch_resolution_receipt_in_transaction(
        self, connection: sqlite3.Connection, permit_id: str
    ) -> DispatchResolutionReceipt:
        reservation = connection.execute(
            "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if reservation is None:
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
        policy_error = self._policy_binding_read_error(connection, str(reservation["scope_key"]))
        if policy_error is not None:
            raise PermitInvalidError(
                policy_error, "risk database is bound to another or unknown policy"
            )
        if reservation["action"] != IntentAction.INCREASE.value:
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "a tracked-order receipt requires an INCREASE reservation",
            )
        if reservation["status"] != self._SETTLED:
            raise PermitInvalidError(
                "PERMIT_NOT_SETTLED", "dispatch receipt requires a settled reservation"
            )
        claim_row = connection.execute(
            "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if claim_row is None:
            raise PermitInvalidError(
                "PERMIT_NOT_DISPATCH_CLAIMED", "dispatch receipt requires an exact durable claim"
            )
        expected_cause = self._dispatch_freeze_cause(str(reservation["intent_id"]))
        if (
            claim_row["intent_id"] != reservation["intent_id"]
            or claim_row["intent_hash"] != reservation["intent_hash"]
            or claim_row["scope_key"] != reservation["scope_key"]
            or claim_row["cause_id"] != expected_cause
        ):
            raise PermitInvalidError(
                "DISPATCH_CLAIM_CONFLICT",
                "the durable dispatch claim no longer matches its reservation",
            )
        try:
            claim = self._dispatch_claim_binding(reservation, claim_row)
        except (TypeError, ValueError) as exc:
            raise PermitInvalidError(
                "DISPATCH_CLAIM_CONFLICT", "the durable dispatch claim is invalid"
            ) from exc
        freeze = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
            (reservation["scope_key"], expected_cause),
        ).fetchone()
        if freeze is None or type(freeze["active"]) is not int or freeze["active"] not in (0, 1):
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT", "the exact dispatch latch is missing"
            )
        resolution = connection.execute(
            "SELECT * FROM risk_dispatch_resolutions WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        audit = connection.execute(
            "SELECT * FROM risk_dispatch_recovery_audits WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        other_freezes = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id <> ?",
            (reservation["scope_key"], expected_cause),
        ).fetchall()
        if any(
            type(row["active"]) is not int or row["active"] not in (0, 1) for row in other_freezes
        ):
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "another account freeze has an invalid active state",
            )
        has_other_active_freezes = any(row["active"] == 1 for row in other_freezes)

        if resolution is None:
            if audit is not None or freeze["active"] != 1:
                raise PermitInvalidError(
                    "DISPATCH_RESOLUTION_STATE_CONFLICT",
                    "an unresolved claim must retain its exact active dispatch latch",
                )
            return DispatchResolutionReceipt(
                status=DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE,
                claim=claim,
                dispatch_latch_active=True,
                has_other_active_freezes=has_other_active_freezes,
            )

        if freeze["active"] != 0 or resolution["proof_kind"] != "ACKED_TRACKED":
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "only an exact ACKED_TRACKED resolution with an inactive dispatch latch is recoverable",
            )
        if (
            resolution["scope_key"] != reservation["scope_key"]
            or resolution["intent_id"] != reservation["intent_id"]
            or resolution["intent_hash"] != reservation["intent_hash"]
            or resolution["cause_id"] != expected_cause
            or resolution["claim_digest"] != claim.claim_digest
        ):
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "the stored resolution no longer matches its exact durable claim",
            )
        try:
            proof = DispatchTrackedOrderProof(
                scope=claim.scope,
                permit_id=permit_id,
                intent_id=resolution["intent_id"],
                intent_hash=resolution["intent_hash"],
                cause_id=resolution["cause_id"],
                claim_digest=resolution["claim_digest"],
                evidence_class=DispatchEvidenceClass(resolution["evidence_class"]),
                dispatch_attempt_count=resolution["dispatch_attempt_count"],
                journal_revision=resolution["journal_revision"],
                journal_record_sha256=resolution["journal_record_sha256"],
                reconciliation_evidence_sha256=resolution["reconciliation_evidence_sha256"],
                writer_fence_sha256=resolution["writer_fence_sha256"],
                provider_order_id=resolution["provider_order_id"],
                accepted_request_sha256=resolution["accepted_request_sha256"],
                exposure_reservation_id=resolution["exposure_reservation_id"],
                exposure_reservation_sha256=resolution["exposure_reservation_sha256"],
                filled_quantity=resolution["filled_quantity"],
                trade_count=resolution["trade_count"],
            )
        except (TypeError, ValueError) as exc:
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "the stored tracked-order proof is invalid",
            ) from exc
        try:
            self._assert_dispatch_proof_matches_claim(proof, claim)
        except PermitInvalidError as exc:
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "the stored tracked-order proof does not match the exact claim",
            ) from exc

        recovery_attestation = None
        if audit is not None:
            recovery_attestation = self._read_dispatch_recovery_audit(
                audit, claim=claim, proof=proof
            )
        return DispatchResolutionReceipt(
            status=DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED,
            claim=claim,
            proof=proof,
            proof_sha256=proof.fingerprint,
            dispatch_latch_active=False,
            has_other_active_freezes=has_other_active_freezes,
            recovery_attestation=recovery_attestation,
        )

    def dispatch_terminal_resolution_receipt(
        self, permit_id: str
    ) -> DispatchTerminalResolutionReceipt:
        """Read one exact no-fill terminal result without changing Risk state."""

        if type(permit_id) is not str or not permit_id or permit_id != permit_id.strip():
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
        with self._connection() as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            try:
                receipt = self._dispatch_terminal_resolution_receipt_in_transaction(
                    connection, permit_id
                )
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
                return receipt

    def _dispatch_terminal_resolution_receipt_in_transaction(
        self, connection: sqlite3.Connection, permit_id: str
    ) -> DispatchTerminalResolutionReceipt:
        reservation = connection.execute(
            "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if reservation is None:
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
        policy_error = self._policy_binding_read_error(connection, str(reservation["scope_key"]))
        if policy_error is not None:
            raise PermitInvalidError(
                policy_error, "risk database is bound to another or unknown policy"
            )
        if reservation["action"] != IntentAction.INCREASE.value:
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT",
                "no-fill terminal evidence requires an INCREASE reservation",
            )
        claim_row = connection.execute(
            "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if claim_row is None:
            raise PermitInvalidError(
                "PERMIT_NOT_DISPATCH_CLAIMED", "terminal receipt requires an exact durable claim"
            )
        expected_cause = self._dispatch_freeze_cause(str(reservation["intent_id"]))
        if (
            claim_row["intent_id"] != reservation["intent_id"]
            or claim_row["intent_hash"] != reservation["intent_hash"]
            or claim_row["scope_key"] != reservation["scope_key"]
            or claim_row["cause_id"] != expected_cause
        ):
            raise PermitInvalidError(
                "DISPATCH_CLAIM_CONFLICT", "durable claim does not match its reservation"
            )
        try:
            claim = self._dispatch_claim_binding(reservation, claim_row)
            if reservation["group_id"] is not None:
                self._validate_group_claim_row(reservation, claim_row)
            elif claim_row["group_id"] is not None:
                raise ValueError("singleton claim carries group identity")
        except (KeyError, TypeError, ValueError, RiskDeniedError, PermitInvalidError) as exc:
            raise PermitInvalidError(
                "DISPATCH_CLAIM_CONFLICT", "durable claim cannot be reconstructed"
            ) from exc

        freeze = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
            (reservation["scope_key"], expected_cause),
        ).fetchone()
        if freeze is None or type(freeze["active"]) is not int or freeze["active"] not in (0, 1):
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT", "exact dispatch latch is missing or invalid"
            )
        resolution = connection.execute(
            "SELECT * FROM risk_dispatch_resolutions WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        audit = connection.execute(
            "SELECT permit_id FROM risk_dispatch_recovery_audits WHERE permit_id = ?",
            (permit_id,),
        ).fetchone()
        exposure = connection.execute(
            "SELECT permit_id FROM risk_order_exposures WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        other_freezes = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id <> ?",
            (reservation["scope_key"], expected_cause),
        ).fetchall()
        if any(
            type(row["active"]) is not int or row["active"] not in (0, 1)
            for row in other_freezes
        ):
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT", "another account freeze state is invalid"
            )
        has_other_active_freezes = any(row["active"] == 1 for row in other_freezes)

        def group_claim_receipt(
            *, resolved: bool
        ) -> Optional[RiskGroupLegClaimReceipt]:  # noqa: UP045 -- Python 3.9 is supported.
            if reservation["group_id"] is None:
                return None
            rows = connection.execute(
                """SELECT * FROM risk_reservations
                   WHERE scope_key = ? AND group_id = ? ORDER BY group_leg_id""",
                (reservation["scope_key"], reservation["group_id"]),
            ).fetchall()
            group = self._group_receipt_in_transaction(
                connection,
                claim.scope,
                str(reservation["group_id"]),
                rows,
            )
            leg = next(
                (item for item in group.legs if item.leg_id == reservation["group_leg_id"]),
                None,
            )
            if (
                leg is None
                or leg.permit_id != permit_id
                or not leg.claimed
                or leg.resolved is not resolved
            ):
                raise PermitInvalidError(
                    "GROUP_STATE_CONFLICT", "group vector does not confirm the exact claim state"
                )
            return self._group_leg_claim_receipt_in_transaction(
                reservation, claim_row, resolved=resolved
            )

        if resolution is None:
            if (
                reservation["status"] != self._SETTLED
                or audit is not None
                or exposure is not None
                or freeze["active"] != 1
            ):
                raise PermitInvalidError(
                    "DISPATCH_TERMINAL_STATE_CONFLICT",
                    "unresolved terminal receipt requires a settled claim and active latch",
                )
            group_claim = group_claim_receipt(resolved=False)
            return DispatchTerminalResolutionReceipt(
                status=DispatchTerminalResolutionReceiptStatus.UNRESOLVED_ACTIVE,
                claim=claim,
                group_claim=group_claim,
                dispatch_latch_active=True,
                has_other_active_freezes=has_other_active_freezes,
            )

        if (
            reservation["status"] != self._NO_FILL
            or reservation["reason"] != resolution["proof_kind"]
            or audit is not None
            or exposure is not None
            or freeze["active"] != 0
            or resolution["proof_kind"] not in {item.value for item in DispatchTerminalState}
            or resolution["scope_key"] != reservation["scope_key"]
            or resolution["intent_id"] != reservation["intent_id"]
            or resolution["intent_hash"] != reservation["intent_hash"]
            or resolution["cause_id"] != expected_cause
            or resolution["claim_digest"] != claim.claim_digest
        ):
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT",
                "stored resolution, terminal reservation, claim, or latch is inconsistent",
            )
        if any(
            resolution[name] is not None
            for name in (
                "provider_order_id",
                "accepted_request_sha256",
                "exposure_reservation_id",
                "exposure_reservation_sha256",
                "exposure_proof_sha256",
                "terminal_state",
                "requested_quantity",
                "cumulative_filled_quantity",
                "exposure_fill_trade_count",
                "exposure_fill_ids_sha256",
                "exposure_order_revision",
            )
        ):
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT",
                "no-fill resolution contains conflicting exposure fields",
            )
        if (
            type(resolution["resolution_proof_sha256"]) is not str
            or type(resolution["fee_record_count"]) is not int
            or type(resolution["fee_history_complete"]) is not int
            or resolution["fee_record_count"] != 0
            or resolution["fee_history_complete"] != 1
        ):
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT",
                "stored no-fill resolution lacks explicit complete zero-fee proof facts",
            )
        try:
            proof = DispatchTerminalProof(
                scope=claim.scope,
                permit_id=permit_id,
                intent_id=str(resolution["intent_id"]),
                intent_hash=str(resolution["intent_hash"]),
                cause_id=str(resolution["cause_id"]),
                claim_digest=str(resolution["claim_digest"]),
                evidence_class=DispatchEvidenceClass(str(resolution["evidence_class"])),
                terminal_state=DispatchTerminalState(str(resolution["proof_kind"])),
                dispatch_attempt_count=int(resolution["dispatch_attempt_count"]),
                journal_revision=int(resolution["journal_revision"]),
                journal_record_sha256=str(resolution["journal_record_sha256"]),
                reconciliation_evidence_sha256=str(
                    resolution["reconciliation_evidence_sha256"]
                ),
                writer_fence_sha256=str(resolution["writer_fence_sha256"]),
                fee_record_count=resolution["fee_record_count"],
                fee_history_complete=resolution["fee_history_complete"] == 1,
                filled_quantity=int(resolution["filled_quantity"]),
                trade_count=int(resolution["trade_count"]),
            )
            self._assert_dispatch_proof_matches_claim(proof, claim)
            stored_proof_sha256 = _require_sha256(
                resolution["resolution_proof_sha256"], "resolution_proof_sha256"
            )
            if stored_proof_sha256 != proof.fingerprint:
                raise ValueError("stored proof fingerprint differs")
        except (KeyError, TypeError, ValueError, OverflowError, PermitInvalidError) as exc:
            raise PermitInvalidError(
                "DISPATCH_TERMINAL_STATE_CONFLICT", "stored no-fill proof is invalid"
            ) from exc
        group_claim = group_claim_receipt(resolved=True)
        return DispatchTerminalResolutionReceipt(
            status=DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED,
            claim=claim,
            proof=proof,
            proof_sha256=proof.fingerprint,
            group_claim=group_claim,
            dispatch_latch_active=False,
            has_other_active_freezes=has_other_active_freezes,
            fee_record_count=int(resolution["fee_record_count"]),
            fee_history_complete=True,
        )

    @staticmethod
    def _read_dispatch_recovery_audit(
        audit: sqlite3.Row,
        *,
        claim: DispatchClaimBinding,
        proof: DispatchTrackedOrderProof,
    ) -> DispatchRecoveryAttestation:
        try:
            payload_json = audit["attestation_json"]
            payload = json.loads(payload_json)
            if type(payload) is not dict or set(payload) != (
                set(DispatchRecoveryAttestation.__dataclass_fields__) | {"schema"}
            ):
                raise ValueError("unexpected recovery audit fields")
            if payload["schema"] != "bt-api-risk-dispatch-recovery-attestation-v1":
                raise ValueError("unknown recovery audit schema")
            scope_payload = payload["scope"]
            if type(scope_payload) is not dict or set(scope_payload) != {
                "provider",
                "account_id",
                "environment",
            }:
                raise ValueError("invalid recovery audit scope")
            if any(type(value) is not str for value in scope_payload.values()):
                raise ValueError("invalid recovery audit scope values")
            fields = {
                name: value for name, value in payload.items() if name not in {"schema", "scope"}
            }
            attestation = DispatchRecoveryAttestation(scope=AccountScope(**scope_payload), **fields)
            if _canonical_json(_dispatch_recovery_attestation_payload(attestation)) != payload_json:
                raise ValueError("recovery audit payload is not canonical")
            if (
                audit["permit_id"] != claim.permit_id
                or audit["scope_key"] != claim.scope.key
                or audit["proof_sha256"] != proof.fingerprint
                or audit["attestation_sha256"] != attestation.fingerprint
                or audit["recovery_event_id"] != attestation.recovery_event_id
                or audit["recovery_event_revision"] != attestation.recovery_event_revision
                or audit["recovery_event_sha256"] != attestation.recovery_event_sha256
                or audit["original_writer_owner_id"] != attestation.original_writer_owner_id
                or audit["original_writer_fencing_token"]
                != attestation.original_writer_fencing_token
                or audit["original_writer_fence_sha256"] != attestation.original_writer_fence_sha256
                or audit["current_writer_owner_id"] != attestation.current_writer_owner_id
                or audit["current_writer_fencing_token"] != attestation.current_writer_fencing_token
                or audit["current_writer_fence_sha256"] != attestation.current_writer_fence_sha256
                or not _recovery_attestation_matches_proof(attestation, proof)
                or attestation.scope != claim.scope
                or attestation.permit_id != claim.permit_id
                or attestation.intent_id != claim.intent_id
                or attestation.intent_hash != claim.intent_hash
                or attestation.cause_id != claim.cause_id
                or attestation.claim_digest != claim.claim_digest
            ):
                raise ValueError("recovery audit does not match its resolution")
            return attestation
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_STATE_CONFLICT",
                "the immutable dispatch recovery audit is invalid",
            ) from exc

    def settle(self, permit_id: str) -> RiskPermit:
        """Consume a dispatch-claimed permit after its outcome is durably known."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if row is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            self._ensure_policy_binding(connection, str(row["scope_key"]))
            self._require_dispatch_claim(connection, row)
            if row["status"] != self._ACTIVE:
                raise PermitInvalidError("PERMIT_NOT_ACTIVE", "only an active permit can settle")
            connection.execute(
                "UPDATE risk_reservations SET status = ? WHERE permit_id = ?",
                (self._SETTLED, permit_id),
            )
            return self._permit_from_row(row)

    def ensure_settled(self, permit_id: str) -> RiskPermit:
        """Atomically prove a dispatch-claimed permit is settled.

        A managed runtime can crash after its execution record becomes known
        but before the separate risk database records ``settled``.  Repeating
        a normal :meth:`settle` would mistake an already-settled permit for a
        failure, while allowing an expired or released permit would hide a
        risk-accounting gap.  This recovery-only primitive accepts exactly an
        active or already settled reservation; every other status remains a
        fail-closed error. It also requires the original active dispatch latch;
        settling the reservation does not clear that latch. This package does
        not yet expose an evidence-bound reconciliation operation.
        """

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if row is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            self._ensure_policy_binding(connection, str(row["scope_key"]))
            self._require_dispatch_claim(connection, row)
            status = str(row["status"])
            if status == self._ACTIVE:
                connection.execute(
                    "UPDATE risk_reservations SET status = ? WHERE permit_id = ?",
                    (self._SETTLED, permit_id),
                )
                return self._permit_from_row(row)
            if status == self._SETTLED:
                return self._permit_from_row(row)
            raise PermitInvalidError(
                "PERMIT_NOT_SETTLEABLE",
                "only an active or settled permit can prove recovery settlement",
            )

    def release(self, permit_id: str, reason: str) -> None:
        """Release a reservation that was proven not to have been dispatched."""
        if not reason.strip():
            raise ValueError("release reason is required")
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT scope_key, status,
                    EXISTS (
                        SELECT 1 FROM risk_dispatch_claims
                        WHERE risk_dispatch_claims.permit_id = risk_reservations.permit_id
                    ) AS dispatch_claimed
                FROM risk_reservations WHERE permit_id = ?
                """,
                (permit_id,),
            ).fetchone()
            if row is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            self._ensure_policy_binding(connection, str(row["scope_key"]))
            group_row = connection.execute(
                "SELECT group_id FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            assert group_row is not None
            if group_row["group_id"] is not None:
                raise PermitInvalidError(
                    "GROUP_RELEASE_REQUIRED",
                    "group permit release must be resolved through release_group_unclaimed",
                )
            if row["dispatch_claimed"]:
                raise PermitInvalidError(
                    "PERMIT_DISPATCH_CLAIMED",
                    "a dispatch-claimed permit cannot be released",
                )
            if row["status"] != self._ACTIVE:
                raise PermitInvalidError("PERMIT_NOT_ACTIVE", "only an active permit can release")
            connection.execute(
                "UPDATE risk_reservations SET status = ?, reason = ? WHERE permit_id = ?",
                (self._RELEASED, reason, permit_id),
            )

    def freeze(self, scope: AccountScope, cause_id: str, reason: str) -> None:
        """Persist one independent freeze cause for a scope."""
        if not cause_id.strip() or not reason.strip():
            raise ValueError("freeze cause_id and reason are required")
        if self._is_dispatch_freeze_cause(cause_id):
            raise ValueError("dispatch-inflight freeze causes are reserved for dispatch claims")
        with self._transaction() as connection:
            self._ensure_policy_binding(connection, scope.key)
            self._freeze_in_transaction(connection, scope, cause_id, reason, self._clock())

    def activate_freeze_once(
        self, scope: AccountScope, cause_id: str, reason: str
    ) -> FreezeActivationResult:
        """Apply one exact cause once and report other active causes atomically.

        This API never reactivates a row on its own retries.  The legacy
        :meth:`freeze` method retains its existing explicit-upsert behavior.
        """
        return _activate_freeze_once(self, scope, cause_id, reason)

    def resolve_freeze(self, scope: AccountScope, cause_id: str) -> None:
        """Resolve an ordinary freeze; dispatch latches need reconciliation."""
        if self._is_dispatch_freeze_cause(cause_id):
            raise PermitInvalidError(
                "DISPATCH_FREEZE_REQUIRES_RECONCILIATION",
                "a dispatch-inflight freeze cannot be cleared by the generic resolver",
            )
        with self._transaction() as connection:
            self._ensure_policy_binding(connection, scope.key)
            connection.execute(
                """
                UPDATE risk_freezes SET active = 0, updated_at = ?
                WHERE scope_key = ? AND cause_id = ?
                """,
                (self._clock(), scope.key, cause_id),
            )

    def resolve_freeze_exact(self, scope: AccountScope, cause_id: str) -> FreezeResolution:
        """Resolve one exact ordinary cause and report remaining scope freezes.

        Unlike :meth:`resolve_freeze`, this method distinguishes an active
        cause, an existing already-resolved cause, and a cause that was never
        recorded. The transition and remaining-freeze check share one write
        transaction, so callers can reconcile a lost acknowledgement without
        inferring identity from a reason string or a separate read.
        Dispatch-inflight causes remain reserved for typed journal proofs.
        """
        if not isinstance(scope, AccountScope):
            raise ValueError("scope must be an AccountScope")
        if not isinstance(cause_id, str) or not cause_id.strip():
            raise ValueError("cause_id is required")
        if self._is_dispatch_freeze_cause(cause_id):
            raise PermitInvalidError(
                "DISPATCH_FREEZE_REQUIRES_RECONCILIATION",
                "a dispatch-inflight freeze cannot be cleared by the generic resolver",
            )

        with self._transaction() as connection:
            self._ensure_policy_binding(connection, scope.key)
            row = connection.execute(
                "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
                (scope.key, cause_id),
            ).fetchone()
            if row is None:
                status = FreezeResolutionStatus.NOT_FOUND
            elif row["active"] == 1:
                updated = connection.execute(
                    """UPDATE risk_freezes SET active = 0, updated_at = ?
                       WHERE scope_key = ? AND cause_id = ? AND active = 1""",
                    (self._clock(), scope.key, cause_id),
                )
                if updated.rowcount != 1:
                    raise RiskGateError("exact freeze resolution did not update one row")
                status = FreezeResolutionStatus.CLEARED
            elif row["active"] == 0:
                status = FreezeResolutionStatus.ALREADY_RESOLVED
            else:
                raise RiskGateError("persisted freeze active state is invalid")

            other_active = connection.execute(
                """SELECT 1 FROM risk_freezes
                   WHERE scope_key = ? AND cause_id <> ? AND active = 1 LIMIT 1""",
                (scope.key, cause_id),
            ).fetchone()
            return FreezeResolution(
                status=status,
                has_other_active_freezes=other_active is not None,
            )

    def latch_dispatch_identity_conflict(self, permit_id: str) -> bool:
        """Keep one exact dispatch latch active but stop it gating new increases.

        A definitive provider identity conflict (a reused trade or provider
        order identity, an unmapped legacy fill, or a contradicting declared
        scope mode) leaves the dispatched order uncreditable while its claim
        latch stays valid evidence.  Such a conflict is accounted, not a
        pending unknown outcome, so it must not wedge the account scope for
        later risk-increasing intents.  This converts only the exact active
        latch of one dispatch claim into the non-blocking state and never
        writes a resolution row.
        """
        if type(permit_id) is not str or not permit_id or permit_id != permit_id.strip():
            return False
        with self._transaction() as connection:
            claim = connection.execute(
                "SELECT scope_key, cause_id FROM risk_dispatch_claims WHERE permit_id = ?",
                (permit_id,),
            ).fetchone()
            if claim is None:
                return False
            self._ensure_policy_binding(connection, str(claim["scope_key"]))
            updated = connection.execute(
                """
                UPDATE risk_freezes SET blocking = 0, updated_at = ?
                WHERE scope_key = ? AND cause_id = ? AND active = 1 AND blocking = 1
                """,
                (self._clock(), str(claim["scope_key"]), str(claim["cause_id"])),
            )
            return updated.rowcount == 1

    @staticmethod
    def _assert_cancel_dispatch_proof_matches_claim(
        proof: CancelDispatchResolutionProof, claim: DispatchClaimBinding
    ) -> None:
        expected_intent_hash = _sha256(
            _canonical_json(
                {
                    "action": IntentAction.CANCEL.value,
                    "notional": "0",
                    "payload_fingerprint": proof.cancel_intent_fingerprint,
                    "scope": proof.scope.key,
                }
            )
        )
        if (
            proof.scope != claim.scope
            or proof.permit_id != claim.permit_id
            or proof.intent_id != claim.intent_id
            or proof.intent_hash != claim.intent_hash
            or proof.cause_id != claim.cause_id
            or proof.claim_digest != claim.claim_digest
            or proof.intent_hash != expected_intent_hash
        ):
            raise PermitInvalidError(
                "CANCEL_DISPATCH_RESOLUTION_PROOF_SCOPE_MISMATCH",
                "cancellation proof does not match its exact risk claim",
            )

    @staticmethod
    def _cancel_dispatch_resolution_row_matches_proof(
        row: sqlite3.Row, proof: CancelDispatchResolutionProof
    ) -> bool:
        expected: dict[str, object] = {
            "permit_id": proof.permit_id,
            "scope_key": proof.scope.key,
            "intent_id": proof.intent_id,
            "intent_hash": proof.intent_hash,
            "cause_id": proof.cause_id,
            "claim_digest": proof.claim_digest,
            "cancel_intent_fingerprint": proof.cancel_intent_fingerprint,
            "execution_scope_key": proof.execution_scope_key,
            "cancel_id": proof.cancel_id,
            "target_intent_id": proof.target_intent_id,
            "provider_order_id": proof.provider_order_id,
            "terminal_state": proof.terminal_state.value,
            "evidence_class": proof.evidence_class.value,
            "dispatch_attempt_count": proof.dispatch_attempt_count,
            "cancel_event_id": proof.cancel_event_id,
            "cancel_event_sequence": proof.cancel_event_sequence,
            "cancel_event_sha256": proof.cancel_event_sha256,
            "cancel_source": proof.cancel_source.value,
            "source_evidence_sha256": proof.source_evidence_sha256,
            "target_postcondition": proof.target_postcondition.value,
            "target_state": proof.target_state,
            "target_filled_quantity": proof.target_filled_quantity,
            "target_updated_at_ns": proof.target_updated_at_ns,
            "target_record_sha256": proof.target_record_sha256,
            "target_event_id": proof.target_event_id,
            "target_event_sequence": proof.target_event_sequence,
            "target_event_type": proof.target_event_type,
            "target_event_sha256": proof.target_event_sha256,
            "writer_owner_id": proof.writer_owner_id,
            "writer_fencing_token": proof.writer_fencing_token,
            "writer_fence_sha256": proof.writer_fence_sha256,
            "proof_sha256": proof.fingerprint,
        }
        return all(row[name] == value for name, value in expected.items())

    def _load_cancel_dispatch_claim_binding(
        self, proof: CancelDispatchResolutionProof
    ) -> DispatchClaimBinding | None:
        with self._connection() as connection:
            prior = connection.execute(
                "SELECT * FROM risk_cancel_dispatch_resolutions WHERE permit_id = ?",
                (proof.permit_id,),
            ).fetchone()
            if prior is not None:
                if not self._cancel_dispatch_resolution_row_matches_proof(prior, proof):
                    raise PermitInvalidError(
                        "CANCEL_DISPATCH_ALREADY_RESOLVED",
                        "this cancellation claim has another immutable resolution",
                    )
                reservation = connection.execute(
                    "SELECT * FROM risk_reservations WHERE permit_id = ?",
                    (proof.permit_id,),
                ).fetchone()
                freeze = connection.execute(
                    "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
                    (proof.scope.key, proof.cause_id),
                ).fetchone()
                if (
                    reservation is None
                    or reservation["status"] != self._SETTLED
                    or reservation["scope_key"] != proof.scope.key
                    or (freeze is not None and int(freeze["active"]) == 1)
                ):
                    raise PermitInvalidError(
                        "CANCEL_DISPATCH_RESOLUTION_INCONSISTENT",
                        "stored cancellation resolution conflicts with current risk state",
                    )
                if reservation["action"] != IntentAction.CANCEL.value or _as_decimal(
                    connection.execute(
                        "SELECT notional FROM risk_reservations WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()["notional"]
                ) != Decimal("0"):
                    raise PermitInvalidError(
                        "CANCEL_DISPATCH_RESOLUTION_NOT_CANCEL",
                        "stored cancellation resolution is not bound to a zero-notional cancel",
                    )
                reservation_row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE permit_id = ?",
                    (proof.permit_id,),
                ).fetchone()
                claim_row = connection.execute(
                    "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?",
                    (proof.permit_id,),
                ).fetchone()
                if reservation_row is None or claim_row is None:
                    raise PermitInvalidError(
                        "CANCEL_DISPATCH_RESOLUTION_INCONSISTENT",
                        "stored cancellation resolution has no matching durable claim",
                    )
                binding = self._dispatch_claim_binding(reservation_row, claim_row)
                self._assert_cancel_dispatch_proof_matches_claim(proof, binding)
                return None

            reservation = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?",
                (proof.permit_id,),
            ).fetchone()
            if reservation is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            policy_error = self._policy_binding_read_error(
                connection, str(reservation["scope_key"])
            )
            if policy_error is not None:
                raise PermitInvalidError(
                    policy_error, "risk database is bound to another or unknown policy"
                )
            if (
                reservation["action"] != IntentAction.CANCEL.value
                or _as_decimal(reservation["notional"]) != Decimal("0")
                or reservation["scope_key"] != proof.scope.key
            ):
                raise PermitInvalidError(
                    "CANCEL_DISPATCH_RESOLUTION_NOT_CANCEL",
                    "cancel resolution requires a zero-notional cancellation permit",
                )
            claim = self._require_dispatch_claim(connection, reservation)
            binding = self._dispatch_claim_binding(reservation, claim)
            self._assert_cancel_dispatch_proof_matches_claim(proof, binding)
            return binding

    def resolve_cancel_dispatch_freeze(self, proof: CancelDispatchResolutionProof) -> None:
        """Resolve one cancel-action latch using an exact terminal cancel record.

        This path never uses order-level ``CANCELED_NO_FILL`` evidence. A
        CANCELLED action must bind its exact terminal target postcondition; a
        REJECTED action leaves the target's open exposure snapshot intact. The
        authority must hold the current external writer fence across the risk
        transaction. An exact already-committed proof is a no-op on replay.
        """

        if type(proof) is not CancelDispatchResolutionProof:
            raise PermitInvalidError(
                "CANCEL_DISPATCH_RESOLUTION_PROOF_REQUIRED",
                "cancel dispatch latch requires a typed cancellation-action proof",
            )
        authority = self._execution_journal_authority
        guard_factory = getattr(authority, "cancel_dispatch_resolution_guard", None)
        if not callable(guard_factory):
            raise PermitInvalidError(
                "CANCEL_DISPATCH_RESOLUTION_AUTHORITY_REQUIRED",
                "a verified cancellation journal authority is required",
            )

        binding = self._load_cancel_dispatch_claim_binding(proof)
        if binding is None:
            return
        self._assert_cancel_dispatch_proof_matches_claim(proof, binding)
        try:
            guard = guard_factory(proof, claim=binding)
            with guard as attestation:
                if type(attestation) is not VerifiedCancelDispatchResolution or not (
                    attestation.scope == binding.scope == proof.scope
                    and attestation.permit_id == binding.permit_id == proof.permit_id
                    and attestation.intent_id == binding.intent_id == proof.intent_id
                    and attestation.intent_hash == binding.intent_hash == proof.intent_hash
                    and attestation.claim_digest == binding.claim_digest == proof.claim_digest
                    and attestation.proof_sha256 == proof.fingerprint
                    and attestation.cancel_event_id == proof.cancel_event_id
                    and attestation.cancel_event_sequence == proof.cancel_event_sequence
                    and attestation.cancel_event_sha256 == proof.cancel_event_sha256
                    and attestation.source_evidence_sha256 == proof.source_evidence_sha256
                    and attestation.target_postcondition is proof.target_postcondition
                    and attestation.target_record_sha256 == proof.target_record_sha256
                    and attestation.target_event_id == proof.target_event_id
                    and attestation.target_event_sequence == proof.target_event_sequence
                    and attestation.target_event_sha256 == proof.target_event_sha256
                    and attestation.writer_owner_id == proof.writer_owner_id
                    and attestation.writer_fencing_token == proof.writer_fencing_token
                    and attestation.writer_fence_sha256 == proof.writer_fence_sha256
                ):
                    raise PermitInvalidError(
                        "CANCEL_DISPATCH_RESOLUTION_PROOF_REJECTED",
                        "the journal authority did not attest this exact cancellation proof",
                    )

                with self._transaction() as connection:
                    existing = connection.execute(
                        "SELECT * FROM risk_cancel_dispatch_resolutions WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if existing is not None:
                        if not self._cancel_dispatch_resolution_row_matches_proof(existing, proof):
                            raise PermitInvalidError(
                                "CANCEL_DISPATCH_ALREADY_RESOLVED",
                                "this cancellation claim has another immutable resolution",
                            )
                        reservation = connection.execute(
                            "SELECT status FROM risk_reservations WHERE permit_id = ?",
                            (proof.permit_id,),
                        ).fetchone()
                        freeze = connection.execute(
                            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
                            (proof.scope.key, proof.cause_id),
                        ).fetchone()
                        if (
                            reservation is None
                            or reservation["status"] != self._SETTLED
                            or (freeze is not None and int(freeze["active"]) == 1)
                        ):
                            raise PermitInvalidError(
                                "CANCEL_DISPATCH_RESOLUTION_INCONSISTENT",
                                "stored cancellation resolution conflicts with current risk state",
                            )
                        return

                    reservation = connection.execute(
                        "SELECT * FROM risk_reservations WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if reservation is None:
                        raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
                    self._ensure_policy_binding(connection, str(reservation["scope_key"]))
                    if (
                        reservation["action"] != IntentAction.CANCEL.value
                        or _as_decimal(reservation["notional"]) != Decimal("0")
                        or reservation["scope_key"] != proof.scope.key
                        or reservation["intent_id"] != proof.intent_id
                        or reservation["intent_hash"] != proof.intent_hash
                    ):
                        raise PermitInvalidError(
                            "CANCEL_DISPATCH_RESOLUTION_NOT_CANCEL",
                            "cancel resolution no longer matches its zero-notional cancel permit",
                        )
                    claim = self._require_dispatch_claim(connection, reservation)
                    current_binding = self._dispatch_claim_binding(reservation, claim)
                    self._assert_cancel_dispatch_proof_matches_claim(proof, current_binding)
                    if reservation["status"] not in {self._ACTIVE, self._SETTLED}:
                        raise PermitInvalidError(
                            "CANCEL_DISPATCH_PERMIT_NOT_SETTLEABLE",
                            "terminal cancel evidence cannot settle this permit state",
                        )
                    prior_event = connection.execute(
                        "SELECT permit_id FROM risk_cancel_dispatch_resolutions "
                        "WHERE execution_scope_key = ? AND cancel_event_id = ?",
                        (proof.execution_scope_key, proof.cancel_event_id),
                    ).fetchone()
                    if prior_event is not None:
                        raise PermitInvalidError(
                            "CANCEL_DISPATCH_EVENT_REPLAYED",
                            "this immutable cancellation event already resolved another claim",
                        )

                    connection.execute(
                        "UPDATE risk_reservations SET status = ?, reason = ? "
                        "WHERE permit_id = ? AND status = ?",
                        (
                            self._SETTLED,
                            "cancel_action_terminal:" + proof.terminal_state.value.lower(),
                            proof.permit_id,
                            self._ACTIVE,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO risk_cancel_dispatch_resolutions (
                            permit_id, scope_key, intent_id, intent_hash, cause_id,
                            claim_digest, cancel_intent_fingerprint, execution_scope_key,
                            cancel_id, target_intent_id, provider_order_id, terminal_state,
                            evidence_class, dispatch_attempt_count, cancel_event_id,
                            cancel_event_sequence, cancel_event_sha256, cancel_source,
                            source_evidence_sha256, target_postcondition, target_state,
                            target_filled_quantity, target_updated_at_ns,
                            target_record_sha256, target_event_id, target_event_sequence,
                            target_event_type, target_event_sha256, writer_owner_id,
                            writer_fencing_token, writer_fence_sha256, proof_sha256, resolved_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            proof.permit_id,
                            current_binding.scope.key,
                            proof.intent_id,
                            proof.intent_hash,
                            proof.cause_id,
                            proof.claim_digest,
                            proof.cancel_intent_fingerprint,
                            proof.execution_scope_key,
                            proof.cancel_id,
                            proof.target_intent_id,
                            proof.provider_order_id,
                            proof.terminal_state.value,
                            proof.evidence_class.value,
                            proof.dispatch_attempt_count,
                            proof.cancel_event_id,
                            proof.cancel_event_sequence,
                            proof.cancel_event_sha256,
                            proof.cancel_source.value,
                            proof.source_evidence_sha256,
                            proof.target_postcondition.value,
                            proof.target_state,
                            proof.target_filled_quantity,
                            proof.target_updated_at_ns,
                            proof.target_record_sha256,
                            proof.target_event_id,
                            proof.target_event_sequence,
                            proof.target_event_type,
                            proof.target_event_sha256,
                            proof.writer_owner_id,
                            proof.writer_fencing_token,
                            proof.writer_fence_sha256,
                            proof.fingerprint,
                            self._clock(),
                        ),
                    )
                    resolved = connection.execute(
                        "UPDATE risk_freezes SET active = 0, updated_at = ? "
                        "WHERE scope_key = ? AND cause_id = ? AND active = 1",
                        (self._clock(), current_binding.scope.key, proof.cause_id),
                    )
                    if resolved.rowcount != 1:
                        raise PermitInvalidError(
                            "DISPATCH_FREEZE_MISSING",
                            "the exact cancellation dispatch latch was not active",
                        )
        except PermitInvalidError:
            raise
        except Exception as exc:
            raise PermitInvalidError(
                "CANCEL_DISPATCH_RESOLUTION_PROOF_REJECTED",
                "the cancellation journal authority could not verify this proof",
            ) from exc

    def resolve_dispatch_exposure(
        self, proof: DispatchExposureResolutionProof
    ) -> DispatchExposureResolutionReceipt:
        """Seed terminal positive-fill occupancy and clear its exact latch atomically.

        This is the only first-observation path for an order whose earliest
        complete source snapshot is already FILLED or partially FILLED then
        CANCELLED.  It never fabricates the legacy zero-fill tracked proof.
        In RESERVATIONS_ONLY mode the full settled reservation remains counted;
        LIVE_GROSS additionally converts that reservation into the existing
        per-order gross/fill ledger under a fresh attributed baseline.
        """
        if type(proof) is not DispatchExposureResolutionProof:
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_PROOF_REQUIRED",
                "a typed positive-fill terminal exposure proof is required",
            )
        observation = proof.observation
        if self._policy.notional_unit is not None and (
            observation.notional_unit != self._policy.notional_unit
            or observation.valuation_unit != self._policy.notional_unit
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_UNIT_MISMATCH",
                "terminal order valuation must use the fixed policy unit",
            )
        now_ns = self._now_ns()
        self._assert_terminal_observation_current(proof, now_ns)
        binding = self._load_dispatch_claim_binding(
            proof.permit_id, proof.journal_record_sha256
        )
        if binding != proof.claim:
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_PROOF_SCOPE_MISMATCH",
                "terminal proof does not carry the current exact durable claim",
            )
        guard_factory = None
        for authority in (
            self._gross_exposure_authority,
            self._execution_journal_authority,
        ):
            candidate = getattr(authority, "dispatch_exposure_resolution_guard", None)
            if callable(candidate):
                guard_factory = candidate
                break
        if not callable(guard_factory):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_AUTHORITY_REQUIRED",
                "a current order-journal authority is required",
            )

        try:
            with guard_factory(proof, claim=binding) as verified:
                if not _verified_dispatch_exposure_matches(
                    verified, proof=proof, claim=binding
                ):
                    raise PermitInvalidError(
                        "DISPATCH_EXPOSURE_PROOF_REJECTED",
                        "the journal authority did not verify these exact terminal facts and fence",
                    )
                with self._transaction() as connection:
                    committed_at_ns = self._now_ns()
                    self._assert_terminal_observation_current(proof, committed_at_ns)
                    self._ensure_policy_binding(connection, proof.scope.key)
                    reservation = connection.execute(
                        "SELECT * FROM risk_reservations WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if reservation is None:
                        raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
                    if (
                        reservation["action"] != IntentAction.INCREASE.value
                        or reservation["status"] != self._SETTLED
                    ):
                        raise PermitInvalidError(
                            "ORDER_EXPOSURE_REQUIRES_SETTLED_INCREASE",
                            "terminal exposure resolution requires a settled increase permit",
                        )
                    claim_row = self._require_dispatch_claim(connection, reservation)
                    current_claim = self._dispatch_claim_binding(reservation, claim_row)
                    if current_claim != binding or current_claim != proof.claim:
                        raise PermitInvalidError(
                            "DISPATCH_RESOLUTION_PROOF_SCOPE_MISMATCH",
                            "the durable dispatch claim changed before terminal exposure commit",
                        )
                    prior_resolution = connection.execute(
                        "SELECT permit_id FROM risk_dispatch_resolutions WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if prior_resolution is not None:
                        raise PermitInvalidError(
                            "DISPATCH_ALREADY_RECONCILED",
                            "this dispatch claim already has a terminal resolution",
                        )
                    prior_exposure = connection.execute(
                        "SELECT permit_id FROM risk_order_exposures WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if prior_exposure is not None:
                        raise PermitInvalidError(
                            "ORDER_EXPOSURE_ALREADY_SEEDED",
                            "this permit already has an exposure projection",
                        )
                    self._assert_order_observation_identity(
                        observation, reservation, claim_row=claim_row
                    )
                    self._assert_terminal_order_matches_actual_claim(
                        observation, reservation, claim_row
                    )

                    baseline = None
                    baseline_fresh = False
                    if self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS:
                        baseline = self._assert_live_baseline_fresh(
                            connection, proof.scope, committed_at_ns
                        )
                        baseline_fence = int(baseline["writer_fencing_token"])
                        if observation.writer_fencing_token < baseline_fence or (
                            observation.writer_fencing_token == baseline_fence
                            and (
                                observation.writer_owner_id != baseline["writer_owner_id"]
                                or observation.writer_fence_sha256
                                != baseline["writer_fence_sha256"]
                            )
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_WRITER_FENCE_STALE",
                                "terminal observation predates the accepted account baseline fence",
                            )
                        baseline_fresh = True
                        latest = connection.execute(
                            "SELECT journal_revision FROM risk_gross_scope_revisions "
                            "WHERE scope_key = ?",
                            (proof.scope.key,),
                        ).fetchone()
                        if latest is None or observation.journal_revision <= int(
                            latest["journal_revision"]
                        ):
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_JOURNAL_REVISION_STALE",
                                "account journal revision must advance monotonically",
                            )

                    snapshot = self._apply_order_exposure_in_transaction(
                        connection,
                        observation,
                        reservation,
                        None,
                        committed_at_ns,
                        dispatch_proof_sha256=proof.fingerprint,
                    )
                    self._persist_order_fill_facts(connection, observation)
                    connection.execute(
                        """
                        INSERT INTO risk_dispatch_resolutions (
                            permit_id, scope_key, intent_id, intent_hash, cause_id,
                            claim_digest, proof_kind, evidence_class,
                            dispatch_attempt_count, journal_revision,
                            journal_record_sha256,
                            reconciliation_evidence_sha256, writer_fence_sha256,
                            provider_order_id, accepted_request_sha256,
                            exposure_reservation_id, exposure_reservation_sha256,
                            filled_quantity, trade_count, resolved_at,
                            resolution_proof_sha256, exposure_proof_sha256,
                            terminal_state, requested_quantity,
                            cumulative_filled_quantity, exposure_fill_trade_count,
                            exposure_fill_ids_sha256, exposure_order_revision
                        ) VALUES (?, ?, ?, ?, ?, ?, 'TERMINAL_EXPOSURE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            proof.permit_id,
                            proof.scope.key,
                            proof.intent_id,
                            proof.intent_hash,
                            proof.cause_id,
                            proof.claim_digest,
                            proof.evidence_class.value,
                            proof.dispatch_attempt_count,
                            observation.journal_revision,
                            observation.journal_record_sha256,
                            proof.reconciliation_evidence_sha256,
                            observation.writer_fence_sha256,
                            observation.provider_order_id,
                            observation.accepted_request_sha256,
                            observation.exposure_reservation_id,
                            observation.exposure_reservation_sha256,
                            self._clock(),
                            proof.fingerprint,
                            observation.fingerprint,
                            observation.state.value,
                            _canonical_decimal_text(observation.requested_quantity),
                            _canonical_decimal_text(observation.cumulative_filled_quantity),
                            observation.fill_trade_count,
                            observation.fill_ids_sha256,
                            observation.order_revision,
                        ),
                    )
                    cleared = connection.execute(
                        "UPDATE risk_freezes SET active = 0, updated_at = ? "
                        "WHERE scope_key = ? AND cause_id = ? AND active = 1",
                        (self._clock(), proof.scope.key, proof.cause_id),
                    )
                    if cleared.rowcount != 1:
                        raise PermitInvalidError(
                            "DISPATCH_FREEZE_MISSING",
                            "the exact dispatch latch was not active at terminal commit",
                        )
                    if self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS:
                        updated = connection.execute(
                            "UPDATE risk_gross_scope_revisions SET journal_revision = ? "
                            "WHERE scope_key = ?",
                            (observation.journal_revision, proof.scope.key),
                        )
                        if updated.rowcount != 1:
                            raise RiskDeniedError(
                                "ORDER_EXPOSURE_JOURNAL_REVISION_UNAVAILABLE",
                                "the account journal revision cursor is unavailable",
                            )
                        if self._gross_scope_over_limit(connection, proof.scope):
                            self._freeze_in_transaction(
                                connection,
                                proof.scope,
                                "gross-exposure-over-cap",
                                "gross_exposure_limit_exceeded",
                                self._clock(),
                            )
                        elif baseline_fresh:
                            connection.execute(
                                "UPDATE risk_freezes SET active = 0, updated_at = ? "
                                "WHERE scope_key = ? AND cause_id = "
                                "'gross-exposure-over-cap' AND active = 1",
                                (self._clock(), proof.scope.key),
                            )
                    # Keep the bound local for static analyzers: the snapshot
                    # return value is deliberately obtained from the exact
                    # durable receipt, not from this transient calculation.
                    del snapshot
                    result = self._dispatch_exposure_resolution_receipt_in_transaction(
                        connection, proof.permit_id
                    )
                    commit_now_ns = self._now_ns()
                    self._assert_terminal_observation_current(proof, commit_now_ns)
                    if self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS:
                        self._assert_live_baseline_fresh(
                            connection, proof.scope, commit_now_ns
                        )
                return result
        except (PermitInvalidError, RiskDeniedError):
            raise
        except Exception as exc:
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_PROOF_REJECTED",
                "the journal authority could not verify this terminal exposure proof",
            ) from exc

    def dispatch_exposure_resolution_receipt(
        self, permit_id: str
    ) -> DispatchExposureResolutionReceipt:
        """Read one exact claim/resolution/exposure snapshot without mutation."""
        if type(permit_id) is not str or not permit_id or permit_id != permit_id.strip():
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit reference is invalid")
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                result = self._dispatch_exposure_resolution_receipt_in_transaction(
                    connection, permit_id
                )
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("ROLLBACK")
            return result

    def _dispatch_exposure_resolution_receipt_in_transaction(
        self, connection: sqlite3.Connection, permit_id: str
    ) -> DispatchExposureResolutionReceipt:
        reservation = connection.execute(
            "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if reservation is None:
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
        if (
            reservation["action"] != IntentAction.INCREASE.value
            or reservation["status"] != self._SETTLED
        ):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT",
                "terminal exposure receipt requires a settled increase reservation",
            )
        policy_error = self._policy_binding_read_error(connection, str(reservation["scope_key"]))
        if policy_error is not None:
            raise PermitInvalidError(
                policy_error, "risk database is bound to another or unknown policy"
            )
        claim_row = connection.execute(
            "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if claim_row is None:
            raise PermitInvalidError("PERMIT_NOT_DISPATCH_CLAIMED", "permit was not claimed")
        try:
            if reservation["group_id"] is not None:
                self._validate_group_claim_row(reservation, claim_row)
            claim = self._dispatch_claim_binding(reservation, claim_row)
        except (KeyError, TypeError, ValueError, RiskDeniedError) as exc:
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT", "stored dispatch claim is inconsistent"
            ) from exc
        cause = self._dispatch_freeze_cause(str(reservation["intent_id"]))
        if claim.cause_id != cause:
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT", "dispatch claim cause is inconsistent"
            )
        freeze = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
            (reservation["scope_key"], cause),
        ).fetchone()
        if freeze is None or type(freeze["active"]) is not int or freeze["active"] not in (0, 1):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT", "exact dispatch latch is missing or invalid"
            )
        other_freezes = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id <> ?",
            (reservation["scope_key"], cause),
        ).fetchall()
        if any(type(row["active"]) is not int or row["active"] not in (0, 1) for row in other_freezes):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT", "another account freeze has an invalid state"
            )
        has_other_active_freezes = any(int(row["active"]) == 1 for row in other_freezes)
        resolution = connection.execute(
            "SELECT * FROM risk_dispatch_resolutions WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        exposure = connection.execute(
            "SELECT * FROM risk_order_exposures WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if resolution is None:
            if freeze["active"] != 1 or exposure is not None:
                raise PermitInvalidError(
                    "DISPATCH_EXPOSURE_STATE_CONFLICT",
                    "an unresolved claim must retain its exact latch and have no exposure row",
                )
            return DispatchExposureResolutionReceipt(
                status=DispatchExposureResolutionReceiptStatus.UNRESOLVED_ACTIVE,
                claim=claim,
                dispatch_latch_active=True,
                has_other_active_freezes=has_other_active_freezes,
            )
        if (
            freeze["active"] != 0
            or resolution["proof_kind"] != "TERMINAL_EXPOSURE"
            or resolution["scope_key"] != reservation["scope_key"]
            or resolution["intent_id"] != reservation["intent_id"]
            or resolution["intent_hash"] != reservation["intent_hash"]
            or resolution["cause_id"] != cause
            or resolution["claim_digest"] != claim.claim_digest
            or exposure is None
        ):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT",
                "stored terminal resolution, exposure, claim, or latch is inconsistent",
            )
        required = (
            "resolution_proof_sha256",
            "exposure_proof_sha256",
            "terminal_state",
            "requested_quantity",
            "cumulative_filled_quantity",
            "exposure_fill_trade_count",
            "exposure_fill_ids_sha256",
            "exposure_order_revision",
        )
        if any(resolution[name] is None for name in required):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT", "terminal resolution proof columns are missing"
            )
        try:
            final_state = OrderExposureState(str(resolution["terminal_state"]))
            requested = _as_decimal(resolution["requested_quantity"])
            filled = _as_decimal(resolution["cumulative_filled_quantity"])
            trade_count = int(resolution["exposure_fill_trade_count"])
            order_revision = int(resolution["exposure_order_revision"])
            journal_revision = int(resolution["journal_revision"])
            proof_sha256 = _require_sha256(
                str(resolution["resolution_proof_sha256"]), "resolution_proof_sha256"
            )
            exposure_proof_sha256 = _require_sha256(
                str(resolution["exposure_proof_sha256"]), "exposure_proof_sha256"
            )
            exposure_id = str(resolution["exposure_reservation_id"])
            exposure_sha256 = _require_sha256(
                str(resolution["exposure_reservation_sha256"]),
                "exposure_reservation_sha256",
            )
            fill_ids_sha = _require_sha256(
                str(resolution["exposure_fill_ids_sha256"]), "exposure_fill_ids_sha256"
            )
            observation_record = _require_sha256(
                str(resolution["journal_record_sha256"]), "journal_record_sha256"
            )
        except (TypeError, ValueError) as exc:
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT", "stored terminal proof fields are malformed"
            ) from exc
        if not (
            final_state in (OrderExposureState.FILLED, OrderExposureState.CANCELLED)
            and requested > 0
            and filled > 0
            and filled <= requested
            and trade_count > 0
            and order_revision > 0
            and journal_revision > 0
            and exposure_id
            and exposure_id == str(exposure["exposure_reservation_id"])
            and exposure_sha256 == str(exposure["exposure_reservation_sha256"])
            and proof_sha256 == str(exposure["dispatch_proof_sha256"])
            and exposure_proof_sha256 == str(exposure["proof_sha256"])
            and final_state.value == exposure["state"]
            and _canonical_decimal_text(requested) == exposure["requested_quantity"]
            and _canonical_decimal_text(filled) == exposure["cumulative_filled_quantity"]
            and Decimal(0) == _as_decimal(exposure["leaves_quantity"])
            and trade_count == int(exposure["fill_trade_count"])
            and fill_ids_sha == str(exposure["fill_ids_sha256"])
            and order_revision == int(exposure["order_revision"])
            and journal_revision == int(exposure["journal_revision"])
            and observation_record == str(exposure["journal_record_sha256"])
            and int(exposure["complete_order_snapshot"]) == 1
            and int(exposure["complete_fill_history"]) == 1
        ):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT",
                "terminal exposure projection no longer matches its immutable resolution",
            )
        fills = connection.execute(
            "SELECT trade_id, quantity FROM risk_order_exposure_fills "
            "WHERE permit_id = ? ORDER BY trade_id",
            (permit_id,),
        ).fetchall()
        if (
            len(fills) != trade_count
            or fill_ids_sha256_from_ids(tuple(str(row["trade_id"]) for row in fills))
            != fill_ids_sha
            or sum((_as_decimal(row["quantity"]) for row in fills), Decimal(0)) != filled
        ):
            raise PermitInvalidError(
                "DISPATCH_EXPOSURE_STATE_CONFLICT",
                "terminal fill rows do not match the recorded complete fill set",
            )
        return DispatchExposureResolutionReceipt(
            status=DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED,
            claim=claim,
            proof_sha256=proof_sha256,
            exposure_proof_sha256=exposure_proof_sha256,
            final_state=final_state,
            requested_quantity=requested,
            filled_quantity=filled,
            trade_count=trade_count,
            order_revision=order_revision,
            journal_revision=journal_revision,
            exposure_reservation_id=exposure_id,
            exposure_reservation_sha256=exposure_sha256,
            dispatch_latch_active=False,
            has_other_active_freezes=has_other_active_freezes,
        )

    def resolve_dispatch_freeze(
        self, proof: DispatchResolutionProof
    ) -> Optional[DispatchTerminalResolutionReceipt]:  # noqa: UP045 -- Python 3.9 is supported.
        """Resolve dispatch uncertainty under a verified execution-journal fence.

        The injected authority must hold its account-wide writer fence while
        this method rechecks the risk claim and commits the one-time proof ID.
        It may attest either a zero-fill terminal rejection/cancellation or an
        ACKED order transferred to a durable exposure reservation. The latter
        clears only the uncertainty latch: the settled permit remains counted.
        Filled/partial callbacks, open orders without tracked exposure, unknown
        or ambiguous evidence are not accepted.
        """
        if type(proof) not in (DispatchTerminalProof, DispatchTrackedOrderProof):
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_PROOF_REQUIRED",
                "dispatch-latch resolution requires a typed journal proof",
            )
        # Cancellation actions have their own terminal-action evidence model.
        # An order-level no-fill proof cannot release a cancel latch: the
        # target may have fills, and a rejected cancel leaves it exposed.
        with self._connection() as connection:
            reservation = connection.execute(
                "SELECT action FROM risk_reservations WHERE permit_id = ?",
                (proof.permit_id,),
            ).fetchone()
        if reservation is not None and reservation["action"] == IntentAction.CANCEL.value:
            raise PermitInvalidError(
                "CANCEL_DISPATCH_REQUIRES_CANCEL_PROOF",
                "cancellation dispatch requires terminal cancellation-action evidence",
            )
        authority = self._execution_journal_authority
        guard_factory = getattr(authority, "dispatch_resolution_guard", None)
        if not callable(guard_factory):
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_AUTHORITY_REQUIRED",
                "a verified execution-journal authority is required",
            )

        binding = self._load_dispatch_claim_binding(proof.permit_id, proof.journal_record_sha256)
        self._assert_dispatch_proof_matches_claim(proof, binding)
        try:
            guard = guard_factory(proof, claim=binding)
            with guard as attestation:
                if type(attestation) is not VerifiedDispatchResolution or not (
                    attestation.scope == binding.scope == proof.scope
                    and attestation.permit_id == binding.permit_id == proof.permit_id
                    and attestation.intent_id == binding.intent_id == proof.intent_id
                    and attestation.intent_hash == binding.intent_hash == proof.intent_hash
                    and attestation.claim_digest == binding.claim_digest == proof.claim_digest
                    and attestation.proof_sha256 == proof.fingerprint
                    and attestation.journal_revision == proof.journal_revision
                    and attestation.journal_record_sha256 == proof.journal_record_sha256
                    and attestation.writer_fence_sha256 == proof.writer_fence_sha256
                ):
                    raise PermitInvalidError(
                        "DISPATCH_RESOLUTION_PROOF_REJECTED",
                        "the journal authority did not attest this exact proof and claim",
                    )
                with self._transaction() as connection:
                    prior = connection.execute(
                        "SELECT permit_id FROM risk_dispatch_resolutions "
                        "WHERE journal_record_sha256 = ?",
                        (proof.journal_record_sha256,),
                    ).fetchone()
                    if prior is not None:
                        raise PermitInvalidError(
                            "DISPATCH_PROOF_REPLAYED",
                            "this immutable journal proof was already consumed",
                        )
                    prior = connection.execute(
                        "SELECT journal_record_sha256 FROM risk_dispatch_resolutions "
                        "WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if prior is not None:
                        raise PermitInvalidError(
                            "DISPATCH_ALREADY_RECONCILED",
                            "this dispatch claim already has a terminal resolution",
                        )
                    reservation = connection.execute(
                        "SELECT * FROM risk_reservations WHERE permit_id = ?",
                        (proof.permit_id,),
                    ).fetchone()
                    if reservation is None:
                        raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
                    if reservation["action"] == IntentAction.CANCEL.value:
                        raise PermitInvalidError(
                            "CANCEL_DISPATCH_REQUIRES_CANCEL_PROOF",
                            "cancellation dispatch requires terminal cancellation-action evidence",
                        )
                    self._ensure_policy_binding(connection, str(reservation["scope_key"]))
                    claim = self._require_dispatch_claim(connection, reservation)
                    current_binding = self._dispatch_claim_binding(reservation, claim)
                    self._assert_dispatch_proof_matches_claim(proof, current_binding)
                    if reservation["status"] != self._SETTLED:
                        raise PermitInvalidError(
                            "PERMIT_NOT_SETTLED",
                            "dispatch proof requires a settled reservation",
                        )
                    if type(proof) is DispatchTerminalProof:
                        proof_kind = proof.terminal_state.value
                        provider_order_id = None
                        accepted_request_sha256 = None
                        exposure_reservation_id = None
                        exposure_reservation_sha256 = None
                        filled_quantity = proof.filled_quantity
                        trade_count = proof.trade_count
                        resolution_proof_sha256 = proof.fingerprint
                        fee_record_count = proof.fee_record_count
                        fee_history_complete = int(proof.fee_history_complete)
                    else:
                        proof_kind = "ACKED_TRACKED"
                        provider_order_id = proof.provider_order_id
                        accepted_request_sha256 = proof.accepted_request_sha256
                        exposure_reservation_id = proof.exposure_reservation_id
                        exposure_reservation_sha256 = proof.exposure_reservation_sha256
                        filled_quantity = proof.filled_quantity
                        trade_count = proof.trade_count
                        resolution_proof_sha256 = None
                        fee_record_count = None
                        fee_history_complete = None
                    connection.execute(
                        """
                        INSERT INTO risk_dispatch_resolutions (
                            permit_id, scope_key, intent_id, intent_hash, cause_id,
                            claim_digest, proof_kind, evidence_class,
                            dispatch_attempt_count, journal_revision,
                            journal_record_sha256,
                            reconciliation_evidence_sha256, writer_fence_sha256,
                            provider_order_id, accepted_request_sha256,
                            exposure_reservation_id, exposure_reservation_sha256,
                            filled_quantity, trade_count, resolution_proof_sha256,
                            fee_record_count, fee_history_complete, resolved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            proof.permit_id,
                            current_binding.scope.key,
                            proof.intent_id,
                            proof.intent_hash,
                            proof.cause_id,
                            proof.claim_digest,
                            proof_kind,
                            proof.evidence_class.value,
                            proof.dispatch_attempt_count,
                            proof.journal_revision,
                            proof.journal_record_sha256,
                            proof.reconciliation_evidence_sha256,
                            proof.writer_fence_sha256,
                            provider_order_id,
                            accepted_request_sha256,
                            exposure_reservation_id,
                            exposure_reservation_sha256,
                            filled_quantity,
                            trade_count,
                            resolution_proof_sha256,
                            fee_record_count,
                            fee_history_complete,
                            self._clock(),
                        ),
                    )
                    if type(proof) is DispatchTerminalProof:
                        settled = connection.execute(
                            "UPDATE risk_reservations SET status = ?, reason = ? "
                            "WHERE permit_id = ? AND status = ?",
                            (self._NO_FILL, proof_kind, proof.permit_id, self._SETTLED),
                        )
                        if settled.rowcount != 1:
                            raise PermitInvalidError(
                                "PERMIT_NOT_SETTLED",
                                "no-fill proof did not consume the settled reservation",
                            )
                    result = connection.execute(
                        """
                        UPDATE risk_freezes SET active = 0, updated_at = ?
                        WHERE scope_key = ? AND cause_id = ? AND active = 1
                        """,
                        (self._clock(), current_binding.scope.key, proof.cause_id),
                    )
                    if result.rowcount != 1:
                        raise PermitInvalidError(
                            "DISPATCH_FREEZE_MISSING",
                            "the exact dispatch latch was not active at commit",
                        )
        except PermitInvalidError:
            raise
        except Exception as exc:
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_PROOF_REJECTED",
                "the execution-journal authority could not verify this proof",
            ) from exc
        if type(proof) is DispatchTerminalProof:
            receipt = self.dispatch_terminal_resolution_receipt(proof.permit_id)
            if (
                receipt.status
                is not DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
                or receipt.proof != proof
                or receipt.proof_sha256 != proof.fingerprint
            ):
                raise PermitInvalidError(
                    "DISPATCH_TERMINAL_STATE_CONFLICT",
                    "committed no-fill resolution did not read back as the exact proof",
                )
            return receipt
        return None

    def resolve_dispatch_freeze_recovered(
        self,
        proof: DispatchTrackedOrderProof,
        attestation: DispatchRecoveryAttestation,
    ) -> None:
        """Consume an original tracked-order proof under a newer journal fence.

        This is deliberately separate from :meth:`resolve_dispatch_freeze`.
        The authority must validate the immutable original proof-ready record
        and a new append-only recovery event while holding the current account
        fence across this method's Risk transaction. No generic state change,
        prior resolution, or inactive dispatch latch is accepted.
        """
        if type(proof) is not DispatchTrackedOrderProof:
            raise PermitInvalidError(
                "DISPATCH_RECOVERY_PROOF_REQUIRED",
                "recovery requires the exact tracked-order proof type",
            )
        if type(attestation) is not DispatchRecoveryAttestation:
            raise PermitInvalidError(
                "DISPATCH_RECOVERY_ATTESTATION_REQUIRED",
                "recovery requires a typed new-fence journal attestation",
            )
        if not _recovery_attestation_matches_proof(attestation, proof):
            raise PermitInvalidError(
                "DISPATCH_RECOVERY_ATTESTATION_MISMATCH",
                "recovery attestation does not match the original tracked-order proof",
            )
        authority = self._execution_journal_authority
        guard_factory = getattr(authority, "recovered_dispatch_resolution_guard", None)
        if not callable(guard_factory):
            raise PermitInvalidError(
                "DISPATCH_RECOVERY_AUTHORITY_REQUIRED",
                "a verified new-fence journal authority is required for recovery",
            )

        binding = self._load_dispatch_claim_binding(proof.permit_id, proof.journal_record_sha256)
        self._assert_dispatch_proof_matches_claim(proof, binding)
        if not _recovery_attestation_matches_claim_proof(attestation, binding, proof):
            raise PermitInvalidError(
                "DISPATCH_RECOVERY_ATTESTATION_MISMATCH",
                "recovery attestation does not match the exact durable Risk claim",
            )

        # Keep the external account fence alive across the entire SQLite
        # transaction. Unknown authority failures propagate so callers retain
        # UNKNOWN/frozen semantics instead of projecting an ordinary denial.
        guard = guard_factory(proof, attestation, claim=binding)
        with guard as verified:
            if not _verified_dispatch_recovery_matches(
                verified, proof=proof, attestation=attestation, claim=binding
            ):
                raise PermitInvalidError(
                    "DISPATCH_RECOVERY_ATTESTATION_REJECTED",
                    "the journal authority did not verify the exact old proof and current fence",
                )
            with self._transaction() as connection:
                reservation = connection.execute(
                    "SELECT * FROM risk_reservations WHERE permit_id = ?",
                    (proof.permit_id,),
                ).fetchone()
                if reservation is None:
                    raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
                if reservation["action"] != IntentAction.INCREASE.value:
                    raise PermitInvalidError(
                        "DISPATCH_RECOVERY_SCOPE_REJECTED",
                        "tracked-order recovery requires an INCREASE reservation",
                    )
                self._ensure_policy_binding(connection, str(reservation["scope_key"]))
                claim_row = self._require_dispatch_claim(connection, reservation)
                current_binding = self._dispatch_claim_binding(reservation, claim_row)
                if current_binding != binding:
                    raise PermitInvalidError(
                        "DISPATCH_CLAIM_CONFLICT",
                        "the exact durable dispatch claim changed during recovery",
                    )
                self._assert_dispatch_proof_matches_claim(proof, current_binding)
                if reservation["status"] != self._SETTLED:
                    raise PermitInvalidError(
                        "PERMIT_NOT_SETTLED", "dispatch recovery requires a settled reservation"
                    )
                prior = connection.execute(
                    "SELECT permit_id FROM risk_dispatch_resolutions WHERE permit_id = ?",
                    (proof.permit_id,),
                ).fetchone()
                if prior is not None:
                    raise PermitInvalidError(
                        "DISPATCH_ALREADY_RECONCILED",
                        "this dispatch claim already has a terminal resolution",
                    )
                prior = connection.execute(
                    "SELECT permit_id FROM risk_dispatch_resolutions "
                    "WHERE journal_record_sha256 = ?",
                    (proof.journal_record_sha256,),
                ).fetchone()
                if prior is not None:
                    raise PermitInvalidError(
                        "DISPATCH_PROOF_REPLAYED",
                        "this immutable journal proof was already consumed",
                    )
                prior = connection.execute(
                    "SELECT permit_id FROM risk_dispatch_recovery_audits "
                    "WHERE recovery_event_id = ? OR recovery_event_sha256 = ?",
                    (attestation.recovery_event_id, attestation.recovery_event_sha256),
                ).fetchone()
                if prior is not None:
                    raise PermitInvalidError(
                        "DISPATCH_RECOVERY_EVENT_REPLAYED",
                        "this immutable recovery event was already consumed",
                    )
                if not _recovery_attestation_matches_claim_proof(
                    attestation, current_binding, proof
                ):
                    raise PermitInvalidError(
                        "DISPATCH_RECOVERY_ATTESTATION_MISMATCH",
                        "recovery attestation no longer matches the current claim",
                    )
                connection.execute(
                    """
                    INSERT INTO risk_dispatch_resolutions (
                        permit_id, scope_key, intent_id, intent_hash, cause_id,
                        claim_digest, proof_kind, evidence_class,
                        dispatch_attempt_count, journal_revision,
                        journal_record_sha256,
                        reconciliation_evidence_sha256, writer_fence_sha256,
                        provider_order_id, accepted_request_sha256,
                        exposure_reservation_id, exposure_reservation_sha256,
                        filled_quantity, trade_count, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ACKED_TRACKED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        proof.permit_id,
                        current_binding.scope.key,
                        proof.intent_id,
                        proof.intent_hash,
                        proof.cause_id,
                        proof.claim_digest,
                        proof.evidence_class.value,
                        proof.dispatch_attempt_count,
                        proof.journal_revision,
                        proof.journal_record_sha256,
                        proof.reconciliation_evidence_sha256,
                        proof.writer_fence_sha256,
                        proof.provider_order_id,
                        proof.accepted_request_sha256,
                        proof.exposure_reservation_id,
                        proof.exposure_reservation_sha256,
                        self._clock(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO risk_dispatch_recovery_audits (
                        permit_id, scope_key, proof_sha256, attestation_sha256,
                        recovery_event_id, recovery_event_revision, recovery_event_sha256,
                        original_writer_owner_id, original_writer_fencing_token,
                        original_writer_fence_sha256, current_writer_owner_id,
                        current_writer_fencing_token, current_writer_fence_sha256,
                        attestation_json, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proof.permit_id,
                        current_binding.scope.key,
                        proof.fingerprint,
                        attestation.fingerprint,
                        attestation.recovery_event_id,
                        attestation.recovery_event_revision,
                        attestation.recovery_event_sha256,
                        attestation.original_writer_owner_id,
                        attestation.original_writer_fencing_token,
                        attestation.original_writer_fence_sha256,
                        attestation.current_writer_owner_id,
                        attestation.current_writer_fencing_token,
                        attestation.current_writer_fence_sha256,
                        _canonical_json(_dispatch_recovery_attestation_payload(attestation)),
                        self._clock(),
                    ),
                )
                cleared = connection.execute(
                    """
                    UPDATE risk_freezes SET active = 0, updated_at = ?
                    WHERE scope_key = ? AND cause_id = ? AND active = 1
                    """,
                    (self._clock(), current_binding.scope.key, proof.cause_id),
                )
                if cleared.rowcount != 1:
                    raise PermitInvalidError(
                        "DISPATCH_FREEZE_MISSING",
                        "the exact dispatch latch was not active at recovery commit",
                    )

    def active_freeze_reasons(self, scope: AccountScope) -> list[str]:
        """Return current independent freeze causes that gate the scope."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT reason FROM risk_freezes "
                "WHERE scope_key = ? AND active = 1 AND blocking = 1 ORDER BY cause_id",
                (scope.key,),
            ).fetchall()
        return [str(row["reason"]) for row in rows]

    def snapshot(self, scope: AccountScope) -> dict[str, object]:
        """Return a local, provider-free view of outstanding reservations."""
        now = self._clock()
        with self._transaction() as connection:
            self._ensure_policy_binding(connection, scope.key)
            self._expire_active(connection, now)
            rows = connection.execute(
                """
                SELECT action, status, notional FROM risk_reservations
                WHERE scope_key = ? AND status IN (?, ?)
                """,
                (scope.key, self._ACTIVE, self._SETTLED),
            ).fetchall()
            increase_rows = [row for row in rows if row["action"] == IntentAction.INCREASE.value]
            return {
                "active_freeze_reasons": self._freeze_reasons(connection, scope),
                "increase_count": len(increase_rows),
                "increase_notional": _sum_decimal_exact(
                    _as_decimal(row["notional"]) for row in increase_rows
                ),
                "policy_fingerprint": self._policy.fingerprint,
            }

    @staticmethod
    def _validate_group_key(scope: AccountScope, group_id: str) -> None:
        if type(scope) is not AccountScope:
            raise RiskDeniedError("GROUP_SCOPE_MISMATCH", "a trusted account scope is required")
        if type(group_id) is not str or not _ALLOCATION_ID.fullmatch(group_id):
            raise RiskDeniedError("GROUP_INVALID_LEG", "group_id is invalid")

    def _stored_group_matches(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        scope: AccountScope,
        group_id: str,
        group_fingerprint: str,
        policy_fingerprint: str,
        deadline_ns: int,
        expected_legs: Sequence[tuple[str, str, str, str]],
    ) -> bool:
        if len(rows) != len(expected_legs) or not rows:
            return False
        actual_members: list[tuple[str, str, str, str]] = []
        for row, expected in zip(rows, expected_legs):
            try:
                profile = str(row["group_exposure_profile_fingerprint"])
                actual = (
                    str(row["group_leg_id"]),
                    str(row["intent_id"]),
                    str(row["intent_hash"]),
                    profile,
                )
                if (
                    actual != expected
                    or row["scope_key"] != scope.key
                    or row["group_id"] != group_id
                    or row["group_fingerprint"] != group_fingerprint
                    or row["policy_hash"] != policy_fingerprint
                    or row["group_deadline_ns"] != deadline_ns
                    or row["group_leg_count"] != len(expected_legs)
                    or row["action"] != IntentAction.INCREASE.value
                    or row["group_expires_at_ns"] is None
                    or int(row["group_expires_at_ns"]) > deadline_ns
                ):
                    return False
                actual_members.append(actual)
            except (KeyError, TypeError, ValueError):
                return False
        try:
            return (
                _risk_group_fingerprint(
                    scope,
                    group_id,
                    policy_fingerprint,
                    deadline_ns,
                    actual_members,
                )
                == group_fingerprint
            )
        except (TypeError, ValueError):
            return False

    def _group_receipt_in_transaction(
        self,
        connection: sqlite3.Connection,
        scope: AccountScope,
        group_id: str,
        rows: Sequence[sqlite3.Row],
    ) -> RiskGroupReservationReceipt:
        """Validate and project every stored group row within the caller's txn."""

        def conflict() -> RiskDeniedError:
            return RiskDeniedError(
                "GROUP_STATE_CONFLICT", "group membership or claim state is inconsistent"
            )

        if not rows:
            raise conflict()
        try:
            first = rows[0]
            group_fingerprint = str(first["group_fingerprint"])
            policy_fingerprint = str(first["policy_hash"])
            deadline_ns = int(first["group_deadline_ns"])
            leg_count = int(first["group_leg_count"])
            if (
                first["scope_key"] != scope.key
                or first["group_id"] != group_id
                or not 1 <= leg_count <= _RISK_GROUP_MAX_LEGS
                or deadline_ns <= 0
                or len(rows) != leg_count
            ):
                raise conflict()
            members: list[tuple[str, str, str, str]] = []
            for row in rows:
                profile = str(row["group_exposure_profile_fingerprint"])
                if (
                    row["scope_key"] != scope.key
                    or row["group_id"] != group_id
                    or row["group_fingerprint"] != group_fingerprint
                    or row["policy_hash"] != policy_fingerprint
                    or int(row["group_deadline_ns"]) != deadline_ns
                    or int(row["group_leg_count"]) != leg_count
                    or int(row["group_expires_at_ns"]) <= 0
                    or int(row["group_expires_at_ns"]) > deadline_ns
                    or row["action"] != IntentAction.INCREASE.value
                ):
                    raise conflict()
                _require_sha256(profile, "exposure_profile_fingerprint")
                _require_sha256(str(row["intent_hash"]), "reserved_intent_hash")
                members.append(
                    (
                        str(row["group_leg_id"]),
                        str(row["intent_id"]),
                        str(row["intent_hash"]),
                        profile,
                    )
                )
            if tuple(sorted(item[0] for item in members)) != tuple(item[0] for item in members):
                raise conflict()
            if len({int(row["group_expires_at_ns"]) for row in rows}) != 1:
                raise conflict()
            if len({item[0] for item in members}) != len(members):
                raise conflict()
            if (
                _risk_group_fingerprint(scope, group_id, policy_fingerprint, deadline_ns, members)
                != group_fingerprint
            ):
                raise conflict()
            result: list[RiskGroupReservationLeg] = []
            for row in rows:
                try:
                    state = RiskGroupReservationState(str(row["status"]))
                except ValueError as exc:
                    raise conflict() from exc
                permit_id = str(row["permit_id"])
                intent_id = str(row["intent_id"])
                cause_id = self._dispatch_freeze_cause(intent_id)
                claim = connection.execute(
                    "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
                ).fetchone()
                resolution = connection.execute(
                    "SELECT * FROM risk_dispatch_resolutions WHERE permit_id = ?", (permit_id,)
                ).fetchone()
                freeze = connection.execute(
                    "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
                    (scope.key, cause_id),
                ).fetchone()
                if claim is None:
                    if resolution is not None or (
                        freeze is not None and int(freeze["active"]) == 1
                    ):
                        raise conflict()
                    if state in (
                        RiskGroupReservationState.SETTLED,
                        RiskGroupReservationState.NO_FILL_TERMINAL,
                    ):
                        raise conflict()
                    claimed = False
                    resolved = False
                    actual_hash = None
                else:
                    actual = self._validate_group_claim_row(row, claim)
                    claimed = True
                    actual_hash = actual.fingerprint
                    binding = self._dispatch_claim_binding(row, claim)
                    if resolution is None:
                        if freeze is None or int(freeze["active"]) != 1:
                            raise conflict()
                        if state in (
                            RiskGroupReservationState.RELEASED,
                            RiskGroupReservationState.EXPIRED,
                            RiskGroupReservationState.NO_FILL_TERMINAL,
                        ):
                            raise conflict()
                        resolved = False
                    else:
                        proof_kind = str(resolution["proof_kind"])
                        expected_state = (
                            RiskGroupReservationState.SETTLED
                            if proof_kind in ("ACKED_TRACKED", "TERMINAL_EXPOSURE")
                            else RiskGroupReservationState.NO_FILL_TERMINAL
                            if proof_kind in {item.value for item in DispatchTerminalState}
                            else None
                        )
                        if (
                            expected_state is None
                            or state is not expected_state
                            or freeze is None
                            or int(freeze["active"]) != 0
                            or resolution["scope_key"] != scope.key
                            or resolution["intent_id"] != intent_id
                            or resolution["intent_hash"] != row["intent_hash"]
                            or resolution["cause_id"] != cause_id
                            or resolution["claim_digest"] != binding.claim_digest
                        ):
                            raise conflict()
                        resolved = True
                result.append(
                    RiskGroupReservationLeg(
                        leg_id=str(row["group_leg_id"]),
                        permit_id=permit_id,
                        intent_id=intent_id,
                        reserved_intent_hash=str(row["intent_hash"]),
                        exposure_profile_fingerprint=str(row["group_exposure_profile_fingerprint"]),
                        actual_intent_hash=actual_hash,
                        state=state,
                        claimed=claimed,
                        resolved=resolved,
                        expires_at_ns=int(row["group_expires_at_ns"]),
                    )
                )
            return RiskGroupReservationReceipt(
                scope=scope,
                group_id=group_id,
                group_fingerprint=group_fingerprint,
                policy_fingerprint=policy_fingerprint,
                deadline_ns=deadline_ns,
                legs=tuple(result),
            )
        except RiskDeniedError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise conflict() from exc

    def _group_leg_claim_receipt_in_transaction(
        self,
        reservation: sqlite3.Row,
        claim_row: sqlite3.Row,
        *,
        resolved: bool,
    ) -> RiskGroupLegClaimReceipt:
        """Rebuild one immutable group claim from rows in the caller's snapshot."""

        try:
            actual = self._validate_group_claim_row(reservation, claim_row)
            claim = self._dispatch_claim_binding(reservation, claim_row)
            if type(resolved) is not bool:
                raise ValueError("invalid resolution state")
            return RiskGroupLegClaimReceipt(
                scope=claim.scope,
                group_id=str(reservation["group_id"]),
                group_fingerprint=str(reservation["group_fingerprint"]),
                policy_fingerprint=str(reservation["policy_hash"]),
                deadline_ns=int(reservation["group_deadline_ns"]),
                leg_id=str(reservation["group_leg_id"]),
                permit=self._permit_from_row(reservation),
                reserved_intent_hash=str(reservation["intent_hash"]),
                actual_intent_hash=actual.fingerprint,
                exposure_profile_fingerprint=str(
                    reservation["group_exposure_profile_fingerprint"]
                ),
                actual_quantity=actual.quantity,
                actual_notional=actual.notional,
                claim=claim,
                resolved=resolved,
            )
        except (KeyError, TypeError, ValueError, RiskDeniedError, PermitInvalidError) as exc:
            raise PermitInvalidError(
                "GROUP_STATE_CONFLICT", "stored group claim cannot be reconstructed"
            ) from exc

    def _validate_group_child_intent(
        self,
        reservation: sqlite3.Row,
        actual_intent: RiskIntent,
        *,
        stored: bool = False,
    ) -> None:
        """Check a mapped child against its persisted maximum-size envelope."""

        error_type = PermitInvalidError if stored else RiskDeniedError

        def reject(code: str, message: str) -> None:
            raise error_type(code, message)

        try:
            expected_profile = str(reservation["group_exposure_profile_fingerprint"])
            _require_sha256(expected_profile, "exposure_profile_fingerprint")
            if (
                actual_intent.intent_id != reservation["intent_id"]
                or actual_intent.scope.key != reservation["scope_key"]
                or actual_intent.action.value != reservation["action"]
                or actual_intent.strategy_id != reservation["strategy_id"]
                or actual_intent.allocation_version != reservation["allocation_version"]
                or actual_intent.instrument != reservation["instrument"]
                or actual_intent.quantity_unit != reservation["quantity_unit"]
                or actual_intent.notional_unit != reservation["notional_unit"]
            ):
                reject(
                    "GROUP_LEG_PROFILE_MISMATCH",
                    "child order changes reserved account, strategy, instrument, or units",
                )
            if actual_intent.exposure_profile_fingerprint != expected_profile:
                reject(
                    "GROUP_LEG_PROFILE_MISMATCH",
                    "child order economic profile differs from its reserved leg",
                )
            if (
                actual_intent.quantity is None
                or reservation["quantity"] is None
                or not _SHA256_HEX.fullmatch(actual_intent.payload_fingerprint)
            ):
                reject(
                    "GROUP_INVALID_LEG",
                    "child requires an exact mapped payload hash and positive quantity",
                )
            reserved_quantity = _as_decimal(reservation["quantity"])
            reserved_notional = _as_decimal(reservation["notional"])
            if (
                actual_intent.quantity <= 0
                or actual_intent.notional <= 0
                or actual_intent.quantity > reserved_quantity
                or actual_intent.notional > reserved_notional
            ):
                reject(
                    "GROUP_LEG_EXCEEDS_RESERVED",
                    "child quantity or trusted notional exceeds the reserved maximum",
                )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            if stored:
                raise PermitInvalidError(
                    "GROUP_STATE_CONFLICT", "stored group child binding is invalid"
                ) from exc
            raise RiskDeniedError(
                "GROUP_INVALID_LEG", "child order does not carry trusted bounded risk facts"
            ) from exc

    @staticmethod
    def _assert_instrument_facts(
        intent: RiskIntent,
        now_ns: int,
        *,
        invalid: bool = False,
    ) -> None:
        """Recheck an optional trusted instrument-fact interval at a write cut."""

        if intent.action is not IntentAction.INCREASE:
            return
        observed_at_ns = intent.instrument_facts_observed_at_ns
        valid_until_ns = intent.instrument_facts_valid_until_ns
        if observed_at_ns is None and valid_until_ns is None:
            return
        error_type = PermitInvalidError if invalid else RiskDeniedError
        if (
            type(observed_at_ns) is not int
            or type(valid_until_ns) is not int
            or observed_at_ns <= 0
            or valid_until_ns <= observed_at_ns
        ):
            raise error_type(
                "INSTRUMENT_FACTS_UNPROVEN",
                "instrument admission lacks a valid trusted facts interval",
            )
        if not observed_at_ns <= now_ns < valid_until_ns:
            raise error_type(
                "INSTRUMENT_FACTS_STALE",
                "instrument facts are not current at the Risk transaction boundary",
            )

    def _assert_current_instrument_facts(
        self,
        intent: RiskIntent,
        *,
        invalid: bool = False,
    ) -> None:
        """Check only mapped opening intents that carry a bounded fact interval."""

        if (
            intent.action is not IntentAction.INCREASE
        ):
            return
        if (
            intent.instrument_facts_observed_at_ns is None
            and intent.instrument_facts_valid_until_ns is None
        ):
            return
        self._assert_instrument_facts(intent, self._now_ns(), invalid=invalid)

    @staticmethod
    def _assert_group_exposure_facts(
        intent: RiskIntent,
        now_ns: int,
        *,
        invalid: bool = False,
    ) -> None:
        """Recheck trusted mapper facts at the transaction's admission cut."""

        error_type = PermitInvalidError if invalid else RiskDeniedError
        observed_at_ns = intent.instrument_facts_observed_at_ns
        valid_until_ns = intent.instrument_facts_valid_until_ns
        if (
            type(observed_at_ns) is not int
            or type(valid_until_ns) is not int
            or observed_at_ns <= 0
            or valid_until_ns <= observed_at_ns
        ):
            raise error_type(
                "GROUP_EXPOSURE_FACTS_UNPROVEN",
                "group intent lacks a trusted instrument-fact validity interval",
            )
        if not observed_at_ns <= now_ns < valid_until_ns:
            raise error_type(
                "GROUP_EXPOSURE_FACTS_STALE",
                "group instrument facts are not current at the Risk transaction boundary",
            )

    def _validate_group_claim_row(self, reservation: sqlite3.Row, claim: sqlite3.Row) -> RiskIntent:
        """Verify the immutable reservation-to-child claim row binding."""

        try:
            if (
                reservation["group_id"] is None
                or claim["group_id"] != reservation["group_id"]
                or claim["group_leg_id"] != reservation["group_leg_id"]
                or claim["group_fingerprint"] != reservation["group_fingerprint"]
                or int(claim["group_deadline_ns"]) != int(reservation["group_deadline_ns"])
                or claim["reserved_intent_hash"] != reservation["intent_hash"]
                or claim["intent_id"] != reservation["intent_id"]
                or claim["intent_hash"] != reservation["intent_hash"]
                or claim["scope_key"] != reservation["scope_key"]
                or claim["cause_id"] != self._dispatch_freeze_cause(str(reservation["intent_id"]))
            ):
                raise ValueError("claim identity mismatch")
            actual_json = str(claim["actual_intent_json"])
            actual_intent = _risk_intent_from_json(actual_json)
            if (
                actual_intent.fingerprint != claim["actual_intent_hash"]
                or claim["actual_exposure_profile_fingerprint"]
                != actual_intent.exposure_profile_fingerprint
                or _as_decimal(claim["actual_quantity"]) != actual_intent.quantity
                or _as_decimal(claim["actual_notional"]) != actual_intent.notional
                or int(claim["actual_instrument_facts_observed_at_ns"])
                != actual_intent.instrument_facts_observed_at_ns
                or int(claim["actual_instrument_facts_valid_until_ns"])
                != actual_intent.instrument_facts_valid_until_ns
            ):
                raise ValueError("child claim facts mismatch")
            self._validate_group_child_intent(reservation, actual_intent, stored=True)
            return actual_intent
        except PermitInvalidError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise PermitInvalidError(
                "GROUP_STATE_CONFLICT", "stored group claim binding is invalid"
            ) from exc

    def close(self) -> None:
        """Provide a symmetry hook; connections are per-operation and already closed."""

    def _existing_permit_or_raise(
        self, row: sqlite3.Row, intent: RiskIntent, now: float
    ) -> RiskPermit:
        if row["intent_hash"] != intent.fingerprint:
            raise RiskDeniedError("INTENT_ID_REUSED", "intent id was reused with another payload")
        if row["status"] != self._ACTIVE:
            raise RiskDeniedError("INTENT_NOT_REUSABLE", "intent was already finalized")
        permit = self._permit_from_row(row)
        if permit.expires_at <= now:
            raise RiskDeniedError("INTENT_EXPIRED", "intent permit has expired")
        if permit.policy_fingerprint != self._policy.fingerprint:
            raise RiskDeniedError("POLICY_CHANGED", "intent was admitted by another policy")
        return permit

    def _assert_increase_limit(self, connection: sqlite3.Connection, intent: RiskIntent) -> None:
        rows = connection.execute(
            """
            SELECT notional, notional_unit FROM risk_reservations
            WHERE scope_key = ? AND action = ? AND status IN (?, ?)
            """,
            (
                intent.scope.key,
                IntentAction.INCREASE.value,
                self._ACTIVE,
                self._SETTLED,
            ),
        ).fetchall()
        if self._policy.notional_unit is not None and any(
            row["notional_unit"] != self._policy.notional_unit for row in rows
        ):
            raise RiskDeniedError(
                "NOTIONAL_UNIT_USAGE_UNAVAILABLE",
                "existing account reservations do not use the fixed policy unit",
            )
        reserved_notional = sum(
            (Fraction(_as_decimal(row["notional"])) for row in rows), Fraction(0)
        )
        if len(rows) >= self._policy.max_increase_count:
            raise RiskDeniedError(
                "ACCOUNT_LIMIT_EXHAUSTED",
                "account increase reservation count limit reached",
            )
        if reserved_notional + Fraction(intent.notional) > Fraction(
            self._policy.max_increase_notional
        ):
            raise RiskDeniedError(
                "ACCOUNT_LIMIT_EXHAUSTED", "account increase notional limit exceeded"
            )

    def _gross_facts_are_fresh(self, observed_at_ns: int, valid_until_ns: int, now_ns: int) -> bool:
        max_age_ns = int(self._active_gross_age_seconds * 1_000_000_000)
        return observed_at_ns <= now_ns < valid_until_ns and now_ns - observed_at_ns <= max_age_ns

    def _assert_order_exposure_freshness(
        self, proof: OrderExposureObservationProof, now_ns: int
    ) -> None:
        if proof.state is OrderExposureState.UNKNOWN:
            return
        if not self._gross_facts_are_fresh(proof.observed_at_ns, proof.valid_until_ns, now_ns):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_OBSERVATION_STALE", "order or valuation evidence is stale"
            )
        max_age_ns = int(self._active_gross_age_seconds * 1_000_000_000)
        if not (
            proof.instrument_metadata_observed_at_ns
            <= now_ns
            < proof.instrument_metadata_valid_until_ns
            and now_ns - proof.instrument_metadata_observed_at_ns <= max_age_ns
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_METADATA_STALE", "instrument metadata is stale or expired"
            )
        if not (
            proof.valuation_observed_at_ns <= now_ns < proof.valuation_valid_until_ns
            and now_ns - proof.valuation_observed_at_ns <= max_age_ns
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_VALUATION_STALE", "valuation facts are stale or expired"
            )
        if proof.fx_observed_at_ns is not None and not (
            proof.fx_observed_at_ns <= now_ns < int(proof.fx_valid_until_ns)
            and now_ns - proof.fx_observed_at_ns <= max_age_ns
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_FX_STALE", "FX valuation facts are stale or expired"
            )
        if proof.valid_until_ns > min(
            proof.instrument_metadata_valid_until_ns,
            proof.valuation_valid_until_ns,
            proof.fx_valid_until_ns or proof.instrument_metadata_valid_until_ns,
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_VALIDITY_MISMATCH",
                "order fact validity outlives metadata or FX valuation facts",
            )

    @property
    def _active_gross_age_seconds(self) -> float:
        return float(self._policy.max_exposure_facts_age_seconds or 0.0)

    def _assert_live_baseline_fresh(
        self, connection: sqlite3.Connection, scope: AccountScope, now_ns: int
    ) -> sqlite3.Row:
        baseline = self._assert_live_baseline_bound(connection, scope)
        if not self._gross_facts_are_fresh(
            int(baseline["observed_at_ns"]), int(baseline["valid_until_ns"]), now_ns
        ):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_STALE", "LIVE_GROSS account baseline is stale or unbound"
            )

        return baseline

    def _assert_live_baseline_bound(
        self, connection: sqlite3.Connection, scope: AccountScope
    ) -> sqlite3.Row:
        baseline = connection.execute(
            "SELECT * FROM risk_gross_exposure_baselines WHERE scope_key = ?",
            (scope.key,),
        ).fetchone()
        if baseline is None:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_REQUIRED", "LIVE_GROSS has no verified account baseline"
            )
        if (
            baseline["policy_hash"] != self._policy.fingerprint
            or baseline["notional_unit"] != self._policy.notional_unit
            or baseline["account_active_increase_intent_count"] is None
        ):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_UNBOUND", "LIVE_GROSS account baseline is not bound"
            )
        unproven_strategy = connection.execute(
            "SELECT 1 FROM risk_gross_baseline_strategies "
            "WHERE scope_key = ? AND active_increase_intent_count IS NULL LIMIT 1",
            (scope.key,),
        ).fetchone()
        if unproven_strategy is not None:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_UNATTRIBUTED",
                "a strategy baseline has no source-proven occupied intent count",
            )
        return baseline

    def _live_gross_usage(
        self, connection: sqlite3.Connection, scope: AccountScope
    ) -> tuple[Decimal, int, dict[str, Decimal], dict[str, int]]:
        baseline = connection.execute(
            "SELECT account_gross_notional, account_active_increase_intent_count "
            "FROM risk_gross_exposure_baselines WHERE scope_key = ?",
            (scope.key,),
        ).fetchone()
        if baseline is None:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_REQUIRED", "LIVE_GROSS has no verified account baseline"
            )
        account_gross = Fraction(_as_decimal(baseline["account_gross_notional"]))
        if baseline["account_active_increase_intent_count"] is None:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_UNATTRIBUTED",
                "baseline has no source-proven occupied increase-intent count",
            )
        account_count = int(baseline["account_active_increase_intent_count"])
        strategy_gross = {
            str(row["strategy_id"]): Fraction(_as_decimal(row["gross_notional"]))
            for row in connection.execute(
                "SELECT strategy_id, gross_notional FROM risk_gross_baseline_strategies "
                "WHERE scope_key = ?",
                (scope.key,),
            ).fetchall()
        }
        strategy_count = {
            str(row["strategy_id"]): int(row["active_increase_intent_count"])
            for row in connection.execute(
                "SELECT strategy_id, active_increase_intent_count "
                "FROM risk_gross_baseline_strategies WHERE scope_key = ?",
                (scope.key,),
            ).fetchall()
        }
        if (
            sum(strategy_count.values()) != account_count
            or sum(strategy_gross.values(), Fraction(0)) != account_gross
        ):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_UNATTRIBUTED",
                "baseline account totals do not match the fully attributed strategy rows",
            )
        reservations = connection.execute(
            """SELECT reservation.permit_id, reservation.intent_id, reservation.strategy_id,
                      reservation.notional, exposure.permit_id AS exposure_permit_id
               FROM risk_reservations AS reservation
               LEFT JOIN risk_order_exposures AS exposure
                 ON exposure.permit_id = reservation.permit_id
               WHERE reservation.scope_key = ? AND reservation.action = ?
                 AND reservation.status IN (?, ?)""",
            (scope.key, IntentAction.INCREASE.value, self._ACTIVE, self._SETTLED),
        ).fetchall()
        for row in reservations:
            if row["exposure_permit_id"] is not None:
                continue
            strategy_id = row["strategy_id"]
            if strategy_id is None:
                raise RiskDeniedError(
                    "GROSS_EXPOSURE_STRATEGY_UNATTRIBUTED",
                    "LIVE_GROSS reservation has no strategy attribution",
                )
            amount = Fraction(_as_decimal(row["notional"]))
            account_gross += amount
            if amount > 0:
                account_count += 1
            strategy_gross[strategy_id] = strategy_gross.get(strategy_id, Fraction(0)) + amount
            if amount > 0:
                strategy_count[strategy_id] = strategy_count.get(strategy_id, 0) + 1
        exposures = connection.execute(
            """SELECT permit_id, strategy_id, filled_gross_notional, open_gross_notional
               FROM risk_order_exposures WHERE scope_key = ?""",
            (scope.key,),
        ).fetchall()
        for row in exposures:
            amount = Fraction(_as_decimal(row["filled_gross_notional"])) + Fraction(
                _as_decimal(row["open_gross_notional"])
            )
            account_gross += amount
            if amount > 0:
                account_count += 1
                strategy_id = str(row["strategy_id"])
                strategy_count[strategy_id] = strategy_count.get(strategy_id, 0) + 1
            strategy_id = str(row["strategy_id"])
            strategy_gross[strategy_id] = strategy_gross.get(strategy_id, Fraction(0)) + amount
        return (
            _decimal_from_fraction(account_gross),
            account_count,
            {
                strategy_id: _decimal_from_fraction(amount)
                for strategy_id, amount in strategy_gross.items()
            },
            strategy_count,
        )

    def _assert_live_gross_limits(
        self,
        connection: sqlite3.Connection,
        intent: RiskIntent,
        *,
        include_intent: bool = True,
        now_ns: Optional[int] = None,  # noqa: UP045 -- Python 3.9 is supported.
    ) -> None:
        # Gross valuations and proof intervals are integer-nanosecond facts.
        # Sampling the dedicated clock here avoids converting a seconds float
        # back to ns at admission and dispatch boundaries.
        if now_ns is None:
            now_ns = self._now_ns()
        self._assert_live_baseline_fresh(connection, intent.scope, now_ns)
        pending = connection.execute(
            """SELECT reservation.permit_id FROM risk_reservations AS reservation
               JOIN risk_dispatch_resolutions AS resolution
                 ON resolution.permit_id = reservation.permit_id
               LEFT JOIN risk_order_exposures AS exposure
                 ON exposure.permit_id = reservation.permit_id
               WHERE reservation.scope_key = ? AND reservation.action = ?
                 AND reservation.status = ? AND resolution.proof_kind = 'ACKED_TRACKED'
                 AND exposure.permit_id IS NULL LIMIT 1""",
            (intent.scope.key, IntentAction.INCREASE.value, self._SETTLED),
        ).fetchone()
        if pending is not None:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_RECONCILIATION_REQUIRED",
                "a tracked dispatched order has not been converted to a fresh observation",
            )
        for row in connection.execute(
            "SELECT * FROM risk_order_exposures WHERE scope_key = ?",
            (intent.scope.key,),
        ).fetchall():
            if any(
                row[name] is None
                for name in (
                    "instrument_metadata_observed_at_ns",
                    "instrument_metadata_valid_until_ns",
                    "valuation_observed_at_ns",
                    "valuation_valid_until_ns",
                    "fill_trade_count",
                    "fill_ids_sha256",
                    "order_side",
                    "position_effect",
                )
            ):
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_SCHEMA_UNPROVEN",
                    "an existing order row lacks required fill or valuation facts",
                )
            if _as_decimal(row["leaves_quantity"]) > 0 and row["leaves_valuation_price"] is None:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_SCHEMA_UNPROVEN",
                    "an existing open order lacks source-backed leaves valuation",
                )
            if row["state"] == OrderExposureState.UNKNOWN.value or not self._gross_facts_are_fresh(
                int(row["observed_at_ns"]), int(row["valid_until_ns"]), now_ns
            ):
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_RECONCILIATION_REQUIRED",
                    "an order exposure is unknown or stale; no wider increase is allowed",
                )
            max_age_ns = int(self._active_gross_age_seconds * 1_000_000_000)
            timestamp_facts = (
                (
                    int(row["instrument_metadata_observed_at_ns"]),
                    int(row["instrument_metadata_valid_until_ns"]),
                ),
                (int(row["valuation_observed_at_ns"]), int(row["valuation_valid_until_ns"])),
            )
            if any(
                observed_at_ns > now_ns
                or now_ns >= valid_until_ns
                or now_ns - observed_at_ns > max_age_ns
                for observed_at_ns, valid_until_ns in timestamp_facts
            ):
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_RECONCILIATION_REQUIRED",
                    "an order valuation fact is stale; no wider increase is allowed",
                )
            if row["quote_currency"] != row["valuation_unit"] and row["fx_pair"] is None:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_SCHEMA_UNPROVEN",
                    "an existing cross-currency order has no FX pair binding",
                )
            if row["fx_pair"] is not None:
                if any(
                    row[name] is None
                    for name in ("fx_rate", "fx_observed_at_ns", "fx_valid_until_ns")
                ):
                    raise RiskDeniedError(
                        "ORDER_EXPOSURE_SCHEMA_UNPROVEN",
                        "an existing cross-currency order lacks complete FX facts",
                    )
                fx_observed_at_ns = int(row["fx_observed_at_ns"])
                fx_valid_until_ns = int(row["fx_valid_until_ns"])
                if (
                    fx_observed_at_ns > now_ns
                    or now_ns >= fx_valid_until_ns
                    or now_ns - fx_observed_at_ns > max_age_ns
                ):
                    raise RiskDeniedError(
                        "ORDER_EXPOSURE_RECONCILIATION_REQUIRED",
                        "an order FX fact is stale; no wider increase is allowed",
                    )

        account_gross, account_count, strategy_gross, strategy_count = self._live_gross_usage(
            connection, intent.scope
        )
        extra_notional = intent.notional if include_intent else Decimal(0)
        extra_count = 1 if include_intent else 0
        if Fraction(account_gross) + Fraction(extra_notional) > Fraction(
            self._policy.max_gross_occupancy_notional
        ):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_ACCOUNT_LIMIT_EXHAUSTED",
                "account gross occupancy exceeds its explicit LIVE_GROSS cap",
            )
        if account_count + extra_count > self._policy.max_gross_occupancy_intent_count:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_ACCOUNT_COUNT_EXHAUSTED",
                "account occupied increase-intent count exceeds its LIVE_GROSS cap",
            )
        if intent.action is IntentAction.INCREASE:
            if intent.strategy_id is None:
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_REQUIRED", "LIVE_GROSS increase needs strategy identity"
                )
            allocation = connection.execute(
                "SELECT * FROM risk_strategy_allocations WHERE scope_key = ? AND strategy_id = ?",
                (intent.scope.key, intent.strategy_id),
            ).fetchone()
            if (
                allocation is None
                or allocation["max_gross_occupancy_notional"] is None
                or allocation["max_gross_occupancy_intent_count"] is None
                or allocation["notional_unit"] != self._policy.notional_unit
            ):
                raise RiskDeniedError(
                    "GROSS_EXPOSURE_STRATEGY_ALLOCATION_REQUIRED",
                    "strategy gross occupancy caps are not explicitly configured",
                )
            strategy_amount = Fraction(
                strategy_gross.get(intent.strategy_id, Decimal(0))
            ) + Fraction(extra_notional)
            strategy_intents = strategy_count.get(intent.strategy_id, 0) + extra_count
            if strategy_amount > Fraction(_as_decimal(allocation["max_gross_occupancy_notional"])):
                raise RiskDeniedError(
                    "GROSS_EXPOSURE_STRATEGY_LIMIT_EXHAUSTED",
                    "strategy gross occupancy exceeds its explicit cap",
                )
            if strategy_intents > int(allocation["max_gross_occupancy_intent_count"]):
                raise RiskDeniedError(
                    "GROSS_EXPOSURE_STRATEGY_COUNT_EXHAUSTED",
                    "strategy occupied increase-intent count exceeds its explicit cap",
                )

    def _gross_scope_over_limit(self, connection: sqlite3.Connection, scope: AccountScope) -> bool:
        account_gross, account_count, strategy_gross, strategy_count = self._live_gross_usage(
            connection, scope
        )
        if (
            account_gross > self._policy.max_gross_occupancy_notional
            or account_count > self._policy.max_gross_occupancy_intent_count
        ):
            return True
        for strategy_id, amount in strategy_gross.items():
            allocation = connection.execute(
                "SELECT max_gross_occupancy_notional, max_gross_occupancy_intent_count "
                "FROM risk_strategy_allocations WHERE scope_key = ? AND strategy_id = ?",
                (scope.key, strategy_id),
            ).fetchone()
            if (
                allocation is None
                or allocation["max_gross_occupancy_notional"] is None
                or allocation["max_gross_occupancy_intent_count"] is None
                or amount > _as_decimal(allocation["max_gross_occupancy_notional"])
                or strategy_count.get(strategy_id, 0)
                > int(allocation["max_gross_occupancy_intent_count"])
            ):
                return True
        return False

    def _assert_gross_baseline_refresh_safe(
        self, connection: sqlite3.Connection, scope: AccountScope
    ) -> None:
        exposure_rows = connection.execute(
            """SELECT filled_gross_notional, open_gross_notional
               FROM risk_order_exposures WHERE scope_key = ?""",
            (scope.key,),
        ).fetchall()
        if any(
            _as_decimal(row["filled_gross_notional"]) > 0
            or _as_decimal(row["open_gross_notional"]) > 0
            for row in exposure_rows
        ):
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_REFRESH_BLOCKED",
                "a refreshed baseline cannot absorb live or filled order exposure",
            )
        pending = connection.execute(
            """SELECT 1 FROM risk_reservations AS reservation
               LEFT JOIN risk_order_exposures AS exposure
                 ON exposure.permit_id = reservation.permit_id
               WHERE reservation.scope_key = ? AND reservation.action = ?
                 AND reservation.status IN (?, ?) AND exposure.permit_id IS NULL LIMIT 1""",
            (scope.key, IntentAction.INCREASE.value, self._ACTIVE, self._SETTLED),
        ).fetchone()
        unresolved = connection.execute(
            """SELECT 1 FROM risk_dispatch_claims AS claim
               LEFT JOIN risk_dispatch_resolutions AS resolution
                 ON resolution.permit_id = claim.permit_id
               WHERE claim.scope_key = ? AND resolution.permit_id IS NULL LIMIT 1""",
            (scope.key,),
        ).fetchone()
        if pending is not None or unresolved is not None:
            raise RiskDeniedError(
                "GROSS_EXPOSURE_BASELINE_REFRESH_BLOCKED",
                "baseline refresh requires no unobserved risk reservation or dispatch claim",
            )

    def _assert_terminal_observation_current(
        self, proof: DispatchExposureResolutionProof, now_ns: int
    ) -> None:
        observation = proof.observation
        if self._policy.exposure_accounting_mode is ExposureAccountingMode.LIVE_GROSS:
            self._assert_order_exposure_freshness(observation, now_ns)
            return
        intervals = (
            (observation.observed_at_ns, observation.valid_until_ns),
            (
                observation.instrument_metadata_observed_at_ns,
                observation.instrument_metadata_valid_until_ns,
            ),
            (observation.valuation_observed_at_ns, observation.valuation_valid_until_ns),
        )
        if observation.fx_observed_at_ns is not None:
            intervals += ((observation.fx_observed_at_ns, observation.fx_valid_until_ns),)
        if any(start > now_ns or end is None or now_ns >= end for start, end in intervals):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_OBSERVATION_STALE",
                "terminal order, metadata, valuation, or FX facts are outside their validity interval",
            )

    def _actual_claim_order_bounds(
        self, reservation: sqlite3.Row, claim_row: sqlite3.Row
    ) -> tuple[Decimal, Decimal]:
        """Return the gate-stored child bounds, or the singleton reservation bounds."""
        if claim_row is None:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_DISPATCH_CLAIM_REQUIRED",
                "order observation requires the exact durable dispatch claim",
            )
        if reservation["group_id"] is None:
            if claim_row["group_id"] is not None:
                raise RiskDeniedError(
                    "GROUP_STATE_CONFLICT", "singleton order has a grouped dispatch claim"
                )
            quantity, notional = reservation["quantity"], reservation["notional"]
        else:
            self._validate_group_claim_row(reservation, claim_row)
            if claim_row["actual_intent_json"] is None:
                raise RiskDeniedError(
                    "GROUP_CLAIM_STATE_INVALID", "group claim has no exact child intent"
                )
            actual = _risk_intent_from_json(str(claim_row["actual_intent_json"]))
            if (
                actual.action is not IntentAction.INCREASE
                or actual.scope.key != reservation["scope_key"]
                or actual.intent_id != reservation["intent_id"]
                or actual.instrument != reservation["instrument"]
                or actual.quantity_unit != reservation["quantity_unit"]
                or actual.notional_unit != reservation["notional_unit"]
                or actual.exposure_profile_fingerprint
                != claim_row["actual_exposure_profile_fingerprint"]
                or actual.quantity is None
                or actual.notional <= 0
            ):
                raise RiskDeniedError(
                    "GROUP_CLAIM_STATE_INVALID", "stored group child intent is inconsistent"
                )
            quantity, notional = claim_row["actual_quantity"], claim_row["actual_notional"]
            if (
                _as_decimal(quantity) != actual.quantity
                or _as_decimal(notional) != actual.notional
            ):
                raise RiskDeniedError(
                    "GROUP_CLAIM_STATE_INVALID", "stored group child size changed"
                )
        if quantity is None or notional is None:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_REQUEST_MISMATCH", "admitted order has no exact quantity/notional"
            )
        return _as_decimal(quantity), _as_decimal(notional)

    def _assert_terminal_order_matches_actual_claim(
        self,
        proof: OrderExposureObservationProof,
        reservation: sqlite3.Row,
        claim_row: sqlite3.Row,
    ) -> None:
        quantity, _notional = self._actual_claim_order_bounds(reservation, claim_row)
        if (
            proof.requested_quantity != quantity
            or proof.instrument_id != reservation["instrument"]
            or (
                reservation["notional_unit"] is not None
                and (
                    proof.notional_unit != reservation["notional_unit"]
                    or proof.valuation_unit != reservation["notional_unit"]
                )
            )
            or (
                self._policy.notional_unit is not None
                and proof.valuation_unit != self._policy.notional_unit
            )
            or proof.position_effect != "OPEN"
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_REQUEST_MISMATCH",
                "terminal order facts differ from the exact admitted child request",
            )

    def _tracked_order_binding(
        self, permit_id: str
    ) -> tuple[sqlite3.Row, DispatchClaimBinding, DispatchTrackedOrderProof]:
        with self._connection() as connection:
            return self._tracked_order_binding_in(connection, permit_id)

    def _tracked_order_binding_in(
        self, connection: sqlite3.Connection, permit_id: str
    ) -> tuple[sqlite3.Row, DispatchClaimBinding, DispatchTrackedOrderProof]:
        reservation = connection.execute(
            "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if reservation is None:
            raise RiskDeniedError("PERMIT_UNKNOWN", "tracked exposure permit does not exist")
        if (
            reservation["action"] != IntentAction.INCREASE.value
            or reservation["status"] != self._SETTLED
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_REQUIRES_SETTLED_INCREASE",
                "order exposure requires an exact settled increase permit",
            )
        claim_row = connection.execute(
            "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        resolution = connection.execute(
            "SELECT * FROM risk_dispatch_resolutions WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if claim_row is None or resolution is None or resolution["proof_kind"] != "ACKED_TRACKED":
            raise RiskDeniedError(
                "ORDER_EXPOSURE_ACKED_TRACKING_REQUIRED",
                "only an ACKED_TRACKED dispatch resolution can enter exposure conversion",
            )
        claim = self._dispatch_claim_binding(reservation, claim_row)
        tracked = DispatchTrackedOrderProof(
            scope=claim.scope,
            permit_id=str(resolution["permit_id"]),
            intent_id=str(resolution["intent_id"]),
            intent_hash=str(resolution["intent_hash"]),
            cause_id=str(resolution["cause_id"]),
            claim_digest=str(resolution["claim_digest"]),
            evidence_class=DispatchEvidenceClass(str(resolution["evidence_class"])),
            dispatch_attempt_count=int(resolution["dispatch_attempt_count"]),
            journal_revision=int(resolution["journal_revision"]),
            journal_record_sha256=str(resolution["journal_record_sha256"]),
            reconciliation_evidence_sha256=str(resolution["reconciliation_evidence_sha256"]),
            writer_fence_sha256=str(resolution["writer_fence_sha256"]),
            provider_order_id=str(resolution["provider_order_id"]),
            accepted_request_sha256=str(resolution["accepted_request_sha256"]),
            exposure_reservation_id=str(resolution["exposure_reservation_id"]),
            exposure_reservation_sha256=str(resolution["exposure_reservation_sha256"]),
            filled_quantity=int(resolution["filled_quantity"]),
            trade_count=int(resolution["trade_count"]),
        )
        return reservation, claim, tracked

    @staticmethod
    def _assert_order_exposure_proof_matches_binding(
        proof: OrderExposureObservationProof,
        claim: DispatchClaimBinding,
        tracked: DispatchTrackedOrderProof,
    ) -> None:
        if not all(
            (
                proof.scope == claim.scope == tracked.scope,
                proof.permit_id == claim.permit_id == tracked.permit_id,
                proof.intent_id == claim.intent_id == tracked.intent_id,
                proof.intent_hash == claim.intent_hash == tracked.intent_hash,
                proof.dispatch_claim_digest == claim.claim_digest == tracked.claim_digest,
                proof.dispatch_proof_sha256 == tracked.fingerprint,
                proof.provider_order_id == tracked.provider_order_id,
                proof.accepted_request_sha256 == tracked.accepted_request_sha256,
                proof.exposure_reservation_id == tracked.exposure_reservation_id,
                proof.exposure_reservation_sha256 == tracked.exposure_reservation_sha256,
            )
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_DISPATCH_BINDING_MISMATCH",
                "observation does not bind the exact settled dispatch claim and ACKED order",
            )

    def _assert_order_observation_identity(
        self,
        proof: OrderExposureObservationProof,
        reservation: sqlite3.Row,
        *,
        claim_row: sqlite3.Row,
    ) -> None:
        expected_quantity, _expected_notional = self._actual_claim_order_bounds(
            reservation, claim_row
        )
        if (
            proof.scope.key != reservation["scope_key"]
            or proof.intent_id != reservation["intent_id"]
            or proof.intent_hash != reservation["intent_hash"]
            or proof.instrument_id != reservation["instrument"]
            or (
                reservation["notional_unit"] is not None
                and proof.notional_unit != reservation["notional_unit"]
            )
            or proof.requested_quantity != expected_quantity
        ):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_REQUEST_MISMATCH",
                "observation quantity/instrument/unit differ from the admitted request",
            )

    @staticmethod
    def _same_order_revision_valuation_refresh(
        proof: OrderExposureObservationProof, prior: sqlite3.Row
    ) -> bool:
        """Allow newer valuation evidence only when provider order facts are unchanged."""
        if prior["fill_trade_count"] is None or prior["fill_ids_sha256"] is None:
            return False
        return all(
            (
                prior["state"] == proof.state.value,
                prior["requested_quantity"]
                == _canonical_decimal_text(proof.requested_quantity),
                prior["cumulative_filled_quantity"]
                == _canonical_decimal_text(proof.cumulative_filled_quantity),
                prior["leaves_quantity"] == _canonical_decimal_text(proof.leaves_quantity),
                int(prior["fill_trade_count"]) == proof.fill_trade_count,
                prior["fill_ids_sha256"] == proof.fill_ids_sha256,
            )
        )

    @staticmethod
    def _assert_order_contract_stable(
        proof: OrderExposureObservationProof, prior: sqlite3.Row
    ) -> None:
        immutable_fields = (
            ("provider_order_id", proof.provider_order_id),
            ("exposure_reservation_id", proof.exposure_reservation_id),
            ("exposure_reservation_sha256", proof.exposure_reservation_sha256),
            ("dispatch_claim_digest", proof.dispatch_claim_digest),
            ("dispatch_proof_sha256", proof.dispatch_proof_sha256),
            ("accepted_request_sha256", proof.accepted_request_sha256),
            ("source_contract_ref", proof.source_contract_ref),
            ("source_contract_version", proof.source_contract_version),
            ("provider_profile_version", proof.provider_profile_version),
            ("normalizer_schema_version", proof.normalizer_schema_version),
            ("source_alias_map_sha256", proof.source_alias_map_sha256),
            ("order_side", proof.order_side),
            ("position_effect", proof.position_effect),
            ("instrument_id", proof.instrument_id),
            ("instrument_version", proof.instrument_version),
            ("instrument_multiplier", _canonical_decimal_text(proof.instrument_multiplier)),
            ("quote_currency", proof.quote_currency),
            ("valuation_unit", proof.valuation_unit),
            ("instrument_metadata_sha256", proof.instrument_metadata_sha256),
            ("notional_unit", proof.notional_unit),
        )
        if any(str(prior[name]) != value for name, value in immutable_fields):
            raise RiskDeniedError(
                "ORDER_EXPOSURE_CONTRACT_CHANGED",
                "order source, contract, side, or valuation denomination changed",
            )

    @staticmethod
    def _assert_order_fills_extend_prior(
        connection: sqlite3.Connection,
        proof: OrderExposureObservationProof,
        prior: Optional[sqlite3.Row],  # noqa: UP045 -- Python 3.9 is supported.
    ) -> None:
        previous = ()
        if prior is not None:
            previous = connection.execute(
                "SELECT * FROM risk_order_exposure_fills WHERE permit_id = ? ORDER BY trade_id",
                (proof.permit_id,),
            ).fetchall()
            previous_count = prior["fill_trade_count"]
            previous_ids_sha = prior["fill_ids_sha256"]
            if previous_count is None or previous_ids_sha is None:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_SCHEMA_UNPROVEN",
                    "prior exposure row lacks immutable fill identity metadata",
                )
            previous_ids = tuple(str(row["trade_id"]) for row in previous)
            if (
                len(previous) != int(previous_count)
                or fill_ids_sha256_from_ids(previous_ids) != previous_ids_sha
            ):
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_FILL_STORE_UNPROVEN",
                    "persisted fills do not match the last accepted complete fill set",
                )
        current = {fact.trade_id: fact for fact in proof.fills}
        for row in previous:
            fact = current.get(str(row["trade_id"]))
            if fact is None or fact.fingerprint != row["fact_sha256"]:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_FILL_IDENTITY_CONFLICT",
                    "a previously accepted immutable fill is missing or changed",
                )

    def _persist_order_fill_facts(
        self, connection: sqlite3.Connection, proof: OrderExposureObservationProof
    ) -> None:
        self._occupy_trade_identities(connection, proof)
        for fact in proof.fills:
            connection.execute(
                """INSERT OR IGNORE INTO risk_order_exposure_fills (
                       permit_id, trade_id, fact_sha256, quantity, price, side,
                       position_effect, fee_amount, fee_currency,
                       source_record_sha256, source_revision
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proof.permit_id,
                    fact.trade_id,
                    fact.fingerprint,
                    _canonical_decimal_text(fact.quantity),
                    _canonical_decimal_text(fact.price),
                    fact.side,
                    fact.position_effect,
                    _canonical_decimal_text(fact.fee_amount),
                    fact.fee_currency,
                    fact.source_record_sha256,
                    fact.source_revision,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM risk_order_exposure_fills WHERE permit_id = ? AND trade_id = ?",
                (proof.permit_id, fact.trade_id),
            ).fetchone()
            assert stored is not None
            expected = (
                fact.fingerprint,
                _canonical_decimal_text(fact.quantity),
                _canonical_decimal_text(fact.price),
                fact.side,
                fact.position_effect,
                _canonical_decimal_text(fact.fee_amount),
                fact.fee_currency,
                fact.source_record_sha256,
                fact.source_revision,
            )
            actual = (
                stored["fact_sha256"],
                stored["quantity"],
                stored["price"],
                stored["side"],
                stored["position_effect"],
                stored["fee_amount"],
                stored["fee_currency"],
                stored["source_record_sha256"],
                int(stored["source_revision"]),
            )
            if actual != expected:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_FILL_IDENTITY_CONFLICT",
                    "fill identity was reused with different immutable facts",
                )

    def _occupy_trade_identities(
        self, connection: sqlite3.Connection, proof: OrderExposureObservationProof
    ) -> None:
        """Atomically claim declared DIM01 trade identities for this permit.

        This runs inside the same transaction as the fill persistence of both
        the incremental post-ACK path and the terminal first-fill path.  A
        proof that declares no identity domain keeps the legacy semantics.  A
        declared domain fixes its scope mode on first use, every fill identity
        is claimed at most once with immutable content, and legacy fill rows
        without a trusted domain mapping refuse the new claim instead of
        guessing a domain or picking a winner.
        """

        domain_ref = proof.trade_id_domain_ref
        if domain_ref is None:
            return
        identity_scope = "" if proof.trade_id_scope == "ACCOUNT" else proof.instrument_id
        bound = connection.execute(
            "SELECT trade_id_scope, scope_key FROM risk_trade_identity_domains "
            "WHERE trade_id_domain_ref = ?",
            (domain_ref,),
        ).fetchone()
        if bound is None:
            connection.execute(
                "INSERT INTO risk_trade_identity_domains "
                "(trade_id_domain_ref, trade_id_scope, scope_key, bound_at) "
                "VALUES (?, ?, ?, ?)",
                (domain_ref, proof.trade_id_scope, proof.scope.key, self._clock()),
            )
        else:
            if str(bound["trade_id_scope"]) != proof.trade_id_scope:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_TRADE_IDENTITY_MODE_MISMATCH",
                    "the trade identity domain is already fixed to another scope mode",
                )
            if str(bound["scope_key"]) != proof.scope.key:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_TRADE_IDENTITY_DOMAIN_REUSED",
                    "the trade identity domain is already bound to another account scope",
                )
        for fact in proof.fills:
            claim = connection.execute(
                "SELECT * FROM risk_trade_identity_claims "
                "WHERE trade_id_domain_ref = ? AND identity_scope = ? AND trade_id = ?",
                (domain_ref, identity_scope, fact.trade_id),
            ).fetchone()
            if claim is not None:
                if (
                    str(claim["permit_id"]) != proof.permit_id
                    or str(claim["scope_key"]) != proof.scope.key
                    or str(claim["fact_sha256"]) != fact.fingerprint
                ):
                    raise RiskDeniedError(
                        "ORDER_EXPOSURE_TRADE_IDENTITY_CONFLICT",
                        "the declared trade identity is already occupied by other facts",
                    )
                continue
            legacy = connection.execute(
                "SELECT fill.permit_id FROM risk_order_exposure_fills AS fill "
                "JOIN risk_order_exposures AS exposure USING (permit_id) "
                "WHERE fill.trade_id = ? AND fill.permit_id != ? "
                "AND exposure.scope_key = ? "
                "AND NOT EXISTS ("
                "SELECT 1 FROM risk_trade_identity_claims AS claim "
                "WHERE claim.permit_id = fill.permit_id AND claim.trade_id = fill.trade_id"
                ") LIMIT 1",
                (fact.trade_id, proof.permit_id, proof.scope.key),
            ).fetchone()
            if legacy is not None:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_TRADE_IDENTITY_UNMAPPED",
                    "a legacy fill identity has no trusted trade identity domain mapping",
                )
            connection.execute(
                "INSERT INTO risk_trade_identity_claims ("
                "trade_id_domain_ref, identity_scope, trade_id, scope_key, "
                "permit_id, instrument_id, fact_sha256, claimed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    domain_ref,
                    identity_scope,
                    fact.trade_id,
                    proof.scope.key,
                    proof.permit_id,
                    proof.instrument_id,
                    fact.fingerprint,
                    self._clock(),
                ),
            )

    @staticmethod
    def _dispatch_latch_active(
        connection: sqlite3.Connection, scope: AccountScope, cause_id: str
    ) -> bool:
        row = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
            (scope.key, cause_id),
        ).fetchone()
        return row is not None and int(row["active"]) == 1

    @staticmethod
    def _gross_unknown_cause(permit_id: str) -> str:
        return "gross-order-unknown:" + permit_id

    @staticmethod
    def _filled_gross_from_fill_facts(proof: OrderExposureObservationProof) -> Decimal:
        fx_rate = Fraction(1) if proof.fx_rate is None else Fraction(proof.fx_rate)
        multiplier = Fraction(proof.instrument_multiplier)
        total = sum(
            (
                Fraction(fact.quantity) * Fraction(fact.price) * multiplier * fx_rate
                for fact in proof.fills
            ),
            Fraction(0),
        )
        return _decimal_upper_bound_from_fraction(total)

    @staticmethod
    def _leaves_gross_from_price(proof: OrderExposureObservationProof) -> Decimal:
        if proof.leaves_quantity == 0:
            return Decimal(0)
        if proof.leaves_valuation_price is None:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_LEAVES_PRICE_REQUIRED",
                "open leaves require a source-backed valuation price",
            )
        fx_rate = Fraction(1) if proof.fx_rate is None else Fraction(proof.fx_rate)
        gross = (
            Fraction(proof.leaves_quantity)
            * Fraction(proof.leaves_valuation_price)
            * Fraction(proof.instrument_multiplier)
            * fx_rate
        )
        return _decimal_upper_bound_from_fraction(gross)

    def _apply_order_exposure_in_transaction(
        self,
        connection: sqlite3.Connection,
        proof: OrderExposureObservationProof,
        reservation: sqlite3.Row,
        prior: Optional[sqlite3.Row],  # noqa: UP045 -- Python 3.9 is supported.
        now_ns: int,
        *,
        dispatch_proof_sha256: Optional[str] = None,  # noqa: UP045 -- Python 3.9 is supported.
    ) -> OrderExposureSnapshot:
        claim_row = connection.execute(
            "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?",
            (proof.permit_id,),
        ).fetchone()
        requested, original_notional = self._actual_claim_order_bounds(
            reservation, claim_row
        )
        if prior is None:
            previous_filled_quantity = Decimal(0)
            previous_leaves_quantity = requested
            previous_filled_gross = Decimal(0)
            previous_open_gross = original_notional
            previous_state = None
        else:
            previous_filled_quantity = _as_decimal(prior["cumulative_filled_quantity"])
            previous_leaves_quantity = _as_decimal(prior["leaves_quantity"])
            previous_filled_gross = _as_decimal(prior["filled_gross_notional"])
            previous_open_gross = _as_decimal(prior["open_gross_notional"])
            previous_state = OrderExposureState(str(prior["state"]))

        if proof.state is OrderExposureState.UNKNOWN:
            filled_quantity = previous_filled_quantity
            leaves_quantity = previous_leaves_quantity
            filled_gross = previous_filled_gross
            conservative_remaining = max(
                Fraction(0),
                Fraction(original_notional) - Fraction(previous_filled_gross),
            )
            open_gross = max(
                previous_open_gross,
                _decimal_upper_bound_from_fraction(conservative_remaining),
            )
        else:
            filled_quantity = proof.cumulative_filled_quantity
            leaves_quantity = proof.leaves_quantity
            fill_gross_from_facts = self._filled_gross_from_fill_facts(proof)
            leaves_gross_from_price = self._leaves_gross_from_price(proof)
            if filled_quantity < previous_filled_quantity:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_FILL_REGRESSION", "cumulative fill quantity regressed"
                )
            if previous_state in (
                OrderExposureState.FILLED,
                OrderExposureState.CANCELLED,
                OrderExposureState.REJECTED,
            ) and proof.state not in (
                OrderExposureState.FILLED,
                OrderExposureState.CANCELLED,
                OrderExposureState.REJECTED,
            ):
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_TERMINAL_REGRESSION", "terminal provider order became open"
                )
            if proof.state is OrderExposureState.ACKED and filled_quantity != 0:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_STATE_MISMATCH", "ACKED requires zero cumulative fills"
                )
            if proof.state is OrderExposureState.PARTIALLY_FILLED and (
                filled_quantity <= 0 or leaves_quantity <= 0
            ):
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_STATE_MISMATCH", "partial fill requires fills and open leaves"
                )
            if proof.state is OrderExposureState.FILLED and filled_quantity != requested:
                raise RiskDeniedError(
                    "ORDER_EXPOSURE_STATE_MISMATCH", "FILLED must cover the requested quantity"
                )

            if leaves_quantity == 0:
                open_gross = Decimal(0)
            else:
                old_open_share = Fraction(0)
                if previous_leaves_quantity > 0:
                    old_open_fraction = Fraction(previous_open_gross)
                    fraction_left = Fraction(leaves_quantity) / Fraction(previous_leaves_quantity)
                    old_open_share = old_open_fraction * fraction_left
                proportional_floor = _decimal_upper_bound_from_fraction(
                    Fraction(original_notional) * Fraction(leaves_quantity) / Fraction(requested)
                )
                open_gross = max(
                    proof.leaves_gross_notional,
                    leaves_gross_from_price,
                    proportional_floor,
                    _decimal_upper_bound_from_fraction(old_open_share),
                )
            filled_floor = _decimal_upper_bound_from_fraction(
                Fraction(original_notional) * Fraction(filled_quantity) / Fraction(requested)
            )
            transferred_floor = Fraction(previous_filled_gross)
            if filled_quantity > previous_filled_quantity and previous_leaves_quantity > 0:
                transferred_floor += (
                    Fraction(previous_open_gross)
                    * Fraction(filled_quantity - previous_filled_quantity)
                    / Fraction(previous_leaves_quantity)
                )
            filled_gross = max(
                previous_filled_gross,
                proof.filled_gross_notional,
                fill_gross_from_facts,
                filled_floor,
                _decimal_upper_bound_from_fraction(transferred_floor),
            )

        values: dict[str, object] = {
            "permit_id": proof.permit_id,
            "scope_key": proof.scope.key,
            "intent_id": proof.intent_id,
            "strategy_id": str(reservation["strategy_id"]),
            "provider_order_id": proof.provider_order_id,
            "exposure_reservation_id": proof.exposure_reservation_id,
            "exposure_reservation_sha256": proof.exposure_reservation_sha256,
            "dispatch_claim_digest": proof.dispatch_claim_digest,
            "dispatch_proof_sha256": (
                proof.dispatch_proof_sha256
                if dispatch_proof_sha256 is None
                else _require_sha256(dispatch_proof_sha256, "dispatch_proof_sha256")
            ),
            "accepted_request_sha256": proof.accepted_request_sha256,
            "order_revision": (
                proof.order_revision
                if prior is None
                else max(proof.order_revision, int(prior["order_revision"]))
            ),
            "journal_revision": proof.journal_revision,
            "journal_record_sha256": proof.journal_record_sha256,
            "observation_sha256": proof.observation_sha256,
            "fill_history_sha256": proof.fill_history_sha256,
            "source_alias_map_sha256": proof.source_alias_map_sha256,
            "proof_sha256": proof.fingerprint,
            "state": proof.state.value,
            "requested_quantity": _canonical_decimal_text(requested),
            "cumulative_filled_quantity": _canonical_decimal_text(filled_quantity),
            "leaves_quantity": _canonical_decimal_text(leaves_quantity),
            "leaves_valuation_price": (
                None
                if proof.leaves_valuation_price is None
                else _canonical_decimal_text(proof.leaves_valuation_price)
            ),
            "filled_gross_notional": _canonical_decimal_text(filled_gross),
            "open_gross_notional": _canonical_decimal_text(open_gross),
            "notional_unit": proof.notional_unit,
            "complete_order_snapshot": int(proof.complete_order_snapshot),
            "complete_fill_history": int(proof.complete_fill_history),
            "fill_watermark_revision": (
                proof.fill_watermark_revision
                if proof.state is not OrderExposureState.UNKNOWN or prior is None
                else int(prior["fill_watermark_revision"])
            ),
            "source_contract_ref": proof.source_contract_ref,
            "source_contract_version": proof.source_contract_version,
            "provider_profile_version": proof.provider_profile_version,
            "normalizer_schema_version": proof.normalizer_schema_version,
            "order_side": proof.order_side,
            "position_effect": proof.position_effect,
            "instrument_id": proof.instrument_id,
            "instrument_version": proof.instrument_version,
            "instrument_multiplier": _canonical_decimal_text(proof.instrument_multiplier),
            "quote_currency": proof.quote_currency,
            "valuation_unit": proof.valuation_unit,
            "instrument_metadata_sha256": proof.instrument_metadata_sha256,
            "instrument_metadata_observed_at_ns": proof.instrument_metadata_observed_at_ns,
            "instrument_metadata_valid_until_ns": proof.instrument_metadata_valid_until_ns,
            "valuation_facts_sha256": proof.valuation_facts_sha256,
            "valuation_observed_at_ns": proof.valuation_observed_at_ns,
            "valuation_valid_until_ns": proof.valuation_valid_until_ns,
            "fill_trade_count": (
                proof.fill_trade_count
                if proof.state is not OrderExposureState.UNKNOWN
                else 0
                if prior is None
                else int(prior["fill_trade_count"])
            ),
            "fill_ids_sha256": (
                proof.fill_ids_sha256
                if proof.state is not OrderExposureState.UNKNOWN
                else fill_ids_sha256(())
                if prior is None
                else str(prior["fill_ids_sha256"])
            ),
            "fx_pair": proof.fx_pair,
            "fx_rate": None if proof.fx_rate is None else _canonical_decimal_text(proof.fx_rate),
            "fx_observed_at_ns": proof.fx_observed_at_ns,
            "fx_valid_until_ns": proof.fx_valid_until_ns,
            "observed_at_ns": proof.observed_at_ns,
            "valid_until_ns": proof.valid_until_ns,
            "writer_owner_id": proof.writer_owner_id,
            "writer_fencing_token": proof.writer_fencing_token,
            "writer_fence_sha256": proof.writer_fence_sha256,
        }
        existing_order = connection.execute(
            """SELECT permit_id FROM risk_order_exposures
               WHERE scope_key = ? AND provider_order_id = ? AND permit_id != ? LIMIT 1""",
            (proof.scope.key, proof.provider_order_id, proof.permit_id),
        ).fetchone()
        if existing_order is not None:
            raise RiskDeniedError(
                "ORDER_EXPOSURE_PROVIDER_ID_REUSED",
                "provider order identity is already bound to another Risk permit",
            )
        columns = tuple(values)
        update = ", ".join(name + " = excluded." + name for name in columns if name != "permit_id")
        connection.execute(
            "INSERT INTO risk_order_exposures ("  # noqa: S608 -- projection columns are fixed typed keys.
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ") ON CONFLICT(permit_id) DO UPDATE SET "
            + update,
            tuple(values[name] for name in columns),
        )
        row = connection.execute(
            "SELECT * FROM risk_order_exposures WHERE permit_id = ?", (proof.permit_id,)
        ).fetchone()
        assert row is not None
        return self._order_exposure_snapshot(row, proof.scope)

    @staticmethod
    def _order_exposure_snapshot(row: sqlite3.Row, scope: AccountScope) -> OrderExposureSnapshot:
        return OrderExposureSnapshot(
            scope=scope,
            permit_id=str(row["permit_id"]),
            intent_id=str(row["intent_id"]),
            order_revision=int(row["order_revision"]),
            journal_revision=int(row["journal_revision"]),
            state=OrderExposureState(str(row["state"])),
            requested_quantity=_as_decimal(row["requested_quantity"]),
            cumulative_filled_quantity=_as_decimal(row["cumulative_filled_quantity"]),
            leaves_quantity=_as_decimal(row["leaves_quantity"]),
            filled_gross_notional=_as_decimal(row["filled_gross_notional"]),
            open_gross_notional=_as_decimal(row["open_gross_notional"]),
            leaves_valuation_price=(
                None
                if row["leaves_valuation_price"] is None
                else _as_decimal(row["leaves_valuation_price"])
            ),
            notional_unit=str(row["notional_unit"]),
            proof_sha256=str(row["proof_sha256"]),
            observed_at_ns=int(row["observed_at_ns"]),
            valid_until_ns=int(row["valid_until_ns"]),
        )

    def _policy_binding_facts(self) -> tuple[object, ...]:
        """Return every policy fact that changes the meaning of one scope ledger."""

        base = (
            self._policy.fingerprint,
            self._policy.policy_id,
            _decimal_text(self._policy.max_increase_notional),
            self._policy.max_increase_count,
            format(float(self._policy.permit_ttl_seconds), ".17g"),
            int(self._policy.require_strategy_allocation),
            self._policy.notional_unit,
        )
        if self._policy.exposure_accounting_mode is ExposureAccountingMode.RESERVATIONS_ONLY:
            # Keep legacy binding facts exactly as they were serialized before
            # LIVE_GROSS existed; added SQL columns remain NULL for this mode.
            return base + (None, None, None, None, None)
        return base + (
            ExposureAccountingMode.LIVE_GROSS.value,
            2,
            _decimal_text(self._policy.max_gross_occupancy_notional),
            self._policy.max_gross_occupancy_intent_count,
            format(float(self._policy.max_exposure_facts_age_seconds), ".17g"),
        )

    def _policy_binding_read_error(
        self, connection: sqlite3.Connection, scope_key: str
    ) -> Optional[str]:  # noqa: UP045 -- Python 3.9 is supported.
        """Check a policy binding without creating or modifying one."""

        facts = self._policy_binding_facts()
        row = connection.execute(
            "SELECT * FROM risk_policy_bindings WHERE scope_key = ?", (scope_key,)
        ).fetchone()
        if row is not None:
            stored = (
                row["policy_hash"],
                row["policy_id"],
                row["max_increase_notional"],
                row["max_increase_count"],
                row["permit_ttl_seconds"],
                row["require_strategy_allocation"],
                row["notional_unit"],
                row["exposure_accounting_mode"],
                row["exposure_schema_version"],
                row["max_gross_occupancy_notional"],
                row["max_gross_occupancy_intent_count"],
                row["max_exposure_facts_age_seconds"],
            )
            return None if stored == facts else "RISK_POLICY_BINDING_MISMATCH"

        historical = connection.execute(
            "SELECT DISTINCT policy_hash FROM risk_reservations WHERE scope_key = ?",
            (scope_key,),
        ).fetchall()
        hashes = {str(item["policy_hash"]) for item in historical}
        if len(hashes) > 1:
            return "RISK_POLICY_BINDING_UNAVAILABLE"
        if hashes:
            return (
                None
                if next(iter(hashes)) == self._policy.fingerprint
                else "RISK_POLICY_BINDING_MISMATCH"
            )
        has_allocation = connection.execute(
            "SELECT 1 FROM risk_strategy_allocations WHERE scope_key = ? LIMIT 1",
            (scope_key,),
        ).fetchone()
        if has_allocation is not None:
            return "RISK_POLICY_BINDING_UNAVAILABLE"
        return "RISK_POLICY_BINDING_UNAVAILABLE"

    def _ensure_policy_binding(self, connection: sqlite3.Connection, scope_key: str) -> None:
        """Persist or enforce the immutable policy selected for an account ledger.

        This is called only while ``BEGIN IMMEDIATE`` is held. Existing history
        is never interpreted under a different policy, and there is deliberately
        no in-place rotation or permissive-policy selection path.
        """

        facts = self._policy_binding_facts()
        row = connection.execute(
            "SELECT * FROM risk_policy_bindings WHERE scope_key = ?", (scope_key,)
        ).fetchone()
        if row is not None:
            stored = (
                row["policy_hash"],
                row["policy_id"],
                row["max_increase_notional"],
                row["max_increase_count"],
                row["permit_ttl_seconds"],
                row["require_strategy_allocation"],
                row["notional_unit"],
                row["exposure_accounting_mode"],
                row["exposure_schema_version"],
                row["max_gross_occupancy_notional"],
                row["max_gross_occupancy_intent_count"],
                row["max_exposure_facts_age_seconds"],
            )
            if stored != facts:
                raise RiskDeniedError(
                    "RISK_POLICY_BINDING_MISMATCH",
                    "account ledger is bound to another immutable risk policy",
                )
            return

        historical = connection.execute(
            "SELECT DISTINCT policy_hash FROM risk_reservations WHERE scope_key = ?",
            (scope_key,),
        ).fetchall()
        hashes = {str(item["policy_hash"]) for item in historical}
        if len(hashes) > 1:
            raise RiskDeniedError(
                "RISK_POLICY_BINDING_UNAVAILABLE",
                "legacy account ledger contains conflicting policy fingerprints",
            )
        if hashes and next(iter(hashes)) != self._policy.fingerprint:
            raise RiskDeniedError(
                "RISK_POLICY_BINDING_MISMATCH",
                "legacy account ledger was created under another risk policy",
            )
        has_allocation = connection.execute(
            "SELECT 1 FROM risk_strategy_allocations WHERE scope_key = ? LIMIT 1",
            (scope_key,),
        ).fetchone()
        if has_allocation is not None and not hashes:
            raise RiskDeniedError(
                "RISK_POLICY_BINDING_UNAVAILABLE",
                "legacy strategy allocation has no policy history to verify",
            )

        connection.execute(
            """INSERT INTO risk_policy_bindings (
                   scope_key, policy_hash, policy_id, max_increase_notional,
                   max_increase_count, permit_ttl_seconds,
                   require_strategy_allocation, notional_unit,
                   exposure_accounting_mode, exposure_schema_version,
                   max_gross_occupancy_notional, max_gross_occupancy_intent_count,
                   max_exposure_facts_age_seconds, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scope_key,
                *facts,
                float(self._clock()),
            ),
        )

    def _assert_intent_notional_unit(self, intent: RiskIntent) -> None:
        """Require one policy-owned denomination before summing notional amounts."""

        if self._policy.notional_unit is None:
            if self._policy.require_strategy_allocation or intent.notional_unit is not None:
                raise RiskDeniedError(
                    "NOTIONAL_UNIT_UNBOUND", "account policy has no fixed notional unit"
                )
            return
        if intent.notional_unit != self._policy.notional_unit:
            raise RiskDeniedError(
                "NOTIONAL_UNIT_MISMATCH",
                "intent notional unit does not match the account policy",
            )

    def _assert_margin_pool_limit(self, connection: sqlite3.Connection, intent: RiskIntent) -> None:
        """Enforce one declared margin-pool ceiling inside the write transaction.

        Only intents that carry a trusted ``AccountResourceKey`` participate;
        keyless intents (and every legacy row) keep their established behavior
        untouched.  Usage counts every live INCREASE reservation in the same
        account and pool at its exact reserved notional, which is the trusted
        mapper's already-valued (FX-converted) maximum, so this path never
        re-derives FX arithmetic.  The check shares the caller's
        ``BEGIN IMMEDIATE`` transaction, so legs of one vector and contenders
        from other writers are accounted atomically before any insert commits.
        """

        resource = intent.resource
        if resource is None or intent.action is not IntentAction.INCREASE:
            return
        limit = connection.execute(
            """SELECT max_notional, notional_unit FROM risk_margin_pool_limits
               WHERE scope_key = ? AND product_type = ? AND margin_pool = ?""",
            (intent.scope.key, resource.product_type, resource.margin_pool),
        ).fetchone()
        if limit is None:
            raise RiskDeniedError(
                "MARGIN_POOL_UNCONFIGURED",
                "intent declares a margin-pool resource without an installed limit",
            )
        if intent.notional_unit != limit["notional_unit"]:
            raise RiskDeniedError(
                "NOTIONAL_UNIT_MISMATCH",
                "intent notional unit does not match the installed pool limit",
            )
        rows = connection.execute(
            """SELECT notional, notional_unit FROM risk_reservations
               WHERE scope_key = ? AND resource_product_type = ? AND resource_margin_pool = ?
                 AND action = ? AND status IN (?, ?)""",
            (
                intent.scope.key,
                resource.product_type,
                resource.margin_pool,
                IntentAction.INCREASE.value,
                self._ACTIVE,
                self._SETTLED,
            ),
        ).fetchall()
        if any(row["notional_unit"] != limit["notional_unit"] for row in rows):
            raise RiskDeniedError(
                "MARGIN_POOL_UNIT_USAGE_UNAVAILABLE",
                "existing pool reservations do not share the installed pool notional unit",
            )
        reserved_notional = sum(
            (Fraction(_as_decimal(row["notional"])) for row in rows), Fraction(0)
        )
        if reserved_notional + Fraction(intent.notional) > Fraction(
            _as_decimal(limit["max_notional"])
        ):
            raise RiskDeniedError(
                "MARGIN_POOL_LIMIT_EXHAUSTED",
                "margin-pool increase notional limit exceeded",
            )

    def _assert_strategy_allocation_limit(
        self, connection: sqlite3.Connection, intent: RiskIntent
    ) -> None:
        """Apply the current strategy allowance inside the reservation transaction."""

        if intent.action is not IntentAction.INCREASE:
            return
        if intent.strategy_id is None:
            if self._policy.require_strategy_allocation:
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_REQUIRED",
                    "risk-increasing intent has no strategy allocation identity",
                )
            return

        allocation = connection.execute(
            """SELECT * FROM risk_strategy_allocations
               WHERE scope_key = ? AND strategy_id = ?""",
            (intent.scope.key, intent.strategy_id),
        ).fetchone()
        if allocation is None:
            raise RiskDeniedError(
                "STRATEGY_ALLOCATION_REQUIRED", "strategy has no installed allocation"
            )
        if allocation["allocation_version"] != intent.allocation_version:
            raise RiskDeniedError(
                "STRATEGY_ALLOCATION_VERSION_CHANGED",
                "intent does not use the current strategy allocation version",
            )
        if self._policy.require_strategy_allocation and (
            self._policy.notional_unit is None
            or allocation["notional_unit"] != self._policy.notional_unit
            or intent.notional_unit != self._policy.notional_unit
        ):
            raise RiskDeniedError(
                "NOTIONAL_UNIT_MISMATCH",
                "strategy allocation, intent, and account policy must share one fixed notional unit",
            )
        if allocation["max_notional"] is not None and (
            allocation["notional_unit"] != self._policy.notional_unit
            or intent.notional_unit != allocation["notional_unit"]
        ):
            raise RiskDeniedError(
                "NOTIONAL_UNIT_MISMATCH",
                "strategy allocation and intent notional units do not match",
            )

        if allocation["max_notional"] is not None:
            rows = connection.execute(
                """SELECT notional FROM risk_reservations
                   WHERE scope_key = ? AND strategy_id = ? AND action = ?
                     AND status IN (?, ?)""",
                (
                    intent.scope.key,
                    intent.strategy_id,
                    IntentAction.INCREASE.value,
                    self._ACTIVE,
                    self._SETTLED,
                ),
            ).fetchall()
            used_notional = sum(
                (Fraction(_as_decimal(row["notional"])) for row in rows), Fraction(0)
            )
            cap = Fraction(_as_decimal(allocation["max_notional"]))
            if used_notional + Fraction(intent.notional) > cap:
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_EXHAUSTED",
                    "strategy increase notional allocation is exhausted",
                )

        if allocation["max_position"] is not None:
            if (
                intent.instrument != allocation["position_instrument"]
                or intent.quantity_unit != allocation["position_unit"]
                or intent.quantity is None
            ):
                raise RiskDeniedError(
                    "STRATEGY_POSITION_SCOPE_MISMATCH",
                    "intent quantity is not expressed in the allocation's exact instrument unit",
                )
            rows = connection.execute(
                """SELECT instrument, quantity_unit, quantity FROM risk_reservations
                   WHERE scope_key = ? AND strategy_id = ? AND action = ?
                     AND status IN (?, ?)""",
                (
                    intent.scope.key,
                    intent.strategy_id,
                    IntentAction.INCREASE.value,
                    self._ACTIVE,
                    self._SETTLED,
                ),
            ).fetchall()
            used_quantity = Fraction(0)
            for row in rows:
                if (
                    row["instrument"] != allocation["position_instrument"]
                    or row["quantity_unit"] != allocation["position_unit"]
                    or row["quantity"] is None
                ):
                    raise RiskDeniedError(
                        "STRATEGY_POSITION_USAGE_UNAVAILABLE",
                        "strategy reservations cannot be summed in one exact instrument unit",
                    )
                used_quantity += Fraction(_as_decimal(row["quantity"]))
            if used_quantity + Fraction(intent.quantity) > Fraction(
                _as_decimal(allocation["max_position"])
            ):
                raise RiskDeniedError(
                    "STRATEGY_ALLOCATION_EXHAUSTED",
                    "strategy reserved quantity allocation is exhausted",
                )

    def _assert_reservation_allocation_current(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        invalid: bool = False,
    ) -> None:
        """Reject a previously issued permit after its allocation revision changes."""

        strategy_id = row["strategy_id"]
        version = row["allocation_version"]
        if strategy_id is None:
            if (
                self._policy.require_strategy_allocation
                and row["action"] == IntentAction.INCREASE.value
            ):
                raise PermitInvalidError(
                    "STRATEGY_ALLOCATION_REQUIRED",
                    "permit has no strategy allocation identity",
                )
            return
        allocation = connection.execute(
            """SELECT allocation_version FROM risk_strategy_allocations
               WHERE scope_key = ? AND strategy_id = ?""",
            (row["scope_key"], strategy_id),
        ).fetchone()
        if allocation is not None and allocation["allocation_version"] == version:
            return
        error = PermitInvalidError if invalid else RiskDeniedError
        code = (
            "STRATEGY_ALLOCATION_VERSION_CHANGED"
            if allocation is not None
            else "STRATEGY_ALLOCATION_UNAVAILABLE"
        )
        raise error(code, "permit allocation version is no longer current")

    def _assert_not_frozen(self, connection: sqlite3.Connection, scope: AccountScope) -> None:
        reasons = self._freeze_reasons(connection, scope)
        if reasons:
            raise RiskDeniedError(
                "FROZEN",
                "new risk-increasing intents are frozen: " + "; ".join(reasons),
            )

    def _validate_active_permit(
        self,
        connection: sqlite3.Connection,
        permit_id: str,
        intent: RiskIntent,
    ) -> RiskPermit:
        """Validate immutable permit binding while an admission transaction is held."""

        row = connection.execute(
            "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
        ).fetchone()
        if row is None:
            raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
        if row["group_id"] is not None:
            raise PermitInvalidError(
                "GROUP_CLAIM_REQUIRED",
                "group permit can only claim through claim_group_leg",
            )
        permit = self._permit_from_row(row)
        if row["status"] != self._ACTIVE:
            raise PermitInvalidError("PERMIT_NOT_ACTIVE", "permit is no longer active")
        if permit.policy_fingerprint != self._policy.fingerprint:
            raise PermitInvalidError("POLICY_CHANGED", "permit was issued under another policy")
        self._ensure_policy_binding(connection, permit.scope.key)
        self._assert_reservation_allocation_current(connection, row, invalid=True)
        if intent.intent_id != permit.intent_id or intent.scope != permit.scope:
            raise PermitInvalidError("PERMIT_SCOPE_MISMATCH", "permit does not match intent scope")
        if intent.fingerprint != row["intent_hash"]:
            raise PermitInvalidError(
                "PERMIT_INTENT_MISMATCH", "permit does not match intent payload"
            )
        return permit

    @staticmethod
    def _dispatch_freeze_cause(intent_id: str) -> str:
        return "dispatch-inflight:" + intent_id

    @staticmethod
    def _dispatch_claim_digest(
        *,
        permit_id: str,
        intent_id: str,
        intent_hash: str,
        scope_key: str,
        cause_id: str,
        claimed_at: float,
        group_claim: Optional[sqlite3.Row] = None,  # noqa: UP045 -- Python 3.9 is supported.
    ) -> str:
        payload: dict[str, object] = {
            "cause_id": cause_id,
            "claimed_at": format(float(claimed_at), ".17g"),
            "intent_hash": intent_hash,
            "intent_id": intent_id,
            "permit_id": permit_id,
            "scope_key": scope_key,
            "schema": "bt-api-risk-dispatch-claim-v1",
        }
        if group_claim is not None and group_claim["group_id"] is not None:
            payload["group_child_claim"] = {
                "actual_exposure_profile_fingerprint": str(
                    group_claim["actual_exposure_profile_fingerprint"]
                ),
                "actual_intent_hash": str(group_claim["actual_intent_hash"]),
                "actual_instrument_facts_observed_at_ns": int(
                    group_claim["actual_instrument_facts_observed_at_ns"]
                ),
                "actual_instrument_facts_valid_until_ns": int(
                    group_claim["actual_instrument_facts_valid_until_ns"]
                ),
                "actual_notional": str(group_claim["actual_notional"]),
                "actual_quantity": str(group_claim["actual_quantity"]),
                "group_deadline_ns": int(group_claim["group_deadline_ns"]),
                "group_fingerprint": str(group_claim["group_fingerprint"]),
                "group_id": str(group_claim["group_id"]),
                "group_leg_id": str(group_claim["group_leg_id"]),
                "reserved_intent_hash": str(group_claim["reserved_intent_hash"]),
                "schema": "bt-api-risk-group-child-claim-v1",
            }
        return _sha256(_canonical_json(payload))

    @staticmethod
    def _is_dispatch_freeze_cause(cause_id: str) -> bool:
        return isinstance(cause_id, str) and cause_id.startswith("dispatch-inflight:")

    def _require_dispatch_claim(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> sqlite3.Row:
        """Require the exact durable claim and still-active safety latch."""
        claim = connection.execute(
            "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?",
            (row["permit_id"],),
        ).fetchone()
        expected_cause = self._dispatch_freeze_cause(str(row["intent_id"]))
        if (
            claim is None
            or claim["intent_id"] != row["intent_id"]
            or claim["intent_hash"] != row["intent_hash"]
            or claim["scope_key"] != row["scope_key"]
            or claim["cause_id"] != expected_cause
        ):
            raise PermitInvalidError(
                "PERMIT_NOT_DISPATCH_CLAIMED",
                "settlement requires the exact durable dispatch claim",
            )
        if row["group_id"] is not None:
            self._validate_group_claim_row(row, claim)
        elif claim["group_id"] is not None:
            raise PermitInvalidError(
                "GROUP_STATE_CONFLICT", "singleton reservation has a grouped dispatch claim"
            )
        freeze = connection.execute(
            "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
            (row["scope_key"], expected_cause),
        ).fetchone()
        if freeze is None or freeze["active"] != 1:
            raise PermitInvalidError(
                "DISPATCH_FREEZE_MISSING",
                "the dispatch claim is missing its active safety latch",
            )
        return claim

    def _dispatch_claim_binding(
        self, reservation: sqlite3.Row, claim: sqlite3.Row
    ) -> DispatchClaimBinding:
        scope = AccountScope(
            provider=str(reservation["provider"]),
            account_id=str(reservation["account_id"]),
            environment=str(reservation["environment"]),
        )
        claimed_at = float(claim["claimed_at"])
        return DispatchClaimBinding(
            scope=scope,
            permit_id=str(reservation["permit_id"]),
            intent_id=str(reservation["intent_id"]),
            intent_hash=str(reservation["intent_hash"]),
            cause_id=str(claim["cause_id"]),
            claimed_at=claimed_at,
            claim_digest=self._dispatch_claim_digest(
                permit_id=str(reservation["permit_id"]),
                intent_id=str(reservation["intent_id"]),
                intent_hash=str(reservation["intent_hash"]),
                scope_key=str(reservation["scope_key"]),
                cause_id=str(claim["cause_id"]),
                claimed_at=claimed_at,
                group_claim=claim,
            ),
        )

    def _load_dispatch_claim_binding(
        self, permit_id: str, journal_record_sha256: str
    ) -> DispatchClaimBinding:
        with self._connection() as connection:
            prior_proof = connection.execute(
                "SELECT permit_id FROM risk_dispatch_resolutions WHERE journal_record_sha256 = ?",
                (journal_record_sha256,),
            ).fetchone()
            if prior_proof is not None:
                raise PermitInvalidError(
                    "DISPATCH_PROOF_REPLAYED",
                    "this immutable journal proof was already consumed",
                )
            reservation = connection.execute(
                "SELECT * FROM risk_reservations WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if reservation is None:
                raise PermitInvalidError("PERMIT_UNKNOWN", "permit does not exist")
            if reservation["action"] == IntentAction.CANCEL.value:
                raise PermitInvalidError(
                    "CANCEL_DISPATCH_REQUIRES_CANCEL_PROOF",
                    "cancellation dispatch requires terminal cancellation-action evidence",
                )
            policy_error = self._policy_binding_read_error(
                connection, str(reservation["scope_key"])
            )
            if policy_error is not None:
                raise PermitInvalidError(
                    policy_error, "risk database is bound to another or unknown policy"
                )
            claim = connection.execute(
                "SELECT * FROM risk_dispatch_claims WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if claim is None:
                raise PermitInvalidError(
                    "PERMIT_NOT_DISPATCH_CLAIMED",
                    "dispatch resolution requires a durable dispatch claim",
                )
            expected_cause = self._dispatch_freeze_cause(str(reservation["intent_id"]))
            if (
                claim["intent_id"] != reservation["intent_id"]
                or claim["intent_hash"] != reservation["intent_hash"]
                or claim["scope_key"] != reservation["scope_key"]
                or claim["cause_id"] != expected_cause
            ):
                raise PermitInvalidError(
                    "DISPATCH_CLAIM_CONFLICT",
                    "the durable dispatch claim no longer matches its reservation",
                )
            prior = connection.execute(
                "SELECT journal_record_sha256 FROM risk_dispatch_resolutions WHERE permit_id = ?",
                (permit_id,),
            ).fetchone()
            if prior is not None:
                raise PermitInvalidError(
                    "DISPATCH_ALREADY_RECONCILED",
                    "this dispatch claim already has a terminal resolution",
                )
            freeze = connection.execute(
                "SELECT active FROM risk_freezes WHERE scope_key = ? AND cause_id = ?",
                (reservation["scope_key"], expected_cause),
            ).fetchone()
            if freeze is None or freeze["active"] != 1:
                raise PermitInvalidError(
                    "DISPATCH_FREEZE_MISSING",
                    "the exact dispatch latch is not active",
                )
            if reservation["status"] != self._SETTLED:
                raise PermitInvalidError(
                    "PERMIT_NOT_SETTLED",
                    "dispatch proof requires a settled reservation",
                )
            return self._dispatch_claim_binding(reservation, claim)

    @staticmethod
    def _assert_dispatch_proof_matches_claim(
        proof: DispatchResolutionProof, claim: DispatchClaimBinding
    ) -> None:
        if (
            proof.scope != claim.scope
            or proof.permit_id != claim.permit_id
            or proof.intent_id != claim.intent_id
            or proof.intent_hash != claim.intent_hash
            or proof.cause_id != claim.cause_id
            or proof.claim_digest != claim.claim_digest
        ):
            raise PermitInvalidError(
                "DISPATCH_RESOLUTION_PROOF_SCOPE_MISMATCH",
                "dispatch proof does not match the exact account, intent, permit and claim",
            )

    @staticmethod
    def _freeze_in_transaction(
        connection: sqlite3.Connection,
        scope: AccountScope,
        cause_id: str,
        reason: str,
        updated_at: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO risk_freezes (scope_key, cause_id, reason, active, blocking, updated_at)
            VALUES (?, ?, ?, 1, 1, ?)
            ON CONFLICT(scope_key, cause_id) DO UPDATE SET
                reason = excluded.reason, active = 1, blocking = 1,
                updated_at = excluded.updated_at
            """,
            (scope.key, cause_id, reason, updated_at),
        )

    def _freeze_reasons(self, connection: sqlite3.Connection, scope: AccountScope) -> list[str]:
        rows = connection.execute(
            "SELECT reason FROM risk_freezes "
            "WHERE scope_key = ? AND active = 1 AND blocking = 1 ORDER BY cause_id",
            (scope.key,),
        ).fetchall()
        return [str(row["reason"]) for row in rows]

    def _next_generation(self, connection: sqlite3.Connection, scope_key: str) -> int:
        row = connection.execute(
            "SELECT generation FROM risk_generations WHERE scope_key = ?", (scope_key,)
        ).fetchone()
        generation = int(row["generation"]) + 1 if row is not None else 1
        connection.execute(
            """
            INSERT INTO risk_generations (scope_key, generation) VALUES (?, ?)
            ON CONFLICT(scope_key) DO UPDATE SET generation = excluded.generation
            """,
            (scope_key, generation),
        )
        return generation

    def _expire_active(self, connection: sqlite3.Connection, now: float) -> None:
        # A provider-capable managed path has persisted a dispatch claim before
        # it can call the provider.  That claim is evidence of possible market
        # exposure, so TTL must never erase its reservation from account risk
        # accounting.  It stays active until a known result is settled or an
        # explicit audited reconciliation path deals with it.
        connection.execute(
            """
            UPDATE risk_reservations SET status = ?, reason = ?
            WHERE status = ? AND (
              (group_id IS NULL AND expires_at <= ?)
              OR (group_id IS NOT NULL AND group_expires_at_ns <= ? AND NOT EXISTS (
                  SELECT 1 FROM risk_dispatch_claims AS group_claim
                  WHERE group_claim.scope_key = risk_reservations.scope_key
                    AND group_claim.group_id = risk_reservations.group_id
              ))
            )
              AND NOT EXISTS (
                  SELECT 1 FROM risk_dispatch_claims
                  WHERE risk_dispatch_claims.permit_id = risk_reservations.permit_id
              )
            """,
            (
                self._EXPIRED,
                "permit_ttl_elapsed",
                self._ACTIVE,
                now,
                self._now_ns(),
            ),
        )

    def _permit_from_row(self, row: sqlite3.Row) -> RiskPermit:
        return RiskPermit(
            permit_id=str(row["permit_id"]),
            intent_id=str(row["intent_id"]),
            scope=AccountScope(
                provider=str(row["provider"]),
                account_id=str(row["account_id"]),
                environment=str(row["environment"]),
            ),
            action=IntentAction(str(row["action"])),
            notional=_as_decimal(row["notional"]),
            policy_fingerprint=str(row["policy_hash"]),
            generation=int(row["generation"]),
            issued_at=float(row["issued_at"]),
            expires_at=float(row["expires_at"]),
            strategy_id=row["strategy_id"],
            allocation_version=row["allocation_version"],
            instrument=row["instrument"],
            quantity=None if row["quantity"] is None else _as_decimal(row["quantity"]),
            quantity_unit=row["quantity_unit"],
            notional_unit=row["notional_unit"],
        )

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS risk_reservations (
                    permit_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL UNIQUE,
                    intent_hash TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    action TEXT NOT NULL,
                    notional TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    strategy_id TEXT,
                    allocation_version TEXT,
                    instrument TEXT,
                    quantity TEXT,
                    quantity_unit TEXT,
                    notional_unit TEXT,
                    group_id TEXT,
                    group_fingerprint TEXT,
                    group_leg_id TEXT,
                    group_deadline_ns INTEGER,
                    group_expires_at_ns INTEGER,
                    group_leg_count INTEGER,
                    group_exposure_profile_fingerprint TEXT,
                    resource_product_type TEXT,
                    resource_margin_pool TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_risk_reservations_scope_status
                    ON risk_reservations(scope_key, status, action);
                CREATE TABLE IF NOT EXISTS risk_freezes (
                    scope_key TEXT NOT NULL,
                    cause_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    blocking INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(scope_key, cause_id)
                );
                CREATE TABLE IF NOT EXISTS risk_dispatch_claims (
                    permit_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    cause_id TEXT NOT NULL,
                    claimed_at REAL NOT NULL,
                    group_id TEXT,
                    group_leg_id TEXT,
                    group_fingerprint TEXT,
                    group_deadline_ns INTEGER,
                    reserved_intent_hash TEXT,
                    actual_intent_hash TEXT,
                    actual_intent_json TEXT,
                    actual_quantity TEXT,
                    actual_notional TEXT,
                    actual_exposure_profile_fingerprint TEXT,
                    actual_instrument_facts_observed_at_ns INTEGER,
                    actual_instrument_facts_valid_until_ns INTEGER,
                    FOREIGN KEY(permit_id) REFERENCES risk_reservations(permit_id)
                );
                CREATE INDEX IF NOT EXISTS idx_risk_dispatch_claims_scope
                    ON risk_dispatch_claims(scope_key, intent_id);
                CREATE TABLE IF NOT EXISTS risk_dispatch_resolutions (
                    permit_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    cause_id TEXT NOT NULL,
                    claim_digest TEXT NOT NULL,
                    proof_kind TEXT NOT NULL,
                    evidence_class TEXT NOT NULL,
                    dispatch_attempt_count INTEGER NOT NULL,
                    journal_revision INTEGER NOT NULL,
                    journal_record_sha256 TEXT NOT NULL UNIQUE,
                    reconciliation_evidence_sha256 TEXT NOT NULL,
                    writer_fence_sha256 TEXT NOT NULL,
                    provider_order_id TEXT,
                    accepted_request_sha256 TEXT,
                    exposure_reservation_id TEXT,
                    exposure_reservation_sha256 TEXT,
                    filled_quantity INTEGER NOT NULL,
                    trade_count INTEGER NOT NULL,
                    resolved_at REAL NOT NULL,
                    FOREIGN KEY(permit_id) REFERENCES risk_reservations(permit_id)
                );
                CREATE TABLE IF NOT EXISTS risk_dispatch_recovery_audits (
                    permit_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    proof_sha256 TEXT NOT NULL,
                    attestation_sha256 TEXT NOT NULL UNIQUE,
                    recovery_event_id TEXT NOT NULL UNIQUE,
                    recovery_event_revision INTEGER NOT NULL CHECK(recovery_event_revision > 0),
                    recovery_event_sha256 TEXT NOT NULL UNIQUE,
                    original_writer_owner_id TEXT NOT NULL,
                    original_writer_fencing_token INTEGER NOT NULL
                        CHECK(original_writer_fencing_token > 0),
                    original_writer_fence_sha256 TEXT NOT NULL,
                    current_writer_owner_id TEXT NOT NULL,
                    current_writer_fencing_token INTEGER NOT NULL
                        CHECK(current_writer_fencing_token > original_writer_fencing_token),
                    current_writer_fence_sha256 TEXT NOT NULL,
                    attestation_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    FOREIGN KEY(permit_id) REFERENCES risk_dispatch_resolutions(permit_id)
                );
                CREATE TRIGGER IF NOT EXISTS risk_dispatch_recovery_audit_immutable_update
                BEFORE UPDATE ON risk_dispatch_recovery_audits
                BEGIN
                    SELECT RAISE(ABORT, 'dispatch recovery audits are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_dispatch_recovery_audit_immutable_delete
                BEFORE DELETE ON risk_dispatch_recovery_audits
                BEGIN
                    SELECT RAISE(ABORT, 'dispatch recovery audits are immutable');
                END;
                CREATE TABLE IF NOT EXISTS risk_cancel_dispatch_resolutions (
                    permit_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    cause_id TEXT NOT NULL,
                    claim_digest TEXT NOT NULL,
                    cancel_intent_fingerprint TEXT NOT NULL,
                    execution_scope_key TEXT NOT NULL,
                    cancel_id TEXT NOT NULL,
                    target_intent_id TEXT NOT NULL,
                    provider_order_id TEXT NOT NULL,
                    terminal_state TEXT NOT NULL
                        CHECK(terminal_state IN ('CANCELLED', 'REJECTED')),
                    evidence_class TEXT NOT NULL
                        CHECK(evidence_class IN ('SIMULATION_JOURNAL', 'NATIVE_PROVIDER_JOURNAL')),
                    dispatch_attempt_count INTEGER NOT NULL CHECK(dispatch_attempt_count = 1),
                    cancel_event_id TEXT NOT NULL,
                    cancel_event_sequence INTEGER NOT NULL CHECK(cancel_event_sequence > 0),
                    cancel_event_sha256 TEXT NOT NULL,
                    cancel_source TEXT NOT NULL CHECK(cancel_source IN ('provider', 'reconcile')),
                    source_evidence_sha256 TEXT NOT NULL,
                    target_postcondition TEXT NOT NULL
                        CHECK(target_postcondition IN ('TARGET_TERMINAL', 'TARGET_REMAINS_OPEN')),
                    target_state TEXT NOT NULL,
                    target_filled_quantity TEXT NOT NULL,
                    target_updated_at_ns INTEGER NOT NULL CHECK(target_updated_at_ns > 0),
                    target_record_sha256 TEXT NOT NULL,
                    target_event_id TEXT NOT NULL,
                    target_event_sequence INTEGER NOT NULL CHECK(target_event_sequence > 0),
                    target_event_type TEXT NOT NULL,
                    target_event_sha256 TEXT NOT NULL,
                    writer_owner_id TEXT NOT NULL,
                    writer_fencing_token INTEGER NOT NULL CHECK(writer_fencing_token > 0),
                    writer_fence_sha256 TEXT NOT NULL,
                    proof_sha256 TEXT NOT NULL UNIQUE,
                    resolved_at REAL NOT NULL,
                    UNIQUE(execution_scope_key, cancel_event_id),
                    FOREIGN KEY(permit_id) REFERENCES risk_reservations(permit_id)
                );
                CREATE TRIGGER IF NOT EXISTS risk_cancel_dispatch_resolution_immutable_update
                BEFORE UPDATE ON risk_cancel_dispatch_resolutions
                BEGIN
                    SELECT RAISE(ABORT, 'cancel dispatch resolution is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_cancel_dispatch_resolution_immutable_delete
                BEFORE DELETE ON risk_cancel_dispatch_resolutions
                BEGIN
                    SELECT RAISE(ABORT, 'cancel dispatch resolution is immutable');
                END;
                CREATE TABLE IF NOT EXISTS risk_generations (
                    scope_key TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_policy_bindings (
                    scope_key TEXT PRIMARY KEY,
                    policy_hash TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    max_increase_notional TEXT NOT NULL,
                    max_increase_count INTEGER NOT NULL,
                    permit_ttl_seconds TEXT NOT NULL,
                    require_strategy_allocation INTEGER NOT NULL,
                    notional_unit TEXT,
                    exposure_accounting_mode TEXT,
                    exposure_schema_version INTEGER,
                    max_gross_occupancy_notional TEXT,
                    max_gross_occupancy_intent_count INTEGER,
                    max_exposure_facts_age_seconds TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_strategy_allocations (
                    scope_key TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    allocation_version TEXT NOT NULL,
                    max_notional TEXT,
                    max_position TEXT,
                    position_instrument TEXT,
                    position_unit TEXT,
                    notional_unit TEXT,
                    max_gross_occupancy_notional TEXT,
                    max_gross_occupancy_intent_count INTEGER,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(scope_key, strategy_id)
                );
                CREATE TABLE IF NOT EXISTS risk_strategy_allocation_versions (
                    scope_key TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    allocation_version TEXT NOT NULL,
                    max_notional TEXT,
                    max_position TEXT,
                    position_instrument TEXT,
                    position_unit TEXT,
                    notional_unit TEXT,
                    max_gross_occupancy_notional TEXT,
                    max_gross_occupancy_intent_count INTEGER,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(scope_key, strategy_id, allocation_version)
                );
                CREATE TABLE IF NOT EXISTS risk_margin_pool_limits (
                    scope_key TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    margin_pool TEXT NOT NULL,
                    allocation_version TEXT NOT NULL,
                    max_notional TEXT NOT NULL,
                    notional_unit TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(scope_key, product_type, margin_pool)
                );
                CREATE TABLE IF NOT EXISTS risk_margin_pool_limit_versions (
                    scope_key TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    margin_pool TEXT NOT NULL,
                    allocation_version TEXT NOT NULL,
                    max_notional TEXT NOT NULL,
                    notional_unit TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(scope_key, product_type, margin_pool, allocation_version)
                );
                CREATE TABLE IF NOT EXISTS risk_gross_exposure_baselines (
                    scope_key TEXT PRIMARY KEY,
                    policy_hash TEXT NOT NULL,
                    proof_sha256 TEXT NOT NULL,
                    account_revision INTEGER NOT NULL CHECK(account_revision > 0),
                    notional_unit TEXT NOT NULL,
                    account_gross_notional TEXT NOT NULL,
                    account_active_increase_intent_count INTEGER NOT NULL,
                    source_snapshot_sha256 TEXT NOT NULL,
                    positions_sha256 TEXT NOT NULL,
                    open_orders_sha256 TEXT NOT NULL,
                    valuation_facts_sha256 TEXT NOT NULL,
                    source_schema_version TEXT NOT NULL,
                    observed_at_ns INTEGER NOT NULL,
                    valid_until_ns INTEGER NOT NULL,
                    writer_owner_id TEXT NOT NULL,
                    writer_fencing_token INTEGER NOT NULL,
                    writer_fence_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_gross_baseline_strategies (
                    scope_key TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    gross_notional TEXT NOT NULL,
                    active_increase_intent_count INTEGER NOT NULL,
                    PRIMARY KEY(scope_key, strategy_id),
                    FOREIGN KEY(scope_key) REFERENCES risk_gross_exposure_baselines(scope_key)
                );
                CREATE TABLE IF NOT EXISTS risk_gross_scope_revisions (
                    scope_key TEXT PRIMARY KEY,
                    journal_revision INTEGER NOT NULL CHECK(journal_revision > 0)
                );
                CREATE TABLE IF NOT EXISTS risk_order_exposures (
                    permit_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    provider_order_id TEXT NOT NULL,
                    exposure_reservation_id TEXT NOT NULL,
                    exposure_reservation_sha256 TEXT NOT NULL,
                    dispatch_claim_digest TEXT NOT NULL,
                    dispatch_proof_sha256 TEXT NOT NULL,
                    accepted_request_sha256 TEXT NOT NULL,
                    order_revision INTEGER NOT NULL CHECK(order_revision > 0),
                    journal_revision INTEGER NOT NULL CHECK(journal_revision > 0),
                    journal_record_sha256 TEXT NOT NULL,
                    observation_sha256 TEXT NOT NULL,
                    fill_history_sha256 TEXT NOT NULL,
                    source_alias_map_sha256 TEXT NOT NULL,
                    proof_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    requested_quantity TEXT NOT NULL,
                    cumulative_filled_quantity TEXT NOT NULL,
                    leaves_quantity TEXT NOT NULL,
                    leaves_valuation_price TEXT,
                    filled_gross_notional TEXT NOT NULL,
                    open_gross_notional TEXT NOT NULL,
                    notional_unit TEXT NOT NULL,
                    complete_order_snapshot INTEGER NOT NULL,
                    complete_fill_history INTEGER NOT NULL,
                    fill_watermark_revision INTEGER NOT NULL,
                    source_contract_ref TEXT NOT NULL,
                    source_contract_version TEXT NOT NULL,
                    provider_profile_version TEXT NOT NULL,
                    normalizer_schema_version TEXT NOT NULL,
                    order_side TEXT NOT NULL CHECK(order_side IN ('BUY', 'SELL')),
                    position_effect TEXT NOT NULL CHECK(position_effect = 'OPEN'),
                    instrument_id TEXT NOT NULL,
                    instrument_version TEXT NOT NULL,
                    instrument_multiplier TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    valuation_unit TEXT NOT NULL,
                    instrument_metadata_sha256 TEXT NOT NULL,
                    instrument_metadata_observed_at_ns INTEGER NOT NULL,
                    instrument_metadata_valid_until_ns INTEGER NOT NULL,
                    valuation_facts_sha256 TEXT NOT NULL,
                    valuation_observed_at_ns INTEGER NOT NULL,
                    valuation_valid_until_ns INTEGER NOT NULL,
                    fill_trade_count INTEGER NOT NULL,
                    fill_ids_sha256 TEXT NOT NULL,
                    fx_pair TEXT,
                    fx_rate TEXT,
                    fx_observed_at_ns INTEGER,
                    fx_valid_until_ns INTEGER,
                    observed_at_ns INTEGER NOT NULL,
                    valid_until_ns INTEGER NOT NULL,
                    writer_owner_id TEXT NOT NULL,
                    writer_fencing_token INTEGER NOT NULL,
                    writer_fence_sha256 TEXT NOT NULL,
                    FOREIGN KEY(permit_id) REFERENCES risk_reservations(permit_id)
                );
                CREATE TABLE IF NOT EXISTS risk_order_exposure_fills (
                    permit_id TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    fact_sha256 TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    position_effect TEXT NOT NULL CHECK(position_effect = 'OPEN'),
                    fee_amount TEXT NOT NULL,
                    fee_currency TEXT NOT NULL,
                    source_record_sha256 TEXT NOT NULL,
                    source_revision INTEGER NOT NULL CHECK(source_revision > 0),
                    PRIMARY KEY(permit_id, trade_id),
                    FOREIGN KEY(permit_id) REFERENCES risk_order_exposures(permit_id)
                );
                CREATE TRIGGER IF NOT EXISTS risk_order_exposure_fills_immutable_update
                BEFORE UPDATE ON risk_order_exposure_fills
                BEGIN
                    SELECT RAISE(ABORT, 'order exposure fill facts are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_order_exposure_fills_immutable_delete
                BEFORE DELETE ON risk_order_exposure_fills
                BEGIN
                    SELECT RAISE(ABORT, 'order exposure fill facts are immutable');
                END;
                CREATE TABLE IF NOT EXISTS risk_trade_identity_domains (
                    trade_id_domain_ref TEXT PRIMARY KEY,
                    trade_id_scope TEXT NOT NULL
                        CHECK(trade_id_scope IN ('ACCOUNT', 'INSTRUMENT')),
                    scope_key TEXT NOT NULL,
                    bound_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_trade_identity_claims (
                    trade_id_domain_ref TEXT NOT NULL,
                    identity_scope TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    permit_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    fact_sha256 TEXT NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    PRIMARY KEY(trade_id_domain_ref, identity_scope, trade_id)
                );
                CREATE TRIGGER IF NOT EXISTS risk_trade_identity_domains_immutable_update
                BEFORE UPDATE ON risk_trade_identity_domains
                BEGIN
                    SELECT RAISE(ABORT, 'trade identity domain bindings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_trade_identity_domains_immutable_delete
                BEFORE DELETE ON risk_trade_identity_domains
                BEGIN
                    SELECT RAISE(ABORT, 'trade identity domain bindings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_trade_identity_claims_immutable_update
                BEFORE UPDATE ON risk_trade_identity_claims
                BEGIN
                    SELECT RAISE(ABORT, 'trade identity claims are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_trade_identity_claims_immutable_delete
                BEFORE DELETE ON risk_trade_identity_claims
                BEGIN
                    SELECT RAISE(ABORT, 'trade identity claims are immutable');
                END;
                CREATE INDEX IF NOT EXISTS idx_risk_order_exposures_scope
                    ON risk_order_exposures(scope_key, state, journal_revision);
                """
            )
            freeze_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(risk_freezes)").fetchall()
            }
            if "blocking" not in freeze_columns:
                # Existing dispatch latches keep gating new increases; only an
                # explicit identity-conflict latch converts one to the exact
                # active-but-non-blocking recovery state.
                connection.execute(
                    "ALTER TABLE risk_freezes ADD COLUMN blocking INTEGER NOT NULL DEFAULT 1"
                )
            dispatch_resolution_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(risk_dispatch_resolutions)"
                ).fetchall()
            }
            for name, column_type in (
                ("resolution_proof_sha256", "TEXT"),
                ("exposure_proof_sha256", "TEXT"),
                ("terminal_state", "TEXT"),
                ("requested_quantity", "TEXT"),
                ("cumulative_filled_quantity", "TEXT"),
                ("exposure_fill_trade_count", "INTEGER"),
                ("exposure_fill_ids_sha256", "TEXT"),
                ("exposure_order_revision", "INTEGER"),
                ("fee_record_count", "INTEGER"),
                ("fee_history_complete", "INTEGER"),
            ):
                if name not in dispatch_resolution_columns:
                    connection.execute(
                        "ALTER TABLE risk_dispatch_resolutions ADD COLUMN " + name + " " + column_type
                    )
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_dispatch_resolution_proof
                   ON risk_dispatch_resolutions(resolution_proof_sha256)
                   WHERE resolution_proof_sha256 IS NOT NULL"""
            )
            connection.execute(
                """CREATE TRIGGER IF NOT EXISTS risk_dispatch_resolution_immutable_update
                   BEFORE UPDATE ON risk_dispatch_resolutions
                   BEGIN
                       SELECT RAISE(ABORT, 'dispatch resolutions are immutable');
                   END"""
            )
            connection.execute(
                """CREATE TRIGGER IF NOT EXISTS risk_dispatch_resolution_immutable_delete
                   BEFORE DELETE ON risk_dispatch_resolutions
                   BEGIN
                       SELECT RAISE(ABORT, 'dispatch resolutions are immutable');
                   END"""
            )
            reservation_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(risk_reservations)").fetchall()
            }
            for name, column_type in (
                ("strategy_id", "TEXT"),
                ("allocation_version", "TEXT"),
                ("instrument", "TEXT"),
                ("quantity", "TEXT"),
                ("quantity_unit", "TEXT"),
                ("notional_unit", "TEXT"),
                ("group_id", "TEXT"),
                ("group_fingerprint", "TEXT"),
                ("group_leg_id", "TEXT"),
                ("group_deadline_ns", "INTEGER"),
                ("group_expires_at_ns", "INTEGER"),
                ("group_leg_count", "INTEGER"),
                ("group_exposure_profile_fingerprint", "TEXT"),
                ("resource_product_type", "TEXT"),
                ("resource_margin_pool", "TEXT"),
            ):
                if name not in reservation_columns:
                    connection.execute(
                        "ALTER TABLE risk_reservations ADD COLUMN " + name + " " + column_type
                    )
            claim_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(risk_dispatch_claims)").fetchall()
            }
            for name, column_type in (
                ("group_id", "TEXT"),
                ("group_leg_id", "TEXT"),
                ("group_fingerprint", "TEXT"),
                ("group_deadline_ns", "INTEGER"),
                ("reserved_intent_hash", "TEXT"),
                ("actual_intent_hash", "TEXT"),
                ("actual_intent_json", "TEXT"),
                ("actual_quantity", "TEXT"),
                ("actual_notional", "TEXT"),
                ("actual_exposure_profile_fingerprint", "TEXT"),
                ("actual_instrument_facts_observed_at_ns", "INTEGER"),
                ("actual_instrument_facts_valid_until_ns", "INTEGER"),
            ):
                if name not in claim_columns:
                    connection.execute(
                        "ALTER TABLE risk_dispatch_claims ADD COLUMN " + name + " " + column_type
                    )
            for table in (
                "risk_strategy_allocations",
                "risk_strategy_allocation_versions",
            ):
                allocation_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()
                }
                if "notional_unit" not in allocation_columns:
                    connection.execute("ALTER TABLE " + table + " ADD COLUMN notional_unit TEXT")
                if "max_gross_occupancy_notional" not in allocation_columns:
                    connection.execute(
                        "ALTER TABLE " + table + " ADD COLUMN max_gross_occupancy_notional TEXT"
                    )
                if "max_gross_occupancy_intent_count" not in allocation_columns:
                    connection.execute(
                        "ALTER TABLE "
                        + table
                        + " ADD COLUMN max_gross_occupancy_intent_count INTEGER"
                    )
            for table, name in (
                ("risk_gross_exposure_baselines", "account_active_increase_intent_count"),
                ("risk_gross_baseline_strategies", "active_increase_intent_count"),
            ):
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()
                }
                if name not in columns:
                    # Existing rows receive NULL and are deliberately unusable
                    # for LIVE_GROSS until replaced by a new attributed baseline.
                    connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " INTEGER")
            exposure_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(risk_order_exposures)").fetchall()
            }
            for name, column_type in (
                ("instrument_metadata_observed_at_ns", "INTEGER"),
                ("valuation_observed_at_ns", "INTEGER"),
                ("valuation_valid_until_ns", "INTEGER"),
                ("fill_trade_count", "INTEGER"),
                ("fill_ids_sha256", "TEXT"),
                ("order_side", "TEXT"),
                ("position_effect", "TEXT"),
                ("leaves_valuation_price", "TEXT"),
            ):
                if name not in exposure_columns:
                    # Never infer missing history/valuation from an old row.
                    connection.execute(
                        "ALTER TABLE risk_order_exposures ADD COLUMN " + name + " " + column_type
                    )
            policy_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(risk_policy_bindings)").fetchall()
            }
            for name, column_type in (
                ("exposure_accounting_mode", "TEXT"),
                ("exposure_schema_version", "INTEGER"),
                ("max_gross_occupancy_notional", "TEXT"),
                ("max_gross_occupancy_intent_count", "INTEGER"),
                ("max_exposure_facts_age_seconds", "TEXT"),
            ):
                if name not in policy_columns:
                    connection.execute(
                        "ALTER TABLE risk_policy_bindings ADD COLUMN " + name + " " + column_type
                    )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_risk_reservations_strategy_status
                   ON risk_reservations(scope_key, strategy_id, status, action)"""
            )
            connection.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_reservations_group_leg
                   ON risk_reservations(scope_key, group_id, group_leg_id)
                   WHERE group_id IS NOT NULL"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_risk_reservations_group
                   ON risk_reservations(scope_key, group_id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_risk_reservations_margin_pool
                   ON risk_reservations(
                       scope_key, resource_product_type, resource_margin_pool, action, status
                   )"""
            )
            connection.execute(
                """CREATE TRIGGER IF NOT EXISTS risk_group_reservation_identity_immutable
                   BEFORE UPDATE OF group_id, group_fingerprint, group_leg_id,
                       group_deadline_ns, group_expires_at_ns, group_leg_count,
                       group_exposure_profile_fingerprint ON risk_reservations
                   WHEN OLD.group_id IS NOT NULL
                   BEGIN
                       SELECT RAISE(ABORT, 'group reservation identity is immutable');
                   END"""
            )
            connection.execute(
                """CREATE TRIGGER IF NOT EXISTS risk_group_dispatch_claim_immutable
                   BEFORE UPDATE ON risk_dispatch_claims
                   WHEN OLD.group_id IS NOT NULL
                   BEGIN
                       SELECT RAISE(ABORT, 'group dispatch claims are immutable');
                   END"""
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self._database_path),
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            # SQLite foreign-key enforcement is connection-local.
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a decimal amount")
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite():
        raise ValueError("decimal amount must be finite")
    return result


def _risk_intent_json(intent: RiskIntent) -> str:
    """Canonical storage for the exact mapped child RiskIntent of a group claim."""

    payload: dict[str, object] = {
        "action": intent.action.value,
        "allocation_version": intent.allocation_version,
        "exposure_profile_fingerprint": intent.exposure_profile_fingerprint,
        "instrument_facts_observed_at_ns": intent.instrument_facts_observed_at_ns,
        "instrument_facts_valid_until_ns": intent.instrument_facts_valid_until_ns,
        "instrument": intent.instrument,
        "intent_id": intent.intent_id,
        "notional": _decimal_text(intent.notional),
        "notional_unit": intent.notional_unit,
        "payload_fingerprint": intent.payload_fingerprint,
        "quantity": None if intent.quantity is None else _decimal_text(intent.quantity),
        "quantity_unit": intent.quantity_unit,
        "scope": {
            "account_id": intent.scope.account_id,
            "environment": intent.scope.environment,
            "provider": intent.scope.provider,
        },
        "strategy_id": intent.strategy_id,
    }
    if intent.resource is not None:
        # Older stored documents never carry this key; keep them canonical by
        # adding the resource section only for intents that declare one.
        payload["resource"] = {
            "margin_pool": intent.resource.margin_pool,
            "product_type": intent.resource.product_type,
        }
    return _canonical_json(payload)


def _risk_intent_from_json(raw: str) -> RiskIntent:
    """Restore only a canonical, exact-shape child RiskIntent document."""

    parsed = json.loads(raw)
    expected_keys = {
        "action",
        "allocation_version",
        "exposure_profile_fingerprint",
        "instrument_facts_observed_at_ns",
        "instrument_facts_valid_until_ns",
        "instrument",
        "intent_id",
        "notional",
        "notional_unit",
        "payload_fingerprint",
        "quantity",
        "quantity_unit",
        "scope",
        "strategy_id",
    }
    if type(parsed) is not dict or set(parsed) not in (
        expected_keys,
        expected_keys | {"resource"},
    ):
        raise ValueError("invalid stored child intent shape")
    scope_raw = parsed["scope"]
    if type(scope_raw) is not dict or set(scope_raw) != {
        "account_id",
        "environment",
        "provider",
    }:
        raise ValueError("invalid stored child scope shape")
    resource = None
    if "resource" in parsed:
        try:
            resource = AccountResourceKey.from_dict(parsed["resource"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid stored child intent resource") from exc
    intent = RiskIntent(
        intent_id=parsed["intent_id"],
        scope=AccountScope(
            provider=scope_raw["provider"],
            account_id=scope_raw["account_id"],
            environment=scope_raw["environment"],
        ),
        action=IntentAction(parsed["action"]),
        notional=Decimal(parsed["notional"]),
        payload_fingerprint=parsed["payload_fingerprint"],
        strategy_id=parsed["strategy_id"],
        allocation_version=parsed["allocation_version"],
        instrument=parsed["instrument"],
        quantity=(None if parsed["quantity"] is None else Decimal(parsed["quantity"])),
        quantity_unit=parsed["quantity_unit"],
        notional_unit=parsed["notional_unit"],
        exposure_profile_fingerprint=parsed["exposure_profile_fingerprint"],
        instrument_facts_observed_at_ns=parsed["instrument_facts_observed_at_ns"],
        instrument_facts_valid_until_ns=parsed["instrument_facts_valid_until_ns"],
        resource=resource,
    )
    if _risk_intent_json(intent) != raw:
        raise ValueError("stored child intent is not canonical")
    return intent


def _risk_group_fingerprint(
    scope: AccountScope,
    group_id: str,
    policy_fingerprint: str,
    deadline_ns: int,
    members: Sequence[tuple[str, str, str, str]],
) -> str:
    ordered = sorted(members, key=lambda item: item[0])
    if not 1 <= len(ordered) <= _RISK_GROUP_MAX_LEGS:
        raise ValueError("invalid group member count")
    for leg_id, intent_id, intent_hash, profile_hash in ordered:
        if type(leg_id) is not str or not _ALLOCATION_ID.fullmatch(leg_id):
            raise ValueError("invalid group leg identity")
        if type(intent_id) is not str or not intent_id:
            raise ValueError("invalid group intent identity")
        _require_sha256(intent_hash, "reserved_intent_hash")
        _require_sha256(profile_hash, "exposure_profile_fingerprint")
    return _sha256(
        _canonical_json(
            {
                "deadline_ns": deadline_ns,
                "group_id": group_id,
                "legs": [
                    {
                        "exposure_profile_fingerprint": profile_hash,
                        "intent_id": intent_id,
                        "leg_id": leg_id,
                        "reserved_intent_hash": intent_hash,
                    }
                    for leg_id, intent_id, intent_hash, profile_hash in ordered
                ],
                "policy_fingerprint": policy_fingerprint,
                "schema": "bt-api-risk-group-reservation-v1",
                "scope": scope.key,
            }
        )
    )


def _sum_decimal_exact(values: Iterator[Decimal]) -> Decimal:
    """Sum finite Decimal values without the ambient context precision."""

    return _decimal_from_fraction(sum((Fraction(value) for value in values), Fraction(0)))


def _decimal_from_fraction(value: Fraction) -> Decimal:
    """Convert a terminating rational to Decimal exactly and context-free."""

    numerator = value.numerator
    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        twos += 1
        denominator //= 2
    while denominator % 5 == 0:
        fives += 1
        denominator //= 5
    if denominator != 1:
        raise ValueError("risk ledger arithmetic did not produce a finite decimal")
    places = max(twos, fives)
    scaled = abs(numerator) * (2 ** (places - twos)) * (5 ** (places - fives))
    digits = tuple(int(character) for character in str(scaled))
    sign = 1 if numerator < 0 else 0
    return Decimal((sign, digits, -places))


def _decimal_upper_bound_from_fraction(value: Fraction, *, places: int = 28) -> Decimal:
    """Round a non-negative gross-occupancy ratio upward without Decimal context."""

    if value < 0 or type(places) is not int or places <= 0:
        raise ValueError("gross occupancy rounding requires a non-negative amount and scale")
    scaled_numerator = value.numerator * (10**places)
    scaled, remainder = divmod(scaled_numerator, value.denominator)
    if remainder:
        scaled += 1
    digits = tuple(int(character) for character in str(scaled))
    return Decimal((0, digits, -places))


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical_decimal_text(value: Decimal) -> str:
    """Render a persisted exposure fact without insignificant trailing zeros.

    Fingerprint payloads keep the scale-preserving ``_decimal_text`` spelling
    byte for byte.  The materialized exposure/fill/resolution tables instead
    publish the canonical text the journal and receipts already use, so one
    stored fact reads back identically no matter which decimal scale the
    arithmetic that produced it happened to carry.
    """

    if value == 0:
        return "0"
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _wall_clock() -> float:
    return time.time()


def _wall_clock_ns() -> int:
    return time.time_ns()


__all__ = [
    "AccountScope",
    "DispatchClaimBinding",
    "DurableRiskGate",
    "FreezeActivationResult",
    "FreezeActivationStatus",
    "IntentAction",
    "PermitInvalidError",
    "RiskDeniedError",
    "RiskGateError",
    "RiskGroupLegClaimReceipt",
    "RiskGroupLegIntent",
    "RiskGroupReservationLeg",
    "RiskGroupReservationReceipt",
    "RiskGroupReservationState",
    "RiskIntent",
    "RiskPermit",
    "RiskPolicy",
]
