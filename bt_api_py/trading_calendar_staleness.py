"""Pure D67-10 arithmetic on explicitly supplied, bounded daily observations.

No clock, exchange calendar, filesystem, account, scheduler or notification is
read. Dates are sequential Shanghai natural dates supplied by the caller; the
caller must independently prove the as-of date (including clock error bounds),
venue/scope, daily trading status, check outcomes, and elapsed check deadlines.
EvidenceClaim is only a caller assertion and reference, not proof of any fact.
A NO_SUCCESS without both outcome and elapsed-deadline evidence is UNKNOWN; the
function never infers that a machine ran or that a deadline elapsed. A success
on any natural date resets the count, including holidays. Unknowns propagate
until later explicit facts narrow the possibilities.

Initial history is unknown, including the possibility the threshold was already
reached. Counts are saturated at the threshold: a None upper bound means the
true maximum is unbounded, not equal to the threshold. This is separate from
D67-31 coverage-end warnings and does not forbid otherwise valid calendar use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_DAY_STATUS = frozenset({"TRADING", "CLOSED", "UNKNOWN"})
_CHECK_STATUS = frozenset({"SUCCESS", "NO_SUCCESS", "UNKNOWN"})
_ASSESSMENTS = frozenset({"VERIFIED", "UNVERIFIED", "UNAVAILABLE"})
MAX_DAYS = 3700


@dataclass(frozen=True)
class EvidenceClaim:
    """Caller assertion and reference, neither of which authenticates a fact."""

    assessment: str
    reference: str | None


@dataclass(frozen=True)
class DailyObservation:
    day: date
    trading_day: str
    check: str
    trading_claim: EvidenceClaim | None
    check_claim: EvidenceClaim | None
    deadline_claim: EvidenceClaim | None = None


@dataclass(frozen=True)
class VenueHistory:
    venue: str
    scope_id: str | None
    scope_claim: EvidenceClaim | None
    days: tuple[DailyObservation, ...]


@dataclass(frozen=True)
class StalenessReport:
    venue: str
    scope_id: str | None
    as_of_date: date | None
    status: str
    reasons: tuple[str, ...]
    min_failed_trading_days: int | None
    max_failed_trading_days: int | None
    threshold: int


def _claim_trusted(claim: EvidenceClaim | None) -> bool:
    if claim is None:
        return False
    if type(claim) is not EvidenceClaim:
        raise ValueError("invalid evidence claim")
    if type(claim.assessment) is not str or claim.assessment not in _ASSESSMENTS:
        raise ValueError("invalid evidence assessment")
    if claim.reference is not None and type(claim.reference) is not str:
        raise ValueError("invalid evidence reference")
    if claim.reference is not None and len(claim.reference) > 1024:
        raise ValueError("evidence reference too long")
    return claim.assessment == "VERIFIED" and bool(claim.reference and claim.reference.strip())


def _validate_history(history: VenueHistory) -> None:
    if type(history) is not VenueHistory:
        raise ValueError("invalid venue history")
    if type(history.venue) is not str or history.venue not in _VENUES:
        raise ValueError("invalid venue")
    if history.scope_id is not None and type(history.scope_id) is not str:
        raise ValueError("invalid scope")
    if history.scope_id is not None and len(history.scope_id) > 256:
        raise ValueError("scope too long")
    _claim_trusted(history.scope_claim)
    if type(history.days) is not tuple or len(history.days) > MAX_DAYS:
        raise ValueError("days must be a bounded tuple")
    previous = None
    for item in history.days:
        if type(item) is not DailyObservation or type(item.day) is not date:
            raise ValueError("invalid daily observation")
        if previous is not None and (item.day - previous).days != 1:
            raise ValueError("daily observations must be consecutive and ordered")
        previous = item.day
        if type(item.trading_day) is not str or item.trading_day not in _DAY_STATUS:
            raise ValueError("invalid trading-day status")
        if type(item.check) is not str or item.check not in _CHECK_STATUS:
            raise ValueError("invalid check status")
        _claim_trusted(item.trading_claim)
        _claim_trusted(item.check_claim)
        _claim_trusted(item.deadline_claim)


def evaluate_staleness(
    histories: tuple[VenueHistory, ...],
    *,
    as_of_date: date | None,
    clock_claim: EvidenceClaim | None,
    threshold: int = 2,
) -> tuple[StalenessReport, ...]:
    """Evaluate only supplied venue scopes; UNKNOWN never becomes a success.

    The caller must ensure every declaration applies to the indicated venue,
    date, scope, check and deadline. Neither a reference nor VERIFIED proves it.
    The input window must end on the caller-proven as-of date; gaps, duplicates,
    reverse order, or multiple scopes for the same venue are rejected.
    """
    if type(threshold) is not int or not 1 <= threshold <= MAX_DAYS:
        raise ValueError("threshold must be a positive bounded integer")
    if type(histories) is not tuple or len(histories) > len(_VENUES):
        raise ValueError("histories must be a bounded tuple")
    if as_of_date is not None and type(as_of_date) is not date:
        raise ValueError("as_of_date must be a date or None")
    clock_trusted = _claim_trusted(clock_claim)
    seen = set()
    reports = []
    for history in histories:
        _validate_history(history)
        if history.venue in seen:
            raise ValueError("multiple scopes for venue")
        seen.add(history.venue)
        reasons = []
        if as_of_date is None or not clock_trusted:
            reasons.append("CLOCK_UNVERIFIED")
        if (
            not history.scope_id
            or not history.scope_id.strip()
            or not _claim_trusted(history.scope_claim)
        ):
            reasons.append("SCOPE_UNVERIFIED")
        if not history.days:
            reasons.append("NO_OBSERVATIONS")
        elif as_of_date is not None and history.days[-1].day != as_of_date:
            reasons.append("OBSERVATIONS_NOT_CURRENT")
        if reasons:
            reports.append(
                StalenessReport(
                    history.venue,
                    history.scope_id,
                    as_of_date if clock_trusted else None,
                    "UNKNOWN",
                    tuple(reasons),
                    None,
                    None,
                    threshold,
                )
            )
            continue

        # [lower, upper] contains the possible counts; upper == threshold is
        # a saturated sentinel for threshold-or-greater, not an exact number.
        lower, upper = 0, threshold
        for item in history.days:
            trading = item.trading_day if _claim_trusted(item.trading_claim) else "UNKNOWN"
            check = item.check if _claim_trusted(item.check_claim) else "UNKNOWN"
            if check == "NO_SUCCESS" and not _claim_trusted(item.deadline_claim):
                check = "UNKNOWN"
            if check == "SUCCESS":
                lower, upper = 0, 0
            elif check == "NO_SUCCESS":
                if trading == "TRADING":
                    lower, upper = min(threshold, lower + 1), min(threshold, upper + 1)
                elif trading == "UNKNOWN":
                    upper = min(threshold, upper + 1)
            else:
                lower = 0
                if trading != "CLOSED":
                    upper = min(threshold, upper + 1)
        if lower == threshold:
            status, reason = "WARNING", "THRESHOLD_PROVEN"
        elif upper < threshold:
            status, reason = "OK", "BELOW_THRESHOLD_PROVEN"
        else:
            status, reason = "UNKNOWN", "COUNT_UNCERTAIN"
        reports.append(
            StalenessReport(
                history.venue,
                history.scope_id,
                as_of_date,
                status,
                (reason,),
                lower,
                None if upper == threshold else upper,
                threshold,
            )
        )
    return tuple(reports)
