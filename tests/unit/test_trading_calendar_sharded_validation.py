"""Six-venue tests use fictional local data and confer no calendar authority."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

import pytest
from bt_api_ctp.calendar_candidate_coverage import CoverageRequirement
from bt_api_ctp.calendar_supplement import parse_supplement

from bt_api_py.trading_calendar_composite import SIMULATION_ONLY
from bt_api_py.trading_calendar_sharded_validation import VenueShard, validate_and_compose_simulated

VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
START = date(2026, 1, 1)


def make_shards(days=2, *, missing=None, conflict=False):
    end = START + timedelta(days=days - 1)
    result = []
    for venue in VENUES:
        source_id = "shared.synthetic"
        content = {
            "sources": [
                {
                    "id": source_id,
                    "description": "fictional other"
                    if conflict and venue == "DCE"
                    else "fictional",
                }
            ],
            "trading_days": [
                {
                    "venue": venue,
                    "day": (START + timedelta(days=n)).isoformat(),
                    "status": "closed",
                    "source_ids": [source_id],
                }
                for n in range(days)
                if (venue, n) != missing
            ],
            "trading_hours": [],
            "exceptions": [],
        }
        reqs = []
        for kind in ("future", "option"):
            product, contract = f"synthetic{kind}", f"{venue.lower()}{kind}2701"
            content["trading_hours"].append(
                {
                    "venue": venue,
                    "kind": kind,
                    "product": product,
                    "contracts": [contract],
                    "valid_from": START.isoformat(),
                    "valid_to": end.isoformat(),
                    "day_windows": [
                        {
                            "id": "day",
                            "phase": "continuous",
                            "start": "09:00",
                            "end": "10:00",
                            "end_day_offset": 0,
                        }
                    ],
                    "night_windows": [],
                    "night_mappings": [],
                    "windows_complete": True,
                    "exceptions_complete": True,
                    "source_ids": [source_id],
                }
            )
            reqs.append(CoverageRequirement(venue, kind, product, contract, START, end))
        candidate = parse_supplement(json.dumps(content).encode())
        result.append(VenueShard(venue, START, end, candidate, tuple(reqs)))
    return tuple(result)


def execute(shards, mode=SIMULATION_ONLY):
    return validate_and_compose_simulated(mode=mode, shards=shards)


def assert_no_bundle(result):
    assert result.composite is None
    assert (
        result.mode,
        result.verification,
        result.coverage,
        result.source_authentication,
        result.utc_coverage,
        result.publication,
    ) == (
        "SIMULATION_ONLY",
        "UNVERIFIED",
        "CALLER_DECLARED_SCOPE_ONLY",
        "NOT_AUTHENTICATED",
        "NOT_PROVEN",
        "NOT_AUTHORIZED",
    )


def test_real_small_shards_are_validated_without_mutating_inputs():
    shards = make_shards()
    before = deepcopy(shards)
    result = execute(shards)
    assert result.status == "READY_SIMULATED" and shards == before
    assert len(result.shards) == 6 and all(
        s.status == "STRUCTURAL_COMPLETE_UNVERIFIED" and s.requested_days == s.checked_days == 4
        for s in result.shards
    )
    assert result.coverage == "CALLER_DECLARED_SCOPE_ONLY"
    assert result.manifest_coverage == "CALLER_DECLARED_RANGE_NOT_FULLY_CHECKED"
    bundle = result.composite
    assert bundle.mode == SIMULATION_ONLY and bundle.source_evidence == "UNVERIFIED"
    assert (
        bundle.snapshot_bytes
        and bundle.snapshot_sha256
        and bundle.manifest_bytes
        and bundle.manifest_sha256
    )
    assert bundle.binding_bytes and bundle.binding_sha256
    assert len(json.loads(bundle.snapshot_bytes)["trading_days"]) == 12
    saved = bundle.snapshot_bytes
    shards[0].candidate.content["trading_days"].clear()
    assert bundle.snapshot_bytes == saved and len(json.loads(saved)["trading_days"]) == 12
    evidence = next(item for item in result.shards if item.venue == shards[0].venue)
    object.__setattr__(shards[0].requirements[0], "end_day", date(2030, 1, 1))
    assert evidence.requirements[0].end_day == START + timedelta(days=1)


def test_six_two_year_synthetic_load_uses_validated_shards():
    shards = make_shards(days=730)
    assert all(len(shard.candidate.content["trading_days"]) == 730 for shard in shards)
    result = execute(shards)
    assert result.status == "READY_SIMULATED" and len(result.shards) == 6
    assert all(
        item.checked_days == item.requested_days == 1460 and not item.gaps for item in result.shards
    )
    assert len(json.loads(result.composite.snapshot_bytes)["trading_days"]) == 4380
    assert len(json.loads(result.composite.manifest_bytes)["coverage"]) == 6


def test_gap_blocks_composition_and_exposes_only_unverified_check(monkeypatch):
    import bt_api_py.trading_calendar_sharded_validation as module

    def forbidden(**kwargs):
        raise AssertionError("composition is not allowed")

    monkeypatch.setattr(module, "compose_simulated", forbidden)
    result = execute(make_shards(missing=("DCE", 1)))
    assert result.status == "CHECK_FAILED" and result.reason == "VENUE_DATE_RECORDS_INCOMPLETE"
    assert_no_bundle(result)
    assert result.venue_date_layer_status == "NOT_PROVEN"


def test_conflicting_cross_venue_sources_never_return_artifacts():
    result = execute(make_shards(conflict=True))
    assert result.status == "COMPOSITE_REJECTED" and result.reason == "SOURCE_CONFLICT"
    assert len(result.shards) == 6
    assert_no_bundle(result)


def test_unchecked_missing_date_inside_declared_range_blocks_composition():
    shards = list(make_shards(days=3, missing=("SHFE", 2)))
    target = shards[0]
    narrower = tuple(replace(req, end_day=START) for req in target.requirements)
    shards[0] = replace(target, requirements=narrower)
    result = execute(tuple(shards))
    assert (result.status, result.reason) == ("CHECK_FAILED", "VENUE_DATE_RECORDS_INCOMPLETE")
    assert_no_bundle(result)


def test_complete_date_range_does_not_prove_other_scopes_or_utc():
    shards = list(make_shards(days=3))
    target = shards[0]
    narrower = tuple(replace(req, end_day=START) for req in target.requirements)
    shards[0] = replace(target, requirements=narrower)
    result = execute(tuple(shards))
    assert result.status == "READY_SIMULATED"
    assert result.coverage == "CALLER_DECLARED_SCOPE_ONLY"
    assert result.manifest_coverage == "CALLER_DECLARED_RANGE_NOT_FULLY_CHECKED"
    assert result.venue_date_layer_status == "EXPLICIT_DATES_STRUCTURALLY_COMPLETE_UNVERIFIED"
    assert (
        result.utc_coverage == "NOT_PROVEN" and result.source_authentication == "NOT_AUTHENTICATED"
    )
    checked = next(item for item in result.shards if item.venue == "SHFE")
    assert checked.coverage_end == START + timedelta(days=2) and checked.checked_days == 2
    assert all(req.end_day == START for req in checked.requirements)


def test_valid_records_outside_declared_range_are_retained():
    shards = list(make_shards(days=3))
    target = shards[0]
    narrowed = tuple(replace(req, end_day=START + timedelta(days=1)) for req in target.requirements)
    shards[0] = replace(target, coverage_end=START + timedelta(days=1), requirements=narrowed)
    result = execute(tuple(shards))
    assert result.status == "READY_SIMULATED"
    rows = json.loads(result.composite.snapshot_bytes)["trading_days"]
    assert any(
        row["venue"] == "SHFE" and row["day"] == (START + timedelta(days=2)).isoformat()
        for row in rows
    )
    coverage = json.loads(result.composite.manifest_bytes)["coverage"]
    shfe = next(row for row in coverage if row["venue"] == "SHFE")
    assert shfe["coverage_end"] == (START + timedelta(days=1)).isoformat()


def test_declared_range_731_limit_and_duplicate_date():
    shards = list(make_shards())
    shards[0] = replace(shards[0], coverage_end=START + timedelta(days=731))
    result = execute(tuple(shards))
    assert result.status == "INVALID_INPUT"
    assert_no_bundle(result)
    shards = list(make_shards())
    shards[0].candidate.content["trading_days"].append(
        deepcopy(shards[0].candidate.content["trading_days"][0])
    )
    result = execute(tuple(shards))
    assert (result.status, result.reason) == ("INVALID_INPUT", "INVALID_SHARDS_OR_SCOPE")
    assert_no_bundle(result)


def test_731_day_declared_range_is_allowed():
    shards = list(make_shards())
    shards[0] = make_shards(days=731)[0]
    result = execute(tuple(shards))
    assert (
        result.status == "READY_SIMULATED"
        and result.venue_date_layer_status == "EXPLICIT_DATES_STRUCTURALLY_COMPLETE_UNVERIFIED"
    )


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate",
        "requirements",
        "too_many_requirements",
        "outside",
        "bad_date",
        "wrong_venue",
        "bad_candidate",
        "mismatched_content",
        "overlap",
        "wrong_scope",
    ],
)
def test_invalid_or_unknown_scope_never_produces_bundle(kind):
    shards = list(make_shards())
    first = shards[0]
    if kind == "missing":
        shards.pop()
    elif kind == "duplicate":
        shards[-1] = shards[0]
    elif kind == "requirements":
        shards[0] = replace(first, requirements=())
    elif kind == "too_many_requirements":
        shards[0] = replace(first, requirements=first.requirements * 33)
    elif kind == "outside":
        shards[0] = replace(
            first,
            requirements=(replace(first.requirements[0], start_day=START - timedelta(days=1)),),
        )
    elif kind == "bad_date":
        shards[0] = replace(first, coverage_start="2026-01-01")
    elif kind == "wrong_venue":
        shards[0] = replace(first, requirements=(replace(first.requirements[0], venue="DCE"),))
    elif kind == "bad_candidate":
        shards[0] = replace(first, candidate=None)
    elif kind == "mismatched_content":
        first.candidate.content["trading_days"][0]["venue"] = "DCE"
    elif kind == "overlap":
        shards[0] = replace(first, requirements=(first.requirements[0], first.requirements[0]))
    else:
        shards[0] = replace(
            first, requirements=(replace(first.requirements[0], contract="unknown"),)
        )
    result = execute(tuple(shards))
    assert result.status in {"INVALID_INPUT", "CHECK_FAILED"}
    assert_no_bundle(result)


def test_mode_is_explicit_and_failures_do_not_return_bytes():
    result = execute(make_shards(), mode="PRODUCTION")
    assert result.status == "INVALID_INPUT" and result.reason == "SIMULATION_MODE_REQUIRED"
    assert_no_bundle(result)


def test_original_mutation_after_check_cannot_enter_composition(monkeypatch):
    from bt_api_ctp import calendar_scope_batches as module

    shards = make_shards()
    original = module.check_scope_batches
    changed = []

    def check_and_change(candidate, *, requirements):
        result = original(candidate, requirements=requirements)
        if not changed:
            venue = requirements[0].venue
            target = next(shard for shard in shards if shard.venue == venue)
            target.candidate.content["trading_days"].clear()
            changed.append(venue)
        return result

    monkeypatch.setattr(module, "check_scope_batches", check_and_change)
    result = execute(shards)
    assert changed and result.status == "READY_SIMULATED"
    rows = json.loads(result.composite.snapshot_bytes)["trading_days"]
    assert sum(row["venue"] == changed[0] for row in rows) == 2


def test_dependency_or_checker_failure_never_exposes_composite(monkeypatch):
    import builtins

    from bt_api_ctp import calendar_scope_batches as module

    shards = make_shards()
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "bt_api_ctp.calendar_candidate_coverage":
            raise ImportError("private details")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", unavailable)
        result = execute(shards)
        assert (result.status, result.reason) == ("DEPENDENCY_UNAVAILABLE", "CTP_UNAVAILABLE")
        assert_no_bundle(result)
        assert "private" not in repr(result)

    def broken(*args, **kwargs):
        raise RuntimeError("private details")

    monkeypatch.setattr(module, "check_scope_batches", broken)
    result = execute(shards)
    assert (result.status, result.reason) == ("CHECK_FAILED", "CHECK_ERROR")
    assert_no_bundle(result)
    assert "private" not in repr(result)


def test_batch_gap_error_and_full_candidate_failure_never_produce_bundle(monkeypatch):
    from bt_api_ctp import calendar_scope_batches as batches
    from bt_api_ctp.calendar_candidate_coverage import StructuralCoverageResult

    shards = make_shards()

    def child_failed(*args, **kwargs):
        return StructuralCoverageResult("INVALID_INPUT", reason="INVALID_REQUIREMENTS_OR_BUDGET")

    monkeypatch.setattr(batches, "check_candidate_coverage", child_failed)
    result = execute(shards)
    assert result.status == "CHECK_FAILED" and result.composite is None
    assert result.shards[0].batch_status == "STRUCTURAL_GAPS"
    assert all(gap.reason == "SCOPE_CHECK_FAILED" for gap in result.shards[0].gaps)


def test_inconsistent_batch_success_is_rejected(monkeypatch):
    from bt_api_ctp import calendar_scope_batches as batches

    def invalid(*args, **kwargs):
        return batches.ScopeBatchResult(
            "SCOPES_CHECKED_UNVERIFIED", requested_days=1, checked_days=1
        )

    monkeypatch.setattr(batches, "check_scope_batches", invalid)
    result = execute(make_shards())
    assert result.status == "CHECK_FAILED" and result.composite is None


def test_composer_is_always_called_after_batches_and_failure_has_no_output(monkeypatch):
    import bt_api_py.trading_calendar_sharded_validation as module
    from bt_api_py.trading_calendar_composite import CompositeResult

    calls = []

    def rejected(**kwargs):
        calls.append(kwargs)
        return CompositeResult(SIMULATION_ONLY, "PROVIDER_ERROR", "UNVERIFIED", "DECLARED_ONLY")

    monkeypatch.setattr(module, "compose_simulated", rejected)
    result = execute(make_shards())
    assert len(calls) == 1 and len(result.shards) == 6
    assert all(item.batch_status == "SCOPES_CHECKED_UNVERIFIED" for item in result.shards)
    assert (result.status, result.reason, result.composite) == (
        "COMPOSITE_REJECTED",
        "PROVIDER_ERROR",
        None,
    )
    assert calls[0]["snapshot_factory"].__name__ == "CalendarSnapshot"


def test_full_candidate_check_failure_blocks_before_composition(monkeypatch):
    from types import SimpleNamespace

    from bt_api_ctp import calendar_scope_batches as batches

    import bt_api_py.trading_calendar_sharded_validation as module

    monkeypatch.setattr(
        batches,
        "normalize_calendar_candidate",
        lambda candidate: SimpleNamespace(status="INVALID_INPUT"),
    )

    def forbidden(**kwargs):
        raise AssertionError("composition must not be reached")

    monkeypatch.setattr(module, "compose_simulated", forbidden)
    result = execute(make_shards())
    assert result.status == "CHECK_FAILED" and result.composite is None
    assert result.shards[0].batch_status == "STRUCTURAL_GAPS"
    assert all(gap.reason == "FULL_CANDIDATE_INVALID" for gap in result.shards[0].gaps)


@pytest.mark.parametrize(
    "part",
    [
        "sources",
        "trading_days",
        "trading_hours",
        "exceptions",
        "metadata",
        "requirements",
        "container_type",
        "source_bookkeeping_type",
    ],
)
def test_checker_mutation_of_private_input_fails_without_composite(monkeypatch, part):
    from bt_api_ctp import calendar_scope_batches as module

    import bt_api_py.trading_calendar_sharded_validation as wrapper

    original = module.check_scope_batches

    def mutate(candidate, *, requirements):
        result = original(candidate, requirements=requirements)
        content = candidate.candidate.content
        if part == "sources":
            content["sources"][0]["description"] = "changed source"
        elif part == "trading_days":
            content["trading_days"][0]["status"] = "open"
        elif part == "trading_hours":
            content["trading_hours"][0]["day_windows"][0]["start"] = "08:00"
        elif part == "exceptions":
            content["exceptions"].append({"private": "changed"})
        elif part == "metadata":
            object.__setattr__(candidate.candidate, "verification", "VERIFIED")
        elif part == "container_type":
            content["trading_days"][0]["source_ids"] = tuple(
                content["trading_days"][0]["source_ids"]
            )
        elif part == "source_bookkeeping_type":
            object.__setattr__(
                candidate.candidate,
                "unused_source_ids",
                list(candidate.candidate.unused_source_ids),
            )
        else:
            object.__setattr__(requirements[0], "contract", "changed")
        return result

    def forbidden(**kwargs):
        raise AssertionError("mutated candidate must not be composed")

    monkeypatch.setattr(module, "check_scope_batches", mutate)
    monkeypatch.setattr(wrapper, "compose_simulated", forbidden)
    result = execute(make_shards())
    assert (result.status, result.reason, result.composite) == (
        "CHECK_FAILED",
        "CHECKER_MUTATED_INPUT",
        None,
    )


def test_later_checker_mutation_of_previously_checked_candidate_is_rejected(monkeypatch):
    from bt_api_ctp import calendar_scope_batches as module

    original = module.check_scope_batches
    previous = []

    def mutate_previous(candidate, *, requirements):
        result = original(candidate, requirements=requirements)
        if previous:
            previous[0].candidate.content["sources"][0]["description"] = "late mutation"
        else:
            previous.append(candidate)
        return result

    monkeypatch.setattr(module, "check_scope_batches", mutate_previous)
    result = execute(make_shards())
    assert (result.status, result.reason, result.composite) == (
        "CHECK_FAILED",
        "CHECKER_MUTATED_INPUT",
        None,
    )
