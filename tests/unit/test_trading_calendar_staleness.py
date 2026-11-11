"""Synthetic per-day claims verify uncertainty arithmetic, not venue facts."""

from datetime import date, datetime, timedelta
from itertools import product

import pytest

from bt_api_py.trading_calendar_staleness import (
    MAX_DAYS,
    DailyObservation,
    EvidenceClaim,
    VenueHistory,
    evaluate_staleness,
)

CLOCK = EvidenceClaim("VERIFIED", "synthetic:clock-bound-and-date")
SCOPE = EvidenceClaim("VERIFIED", "synthetic:scope")
DAY = EvidenceClaim("VERIFIED", "synthetic:trading-date")
CHECK = EvidenceClaim("VERIFIED", "synthetic:check")
DEADLINE = EvidenceClaim("VERIFIED", "synthetic:elapsed-deadline")


def day(
    day_date,
    trading="TRADING",
    check="NO_SUCCESS",
    *,
    trading_claim=DAY,
    check_claim=CHECK,
    deadline_claim=DEADLINE,
):
    return DailyObservation(day_date, trading, check, trading_claim, check_claim, deadline_claim)


def history(days, venue="SHFE", scope="account-calendar", scope_claim=SCOPE):
    return VenueHistory(venue, scope, scope_claim, tuple(days))


def evaluate(histories, as_of=date(2026, 11, 9), clock=CLOCK, **kwargs):
    return evaluate_staleness(tuple(histories), as_of_date=as_of, clock_claim=clock, **kwargs)


def test_friday_and_monday_failures_count_two_trading_days_across_weekend():
    days = [
        day(date(2026, 11, 6)),
        day(date(2026, 11, 7), "CLOSED"),
        day(date(2026, 11, 8), "CLOSED"),
        day(date(2026, 11, 9)),
    ]
    (result,) = evaluate([history(days)])
    assert result.status == "WARNING"
    assert result.min_failed_trading_days == 2
    assert result.max_failed_trading_days is None  # at least 2, not an exact count
    assert not hasattr(result, "last_success_at")


def test_weekend_success_resets_even_when_friday_and_monday_fail():
    days = [
        day(date(2026, 11, 6)),
        day(date(2026, 11, 7), "CLOSED", "SUCCESS"),
        day(date(2026, 11, 8), "CLOSED"),
        day(date(2026, 11, 9)),
    ]
    (result,) = evaluate([history(days)])
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        1,
        1,
    )


def test_holiday_failures_do_not_add_and_success_on_any_date_resets():
    days = [
        day(date(2026, 11, 5), check="SUCCESS"),
        day(date(2026, 11, 6), "CLOSED"),
        day(date(2026, 11, 7), "CLOSED"),
        day(date(2026, 11, 8), "CLOSED"),
        day(date(2026, 11, 9), "CLOSED"),
    ]
    (result,) = evaluate([history(days)])
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        0,
        0,
    )


def test_unknown_initial_state_is_not_assumed_zero():
    (result,) = evaluate([history([day(date(2026, 11, 9))])])
    assert result.status == "UNKNOWN"
    assert result.min_failed_trading_days == 1 and result.max_failed_trading_days is None


def test_unknown_then_two_explicit_failures_warn_and_success_resets():
    days = [
        day(date(2026, 11, 6), "UNKNOWN", "UNKNOWN", trading_claim=None, check_claim=None),
        day(date(2026, 11, 7), "CLOSED"),
        day(date(2026, 11, 8)),
        day(date(2026, 11, 9)),
    ]
    (result,) = evaluate([history(days)])
    assert result.status == "WARNING" and result.min_failed_trading_days == 2
    reset = days + [day(date(2026, 11, 10), "CLOSED", "SUCCESS")]
    (result,) = evaluate([history(reset)], as_of=date(2026, 11, 10))
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        0,
        0,
    )


def test_unverified_daily_source_or_unknown_check_propagates_possibilities():
    days = [
        day(date(2026, 11, 8), check="SUCCESS"),
        day(date(2026, 11, 9), trading="TRADING", trading_claim=None),
    ]
    (result,) = evaluate([history(days)])
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        0,
        1,
    )
    assert result.min_failed_trading_days != result.max_failed_trading_days
    days[1] = day(date(2026, 11, 9), check="SUCCESS", check_claim=None)
    (result,) = evaluate([history(days)])
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        0,
        1,
    )


def test_no_success_without_elapsed_deadline_cannot_be_treated_as_failure():
    days = [day(date(2026, 11, 8), check="SUCCESS"), day(date(2026, 11, 9), deadline_claim=None)]
    (result,) = evaluate([history(days)])
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        0,
        1,
    )


def test_each_venue_is_independent_and_no_failure_refreshes_another():
    first = [day(date(2026, 11, 8), check="SUCCESS"), day(date(2026, 11, 9), check="SUCCESS")]
    second = [day(date(2026, 11, 8)), day(date(2026, 11, 9))]
    unknown = [day(date(2026, 11, 8), check="UNKNOWN"), day(date(2026, 11, 9))]
    results = evaluate([history(first, "SHFE"), history(second, "DCE"), history(unknown, "GFEX")])
    assert [r.status for r in results] == ["OK", "WARNING", "UNKNOWN"]
    assert [r.min_failed_trading_days for r in results] == [0, 2, 1]


@pytest.mark.parametrize(
    "dates",
    [
        (date(2026, 11, 7), date(2026, 11, 9)),
        (date(2026, 11, 9), date(2026, 11, 9)),
        (date(2026, 11, 9), date(2026, 11, 8)),
    ],
)
def test_missing_duplicate_and_out_of_order_natural_dates_are_rejected(dates):
    with pytest.raises(ValueError, match="consecutive"):
        evaluate([history([day(d) for d in dates])])


def test_cross_year_consecutive_days_and_custom_threshold():
    days = [
        day(date(2026, 12, 30), check="SUCCESS"),
        day(date(2026, 12, 31)),
        day(date(2027, 1, 1), "CLOSED"),
        day(date(2027, 1, 2)),
        day(date(2027, 1, 3)),
    ]
    (result,) = evaluate([history(days)], as_of=date(2027, 1, 3), threshold=3)
    assert result.status == "WARNING" and result.min_failed_trading_days == 3


def test_uncertainty_bounds_match_exhaustive_three_day_possibilities():
    # Enumerate actual possible histories independently of the interval update.
    day_states = ("TRADING", "CLOSED", "UNKNOWN")
    check_states = ("SUCCESS", "NO_SUCCESS", "UNKNOWN")
    for choices in product(product(day_states, check_states), repeat=3):
        possible = {0, 1, 2}  # 2 represents two or more failures.
        days = []
        for index, (day_state, check_state) in enumerate(choices):
            days.append(day(date(2026, 11, 7) + timedelta(days=index), day_state, check_state))
            trading_cases = ("TRADING", "CLOSED") if day_state == "UNKNOWN" else (day_state,)
            check_cases = ("SUCCESS", "NO_SUCCESS") if check_state == "UNKNOWN" else (check_state,)
            possible = {
                0 if check == "SUCCESS" else min(2, count + (trading == "TRADING"))
                for count, trading, check in product(possible, trading_cases, check_cases)
            }
        (result,) = evaluate([history(days)])
        assert result.min_failed_trading_days == min(possible)
        assert result.max_failed_trading_days == (None if 2 in possible else max(possible))
        assert result.status == (
            "WARNING" if min(possible) == 2 else "UNKNOWN" if 2 in possible else "OK"
        )


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"clock": None}, "CLOCK_UNVERIFIED"),
        ({"clock": EvidenceClaim("UNVERIFIED", "clock")}, "CLOCK_UNVERIFIED"),
        ({"as_of": None}, "CLOCK_UNVERIFIED"),
        ({"as_of": date(2026, 11, 10)}, "OBSERVATIONS_NOT_CURRENT"),
    ],
)
def test_unavailable_clock_or_omitted_end_of_window_is_unknown(kwargs, reason):
    (result,) = evaluate([history([day(date(2026, 11, 9), check="SUCCESS")])], **kwargs)
    assert result.status == "UNKNOWN" and reason in result.reasons
    assert result.min_failed_trading_days is None and result.max_failed_trading_days is None


def test_unverified_scope_or_absent_observations_is_unknown():
    a = history([day(date(2026, 11, 9), check="SUCCESS")], scope_claim=None)
    b = history([], venue="DCE")
    first, second = evaluate([a, b])
    assert first.status == "UNKNOWN" and "SCOPE_UNVERIFIED" in first.reasons
    assert second.status == "UNKNOWN" and "NO_OBSERVATIONS" in second.reasons


def test_untrusted_scope_and_clock_cannot_warn_even_after_two_claimed_failures():
    days = [day(date(2026, 11, 8)), day(date(2026, 11, 9))]
    no_scope = evaluate([history(days, scope_claim=None)])[0]
    no_clock = evaluate([history(days)], clock=None)[0]
    assert no_scope.status == no_clock.status == "UNKNOWN"
    assert no_scope.min_failed_trading_days is None and no_clock.min_failed_trading_days is None
    assert no_clock.as_of_date is None


def test_proven_success_resets_even_if_its_day_type_is_unknown():
    days = [
        day(date(2026, 11, 8), "UNKNOWN", "SUCCESS", trading_claim=None),
        day(date(2026, 11, 9), "CLOSED"),
    ]
    (result,) = evaluate([history(days)])
    assert (result.status, result.min_failed_trading_days, result.max_failed_trading_days) == (
        "OK",
        0,
        0,
    )


@pytest.mark.parametrize("threshold", [0, -1, True, 1.5, MAX_DAYS + 1])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError):
        evaluate([history([])], threshold=threshold)


@pytest.mark.parametrize(
    "bad",
    [
        VenueHistory("bad", "scope", SCOPE, ()),
        VenueHistory("SHFE", 1, SCOPE, ()),
        VenueHistory("SHFE", "scope", True, ()),
        VenueHistory("SHFE", "scope", SCOPE, []),
        history([day(date(2026, 11, 9), trading="bad")]),
        history([day(date(2026, 11, 9), check="bad")]),
        history([day(date(2026, 11, 9), check_claim=EvidenceClaim(True, "ref"))]),
        history([day(date(2026, 11, 9), deadline_claim=EvidenceClaim("VERIFIED", 3))]),
    ],
)
def test_invalid_types_and_statuses_rejected(bad):
    with pytest.raises(ValueError):
        evaluate([bad])


def test_duplicate_venue_scopes_and_invalid_date_rejected():
    with pytest.raises(ValueError, match="multiple scopes"):
        evaluate([history([]), history([], scope="different")])
    with pytest.raises(ValueError, match="as_of_date"):
        evaluate([history([])], as_of=datetime(2026, 11, 9))
    assert evaluate([]) == ()
