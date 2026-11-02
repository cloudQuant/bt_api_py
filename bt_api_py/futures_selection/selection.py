"""Candidate assessment and source-evidence checks for futures selection."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence, Tuple
from .models import (
    CalendarEvidence,
    ContractAssessment,
    ExchangeTradingCalendar,
    FuturesContractRecord,
    MainContractPolicy,
    MainContractRankingSnapshot,
    ProductKey,
    ProductSelection,
    TradingCalendarContractError,
    _KNOWN_NON_FUTURES,
    _RANKING_METRIC_MAX_ABS_EXPONENT,
    _RANKING_METRIC_MAX_DIGITS,
    _SOURCE_SHA256,
    _decimal_text,
    _is_actual_month_contract,
    _number_as_decimal,
)



def _calendar_evidence(calendar: ExchangeTradingCalendar) -> CalendarEvidence:
    return CalendarEvidence(
        exchange_id=calendar.exchange_id,
        source_id=calendar.source_id,
        version=calendar.version,
        available_at=calendar.available_at,
        retrieved_at=calendar.retrieved_at,
        coverage_start=calendar.coverage_start,
        coverage_through=calendar.coverage_through,
        complete=calendar.complete,
        trading_days=calendar.trading_days,
        sha256=calendar.evidence_sha256,
        evidence_mode=calendar.evidence_mode,
        source_manifest_sha256=calendar.source_manifest_sha256,
    )


def _blocked_product(
    product: ProductKey,
    *,
    expected_prior: Optional[date],
    ranking_day: Optional[date],
    blockers: Sequence[str],
    in_target_pool: bool = True,
) -> ProductSelection:
    return ProductSelection(
        product=product,
        in_target_pool=in_target_pool,
        status="BLOCKED" if in_target_pool else "OUT_OF_SCOPE",
        expected_prior_trading_day=expected_prior,
        ranking_trading_day=ranking_day,
        selected_instrument_id=None,
        ranked_instrument_ids=(),
        excluded_instrument_ids=(),
        blockers=tuple(blockers) if in_target_pool else (),
    )


def _source_blockers(
    snapshot: Optional[MainContractRankingSnapshot], decision_at: datetime
) -> Tuple[str, ...]:
    if snapshot is None:
        return ("RANKING_SOURCE_UNAVAILABLE",)
    blockers = list(
        _named_source_blockers(
            "RANKING",
            source_id=snapshot.source_id,
            snapshot_id=snapshot.snapshot_id,
            source_sha256=snapshot.source_sha256,
            available_at=snapshot.available_at,
            retrieved_at=snapshot.retrieved_at,
            decision_at=decision_at,
        )
    )
    blockers.extend(
        _named_source_blockers(
            "CATALOG",
            source_id=snapshot.catalog_source_id,
            snapshot_id=snapshot.catalog_snapshot_id,
            source_sha256=snapshot.catalog_sha256,
            available_at=snapshot.catalog_available_at,
            retrieved_at=snapshot.catalog_retrieved_at,
            decision_at=decision_at,
        )
    )
    return tuple(blockers)


def _named_source_blockers(
    name: str,
    *,
    source_id: Any,
    snapshot_id: Any,
    source_sha256: Any,
    available_at: Any,
    retrieved_at: Any,
    decision_at: datetime,
) -> Tuple[str, ...]:
    """Validate one source's point-in-time identity and observation metadata."""

    blockers = []
    if type(source_id) is not str or not source_id or source_id != source_id.strip():
        blockers.append("{}_SOURCE_ID_MISSING".format(name))
    if type(snapshot_id) is not str or not snapshot_id or snapshot_id != snapshot_id.strip():
        blockers.append("{}_SNAPSHOT_ID_MISSING".format(name))
    if type(source_sha256) is not str or not _SOURCE_SHA256.fullmatch(source_sha256):
        blockers.append("{}_SOURCE_DIGEST_INVALID".format(name))
    if (
        not isinstance(available_at, datetime)
        or available_at.tzinfo is None
        or available_at.utcoffset() is None
    ):
        blockers.append("{}_AVAILABLE_AT_INVALID".format(name))
    elif available_at.astimezone(timezone.utc) > decision_at.astimezone(timezone.utc):
        blockers.append("{}_NOT_AVAILABLE_AT_DECISION".format(name))
    if (
        not isinstance(retrieved_at, datetime)
        or retrieved_at.tzinfo is None
        or retrieved_at.utcoffset() is None
    ):
        blockers.append("{}_RETRIEVED_AT_INVALID".format(name))
    else:
        retrieved_at_utc = retrieved_at.astimezone(timezone.utc)
        if retrieved_at_utc > decision_at.astimezone(timezone.utc):
            blockers.append("{}_RETRIEVED_AFTER_DECISION".format(name))
        if (
            isinstance(available_at, datetime)
            and available_at.tzinfo is not None
            and available_at.utcoffset() is not None
            and retrieved_at_utc < available_at.astimezone(timezone.utc)
        ):
            blockers.append("{}_RETRIEVED_BEFORE_AVAILABLE".format(name))
    return tuple(blockers)


def _source_text(value: Any) -> Optional[str]:
    if type(value) is str and value and value == value.strip():
        return value
    return None


def _source_digest(value: Any) -> Optional[str]:
    if type(value) is str and _SOURCE_SHA256.fullmatch(value):
        return value
    return None


def _source_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value
    return None


def _exact_equal_weight_score(open_interest: Decimal, volume: Decimal) -> Optional[Decimal]:
    """Return the exact Decimal mean without using the active Decimal context.

    Ranking metrics are normally bounded count values. A finite bound keeps
    hostile or malformed exponents from allocating enormous powers of ten;
    values outside that bound fail closed rather than silently rounding.
    """

    left = open_interest.as_tuple()
    right = volume.as_tuple()
    left_exponent = left.exponent
    right_exponent = right.exponent
    if (
        not isinstance(left_exponent, int)
        or not isinstance(right_exponent, int)
        or abs(left_exponent) > _RANKING_METRIC_MAX_ABS_EXPONENT
        or abs(right_exponent) > _RANKING_METRIC_MAX_ABS_EXPONENT
    ):
        return None
    if (
        len(left.digits) > _RANKING_METRIC_MAX_DIGITS
        or len(right.digits) > _RANKING_METRIC_MAX_DIGITS
    ):
        return None
    left_digits = "".join(str(digit) for digit in left.digits) or "0"
    right_digits = "".join(str(digit) for digit in right.digits) or "0"
    common_exponent = min(left_exponent, right_exponent)
    left_shift = left_exponent - common_exponent
    right_shift = right_exponent - common_exponent
    if (
        len(left_digits) + left_shift > _RANKING_METRIC_MAX_DIGITS
        or len(right_digits) + right_shift > _RANKING_METRIC_MAX_DIGITS
        or abs(common_exponent - 1) > _RANKING_METRIC_MAX_ABS_EXPONENT + 1
    ):
        return None
    left_coefficient = int(left_digits) * (10**left_shift)
    right_coefficient = int(right_digits) * (10**right_shift)
    if left.sign:
        left_coefficient = -left_coefficient
    if right.sign:
        right_coefficient = -right_coefficient
    score_coefficient = (left_coefficient + right_coefficient) * 5
    digits = tuple(int(char) for char in str(abs(score_coefficient)))
    return Decimal((1 if score_coefficient < 0 else 0, digits, common_exponent - 1))


def _calendar_for(
    product: ProductKey,
    calendars: Mapping[str, ExchangeTradingCalendar],
    decision_at: datetime,
) -> Tuple[Optional[ExchangeTradingCalendar], Tuple[str, ...]]:
    calendar = calendars.get(product.exchange_id)
    if calendar is None:
        return None, ("EXCHANGE_CALENDAR_UNAVAILABLE",)
    if not calendar.complete:
        return calendar, ("EXCHANGE_CALENDAR_INCOMPLETE",)
    if calendar.available_at.astimezone(timezone.utc) > decision_at.astimezone(timezone.utc):
        return None, ("CALENDAR_NOT_AVAILABLE_AT_DECISION",)
    if calendar.retrieved_at.astimezone(timezone.utc) > decision_at.astimezone(timezone.utc):
        return None, ("CALENDAR_RETRIEVED_AFTER_DECISION",)
    return calendar, ()


def _candidate_assessment(
    record: FuturesContractRecord,
    *,
    current_trading_day: Optional[date],
    calendar: Optional[ExchangeTradingCalendar],
    policy: MainContractPolicy,
    suppress_source_values: bool,
    exact_metric_limit: bool = False,
    require_month_contract: bool = True,
) -> Tuple[ContractAssessment, bool]:
    """Return the assessment and whether it is rank-eligible."""

    if suppress_source_values:
        return (
            ContractAssessment(
                product=record.product,
                instrument_id=record.instrument_id,
                status="BLOCKED",
                reason="SOURCE_PROVENANCE_INVALID",
                expiry_date=None,
                trading_days_to_expiry=None,
                open_interest=None,
                volume=None,
                rank=None,
            ),
            False,
        )

    asset_type = record.asset_type.casefold() if record.asset_type is not None else None
    if asset_type in _KNOWN_NON_FUTURES:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "EXCLUDED",
                "NOT_ORDINARY_FUTURE",
                record.expiry_date,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if asset_type != "future":
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "BLOCKED",
                "ASSET_TYPE_UNKNOWN",
                record.expiry_date,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if record.is_trading is False:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "EXCLUDED",
                "NOT_TRADING",
                record.expiry_date,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if record.is_trading is not True:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "BLOCKED",
                "TRADING_STATUS_UNKNOWN",
                record.expiry_date,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if require_month_contract and not _is_actual_month_contract(
        record.instrument_id, record.product.product_id
    ):
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "EXCLUDED",
                "NOT_ACTUAL_MONTH_CONTRACT",
                record.expiry_date,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if record.expiry_date is None:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "BLOCKED",
                "EXPIRY_UNAVAILABLE",
                None,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if current_trading_day is None or calendar is None:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "BLOCKED",
                "EXPIRY_CALENDAR_UNAVAILABLE",
                record.expiry_date,
                None,
                None,
                None,
                None,
            ),
            False,
        )
    if record.expiry_date <= current_trading_day:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "EXCLUDED",
                "EXPIRED_OR_EXPIRY_DAY",
                record.expiry_date,
                0,
                None,
                None,
                None,
                0,
                True,
            ),
            False,
        )
    if record.expiry_date > calendar.coverage_through:
        lower_bound = sum(
            current_trading_day < day <= calendar.coverage_through for day in calendar.trading_days
        )
        remaining = None
        distance_exact = False
        if lower_bound < policy.minimum_trading_days_to_expiry:
            return (
                ContractAssessment(
                    record.product,
                    record.instrument_id,
                    "BLOCKED",
                    "EXPIRY_SAFETY_UNPROVEN",
                    record.expiry_date,
                    None,
                    None,
                    None,
                    None,
                    lower_bound,
                    False,
                ),
                False,
            )
    else:
        try:
            remaining = calendar.trading_days_to_expiry(current_trading_day, record.expiry_date)
        except TradingCalendarContractError as exc:
            return (
                ContractAssessment(
                    record.product,
                    record.instrument_id,
                    "BLOCKED",
                    exc.code,
                    record.expiry_date,
                    None,
                    None,
                    None,
                    None,
                ),
                False,
            )
        lower_bound = remaining
        distance_exact = True
    if remaining is not None and remaining < policy.minimum_trading_days_to_expiry:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "EXCLUDED",
                "EXPIRY_TOO_NEAR",
                record.expiry_date,
                remaining,
                None,
                None,
                None,
                lower_bound,
                distance_exact,
            ),
            False,
        )
    open_interest = _number_as_decimal(record.open_interest)
    volume = _number_as_decimal(record.volume)
    if open_interest is None or volume is None:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "BLOCKED",
                "RANKING_VALUE_MISSING_OR_INVALID",
                record.expiry_date,
                remaining,
                None,
                None,
                None,
                lower_bound,
                distance_exact,
            ),
            False,
        )
    if exact_metric_limit and _exact_equal_weight_score(open_interest, volume) is None:
        return (
            ContractAssessment(
                record.product,
                record.instrument_id,
                "BLOCKED",
                "RANKING_VALUE_RANGE_UNSUPPORTED",
                record.expiry_date,
                remaining,
                None,
                None,
                None,
                lower_bound,
                distance_exact,
            ),
            False,
        )
    return (
        ContractAssessment(
            record.product,
            record.instrument_id,
            "ELIGIBLE",
            None,
            record.expiry_date,
            remaining,
            _decimal_text(open_interest),
            _decimal_text(volume),
            None,
            lower_bound,
            distance_exact,
        ),
        True,
    )
