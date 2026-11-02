"""SDK-neutral, provider-issued execution reconciliation evidence.

This module defines the boundary used by framework adapters to reconcile
orders, fills, and positions after an uncertain execution interval. It contains
no Backtrader types and no provider-native fields. The DTOs are data contracts,
not proof on their own: only an SDK-composed provider authority can issue and
validate a complete receipt, its current lease, and its position consistency
evidence.

The currently published generic query protocol covers active open orders and
positions, but has no complete terminal-order or account-fill family. A query
adapter that only has those generic families cannot implement this contract by
turning an empty result into a safe-to-resume decision.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bt_api_base.reconciliation_protocol import (
    CommissionAmount,
    FeeRate,
    PositionReconciliationReceipt,
    ProviderReconciliationAuthority,
    ReconciledOrder,
    ReconciliationCoverage,
    ReconciliationEvidence,
    ReconciliationFill,
    ReconciliationLease,
    ReconciliationPosition,
    ReconciliationRequest,
    TerminalOrderReconciliationReceipt,
    TerminalOrderSnapshot,
    UnresolvedFillObservation,
    UnresolvedPositionObservation,
    validate_reconciliation_order_status,
)


_FAMILIES = frozenset({"orders", "fills", "positions"})
_TERMINAL_ORDER_STATUSES = frozenset({"completed", "canceled", "rejected", "expired"})
_RESULT_SEAL = object()


class ReconciliationError(RuntimeError):
    """Finite public failure from the reconciliation facade."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_reconciliation_timeout(timeout: object, *, maximum_seconds: float = 60.0) -> float:
    """Check a finite timeout against the caller's selected execution bound."""
    if type(timeout) not in (int, float) or not math.isfinite(float(timeout)):
        raise ValueError("reconciliation timeout must be finite")
    value = float(timeout)
    if value <= 0.0 or value > maximum_seconds:
        raise ValueError("reconciliation timeout is outside the supported bound")
    return value


class ReconciliationClient:
    """Select and invoke the SDK reconciliation facade without framework state."""

    @staticmethod
    def select_facade(api: object, route: object):
        from .facade_protocol import ReconciliationFacadePort, ReconciliationFacadeSelector

        if not isinstance(api, ReconciliationFacadeSelector):
            raise ReconciliationError("reconciliation_selector_unavailable")
        selector = getattr(api, "reconciliation", None)
        if not callable(selector):
            raise ReconciliationError("reconciliation_selector_unavailable")
        facade = selector(route)
        if not isinstance(facade, ReconciliationFacadePort):
            raise ReconciliationError("reconciliation_route_port_unavailable")
        return facade

    @staticmethod
    def prepare_handle(facade: object, *, correlation: object = None):
        from .facade_protocol import OrderCorrelation, ReconciliationRequestHandle

        if correlation is None:
            handle = facade.prepare_authorized_account_reconciliation()
        else:
            if type(correlation) is not OrderCorrelation:
                raise ReconciliationError("reconciliation_correlation_invalid")
            handle = facade.prepare_order_reconciliation(correlation)
        if inspect.isawaitable(handle):
            close = getattr(handle, "close", None)
            if callable(close):
                close()
            raise ReconciliationError("reconciliation_handle_async_invalid")
        if type(handle) is not ReconciliationRequestHandle:
            raise ReconciliationError("reconciliation_handle_invalid")
        return handle

    @staticmethod
    def is_request_handle(handle: object) -> bool:
        from .facade_protocol import ReconciliationRequestHandle

        return type(handle) is ReconciliationRequestHandle

    @staticmethod
    def snapshot(facade: object, handle: object, *, timeout: float):
        return facade.snapshot(handle, timeout=timeout)

    @staticmethod
    def validate_snapshot(facade: object, handle: object, result: object, *, now: object):
        return facade.validate_snapshot(handle, result, now=now)


def _text(value: object, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        raise ValueError(f"{field_name} must be non-empty, trimmed printable text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    if len(encoded) > 256:
        raise ValueError(f"{field_name} exceeds the UTF-8 byte limit")
    return value


def _decimal(value: object, field_name: str, *, minimum: Decimal | None = None) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise TypeError(f"{field_name} must be a finite Decimal")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} is below its minimum")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be a timezone-aware UTC datetime")
    return value


def _texts(values: object, field_name: str) -> tuple[str, ...]:
    if type(values) not in (tuple, list):
        raise TypeError(f"{field_name} must be a tuple or list")
    result = tuple(_text(value, field_name) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True, init=False)
class ReconciliationResult:
    """Validated SDK projection suitable for a Store to adapt locally."""

    evidence: ReconciliationEvidence | None
    complete: bool
    reason: str
    evidence_verified: bool = False
    lease_current: bool = False
    position_consistency_verified: bool = False

    def __init__(
        self,
        evidence: ReconciliationEvidence | None,
        complete: bool,
        reason: str,
        evidence_verified: bool = False,
        lease_current: bool = False,
        position_consistency_verified: bool = False,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _RESULT_SEAL:
            raise TypeError("ReconciliationResult values are produced by ReconciliationFacade")
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "complete", complete)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "evidence_verified", evidence_verified)
        object.__setattr__(self, "lease_current", lease_current)
        object.__setattr__(self, "position_consistency_verified", position_consistency_verified)

    @property
    def safe_to_resume(self) -> bool:
        return (
            self.complete is True
            and self.evidence_verified is True
            and self.lease_current is True
            and self.position_consistency_verified is True
        )

    @property
    def snapshot_id(self) -> str | None:
        evidence = self.evidence
        if evidence is None or evidence.snapshot_ref is None or evidence.lease is None:
            return None
        return _sha256(
            {
                "evidence_digest": _evidence_digest(evidence),
                "lease_ref": evidence.lease.lease_ref,
                "observed_at": _iso(evidence.observed_at),
            }
        )

    @property
    def consistency_token(self) -> str | None:
        evidence = self.evidence
        if evidence is None or not self.complete:
            return None
        return _sha256(
            {
                "scope_ref": evidence.request.scope_ref,
                "source_ref": evidence.source_ref,
                "generation": evidence.generation,
                "orders": _rows_digest(evidence.orders),
                "fills": _rows_digest(evidence.fills),
                "positions": _rows_digest(evidence.positions),
            }
        )

    def to_payload(self) -> dict[str, object]:
        """Return a credential-safe neutral payload; no BT-local ref is present."""
        evidence = self.evidence
        if evidence is None:
            return {
                "complete": False,
                "safe_to_resume": False,
                "consistency_token": None,
                "snapshot_id": None,
                "orders": None,
                "fills": None,
                "positions": None,
                "unresolved_orders": None,
                "unresolved_fills": None,
                "unresolved_positions": None,
                "reason": self.reason,
            }
        return {
            "complete": self.complete,
            "safe_to_resume": self.safe_to_resume,
            "consistency_token": self.consistency_token,
            "snapshot_id": self.snapshot_id,
            "source_ref": evidence.source_ref,
            "generation": evidence.generation,
            "scope_ref": evidence.request.scope_ref,
            "account_ref": evidence.request.account_ref,
            "strategy_ref": evidence.request.strategy_ref,
            "snapshot_ref": evidence.snapshot_ref,
            "consistency_ref": evidence.consistency_ref,
            "lease_ref": evidence.lease.lease_ref if evidence.lease is not None else None,
            "evidence_digest": _evidence_digest(evidence),
            "orders": (
                None
                if evidence.orders is None
                else [_order_payload(row) for row in evidence.orders]
            ),
            "fills": (
                None if evidence.fills is None else [_fill_payload(row) for row in evidence.fills]
            ),
            "positions": (
                None
                if evidence.positions is None
                else [_position_payload(row) for row in evidence.positions]
            ),
            "unresolved_orders": (
                None if evidence.unresolved_orders is None else [
                    _terminal_order_payload(row) for row in evidence.unresolved_orders
                ]
            ),
            "unresolved_fills": (
                None if evidence.unresolved_fills is None else [
                    _unresolved_fill_payload(row) for row in evidence.unresolved_fills
                ]
            ),
            "unresolved_positions": (
                None if evidence.unresolved_positions is None else [
                    _unresolved_position_payload(row)
                    for row in evidence.unresolved_positions
                ]
            ),
            "reason": self.reason,
        }


class ReconciliationFacade:
    """Validate provider-issued order/fill/position evidence before projection."""

    def __init__(self, authority: ProviderReconciliationAuthority) -> None:
        required = (
            "query_reconciliation",
            "verify_reconciliation_evidence",
            "validate_reconciliation_lease",
            "verify_position_consistency",
        )
        if authority is None or any(
            not callable(getattr(authority, name, None)) for name in required
        ):
            raise TypeError("authority does not implement ProviderReconciliationAuthority")
        issuer_ref = getattr(authority, "issuer_ref", None)
        _text(issuer_ref, "issuer_ref")
        self._authority = authority
        self._issuer_ref = issuer_ref

    def snapshot(
        self,
        request: ReconciliationRequest,
        *,
        timeout: float = 5.0,
    ) -> ReconciliationResult:
        """Read and validate one current, coherent provider snapshot.

        Failure returns an explicit incomplete result. It never substitutes
        empty rows for a failed, unavailable, partial, or unknown query.
        """
        if type(request) is not ReconciliationRequest:
            raise TypeError("request must be an exact ReconciliationRequest")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a finite nonnegative number")
        if not math.isfinite(float(timeout)) or timeout < 0:
            raise ValueError("timeout must be a finite nonnegative number")
        now = datetime.now(UTC)
        deadline = now + timedelta(seconds=float(timeout))
        try:
            evidence = self._authority.query_reconciliation(request, deadline=deadline)
        except Exception:
            return _result(None, False, "reconciliation_query_failed")
        if type(evidence) is not ReconciliationEvidence:
            return _result(None, False, "reconciliation_evidence_invalid")

        structural_ok, structural_reason = _validate_evidence(evidence, request, self._issuer_ref)
        if not structural_ok:
            return _result(evidence, False, structural_reason)

        try:
            issuer_valid = self._authority.verify_reconciliation_evidence(evidence, request)
        except Exception:
            issuer_valid = False
        if type(issuer_valid) is not bool or not issuer_valid:
            return _result(evidence, False, "reconciliation_issuer_unverified")

        try:
            lease_current = self._authority.validate_reconciliation_lease(evidence, now=now)
        except Exception:
            lease_current = False
        if type(lease_current) is not bool or not lease_current:
            return _result(
                evidence,
                True,
                "reconciliation_lease_not_current",
                evidence_verified=True,
            )

        try:
            position_verified = self._authority.verify_position_consistency(evidence)
        except Exception:
            position_verified = False
        if type(position_verified) is not bool or not position_verified:
            return _result(
                evidence,
                True,
                "position_consistency_unverified",
                evidence_verified=True,
                lease_current=True,
            )
        return _result(
            evidence,
            True,
            "reconciliation_verified",
            evidence_verified=True,
            lease_current=True,
            position_consistency_verified=True,
        )

    def snapshot_payload(
        self,
        request: ReconciliationRequest,
        *,
        timeout: float = 5.0,
    ) -> dict[str, object]:
        """Return the Store-facing mapping after trusted evidence validation."""
        return self.snapshot(request, timeout=timeout).to_payload()

    def validate_snapshot(
        self,
        request: ReconciliationRequest,
        result: ReconciliationResult,
        *,
        now: datetime,
    ) -> bool:
        """Revalidate one issued result and its current provider lease.

        ``now`` is checked as an aware UTC value for the facade contract. Lease
        currentness is evaluated with the SDK's own current clock so a caller
        cannot make an expired lease appear current by supplying an old time.
        """
        if (
            type(request) is not ReconciliationRequest
            or type(result) is not ReconciliationResult
            or result.complete is not True
            or result.safe_to_resume is not True
            or result.evidence is None
            or type(now) is not datetime
            or now.tzinfo is None
            or now.utcoffset() != timedelta(0)
        ):
            return False
        evidence = result.evidence
        if type(evidence) is not ReconciliationEvidence:
            return False
        structural_ok, _ = _validate_evidence(evidence, request, self._issuer_ref)
        if structural_ok is not True:
            return False
        try:
            evidence_valid = self._authority.verify_reconciliation_evidence(evidence, request)
            lease_current = self._authority.validate_reconciliation_lease(
                evidence, now=datetime.now(UTC)
            )
            position_consistent = self._authority.verify_position_consistency(evidence)
        except Exception:
            return False
        return (
            type(evidence_valid) is bool
            and evidence_valid is True
            and type(lease_current) is bool
            and lease_current is True
            and type(position_consistent) is bool
            and position_consistent is True
        )


def _result(
    evidence: ReconciliationEvidence | None,
    complete: bool,
    reason: str,
    *,
    evidence_verified: bool = False,
    lease_current: bool = False,
    position_consistency_verified: bool = False,
) -> ReconciliationResult:
    return ReconciliationResult(
        evidence,
        complete,
        reason,
        evidence_verified,
        lease_current,
        position_consistency_verified,
        _seal=_RESULT_SEAL,
    )


def _validate_evidence(
    evidence: ReconciliationEvidence,
    request: ReconciliationRequest,
    issuer_ref: str,
) -> tuple[bool, str]:
    if evidence.request != request or evidence.issuer_ref != issuer_ref:
        return False, "reconciliation_scope_or_issuer_mismatch"
    if evidence.blockers:
        return False, "reconciliation_provider_blocked"
    if (
        evidence.source_ref is None
        or evidence.generation is None
        or evidence.snapshot_ref is None
        or evidence.consistency_ref is None
        or evidence.observed_at is None
        or evidence.lease is None
        or evidence.coverage is None
        or evidence.orders is None
        or evidence.fills is None
        or evidence.positions is None
        or evidence.unresolved_orders is None
        or evidence.unresolved_fills is None
        or evidence.unresolved_positions is None
        or evidence.position_receipt is None
    ):
        return False, "reconciliation_evidence_missing"

    coverage_by_family = {item.family: item for item in evidence.coverage}
    if len(coverage_by_family) != len(evidence.coverage) or set(coverage_by_family) != _FAMILIES:
        return False, "reconciliation_coverage_incomplete"
    for family, coverage in coverage_by_family.items():
        if (
            coverage.scope_ref != request.scope_ref
            or coverage.source_ref != evidence.source_ref
            or coverage.generation != evidence.generation
            or coverage.snapshot_ref != evidence.snapshot_ref
            or coverage.consistency_ref != evidence.consistency_ref
            or coverage.complete is not True
            or coverage.scope_complete is not True
            or coverage.blockers
            or (family in {"orders", "fills"} and coverage.history_complete is not True)
        ):
            return False, "reconciliation_scope_not_complete"
        rows = getattr(evidence, family)
        if rows and coverage.empty_result_proven is True:
            return False, "reconciliation_nonempty_result_marked_empty"
        if not rows and coverage.empty_result_proven is not True:
            return False, "reconciliation_empty_result_unproven"

    if evidence.unresolved_orders or evidence.unresolved_fills or evidence.unresolved_positions:
        return False, "reconciliation_unresolved_observations_present"

    lease = evidence.lease
    if (
        lease.scope_ref != request.scope_ref
        or lease.source_ref != evidence.source_ref
        or lease.generation != evidence.generation
        or lease.snapshot_ref != evidence.snapshot_ref
        or lease.request_digest != _request_digest(request)
        or lease.evidence_digest != _evidence_digest(evidence)
    ):
        return False, "reconciliation_lease_binding_mismatch"

    order_by_id: dict[str, ReconciledOrder] = {}
    order_by_intent: dict[str, ReconciledOrder] = {}
    seen_client_ids: set[str] = set()
    for row in evidence.orders:
        order = row.order
        if (
            row.scope_ref != request.scope_ref
            or row.account_ref != request.account_ref
            or row.strategy_ref != request.strategy_ref
            or row.source_ref != evidence.source_ref
            or row.generation != evidence.generation
            or row.snapshot_ref != evidence.snapshot_ref
            or row.lease_ref != evidence.lease.lease_ref
            or order.scope_ref != request.scope_ref
            or (request.instrument_refs and order.instrument_ref not in request.instrument_refs)
            or not row.intent_ref
            or not row.client_order_id
            or row.intent_ref in order_by_intent
            or order.order_id in order_by_id
            or row.client_order_id in seen_client_ids
        ):
            return False, "reconciliation_order_binding_invalid"
        if request.intent_refs and row.intent_ref not in request.intent_refs:
            return False, "reconciliation_unrequested_intent"
        if order.status == "unknown":
            return False, "reconciliation_order_status_unknown"
        if order.status in _TERMINAL_ORDER_STATUSES and order.terminal_confirmed is not True:
            return False, "reconciliation_terminal_order_unconfirmed"
        if order.status == "open" and order.filled_quantity != 0:
            return False, "reconciliation_order_fill_quantity_mismatch"
        if order.status == "partially_filled" and not (0 < order.filled_quantity < order.quantity):
            return False, "reconciliation_order_fill_quantity_mismatch"
        if order.status == "completed" and order.filled_quantity != order.quantity:
            return False, "reconciliation_order_fill_quantity_mismatch"
        order_by_id[order.order_id] = row
        order_by_intent[row.intent_ref] = row
        seen_client_ids.add(row.client_order_id)
    if request.intent_refs and set(order_by_intent) != set(request.intent_refs):
        return False, "reconciliation_intent_coverage_mismatch"

    fills_by_order: dict[str, Decimal] = {}
    seen_fill_ids: set[str] = set()
    for fill in evidence.fills:
        parent = order_by_id.get(fill.order_id)
        if (
            parent is None
            or fill.fill_id in seen_fill_ids
            or fill.scope_ref != request.scope_ref
            or fill.intent_ref != parent.intent_ref
            or fill.client_order_id != parent.client_order_id
            or fill.instrument_ref != parent.order.instrument_ref
            or fill.symbol != parent.order.symbol
            or fill.side != parent.order.side
            or fill.quantity_unit != parent.order.quantity_unit
            or fill.commission is None
        ):
            return False, "reconciliation_fill_binding_invalid"
        seen_fill_ids.add(fill.fill_id)
        fills_by_order[fill.order_id] = (
            fills_by_order.get(fill.order_id, Decimal("0")) + fill.quantity
        )
    for order_id, row in order_by_id.items():
        if fills_by_order.get(order_id, Decimal("0")) != row.order.filled_quantity:
            return False, "reconciliation_order_fill_total_mismatch"

    position_keys: set[tuple[str, str]] = set()
    expected_units = set()
    for position in evidence.positions:
        key = (position.instrument_ref, position.position_side)
        if position.scope_ref != request.scope_ref or key in position_keys:
            return False, "reconciliation_position_binding_invalid"
        if request.instrument_refs and position.instrument_ref not in request.instrument_refs:
            return False, "reconciliation_position_scope_mismatch"
        position_keys.add(key)
        expected_units.add(
            (position.instrument_ref, position.position_side, position.quantity_unit)
        )

    receipt = evidence.position_receipt
    if (
        receipt.scope_ref != request.scope_ref
        or receipt.account_ref != request.account_ref
        or receipt.strategy_ref != request.strategy_ref
        or receipt.source_ref != evidence.source_ref
        or receipt.generation != evidence.generation
        or receipt.snapshot_ref != evidence.snapshot_ref
        or receipt.lease_ref != lease.lease_ref
        or receipt.orders_digest != _rows_digest(evidence.orders)
        or receipt.fills_digest != _rows_digest(evidence.fills)
        or receipt.positions_digest != _rows_digest(evidence.positions)
        or set(receipt.quantity_units) != expected_units
        or receipt.consistent is not True
    ):
        return False, "position_reconciliation_receipt_mismatch"
    return True, "reconciliation_evidence_complete"


def _request_digest(request: ReconciliationRequest) -> str:
    return _sha256(
        {
            "scope_ref": request.scope_ref,
            "account_ref": request.account_ref,
            "venue_ref": request.venue_ref,
            "strategy_ref": request.strategy_ref,
            "instrument_refs": list(request.instrument_refs),
            "intent_refs": list(request.intent_refs),
        }
    )


def _evidence_digest(evidence: ReconciliationEvidence) -> str:
    return _sha256(
        {
            "request_digest": _request_digest(evidence.request),
            "issuer_ref": evidence.issuer_ref,
            "source_ref": evidence.source_ref,
            "generation": evidence.generation,
            "snapshot_ref": evidence.snapshot_ref,
            "consistency_ref": evidence.consistency_ref,
            "observed_at": _iso(evidence.observed_at),
            "coverage": (
                None
                if evidence.coverage is None
                else [_coverage_payload(item) for item in evidence.coverage]
            ),
            "orders": (
                None if evidence.orders is None else [_order_payload(item) for item in evidence.orders]
            ),
            "fills": (
                None if evidence.fills is None else [_fill_payload(item) for item in evidence.fills]
            ),
            "positions": (
                None
                if evidence.positions is None
                else [_position_payload(item) for item in evidence.positions]
            ),
            "unresolved_orders": (
                None
                if evidence.unresolved_orders is None
                else [
                    _terminal_order_payload(item) for item in evidence.unresolved_orders
                ]
            ),
            "unresolved_fills": (
                None
                if evidence.unresolved_fills is None
                else [
                    _unresolved_fill_payload(item) for item in evidence.unresolved_fills
                ]
            ),
            "unresolved_positions": (
                None
                if evidence.unresolved_positions is None
                else [
                    _unresolved_position_payload(item)
                    for item in evidence.unresolved_positions
                ]
            ),
            "position_receipt": _position_receipt_payload(evidence.position_receipt),
            "blockers": list(evidence.blockers),
        }
    )


def _rows_digest(rows: Sequence[object] | None) -> str:
    return _sha256([_row_payload(row) for row in rows or ()])


def _row_payload(row: object) -> dict[str, object]:
    if type(row) is ReconciledOrder:
        return _order_payload(row)
    if type(row) is ReconciliationFill:
        return _fill_payload(row)
    if type(row) is ReconciliationPosition:
        return _position_payload(row)
    if type(row) is TerminalOrderSnapshot:
        return _terminal_order_payload(row)
    if type(row) is UnresolvedFillObservation:
        return _unresolved_fill_payload(row)
    if type(row) is UnresolvedPositionObservation:
        return _unresolved_position_payload(row)
    raise TypeError("unsupported reconciliation row")


def _order_payload(row: ReconciledOrder) -> dict[str, object]:
    order = row.order
    return {
        "scope_ref": row.scope_ref,
        "account_ref": row.account_ref,
        "strategy_ref": row.strategy_ref,
        "source_ref": row.source_ref,
        "generation": row.generation,
        "snapshot_ref": row.snapshot_ref,
        "lease_ref": row.lease_ref,
        "intent_ref": row.intent_ref,
        "client_order_id": row.client_order_id,
        "order": {
            "scope_ref": order.scope_ref,
            "instrument_ref": order.instrument_ref,
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side,
            "status": order.status,
            "terminal_confirmed": order.terminal_confirmed,
            "quantity": str(order.quantity),
            "filled_quantity": str(order.filled_quantity),
            "quantity_unit": order.quantity_unit,
            "price": _decimal_text(order.price),
            "average_price": _decimal_text(order.average_price),
            "observed_at": _iso(order.observed_at),
        },
    }


def _fill_payload(fill: ReconciliationFill) -> dict[str, object]:
    commission = fill.commission
    return {
        "scope_ref": fill.scope_ref,
        "intent_ref": fill.intent_ref,
        "client_order_id": fill.client_order_id,
        "fill_id": fill.fill_id,
        "trade_id": fill.fill_id,
        "order_id": fill.order_id,
        "instrument_ref": fill.instrument_ref,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": str(fill.quantity),
        "quantity_unit": fill.quantity_unit,
        "price": str(fill.price),
        "fee": str(commission.amount) if commission is not None else None,
        "fee_currency": commission.currency if commission is not None else None,
        "fee_unit": commission.unit if commission is not None else None,
        "timestamp": fill.timestamp.isoformat(),
    }


def _terminal_order_payload(order: TerminalOrderSnapshot) -> dict[str, object]:
    return {
        "scope_ref": order.scope_ref,
        "instrument_ref": order.instrument_ref,
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side,
        "status": order.status,
        "terminal_confirmed": order.terminal_confirmed,
        "quantity": str(order.quantity),
        "filled_quantity": str(order.filled_quantity),
        "quantity_unit": order.quantity_unit,
        "price": _decimal_text(order.price),
        "average_price": _decimal_text(order.average_price),
        "observed_at": _iso(order.observed_at),
    }


def _unresolved_fill_payload(fill: UnresolvedFillObservation) -> dict[str, object]:
    commission = fill.commission
    return {
        "scope_ref": fill.scope_ref,
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "instrument_ref": fill.instrument_ref,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": str(fill.quantity),
        "quantity_unit": fill.quantity_unit,
        "price": str(fill.price),
        "timestamp": fill.timestamp.isoformat(),
        "commission": str(commission.amount) if commission is not None else None,
        "commission_currency": commission.currency if commission is not None else None,
        "commission_unit": commission.unit if commission is not None else None,
    }


def _unresolved_position_payload(
    position: UnresolvedPositionObservation,
) -> dict[str, object]:
    return {
        "scope_ref": position.scope_ref,
        "instrument_ref": position.instrument_ref,
        "symbol": position.symbol,
        "reported_position_side": position.reported_position_side,
        "quantity": str(position.quantity),
        "quantity_unit": position.quantity_unit,
        "observed_at": position.observed_at.isoformat(),
        "average_price": _decimal_text(position.average_price),
    }


def _position_payload(position: ReconciliationPosition) -> dict[str, object]:
    return {
        "scope_ref": position.scope_ref,
        "instrument_ref": position.instrument_ref,
        "symbol": position.symbol,
        "position_side": position.position_side,
        "quantity": str(position.quantity),
        "quantity_unit": position.quantity_unit,
        "average_price": _decimal_text(position.average_price),
    }


def _coverage_payload(item: ReconciliationCoverage) -> dict[str, object]:
    return {
        "family": item.family,
        "scope_ref": item.scope_ref,
        "source_ref": item.source_ref,
        "generation": item.generation,
        "snapshot_ref": item.snapshot_ref,
        "consistency_ref": item.consistency_ref,
        "complete": item.complete,
        "scope_complete": item.scope_complete,
        "history_complete": item.history_complete,
        "empty_result_proven": item.empty_result_proven,
        "blockers": list(item.blockers),
    }


def _position_receipt_payload(receipt: PositionReconciliationReceipt | None) -> object:
    if receipt is None:
        return None
    return {
        "receipt_ref": receipt.receipt_ref,
        "scope_ref": receipt.scope_ref,
        "account_ref": receipt.account_ref,
        "strategy_ref": receipt.strategy_ref,
        "source_ref": receipt.source_ref,
        "generation": receipt.generation,
        "snapshot_ref": receipt.snapshot_ref,
        "lease_ref": receipt.lease_ref,
        "orders_digest": receipt.orders_digest,
        "fills_digest": receipt.fills_digest,
        "positions_digest": receipt.positions_digest,
        "quantity_units": [list(row) for row in receipt.quantity_units],
        "consistent": receipt.consistent,
    }


def parse_reported_commission(
    amount: object, currency: object, unit: object
) -> tuple[CommissionAmount | None, str | None]:
    """Validate reported neutral commission fields without asserting their origin."""
    if amount is None and currency is None and unit is None:
        return None, "fee_unavailable"
    if (
        type(currency) is not str
        or not currency
        or currency != currency.strip()
        or unit != "currency"
    ):
        return None, "commission_currency_or_unit_unavailable"
    if isinstance(amount, bool) or amount in (None, ""):
        return None, "fee_amount_invalid"
    try:
        value = amount if type(amount) is Decimal else Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return None, "fee_amount_invalid"
    if not value.is_finite():
        return None, "fee_amount_invalid"
    try:
        return CommissionAmount(value, currency, unit), None
    except (TypeError, ValueError):
        return None, "commission_currency_or_unit_unavailable"


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CommissionAmount",
    "FeeRate",
    "PositionReconciliationReceipt",
    "ProviderReconciliationAuthority",
    "ReconciledOrder",
    "ReconciliationCoverage",
    "ReconciliationClient",
    "ReconciliationError",
    "ReconciliationEvidence",
    "ReconciliationFacade",
    "ReconciliationFill",
    "ReconciliationLease",
    "ReconciliationPosition",
    "ReconciliationRequest",
    "ReconciliationResult",
    "TerminalOrderSnapshot",
    "TerminalOrderReconciliationReceipt",
    "UnresolvedFillObservation",
    "UnresolvedPositionObservation",
    "parse_reported_commission",
    "validate_reconciliation_timeout",
    "validate_reconciliation_order_status",
]
