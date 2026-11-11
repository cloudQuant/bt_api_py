"""Local-only automatic-path stress sample; all venue data is fictional.

The 2026–2027 dates, weekday pattern, sessions and sources are synthetic test
inputs. Results do not establish actual exchange coverage, provenance, UTC
coverage, listing, publication authorization or production performance.
"""

import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta

import pytest
from bt_api_ctp.calendar_auto_candidate import build_auto_candidate
from bt_api_ctp.calendar_candidate_coverage import CoverageRequirement
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.calendar_supplement import MAX_INPUT_BYTES, SupplementError, parse_supplement

from bt_api_py.trading_calendar_composite import SIMULATION_ONLY
from bt_api_py.trading_calendar_sharded_validation import VenueShard, validate_and_compose_simulated
from tests.calendar_test_store import FixtureCalendarStore as TradingCalendarStore

START, END = date(2026, 1, 1), date(2027, 12, 31)
VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
SOURCE = "s" * 82
DAY_COUNT = (END - START).days + 1


def scopes(venue):
    return tuple(
        (kind, "synthetic", f"{venue.lower()}_{suffix}")
        for kind, suffix in (("future", "future"), ("option", "option1"), ("option", "option2"))
    )


def synthetic_layers(venue):
    days, mappings = [], []
    for offset in range(DAY_COUNT):
        day = (START + timedelta(days=offset)).isoformat()
        opened = offset % 7 < 5  # Fictional pattern, not an exchange calendar.
        days.append(
            {
                "venue": venue,
                "day": day,
                "status": "open" if opened else "closed",
                "source_ids": [SOURCE],
            }
        )
        if opened:
            # Null explicitly declares no night in this fictional sample.
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
                    "id": "synthetic.day",
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
        "sources": [{"id": SOURCE, "description": "fictional synthetic only"}],
        "trading_days": days,
        "trading_hours": hours,
        "exceptions": [],
    }


def build_shard(venue, layers):
    built = build_auto_candidate(venue=venue, coverage_start=START, coverage_end=END, layers=layers)
    assert (built.status, built.verification, built.coverage, built.publication) == (
        "CANDIDATE",
        "UNVERIFIED",
        "NOT_PROVEN",
        "NOT_AUTHORIZED",
    )
    requirements = tuple(
        CoverageRequirement(venue, kind, product, contract, START, END)
        for kind, product, contract in scopes(venue)
    )
    return VenueShard(venue, START, END, built.candidate, requirements)


@pytest.fixture(scope="module")
def shards():
    assert DAY_COUNT == 730 and len(SOURCE) == 82
    values = []
    for venue in VENUES:
        layers = synthetic_layers(venue)
        raw = json.dumps(layers, sort_keys=True, separators=(",", ":")).encode()
        assert len(raw) > MAX_INPUT_BYTES
        if venue == "SHFE":
            with pytest.raises(SupplementError):
                parse_supplement(raw)
        assert len(layers["trading_days"]) == 730
        assert sum(row["status"] == "open" for row in layers["trading_days"]) == 522
        assert len(layers["trading_hours"]) == 3
        assert all(len(row["night_mappings"]) == 522 for row in layers["trading_hours"])
        values.append(build_shard(venue, layers))
    return tuple(values)


def compose_and_publish(shards, directory):
    """Test-only store call, performed exclusively after simulated validation."""
    result = validate_and_compose_simulated(mode=SIMULATION_ONLY, shards=shards)
    if result.status != "READY_SIMULATED":
        return result, None, None
    bundle = result.composite
    store = TradingCalendarStore(directory)
    publication = store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id="fictional-auto-pub",
        version_id="fictional-auto-v1",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    return result, publication, store


def test_real_automatic_candidates_through_six_venue_temporary_store(tmp_path, shards):
    result, publication, store = compose_and_publish(shards, tmp_path)
    assert result.status == "READY_SIMULATED" and publication.visibility == "COMMITTED"
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
    assert result.manifest_coverage == "CALLER_DECLARED_RANGE_NOT_FULLY_CHECKED"
    assert result.venue_date_layer_status == "EXPLICIT_DATES_STRUCTURALLY_COMPLETE_UNVERIFIED"
    assert len(result.shards) == 6 and sum(len(item.requirements) for item in result.shards) == 18
    assert all(
        item.status == "STRUCTURAL_COMPLETE_UNVERIFIED"
        and item.checked_days == 2190
        and not item.gaps
        for item in result.shards
    )
    bundle = result.composite
    payload = json.loads(bundle.snapshot_bytes)
    assert len(payload["trading_days"]) == 4380 and len(payload["trading_hours"]) == 18
    assert len(payload["sources"]) == 1 and payload["sources"][0]["id"] == SOURCE
    assert all(len(row["night_mappings"]) == 522 for row in payload["trading_hours"])
    assert hashlib.sha256(bundle.snapshot_bytes).hexdigest() == bundle.snapshot_sha256
    assert hashlib.sha256(bundle.manifest_bytes).hexdigest() == bundle.manifest_sha256
    assert hashlib.sha256(bundle.binding_bytes).hexdigest() == bundle.binding_sha256
    binding = json.loads(bundle.binding_bytes)
    assert (binding["snapshot_sha256"], binding["manifest_sha256"]) == (
        bundle.snapshot_sha256,
        bundle.manifest_sha256,
    )
    assert len(json.loads(bundle.manifest_bytes)["coverage"]) == 6
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
            for day in (date(2026, 12, 31), date(2027, 1, 1)):
                item = snapshot.query(
                    venue=venue, kind=kind, product=product, contract=contract, trading_day=day
                )
                assert item.status == "COMPLETE" and item.sources_verified is False
                assert len(item.windows) == 1 and item.windows[0].start_local.hour == 9
                assert item.windows[0].end_local.hour == 10


def test_missing_date_refuses_composition_and_store(tmp_path, shards):
    changed = list(shards)
    layers = deepcopy(shards[0].candidate.content)
    # Offset 5 is closed in our synthetic pattern, so no night mapping references it.
    missing = (START + timedelta(days=5)).isoformat()
    layers["trading_days"] = [row for row in layers["trading_days"] if row["day"] != missing]
    changed[0] = build_shard(shards[0].venue, layers)
    result, publication, store = compose_and_publish(tuple(changed), tmp_path)
    assert (result.status, result.reason) == ("CHECK_FAILED", "VENUE_DATE_RECORDS_INCOMPLETE")
    assert result.composite is publication is store is None and not list(tmp_path.iterdir())
    assert result.verification == "UNVERIFIED" and result.publication == "NOT_AUTHORIZED"


def test_cross_venue_source_conflict_refuses_composition_and_store(tmp_path, shards):
    changed = list(shards)
    layers = deepcopy(shards[-1].candidate.content)
    layers["sources"][0]["description"] = "different fictional synthetic claim"
    changed[-1] = build_shard(shards[-1].venue, layers)
    result, publication, store = compose_and_publish(tuple(changed), tmp_path)
    assert (result.status, result.reason) == ("COMPOSITE_REJECTED", "SOURCE_CONFLICT")
    assert result.composite is publication is store is None and not list(tmp_path.iterdir())
    assert result.verification == "UNVERIFIED" and result.publication == "NOT_AUTHORIZED"
