"""Synthetic claims demonstrate arithmetic, not verified exchange coverage."""

from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

import pytest

from bt_api_py.trading_calendar_coverage_health import (
    CoverageObservation,
    EvidenceClaim,
    evaluate_coverage_health,
)

CLOCK = EvidenceClaim("VERIFIED", "synthetic:clock")
VERSION = EvidenceClaim("VERIFIED", "synthetic:version")
SOURCE = EvidenceClaim("VERIFIED", "synthetic:source")


def obs(
    venue="SHFE",
    kind="published",
    version="v1",
    end=date(2026, 12, 31),
    version_claim=VERSION,
    source_claim=SOURCE,
):
    return CoverageObservation(venue, kind, version, end, version_claim, source_claim)


def evaluate(
    rows,
    instant=datetime(2026, 12, 1, tzinfo=UTC),
    claim=CLOCK,
    error_bound=timedelta(0),
    **kwargs,
):
    return evaluate_coverage_health(
        rows, clock_instant=instant, clock_claim=claim, clock_error_bound=error_bound, **kwargs
    )


@pytest.mark.parametrize(
    "now,end,threshold,status,days",
    [
        (date(2026, 12, 1), date(2026, 12, 31), 30, "WARNING", 30),
        (date(2026, 11, 30), date(2026, 12, 31), 30, "OK", 31),
        (date(2026, 12, 31), date(2026, 12, 31), 30, "WARNING", 0),
        (date(2027, 1, 1), date(2026, 12, 31), 30, "WARNING", -1),
        (date(2026, 12, 30), date(2026, 12, 31), 0, "OK", 1),
        (date(2026, 12, 31), date(2026, 12, 31), 0, "WARNING", 0),
        (date(2028, 1, 30), date(2028, 2, 29), 30, "WARNING", 30),
        (date(2028, 2, 28), date(2028, 3, 1), 1, "OK", 2),
        (date(2027, 12, 30), date(2028, 1, 1), 2, "WARNING", 2),
    ],
)
def test_calendar_day_boundaries(now, end, threshold, status, days):
    instant = datetime(now.year, now.month, now.day, tzinfo=UTC)
    (result,) = evaluate([obs(end=end)], instant=instant, threshold_days=threshold)
    assert (result.status, result.days_remaining, result.threshold_days) == (
        status,
        days,
        threshold,
    )
    assert result.shanghai_date == now
    assert result.coverage_end == end


def test_default_threshold_uses_shanghai_date_not_utc_or_trading_day():
    instant = datetime(2026, 11, 30, 16, 0, tzinfo=UTC)
    (result,) = evaluate([obs()], instant=instant)
    assert result.shanghai_date == date(2026, 12, 1)
    assert result.status == "WARNING" and result.days_remaining == 30


def test_clock_uncertainty_crossing_shanghai_midnight_is_unknown():
    instant = datetime(2026, 11, 30, 15, 59, 59, 900000, tzinfo=UTC)
    (result,) = evaluate([obs()], instant=instant, error_bound=timedelta(milliseconds=200))
    assert result.status == "UNKNOWN"
    assert result.days_remaining is None and result.shanghai_date is None
    assert "CLOCK_DATE_AMBIGUOUS" in result.reasons


def test_small_clock_uncertainty_does_not_cross_midnight():
    instant = datetime(2026, 11, 30, 15, 59, 59, 900000, tzinfo=UTC)
    (result,) = evaluate([obs()], instant=instant, error_bound=timedelta(milliseconds=50))
    assert result.shanghai_date == date(2026, 11, 30)
    assert result.status == "OK" and result.days_remaining == 31


def test_missing_or_unbounded_clock_error_is_unknown():
    (result,) = evaluate_coverage_health(
        [obs()], clock_instant=datetime(2026, 12, 1, tzinfo=UTC), clock_claim=CLOCK
    )
    assert result.status == "UNKNOWN" and result.days_remaining is None
    assert "CLOCK_ERROR_BOUND_UNKNOWN" in result.reasons
    (result,) = evaluate([obs()], error_bound=None)
    assert result.status == "UNKNOWN" and "CLOCK_ERROR_BOUND_UNKNOWN" in result.reasons


def test_clock_uncertainty_crossing_leap_day_boundary_is_unknown():
    instant = datetime(2028, 2, 29, 15, 59, 59, tzinfo=UTC)
    (result,) = evaluate(
        [obs(end=date(2028, 3, 30))], instant=instant, error_bound=timedelta(seconds=1)
    )
    assert result.status == "UNKNOWN" and "CLOCK_DATE_AMBIGUOUS" in result.reasons


@pytest.mark.parametrize("error_bound", [timedelta(microseconds=-1), True, 0, float("inf")])
def test_invalid_clock_error_bound_is_rejected(error_bound):
    with pytest.raises(ValueError, match="clock_error_bound"):
        evaluate([obs()], error_bound=error_bound)


def test_unrepresentable_clock_error_interval_is_unknown():
    (result,) = evaluate([obs()], error_bound=timedelta.max)
    assert result.status == "UNKNOWN" and "CLOCK_ERROR_INTERVAL_UNKNOWN" in result.reasons


def test_published_and_bound_versions_and_each_venue_are_independent():
    rows = [
        obs("SHFE", "published", "new", date(2027, 12, 31)),
        obs("SHFE", "bound", "old", date(2026, 12, 31)),
        obs("DCE", "published", "v2", date(2027, 12, 31), source_claim=None),
        obs("GFEX", "published", None, None),
    ]
    results = evaluate(rows)
    assert [(r.venue, r.version_kind, r.version_id, r.status) for r in results] == [
        ("SHFE", "published", "new", "OK"),
        ("SHFE", "bound", "old", "WARNING"),
        ("DCE", "published", "v2", "UNKNOWN"),
        ("GFEX", "published", None, "UNKNOWN"),
    ]
    assert results[2].days_remaining is None and "SOURCE_UNAVAILABLE" in results[2].reasons
    assert set(results[3].reasons) == {"VERSION_ID_MISSING", "COVERAGE_END_MISSING"}


@pytest.mark.parametrize(
    "instant,claim,reason",
    [
        (None, CLOCK, "CLOCK_TIME_MISSING"),
        (datetime(2026, 12, 1), CLOCK, "CLOCK_TIMEZONE_UNKNOWN"),
        (datetime(2026, 12, 1, tzinfo=UTC), None, "CLOCK_UNAVAILABLE"),
        (datetime(2026, 12, 1, tzinfo=UTC), EvidenceClaim("UNVERIFIED", "x"), "CLOCK_UNVERIFIED"),
        (
            datetime(2026, 12, 1, tzinfo=UTC),
            EvidenceClaim("VERIFIED", ""),
            "CLOCK_REFERENCE_MISSING",
        ),
    ],
)
def test_unverifiable_clock_is_unknown(instant, claim, reason):
    (result,) = evaluate([obs()], instant=instant, claim=claim)
    assert result.status == "UNKNOWN" and result.shanghai_date is None
    assert result.days_remaining is None and reason in result.reasons


def test_invalid_timezone_is_unknown_even_if_caller_declares_verified():
    class BrokenZone(tzinfo):
        def utcoffset(self, dt):
            raise ValueError("invalid offset")

    (result,) = evaluate([obs()], instant=datetime(2026, 12, 1, tzinfo=BrokenZone()))
    assert result.status == "UNKNOWN" and "CLOCK_TIMEZONE_UNKNOWN" in result.reasons


@pytest.mark.parametrize(
    "version_claim,source_claim,reason",
    [
        (None, SOURCE, "VERSION_UNAVAILABLE"),
        (EvidenceClaim("UNVERIFIED", "v"), SOURCE, "VERSION_UNVERIFIED"),
        (EvidenceClaim("UNAVAILABLE", "v"), SOURCE, "VERSION_UNAVAILABLE"),
        (EvidenceClaim("VERIFIED", " "), SOURCE, "VERSION_REFERENCE_MISSING"),
        (VERSION, None, "SOURCE_UNAVAILABLE"),
        (VERSION, EvidenceClaim("UNVERIFIED", "x"), "SOURCE_UNVERIFIED"),
        (VERSION, EvidenceClaim("VERIFIED", None), "SOURCE_REFERENCE_MISSING"),
    ],
)
def test_unverified_or_missing_source_or_version_does_not_produce_a_day_count(
    version_claim, source_claim, reason
):
    (result,) = evaluate([obs(version_claim=version_claim, source_claim=source_claim)])
    assert result.status == "UNKNOWN" and result.days_remaining is None
    assert reason in result.reasons


@pytest.mark.parametrize(
    "kwargs",
    [
        {"threshold_days": -1},
        {"threshold_days": True},
        {"threshold_days": 1.5},
    ],
)
def test_invalid_thresholds_are_rejected(kwargs):
    with pytest.raises(ValueError):
        evaluate([obs()], **kwargs)


@pytest.mark.parametrize(
    "bad",
    [
        obs(venue="UNKNOWN"),
        obs(kind="other"),
        obs(version=123),
        obs(end=datetime(2026, 12, 31, tzinfo=UTC)),
        obs(source_claim=True),
        obs(source_claim=EvidenceClaim(True, "source")),
        obs(source_claim=EvidenceClaim("VERIFIED", 1)),
    ],
)
def test_malformed_structures_are_rejected(bad):
    with pytest.raises(ValueError):
        evaluate([bad])


def test_duplicate_venue_kind_rejected_and_no_missing_venue_is_fabricated():
    with pytest.raises(ValueError, match="duplicate"):
        evaluate([obs(), obs(version="v2")])
    (only,) = evaluate([obs("INE", "bound")])
    assert only.venue == "INE" and only.version_kind == "bound"
    assert evaluate([]) == ()


def test_fixed_offset_instant_converts_to_shanghai():
    # This is not a calculation of a trading day or a staleness alert.
    instant = datetime(2026, 12, 1, 0, 30, tzinfo=timezone(timedelta(hours=9)))
    (result,) = evaluate([obs()], instant=instant)
    assert result.shanghai_date == date(2026, 11, 30)
    assert result.days_remaining == 31 and result.status == "OK"
