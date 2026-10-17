"""Typed DIM05 two-leg old-contract exit contracts and durable sidecar.

The request carries only a source group/leg selector and an explicit limit
price.  Risk must reconstruct the accepted v1/v2 source, current registration,
closable quantity, and every prior terminal CLOSE receipt before it creates an
authorization.  This module does not grant service capability or dispatch an
order; it only validates typed values and persists the coordinator's
idempotent action/recovery projection.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any

from bt_api_execution import (
    ExecutionScope,
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
    GroupOrderIntent,
    order_intent_from_payload,
)

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_DECIMAL_TEXT = 256
_MAX_DECIMAL_DIGITS = 128
_MAX_DECIMAL_EXPONENT = 128
_MAX_PRIOR_CLOSES = 64
# At common exponent -128, a 128-digit coefficient with exponent +128 expands
# to at most 384 scaled digits. Summing 64 bounded prior fills adds at most two
# digits, so 512 digits keep every accepted add/subtract exact.
_EXACT_DECIMAL_PRECISION = 512
_MAX_PAYLOAD_BYTES = 1_048_576
_REQUEST_SCHEMA = "group-roll-exit-request-v1"
_SOURCE_SCHEMA = "group-roll-exit-source-facts-v1"
_PRIOR_CLOSE_SCHEMA = "group-roll-exit-prior-close-v1"
_AUTH_SCHEMA = "group-roll-exit-authorization-v1"
_OBSERVATION_SCHEMA = "group-roll-exit-observation-v1"


class GroupRollExitError(RuntimeError):
    """A malformed, conflicting, or unavailable durable roll-exit action."""


class GroupRollExitPhase(StrEnum):
    """Monotonic coordinator observation of one Risk-owned close action."""

    PREPARED = "PREPARED"
    SUBMIT_STARTED = "SUBMIT_STARTED"
    STATUS_OBSERVED = "STATUS_OBSERVED"
    UNKNOWN = "UNKNOWN"
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"


def _reference(value: object, name: str) -> str:
    if type(value) is not str or _REF.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or (positive and value <= 0):
        raise ValueError(f"invalid {name}")
    parts = value.as_tuple()
    if (
        len(parts.digits) > _MAX_DECIMAL_DIGITS
        or type(parts.exponent) is not int
        or abs(parts.exponent) > _MAX_DECIMAL_EXPONENT
    ):
        raise ValueError(f"out-of-range {name}")
    return value


def _decimal_text(value: Decimal) -> str:
    return format(_decimal(value, "decimal value"), "f")


def _is_multiple_of(value: Decimal, step: Decimal) -> bool:
    """Check a bounded Decimal lattice exactly, independent of ambient context."""
    with localcontext() as context:
        context.prec = _EXACT_DECIMAL_PRECISION
        return value % step == 0


def _decimal_from_payload(value: object, name: str, *, positive: bool = False) -> Decimal:
    if type(value) is not str or len(value) > _MAX_DECIMAL_TEXT or _DECIMAL.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid {name}") from None
    if (
        not result.is_finite()
        or (positive and result <= 0)
        or _decimal(result, name) != result
        or format(result, "f") != value
    ):
        raise ValueError(f"invalid {name}")
    return result


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ValueError("payload is not canonical JSON") from None
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds the roll-exit size bound")
    return encoded


def _thaw_frozen_payload(value: object, *, depth: int = 0) -> Any:
    """Convert wire-frozen JSON containers for strict domain payload parsers.

    ``WireEnvelope`` freezes JSON arrays as tuples and objects as mapping
    proxies. Some established execution contracts intentionally accept only
    ordinary JSON ``list`` values, so crossing that boundary requires a
    recursive, JSON-only thaw rather than relaxing those contracts.
    """

    if depth > 64:
        raise ValueError("payload nesting exceeds the wire bound")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("payload object keys must be strings")
        return {
            key: _thaw_frozen_payload(item, depth=depth + 1)
            for key, item in value.items()
        }
    if type(value) in (list, tuple):
        return [_thaw_frozen_payload(item, depth=depth + 1) for item in value]
    if value is None or type(value) in (bool, int, str):
        return value
    raise ValueError("payload contains a non-JSON value")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _close_side(source_side: Side) -> Side:
    return Side.SELL if source_side is Side.BUY else Side.BUY


@dataclass(frozen=True, slots=True)
class GroupRollExitRequestV1:
    """Minimal strategy request; identity, side, quantity and instrument are absent."""

    portfolio_ref: str
    group_id: str
    source_leg_id: str
    limit_price: Decimal

    def __post_init__(self) -> None:
        for name in ("portfolio_ref", "group_id", "source_leg_id"):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        object.__setattr__(self, "limit_price", _decimal(self.limit_price, "limit_price", positive=True))

    @property
    def fingerprint(self) -> str:
        return _sha256(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _REQUEST_SCHEMA,
            "portfolio_ref": self.portfolio_ref,
            "group_id": self.group_id,
            "source_leg_id": self.source_leg_id,
            "limit_price": _decimal_text(self.limit_price),
        }

    @classmethod
    def from_payload(cls, value: object) -> GroupRollExitRequestV1:
        expected = {"schema", "portfolio_ref", "group_id", "source_leg_id", "limit_price"}
        if not isinstance(value, Mapping) or set(value) != expected or value["schema"] != _REQUEST_SCHEMA:
            raise ValueError("invalid group roll-exit request")
        return cls(
            portfolio_ref=value["portfolio_ref"],
            group_id=value["group_id"],
            source_leg_id=value["source_leg_id"],
            limit_price=_decimal_from_payload(value["limit_price"], "limit_price", positive=True),
        )


@dataclass(frozen=True, slots=True)
class GroupRollExitPriorCloseV1:
    """One exact Risk-verified terminal CLOSE previously allocated to this source leg."""

    action_id: str
    intent_id: str
    client_order_id: str
    source_digest: str
    request_fingerprint: str
    authorization_sha256: str
    receipt_sha256: str
    resolution_sha256: str
    closing_side: Side
    instrument: str
    metadata_version: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    terminal_state: ExecutionState
    order_revision: int

    def __post_init__(self) -> None:
        for name in ("action_id", "intent_id", "client_order_id", "instrument", "metadata_version"):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        for name in (
            "source_digest", "request_fingerprint", "authorization_sha256",
            "receipt_sha256", "resolution_sha256",
        ):
            _digest(getattr(self, name), name)
        try:
            object.__setattr__(self, "closing_side", Side(self.closing_side))
            object.__setattr__(self, "terminal_state", ExecutionState(self.terminal_state))
        except (TypeError, ValueError):
            raise ValueError("invalid prior CLOSE enum") from None
        object.__setattr__(self, "requested_quantity", _decimal(self.requested_quantity, "requested_quantity", positive=True))
        object.__setattr__(self, "filled_quantity", _decimal(self.filled_quantity, "filled_quantity"))
        if (
            self.filled_quantity < 0
            or self.filled_quantity > self.requested_quantity
            or self.terminal_state not in {ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}
            or type(self.order_revision) is not int
            or self.order_revision <= 0
        ):
            raise ValueError("invalid prior terminal CLOSE receipt")
        if (
            self.terminal_state is ExecutionState.FILLED
            and self.filled_quantity != self.requested_quantity
        ) or (
            self.terminal_state is ExecutionState.REJECTED
            and self.filled_quantity != 0
        ):
            raise ValueError("prior CLOSE terminal state conflicts with its fills")
        if (
            self.intent_id != self.action_id
            or self.client_order_id != group_roll_exit_client_order_id(self.action_id)
            or self.action_id != group_roll_exit_action_id(self.source_digest, self.request_fingerprint)
        ):
            raise ValueError("prior CLOSE command identity is not source-derived")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _PRIOR_CLOSE_SCHEMA,
            "action_id": self.action_id,
            "intent_id": self.intent_id,
            "client_order_id": self.client_order_id,
            "source_digest": self.source_digest,
            "request_fingerprint": self.request_fingerprint,
            "authorization_sha256": self.authorization_sha256,
            "receipt_sha256": self.receipt_sha256,
            "resolution_sha256": self.resolution_sha256,
            "closing_side": self.closing_side.value,
            "instrument": self.instrument,
            "metadata_version": self.metadata_version,
            "requested_quantity": _decimal_text(self.requested_quantity),
            "filled_quantity": _decimal_text(self.filled_quantity),
            "terminal_state": self.terminal_state.value,
            "order_revision": self.order_revision,
        }

    @classmethod
    def from_payload(cls, value: object) -> GroupRollExitPriorCloseV1:
        expected = {
            "schema", "action_id", "intent_id", "client_order_id", "source_digest",
            "request_fingerprint", "authorization_sha256", "receipt_sha256",
            "resolution_sha256", "closing_side", "instrument", "metadata_version",
            "requested_quantity", "filled_quantity", "terminal_state", "order_revision",
        }
        if not isinstance(value, Mapping) or set(value) != expected or value["schema"] != _PRIOR_CLOSE_SCHEMA:
            raise ValueError("invalid prior CLOSE reference")
        return cls(
            action_id=value["action_id"],
            intent_id=value["intent_id"],
            client_order_id=value["client_order_id"],
            source_digest=value["source_digest"],
            request_fingerprint=value["request_fingerprint"],
            authorization_sha256=value["authorization_sha256"],
            receipt_sha256=value["receipt_sha256"],
            resolution_sha256=value["resolution_sha256"],
            closing_side=Side(value["closing_side"]),
            instrument=value["instrument"],
            metadata_version=value["metadata_version"],
            requested_quantity=_decimal_from_payload(value["requested_quantity"], "requested_quantity", positive=True),
            filled_quantity=_decimal_from_payload(value["filled_quantity"], "filled_quantity"),
            terminal_state=ExecutionState(value["terminal_state"]),
            order_revision=value["order_revision"],
        )


@dataclass(frozen=True, slots=True)
class GroupRollExitSourceFactsV1:
    """Risk-rebuilt v1/v2 source order, close proofs, and write registration."""

    portfolio_ref: str
    origin_run_id: str
    group_id: str
    group_version: int
    group_revision: int
    group_fingerprint: str
    source_risk_reservation_fingerprint: str
    source_risk_intent_fingerprint: str
    group_request: GroupOrderIntent
    account_ref: str
    strategy_id: str
    source_leg_id: str
    source_intent_id: str
    source_request_fingerprint: str
    source_child_intent: OrderIntent
    source_execution_scope: ExecutionScope
    source_terminal_status: GroupChildOrderStatus
    source_requested_quantity: Decimal
    source_claim: GroupLegClaimReceipt
    source_risk_resolution: GroupDispatchResolutionReceipt
    source_event_digest: str
    source_side: Side
    instrument: str
    source_metadata_version: str
    close_metadata_version: str
    close_metadata_digest: str
    close_quantity_step: Decimal
    close_tick_size: Decimal
    close_metadata_valid_until_ns: int
    source_position_side: str
    source_position_bucket: str
    source_filled_quantity: Decimal
    prior_resolved_closes: tuple[GroupRollExitPriorCloseV1, ...]
    remaining_quantity: Decimal
    closable_position_version: str
    service_principal_ref: str
    service_instance_id: str
    service_scope_ref: str
    service_registration_id: str
    service_operation: str
    service_port: str
    service_registration_digest: str
    service_epoch: str
    service_policy_version: str
    service_requirements_version: str
    service_write_run_id: str

    def __post_init__(self) -> None:
        for name in (
            "portfolio_ref", "origin_run_id", "group_id", "account_ref", "strategy_id",
            "source_leg_id", "source_intent_id", "instrument", "source_metadata_version",
            "close_metadata_version", "service_principal_ref", "service_instance_id",
            "service_scope_ref", "service_registration_id", "service_epoch",
            "service_policy_version", "service_requirements_version", "service_write_run_id",
        ):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        for name in (
            "group_fingerprint", "source_risk_reservation_fingerprint",
            "source_risk_intent_fingerprint", "source_request_fingerprint",
            "source_event_digest", "closable_position_version", "close_metadata_digest",
            "service_registration_digest",
        ):
            _digest(getattr(self, name), name)
        if type(self.group_version) is not int or self.group_version not in {1, 2}:
            raise ValueError("DIM05 roll exit accepts only two-leg v1/v2 sources")
        if type(self.group_revision) is not int or self.group_revision <= 0:
            raise ValueError("exact persisted group revision is required")
        if type(self.group_request) is not GroupOrderIntent:
            raise ValueError("exact accepted group request is required")
        if (
            self.group_request.version != self.group_version
            or len(self.group_request.legs) != 2
            or self.group_request.group_id != self.group_id
            or self.group_request.portfolio_ref != self.portfolio_ref
            or self.group_request.strategy_id != self.strategy_id
            or self.group_request.fingerprint != self.group_fingerprint
        ):
            raise ValueError("group request differs from the source fingerprint")
        if type(self.source_execution_scope) is not ExecutionScope:
            raise ValueError("exact source execution scope is required")
        if self.source_execution_scope.account_ref != self.account_ref or self.source_execution_scope.strategy_id != self.strategy_id:
            raise ValueError("source execution scope differs from group ownership")
        try:
            object.__setattr__(self, "source_side", Side(self.source_side))
        except (TypeError, ValueError):
            raise ValueError("invalid source side") from None
        if type(self.source_position_side) is not str or self.source_position_side not in {"NET", "LONG", "SHORT"}:
            raise ValueError("invalid source position side")
        if type(self.source_position_bucket) is not str or self.source_position_bucket not in {"ALL", "TODAY", "YESTERDAY"}:
            raise ValueError("invalid source position bucket")
        try:
            source_leg = self.group_request.leg(self.source_leg_id)
        except Exception:
            raise ValueError("source leg is not a member of the accepted group") from None
        source_intent = source_leg.maximum_intent
        if (
            source_leg.account_ref != self.account_ref
            or source_intent.intent_id != self.source_intent_id
            or type(self.source_child_intent) is not OrderIntent
            or self.source_child_intent.intent_id != self.source_intent_id
            or self.source_child_intent.scope != self.source_execution_scope
            or self.source_child_intent.instrument != self.instrument
            or self.source_child_intent.side is not self.source_side
            or self.source_child_intent.position_effect is not PositionEffect.OPEN
            or self.source_child_intent.position_side != self.source_position_side
            or self.source_child_intent.position_bucket != self.source_position_bucket
            or self.source_child_intent.metadata_version != self.source_metadata_version
            or self.source_child_intent.fingerprint != self.source_request_fingerprint
            or self.source_child_intent.quantity > source_intent.quantity
            or not _is_multiple_of(self.source_child_intent.quantity, source_leg.quantity_step)
            or replace(source_intent, quantity=self.source_child_intent.quantity)
            != self.source_child_intent
        ):
            raise ValueError("source leg facts differ from the accepted group request")
        if type(self.source_terminal_status) is not GroupChildOrderStatus:
            raise ValueError("exact terminal source order status is required")
        object.__setattr__(self, "source_requested_quantity", _decimal(self.source_requested_quantity, "source_requested_quantity", positive=True))
        object.__setattr__(self, "source_filled_quantity", _decimal(self.source_filled_quantity, "source_filled_quantity", positive=True))
        object.__setattr__(self, "remaining_quantity", _decimal(self.remaining_quantity, "remaining_quantity"))
        object.__setattr__(self, "close_quantity_step", _decimal(self.close_quantity_step, "close_quantity_step", positive=True))
        object.__setattr__(self, "close_tick_size", _decimal(self.close_tick_size, "close_tick_size", positive=True))
        if (
            self.source_requested_quantity != source_intent.quantity
            or type(self.source_claim) is not GroupLegClaimReceipt
            or type(self.source_risk_resolution) is not GroupDispatchResolutionReceipt
            or self.source_claim.actual_child_intent_hash != self.source_risk_intent_fingerprint
            or self.source_claim.actual_quantity != self.source_child_intent.quantity
        ):
            raise ValueError("source claim/resolution proof is incomplete")
        permit = self.source_claim.permit
        resolution = self.source_risk_resolution
        if (
            permit.group_id != self.group_id
            or permit.leg_id != self.source_leg_id
            or permit.portfolio_ref != self.portfolio_ref
            or permit.strategy_id != self.strategy_id
            or permit.account_ref != self.account_ref
            or permit.group_fingerprint != self.source_risk_reservation_fingerprint
            or permit.order_intent_hash != self.source_child_intent.fingerprint
            or resolution.claim != self.source_claim
            or resolution.resolution_kind != "TERMINAL_EXPOSURE_RESOLVED"
            or resolution.final_state not in {ExecutionState.FILLED, ExecutionState.CANCELLED}
            or resolution.requested_quantity != self.source_child_intent.quantity
            or resolution.filled_quantity <= 0
            or resolution.trade_count <= 0
            or resolution.order_revision is None
            or resolution.dispatch_latch_active
        ):
            raise ValueError("source Risk permit or terminal resolution proof is invalid")
        if (
            type(self.service_operation) is not str
            or self.service_operation != "orders.submit_group_roll_exit"
            or type(self.service_port) is not str
            or self.service_port != "service.order_entry"
        ):
            raise ValueError("exact group roll-exit write registration is required")
        if type(self.close_metadata_valid_until_ns) is not int or self.close_metadata_valid_until_ns <= 0:
            raise ValueError("close metadata validity bound is required")
        if self.service_write_run_id != self.origin_run_id:
            raise ValueError("service write registration differs from the accepted strategy run")
        if self.source_terminal_status.state not in {ExecutionState.FILLED, ExecutionState.CANCELLED}:
            raise ValueError("source leg must have a terminal fill or partial cancellation")
        if (
            self.source_terminal_status.intent_id != self.source_intent_id
            or self.source_terminal_status.filled_quantity <= 0
            or self.source_terminal_status.filled_quantity > self.source_child_intent.quantity
            or self.source_terminal_status.trade_count is None
            or self.source_terminal_status.trade_count <= 0
            or self.source_terminal_status.order_revision != resolution.order_revision
            or self.source_terminal_status.state is not resolution.final_state
            or self.source_terminal_status.filled_quantity != resolution.filled_quantity
            or self.source_terminal_status.trade_count != resolution.trade_count
        ):
            raise ValueError("source terminal fill facts are incomplete")
        if (
            self.source_terminal_status.state is ExecutionState.FILLED
            and self.source_terminal_status.filled_quantity != self.source_child_intent.quantity
        ):
            raise ValueError("filled source intent must have a full terminal fill")
        if self.source_filled_quantity != self.source_terminal_status.filled_quantity:
            raise ValueError("source fill quantity differs from terminal status")
        if not _is_multiple_of(self.remaining_quantity, self.close_quantity_step):
            raise ValueError("remaining CLOSE quantity is off the current metadata lattice")
        prior = self.prior_resolved_closes
        if (
            type(prior) is not tuple
            or len(prior) > _MAX_PRIOR_CLOSES
            or any(type(item) is not GroupRollExitPriorCloseV1 for item in prior)
            or len({item.action_id for item in prior}) != len(prior)
            or len({item.intent_id for item in prior}) != len(prior)
            or len({item.client_order_id for item in prior}) != len(prior)
        ):
            raise ValueError("invalid or unbounded prior CLOSE references")
        if any(
            item.closing_side is not _close_side(self.source_side)
            or item.instrument != self.instrument
            for item in prior
        ):
            raise ValueError("prior CLOSE receipts do not match the source leg")
        with localcontext() as context:
            context.prec = _EXACT_DECIMAL_PRECISION
            prior_filled_quantity = sum(
                (item.filled_quantity for item in prior), Decimal(0)
            )
            remaining = self.source_filled_quantity - prior_filled_quantity
        if remaining < 0 or remaining != self.remaining_quantity:
            raise ValueError("remaining source exposure differs from Risk terminal CLOSE receipts")
        _canonical_bytes(self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _SOURCE_SCHEMA,
            "portfolio_ref": self.portfolio_ref,
            "origin_run_id": self.origin_run_id,
            "group_id": self.group_id,
            "group_version": self.group_version,
            "group_revision": self.group_revision,
            "group_fingerprint": self.group_fingerprint,
            "source_risk_reservation_fingerprint": self.source_risk_reservation_fingerprint,
            "source_risk_intent_fingerprint": self.source_risk_intent_fingerprint,
            "group_request": self.group_request.to_payload(),
            "account_ref": self.account_ref,
            "strategy_id": self.strategy_id,
            "source_leg_id": self.source_leg_id,
            "source_intent_id": self.source_intent_id,
            "source_request_fingerprint": self.source_request_fingerprint,
            "source_child_intent": self.source_child_intent.to_payload(),
            "source_execution_scope": self.source_execution_scope.to_payload(),
            "source_terminal_status": self.source_terminal_status.to_payload(),
            "source_requested_quantity": _decimal_text(self.source_requested_quantity),
            "source_claim": self.source_claim.to_payload(),
            "source_risk_resolution": self.source_risk_resolution.to_payload(),
            "source_event_digest": self.source_event_digest,
            "source_side": self.source_side.value,
            "instrument": self.instrument,
            "source_metadata_version": self.source_metadata_version,
            "close_metadata_version": self.close_metadata_version,
            "close_metadata_digest": self.close_metadata_digest,
            "close_quantity_step": _decimal_text(self.close_quantity_step),
            "close_tick_size": _decimal_text(self.close_tick_size),
            "close_metadata_valid_until_ns": self.close_metadata_valid_until_ns,
            "source_position_side": self.source_position_side,
            "source_position_bucket": self.source_position_bucket,
            "source_filled_quantity": _decimal_text(self.source_filled_quantity),
            "prior_resolved_closes": [item.to_payload() for item in self.prior_resolved_closes],
            "remaining_quantity": _decimal_text(self.remaining_quantity),
            "closable_position_version": self.closable_position_version,
            "service_principal_ref": self.service_principal_ref,
            "service_instance_id": self.service_instance_id,
            "service_scope_ref": self.service_scope_ref,
            "service_registration_id": self.service_registration_id,
            "service_operation": self.service_operation,
            "service_port": self.service_port,
            "service_registration_digest": self.service_registration_digest,
            "service_epoch": self.service_epoch,
            "service_policy_version": self.service_policy_version,
            "service_requirements_version": self.service_requirements_version,
            "service_write_run_id": self.service_write_run_id,
        }

    @property
    def digest(self) -> str:
        return _sha256(self.to_payload())

    @classmethod
    def from_payload(cls, value: object) -> GroupRollExitSourceFactsV1:
        expected = {
            "schema", "portfolio_ref", "origin_run_id", "group_id", "group_version",
            "group_revision", "group_fingerprint", "source_risk_reservation_fingerprint",
            "source_risk_intent_fingerprint", "group_request", "account_ref", "strategy_id",
            "source_leg_id", "source_intent_id", "source_request_fingerprint",
            "source_child_intent", "source_execution_scope", "source_terminal_status", "source_requested_quantity",
            "source_claim", "source_risk_resolution", "source_event_digest", "source_side",
            "instrument", "source_metadata_version", "close_metadata_version", "close_metadata_digest",
            "close_quantity_step", "close_tick_size", "close_metadata_valid_until_ns",
            "source_position_side", "source_position_bucket", "source_filled_quantity",
            "prior_resolved_closes", "remaining_quantity", "closable_position_version",
            "service_principal_ref", "service_instance_id", "service_scope_ref",
            "service_registration_id", "service_operation", "service_port",
            "service_registration_digest", "service_epoch", "service_policy_version",
            "service_requirements_version", "service_write_run_id",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected | {"schema"}
            or value["schema"] != _SOURCE_SCHEMA
            or type(value["prior_resolved_closes"]) not in (list, tuple)
            or len(value["prior_resolved_closes"]) > _MAX_PRIOR_CLOSES
            or not isinstance(value["source_execution_scope"], Mapping)
            or set(value["source_execution_scope"])
            != {"provider", "environment", "account_ref", "strategy_id", "trading_day"}
        ):
            raise ValueError("invalid group roll-exit source facts")
        # The service wire contract represents arrays as tuples and objects as
        # mapping proxies. Convert only JSON values back to ordinary containers
        # before calling existing execution payload parsers (which correctly
        # require JSON lists for fields such as GroupOrderIntent.legs).
        value = _thaw_frozen_payload(value)
        return cls(
            portfolio_ref=value["portfolio_ref"],
            origin_run_id=value["origin_run_id"],
            group_id=value["group_id"],
            group_version=value["group_version"],
            group_revision=value["group_revision"],
            group_fingerprint=value["group_fingerprint"],
            source_risk_reservation_fingerprint=value["source_risk_reservation_fingerprint"],
            source_risk_intent_fingerprint=value["source_risk_intent_fingerprint"],
            group_request=GroupOrderIntent.from_payload(value["group_request"]),
            account_ref=value["account_ref"],
            strategy_id=value["strategy_id"],
            source_leg_id=value["source_leg_id"],
            source_intent_id=value["source_intent_id"],
            source_request_fingerprint=value["source_request_fingerprint"],
            source_child_intent=order_intent_from_payload(value["source_child_intent"]),
            source_execution_scope=ExecutionScope(**value["source_execution_scope"]),
            source_terminal_status=GroupChildOrderStatus.from_payload(value["source_terminal_status"]),
            source_requested_quantity=_decimal_from_payload(value["source_requested_quantity"], "source_requested_quantity", positive=True),
            source_claim=GroupLegClaimReceipt.from_payload(value["source_claim"]),
            source_risk_resolution=GroupDispatchResolutionReceipt.from_payload(value["source_risk_resolution"]),
            source_event_digest=value["source_event_digest"],
            source_side=Side(value["source_side"]),
            instrument=value["instrument"],
            source_metadata_version=value["source_metadata_version"],
            close_metadata_version=value["close_metadata_version"],
            close_metadata_digest=value["close_metadata_digest"],
            close_quantity_step=_decimal_from_payload(value["close_quantity_step"], "close_quantity_step", positive=True),
            close_tick_size=_decimal_from_payload(value["close_tick_size"], "close_tick_size", positive=True),
            close_metadata_valid_until_ns=value["close_metadata_valid_until_ns"],
            source_position_side=value["source_position_side"],
            source_position_bucket=value["source_position_bucket"],
            source_filled_quantity=_decimal_from_payload(value["source_filled_quantity"], "source_filled_quantity", positive=True),
            prior_resolved_closes=tuple(GroupRollExitPriorCloseV1.from_payload(item) for item in value["prior_resolved_closes"]),
            remaining_quantity=_decimal_from_payload(value["remaining_quantity"], "remaining_quantity"),
            closable_position_version=value["closable_position_version"],
            service_principal_ref=value["service_principal_ref"],
            service_instance_id=value["service_instance_id"],
            service_scope_ref=value["service_scope_ref"],
            service_registration_id=value["service_registration_id"],
            service_operation=value["service_operation"],
            service_port=value["service_port"],
            service_registration_digest=value["service_registration_digest"],
            service_epoch=value["service_epoch"],
            service_policy_version=value["service_policy_version"],
            service_requirements_version=value["service_requirements_version"],
            service_write_run_id=value["service_write_run_id"],
        )


def group_roll_exit_action_id(source_digest: str, request_fingerprint: str) -> str:
    """Derive a stable command identity; callers cannot choose a second CLOSE ID."""

    _digest(source_digest, "source_digest")
    _digest(request_fingerprint, "request_fingerprint")
    derived = _sha256({
        "schema": _AUTH_SCHEMA,
        "source_digest": source_digest,
        "request_fingerprint": request_fingerprint,
    })
    return "dim05-roll-" + derived


def group_roll_exit_client_order_id(action_id: str) -> str:
    """Derive the only Risk client id from the source-bound stable action id."""

    _reference(action_id, "action_id")
    return "dim05-client-" + _sha256({"schema": _AUTH_SCHEMA, "action_id": action_id})[:40]


@dataclass(frozen=True, slots=True)
class GroupRollExitAuthorizationV1:
    """Risk-built authorization for exactly one source-derived CLOSE command."""

    request: GroupRollExitRequestV1
    source: GroupRollExitSourceFactsV1
    action_id: str
    client_order_id: str
    intent: OrderIntent
    deadline_ns: int
    authorization_sha256: str

    def __post_init__(self) -> None:
        if type(self.request) is not GroupRollExitRequestV1 or type(self.source) is not GroupRollExitSourceFactsV1:
            raise ValueError("typed roll-exit request and source facts are required")
        object.__setattr__(self, "action_id", _reference(self.action_id, "action_id"))
        object.__setattr__(self, "client_order_id", _reference(self.client_order_id, "client_order_id"))
        _digest(self.authorization_sha256, "authorization_sha256")
        if type(self.intent) is not OrderIntent:
            raise ValueError("typed CLOSE intent is required")
        if type(self.deadline_ns) is not int or self.deadline_ns <= 0 or self.intent.deadline_ns != self.deadline_ns:
            raise ValueError("invalid CLOSE deadline")
        if (
            self.request.portfolio_ref != self.source.portfolio_ref
            or self.request.group_id != self.source.group_id
            or self.request.source_leg_id != self.source.source_leg_id
            or self.source.remaining_quantity <= 0
            or self.action_id != group_roll_exit_action_id(self.source.digest, self.request.fingerprint)
            or self.client_order_id != group_roll_exit_client_order_id(self.action_id)
            or not _is_multiple_of(self.request.limit_price, self.source.close_tick_size)
            or self.deadline_ns > self.source.close_metadata_valid_until_ns
        ):
            raise ValueError("authorization is not bound to the exact current source")
        if (
            self.intent.intent_id != self.action_id
            or self.intent.signal_id != self.action_id
            or self.intent.scope != self.source.source_execution_scope
            or self.intent.scope.account_ref != self.source.account_ref
            or self.intent.scope.strategy_id != self.source.strategy_id
            or self.intent.instrument != self.source.instrument
            or self.intent.side is not _close_side(self.source.source_side)
            or self.intent.position_effect is not PositionEffect.CLOSE
            or self.intent.reduce_only is not True
            or self.intent.position_side != self.source.source_position_side
            or self.intent.position_bucket != self.source.source_position_bucket
            or self.intent.order_type is not OrderType.LIMIT
            or self.intent.quantity != self.source.remaining_quantity
            or self.intent.price != self.request.limit_price
            or self.intent.metadata_version != self.source.close_metadata_version
            or self.intent.time_in_force is not TimeInForce.GTC
            or dict(self.intent.tags)
            or not _is_multiple_of(self.intent.quantity, self.source.close_quantity_step)
        ):
            raise ValueError("authorization CLOSE differs from exact source-derived terms")
        if self.authorization_sha256 != _sha256(self._payload_without_digest()):
            raise ValueError("authorization digest mismatch")

    def _payload_without_digest(self) -> dict[str, Any]:
        return {
            "schema": _AUTH_SCHEMA,
            "request": self.request.to_payload(),
            "source": self.source.to_payload(),
            "action_id": self.action_id,
            "client_order_id": self.client_order_id,
            "intent": self.intent.to_payload(),
            "deadline_ns": self.deadline_ns,
        }

    def to_payload(self) -> dict[str, Any]:
        payload = self._payload_without_digest()
        payload["authorization_sha256"] = self.authorization_sha256
        return payload

    @classmethod
    def build(
        cls,
        *,
        request: GroupRollExitRequestV1,
        source: GroupRollExitSourceFactsV1,
        intent: OrderIntent,
        deadline_ns: int,
    ) -> GroupRollExitAuthorizationV1:
        action_id = group_roll_exit_action_id(source.digest, request.fingerprint)
        client_order_id = group_roll_exit_client_order_id(action_id)
        provisional = {
            "schema": _AUTH_SCHEMA,
            "request": request.to_payload(),
            "source": source.to_payload(),
            "action_id": action_id,
            "client_order_id": client_order_id,
            "intent": intent.to_payload(),
            "deadline_ns": deadline_ns,
        }
        return cls(
            request=request,
            source=source,
            action_id=action_id,
            client_order_id=client_order_id,
            intent=intent,
            deadline_ns=deadline_ns,
            authorization_sha256=_sha256(provisional),
        )

    @classmethod
    def from_payload(cls, value: object) -> GroupRollExitAuthorizationV1:
        expected = {
            "schema", "request", "source", "action_id", "client_order_id", "intent", "deadline_ns",
            "authorization_sha256",
        }
        if not isinstance(value, Mapping) or set(value) != expected or value["schema"] != _AUTH_SCHEMA:
            raise ValueError("invalid group roll-exit authorization")
        return cls(
            request=GroupRollExitRequestV1.from_payload(value["request"]),
            source=GroupRollExitSourceFactsV1.from_payload(value["source"]),
            action_id=value["action_id"],
            client_order_id=value["client_order_id"],
            intent=order_intent_from_payload(value["intent"]),
            deadline_ns=value["deadline_ns"],
            authorization_sha256=value["authorization_sha256"],
        )


@dataclass(frozen=True, slots=True)
class GroupRollExitObservationV1:
    """Bounded result of Risk submit/query; terminal receipts are digest-bound."""

    action_id: str
    phase: GroupRollExitPhase
    source_digest: str
    request_fingerprint: str
    authorization_sha256: str
    intent_id: str
    close_state: ExecutionState | None
    requested_quantity: Decimal
    filled_quantity: Decimal
    order_revision: int | None
    close_receipt_sha256: str | None
    detail_code: str | None

    def __post_init__(self) -> None:
        for name in ("action_id", "intent_id"):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        for name in ("source_digest", "request_fingerprint", "authorization_sha256"):
            _digest(getattr(self, name), name)
        try:
            object.__setattr__(self, "phase", GroupRollExitPhase(self.phase))
            if self.close_state is not None:
                object.__setattr__(self, "close_state", ExecutionState(self.close_state))
        except (TypeError, ValueError):
            raise ValueError("invalid roll-exit observation phase/state") from None
        object.__setattr__(self, "requested_quantity", _decimal(self.requested_quantity, "requested_quantity", positive=True))
        object.__setattr__(self, "filled_quantity", _decimal(self.filled_quantity, "filled_quantity"))
        if (
            self.filled_quantity < 0
            or self.filled_quantity > self.requested_quantity
            or (self.order_revision is not None and (type(self.order_revision) is not int or self.order_revision <= 0))
        ):
            raise ValueError("invalid roll-exit order observation")
        if self.close_receipt_sha256 is not None:
            _digest(self.close_receipt_sha256, "close_receipt_sha256")
        if self.detail_code is not None and (
            type(self.detail_code) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", self.detail_code)
        ):
            raise ValueError("invalid roll-exit detail code")
        if self.phase is GroupRollExitPhase.NOT_FOUND:
            raise ValueError("NOT_FOUND cannot be persisted as a roll-exit observation")
        if self.phase is GroupRollExitPhase.PREPARED:
            valid_projection = (
                self.close_state is None
                and self.filled_quantity == 0
                and self.order_revision is None
                and self.close_receipt_sha256 is None
                and self.detail_code is None
            )
        elif self.phase is GroupRollExitPhase.SUBMIT_STARTED:
            valid_projection = (
                self.close_state in {None, ExecutionState.PENDING_ADMISSION, ExecutionState.PENDING_DISPATCH, ExecutionState.DISPATCHING}
                and self.filled_quantity == 0
                and self.close_receipt_sha256 is None
            )
        elif self.phase is GroupRollExitPhase.STATUS_OBSERVED:
            valid_projection = (
                self.close_state in {
                    ExecutionState.PENDING_ADMISSION,
                    ExecutionState.PENDING_DISPATCH,
                    ExecutionState.DISPATCHING,
                    ExecutionState.ACKED,
                    ExecutionState.PARTIALLY_FILLED,
                }
                and self.close_receipt_sha256 is None
                and self.order_revision is not None
            )
        elif self.phase is GroupRollExitPhase.UNKNOWN:
            valid_projection = (
                self.close_state is ExecutionState.UNKNOWN
                and self.close_receipt_sha256 is None
            )
        elif self.phase is GroupRollExitPhase.RESOLVED:
            valid_projection = (
                self.close_state in {ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}
                and self.order_revision is not None
                and self.close_receipt_sha256 is not None
                and (
                    self.close_state is not ExecutionState.FILLED
                    or self.filled_quantity == self.requested_quantity
                )
                and (
                    self.close_state is not ExecutionState.REJECTED
                    or self.filled_quantity == 0
                )
            )
        else:
            valid_projection = False
        if not valid_projection:
            raise ValueError("roll-exit phase conflicts with the close observation")
        if self.close_state is not ExecutionState.PARTIALLY_FILLED and self.phase is GroupRollExitPhase.STATUS_OBSERVED and self.filled_quantity != 0:
            raise ValueError("non-partial close state cannot carry fills")
        if self.close_state is ExecutionState.PARTIALLY_FILLED and not (0 < self.filled_quantity < self.requested_quantity):
            raise ValueError("partial close observation requires a partial fill")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _OBSERVATION_SCHEMA,
            "action_id": self.action_id,
            "phase": self.phase.value,
            "source_digest": self.source_digest,
            "request_fingerprint": self.request_fingerprint,
            "authorization_sha256": self.authorization_sha256,
            "intent_id": self.intent_id,
            "close_state": None if self.close_state is None else self.close_state.value,
            "requested_quantity": _decimal_text(self.requested_quantity),
            "filled_quantity": _decimal_text(self.filled_quantity),
            "order_revision": self.order_revision,
            "close_receipt_sha256": self.close_receipt_sha256,
            "detail_code": self.detail_code,
        }

    @classmethod
    def from_payload(cls, value: object) -> GroupRollExitObservationV1:
        expected = {
            "schema", "action_id", "phase", "source_digest", "request_fingerprint",
            "authorization_sha256", "intent_id", "close_state", "requested_quantity",
            "filled_quantity", "order_revision", "close_receipt_sha256", "detail_code",
        }
        if not isinstance(value, Mapping) or set(value) != expected or value["schema"] != _OBSERVATION_SCHEMA:
            raise ValueError("invalid group roll-exit observation")
        return cls(
            action_id=value["action_id"],
            phase=GroupRollExitPhase(value["phase"]),
            source_digest=value["source_digest"],
            request_fingerprint=value["request_fingerprint"],
            authorization_sha256=value["authorization_sha256"],
            intent_id=value["intent_id"],
            close_state=None if value["close_state"] is None else ExecutionState(value["close_state"]),
            requested_quantity=_decimal_from_payload(value["requested_quantity"], "requested_quantity", positive=True),
            filled_quantity=_decimal_from_payload(value["filled_quantity"], "filled_quantity"),
            order_revision=value["order_revision"],
            close_receipt_sha256=value["close_receipt_sha256"],
            detail_code=value["detail_code"],
        )


@dataclass(frozen=True, slots=True)
class GroupRollExitActionRecordV1:
    authorization: GroupRollExitAuthorizationV1
    phase: GroupRollExitPhase
    observation: GroupRollExitObservationV1 | None
    updated_at_ns: int

    def __post_init__(self) -> None:
        if type(self.authorization) is not GroupRollExitAuthorizationV1:
            raise ValueError("typed roll-exit authorization is required")
        try:
            object.__setattr__(self, "phase", GroupRollExitPhase(self.phase))
        except (TypeError, ValueError):
            raise ValueError("invalid stored roll-exit phase") from None
        if self.observation is not None and type(self.observation) is not GroupRollExitObservationV1:
            raise ValueError("invalid stored roll-exit observation")
        if type(self.updated_at_ns) is not int or self.updated_at_ns <= 0:
            raise ValueError("invalid roll-exit update timestamp")
        if self.observation is not None and (
            self.observation.action_id != self.authorization.action_id
            or self.observation.source_digest != self.authorization.source.digest
            or self.observation.request_fingerprint != self.authorization.request.fingerprint
            or self.observation.authorization_sha256 != self.authorization.authorization_sha256
            or self.observation.intent_id != self.authorization.intent.intent_id
            or self.observation.phase is not self.phase
        ):
            raise ValueError("stored observation does not match its authorization")
        if self.phase is GroupRollExitPhase.NOT_FOUND:
            raise ValueError("NOT_FOUND cannot be persisted as a roll-exit action")
        if self.observation is None and self.phase not in {
            GroupRollExitPhase.PREPARED,
            GroupRollExitPhase.SUBMIT_STARTED,
        }:
            raise ValueError("persisted roll-exit phase requires its Risk observation")


class GroupRollExitActionStore:
    """SQLite sidecar for exact prepare/start/query recovery, independent of group terminal state."""

    _NONTERMINAL = frozenset(
        {
            GroupRollExitPhase.PREPARED,
            GroupRollExitPhase.SUBMIT_STARTED,
            GroupRollExitPhase.STATUS_OBSERVED,
            GroupRollExitPhase.UNKNOWN,
        }
    )
    _TRANSITIONS = {
        GroupRollExitPhase.PREPARED: frozenset(
            {GroupRollExitPhase.PREPARED, GroupRollExitPhase.SUBMIT_STARTED, GroupRollExitPhase.UNKNOWN, GroupRollExitPhase.RESOLVED}
        ),
        GroupRollExitPhase.SUBMIT_STARTED: frozenset(
            {GroupRollExitPhase.SUBMIT_STARTED, GroupRollExitPhase.STATUS_OBSERVED, GroupRollExitPhase.UNKNOWN, GroupRollExitPhase.RESOLVED}
        ),
        GroupRollExitPhase.STATUS_OBSERVED: frozenset(
            {GroupRollExitPhase.STATUS_OBSERVED, GroupRollExitPhase.UNKNOWN, GroupRollExitPhase.RESOLVED}
        ),
        GroupRollExitPhase.UNKNOWN: frozenset(
            {
                GroupRollExitPhase.UNKNOWN,
                GroupRollExitPhase.SUBMIT_STARTED,
                GroupRollExitPhase.STATUS_OBSERVED,
                GroupRollExitPhase.RESOLVED,
            }
        ),
        GroupRollExitPhase.RESOLVED: frozenset({GroupRollExitPhase.RESOLVED}),
    }

    def __init__(self, path: str | Path | object, *, busy_timeout_ms: int = 5_000, clock_ns=time.time_ns) -> None:
        if type(busy_timeout_ms) is not int or busy_timeout_ms <= 0 or not callable(clock_ns):
            raise GroupRollExitError("invalid roll-exit sidecar configuration")
        self._clock_ns = clock_ns
        self._closed = False
        owner_connection = getattr(path, "_connection", None)
        owner_transaction = getattr(path, "_transaction", None)
        owner_lock = getattr(path, "_lock", None)
        self._owner = (
            path
            if owner_connection is not None
            and callable(owner_transaction)
            and owner_lock is not None
            else None
        )
        self._owns_connection = self._owner is None
        self._lock = threading.RLock() if self._owner is None else owner_lock
        if self._owner is not None:
            self._connection = owner_connection
            try:
                with self._transaction():
                    self._create_schema()
            except (OSError, sqlite3.Error) as error:
                raise GroupRollExitError("roll-exit sidecar unavailable") from error
            return
        if not isinstance(path, (str, Path)):
            raise GroupRollExitError("roll-exit sidecar requires a path or shared SQLite owner")
        self._path = str(path)
        try:
            self._connection = sqlite3.connect(
                self._path,
                timeout=busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._create_schema()
        except (OSError, sqlite3.Error) as error:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            raise GroupRollExitError("roll-exit sidecar unavailable") from error

    def _create_schema(self) -> None:
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS coordinator_group_roll_exit_actions ("
            "action_id TEXT PRIMARY KEY, portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, "
            "source_leg_id TEXT NOT NULL, source_digest TEXT NOT NULL, request_fingerprint TEXT NOT NULL, "
            "authorization_sha256 TEXT NOT NULL, authorization_json TEXT NOT NULL, "
            "phase TEXT NOT NULL, observation_json TEXT, updated_at_ns INTEGER NOT NULL)"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS coordinator_group_roll_exit_source_idx "
            "ON coordinator_group_roll_exit_actions(portfolio_ref, group_id, source_leg_id, updated_at_ns)"
        )

    def prepare(self, authorization: GroupRollExitAuthorizationV1) -> GroupRollExitActionRecordV1:
        """Persist one Risk-prepared authorization or return its exact replay."""

        if type(authorization) is not GroupRollExitAuthorizationV1:
            raise GroupRollExitError("typed Risk authorization required")
        payload_json = _canonical_bytes(authorization.to_payload()).decode("ascii")
        source = authorization.source
        request = authorization.request
        with self._transaction():
            existing = self._connection.execute(
                "SELECT * FROM coordinator_group_roll_exit_actions WHERE action_id=?",
                (authorization.action_id,),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if (
                    record.authorization.authorization_sha256 != authorization.authorization_sha256
                    or record.authorization.to_payload() != authorization.to_payload()
                ):
                    raise GroupRollExitError("roll-exit action identity conflict")
                return record
            active = self._connection.execute(
                "SELECT action_id, phase FROM coordinator_group_roll_exit_actions "
                "WHERE portfolio_ref=? AND group_id=? AND source_leg_id=? "
                "AND phase IN (?, ?, ?, ?) ORDER BY updated_at_ns LIMIT 1",
                (
                    request.portfolio_ref,
                    request.group_id,
                    request.source_leg_id,
                    *(phase.value for phase in sorted(self._NONTERMINAL, key=lambda item: item.value)),
                ),
            ).fetchone()
            if active is not None:
                raise GroupRollExitError("prior roll-exit action requires query/recovery")
            now = self._now()
            self._connection.execute(
                "INSERT INTO coordinator_group_roll_exit_actions "
                "(action_id, portfolio_ref, group_id, source_leg_id, source_digest, request_fingerprint, "
                "authorization_sha256, authorization_json, phase, observation_json, updated_at_ns) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    authorization.action_id,
                    request.portfolio_ref,
                    request.group_id,
                    request.source_leg_id,
                    source.digest,
                    request.fingerprint,
                    authorization.authorization_sha256,
                    payload_json,
                    GroupRollExitPhase.PREPARED.value,
                    now,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM coordinator_group_roll_exit_actions WHERE action_id=?",
                (authorization.action_id,),
            ).fetchone()
            return self._record(row)

    def get(self, action_id: str) -> GroupRollExitActionRecordV1 | None:
        action_id = _reference(action_id, "action_id")
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT * FROM coordinator_group_roll_exit_actions WHERE action_id=?",
                (action_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def find_for_source(
        self, portfolio_ref: str, group_id: str, source_leg_id: str
    ) -> tuple[GroupRollExitActionRecordV1, ...]:
        for name, value in (("portfolio_ref", portfolio_ref), ("group_id", group_id), ("source_leg_id", source_leg_id)):
            _reference(value, name)
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                "SELECT * FROM coordinator_group_roll_exit_actions "
                "WHERE portfolio_ref=? AND group_id=? AND source_leg_id=? ORDER BY updated_at_ns, action_id",
                (portfolio_ref, group_id, source_leg_id),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def claim_submit(self, action_id: str, authorization_sha256: str) -> bool:
        """Atomically allow exactly one coordinator submit call after prepare."""

        action_id = _reference(action_id, "action_id")
        _digest(authorization_sha256, "authorization_sha256")
        with self._transaction():
            row = self._connection.execute(
                "SELECT phase, authorization_sha256 FROM coordinator_group_roll_exit_actions WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if row is None or row["authorization_sha256"] != authorization_sha256:
                raise GroupRollExitError("roll-exit authorization is not persisted")
            if row["phase"] != GroupRollExitPhase.PREPARED.value:
                return False
            changed = self._connection.execute(
                "UPDATE coordinator_group_roll_exit_actions SET phase=?, observation_json=NULL, updated_at_ns=? "
                "WHERE action_id=? AND phase=? AND authorization_sha256=?",
                (
                    GroupRollExitPhase.SUBMIT_STARTED.value,
                    self._now(),
                    action_id,
                    GroupRollExitPhase.PREPARED.value,
                    authorization_sha256,
                ),
            ).rowcount
            return changed == 1

    def record_observation(self, observation: GroupRollExitObservationV1) -> GroupRollExitActionRecordV1:
        if type(observation) is not GroupRollExitObservationV1:
            raise GroupRollExitError("typed Risk observation required")
        observation_json = _canonical_bytes(observation.to_payload()).decode("ascii")
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM coordinator_group_roll_exit_actions WHERE action_id=?",
                (observation.action_id,),
            ).fetchone()
            if row is None:
                raise GroupRollExitError("unknown roll-exit action")
            record = self._record(row)
            if (
                observation.source_digest != record.authorization.source.digest
                or observation.request_fingerprint != record.authorization.request.fingerprint
                or observation.authorization_sha256 != record.authorization.authorization_sha256
                or observation.intent_id != record.authorization.intent.intent_id
            ):
                raise GroupRollExitError("Risk observation does not match the prepared authorization")
            if observation.phase not in self._TRANSITIONS[record.phase]:
                raise GroupRollExitError("roll-exit phase regression")
            previous = record.observation
            if previous is not None:
                if previous.phase is GroupRollExitPhase.RESOLVED:
                    if observation != previous:
                        raise GroupRollExitError("resolved roll-exit observation changed")
                    return record
                if observation.phase in {
                        GroupRollExitPhase.SUBMIT_STARTED,
                    GroupRollExitPhase.STATUS_OBSERVED,
                    GroupRollExitPhase.UNKNOWN,
                    GroupRollExitPhase.RESOLVED,
                }:
                    if (
                        previous.order_revision is not None
                        and observation.order_revision is not None
                        and observation.order_revision < previous.order_revision
                    ):
                        raise GroupRollExitError("roll-exit order revision regressed")
                    if observation.filled_quantity < previous.filled_quantity:
                        if observation.phase is not GroupRollExitPhase.UNKNOWN:
                            raise GroupRollExitError("roll-exit filled quantity regressed")
                        observation = replace(
                            observation, filled_quantity=previous.filled_quantity
                        )
                    if (
                        previous.order_revision is not None
                        and observation.order_revision is None
                    ):
                        if observation.phase is not GroupRollExitPhase.UNKNOWN:
                            raise GroupRollExitError("roll-exit order revision regressed")
                        observation = replace(
                            observation, order_revision=previous.order_revision
                        )
            now = self._now()
            observation_json = _canonical_bytes(observation.to_payload()).decode("ascii")
            self._connection.execute(
                "UPDATE coordinator_group_roll_exit_actions SET phase=?, observation_json=?, updated_at_ns=? "
                "WHERE action_id=?",
                (observation.phase.value, observation_json, now, observation.action_id),
            )
            updated = self._connection.execute(
                "SELECT * FROM coordinator_group_roll_exit_actions WHERE action_id=?",
                (observation.action_id,),
            ).fetchone()
            return self._record(updated)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._owns_connection:
                self._connection.close()
            self._closed = True

    def _now(self) -> int:
        now = self._clock_ns()
        if type(now) is not int or now <= 0:
            raise GroupRollExitError("invalid roll-exit clock")
        return now

    def _ensure_open(self) -> None:
        if self._closed or (self._owner is not None and getattr(self._owner, "_closed", False)):
            raise GroupRollExitError("roll-exit sidecar is closed")

    def _record(self, row: sqlite3.Row) -> GroupRollExitActionRecordV1:
        try:
            authorization_payload = json.loads(row["authorization_json"])
            authorization = GroupRollExitAuthorizationV1.from_payload(authorization_payload)
            observation_payload = None if row["observation_json"] is None else json.loads(row["observation_json"])
            observation = None if observation_payload is None else GroupRollExitObservationV1.from_payload(observation_payload)
            phase = GroupRollExitPhase(row["phase"])
            if (
                authorization.action_id != row["action_id"]
                or authorization.request.portfolio_ref != row["portfolio_ref"]
                or authorization.request.group_id != row["group_id"]
                or authorization.request.source_leg_id != row["source_leg_id"]
                or authorization.source.digest != row["source_digest"]
                or authorization.request.fingerprint != row["request_fingerprint"]
                or authorization.authorization_sha256 != row["authorization_sha256"]
                or (observation is not None and observation.phase is not phase)
            ):
                raise ValueError
            return GroupRollExitActionRecordV1(
                authorization=authorization,
                phase=phase,
                observation=observation,
                updated_at_ns=row["updated_at_ns"],
            )
        except Exception:
            raise GroupRollExitError("roll-exit sidecar row is corrupt") from None

    def _transaction(self):
        if self._owner is not None:
            return self._owner._transaction()
        store = self

        class _Transaction:
            def __enter__(self):
                store._lock.acquire()
                try:
                    store._ensure_open()
                    store._connection.execute("BEGIN IMMEDIATE")
                except Exception:
                    store._lock.release()
                    raise
                return self

            def __exit__(self, exc_type, exc, traceback):
                try:
                    store._connection.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()
                return False

        return _Transaction()


__all__ = [
    "GroupRollExitActionRecordV1",
    "GroupRollExitActionStore",
    "GroupRollExitAuthorizationV1",
    "GroupRollExitError",
    "GroupRollExitObservationV1",
    "GroupRollExitPhase",
    "GroupRollExitPriorCloseV1",
    "GroupRollExitRequestV1",
    "GroupRollExitSourceFactsV1",
    "group_roll_exit_action_id",
    "group_roll_exit_client_order_id",
]
