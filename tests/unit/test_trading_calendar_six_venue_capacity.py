"""SIMULATION ONLY: 2026–2027 closed dates are fictional, not venue facts.

All writes are to pytest's temporary local store. Structural completeness and
caller-declared source/coverage do not authenticate any real exchange rule,
calendar, listing, UTC window, publication permission, or production capacity.
"""

import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta

import pytest
from bt_api_ctp.calendar_candidate_coverage import CoverageRequirement, check_candidate_coverage
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.calendar_supplement import MAX_LIST_ITEMS, SupplementError, parse_supplement

from bt_api_py.trading_calendar_composite import SIMULATION_ONLY, compose_simulated
from bt_api_py.trading_calendar_store import TradingCalendarStore
from bt_api_py.trading_calendar_update_simulation import VenueContent

START = date(2026, 1, 1)
END = date(2027, 12, 31)
VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
DAY_COUNT = (END - START).days + 1


def scopes(venue):
    return tuple(
        (kind, f"synthetic{kind}", f"{venue.lower()}{kind}2701") for kind in ("future", "option")
    )


def synthetic_candidate(venue, *, missing=None, source_description="fictional shared test source"):
    source_id = "shared.synthetic"
    days = [
        {
            "venue": venue,
            "day": (START + timedelta(days=offset)).isoformat(),
            "status": "closed",
            "source_ids": [source_id],
        }
        for offset in range(DAY_COUNT)
        if START + timedelta(days=offset) != missing
    ]
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
                    "id": "fictional.day",
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
        for kind, product, contract in scopes(venue)
    ]
    content = {
        "sources": [{"id": source_id, "description": source_description}],
        "trading_days": days,
        "trading_hours": hours,
        "exceptions": [],
    }
    return parse_supplement(json.dumps(content, ensure_ascii=False).encode())


@pytest.fixture(scope="module")
def candidates():
    return {venue: synthetic_candidate(venue) for venue in VENUES}


def coverage(candidate, venue):
    requirements = tuple(
        CoverageRequirement(venue, kind, product, contract, START, END)
        for kind, product, contract in scopes(venue)
    )
    return check_candidate_coverage(candidate, requirements=requirements)


def assemble_after_checks(candidates_by_venue, store, compose=compose_simulated):
    """Test-only gate; the SDK composer itself does not perform this coverage check."""
    checks = {venue: coverage(candidate, venue) for venue, candidate in candidates_by_venue.items()}
    if set(checks) != set(VENUES) or any(
        result.status != "STRUCTURAL_COMPLETE_UNVERIFIED" for result in checks.values()
    ):
        return checks, None, None
    groups = tuple(
        VenueContent.from_layers(venue, START, END, candidates_by_venue[venue].content)
        for venue in VENUES
    )
    bundle = compose(mode=SIMULATION_ONLY, venues=groups, snapshot_factory=CalendarSnapshot)
    if bundle.status != "READY_SIMULATED":
        return checks, bundle, None
    published = store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id="simulation-capacity-pub",
        version_id="simulation-capacity-v1",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    return checks, bundle, published


def test_six_venue_two_year_shards_compose_store_and_query(tmp_path, candidates):
    assert DAY_COUNT == 730 and MAX_LIST_ITEMS == 1000
    assert all(candidate.verification == "UNVERIFIED" for candidate in candidates.values())
    assert all(
        len(candidate.content["trading_days"]) == 730 < MAX_LIST_ITEMS
        for candidate in candidates.values()
    )
    oversized = deepcopy(candidates["SHFE"].content)
    for offset in range(MAX_LIST_ITEMS + 1 - DAY_COUNT):
        oversized["trading_days"].append(
            {
                "venue": "SHFE",
                "day": (END + timedelta(days=offset + 1)).isoformat(),
                "status": "closed",
                "source_ids": ["shared.synthetic"],
            }
        )
    with pytest.raises(SupplementError):
        parse_supplement(json.dumps(oversized).encode())
    store = TradingCalendarStore(tmp_path)
    checks, bundle, published = assemble_after_checks(candidates, store)
    for result in checks.values():
        assert result.status == "STRUCTURAL_COMPLETE_UNVERIFIED" and not result.gaps
        assert result.requested_days == result.checked_days == 1460
        assert (
            result.verification,
            result.coverage,
            result.source_authentication,
            result.utc_coverage,
            result.publication,
        ) == (
            "UNVERIFIED",
            "CALLER_DECLARED_SCOPE_ONLY",
            "NOT_AUTHENTICATED",
            "NOT_PROVEN",
            "NOT_AUTHORIZED",
        )
    assert bundle.status == "READY_SIMULATED" and bundle.source_evidence == "UNVERIFIED"
    assert bundle.coverage_evidence == "DECLARED_ONLY" and published.visibility == "COMMITTED"
    payload = json.loads(bundle.snapshot_bytes)
    assert len(payload["trading_days"]) == 6 * 730 == 4380
    assert len(payload["trading_hours"]) == 12 and len(payload["sources"]) == 1
    manifest = json.loads(bundle.manifest_bytes)
    binding = json.loads(bundle.binding_bytes)
    assert len(manifest["coverage"]) == 6
    assert {row["venue"] for row in manifest["coverage"]} == set(VENUES)
    assert all(
        (row["coverage_start"], row["coverage_end"], row["semantics"])
        == (START.isoformat(), END.isoformat(), "DECLARED_INCLUSIVE")
        for row in manifest["coverage"]
    )
    assert hashlib.sha256(bundle.snapshot_bytes).hexdigest() == bundle.snapshot_sha256
    assert hashlib.sha256(bundle.manifest_bytes).hexdigest() == bundle.manifest_sha256
    assert hashlib.sha256(bundle.binding_bytes).hexdigest() == bundle.binding_sha256
    assert (binding["snapshot_sha256"], binding["manifest_sha256"]) == (
        bundle.snapshot_sha256,
        bundle.manifest_sha256,
    )
    stored = store.read_current()
    assert (
        stored.content_sha256 == bundle.snapshot_sha256
        and stored.coverage_status == "DECLARED_UNVERIFIED"
    )
    assert (stored.coverage_manifest_bytes, stored.binding_bytes) == (
        bundle.manifest_bytes,
        bundle.binding_bytes,
    )
    snapshot = CalendarSnapshot(stored.content, calendar_ref=stored.version_id)
    for venue in VENUES:
        for kind, product, contract in scopes(venue):
            for day in (START, date(2026, 12, 31), date(2027, 1, 1), END):
                result = snapshot.query(
                    venue=venue, kind=kind, product=product, contract=contract, trading_day=day
                )
                assert (result.status, result.reason, result.sources_verified, result.windows) == (
                    "COMPLETE",
                    "EXPLICIT_DATA",
                    False,
                    (),
                )
            for day in (START - timedelta(days=1), END + timedelta(days=1)):
                result = snapshot.query(
                    venue=venue, kind=kind, product=product, contract=contract, trading_day=day
                )
                assert (result.status, result.reason, result.sources_verified) == (
                    "UNKNOWN",
                    "COVERAGE_INSUFFICIENT",
                    False,
                )


def test_missing_day_blocks_composition_and_publication(tmp_path, candidates, monkeypatch):
    missing = date(2026, 8, 13)
    shards = dict(candidates)
    shards["DCE"] = synthetic_candidate("DCE", missing=missing)
    store = TradingCalendarStore(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("gate must not compose or publish")

    monkeypatch.setattr(store, "publish", forbidden)
    checks, bundle, result = assemble_after_checks(shards, store, compose=forbidden)
    gap = checks["DCE"]
    assert gap.status == "STRUCTURAL_GAPS" and gap.checked_days == 1460
    assert len(gap.gaps) == 2 and {item.kind for item in gap.gaps} == {"future", "option"}
    assert all(
        item.start_day == item.end_day == missing and item.reason == "COVERAGE_INSUFFICIENT"
        for item in gap.gaps
    )
    assert all(
        checks[venue].status == "STRUCTURAL_COMPLETE_UNVERIFIED"
        for venue in VENUES
        if venue != "DCE"
    )
    assert bundle is result is None and not calls and not (tmp_path / "CURRENT.json").exists()


def test_cross_venue_source_conflict_blocks_publication(tmp_path, candidates, monkeypatch):
    shards = deepcopy(candidates)
    shards["GFEX"] = synthetic_candidate("GFEX", source_description="different fictional claim")
    store = TradingCalendarStore(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("conflicted composition must not publish")

    monkeypatch.setattr(store, "publish", forbidden)
    checks, bundle, result = assemble_after_checks(shards, store)
    assert all(check.status == "STRUCTURAL_COMPLETE_UNVERIFIED" for check in checks.values())
    assert bundle.status == "SOURCE_CONFLICT" and bundle.snapshot_bytes is None
    assert bundle.manifest_bytes is None and bundle.binding_bytes is None
    assert result is None and not calls and not (tmp_path / "CURRENT.json").exists()
