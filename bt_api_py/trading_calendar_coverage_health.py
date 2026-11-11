"""Pure D67-31 coverage-end arithmetic; no authority, scheduling or IO.

The caller must independently verify and bind clock, version, and source claims
to each venue and version. EvidenceClaim merely carries that caller's declared
assessment and a reference: neither the words nor the reference authenticate
anything. These results are conditional on that external verification and are
not trading permission, full coverage proof or a published/bound-version lookup.
No system clock, account, updates, last-success state, or other venues are read.
The error bound is also an external claim; this function cannot prove it.
D67-10's two-trading-day failure alert is a separate, unimplemented contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_VERSION_KINDS = frozenset({"published", "bound"})
_ASSESSMENTS = frozenset({"VERIFIED", "UNVERIFIED", "UNAVAILABLE"})


@dataclass(frozen=True)
class EvidenceClaim:
    """Caller assertion and reference only, not independently verified evidence."""

    assessment: str
    reference: str | None


@dataclass(frozen=True)
class CoverageObservation:
    venue: str
    version_kind: str
    version_id: str | None
    coverage_end: date | None
    version_claim: EvidenceClaim | None
    source_claim: EvidenceClaim | None


@dataclass(frozen=True)
class CoverageHealth:
    venue: str
    version_kind: str
    version_id: str | None
    status: str
    reasons: tuple[str, ...]
    shanghai_date: date | None
    coverage_end: date | None
    days_remaining: int | None
    threshold_days: int


def _claim_problems(claim: EvidenceClaim | None, label: str) -> list[str]:
    if claim is None:
        return [label + "_UNAVAILABLE"]
    if type(claim) is not EvidenceClaim:
        raise ValueError("invalid evidence claim")
    if type(claim.assessment) is not str or claim.assessment not in _ASSESSMENTS:
        raise ValueError("invalid evidence assessment")
    if claim.reference is not None and type(claim.reference) is not str:
        raise ValueError("invalid evidence reference type")
    if claim.reference is not None and len(claim.reference) > 1024:
        raise ValueError("evidence reference too long")
    if claim.assessment != "VERIFIED":
        return [label + "_" + claim.assessment]
    if claim.reference is None or not claim.reference.strip():
        return [label + "_REFERENCE_MISSING"]
    return []


def _local_date(
    instant: datetime | None, error_bound: timedelta | None
) -> tuple[date | None, str | None]:
    if instant is None:
        return None, "CLOCK_TIME_MISSING"
    if type(instant) is not datetime:
        raise ValueError("clock instant must be a datetime or None")
    try:
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None, "CLOCK_TIMEZONE_UNKNOWN"
        utc_instant = instant.astimezone(UTC)
        shanghai = ZoneInfo("Asia/Shanghai")
    except Exception:
        # User-provided tzinfo implementations and local tzdata can fail.
        return None, "CLOCK_TIMEZONE_UNKNOWN"
    if error_bound is None:
        return None, None
    try:
        earliest = (utc_instant - error_bound).astimezone(shanghai).date()
        latest = (utc_instant + error_bound).astimezone(shanghai).date()
    except (OverflowError, ValueError):
        return None, "CLOCK_ERROR_INTERVAL_UNKNOWN"
    if earliest != latest:
        return None, "CLOCK_DATE_AMBIGUOUS"
    return earliest, None


def evaluate_coverage_health(
    observations: list[CoverageObservation] | tuple[CoverageObservation, ...],
    *,
    clock_instant: datetime | None,
    clock_claim: EvidenceClaim | None,
    clock_error_bound: timedelta | None = None,
    threshold_days: int = 30,
) -> tuple[CoverageHealth, ...]:
    """Calculate per supplied venue and version; missing evidence stays UNKNOWN.

    Inputs do not establish that a version was published, bound, or verified.
    The caller must ensure each asserted reference applies to the exact supplied
    scope and instant. Output covers only the supplied rows, with no implied
    refresh or success for omitted venues. None means an unknown or unbounded
    error. A zero bound is an explicit claim, not a default. No time is read.
    """
    if type(threshold_days) is not int or threshold_days < 0:
        raise ValueError("threshold_days must be a nonnegative integer")
    if type(observations) not in (list, tuple):
        raise ValueError("observations must be a list or tuple")
    if clock_error_bound is not None and (
        type(clock_error_bound) is not timedelta or clock_error_bound < timedelta(0)
    ):
        raise ValueError("clock_error_bound must be a nonnegative timedelta or None")
    clock_issues = _claim_problems(clock_claim, "CLOCK")
    if clock_error_bound is None:
        clock_issues.append("CLOCK_ERROR_BOUND_UNKNOWN")
    today, clock_error = _local_date(clock_instant, clock_error_bound)
    if clock_error:
        clock_issues.append(clock_error)
    if clock_issues:
        today = None

    results = []
    seen = set()
    for row in observations:
        if type(row) is not CoverageObservation:
            raise ValueError("invalid coverage observation")
        if type(row.venue) is not str or row.venue not in _VENUES:
            raise ValueError("invalid venue")
        if type(row.version_kind) is not str or row.version_kind not in _VERSION_KINDS:
            raise ValueError("invalid version kind")
        key = (row.venue, row.version_kind)
        if key in seen:
            raise ValueError("duplicate venue/version observation")
        seen.add(key)
        if row.version_id is not None and type(row.version_id) is not str:
            raise ValueError("invalid version id type")
        if row.version_id is not None and len(row.version_id) > 256:
            raise ValueError("version id too long")
        if row.coverage_end is not None and type(row.coverage_end) is not date:
            raise ValueError("coverage end must be a date or None")
        issues = list(clock_issues)
        issues.extend(_claim_problems(row.version_claim, "VERSION"))
        issues.extend(_claim_problems(row.source_claim, "SOURCE"))
        if row.version_id is None or not row.version_id.strip():
            issues.append("VERSION_ID_MISSING")
        if row.coverage_end is None:
            issues.append("COVERAGE_END_MISSING")
        if issues:
            results.append(
                CoverageHealth(
                    row.venue,
                    row.version_kind,
                    row.version_id,
                    "UNKNOWN",
                    tuple(issues),
                    today,
                    row.coverage_end,
                    None,
                    threshold_days,
                )
            )
            continue
        days_remaining = (row.coverage_end - today).days
        status = "WARNING" if days_remaining <= threshold_days else "OK"
        reason = "END_WITHIN_THRESHOLD" if status == "WARNING" else "END_BEYOND_THRESHOLD"
        results.append(
            CoverageHealth(
                row.venue,
                row.version_kind,
                row.version_id,
                status,
                (reason,),
                today,
                row.coverage_end,
                days_remaining,
                threshold_days,
            )
        )
    return tuple(results)
