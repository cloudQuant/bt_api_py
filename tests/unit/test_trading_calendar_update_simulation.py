"""Pure synthetic update decisions, with fake validators and no publication."""

from datetime import UTC, date, datetime, timedelta

import pytest

from bt_api_py.trading_calendar_update_simulation import (
    SIMULATION_ONLY,
    PriorVenue,
    SimulationConfig,
    VenueAttempt,
    VenueContent,
    VenueValidation,
    simulate_update,
)

START = date(2026, 1, 1)
END = date(2026, 12, 31)
NOW = datetime(2026, 6, 1, tzinfo=UTC)
OLD = NOW - timedelta(days=2)


def layers(mark="original", source="synthetic-source"):
    return {
        "trading_days": [{"day": "2026-06-01", "state": "open"}],
        "trading_hours": [{"session": mark}],
        "exceptions": [],
        "sources": [{"id": source, "locator": "fictional"}],
    }


def content(venue, mark="original", source="synthetic-source", start=START, end=END):
    return VenueContent.from_layers(venue, start, end, layers(mark, source))


def success(venue, value, checked=NOW, run_id="run1", conflict="CLEAR"):
    return VenueAttempt(venue, checked, "SUCCESS", conflict, value, run_id=run_id)


def failed(venue, checked=NOW, conflict="CLEAR", code="FETCH_FAILED"):
    return VenueAttempt(venue, checked, "FAILED", conflict, failure_code=code)


class FakeValidator:
    def __init__(self):
        self.calls = []
        self.reject_candidate = set()
        self.reject_retained = set()
        self.raise_candidate = set()
        self.raise_retained = set()
        self.composite = True

    def validate_venue(self, value, attempt, required_start, required_end, *, retained):
        self.calls.append((value.venue, retained))
        if value.venue in (self.raise_retained if retained else self.raise_candidate):
            raise RuntimeError("fictional failure")
        passed = value.venue not in (self.reject_retained if retained else self.reject_candidate)
        return VenueValidation(passed, passed, passed, True, passed)

    def validate_composite(self, values, required_start, required_end):
        if isinstance(self.composite, Exception):
            raise self.composite
        return self.composite


def inputs():
    a, b = content("A"), content("B")
    return {
        "mode": SIMULATION_ONLY,
        "directory_token": "calendar-root",
        "config": SimulationConfig("calendar-root", True, {}),
        "expected_venues": ("A", "B"),
        "required_start": START,
        "required_end": END,
        "prior": (PriorVenue(a, OLD), PriorVenue(b, OLD)),
        "attempts": (success("A", a), success("B", b)),
        "evaluated_at_utc": NOW,
        "validator": FakeValidator(),
    }


def report(result, venue):
    return next(row for row in result.reports if row.venue == venue)


def test_partial_success_carries_verified_old_without_refreshing_failed_time():
    args = inputs()
    args["attempts"] = (success("A", content("A", "changed")), failed("B", code="TIMEOUT"))
    result = simulate_update(**args)
    assert (result.mode, result.outcome, result.policy, result.policy_origin) == (
        SIMULATION_ONLY,
        "WOULD_PUBLISH",
        "auto",
        "default",
    )
    assert result.requires_policy_recheck
    assert [item.venue for item in result.candidate] == ["A", "B"]
    assert len(result.candidate_sha256) == 64
    a, b = report(result, "A"), report(result, "B")
    assert (a.check, a.changed_fields, a.last_success_at_utc) == (
        "SUCCESS_CHANGED",
        ("trading_hours",),
        NOW,
    )
    assert (b.check, b.reason, b.last_success_at_utc) == ("FAILED_CARRIED", "TIMEOUT", OLD)
    assert b.changed_fields == ()
    assert ("B", True) in args["validator"].calls


def test_all_unchanged_and_partial_failure_are_distinguished():
    args = inputs()
    result = simulate_update(**args)
    assert (result.outcome, result.candidate, result.candidate_sha256) == ("NO_CHANGES", None, None)
    assert all(
        row.check == "SUCCESS_UNCHANGED" and row.last_success_at_utc == NOW
        for row in result.reports
    )
    args["attempts"] = (success("A", content("A")), failed("B"))
    result = simulate_update(**args)
    assert (result.outcome, result.candidate) == ("NO_CHANGES_WITH_FAILURES", None)
    assert report(result, "B").check == "FAILED_CARRIED"
    assert report(result, "B").last_success_at_utc == OLD


@pytest.mark.parametrize("conflict", ["CONFLICT", "UNKNOWN"])
def test_conflicting_or_uncertain_failed_exchange_never_carries_previous(conflict):
    args = inputs()
    args["attempts"] = (success("A", content("A", "changed")), failed("B", conflict=conflict))
    result = simulate_update(**args)
    assert result.outcome == "BLOCKED" and result.candidate is None
    assert report(result, "B").check == "FAILED_BLOCKED"
    assert report(result, "B").reason == (
        "KNOWN_CONFLICT" if conflict == "CONFLICT" else "CONFLICT_UNCERTAIN"
    )
    assert report(result, "B").last_success_at_utc == OLD
    assert ("B", True) not in args["validator"].calls


def test_expired_old_coverage_and_missing_prior_block_with_original_failure_reason():
    args = inputs()
    args["prior"] = (args["prior"][0], PriorVenue(content("B", end=date(2026, 6, 1)), OLD))
    args["attempts"] = (success("A", content("A", "changed")), failed("B", code="TIMEOUT"))
    result = simulate_update(**args)
    assert result.outcome == "BLOCKED"
    assert (report(result, "B").reason, report(result, "B").retention_reason) == (
        "TIMEOUT",
        "PRIOR_COVERAGE_INSUFFICIENT",
    )
    args["prior"] = (args["prior"][0],)
    result = simulate_update(**args)
    assert result.outcome == "BLOCKED"
    assert report(result, "B").retention_reason == "NO_PRIOR"


def test_missing_attempt_is_not_assumed_safe_for_old_fallback():
    args = inputs()
    args["attempts"] = (success("A", content("A", "changed")),)
    result = simulate_update(**args)
    assert result.outcome == "BLOCKED"
    assert (report(result, "B").check, report(result, "B").reason) == (
        "FAILED_BLOCKED",
        "MISSING_ATTEMPT",
    )
    assert report(result, "B").last_success_at_utc == OLD


def test_fresh_complete_candidates_work_without_previous_snapshot():
    args = inputs()
    args["prior"] = ()
    result = simulate_update(**args)
    assert result.outcome == "WOULD_PUBLISH"
    assert report(result, "A").changed_fields == (
        "coverage",
        "trading_days",
        "trading_hours",
        "exceptions",
        "sources",
    )


@pytest.mark.parametrize(
    "mode,expected,origin",
    [
        ("auto", "WOULD_PUBLISH", "explicit"),
        ("manual", "NEEDS_REVIEW", "explicit"),
        (None, "WOULD_PUBLISH", "default"),
    ],
)
def test_auto_manual_and_default_config(mode, expected, origin):
    args = inputs()
    args["config"] = SimulationConfig(
        "calendar-root", True, {} if mode is None else {"review_mode": mode}
    )
    args["attempts"] = (success("A", content("A", "changed")), success("B", content("B")))
    result = simulate_update(**args)
    assert (result.outcome, result.policy_origin) == (expected, origin)
    assert result.policy == (mode or "auto")


@pytest.mark.parametrize(
    "config",
    [
        SimulationConfig("calendar-root", False, None),
        SimulationConfig("calendar-root", True, None),
        SimulationConfig("calendar-root", True, {"review_mode": None}),
        SimulationConfig("calendar-root", True, {"review_mode": "AUTO"}),
        SimulationConfig("calendar-root", True, {"review_mode": False}),
        SimulationConfig("calendar-root", True, {"unexpected": "auto"}),
        SimulationConfig("other-root", True, {}),
    ],
)
def test_bad_unavailable_or_wrong_directory_config_is_never_default_auto(config):
    args = inputs()
    args["config"] = config
    result = simulate_update(**args)
    assert result.outcome == "CONFIG_ERROR" and result.policy is None and result.candidate is None


def test_changed_source_and_coverage_are_material():
    args = inputs()
    args["attempts"] = (success("A", content("A", source="new-source")), success("B", content("B")))
    assert report(simulate_update(**args), "A").changed_fields == ("sources",)
    args["attempts"] = (
        success("A", content("A", start=date(2025, 12, 31))),
        success("B", content("B")),
    )
    assert report(simulate_update(**args), "A").changed_fields == ("coverage",)


def test_json_types_are_material_and_check_time_metadata_is_not():
    args = inputs()
    old_layers = layers()
    old_layers["trading_days"] = [{"value": 1}]
    new_layers = layers()
    new_layers["trading_days"] = [{"value": True}]
    a = VenueContent.from_layers("A", START, END, old_layers)
    a_new = VenueContent.from_layers("A", START, END, new_layers)
    args["prior"] = (PriorVenue(a, OLD), args["prior"][1])
    args["attempts"] = (success("A", a_new), success("B", content("B")))
    assert report(simulate_update(**args), "A").changed_fields == ("trading_days",)
    args["attempts"] = (
        success("A", a, checked=NOW - timedelta(hours=1), run_id="other"),
        success("B", content("B"), run_id="other"),
    )
    assert simulate_update(**args).outcome == "NO_CHANGES"
    new_layers["sources"][0]["retrieved_at_utc"] = "2026-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        VenueContent.from_layers("A", START, END, new_layers)


def test_failure_validator_and_composite_do_not_fake_approval():
    args = inputs()
    args["attempts"] = (success("A", content("A", "changed")), failed("B", code="TIMEOUT"))
    args["validator"].reject_retained.add("B")
    result = simulate_update(**args)
    assert result.outcome == "BLOCKED"
    assert (report(result, "B").reason, report(result, "B").retention_reason) == (
        "TIMEOUT",
        "RETAINED_VALIDATION_FAILED",
    )
    args["validator"].reject_retained.clear()
    args["validator"].raise_candidate.add("A")
    assert simulate_update(**args).outcome == "BLOCKED"
    args["validator"].raise_candidate.clear()
    for value in (False, None, 1, RuntimeError("composite error")):
        args["validator"].composite = value
        result = simulate_update(**args)
        assert result.outcome == "BLOCKED" and result.candidate is None
        assert result.reason in ("COMPOSITE_UNPROVEN", "VALIDATOR_ERROR")


def test_failed_candidate_can_only_carry_separately_revalidated_old_data():
    args = inputs()
    args["attempts"] = (success("A", content("A", "new")), success("B", content("B", "new")))
    args["validator"].reject_candidate.add("B")
    result = simulate_update(**args)
    assert result.outcome == "WOULD_PUBLISH"
    assert (
        report(result, "B").check,
        report(result, "B").reason,
        report(result, "B").last_success_at_utc,
    ) == (
        "FAILED_CARRIED",
        "CANDIDATE_VALIDATION_FAILED",
        OLD,
    )
    assert ("B", True) in args["validator"].calls


def test_candidate_with_insufficient_coverage_is_not_accepted_as_changed():
    args = inputs()
    args["attempts"] = (
        success("A", content("A", "changed")),
        success("B", content("B", "changed", end=date(2026, 7, 1))),
    )
    result = simulate_update(**args)
    b = report(result, "B")
    assert result.outcome == "WOULD_PUBLISH"
    assert (b.check, b.reason, b.last_success_at_utc, b.changed_fields) == (
        "FAILED_CARRIED",
        "CANDIDATE_VALIDATION_FAILED",
        OLD,
        (),
    )
    assert result.candidate[1] == content("B")


def test_validator_must_return_exact_structured_true_flags():
    args = inputs()
    args["attempts"] = (success("A", content("A", "changed")), success("B", content("B")))

    class WrongValidator(FakeValidator):
        def validate_venue(self, value, attempt, required_start, required_end, *, retained):
            return VenueValidation(True, True, True, 1, True)

    args["validator"] = WrongValidator()
    result = simulate_update(**args)
    assert result.outcome == "BLOCKED" and result.candidate is None
    assert report(result, "A").retention_reason == "CONFLICT_UNCERTAIN"


def test_build_failure_and_candidate_tampering_are_blocked():
    args = inputs()
    args["attempts"] = (success("A", content("A", "changed")), success("B", content("B")))
    args["candidate_builder"] = lambda rows: (_ for _ in ()).throw(RuntimeError("failed"))
    assert simulate_update(**args).reason == "BUILDER_ERROR"
    args["candidate_builder"] = lambda rows: rows[:1]
    assert simulate_update(**args).reason == "BUILDER_INVALID"


def test_configuration_mutation_during_validation_is_detected():
    args = inputs()
    settings = {"review_mode": "auto"}
    args["config"] = SimulationConfig("calendar-root", True, settings)
    args["attempts"] = (success("A", content("A", "changed")), success("B", content("B")))

    def mutate(rows):
        settings["review_mode"] = "manual"
        return rows

    args["candidate_builder"] = mutate
    result = simulate_update(**args)
    assert (result.outcome, result.reason, result.candidate) == (
        "CONFIG_ERROR",
        "CONFIG_CHANGED",
        None,
    )


def test_non_simulation_mode_and_bad_evidence_fail_closed():
    args = inputs()
    args["mode"] = "production"
    with pytest.raises(ValueError):
        simulate_update(**args)
    args = inputs()
    args["attempts"] = (success("A", content("A")), success("A", content("A")))
    assert simulate_update(**args).outcome == "INPUT_ERROR"
    args = inputs()
    args["attempts"] = (success("A", content("A"), checked=NOW + timedelta(seconds=1)),)
    assert simulate_update(**args).outcome == "INPUT_ERROR"
    args = inputs()
    args["evaluated_at_utc"] = NOW.replace(tzinfo=None)
    assert simulate_update(**args).outcome == "INPUT_ERROR"


def test_venue_content_is_detached_and_validated():
    data = layers()
    record = VenueContent.from_layers("A", START, END, data)
    data["trading_hours"][0]["session"] = "mutated"
    assert record == content("A")
    with pytest.raises(ValueError):
        VenueContent("A", START, END, b'{"sources":[]}')
    with pytest.raises(ValueError):
        VenueContent.from_layers("A", END, START, layers())
