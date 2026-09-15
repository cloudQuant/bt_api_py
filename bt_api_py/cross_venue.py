"""Provider-neutral cross-venue execution-planning primitives.

The helpers deliberately have no broker, strategy, transport, or venue-schema
state. They consume the public typed SDK contracts and normalized Decimal
values, so Backtrader and other consumers can share quantity, depth, and cost
calculations without creating a second exchange client or duplicating vendor
normalization.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
from math import gcd
from typing import Any

from ._contracts.models import FeeSchedule, Freshness, FundingSnapshot, InstrumentSpec

DecimalInput = Decimal | int | str | float
DepthInput = Sequence[tuple[DecimalInput, DecimalInput]]

ORDERBOOK_HEALTHY_CONTINUITY = frozenset({"continuous", "ok", "recovered", "snapshot"})
ORDERBOOK_UNHEALTHY_CONTINUITY = frozenset(
    {"checksum_failed", "disconnected", "gap", "out_of_order", "stale"}
)


class CrossVenueValueError(ValueError):
    """A public cross-venue contract value is missing or unsafe."""


class InsufficientDepth(CrossVenueValueError):  # noqa: N818
    """The requested base quantity cannot be filled from the supplied book."""


# The name existed only in the unmerged Iteration 21 work. Keep a local alias
# while its two source examples migrate to the public cross-venue vocabulary.
CrossExchangeValueError = CrossVenueValueError


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise CrossVenueValueError(f"{field} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CrossVenueValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def coerce_funding_snapshot(
    value: FundingSnapshot | Mapping[str, Any],
    *,
    now_epoch: DecimalInput,
    expected_exchange_name: str | None = None,
    expected_symbol: str | None = None,
) -> FundingSnapshot:
    """Require one fresh, complete public :class:`FundingSnapshot`.

    Backtrader's compatibility APIs may serialize an SDK dataclass to a
    mapping.  This narrow adapter reconstructs the public type; it does not
    parse venue fields or invent defaults.  A schedule that expires between a
    cache read and a decision is rejected here.
    """

    if isinstance(value, FundingSnapshot):
        snapshot = value
    elif isinstance(value, Mapping):
        interval = value.get("settlement_interval_seconds")
        if value.get("available") is True and (
            isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0
        ):
            raise CrossVenueValueError("funding_interval_invalid")
        freshness_value = value.get("freshness")
        if isinstance(freshness_value, Freshness):
            freshness = freshness_value
        elif isinstance(freshness_value, Mapping):
            observed_at = _aware_datetime(freshness_value.get("observed_at"), "funding_observed_at")
            freshness = Freshness(
                source=str(freshness_value.get("source") or value.get("source") or "").strip(),
                observed_at=observed_at,
                stale=bool(freshness_value.get("stale", False)),
                stale_reason=(
                    None
                    if freshness_value.get("stale_reason") in (None, "")
                    else str(freshness_value.get("stale_reason"))
                ),
            )
        else:
            raise CrossVenueValueError("funding_freshness_missing")
        try:
            snapshot = FundingSnapshot(
                exchange_name=str(value.get("exchange_name") or ""),
                symbol=str(value.get("symbol") or ""),
                rate=value.get("rate"),
                next_funding_time=_aware_datetime(
                    value.get("next_funding_time"), "next_funding_time"
                )
                if value.get("next_funding_time") is not None
                else None,
                settlement_interval_seconds=interval,
                source=str(value.get("source") or "").strip(),
                freshness=freshness,
                available=value.get("available") is True,
                unavailable_reason=value.get("unavailable_reason"),
                raw=value.get("raw") if isinstance(value.get("raw"), Mapping) else {},
            )
        except (TypeError, ValueError) as exc:
            raise CrossVenueValueError(str(exc) or "funding_snapshot_invalid") from exc
    else:
        raise CrossVenueValueError("funding_snapshot_missing")

    if snapshot.available is not True:
        raise CrossVenueValueError(snapshot.unavailable_reason or "funding_unavailable")
    if snapshot.freshness.stale:
        raise CrossVenueValueError(snapshot.freshness.stale_reason or "funding_stale")
    if not snapshot.source:
        raise CrossVenueValueError("funding_source_missing")
    if expected_exchange_name and snapshot.exchange_name != expected_exchange_name:
        raise CrossVenueValueError("funding_exchange_name_mismatch")
    if expected_symbol and snapshot.symbol != expected_symbol:
        raise CrossVenueValueError("funding_symbol_mismatch")
    if snapshot.next_funding_time is None or snapshot.rate is None:
        raise CrossVenueValueError("funding_snapshot_incomplete")
    now = decimal_value(now_epoch, "funding_now_epoch")
    next_epoch = decimal_value(snapshot.next_funding_time.timestamp(), "next_funding_time")
    if next_epoch <= now:
        raise CrossVenueValueError("funding_schedule_expired")
    return snapshot


def normalize_orderbook_evidence(
    sequence: object,
    previous_sequence: object,
    snapshot_or_delta: object,
    continuity_status: object,
) -> tuple[int, int | None, str, str]:
    """Validate the venue-supplied identity and continuity of one L2 book.

    Sequence zero, implicit snapshot defaults, and ``unknown`` continuity are
    deliberately rejected.  They cannot prove that a consumer has a complete
    book and must never become a tradable observation.
    """

    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise CrossExchangeValueError("orderbook_sequence_missing_or_invalid")

    if not isinstance(snapshot_or_delta, str):
        raise CrossExchangeValueError("orderbook_snapshot_kind_missing_or_invalid")
    snapshot_kind = snapshot_or_delta.strip().lower()
    if snapshot_kind not in {"snapshot", "delta"}:
        raise CrossExchangeValueError("orderbook_snapshot_kind_missing_or_invalid")

    if not isinstance(continuity_status, str):
        raise CrossExchangeValueError("orderbook_continuity_missing_or_invalid")
    continuity = continuity_status.strip().lower()
    if continuity not in ORDERBOOK_HEALTHY_CONTINUITY | ORDERBOOK_UNHEALTHY_CONTINUITY:
        raise CrossExchangeValueError("orderbook_continuity_missing_or_invalid")

    previous = None
    if previous_sequence not in (None, ""):
        if isinstance(previous_sequence, bool) or not isinstance(previous_sequence, int):
            raise CrossExchangeValueError("orderbook_previous_sequence_missing_or_invalid")
        previous = previous_sequence
    if snapshot_kind == "delta" and previous is None:
        raise CrossExchangeValueError("orderbook_previous_sequence_missing_or_invalid")

    return sequence, previous, snapshot_kind, continuity


def decimal_value(value: DecimalInput, field: str = "value") -> Decimal:
    """Return a finite Decimal, converting floats through their text form."""

    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CrossExchangeValueError(f"{field} must be a decimal number") from exc
    if not result.is_finite():
        raise CrossExchangeValueError(f"{field} must be finite")
    return result


def _positive(value: DecimalInput, field: str, *, allow_zero: bool = False) -> Decimal:
    result = decimal_value(value, field)
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise CrossExchangeValueError(f"{field} must be {qualifier}")
    return result


@dataclass(frozen=True)
class CrossVenueLeg:
    """One leg's derived execution inputs, built from public SDK metadata.

    This is not a venue schema and not a paired-order engine. ``InstrumentSpec``
    remains the source of contract identity and quantity rules; this value only
    carries the fee and funding inputs needed to price a neutral two-leg
    opportunity.
    """

    multiplier: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    price_tick: Decimal
    taker_fee: Decimal
    funding_interval_seconds: Decimal = Decimal("28800")

    @classmethod
    def from_instrument_spec(
        cls,
        instrument: InstrumentSpec,
        *,
        taker_fee: DecimalInput,
        funding_interval_seconds: DecimalInput = Decimal("28800"),
    ) -> CrossVenueLeg:
        """Derive execution quantities from a complete public SDK contract."""

        if not isinstance(instrument, InstrumentSpec):
            raise CrossVenueValueError("instrument must be an InstrumentSpec")
        if not instrument.available:
            raise CrossVenueValueError(
                "instrument_spec_unavailable:" + str(instrument.unavailable_reason or "unknown")
            )
        if instrument.freshness.stale:
            raise CrossVenueValueError(instrument.freshness.stale_reason or "instrument_spec_stale")
        if not instrument.source:
            raise CrossVenueValueError("instrument_spec_source_missing")
        if not instrument.linear:
            raise CrossVenueValueError("cross_venue_leg_requires_linear_contract")
        required = (
            instrument.price_tick,
            instrument.quantity_step,
            instrument.min_quantity,
            instrument.min_notional,
        )
        if any(value is None for value in required):
            raise CrossVenueValueError("instrument_spec_missing_execution_rules")
        try:
            multiplier = instrument.native_to_base_quantity(Decimal(1))
        except (TypeError, ValueError) as exc:
            raise CrossVenueValueError("instrument_spec_invalid_quantity_conversion") from exc
        return cls(
            multiplier=multiplier,
            quantity_step=instrument.quantity_step,
            minimum_quantity=instrument.min_quantity,
            minimum_notional=instrument.min_notional,
            price_tick=instrument.price_tick,
            taker_fee=taker_fee,
            funding_interval_seconds=funding_interval_seconds,
        )

    @classmethod
    def from_sdk_contracts(
        cls,
        instrument: InstrumentSpec,
        fees: FeeSchedule | DecimalInput,
        funding: FundingSnapshot | DecimalInput = Decimal("28800"),
    ) -> CrossVenueLeg:
        """Build a leg from SDK metadata, fee, and funding contracts.

        A Decimal fee/funding interval is allowed for an explicitly labelled
        conservative replay or paper bound. Live callers should supply the
        typed account FeeSchedule and public FundingSnapshot.
        """

        if not isinstance(instrument, InstrumentSpec):
            raise CrossVenueValueError("instrument must be an InstrumentSpec")
        if isinstance(fees, FeeSchedule):
            if not fees.available or fees.taker_rate is None:
                raise CrossVenueValueError(fees.unavailable_reason or "fee_unavailable")
            if fees.freshness.stale:
                raise CrossVenueValueError(fees.freshness.stale_reason or "fee_stale")
            if not fees.source:
                raise CrossVenueValueError("fee_source_missing")
            if fees.exchange_name != instrument.exchange_name:
                raise CrossVenueValueError("fee_exchange_name_mismatch")
            if fees.symbol != instrument.symbol:
                raise CrossVenueValueError("fee_symbol_mismatch")
            taker_fee = fees.taker_rate
        else:
            taker_fee = fees
        if isinstance(funding, FundingSnapshot):
            if not funding.available or funding.settlement_interval_seconds is None:
                raise CrossVenueValueError(funding.unavailable_reason or "funding_unavailable")
            if funding.freshness.stale:
                raise CrossVenueValueError(funding.freshness.stale_reason or "funding_stale")
            if not funding.source:
                raise CrossVenueValueError("funding_source_missing")
            if funding.exchange_name != instrument.exchange_name:
                raise CrossVenueValueError("funding_exchange_name_mismatch")
            if funding.symbol != instrument.symbol:
                raise CrossVenueValueError("funding_symbol_mismatch")
            interval = Decimal(funding.settlement_interval_seconds)
        else:
            interval = funding
        return cls.from_instrument_spec(
            instrument,
            taker_fee=taker_fee,
            funding_interval_seconds=interval,
        )

    def __post_init__(self) -> None:
        for name in ("multiplier", "quantity_step", "minimum_quantity", "price_tick"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        for name in ("minimum_notional", "taker_fee"):
            object.__setattr__(self, name, _positive(getattr(self, name), name, allow_zero=True))
        object.__setattr__(
            self,
            "funding_interval_seconds",
            _positive(self.funding_interval_seconds, "funding_interval_seconds"),
        )

    @property
    def base_step(self) -> Decimal:
        return self.multiplier * self.quantity_step

    @property
    def base_minimum(self) -> Decimal:
        return self.multiplier * self.minimum_quantity

    def native_to_base(self, native_quantity: DecimalInput) -> Decimal:
        return decimal_value(native_quantity, "native_quantity") * self.multiplier

    def base_to_native(self, base_quantity: DecimalInput) -> Decimal:
        return decimal_value(base_quantity, "base_quantity") / self.multiplier

    def quantize_native_down(self, native_quantity: DecimalInput) -> Decimal:
        quantity = _positive(native_quantity, "native_quantity", allow_zero=True)
        steps = (quantity / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return steps * self.quantity_step

    def quantize_price(self, price: DecimalInput, side: str) -> Decimal:
        value = _positive(price, "price")
        if side not in {"buy", "sell"}:
            raise CrossExchangeValueError("side must be buy or sell")
        rounding = ROUND_CEILING if side == "buy" else ROUND_DOWN
        return (value / self.price_tick).to_integral_value(rounding=rounding) * self.price_tick


# ``InstrumentRule`` was introduced only by the unmerged Iteration 21 branch.
# Retain it as a source-compatibility alias while callers migrate to the
# explicit cross-venue terminology.
InstrumentRule = CrossVenueLeg


@dataclass(frozen=True)
class QuantityLattice:
    requested_base: Decimal
    common_step_base: Decimal
    minimum_base: Decimal
    quantity_base: Decimal
    tradable: bool

    def as_dict(self) -> Mapping[str, str | bool]:
        return {
            "requested_base": str(self.requested_base),
            "common_step_base": str(self.common_step_base),
            "minimum_base": str(self.minimum_base),
            "quantity_base": str(self.quantity_base),
            "tradable": self.tradable,
        }


def _decimal_lcm(values: Iterable[Decimal]) -> Decimal:
    normalized = [decimal_value(value).normalize() for value in values]
    if not normalized or any(value <= 0 for value in normalized):
        raise CrossExchangeValueError("quantity steps must be positive")
    scale_places = max(max(0, -value.as_tuple().exponent) for value in normalized)
    scale = Decimal(1).scaleb(-scale_places)
    integers = [int((value / scale).to_integral_exact()) for value in normalized]
    result = integers[0]
    for value in integers[1:]:
        result = result * value // gcd(result, value)
    return Decimal(result) * scale


def quantity_lattice(
    requested_base: DecimalInput, rules: Iterable[CrossVenueLeg]
) -> QuantityLattice:
    """Floor a base quantity to the common native lot lattice."""

    rule_list = tuple(rules)
    if not rule_list:
        raise CrossExchangeValueError("at least one instrument rule is required")
    requested = _positive(requested_base, "requested_base", allow_zero=True)
    step = _decimal_lcm(rule.base_step for rule in rule_list)
    raw_minimum = max(rule.base_minimum for rule in rule_list)
    minimum = (raw_minimum / step).to_integral_value(rounding=ROUND_CEILING) * step
    quantity = (requested / step).to_integral_value(rounding=ROUND_DOWN) * step
    return QuantityLattice(requested, step, minimum, quantity, quantity >= minimum and quantity > 0)


@dataclass(frozen=True)
class ExecutableVWAP:
    side: str
    quantity_base: Decimal
    price: Decimal
    notional: Decimal
    top_price: Decimal
    depth_impact: Decimal
    levels_consumed: int
    marginal_price: Decimal

    def as_dict(self) -> Mapping[str, str | int]:
        return {
            "side": self.side,
            "quantity_base": str(self.quantity_base),
            "price": str(self.price),
            "notional": str(self.notional),
            "top_price": str(self.top_price),
            "depth_impact": str(self.depth_impact),
            "levels_consumed": self.levels_consumed,
            "marginal_price": str(self.marginal_price),
        }


def executable_vwap(levels: DepthInput, quantity_base: DecimalInput, side: str) -> ExecutableVWAP:
    """Calculate the executable VWAP for base-denominated depth levels.

    Levels must already be ordered in execution order: asks low-to-high for a
    buy and bids high-to-low for a sell.  Quantities are base units.
    """

    if side not in {"buy", "sell"}:
        raise CrossExchangeValueError("side must be buy or sell")
    target = _positive(quantity_base, "quantity_base")
    parsed = [
        (_positive(price, "level_price"), _positive(size, "level_quantity"))
        for price, size in levels
    ]
    if not parsed:
        raise InsufficientDepth("order book has no depth")
    for previous, current in zip(parsed, parsed[1:], strict=False):
        ordered = current[0] >= previous[0] if side == "buy" else current[0] <= previous[0]
        if not ordered:
            raise CrossExchangeValueError("depth levels are not in executable order")
    remaining = target
    notional = Decimal(0)
    consumed = 0
    marginal = parsed[0][0]
    for price, available in parsed:
        take = min(remaining, available)
        notional += take * price
        remaining -= take
        consumed += 1
        marginal = price
        if remaining == 0:
            break
    if remaining > 0:
        raise InsufficientDepth(f"requested {target} base units but depth is short by {remaining}")
    vwap = notional / target
    top = parsed[0][0]
    depth_impact = abs(vwap - top) * target
    return ExecutableVWAP(side, target, vwap, notional, top, depth_impact, consumed, marginal)


def aggregate_confirmed_fills(
    fills: DepthInput,
    *,
    side: str,
    expected_quantity_base: DecimalInput | None = None,
) -> ExecutableVWAP:
    """Aggregate confirmed ``(price, base quantity)`` fills without reordering them."""

    if side not in {"buy", "sell"}:
        raise CrossExchangeValueError("side must be buy or sell")
    parsed = tuple(
        (_positive(price, "fill_price"), _positive(quantity, "fill_quantity"))
        for price, quantity in fills
    )
    if not parsed:
        raise CrossExchangeValueError("at least one confirmed fill is required")
    quantity = sum((item[1] for item in parsed), Decimal(0))
    if expected_quantity_base is not None:
        expected = _positive(expected_quantity_base, "expected_quantity_base")
        if quantity != expected:
            raise CrossExchangeValueError(
                "confirmed fill quantity does not match expected quantity"
            )
    notional = sum((price * size for price, size in parsed), Decimal(0))
    vwap = notional / quantity
    top = parsed[0][0]
    return ExecutableVWAP(
        side=side,
        quantity_base=quantity,
        price=vwap,
        notional=notional,
        top_price=top,
        depth_impact=abs(vwap - top) * quantity,
        levels_consumed=len(parsed),
        marginal_price=parsed[-1][0],
    )


def funding_settlement_count(
    now_epoch: DecimalInput,
    next_funding_epoch: DecimalInput | None,
    holding_seconds: DecimalInput,
    interval_seconds: DecimalInput = Decimal("28800"),
) -> int:
    """Count settlements in ``(now, now + holding]`` without sampling clocks."""

    if next_funding_epoch is None:
        return 0
    now = decimal_value(now_epoch, "now_epoch")
    next_time = decimal_value(next_funding_epoch, "next_funding_epoch")
    holding = _positive(holding_seconds, "holding_seconds", allow_zero=True)
    interval = _positive(interval_seconds, "interval_seconds")
    end = now + holding
    if next_time <= now:
        elapsed_intervals = int(
            ((now - next_time) / interval).to_integral_value(rounding=ROUND_DOWN)
        )
        next_time += Decimal(elapsed_intervals + 1) * interval
    if next_time > end:
        return 0
    return 1 + int(((end - next_time) / interval).to_integral_value(rounding=ROUND_DOWN))


def signed_funding_cashflow(
    notional: DecimalInput,
    funding_rate: DecimalInput,
    position_side: str,
    settlements: int,
) -> Decimal:
    """Return expected account cashflow; positive means cash received."""

    value = _positive(notional, "notional", allow_zero=True)
    rate = decimal_value(funding_rate, "funding_rate")
    if position_side not in {"long", "short"}:
        raise CrossExchangeValueError("position_side must be long or short")
    if not isinstance(settlements, int) or settlements < 0:
        raise CrossExchangeValueError("settlements must be a nonnegative integer")
    direction = Decimal(-1) if position_side == "long" else Decimal(1)
    return value * rate * direction * settlements


@dataclass(frozen=True)
class CostBreakdown:
    """Complete round-trip estimate.  Every cost component is applied once."""

    quantity_base: Decimal
    entry_executable_edge: Decimal
    expected_exit_basis: Decimal
    expected_exit_basis_notional: Decimal
    expected_gross_convergence: Decimal
    entry_fees: Decimal
    expected_exit_execution_cost: Decimal
    predicted_exit_fees: Decimal
    signed_funding_cashflow: Decimal
    latency_adverse_selection_reserve: Decimal
    failure_leg_reserve: Decimal
    model_error_buffer: Decimal
    entry_buy_depth_impact_audit: Decimal
    entry_sell_depth_impact_audit: Decimal
    total_cost: Decimal
    expected_net: Decimal

    def as_dict(self) -> Mapping[str, str]:
        return {field.name: str(getattr(self, field.name)) for field in fields(self)}

    def assert_conserved(self) -> None:
        if self.expected_exit_basis_notional != self.quantity_base * self.expected_exit_basis:
            raise AssertionError("expected exit basis notional is not conserved")
        if (
            self.expected_gross_convergence
            != self.entry_executable_edge - self.expected_exit_basis_notional
        ):
            raise AssertionError("expected gross convergence is not conserved")
        expected_total = (
            self.entry_fees
            + self.expected_exit_execution_cost
            + self.predicted_exit_fees
            + self.latency_adverse_selection_reserve
            + self.failure_leg_reserve
            + self.model_error_buffer
            - self.signed_funding_cashflow
        )
        if self.total_cost != expected_total:
            raise AssertionError("cost components do not sum to total_cost")
        if self.expected_net != self.expected_gross_convergence - self.total_cost:
            raise AssertionError("expected_net is not conserved")


@dataclass(frozen=True)
class RealizedEconomics:
    """Actual four-fill economics plus a separately labelled risk preview.

    The exit leg notionals already contain the executable close spread and
    depth impact.  Consequently, an entry-time ``expected_exit_execution_cost``
    must never be subtracted from these values again.  Forecast reserves remain
    visible in ``risk_adjusted_net`` without being presented as cash PnL.
    """

    quantity_base: Decimal
    entry_executable_edge: Decimal
    exit_executable_edge: Decimal
    gross_pnl: Decimal
    entry_fees: Decimal
    exit_fees: Decimal
    signed_funding_cashflow: Decimal
    failure_leg_loss: Decimal
    realized_net: Decimal
    latency_reserve: Decimal
    failure_reserve: Decimal
    model_buffer: Decimal
    total_preview_reserve: Decimal
    risk_adjusted_net: Decimal

    def as_dict(self) -> Mapping[str, str]:
        return {field.name: str(getattr(self, field.name)) for field in fields(self)}

    def assert_conserved(self) -> None:
        expected_gross = self.entry_executable_edge + self.exit_executable_edge
        if self.gross_pnl != expected_gross:
            raise AssertionError("realized gross does not conserve the four executable fills")
        expected_net = (
            self.gross_pnl
            - self.entry_fees
            - self.exit_fees
            + self.signed_funding_cashflow
            - self.failure_leg_loss
        )
        if self.realized_net != expected_net:
            raise AssertionError("realized net does not conserve fees, funding, and leg loss")
        expected_reserve = self.latency_reserve + self.failure_reserve + self.model_buffer
        if self.total_preview_reserve != expected_reserve:
            raise AssertionError("preview reserves do not sum")
        if self.risk_adjusted_net != self.realized_net - self.total_preview_reserve:
            raise AssertionError("risk-adjusted net does not conserve preview reserves")


def realized_round_trip_economics(
    *,
    quantity_base: DecimalInput,
    entry_buy: ExecutableVWAP,
    entry_sell: ExecutableVWAP,
    exit_sell: ExecutableVWAP,
    exit_buy: ExecutableVWAP,
    buy_venue_fee_rate: DecimalInput,
    sell_venue_fee_rate: DecimalInput,
    entry_fees_paid: DecimalInput | None = None,
    exit_fees_paid: DecimalInput | None = None,
    signed_funding: DecimalInput = Decimal(0),
    failure_leg_loss: DecimalInput = Decimal(0),
    latency_reserve: DecimalInput = Decimal(0),
    failure_reserve: DecimalInput = Decimal(0),
    model_buffer: DecimalInput = Decimal(0),
) -> RealizedEconomics:
    """Calculate actual pair PnL without reusing the forecast exit reserve.

    ``entry_buy`` and ``exit_sell`` belong to the long venue. ``entry_sell``
    and ``exit_buy`` belong to the short venue. All four VWAP quantities must
    match the confirmed base quantity.
    """

    quantity = _positive(quantity_base, "quantity_base")
    fills = (entry_buy, entry_sell, exit_sell, exit_buy)
    if any(fill.quantity_base != quantity for fill in fills):
        raise CrossExchangeValueError("all realized VWAP quantities must match quantity_base")
    if entry_buy.side != "buy" or entry_sell.side != "sell":
        raise CrossExchangeValueError("entry VWAP sides are inconsistent")
    if exit_sell.side != "sell" or exit_buy.side != "buy":
        raise CrossExchangeValueError("exit VWAP sides are inconsistent")

    buy_fee = _positive(buy_venue_fee_rate, "buy_venue_fee_rate", allow_zero=True)
    sell_fee = _positive(sell_venue_fee_rate, "sell_venue_fee_rate", allow_zero=True)
    funding = decimal_value(signed_funding, "signed_funding")
    leg_loss = _positive(failure_leg_loss, "failure_leg_loss", allow_zero=True)
    latency = _positive(latency_reserve, "latency_reserve", allow_zero=True)
    failure = _positive(failure_reserve, "failure_reserve", allow_zero=True)
    buffer = _positive(model_buffer, "model_buffer", allow_zero=True)

    entry_edge = entry_sell.notional - entry_buy.notional
    exit_edge = exit_sell.notional - exit_buy.notional
    gross = entry_edge + exit_edge
    entry_fees = (
        entry_buy.notional * buy_fee + entry_sell.notional * sell_fee
        if entry_fees_paid is None
        else _positive(entry_fees_paid, "entry_fees_paid", allow_zero=True)
    )
    exit_fees = (
        exit_sell.notional * buy_fee + exit_buy.notional * sell_fee
        if exit_fees_paid is None
        else _positive(exit_fees_paid, "exit_fees_paid", allow_zero=True)
    )
    realized_net = gross - entry_fees - exit_fees + funding - leg_loss
    preview_reserve = latency + failure + buffer
    result = RealizedEconomics(
        quantity_base=quantity,
        entry_executable_edge=entry_edge,
        exit_executable_edge=exit_edge,
        gross_pnl=gross,
        entry_fees=entry_fees,
        exit_fees=exit_fees,
        signed_funding_cashflow=funding,
        failure_leg_loss=leg_loss,
        realized_net=realized_net,
        latency_reserve=latency,
        failure_reserve=failure,
        model_buffer=buffer,
        total_preview_reserve=preview_reserve,
        risk_adjusted_net=realized_net - preview_reserve,
    )
    result.assert_conserved()
    return result


def round_trip_cost(
    *,
    quantity_base: DecimalInput,
    entry_buy: ExecutableVWAP,
    entry_sell: ExecutableVWAP,
    buy_fee_rate: DecimalInput,
    sell_fee_rate: DecimalInput,
    expected_exit_basis: DecimalInput,
    expected_exit_buy_price: DecimalInput,
    expected_exit_sell_price: DecimalInput,
    expected_exit_execution_cost: DecimalInput,
    signed_funding: DecimalInput = Decimal(0),
    latency_reserve: DecimalInput = Decimal(0),
    failure_reserve: DecimalInput = Decimal(0),
    model_buffer: DecimalInput = Decimal(0),
) -> CostBreakdown:
    """Evaluate a four-fill round trip from L2 entry prices.

    ``entry_executable_edge`` uses the two VWAP notionals directly, so entry
    spread and depth impact are already included. ``expected_exit_basis`` is
    the signed, per-base-unit model target for ``sell venue - buy venue`` at
    close.  The forecast can therefore capture only convergence from the
    executable entry edge to that target; a persistent cross-venue basis is
    never treated as free profit.  Projected close spread/depth belongs in
    ``expected_exit_execution_cost`` exactly once.
    """

    quantity = _positive(quantity_base, "quantity_base")
    if entry_buy.quantity_base != quantity or entry_sell.quantity_base != quantity:
        raise CrossExchangeValueError("entry VWAP quantities must match quantity_base")
    buy_fee = _positive(buy_fee_rate, "buy_fee_rate", allow_zero=True)
    sell_fee = _positive(sell_fee_rate, "sell_fee_rate", allow_zero=True)
    exit_basis = decimal_value(expected_exit_basis, "expected_exit_basis")
    exit_buy = _positive(expected_exit_buy_price, "expected_exit_buy_price")
    exit_sell = _positive(expected_exit_sell_price, "expected_exit_sell_price")
    exit_cost = _positive(
        expected_exit_execution_cost, "expected_exit_execution_cost", allow_zero=True
    )
    funding = decimal_value(signed_funding, "signed_funding")
    latency = _positive(latency_reserve, "latency_reserve", allow_zero=True)
    failure = _positive(failure_reserve, "failure_reserve", allow_zero=True)
    buffer = _positive(model_buffer, "model_buffer", allow_zero=True)

    entry_edge = entry_sell.notional - entry_buy.notional
    exit_basis_notional = quantity * exit_basis
    expected_gross = entry_edge - exit_basis_notional
    entry_fees = entry_buy.notional * buy_fee + entry_sell.notional * sell_fee
    predicted_exit_fees = quantity * exit_buy * sell_fee + quantity * exit_sell * buy_fee
    total = entry_fees + exit_cost + predicted_exit_fees + latency + failure + buffer - funding
    result = CostBreakdown(
        quantity_base=quantity,
        entry_executable_edge=entry_edge,
        expected_exit_basis=exit_basis,
        expected_exit_basis_notional=exit_basis_notional,
        expected_gross_convergence=expected_gross,
        entry_fees=entry_fees,
        expected_exit_execution_cost=exit_cost,
        predicted_exit_fees=predicted_exit_fees,
        signed_funding_cashflow=funding,
        latency_adverse_selection_reserve=latency,
        failure_leg_reserve=failure,
        model_error_buffer=buffer,
        entry_buy_depth_impact_audit=entry_buy.depth_impact,
        entry_sell_depth_impact_audit=entry_sell.depth_impact,
        total_cost=total,
        expected_net=expected_gross - total,
    )
    result.assert_conserved()
    return result


__all__ = [
    "CostBreakdown",
    "CrossVenueLeg",
    "CrossVenueValueError",
    "CrossExchangeValueError",
    "ExecutableVWAP",
    "InsufficientDepth",
    "InstrumentRule",
    "QuantityLattice",
    "RealizedEconomics",
    "aggregate_confirmed_fills",
    "coerce_funding_snapshot",
    "decimal_value",
    "executable_vwap",
    "funding_settlement_count",
    "normalize_orderbook_evidence",
    "quantity_lattice",
    "realized_round_trip_economics",
    "round_trip_cost",
    "signed_funding_cashflow",
]
