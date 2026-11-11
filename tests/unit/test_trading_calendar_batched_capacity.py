"""Local simulation only: all calendars, dates, identities and sessions are fictional."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

import pytest
from bt_api_ctp.calendar_auto_candidate import build_auto_candidate
from bt_api_ctp.calendar_candidate_coverage import CoverageRequirement, check_candidate_coverage
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_composite import SIMULATION_ONLY
from bt_api_py.trading_calendar_sharded_validation import VenueShard, validate_and_compose_simulated
from bt_api_py.trading_calendar_store import TradingCalendarStore

START, END = date(2026, 1, 1), date(2027, 12, 31)
VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
SOURCE = "s" * 82


def scopes(venue):
    return tuple(
        ("future" if index < 6 else "option", "synthetic", f"{venue.lower()}_contract{index:02d}")
        for index in range(12)
    )


def layers(venue):
    days, mappings = [], []
    for index in range(730):
        day = (START + timedelta(days=index)).isoformat()
        opened = index % 7 < 5  # Explicit synthetic claims only.
        days.append(
            {
                "venue": venue,
                "day": day,
                "status": "open" if opened else "closed",
                "source_ids": [SOURCE],
            }
        )
        if opened:
            mappings.append({"trading_day": day, "start_date": None, "source_ids": [SOURCE]})
    hours = [
        {
            "venue": venue,
            "kind": kind,
            "product": product,
            "contracts": [contract],
            "valid_from": START.isoformat(),
            "valid_to": END.isoformat(),
            "day_windows": [
                {
                    "id": "fake.day",
                    "phase": "continuous",
                    "start": "09:00",
                    "end": "10:00",
                    "end_day_offset": 0,
                }
            ],
            "night_windows": [],
            "night_mappings": deepcopy(mappings),
            "source_ids": [SOURCE],
            "windows_complete": True,
            "exceptions_complete": True,
        }
        for kind, product, contract in scopes(venue)
    ]
    return {
        "sources": [{"id": SOURCE, "description": "synthetic only"}],
        "trading_days": days,
        "trading_hours": hours,
        "exceptions": [],
    }


def make_shard(venue, content):
    built = build_auto_candidate(
        venue=venue, coverage_start=START, coverage_end=END, layers=content
    )
    assert (built.status, built.verification, built.publication) == (
        "CANDIDATE",
        "UNVERIFIED",
        "NOT_AUTHORIZED",
    )
    requirements = tuple(
        CoverageRequirement(venue, kind, product, contract, START, END)
        for kind, product, contract in scopes(venue)
    )
    return VenueShard(venue, START, END, built.candidate, requirements)


@pytest.fixture(scope="module")
def shards():
    return tuple(make_shard(venue, layers(venue)) for venue in VENUES)


def execute(shards):
    return validate_and_compose_simulated(mode=SIMULATION_ONLY, shards=shards)


def test_twelve_scope_six_venues_build_compose_and_roundtrip(tmp_path, shards):
    for shard in shards:
        direct = check_candidate_coverage(shard.candidate, requirements=shard.requirements)
        assert (
            direct.status == "INVALID_INPUT" and direct.reason == "INVALID_REQUIREMENTS_OR_BUDGET"
        )
    result = execute(shards)
    assert result.status == "READY_SIMULATED" and len(result.shards) == 6
    assert (
        result.mode,
        result.verification,
        result.coverage,
        result.utc_evidence,
        result.publication,
    ) == (
        "SIMULATION_ONLY",
        "UNVERIFIED",
        "CALLER_DECLARED_SCOPE_ONLY",
        "UTC_NOT_PROVEN",
        "NOT_AUTHORIZED",
    )
    assert all(
        item.batch_status == "SCOPES_CHECKED_UNVERIFIED"
        and item.checked_days == 12 * 730
        and item.requested_days == 12 * 730
        and not item.gaps
        for item in result.shards
    )
    bundle = result.composite
    data = json.loads(bundle.snapshot_bytes)
    assert len(data["trading_days"]) == 4380 and len(data["trading_hours"]) == 72
    assert all(len(row["night_mappings"]) == 522 for row in data["trading_hours"])
    store = TradingCalendarStore(tmp_path)
    publication = store.publish(
        data,
        publication_id="batch-simulation-pub",
        version_id="batch-simulation-v1",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    assert publication.visibility == "COMMITTED"
    loaded = store.read_current()
    assert (
        loaded.coverage_status == "DECLARED_UNVERIFIED"
        and loaded.content_sha256 == bundle.snapshot_sha256
    )
    assert (loaded.coverage_manifest_bytes, loaded.binding_bytes) == (
        bundle.manifest_bytes,
        bundle.binding_bytes,
    )
    manifest = json.loads(loaded.coverage_manifest_bytes)
    assert len(manifest["coverage"]) == 6 and {row["venue"] for row in manifest["coverage"]} == set(
        VENUES
    )
    snapshot = CalendarSnapshot(loaded.content, calendar_ref=loaded.version_id)
    for venue in VENUES:
        for kind, product, contract in scopes(venue):
            for day in (date(2026, 12, 31), date(2027, 1, 1)):
                value = snapshot.query(
                    venue=venue, kind=kind, product=product, contract=contract, trading_day=day
                )
                assert value.status == "COMPLETE" and value.sources_verified is False
                assert len(value.windows) == 1 and value.windows[0].start_local.hour == 9


def test_one_scope_gap_blocks_all_output_and_source_conflict(shards):
    changed = list(shards)
    content = deepcopy(shards[0].candidate.content)
    content["trading_hours"][0]["windows_complete"] = False
    changed[0] = make_shard(shards[0].venue, content)
    gap = execute(tuple(changed))
    assert gap.status == "CHECK_FAILED" and gap.composite is None
    evidence = next(item for item in gap.shards if item.venue == shards[0].venue)
    assert evidence.batch_status == "STRUCTURAL_GAPS" and evidence.gaps
    assert {item.contract for item in evidence.gaps} == {scopes(shards[0].venue)[0][2]}
    conflict = list(shards)
    content = deepcopy(shards[-1].candidate.content)
    content["sources"][0]["description"] = "different synthetic source"
    conflict[-1] = make_shard(shards[-1].venue, content)
    result = execute(tuple(conflict))
    assert (result.status, result.reason, result.composite) == (
        "COMPOSITE_REJECTED",
        "SOURCE_CONFLICT",
        None,
    )


def test_declared_requirement_outside_venue_range_blocks_output(shards):
    changed = list(shards)
    first = shards[0]
    requirements = list(first.requirements)
    requirements[0] = replace(requirements[0], start_day=START - timedelta(days=1))
    changed[0] = replace(first, requirements=tuple(requirements))
    result = execute(tuple(changed))
    assert result.status == "INVALID_INPUT" and result.composite is None
