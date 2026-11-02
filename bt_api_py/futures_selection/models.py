"""Selection models, policies, and their shared validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Tuple
import hashlib
import json
import re


_SOURCE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


_MONTH_CONTRACT = re.compile(r"^[A-Za-z]{1,8}([0-9]{3,4})$")


_KNOWN_NON_FUTURES = frozenset(
    {
        "option",
        "options",
        "non_future",
        "non_futures",
        "combination",
        "spot",
        "spot_option",
        "index",
    }
)


LEGACY_PREVIOUS_COMPLETE_DAY_EOD_OI_V1 = "legacy_previous_complete_day_eod_oi_v1"


RANKING_MODE_LEGACY_PRIOR_DAY_OI = "legacy_previous_complete_day_eod_oi_v1"


RANKING_MODE_FUTURES_MARKET_SNAPSHOT = "futures_market_snapshot_equal_weight_v1"


_RANKING_METRIC_MAX_DIGITS = 512


_RANKING_METRIC_MAX_ABS_EXPONENT = 256


EXCLUSION_REASON_NO_LISTED_CONTRACTS = "NO_LISTED_CONTRACTS"


class TradingCalendarContractError(ValueError):
    """Raised when an exchange calendar cannot prove a requested date fact."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MainContractSelectionBlocked(RuntimeError):
    """Raised when code asks for an executable mapping from a blocked manifest."""


def _identity_text(value: Any, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("{} must be non-empty trimmed text".format(field_name))
    return value


def _aware_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("{} must be timezone-aware".format(field_name))
    return value


def _calendar_date(value: Any, field_name: str) -> date:
    # datetime is a date subclass, but is never a valid exchange-day label.
    if type(value) is not date:
        raise ValueError("{} must be a date, not a datetime".format(field_name))
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _key_order(key: "ProductKey") -> Tuple[str, str]:
    return key.exchange_id, key.product_id


def _number_as_decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return number


def _decimal_text(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value, "f")


def _is_actual_month_contract(instrument_id: str, product_id: str) -> bool:
    """Accept standard year-month contracts and reject continuous aliases."""

    match = _MONTH_CONTRACT.fullmatch(instrument_id)
    if match is None:
        return False
    digits = match.group(1)
    product_prefix = instrument_id[: -len(digits)]
    if product_prefix.casefold() != product_id.casefold():
        return False
    # A real month suffix rejects common 888/999/8888/9999 continuous aliases.
    month = int(digits[-2:])
    return 1 <= month <= 12


@dataclass(frozen=True)
class ProductKey:
    """Canonical product identity; instrument IDs remain untouched."""

    exchange_id: str
    product_id: str

    def __post_init__(self) -> None:
        exchange = _identity_text(self.exchange_id, "exchange_id").upper()
        product = _identity_text(self.product_id, "product_id").upper()
        object.__setattr__(self, "exchange_id", exchange)
        object.__setattr__(self, "product_id", product)


@dataclass(frozen=True)
class ExchangeTradingDay:
    """Provider session trading day being considered for one exchange."""

    exchange_id: str
    trading_day: date

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "exchange_id", _identity_text(self.exchange_id, "exchange_id").upper()
        )
        _calendar_date(self.trading_day, "trading_day")


@dataclass(frozen=True)
class ExchangeTradingCalendar:
    """Immutable, source-described exchange trading-day evidence.

    ``complete`` means the supplied day sequence is exhaustive over the
    declared coverage interval.  The selector never substitutes weekdays or
    a local calendar when this evidence is missing or incomplete.
    """

    exchange_id: str
    source_id: str
    version: str
    available_at: datetime
    retrieved_at: datetime
    coverage_start: date
    coverage_through: date
    trading_days: Tuple[date, ...]
    complete: bool = True
    evidence_mode: str = "source_backed"
    source_manifest_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "exchange_id", _identity_text(self.exchange_id, "exchange_id").upper()
        )
        object.__setattr__(self, "source_id", _identity_text(self.source_id, "calendar source_id"))
        object.__setattr__(self, "version", _identity_text(self.version, "calendar version"))
        if self.evidence_mode not in ("source_backed", "expected_rule_derived"):
            raise ValueError("unsupported calendar evidence_mode")
        if self.evidence_mode == "expected_rule_derived":
            if type(self.source_manifest_sha256) is not str or not _SOURCE_SHA256.fullmatch(
                self.source_manifest_sha256
            ):
                raise ValueError("expected-rule calendar requires a rules manifest SHA-256")
        elif self.source_manifest_sha256 is not None:
            raise ValueError("source_manifest_sha256 is only valid for expected-rule calendars")
        _aware_datetime(self.available_at, "calendar available_at")
        _aware_datetime(self.retrieved_at, "calendar retrieved_at")
        if self.retrieved_at.astimezone(timezone.utc) < self.available_at.astimezone(timezone.utc):
            raise ValueError("calendar retrieved_at precedes available_at")
        start = _calendar_date(self.coverage_start, "coverage_start")
        through = _calendar_date(self.coverage_through, "coverage_through")
        if through < start:
            raise ValueError("calendar coverage_through precedes coverage_start")
        days = tuple(self.trading_days)
        if any(type(day) is not date for day in days):
            raise ValueError("calendar trading_days must contain only dates")
        if tuple(sorted(set(days))) != days:
            raise ValueError("calendar trading_days must be unique and ascending")
        if any(day < start or day > through for day in days):
            raise ValueError("calendar trading day lies outside its declared coverage")
        if type(self.complete) is not bool:
            raise ValueError("calendar complete must be a bool")
        object.__setattr__(self, "trading_days", days)

    @property
    def evidence_sha256(self) -> str:
        projection = {
            "exchange_id": self.exchange_id,
            "source_id": self.source_id,
            "version": self.version,
            "available_at": _utc_text(self.available_at),
            "retrieved_at": _utc_text(self.retrieved_at),
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_through": self.coverage_through.isoformat(),
            "complete": self.complete,
            "trading_days": [day.isoformat() for day in self.trading_days],
        }
        if self.evidence_mode != "source_backed":
            projection["evidence_mode"] = self.evidence_mode
            projection["source_manifest_sha256"] = self.source_manifest_sha256
        return _canonical_json_sha256(projection)

    def previous_complete_trading_day(self, current_trading_day: date) -> date:
        if not self.complete:
            raise TradingCalendarContractError("CALENDAR_INCOMPLETE")
        current = _calendar_date(current_trading_day, "current_trading_day")
        if current < self.coverage_start or current > self.coverage_through:
            raise TradingCalendarContractError("CURRENT_DAY_OUTSIDE_CALENDAR")
        try:
            index = self.trading_days.index(current)
        except ValueError:
            raise TradingCalendarContractError("CURRENT_DAY_NOT_TRADING_DAY") from None
        if index == 0:
            raise TradingCalendarContractError("PRIOR_TRADING_DAY_UNAVAILABLE")
        return self.trading_days[index - 1]

    def trading_days_to_expiry(self, current_trading_day: date, expiry_date: date) -> int:
        if not self.complete:
            raise TradingCalendarContractError("CALENDAR_INCOMPLETE")
        current = _calendar_date(current_trading_day, "current_trading_day")
        expiry = _calendar_date(expiry_date, "expiry_date")
        if current not in self.trading_days:
            raise TradingCalendarContractError("CURRENT_DAY_NOT_TRADING_DAY")
        if expiry > self.coverage_through or expiry < self.coverage_start:
            raise TradingCalendarContractError("EXPIRY_OUTSIDE_CALENDAR")
        if expiry not in self.trading_days:
            raise TradingCalendarContractError("EXPIRY_NOT_COVERED_AS_TRADING_DAY")
        return sum(current < day <= expiry for day in self.trading_days)


@dataclass(frozen=True)
class RolloverBoundaryEvidence:
    """Source evidence for the first session open of a new TradingDay.

    Session boundary resolution stays with the shared exchange-calendar
    adapter.  The rollover planner accepts only this explicit evidence; it
    never derives a boundary from local midnight or the machine clock.
    """

    boundary_id: str
    source_id: str
    source_sha256: str
    available_at: datetime
    retrieved_at: datetime
    effective_at: datetime
    trading_days: Tuple[ExchangeTradingDay, ...]
    boundary_kind: str = "FIRST_SESSION_OPEN_FOR_TRADING_DAY"

    def __post_init__(self) -> None:
        _identity_text(self.boundary_id, "boundary_id")
        _identity_text(self.source_id, "boundary source_id")
        if type(self.source_sha256) is not str or not _SOURCE_SHA256.fullmatch(self.source_sha256):
            raise ValueError("boundary source_sha256 must be lowercase SHA-256 hex")
        _aware_datetime(self.available_at, "boundary available_at")
        _aware_datetime(self.retrieved_at, "boundary retrieved_at")
        _aware_datetime(self.effective_at, "boundary effective_at")
        if self.retrieved_at.astimezone(timezone.utc) < self.available_at.astimezone(timezone.utc):
            raise ValueError("boundary retrieved_at precedes available_at")
        if self.boundary_kind != "FIRST_SESSION_OPEN_FOR_TRADING_DAY":
            raise ValueError("unsupported rollover boundary kind")
        days = tuple(self.trading_days)
        if any(not isinstance(item, ExchangeTradingDay) for item in days):
            raise ValueError("boundary trading_days must contain ExchangeTradingDay records")
        exchange_ids = [item.exchange_id for item in days]
        if len(set(exchange_ids)) != len(exchange_ids):
            raise ValueError("boundary trading_days contains duplicate exchanges")
        object.__setattr__(
            self, "trading_days", tuple(sorted(days, key=lambda item: item.exchange_id))
        )


@dataclass(frozen=True)
class FuturesContractRecord:
    """One source candidate, including rows that the policy will exclude."""

    product: ProductKey
    instrument_id: str
    asset_type: Optional[str]
    is_trading: Optional[bool]
    expiry_date: Optional[date]
    open_interest: Any
    volume: Any

    def __post_init__(self) -> None:
        if not isinstance(self.product, ProductKey):
            raise ValueError("contract product must be a ProductKey")
        if type(self.instrument_id) is not str:
            raise ValueError("instrument_id must be text")
        if self.asset_type is not None and type(self.asset_type) is not str:
            raise ValueError("asset_type must be text or None")
        if self.is_trading is not None and type(self.is_trading) is not bool:
            raise ValueError("is_trading must be bool or None")
        if self.expiry_date is not None and type(self.expiry_date) is not date:
            raise ValueError("expiry_date must be date or None")


@dataclass(frozen=True)
class ProductRankingCoverage:
    """Completeness and units for one exchange/product's ranking rows."""

    product: ProductKey
    candidate_count: int
    expected_instrument_ids: Tuple[str, ...]
    ranking_instrument_ids: Tuple[str, ...]
    candidate_set_complete: bool
    ranking_values_complete: bool
    finalized: bool
    latest_available: bool
    ranking_trading_day: Optional[date]
    open_interest_unit: Optional[str]
    volume_unit: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.product, ProductKey):
            raise ValueError("coverage product must be a ProductKey")
        if type(self.candidate_count) is not int or self.candidate_count < 0:
            raise ValueError("candidate_count must be a non-negative int")
        for name in ("expected_instrument_ids", "ranking_instrument_ids"):
            ids = tuple(getattr(self, name))
            if any(type(instrument_id) is not str for instrument_id in ids):
                raise ValueError("{} must contain instrument ID text".format(name))
            object.__setattr__(self, name, tuple(sorted(ids)))
        for name in (
            "candidate_set_complete",
            "ranking_values_complete",
            "finalized",
            "latest_available",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError("{} must be a bool".format(name))
        if self.ranking_trading_day is not None:
            _calendar_date(self.ranking_trading_day, "ranking_trading_day")
        for name in ("open_interest_unit", "volume_unit"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not str or not value or value != value.strip()
            ):
                raise ValueError("{} must be non-empty text or None".format(name))


@dataclass(frozen=True)
class MainContractRankingSnapshot:
    """A point-in-time source snapshot consumed by the pure selector.

    The producer digest must identify the exact source payload.  It does not
    authenticate the producer by itself; that remains the adapter's duty.
    """

    source_id: str
    snapshot_id: str
    source_sha256: str
    available_at: datetime
    retrieved_at: datetime
    catalog_source_id: str
    catalog_snapshot_id: str
    catalog_sha256: str
    catalog_available_at: datetime
    catalog_retrieved_at: datetime
    coverage: Tuple[ProductRankingCoverage, ...]
    candidates: Tuple[FuturesContractRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", tuple(self.coverage))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if any(not isinstance(item, ProductRankingCoverage) for item in self.coverage):
            raise ValueError("coverage must contain ProductRankingCoverage records")
        if any(not isinstance(item, FuturesContractRecord) for item in self.candidates):
            raise ValueError("candidates must contain FuturesContractRecord records")


@dataclass(frozen=True)
class MainContractPolicy:
    """Frozen main-contract ranking and expiry-safety policy."""

    policy_id: str = "prior-day-oi-volume-expiry-id-v1"
    minimum_trading_days_to_expiry: int = 5
    ranking_mode: str = RANKING_MODE_LEGACY_PRIOR_DAY_OI
    max_snapshot_age_seconds: int = 300
    max_collection_span_seconds: int = 300

    def __post_init__(self) -> None:
        _identity_text(self.policy_id, "policy_id")
        if (
            type(self.minimum_trading_days_to_expiry) is not int
            or self.minimum_trading_days_to_expiry < 0
        ):
            raise ValueError("minimum_trading_days_to_expiry must be a non-negative int")
        if self.ranking_mode not in (
            RANKING_MODE_LEGACY_PRIOR_DAY_OI,
            RANKING_MODE_FUTURES_MARKET_SNAPSHOT,
        ):
            raise ValueError("unsupported main-contract ranking_mode")
        for name in ("max_snapshot_age_seconds", "max_collection_span_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError("{} must be a non-negative int".format(name))


DEFAULT_MAIN_CONTRACT_POLICY = MainContractPolicy()


LEGACY_PREVIOUS_COMPLETE_DAY_EOD_OI_POLICY = MainContractPolicy(
    policy_id=LEGACY_PREVIOUS_COMPLETE_DAY_EOD_OI_V1,
    ranking_mode=RANKING_MODE_LEGACY_PRIOR_DAY_OI,
)


FUTURES_MARKET_SNAPSHOT_EQUAL_WEIGHT_POLICY = MainContractPolicy(
    policy_id=RANKING_MODE_FUTURES_MARKET_SNAPSHOT,
    ranking_mode=RANKING_MODE_FUTURES_MARKET_SNAPSHOT,
)


@dataclass(frozen=True)
class CalendarEvidence:
    exchange_id: str
    source_id: str
    version: str
    available_at: datetime
    retrieved_at: datetime
    coverage_start: date
    coverage_through: date
    complete: bool
    trading_days: Tuple[date, ...]
    sha256: str
    evidence_mode: str = "source_backed"
    source_manifest_sha256: Optional[str] = None


@dataclass(frozen=True)
class TradingDayReference:
    exchange_id: str
    current_trading_day: date
    expected_prior_trading_day: Optional[date]


@dataclass(frozen=True)
class ContractAssessment:
    product: ProductKey
    instrument_id: str
    status: str
    reason: Optional[str]
    expiry_date: Optional[date]
    trading_days_to_expiry: Optional[int]
    open_interest: Optional[str]
    volume: Optional[str]
    rank: Optional[int]
    trading_days_to_expiry_lower_bound: Optional[int] = None
    expiry_distance_exact: bool = False
    ranking_score: Optional[str] = None
    asset_type: Optional[str] = None
    is_trading: Optional[bool] = None
    quote_trading_day: Optional[date] = None
    contract_kind: Optional[str] = None
    provider_event_date: Optional[date] = None
    provider_event_time: Optional[str] = None
    provider_event_millisecond: Optional[int] = None
    volume_semantics: Optional[str] = None


@dataclass(frozen=True)
class ProductSelection:
    product: ProductKey
    in_target_pool: bool
    status: str
    expected_prior_trading_day: Optional[date]
    ranking_trading_day: Optional[date]
    selected_instrument_id: Optional[str]
    ranked_instrument_ids: Tuple[str, ...]
    excluded_instrument_ids: Tuple[str, ...]
    blockers: Tuple[str, ...]
    selected_score: Optional[str] = None
    exclusion_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.exclusion_reason is None:
            return
        if self.exclusion_reason != EXCLUSION_REASON_NO_LISTED_CONTRACTS:
            raise ValueError("unsupported product exclusion_reason")
        if self.in_target_pool or self.status != "EXCLUDED":
            raise ValueError("exclusion_reason requires an out-of-pool EXCLUDED product")


@dataclass(frozen=True)
class MainContractMarketCoverage:
    """Neutral catalog/quote identity coverage for one selected product."""

    product: ProductKey
    catalog_instrument_ids: Tuple[str, ...]
    expected_quote_instrument_ids: Tuple[str, ...]
    observed_quote_instrument_ids: Tuple[str, ...]
    metric_unit: str = "contracts"
    volume_semantics: str = "cumulative_trading_day"

    def __post_init__(self) -> None:
        if type(self.product) is not ProductKey:
            raise ValueError("market coverage product must be a ProductKey")
        for name in (
            "catalog_instrument_ids",
            "expected_quote_instrument_ids",
            "observed_quote_instrument_ids",
        ):
            values = tuple(getattr(self, name))
            if any(type(value) is not str for value in values):
                raise ValueError("{} must contain neutral instrument IDs".format(name))
            object.__setattr__(self, name, values)
        _identity_text(self.metric_unit, "metric_unit")
        _identity_text(self.volume_semantics, "volume_semantics")


@dataclass(frozen=True)
class MainContractManifest:
    """Immutable selection outcome and enough public evidence to reproduce it."""

    status: str
    decision_at: datetime
    policy: MainContractPolicy
    target_products: Tuple[ProductKey, ...]
    source_id: Optional[str]
    source_snapshot_id: Optional[str]
    source_sha256: Optional[str]
    source_available_at: Optional[datetime]
    source_retrieved_at: Optional[datetime]
    catalog_source_id: Optional[str]
    catalog_snapshot_id: Optional[str]
    catalog_sha256: Optional[str]
    catalog_available_at: Optional[datetime]
    catalog_retrieved_at: Optional[datetime]
    trading_days: Tuple[TradingDayReference, ...]
    calendars: Tuple[CalendarEvidence, ...]
    coverage: Tuple[ProductRankingCoverage, ...]
    products: Tuple[ProductSelection, ...]
    candidates: Tuple[ContractAssessment, ...]
    blockers: Tuple[str, ...]
    market_coverage: Tuple[MainContractMarketCoverage, ...] = ()
    market_snapshot_evidence_json: Optional[str] = None

    def __post_init__(self) -> None:
        _aware_datetime(self.decision_at, "decision_at")
        for name in (
            "target_products",
            "trading_days",
            "calendars",
            "coverage",
            "products",
            "candidates",
            "blockers",
            "market_coverage",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.market_snapshot_evidence_json is not None:
            if type(self.market_snapshot_evidence_json) is not str:
                raise ValueError("market_snapshot_evidence_json must be text or None")
            try:
                snapshot_evidence = json.loads(self.market_snapshot_evidence_json)
            except (TypeError, ValueError):
                raise ValueError("market snapshot evidence must be valid JSON") from None
            if type(snapshot_evidence) is not dict:
                raise ValueError("market snapshot evidence must be a JSON object")

    def selected_mapping(self) -> Tuple[Tuple[ProductKey, str], ...]:
        """Return the executable mapping only when every target product is ready."""

        if self.status != "READY":
            raise MainContractSelectionBlocked(
                "main-contract manifest is blocked: {}".format(", ".join(self.blockers))
            )
        selected = tuple(item for item in self.products if item.in_target_pool)
        targets = set(self.target_products)
        if (
            self.blockers
            or not targets
            or len(targets) != len(self.target_products)
            or len(selected) != len(targets)
            or {item.product for item in selected} != targets
            or any(
                item.status != "READY"
                or item.blockers
                or not isinstance(item.selected_instrument_id, str)
                or not item.selected_instrument_id.strip()
                or not item.ranked_instrument_ids
                or item.selected_instrument_id != item.ranked_instrument_ids[0]
                for item in selected
            )
        ):
            raise MainContractSelectionBlocked("main-contract manifest has an incomplete mapping")
        by_product = {item.product: item.selected_instrument_id for item in selected}
        return tuple((product, by_product[product]) for product in self.target_products)

    def to_dict(self) -> dict[str, Any]:
        """Create a fresh JSON-safe audit representation."""

        market_snapshot_mode = (
            self.policy.ranking_mode == RANKING_MODE_FUTURES_MARKET_SNAPSHOT
        )
        projection = {
            "schema": (
                "backtrader.futures_main_contract_manifest.v3"
                if market_snapshot_mode
                else "backtrader.futures_main_contract_manifest.v1"
            ),
            "status": self.status,
            "decision_at": _utc_text(self.decision_at),
            "policy": {
                "policy_id": self.policy.policy_id,
                "minimum_trading_days_to_expiry": self.policy.minimum_trading_days_to_expiry,
                "ranking": [
                    "open_interest_desc",
                    "volume_desc",
                    "expiry_date_asc",
                    "instrument_id_asc",
                ],
            },
            "target_products": [_product_dict(item) for item in self.target_products],
            "source": None
            if all(
                value is None
                for value in (
                    self.source_id,
                    self.source_snapshot_id,
                    self.source_sha256,
                    self.source_available_at,
                    self.source_retrieved_at,
                    self.catalog_source_id,
                    self.catalog_snapshot_id,
                    self.catalog_sha256,
                    self.catalog_available_at,
                    self.catalog_retrieved_at,
                )
            )
            else {
                "source_id": self.source_id,
                "snapshot_id": self.source_snapshot_id,
                "sha256": self.source_sha256,
                "available_at": _utc_text(self.source_available_at)
                if self.source_available_at is not None
                else None,
                "retrieved_at": _utc_text(self.source_retrieved_at)
                if self.source_retrieved_at is not None
                else None,
                "catalog": {
                    "source_id": self.catalog_source_id,
                    "snapshot_id": self.catalog_snapshot_id,
                    "sha256": self.catalog_sha256,
                    "available_at": _utc_text(self.catalog_available_at)
                    if self.catalog_available_at is not None
                    else None,
                    "retrieved_at": _utc_text(self.catalog_retrieved_at)
                    if self.catalog_retrieved_at is not None
                    else None,
                },
            },
            "trading_days": [
                {
                    "exchange_id": item.exchange_id,
                    "current": item.current_trading_day.isoformat(),
                    "expected_prior_complete": item.expected_prior_trading_day.isoformat()
                    if item.expected_prior_trading_day is not None
                    else None,
                }
                for item in self.trading_days
            ],
            "calendars": [
                {
                    "exchange_id": item.exchange_id,
                    "source_id": item.source_id,
                    "version": item.version,
                    "available_at": _utc_text(item.available_at),
                    "retrieved_at": _utc_text(item.retrieved_at),
                    "coverage_start": item.coverage_start.isoformat(),
                    "coverage_through": item.coverage_through.isoformat(),
                    "complete": item.complete,
                    "trading_days": [day.isoformat() for day in item.trading_days],
                    "sha256": item.sha256,
                    **(
                        {
                            "evidence_mode": item.evidence_mode,
                            "source_manifest_sha256": item.source_manifest_sha256,
                        }
                        if item.evidence_mode != "source_backed"
                        else {}
                    ),
                }
                for item in self.calendars
            ],
            "coverage": [_coverage_dict(item) for item in self.coverage],
            "products": [
                {
                    "product": _product_dict(item.product),
                    "in_target_pool": item.in_target_pool,
                    "status": item.status,
                    "expected_prior_trading_day": item.expected_prior_trading_day.isoformat()
                    if item.expected_prior_trading_day is not None
                    else None,
                    "ranking_trading_day": item.ranking_trading_day.isoformat()
                    if item.ranking_trading_day is not None
                    else None,
                    "selected_instrument_id": item.selected_instrument_id,
                    "ranked_instrument_ids": list(item.ranked_instrument_ids),
                    "excluded_instrument_ids": list(item.excluded_instrument_ids),
                    "blockers": list(item.blockers),
                    **({"selected_score": item.selected_score} if market_snapshot_mode else {}),
                    **(
                        {"exclusion_reason": item.exclusion_reason}
                        if item.exclusion_reason
                        else {}
                    ),
                }
                for item in self.products
            ],
            "candidates": [
                {
                    "product": _product_dict(item.product),
                    "instrument_id": item.instrument_id,
                    "status": item.status,
                    "reason": item.reason,
                    "expiry_date": item.expiry_date.isoformat()
                    if item.expiry_date is not None
                    else None,
                    "trading_days_to_expiry": item.trading_days_to_expiry,
                    "trading_days_to_expiry_lower_bound": item.trading_days_to_expiry_lower_bound,
                    "expiry_distance_exact": item.expiry_distance_exact,
                    "open_interest": item.open_interest,
                    "volume": item.volume,
                    "rank": item.rank,
                    **(
                        {
                            "ranking_score": item.ranking_score,
                            "asset_type": item.asset_type,
                            "contract_kind": item.contract_kind,
                            "is_trading": item.is_trading,
                            "quote_trading_day": item.quote_trading_day.isoformat()
                            if item.quote_trading_day is not None
                            else None,
                            "provider_event_date": item.provider_event_date.isoformat()
                            if item.provider_event_date is not None
                            else None,
                            "provider_event_time": item.provider_event_time,
                            "provider_event_millisecond": item.provider_event_millisecond,
                            "volume_semantics": item.volume_semantics,
                        }
                        if market_snapshot_mode
                        else {}
                    ),
                }
                for item in self.candidates
            ],
            "blockers": list(self.blockers),
        }
        if market_snapshot_mode:
            projection["policy"].update(
                {
                    "ranking_mode": self.policy.ranking_mode,
                    "score_formula": "0.5 * open_interest + 0.5 * volume",
                    "weights": {"open_interest": "0.5", "volume": "0.5"},
                    "metric_unit": "contracts",
                    "volume_semantics": "cumulative_trading_day",
                    "ranking": [
                        "equal_weight_score_desc",
                        "open_interest_desc",
                        "volume_desc",
                        "expiry_date_asc",
                        "instrument_id_asc",
                    ],
                    "max_snapshot_age_seconds": self.policy.max_snapshot_age_seconds,
                    "max_collection_span_seconds": self.policy.max_collection_span_seconds,
                }
            )
            projection["market_snapshot"] = (
                json.loads(self.market_snapshot_evidence_json)
                if self.market_snapshot_evidence_json is not None
                else None
            )
            projection["market_coverage"] = [
                {
                    "product": _product_dict(item.product),
                    "catalog_instrument_ids": list(item.catalog_instrument_ids),
                    "expected_quote_instrument_ids": list(item.expected_quote_instrument_ids),
                    "observed_quote_instrument_ids": list(item.observed_quote_instrument_ids),
                    "metric_unit": item.metric_unit,
                    "volume_semantics": item.volume_semantics,
                }
                for item in self.market_coverage
            ]
        return projection

    @property
    def manifest_sha256(self) -> str:
        return _canonical_json_sha256(self.to_dict())


def _product_dict(key: ProductKey) -> dict[str, str]:
    return {"exchange_id": key.exchange_id, "product_id": key.product_id}


def _coverage_dict(item: ProductRankingCoverage) -> dict[str, Any]:
    return {
        "product": _product_dict(item.product),
        "candidate_count": item.candidate_count,
        "expected_instrument_ids": list(item.expected_instrument_ids),
        "ranking_instrument_ids": list(item.ranking_instrument_ids),
        "candidate_set_complete": item.candidate_set_complete,
        "ranking_values_complete": item.ranking_values_complete,
        "finalized": item.finalized,
        "latest_available": item.latest_available,
        "ranking_trading_day": item.ranking_trading_day.isoformat()
        if item.ranking_trading_day is not None
        else None,
        "open_interest_unit": item.open_interest_unit,
        "volume_unit": item.volume_unit,
    }
