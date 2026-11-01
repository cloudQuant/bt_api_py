"""Provider-neutral aggregation of SDK market ticks into synchronized cohorts.

This module accepts only the public ``MarketTickEvent`` DTO. It does not read
provider messages, create clock evidence, or grant execution authority. A
cohort is returned only when every configured leg has a newer quote in one
source scope and the caller supplies verified current-time evidence.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Optional, Tuple

_MAX_NUMBER = 1.0e30
_MAX_SEQUENCE = (1 << 64) - 1
_PLACEHOLDERS = frozenset(
    {"unknown", "unverified", "n/a", "na", "none", "null", "unset", "placeholder"}
)


class MarketCohortReason:
    WAITING_FOR_LEGS = "WAITING_FOR_LEGS"
    WAITING_FOR_ALL_LEGS_NEW = "WAITING_FOR_ALL_LEGS_NEW"
    UNEXPECTED_SYMBOL = "UNEXPECTED_SYMBOL"
    QUOTE_SYMBOL_MISSING = "QUOTE_SYMBOL_MISSING"
    QUOTE_IDENTITY_CONFLICT = "QUOTE_IDENTITY_CONFLICT"
    EXCHANGE_MISMATCH = "EXCHANGE_MISMATCH"
    ASSET_TYPE_MISMATCH = "ASSET_TYPE_MISMATCH"
    MARKET_TICK_API_UNAVAILABLE = "MARKET_TICK_API_UNAVAILABLE"
    UNSUPPORTED_QUOTE_SCHEMA = "UNSUPPORTED_QUOTE_SCHEMA"
    VOLUME_SEMANTICS_NOT_DELTA = "VOLUME_SEMANTICS_NOT_DELTA"
    SOURCE_CLOCK_UNVERIFIED = "SOURCE_CLOCK_UNVERIFIED"
    RECEIVE_CLOCK_UNVERIFIED = "RECEIVE_CLOCK_UNVERIFIED"
    FRESHNESS_UNVERIFIED = "FRESHNESS_UNVERIFIED"
    EVENT_TIME_SOURCE_MISSING = "EVENT_TIME_SOURCE_MISSING"
    RULES_HASH_MISMATCH = "RULES_HASH_MISMATCH"
    QUOTE_SOURCE_MISSING = "QUOTE_SOURCE_MISSING"
    QUOTE_STREAM_UNREADY = "QUOTE_STREAM_UNREADY"
    QUOTE_CONTINUITY_NOT_CONTINUOUS = "QUOTE_CONTINUITY_NOT_CONTINUOUS"
    QUOTE_QUALITY_FLAGS_INVALID = "QUOTE_QUALITY_FLAGS_INVALID"
    QUOTE_QUALITY_FLAGS_PRESENT = "QUOTE_QUALITY_FLAGS_PRESENT"
    EXECUTION_INELIGIBLE_QUOTE = "EXECUTION_INELIGIBLE_QUOTE"
    VOLUME_INCOMPLETE = "VOLUME_INCOMPLETE"
    VOLUME_QUALITY_NOT_CONTINUOUS = "VOLUME_QUALITY_NOT_CONTINUOUS"
    QUOTE_NUMERIC_TYPE_INVALID = "QUOTE_NUMERIC_TYPE_INVALID"
    QUOTE_NUMERIC_INVALID = "QUOTE_NUMERIC_INVALID"
    QUOTE_NONPOSITIVE = "QUOTE_NONPOSITIVE"
    QUOTE_CROSSED = "QUOTE_CROSSED"
    DAILY_PRICE_LIMIT_INVALID = "DAILY_PRICE_LIMIT_INVALID"
    QUOTE_OUTSIDE_DAILY_LIMIT = "QUOTE_OUTSIDE_DAILY_LIMIT"
    QUOTE_OFF_TICK_GRID = "QUOTE_OFF_TICK_GRID"
    QUOTE_IDENTITY_TYPE_INVALID = "QUOTE_IDENTITY_TYPE_INVALID"
    QUOTE_IDENTITY_OR_CLOCK_MISSING = "QUOTE_IDENTITY_OR_CLOCK_MISSING"
    TRADING_DATE_INVALID = "TRADING_DATE_INVALID"
    EVENT_SESSION_DATE_INVALID = "EVENT_SESSION_DATE_INVALID"
    CLOCK_DOMAIN_UNKNOWN = "CLOCK_DOMAIN_UNKNOWN"
    SOURCE_TIME_INVALID = "SOURCE_TIME_INVALID"
    RECEIVE_TIME_INVALID = "RECEIVE_TIME_INVALID"
    SOURCE_TIME_AFTER_RECEIVE = "SOURCE_TIME_AFTER_RECEIVE"
    SOURCE_CLOCK_ERROR_INVALID = "SOURCE_CLOCK_ERROR_INVALID"
    RECEIVE_CLOCK_ERROR_INVALID = "RECEIVE_CLOCK_ERROR_INVALID"
    DUPLICATE_OR_OUT_OF_ORDER = "DUPLICATE_OR_OUT_OF_ORDER"
    OUT_OF_ORDER_RECEIVE_TIME = "OUT_OF_ORDER_RECEIVE_TIME"
    OUT_OF_ORDER_SOURCE_TIME = "OUT_OF_ORDER_SOURCE_TIME"
    COHORT_EXCHANGE_MISMATCH = "COHORT_EXCHANGE_MISMATCH"
    COHORT_TRADING_DATE_MISMATCH = "COHORT_TRADING_DATE_MISMATCH"
    COHORT_EVENT_SESSION_DATE_MISMATCH = "COHORT_EVENT_SESSION_DATE_MISMATCH"
    COHORT_CONNECTION_GENERATION_MISMATCH = "COHORT_CONNECTION_GENERATION_MISMATCH"
    COHORT_SUBSCRIPTION_EPOCH_MISMATCH = "COHORT_SUBSCRIPTION_EPOCH_MISMATCH"
    COHORT_RULES_HASH_MISMATCH = "COHORT_RULES_HASH_MISMATCH"
    COHORT_CLOCK_DOMAIN_MISMATCH = "COHORT_CLOCK_DOMAIN_MISMATCH"
    STALE_COHORT_RECEIVE_TIME = "STALE_COHORT_RECEIVE_TIME"
    BLOCKED_CROSS_LEG_SKEW = "BLOCKED_CROSS_LEG_SKEW"
    STALE_COHORT_SOURCE_TIME = "STALE_COHORT_SOURCE_TIME"
    BLOCKED_SOURCE_SKEW = "BLOCKED_SOURCE_SKEW"
    TRUSTED_NOW_REQUIRED = "TRUSTED_NOW_REQUIRED"
    TRUSTED_NOW_INVALID = "TRUSTED_NOW_INVALID"
    NOW_CLOCK_DOMAIN_MISMATCH = "NOW_CLOCK_DOMAIN_MISMATCH"
    NOW_WALL_TIME_BEFORE_QUOTE = "NOW_WALL_TIME_BEFORE_QUOTE"
    RETIRED_CONNECTION_SCOPE = "RETIRED_CONNECTION_SCOPE"
    NO_CONFIRMED_COHORT = "NO_CONFIRMED_COHORT"


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a finite number")
    result = float(value)
    if not math.isfinite(result) or abs(result) >= _MAX_NUMBER or (positive and result <= 0):
        raise ValueError("value must be a finite number in range")
    return result


def _text(value: Any, *, identity: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("value must be non-empty trimmed text")
    if identity and value.casefold() in _PLACEHOLDERS:
        raise ValueError("value must identify an observed source")
    return value


def _positive_sequence(value: Any) -> Optional[int]:
    if type(value) is not int or not 0 < value <= _MAX_SEQUENCE:
        return None
    return value


def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        try:
            value = value.astimezone(timezone.utc).timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        try:
            value = parsed.astimezone(timezone.utc).timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    elif not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return result if math.isfinite(result) and abs(result) < _MAX_NUMBER else None


def _date_digits(value: Any) -> Optional[str]:
    if type(value) is not str:
        return None
    text_value = value.replace("-", "") if len(value) == 10 else value
    if len(text_value) != 8 or not text_value.isascii() or not text_value.isdecimal():
        return None
    try:
        datetime.strptime(text_value, "%Y%m%d")
    except ValueError:
        return None
    return text_value


def _event_date(value: Any) -> Optional[str]:
    if type(value) is not str or len(value) != 10:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y%m%d")


def _compact_date(value: Any) -> bool:
    if type(value) is not str or len(value) != 8 or not value.isascii() or not value.isdecimal():
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _tick_aligned(price: float, tick: float) -> bool:
    try:
        return Decimal(str(price)).remainder_near(Decimal(str(tick))) == 0
    except (InvalidOperation, ValueError):
        return False


def _read(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _read_first(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


@dataclass(frozen=True)
class MarketCohortLeg:
    symbol: str
    exchange: str
    price_tick: float
    asset_type: Optional[str] = None

    def __post_init__(self) -> None:
        _text(self.symbol)
        _text(self.exchange)
        object.__setattr__(self, "price_tick", _number(self.price_tick, positive=True))
        if self.asset_type is not None and self.asset_type not in {"future", "option"}:
            raise ValueError("asset_type must be future, option, or None")


@dataclass(frozen=True)
class MarketCohortPolicy:
    max_receive_age_ms: float
    max_receive_skew_ms: float
    max_source_age_ms: float
    max_source_skew_ms: float
    max_source_clock_error_ms: float
    max_receive_clock_error_ms: float

    def __post_init__(self) -> None:
        for name in (
            "max_receive_age_ms",
            "max_receive_skew_ms",
            "max_source_age_ms",
            "max_source_skew_ms",
            "max_source_clock_error_ms",
            "max_receive_clock_error_ms",
        ):
            object.__setattr__(self, name, _number(getattr(self, name)))
            if getattr(self, name) < 0:
                raise ValueError("cohort time bounds must be non-negative")


@dataclass(frozen=True)
class MarketCohortNow:
    now_monotonic_ns: int
    now_epoch: float
    clock_domain_id: str
    receive_clock_error_ms: float
    receive_clock_quality: str = "verified"
    freshness_verified: bool = True

    def __post_init__(self) -> None:
        epoch = _epoch(self.now_epoch)
        if _positive_sequence(self.now_monotonic_ns) is None or epoch is None:
            raise ValueError("trusted time evidence is invalid")
        _text(self.clock_domain_id, identity=True)
        if self.receive_clock_quality != "verified" or self.freshness_verified is not True:
            raise ValueError("trusted receive clock and freshness must be verified")
        error = _number(self.receive_clock_error_ms)
        if error < 0:
            raise ValueError("receive clock uncertainty must be non-negative")
        object.__setattr__(self, "now_epoch", epoch)
        object.__setattr__(self, "receive_clock_error_ms", error)


@dataclass(frozen=True)
class MarketQuoteEvidence:
    symbol: str
    exchange: str
    asset_type: Optional[str]
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    last: float
    lower_limit: float
    upper_limit: float
    source_epoch: float
    receive_epoch: float
    receive_monotonic_ns: int
    ingest_seq: int
    connection_generation: int
    subscription_epoch: int
    trading_date: str
    event_session_date: str
    clock_domain_id: str
    rules_hash: str
    source: str
    event_time_source: str
    source_clock_error_ms: float
    receive_clock_error_ms: float

    @property
    def update_identity(self) -> Tuple[str, int, int, int]:
        return (self.symbol, self.connection_generation, self.subscription_epoch, self.ingest_seq)


@dataclass(frozen=True)
class MarketQuoteValidation:
    quote: Optional[MarketQuoteEvidence]
    reason: Optional[str]

    @property
    def accepted(self) -> bool:
        return self.quote is not None


@dataclass(frozen=True)
class MarketQuoteCohort:
    quotes: Mapping[str, MarketQuoteEvidence]
    exchange: str
    trading_date: str
    event_session_date: str
    connection_generation: int
    subscription_epoch: int
    clock_domain_id: str
    rules_hash: str
    cohort_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.quotes, Mapping) or not self.quotes:
            raise ValueError("quotes must be a non-empty mapping")
        copied = dict(self.quotes)
        if any(not isinstance(item, MarketQuoteEvidence) for item in copied.values()):
            raise TypeError("quotes must contain MarketQuoteEvidence values")
        if any(symbol != item.symbol for symbol, item in copied.items()):
            raise ValueError("quote keys must match their symbols")
        _text(self.exchange)
        if not _compact_date(self.trading_date) or not _compact_date(self.event_session_date):
            raise ValueError("cohort dates must be valid YYYYMMDD values")
        if _positive_sequence(self.connection_generation) is None:
            raise ValueError("connection_generation is invalid")
        if _positive_sequence(self.subscription_epoch) is None:
            raise ValueError("subscription_epoch is invalid")
        _text(self.clock_domain_id, identity=True)
        _text(self.rules_hash, identity=True)
        _text(self.cohort_id)
        for quote in copied.values():
            if (
                quote.exchange != self.exchange
                or quote.trading_date != self.trading_date
                or quote.event_session_date != self.event_session_date
                or quote.connection_generation != self.connection_generation
                or quote.subscription_epoch != self.subscription_epoch
                or quote.clock_domain_id != self.clock_domain_id
                or quote.rules_hash != self.rules_hash
            ):
                raise ValueError("cohort facts must match all quote records")
        object.__setattr__(self, "quotes", MappingProxyType(copied))

    def quote_for(self, symbol: str) -> MarketQuoteEvidence:
        return self.quotes[symbol]


@dataclass(frozen=True)
class MarketCohortResult:
    cohort: Optional[MarketQuoteCohort]
    reason: Optional[str]

    @property
    def accepted(self) -> bool:
        return self.cohort is not None


def _tick_type():
    try:
        from bt_api_py.market_tick_contracts import MarketTickEvent
    except (ImportError, AttributeError):
        return None
    return MarketTickEvent


def _now_evidence(value: Any, policy: MarketCohortPolicy):
    if value is None:
        return None, MarketCohortReason.TRUSTED_NOW_REQUIRED
    if isinstance(value, MarketCohortNow):
        now = value
    else:
        monotonic = _positive_sequence(
            _read_first(value, "now_monotonic_ns", "recv_monotonic_ns", "received_monotonic_ns")
        )
        epoch = _epoch(
            _read_first(value, "now_epoch", "now_time_utc", "wall_time_utc", "recv_time_utc")
        )
        domain = _read(value, "clock_domain_id")
        quality = _read(value, "receive_clock_quality")
        fresh = _read(value, "freshness_verified")
        error = _read(value, "receive_clock_error_ms")
        if monotonic is None or epoch is None or not _valid_identity(domain):
            return None, MarketCohortReason.TRUSTED_NOW_INVALID
        if quality != "verified":
            return None, MarketCohortReason.RECEIVE_CLOCK_UNVERIFIED
        if fresh is not True:
            return None, MarketCohortReason.FRESHNESS_UNVERIFIED
        try:
            now = MarketCohortNow(monotonic, epoch, domain, error)
        except (TypeError, ValueError):
            return None, MarketCohortReason.RECEIVE_CLOCK_ERROR_INVALID
    if now.receive_clock_error_ms > policy.max_receive_clock_error_ms:
        return None, MarketCohortReason.RECEIVE_CLOCK_ERROR_INVALID
    return now, None


def _valid_identity(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.strip() == value
        and value.casefold() not in _PLACEHOLDERS
    )


def _normalize_quote(
    event: Any,
    *,
    leg: MarketCohortLeg,
    expected_rules_hash: str,
    policy: MarketCohortPolicy,
) -> MarketQuoteValidation:
    event_type = _tick_type()
    if event_type is None:
        return MarketQuoteValidation(None, MarketCohortReason.MARKET_TICK_API_UNAVAILABLE)
    if type(event) is not event_type:
        return MarketQuoteValidation(None, MarketCohortReason.UNSUPPORTED_QUOTE_SCHEMA)

    instrument, data, clock = event.key.instrument, event.data, event.clock
    symbol, exchange, asset_type = instrument.instrument_id, instrument.venue, instrument.product_type
    if type(symbol) is not str or not symbol:
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_SYMBOL_MISSING)
    if symbol != leg.symbol:
        return MarketQuoteValidation(None, MarketCohortReason.UNEXPECTED_SYMBOL)
    if type(exchange) is not str or exchange != leg.exchange:
        return MarketQuoteValidation(None, MarketCohortReason.EXCHANGE_MISMATCH)
    if leg.asset_type is not None and asset_type != leg.asset_type:
        return MarketQuoteValidation(None, MarketCohortReason.ASSET_TYPE_MISMATCH)

    if getattr(clock.source_clock_quality, "value", None) != "verified":
        return MarketQuoteValidation(None, MarketCohortReason.SOURCE_CLOCK_UNVERIFIED)
    if getattr(clock.receive_clock_quality, "value", None) != "verified":
        return MarketQuoteValidation(None, MarketCohortReason.RECEIVE_CLOCK_UNVERIFIED)
    if getattr(clock.freshness_status, "value", None) != "fresh" or event.data_clock_qualified is not True:
        return MarketQuoteValidation(None, MarketCohortReason.FRESHNESS_UNVERIFIED)
    if data.delta_volume is None:
        return MarketQuoteValidation(None, MarketCohortReason.VOLUME_SEMANTICS_NOT_DELTA)
    if not _valid_identity(data.event_time_provenance):
        return MarketQuoteValidation(None, MarketCohortReason.EVENT_TIME_SOURCE_MISSING)
    if not _valid_identity(data.quality_contract_ref) or data.quality_contract_ref != expected_rules_hash:
        return MarketQuoteValidation(None, MarketCohortReason.RULES_HASH_MISMATCH)
    if not _valid_identity(data.provider_source):
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_SOURCE_MISSING)
    if data.continuity_status.casefold() != "continuous":
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_CONTINUITY_NOT_CONTINUOUS)
    if type(data.quality_flags) not in (tuple, list) or data.quality_flags:
        reason = (
            MarketCohortReason.QUOTE_QUALITY_FLAGS_INVALID
            if type(data.quality_flags) not in (tuple, list)
            else MarketCohortReason.QUOTE_QUALITY_FLAGS_PRESENT
        )
        return MarketQuoteValidation(None, reason)
    if data.execution_eligible is not True:
        return MarketQuoteValidation(None, MarketCohortReason.EXECUTION_INELIGIBLE_QUOTE)
    if data.volume_complete is not True:
        return MarketQuoteValidation(None, MarketCohortReason.VOLUME_INCOMPLETE)
    if data.volume_quality.casefold() != "continuous":
        return MarketQuoteValidation(None, MarketCohortReason.VOLUME_QUALITY_NOT_CONTINUOUS)

    raw = {
        "bid": data.bid_price,
        "ask": data.ask_price,
        "bid_size": data.bid_size,
        "ask_size": data.ask_size,
        "last": data.last_price,
        "lower_limit": data.lower_limit,
        "upper_limit": data.upper_limit,
        "source_clock_error_ms": clock.source_clock_error_ms,
        "receive_clock_error_ms": clock.receive_clock_error_ms,
    }
    parsed = {}
    for name, item in raw.items():
        try:
            parsed[name] = _number(item)
        except (TypeError, ValueError, OverflowError):
            if name.endswith("clock_error_ms"):
                code = (
                    MarketCohortReason.SOURCE_CLOCK_ERROR_INVALID
                    if name.startswith("source")
                    else MarketCohortReason.RECEIVE_CLOCK_ERROR_INVALID
                )
                return MarketQuoteValidation(None, code)
            return MarketQuoteValidation(None, MarketCohortReason.QUOTE_NUMERIC_TYPE_INVALID)
    bid, ask, bid_size, ask_size, last = (parsed[n] for n in ("bid", "ask", "bid_size", "ask_size", "last"))
    low, high = parsed["lower_limit"], parsed["upper_limit"]
    source_error, receive_error = parsed["source_clock_error_ms"], parsed["receive_clock_error_ms"]
    if min(bid, ask, bid_size, ask_size, last) <= 0:
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_NONPOSITIVE)
    if ask < bid:
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_CROSSED)
    if low <= 0 or high <= low:
        return MarketQuoteValidation(None, MarketCohortReason.DAILY_PRICE_LIMIT_INVALID)
    if any(value < low or value > high for value in (bid, ask, last)):
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_OUTSIDE_DAILY_LIMIT)
    if any(not _tick_aligned(value, leg.price_tick) for value in (bid, ask, last, low, high)):
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_OFF_TICK_GRID)
    if source_error < 0 or source_error > policy.max_source_clock_error_ms:
        return MarketQuoteValidation(None, MarketCohortReason.SOURCE_CLOCK_ERROR_INVALID)
    if receive_error < 0 or receive_error > policy.max_receive_clock_error_ms:
        return MarketQuoteValidation(None, MarketCohortReason.RECEIVE_CLOCK_ERROR_INVALID)

    source_epoch = _epoch(clock.provider_event_time_utc)
    if source_epoch is None:
        return MarketQuoteValidation(None, MarketCohortReason.SOURCE_TIME_INVALID)
    receive_epoch = _epoch(clock.received_at_utc)
    if receive_epoch is None:
        return MarketQuoteValidation(None, MarketCohortReason.RECEIVE_TIME_INVALID)
    receive_mono = _positive_sequence(clock.received_monotonic_ns)
    provider_sequence = _positive_sequence(data.provider_sequence)
    generation = _positive_sequence(event.source.connection_generation)
    subscription_epoch = _positive_sequence(data.provider_subscription_epoch)
    if None in (receive_mono, provider_sequence, generation, subscription_epoch):
        return MarketQuoteValidation(None, MarketCohortReason.QUOTE_IDENTITY_TYPE_INVALID)
    trading_date = _event_date(data.trading_date)
    if trading_date is None:
        return MarketQuoteValidation(None, MarketCohortReason.TRADING_DATE_INVALID)
    event_session_date = _event_date(data.event_session_date)
    if event_session_date is None:
        return MarketQuoteValidation(None, MarketCohortReason.EVENT_SESSION_DATE_INVALID)
    domain = event.source.clock_domain_id
    if not _valid_identity(domain):
        return MarketQuoteValidation(None, MarketCohortReason.CLOCK_DOMAIN_UNKNOWN)
    if source_epoch > receive_epoch:
        return MarketQuoteValidation(None, MarketCohortReason.SOURCE_TIME_AFTER_RECEIVE)

    quote = MarketQuoteEvidence(
        symbol=symbol,
        exchange=exchange,
        asset_type=asset_type if type(asset_type) is str else None,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        last=last,
        lower_limit=low,
        upper_limit=high,
        source_epoch=source_epoch,
        receive_epoch=receive_epoch,
        receive_monotonic_ns=receive_mono,
        ingest_seq=provider_sequence,
        connection_generation=generation,
        subscription_epoch=subscription_epoch,
        trading_date=trading_date,
        event_session_date=event_session_date,
        clock_domain_id=domain,
        rules_hash=data.quality_contract_ref,
        source=data.provider_source,
        event_time_source=data.event_time_provenance,
        source_clock_error_ms=source_error,
        receive_clock_error_ms=receive_error,
    )
    return MarketQuoteValidation(quote, None)


def validate_market_quote(
    event: Any,
    *,
    leg: MarketCohortLeg,
    expected_rules_hash: str,
    policy: MarketCohortPolicy,
) -> MarketQuoteValidation:
    return _normalize_quote(
        event, leg=leg, expected_rules_hash=expected_rules_hash, policy=policy
    )


class MarketQuoteCohortValidator:
    """Collect a fresh all-leg set while keeping producer scopes separate."""

    def __init__(
        self,
        *,
        expected_legs: Iterable[MarketCohortLeg],
        expected_rules_hash: str,
        policy: MarketCohortPolicy,
    ) -> None:
        legs = tuple(expected_legs)
        if len(legs) not in (2, 3):
            raise ValueError("expected_legs must contain two or three market legs")
        if not all(isinstance(item, MarketCohortLeg) for item in legs):
            raise TypeError("expected_legs must contain MarketCohortLeg values")
        symbols = tuple(item.symbol for item in legs)
        if len(set(symbols)) != len(symbols) or len({item.exchange for item in legs}) != 1:
            raise ValueError("legs must have unique symbols on a common exchange")
        if not isinstance(policy, MarketCohortPolicy):
            raise TypeError("policy must be MarketCohortPolicy")
        self.expected_legs = legs
        self.expected_rules_hash = _text(expected_rules_hash, identity=True)
        self.policy = policy
        self._by_symbol = MappingProxyType({item.symbol: item for item in legs})
        self._active: Optional[Tuple[int, int]] = None
        self._highest: Optional[Tuple[int, int]] = None
        self._retired: set[Tuple[int, int]] = set()
        self._current: dict[str, MarketQuoteEvidence] = {}
        self._last_seen: dict[Tuple[int, int], dict[str, MarketQuoteEvidence]] = {}
        self._admitted: dict[Tuple[int, int], dict[str, int]] = {}
        self._confirmed: Optional[MarketQuoteCohort] = None

    @property
    def expected_symbols(self) -> Tuple[str, ...]:
        return tuple(leg.symbol for leg in self.expected_legs)

    def reset(self) -> None:
        self._active = None
        self._highest = None
        self._retired.clear()
        self._current.clear()
        self._last_seen.clear()
        self._admitted.clear()
        self._confirmed = None

    def ingest(self, event: Any, *, now: Any = None) -> MarketCohortResult:
        tick_type = _tick_type()
        if tick_type is None:
            self._clear_current()
            return MarketCohortResult(None, MarketCohortReason.MARKET_TICK_API_UNAVAILABLE)
        if type(event) is not tick_type:
            self._clear_current()
            return MarketCohortResult(None, MarketCohortReason.UNSUPPORTED_QUOTE_SCHEMA)
        symbol = event.key.instrument.instrument_id
        if type(symbol) is not str or not symbol:
            return MarketCohortResult(None, MarketCohortReason.QUOTE_SYMBOL_MISSING)
        leg = self._by_symbol.get(symbol)
        if leg is None:
            return MarketCohortResult(None, MarketCohortReason.UNEXPECTED_SYMBOL)
        scope = self._event_scope(event)
        if self._rollback(scope):
            return MarketCohortResult(None, MarketCohortReason.RETIRED_CONNECTION_SCOPE)

        validation = validate_market_quote(
            event, leg=leg, expected_rules_hash=self.expected_rules_hash, policy=self.policy
        )
        if validation.quote is None:
            self._clear_after_bad_scope(scope)
            return MarketCohortResult(None, validation.reason)
        quote = validation.quote
        scope = (quote.connection_generation, quote.subscription_epoch)
        if self._rollback(scope) or scope in self._retired:
            return MarketCohortResult(None, MarketCohortReason.RETIRED_CONNECTION_SCOPE)
        if self._highest is None or scope > self._highest:
            self._highest = scope
        if self._active != scope:
            self._switch_scope(scope)

        trusted, reason = _now_evidence(now, self.policy)
        if trusted is None:
            self._clear_current()
            return MarketCohortResult(None, reason)
        reason = self._quote_time_reason(quote, trusted)
        if reason is not None:
            self._clear_current()
            return MarketCohortResult(None, reason)

        last = self._last_seen.setdefault(scope, {}).get(symbol)
        if last is not None:
            if quote.ingest_seq <= last.ingest_seq:
                self._clear_current()
                return MarketCohortResult(None, MarketCohortReason.DUPLICATE_OR_OUT_OF_ORDER)
            if quote.receive_monotonic_ns < last.receive_monotonic_ns:
                self._clear_current()
                return MarketCohortResult(None, MarketCohortReason.OUT_OF_ORDER_RECEIVE_TIME)
            if quote.source_epoch < last.source_epoch:
                self._clear_current()
                return MarketCohortResult(None, MarketCohortReason.OUT_OF_ORDER_SOURCE_TIME)

        if self._confirmed is not None:
            old = self._confirmed.quote_for(symbol)
            if old.update_identity != quote.update_identity:
                self._confirmed = None
        self._last_seen[scope][symbol] = quote
        self._current[symbol] = quote
        if len(self._current) != len(self.expected_legs):
            return MarketCohortResult(None, MarketCohortReason.WAITING_FOR_LEGS)

        chosen = {name: self._current[name] for name in self.expected_symbols}
        reason = self._cohort_reason(chosen, trusted)
        if reason is not None:
            self._clear_current()
            return MarketCohortResult(None, reason)
        admitted = self._admitted.setdefault(scope, dict.fromkeys(self.expected_symbols, 0))
        if any(chosen[name].ingest_seq <= admitted[name] for name in self.expected_symbols):
            return MarketCohortResult(None, MarketCohortReason.WAITING_FOR_ALL_LEGS_NEW)
        self._admitted[scope] = {name: chosen[name].ingest_seq for name in self.expected_symbols}
        cohort = self._cohort(chosen)
        self._confirmed = cohort
        return MarketCohortResult(cohort, None)

    def validate_at(self, *, now: Any = None) -> MarketCohortResult:
        cohort = self._confirmed
        if cohort is None:
            return MarketCohortResult(None, MarketCohortReason.NO_CONFIRMED_COHORT)
        trusted, reason = _now_evidence(now, self.policy)
        if trusted is None:
            self._clear_current()
            return MarketCohortResult(None, reason)
        scope = (cohort.connection_generation, cohort.subscription_epoch)
        if scope != self._active or scope in self._retired:
            self._clear_current()
            return MarketCohortResult(None, MarketCohortReason.RETIRED_CONNECTION_SCOPE)
        reason = self._cohort_reason(cohort.quotes, trusted)
        if reason is not None:
            self._clear_current()
            return MarketCohortResult(None, reason)
        return MarketCohortResult(cohort, None)

    def recheck(self, *, now: Any = None) -> MarketCohortResult:
        return self.validate_at(now=now)

    def _clear_current(self) -> None:
        self._current.clear()
        self._confirmed = None

    def _clear_after_bad_scope(self, scope: Optional[Tuple[int, int]]) -> None:
        if self._rollback(scope):
            return
        if scope is not None and scope not in self._retired and self._active != scope:
            if self._highest is None or scope > self._highest:
                self._highest = scope
            self._switch_scope(scope)
            return
        self._clear_current()

    def _rollback(self, scope: Optional[Tuple[int, int]]) -> bool:
        return scope is not None and self._highest is not None and scope < self._highest

    def _switch_scope(self, scope: Tuple[int, int]) -> None:
        if self._active == scope:
            return
        if self._active is not None:
            self._retired.add(self._active)
        self._clear_current()
        self._active = scope

    @staticmethod
    def _event_scope(event: Any) -> Optional[Tuple[int, int]]:
        generation = _positive_sequence(event.source.connection_generation)
        subscription = _positive_sequence(event.data.provider_subscription_epoch)
        return None if generation is None or subscription is None else (generation, subscription)

    def _quote_time_reason(self, quote: MarketQuoteEvidence, now: MarketCohortNow) -> Optional[str]:
        if quote.clock_domain_id != now.clock_domain_id:
            return MarketCohortReason.NOW_CLOCK_DOMAIN_MISMATCH
        if now.now_monotonic_ns < quote.receive_monotonic_ns:
            return MarketCohortReason.OUT_OF_ORDER_RECEIVE_TIME
        if (now.now_monotonic_ns - quote.receive_monotonic_ns) / 1_000_000.0 > self.policy.max_receive_age_ms:
            return MarketCohortReason.STALE_COHORT_RECEIVE_TIME
        wall_high = now.now_epoch + now.receive_clock_error_ms / 1000.0
        recv_low = quote.receive_epoch - quote.receive_clock_error_ms / 1000.0
        source_low = quote.source_epoch - quote.source_clock_error_ms / 1000.0
        if wall_high < recv_low or wall_high < source_low:
            return MarketCohortReason.NOW_WALL_TIME_BEFORE_QUOTE
        if (wall_high - recv_low) * 1000.0 > self.policy.max_receive_age_ms:
            return MarketCohortReason.STALE_COHORT_RECEIVE_TIME
        if (wall_high - source_low) * 1000.0 > self.policy.max_source_age_ms:
            return MarketCohortReason.STALE_COHORT_SOURCE_TIME
        return None

    def _cohort(self, quotes: Mapping[str, MarketQuoteEvidence]) -> MarketQuoteCohort:
        first = quotes[self.expected_symbols[0]]
        cohort_id = "|".join(
            "{}:{}:{}:{}".format(name, quotes[name].connection_generation,
                                 quotes[name].subscription_epoch, quotes[name].ingest_seq)
            for name in sorted(quotes)
        )
        return MarketQuoteCohort(
            quotes=quotes,
            exchange=first.exchange,
            trading_date=first.trading_date,
            event_session_date=first.event_session_date,
            connection_generation=first.connection_generation,
            subscription_epoch=first.subscription_epoch,
            clock_domain_id=first.clock_domain_id,
            rules_hash=first.rules_hash,
            cohort_id=cohort_id,
        )

    def _cohort_reason(
        self, quotes: Mapping[str, MarketQuoteEvidence], now: MarketCohortNow
    ) -> Optional[str]:
        if tuple(quotes) != self.expected_symbols or any(
            quotes[name].symbol != name for name in self.expected_symbols
        ):
            return MarketCohortReason.WAITING_FOR_LEGS
        same_fields = (
            ("exchange", MarketCohortReason.COHORT_EXCHANGE_MISMATCH),
            ("trading_date", MarketCohortReason.COHORT_TRADING_DATE_MISMATCH),
            ("event_session_date", MarketCohortReason.COHORT_EVENT_SESSION_DATE_MISMATCH),
            ("connection_generation", MarketCohortReason.COHORT_CONNECTION_GENERATION_MISMATCH),
            ("subscription_epoch", MarketCohortReason.COHORT_SUBSCRIPTION_EPOCH_MISMATCH),
            ("rules_hash", MarketCohortReason.COHORT_RULES_HASH_MISMATCH),
            ("clock_domain_id", MarketCohortReason.COHORT_CLOCK_DOMAIN_MISMATCH),
        )
        for field_name, reason in same_fields:
            if len({getattr(q, field_name) for q in quotes.values()}) != 1:
                return reason
        for quote in quotes.values():
            reason = self._quote_time_reason(quote, now)
            if reason is not None:
                return reason
        recv_times = [q.receive_monotonic_ns for q in quotes.values()]
        if (max(recv_times) - min(recv_times)) / 1_000_000.0 > self.policy.max_receive_skew_ms:
            return MarketCohortReason.BLOCKED_CROSS_LEG_SKEW
        lows = [q.source_epoch - q.source_clock_error_ms / 1000.0 for q in quotes.values()]
        highs = [q.source_epoch + q.source_clock_error_ms / 1000.0 for q in quotes.values()]
        if (max(highs) - min(lows)) * 1000.0 > self.policy.max_source_skew_ms:
            return MarketCohortReason.BLOCKED_SOURCE_SKEW
        return None


__all__ = [
    "MarketCohortPolicy",
    "MarketCohortNow",
    "MarketCohortReason",
    "MarketCohortLeg",
    "MarketQuoteEvidence",
    "MarketQuoteValidation",
    "MarketQuoteCohort",
    "MarketCohortResult",
    "MarketQuoteCohortValidator",
    "validate_market_quote",
]
