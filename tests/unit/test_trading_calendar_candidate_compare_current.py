"""Synthetic read-only comparisons; no exchange facts or approvals are implied."""

import copy
import json
import subprocess
import sys
from datetime import date, timedelta

import pytest
from bt_api_ctp.calendar_supplement import parse_supplement

from bt_api_py import trading_calendar_candidate_inbox as inbox
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_candidate_inbox import ID, objects, source_checks


@pytest.fixture
def root(tmp_path):
    path = tmp_path.resolve() / "calendar"
    path.mkdir()
    return path


def content():
    return {
        "sources": [{"id": "s1", "description": "synthetic reference"}],
        "trading_days": [
            {"venue": "SHFE", "day": "2026-01-02", "status": "open", "source_ids": ["s1"]},
            {"venue": "DCE", "day": "2026-01-02", "status": "closed", "source_ids": ["s1"]},
        ],
        "trading_hours": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "rb",
                "contracts": ["rb2701"],
                "valid_from": "2026-01-01",
                "valid_to": "2026-01-31",
                "day_windows": [
                    {
                        "id": "day1",
                        "phase": "continuous",
                        "start": "09:00",
                        "end": "10:15",
                        "end_day_offset": 0,
                    },
                    {
                        "id": "day2",
                        "phase": "continuous",
                        "start": "10:30",
                        "end": "11:30",
                        "end_day_offset": 0,
                    },
                ],
                "night_windows": [
                    {
                        "id": "night1",
                        "phase": "continuous",
                        "start": "21:00",
                        "end": "23:00",
                        "end_day_offset": 0,
                    }
                ],
                "night_mappings": [
                    {"trading_day": "2026-01-02", "start_date": None, "source_ids": ["s1"]}
                ],
                "source_ids": ["s1"],
                "windows_complete": False,
                "exceptions_complete": False,
            },
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "rb",
                "contracts": ["rb2701"],
                "valid_from": "2026-02-01",
                "valid_to": "2026-02-28",
                "day_windows": [],
                "night_windows": [],
                "night_mappings": [],
                "source_ids": ["s1"],
                "windows_complete": False,
                "exceptions_complete": False,
            },
        ],
        "exceptions": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "rb",
                "contract": "rb2701",
                "trading_day": "2026-01-02",
                "action": "cancel_window",
                "window_id": "day1",
                "source_ids": ["s1"],
            }
        ],
    }


def absent_checks():
    absent = {"status": "NOT_REQUESTED", "reason": "NOT_REQUESTED", "attempts": []}
    return {
        "hours": absent,
        "holiday": absent,
        "holiday_conversion_status": "NOT_ATTEMPTED",
        "merge_status": "NOT_ATTEMPTED",
        "merge_reason": "NONE",
        "normalization_status": "NOT_ATTEMPTED",
    }


def candidate(root, candidate_content, candidate_id=ID):
    supplement = parse_supplement(json.dumps(candidate_content).encode())
    result = inbox.retain_candidate(
        root=root,
        candidate_id=candidate_id,
        merged=None,
        supplement=supplement,
        hours_page=None,
        source_checks=absent_checks(),
        filesystem=inbox.TRUSTED_LOCAL_POSIX,
    )
    assert result.status == "COMMITTED"
    return result


def current(root, baseline, number=0):
    result = FixtureCalendarStore(root).publish(
        baseline, publication_id=f"pub{number}", version_id=f"v{number}"
    )
    assert result.visibility == "COMMITTED"
    return result


def compare(root, candidate_id=ID):
    return inbox.compare_candidate_with_current(
        root=root, candidate_id=candidate_id, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )


def test_partial_comparison_never_infers_deletions_or_absent_night(root):
    baseline = content()
    partial = copy.deepcopy(baseline)
    partial["trading_days"] = partial["trading_days"][:1]
    partial["trading_hours"] = partial["trading_hours"][:1]
    partial["trading_hours"][0]["day_windows"] = partial["trading_hours"][0]["day_windows"][:1]
    partial["trading_hours"][0]["night_windows"] = []
    partial["trading_hours"][0]["night_mappings"] = []
    partial["exceptions"] = []
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["current_only"] == result["overall_change"] == "NOT_ASSESSED"
    assert result["approval"] == "NOT_PERFORMED" and result["publication"] == "NOT_AUTHORIZED"
    assert result["baseline_row_counts"]["trading_days"] == 2
    assert result["candidate_row_counts"]["trading_days"] == 1
    assert result["comparison"]["trading_days"][0]["relation"] == "SAME_DECLARATION"
    hour = result["comparison"]["trading_hours"][0]
    assert hour["relation"] == "MATCHED_SCOPE"
    assert (
        len(hour["day_windows"]) == 1 and hour["day_windows"][0]["relation"] == "SAME_DECLARATION"
    )
    assert hour["night_windows"] == [] and hour["night_mappings"] == []
    assert hour["omitted_windows_and_mappings"] == "NOT_ASSESSED"
    assert result["unresolved_nights"] == {"status": "UNKNOWN", "items": None}
    assert result["comparison"]["exceptions"] == []
    assert "DELETE" not in json.dumps(result).upper()


def test_declared_values_and_explicit_null_night_mapping_are_not_certified(root):
    baseline = content()
    partial = copy.deepcopy(baseline)
    partial["trading_days"][1]["status"] = "open"
    partial["trading_hours"] = partial["trading_hours"][:1]
    partial["trading_hours"][0]["day_windows"][0]["end"] = "10:00"
    # The baseline explicitly maps this date to a prior civil night. Candidate
    # explicitly says null, which remains only an unverified declaration.
    baseline["trading_hours"][0]["night_mappings"][0]["start_date"] = "2025-12-31"
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["comparison"]["trading_days"][0]["relation"] == "DIFFERENT_DECLARATION"
    hour = result["comparison"]["trading_hours"][0]
    assert hour["day_windows"][0]["relation"] == "DIFFERENT_DECLARATION"
    mapping = hour["night_mappings"][0]
    assert mapping["relation"] == "DIFFERENT_DECLARATION"
    assert (
        mapping["candidate_start_date"] is None and mapping["baseline_start_date"] == "2025-12-31"
    )
    assert mapping["interpretation"] == "DECLARATION_ONLY_NOT_VERIFIED"


def test_source_only_changes_do_not_become_business_differences(root):
    baseline = content()
    partial = copy.deepcopy(baseline)
    partial["sources"] = [{"id": "s2", "description": "new page digest"}]
    for layer in ("trading_days", "trading_hours", "exceptions"):
        for row in partial[layer]:
            row["source_ids"] = ["s2"]
            if layer == "trading_hours":
                for mapping in row["night_mappings"]:
                    mapping["source_ids"] = ["s2"]
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert all(
        row["relation"] == "SAME_DECLARATION" for row in result["comparison"]["trading_days"]
    )
    assert result["comparison"]["trading_days"][0]["source_relation"] == "DIFFERENT_UNVERIFIED"
    assert (
        result["comparison"]["source_clues"][0]["relation"]
        == "CANDIDATE_SOURCE_OBSERVATION_UNVERIFIED"
    )
    assert result["overall_change"] == "NOT_ASSESSED"


@pytest.mark.parametrize("change", ["period", "contracts", "exception", "window_overlap"])
def test_ambiguous_overlapping_scope_returns_unknown(root, change):
    baseline = content()
    partial = copy.deepcopy(baseline)
    if change == "period":
        partial["trading_hours"] = partial["trading_hours"][:1]
        partial["trading_hours"][0]["valid_to"] = "2026-01-20"
    elif change == "contracts":
        partial["trading_hours"] = partial["trading_hours"][:1]
        partial["trading_hours"][0]["contracts"].append("rb2702")
    elif change == "exception":
        partial["exceptions"][0]["action"] = "close_day"
        partial["exceptions"][0]["window_id"] = None
    else:
        partial["trading_hours"][0]["day_windows"][0]["id"] = "other"
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and result["reason"] == "UNSUPPORTED_OR_AMBIGUOUS"
    assert "comparison" not in result


@pytest.mark.parametrize("session", ["day_windows", "night_windows"])
@pytest.mark.parametrize("case", ["overlap_sibling", "valid_same_id", "new_nonoverlap"])
def test_window_comparison_checks_other_baseline_windows_even_for_same_id(root, session, case):
    baseline = content()
    baseline_hours = baseline["trading_hours"][0]
    if session == "day_windows":
        first = baseline_hours[session][0]
        if case == "overlap_sibling":
            window = {**first, "start": "10:45", "end": "11:00"}
        elif case == "valid_same_id":
            window = {**first, "start": "09:10", "end": "10:10"}
        else:
            window = {**first, "id": "day3", "start": "11:40", "end": "12:00"}
    else:
        baseline_hours[session][0]["end"] = "22:00"
        baseline_hours[session].append(
            {
                "id": "night2",
                "phase": "continuous",
                "start": "22:30",
                "end": "23:30",
                "end_day_offset": 0,
            }
        )
        first = baseline_hours[session][0]
        if case == "overlap_sibling":
            window = {**first, "start": "22:45", "end": "23:00"}
        elif case == "valid_same_id":
            window = {**first, "start": "21:10", "end": "21:50"}
        else:
            window = {**first, "id": "night3", "start": "23:40", "end": "23:50"}
    partial = copy.deepcopy(baseline)
    partial["trading_hours"][0][session] = [window]
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    if case == "overlap_sibling":
        assert result["status"] == "UNKNOWN" and "comparison" not in result
    else:
        assert result["status"] == "PARTIAL"
        rows = result["comparison"]["trading_hours"][0][session]
        expected = "DIFFERENT_DECLARATION" if case == "valid_same_id" else "CANDIDATE_OBSERVATION"
        assert rows[0]["relation"] == expected


@pytest.mark.parametrize("conflicting_side", ["candidate", "baseline"])
def test_explicit_same_trading_day_cross_midnight_overlap_is_unknown(root, conflicting_side):
    baseline, partial = content(), content()
    conflicting = partial if conflicting_side == "candidate" else baseline
    hour = conflicting["trading_hours"][0]
    hour["night_windows"][0].update(start="23:30", end="09:30", end_day_offset=1)
    hour["night_mappings"][0]["start_date"] = "2026-01-01"
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and "comparison" not in result


@pytest.mark.parametrize("conflicting_side", ["candidate", "baseline"])
def test_explicit_adjacent_trading_days_overlap_is_unknown(root, conflicting_side):
    baseline, partial = content(), content()
    conflicting = partial if conflicting_side == "candidate" else baseline
    conflicting["trading_days"].append(
        {"venue": "SHFE", "day": "2026-01-03", "status": "open", "source_ids": ["s1"]}
    )
    hour = conflicting["trading_hours"][0]
    hour["day_windows"].append(
        {"id": "day3", "phase": "continuous", "start": "13:30", "end": "15:00", "end_day_offset": 0}
    )
    hour["night_windows"][0].update(start="14:00", end="16:00")
    hour["night_mappings"][0]["start_date"] = "2026-01-01"
    hour["night_mappings"].append(
        {"trading_day": "2026-01-03", "start_date": "2026-01-02", "source_ids": ["s1"]}
    )
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and "comparison" not in result


def test_missing_night_mapping_is_not_assessed_even_when_day_windows_do_not_overlap(root):
    baseline = content()
    baseline["trading_hours"][0]["night_mappings"] = []
    partial = copy.deepcopy(baseline)
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["explicit_window_check"]["missing_mapping"] == "NOT_ASSESSED"
    assert result["explicit_window_check"]["uncovered_dates"] == "NOT_ASSESSED"
    assert result["explicit_window_check"]["source_authentication"] == "NOT_PROVEN"


@pytest.mark.parametrize("direction", ["candidate_day", "candidate_night"])
def test_candidate_and_baseline_other_utc_windows_must_not_overlap(root, direction):
    baseline, partial = content(), content()
    baseline["exceptions"] = []
    partial["exceptions"] = []
    if direction == "candidate_day":
        b_hour, c_hour = baseline["trading_hours"][0], partial["trading_hours"][0]
    else:
        b_hour, c_hour = partial["trading_hours"][0], baseline["trading_hours"][0]
    b_hour["night_windows"][0].update(start="23:30", end="00:45", end_day_offset=1)
    b_hour["night_mappings"][0]["start_date"] = "2026-01-01"
    c_hour["night_windows"] = []
    c_hour["night_mappings"] = []
    c_hour["day_windows"] = [{**c_hour["day_windows"][0], "start": "00:30", "end": "01:00"}]
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and "comparison" not in result


@pytest.mark.parametrize("case", ["baseline_cancel", "candidate_cancel", "missing_mapping"])
def test_cross_input_explicit_cancellation_and_missing_mapping_are_not_inferred(root, case):
    baseline, partial = content(), content()
    baseline["exceptions"] = []
    partial["exceptions"] = []
    b_hour, c_hour = baseline["trading_hours"][0], partial["trading_hours"][0]
    b_hour["night_windows"][0].update(start="23:30", end="00:45", end_day_offset=1)
    b_hour["night_mappings"][0]["start_date"] = "2026-01-01"
    c_hour["night_windows"] = []
    c_hour["night_mappings"] = []
    c_hour["day_windows"] = [{**c_hour["day_windows"][0], "start": "00:30", "end": "01:00"}]
    exception = {
        "venue": "SHFE",
        "kind": "future",
        "product": "rb",
        "contract": "rb2701",
        "trading_day": "2026-01-02",
        "action": "cancel_window",
        "source_ids": ["s1"],
    }
    if case == "baseline_cancel":
        baseline["exceptions"] = [{**exception, "window_id": "night1"}]
    elif case == "candidate_cancel":
        partial["exceptions"] = [{**exception, "window_id": "day1"}]
    else:
        b_hour["night_mappings"] = []
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["explicit_window_check"]["missing_mapping"] == "NOT_ASSESSED"
    if case == "missing_mapping":
        assert result["explicit_window_check"]["baseline"]["missing_mapping_count"] > 0


def test_different_nonoverlapping_period_is_candidate_observation(root):
    baseline = content()
    partial = copy.deepcopy(baseline)
    partial["trading_hours"] = [partial["trading_hours"][1]]
    partial["trading_hours"][0]["valid_from"] = "2026-03-01"
    partial["trading_hours"][0]["valid_to"] = "2026-03-31"
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["comparison"]["trading_hours"][0]["relation"] == "CANDIDATE_OBSERVATION"


def test_exact_exception_match_does_not_infer_omitted_sibling_deletion(root):
    baseline = content()
    sibling = {**baseline["exceptions"][0], "window_id": "day2"}
    baseline["exceptions"].append(sibling)
    partial = content()
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL" and result["current_only"] == "NOT_ASSESSED"
    assert len(result["comparison"]["exceptions"]) == 1
    assert result["comparison"]["exceptions"][0]["relation"] == "SAME_DECLARATION"


@pytest.mark.parametrize(
    "candidate_contract,baseline_contract", [("rb2701", None), (None, "rb2701")]
)
def test_different_cancel_window_ids_are_independent_even_with_wildcard_scope(
    root, candidate_contract, baseline_contract
):
    baseline, partial = content(), content()
    baseline["exceptions"][0].update(contract=baseline_contract, window_id="day2")
    partial["exceptions"][0].update(contract=candidate_contract, window_id="day1")
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["comparison"]["exceptions"][0]["relation"] == "CANDIDATE_OBSERVATION"
    assert result["current_only"] == "NOT_ASSESSED"


@pytest.mark.parametrize("baseline_action", ["close_day", "cancel_window"])
def test_wildcard_exception_close_or_same_window_overlap_is_unknown(root, baseline_action):
    baseline, partial = content(), content()
    baseline["exceptions"][0].update(
        contract=None,
        action=baseline_action,
        window_id=None if baseline_action == "close_day" else "day1",
    )
    candidate(root, partial)
    current(root, baseline)
    assert compare(root)["status"] == "UNKNOWN"


@pytest.mark.parametrize("missing", ["date", "hours", "night_mapping"])
def test_exception_assessment_exposes_missing_context_without_claiming_failure(root, missing):
    baseline = content()
    baseline["exceptions"][0]["window_id"] = "night1"
    if missing == "date":
        baseline["trading_days"] = [
            row for row in baseline["trading_days"] if row["venue"] != "SHFE"
        ]
    elif missing == "hours":
        baseline["trading_hours"] = []
    else:
        baseline["trading_hours"][0]["night_mappings"] = []
    candidate(root, baseline)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    for side in ("candidate", "baseline"):
        check = result["explicit_window_check"][side]
        assert check["declared_exception_count"] == 1
        assert check["exception_scope_coverage"] == "NOT_PROVEN"
        item = check["exception_assessments"][0]
        assert item["scope_coverage"] == item["window_fact"] == "NOT_PROVEN"
        if missing in {"date", "hours"}:
            assert check["assessed_at_least_one_scope_day_count"] == 0
            assert check["unassessed_exception_count"] == 1
            assert item["scope_day_match"] == "NOT_ASSESSED"
            assert item["not_seen_in_declared_windows_scope_day_count"] == 0
        else:
            assert check["assessed_at_least_one_scope_day_count"] == 1
            assert check["unassessed_exception_count"] == 0
            assert item["scope_day_match"] == "MATCHED_AT_LEAST_ONE"
            assert check["missing_mapping_count"] > 0
            assert item["not_seen_in_declared_windows_scope_day_count"] == 1
            assert check["cancel_window_not_seen_in_declared_windows_scope_day_count"] == 1


def test_global_exception_one_known_contract_does_not_prove_other_contract_coverage(root):
    baseline = content()
    baseline["exceptions"][0].update(contract=None, window_id="night1")
    baseline["trading_hours"][0]["night_mappings"] = []
    other = copy.deepcopy(baseline["trading_hours"][0])
    other["contracts"] = ["rb2702"]
    baseline["trading_hours"].append(other)
    partial = content()
    partial["exceptions"][0].update(contract=None, window_id="night1")
    partial["trading_hours"][0]["night_mappings"] = []
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    for side, hits in (("candidate", 1), ("baseline", 2)):
        check = result["explicit_window_check"][side]
        assert (
            check["declared_exception_count"],
            check["assessed_at_least_one_scope_day_count"],
            check["unassessed_exception_count"],
        ) == (1, 1, 0)
        item = check["exception_assessments"][0]
        assert item["matched_scope_day_count"] == hits
        assert item["not_seen_in_declared_windows_scope_day_count"] == hits
        assert check["cancel_window_not_seen_in_declared_windows_scope_day_count"] == hits
        assert item["scope_coverage"] == item["window_fact"] == "NOT_PROVEN"
        assert check["exception_scope_coverage"] == "NOT_PROVEN"


def test_unresolved_nights_are_carried_without_certification(root):
    hours, _, merged = objects()
    assert (
        inbox.retain_candidate(
            root=root,
            candidate_id=ID,
            merged=merged,
            hours_page=hours,
            source_checks=source_checks(),
            filesystem=inbox.TRUSTED_LOCAL_POSIX,
        ).status
        == "COMMITTED"
    )
    current(root, merged.supplement.content)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["unresolved_nights"]["status"] == "UNVERIFIED_REVIEW_REQUIRED"
    assert len(result["unresolved_nights"]["items"]) == 7
    assert result["unresolved_nights"]["items"][0]["raw_text"]
    assert result["hours_page_status"] == "CAPTURED_UNVERIFIED"


def test_reordered_candidates_and_changing_current_keep_exact_baseline_identity(root):
    baseline = content()
    partial = copy.deepcopy(baseline)
    for key in partial:
        partial[key].reverse()
    candidate(root, partial)
    current(root, baseline)
    first = compare(root)
    assert first["baseline"]["publication_id"] == "pub0"
    baseline["trading_days"][1]["status"] = "open"
    current(root, baseline, 1)
    second = compare(root)
    assert second["status"] == "PARTIAL" and second["baseline"]["publication_id"] == "pub1"
    assert first["baseline"]["content_sha256"] != second["baseline"]["content_sha256"]
    assert first["baseline"]["currentness"] == "OBSERVED_UNDER_LOCK_NOT_REVALIDATED"


def test_six_venue_two_year_baseline_is_scanned_not_truncated(root):
    venues = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
    first = date(2026, 1, 1)
    baseline = {
        "sources": [{"id": "s1", "description": "synthetic"}],
        "trading_days": [],
        "trading_hours": [],
        "exceptions": [],
    }
    for venue in venues:
        for number in range(731):
            baseline["trading_days"].append(
                {
                    "venue": venue,
                    "day": (first + timedelta(days=number)).isoformat(),
                    "status": "closed",
                    "source_ids": ["s1"],
                }
            )
    partial = {
        "sources": baseline["sources"],
        "trading_days": [baseline["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["baseline_row_counts"]["trading_days"] == 4386
    assert result["candidate_row_counts"]["trading_days"] == 1
    assert result["current_only"] == "NOT_ASSESSED"


def test_six_venues_21_scopes_with_open_day_night_mappings_are_bounded(root):
    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    venues = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
    first = date(2026, 1, 1)
    days = [(first + timedelta(days=number)).isoformat() for number in range(731)]
    open_days = [day for day in days if date.fromisoformat(day).weekday() < 5]
    assert len(open_days) == 522
    mappings = [
        {
            "trading_day": day,
            "start_date": (date.fromisoformat(day) - timedelta(days=1)).isoformat(),
            "source_ids": ["s1"],
        }
        for day in open_days
    ]
    baseline = {
        "sources": [{"id": "s1", "description": "synthetic"}],
        "trading_days": [],
        "trading_hours": [],
        "exceptions": [],
    }
    for venue in venues:
        baseline["trading_days"].extend(
            {
                "venue": venue,
                "day": day,
                "status": "open" if day in open_days else "closed",
                "source_ids": ["s1"],
            }
            for day in days
        )
        for index in range(21):
            baseline["trading_hours"].append(
                {
                    "venue": venue,
                    "kind": "future",
                    "product": f"p{index}",
                    "contracts": [f"c{index}"],
                    "valid_from": days[0],
                    "valid_to": days[-1],
                    "day_windows": [
                        {
                            "id": "day1",
                            "phase": "continuous",
                            "start": "09:00",
                            "end": "10:15",
                            "end_day_offset": 0,
                        }
                    ],
                    "night_windows": [
                        {
                            "id": "night1",
                            "phase": "continuous",
                            "start": "21:00",
                            "end": "23:00",
                            "end_day_offset": 0,
                        }
                    ],
                    "night_mappings": copy.deepcopy(mappings),
                    "source_ids": ["s1"],
                    "windows_complete": False,
                    "exceptions_complete": False,
                }
            )
    first_venue = {
        "sources": baseline["sources"],
        "trading_days": [row for row in baseline["trading_days"] if row["venue"] == "SHFE"],
        "trading_hours": [row for row in baseline["trading_hours"] if row["venue"] == "SHFE"],
        "exceptions": [],
    }
    CalendarSnapshot(first_venue, calendar_ref="synthetic")
    partial = {
        "sources": baseline["sources"],
        "trading_days": [baseline["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "PARTIAL"
    assert result["baseline_row_counts"]["trading_days"] == 4386
    assert result["baseline_row_counts"]["trading_hours"] == 126
    assert result["explicit_window_check"]["baseline"]["scope_days_checked"] == 6 * 21 * 522
    assert result["explicit_window_check"]["coverage"] == "NOT_ASSESSED"


def test_over_budget_and_corrupt_or_missing_current_return_unknown(root):
    partial = content()
    candidate(root, partial)
    assert compare(root)["reason"] == "BASELINE_UNAVAILABLE"
    first = date(2026, 1, 1)
    baseline = {
        "sources": [{"id": "s1", "description": "synthetic"}],
        "trading_days": [
            {
                "venue": "SHFE",
                "day": (first + timedelta(days=i)).isoformat(),
                "status": "closed",
                "source_ids": ["s1"],
            }
            for i in range(732)
        ],
        "trading_hours": [],
        "exceptions": [],
    }
    current(root, baseline)
    assert compare(root)["reason"] == "BASELINE_UNSUPPORTED_OR_TOO_LARGE"
    (root / "CURRENT.json").write_text("{}")
    assert compare(root)["reason"] == "BASELINE_UNAVAILABLE"


def test_candidate_tamper_and_symlink_root_rejected(root, tmp_path):
    candidate(root, content())
    current(root, content())
    link = tmp_path.resolve() / "link"
    link.symlink_to(root, target_is_directory=True)
    assert compare(link)["status"] == "UNKNOWN"
    path = root / "inbox" / f"candidate-{ID}.json"
    path.write_text("{}")
    assert compare(root)["reason"] == "CANDIDATE_UNAVAILABLE"


def test_ambiguous_duplicate_baseline_day_is_unknown(root):
    baseline = content()
    baseline["trading_days"].append({**baseline["trading_days"][0], "status": "closed"})
    candidate(root, content())
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and result["reason"] == "BASELINE_UNSUPPORTED_OR_TOO_LARGE"
    assert "comparison" not in result


@pytest.mark.parametrize(
    "damage", ["source", "day", "owner", "period", "exception", "window", "mapping"]
)
def test_global_baseline_invariants_survive_scope_batching(root, damage):
    baseline = content()
    if damage == "source":
        baseline["sources"].append(dict(baseline["sources"][0]))
    elif damage == "day":
        baseline["trading_days"].append(dict(baseline["trading_days"][0]))
    elif damage == "owner":
        # Same contract assigned to different products in nonoverlapping periods.
        baseline["trading_hours"][1]["product"] = "ag"
    elif damage == "period":
        baseline["trading_hours"][1]["valid_from"] = "2026-01-15"
    elif damage == "exception":
        baseline["exceptions"].append(dict(baseline["exceptions"][0]))
    elif damage == "window":
        baseline["trading_hours"][0]["day_windows"].append(
            dict(baseline["trading_hours"][0]["day_windows"][0])
        )
    else:
        baseline["trading_hours"][0]["night_mappings"].append(
            dict(baseline["trading_hours"][0]["night_mappings"][0])
        )
    # Candidate deliberately names no SHFE hours; detecting a bad baseline
    # must not depend on cross-input scope overlap.
    partial = {
        "sources": [{"id": "s1", "description": "synthetic reference"}],
        "trading_days": [
            {"venue": "DCE", "day": "2026-01-02", "status": "closed", "source_ids": ["s1"]}
        ],
        "trading_hours": [],
        "exceptions": [],
    }
    candidate(root, partial)
    current(root, baseline)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and "comparison" not in result


def test_explicit_cli_compare_does_not_expose_paths_or_change_current(root):
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(content()))
    preview_command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_preview",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "20",
        "--retain-candidate",
        "--trusted-local-posix",
    ]
    created = subprocess.run(
        preview_command, capture_output=True, text=True, timeout=30, check=False
    )
    assert created.returncode == 10, created.stderr
    report = json.loads(created.stdout)
    assert report["candidate_status"] == "COMMITTED"
    current(root, content())
    before = (root / "CURRENT.json").read_bytes()
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_candidate_inbox",
        "show",
        "--calendar-root",
        str(root),
        "--candidate-id",
        report["candidate_id"],
        "--trusted-local-posix",
        "--against-current",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == "PARTIAL" and body["baseline"]["publication_id"] == "pub0"
    assert (
        body["baseline"]["reference_status"] == "CURRENT_WITH_CONSISTENT_UNAUTHENTICATED_RECEIPTS"
    )
    assert body["baseline"]["publication_proof"] == "NOT_PROVEN"
    assert body["baseline"]["receipt_consistency"] == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert str(root) not in result.stdout and (root / "CURRENT.json").read_bytes() == before


def test_pending_or_missing_receipt_never_becomes_publication_proof(root):
    candidate(root, content())
    current(root, content())
    (root / "receipts" / "pub0.json").unlink()
    pending = root / "PENDING.json"
    pending.write_bytes(b"unverified-pending")
    result = compare(root)
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "BASELINE_UNAVAILABLE"
    assert pending.read_bytes() == b"unverified-pending"


def test_output_limit_rejects_without_returning_partial_rows(root, monkeypatch):
    candidate(root, content())
    current(root, content())
    monkeypatch.setattr(inbox, "_COMPARE_OUTPUT_LIMIT", 32)
    result = compare(root)
    assert result["status"] == "UNKNOWN" and result["reason"] == "OUTPUT_LIMIT"
    assert "comparison" not in result
