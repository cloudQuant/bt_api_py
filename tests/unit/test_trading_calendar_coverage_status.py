"""Synthetic local declarations only; these do not prove exchange coverage."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_coverage_health import EvidenceClaim
from bt_api_py.trading_calendar_coverage_status import VENUES, read_coverage_status
from bt_api_py.trading_calendar_store import TradingCalendarStore
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore

CLOCK = EvidenceClaim("VERIFIED", "synthetic:clock")
NOW = datetime(2026, 11, 30, 16, 0, tzinfo=UTC)


class Provider:
    def query(self, **kwargs):
        return None


def bundle(end, venues=VENUES, starts=None):
    items = []
    for venue in venues:
        layers = {
            "sources": [{"id": "src", "description": "synthetic"}],
            "trading_days": [{"venue": venue, "day": "2026-01-01", "source_ids": ["src"]}],
            "trading_hours": [
                {
                    "venue": venue,
                    "kind": "future",
                    "product": "x",
                    "contracts": ["x1"],
                    "valid_from": "2026-01-01",
                    "valid_to": "2027-12-31",
                    "source_ids": ["src"],
                    "night_mappings": [],
                }
            ],
            "exceptions": [],
        }
        start = starts[venue] if starts is not None else date(2026, 1, 1)
        end_day = end[venue] if isinstance(end, dict) else end
        items.append(VenueContent.from_layers(venue, start, end_day, layers))
    result = compose_simulated(
        mode="SIMULATION_ONLY", venues=tuple(items), snapshot_factory=lambda *a, **k: Provider()
    )
    assert result.status == "READY_SIMULATED"
    return result


def publish(store, item, suffix):
    result = store.publish(
        json.loads(item.snapshot_bytes),
        publication_id=f"p{suffix}",
        version_id=f"v{suffix}",
        coverage_manifest_bytes=item.manifest_bytes,
        coverage_manifest_sha256=item.manifest_sha256,
        binding_bytes=item.binding_bytes,
        binding_sha256=item.binding_sha256,
    )
    assert result.visibility == "COMMITTED"


def report(store, **kwargs):
    return read_coverage_status(
        store,
        mode="SIMULATION_ONLY",
        clock_instant=kwargs.pop("clock_instant", NOW),
        clock_claim=kwargs.pop("clock_claim", CLOCK),
        clock_error_bound=kwargs.pop("clock_error_bound", timedelta(0)),
        **kwargs,
    )


def by_venue(rows):
    return {item.health.venue: item for item in rows}


def test_single_current_read_and_bound_version_remains_independent(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31)), 1)
    bound = store.read_current()
    publish(store, bundle(date(2027, 12, 31)), 2)

    class OneRead:
        calls = 0

        def read_current(self):
            self.calls += 1
            return store.read_current()

    reader = OneRead()
    result = report(reader, bound_snapshot=bound)
    assert reader.calls == 1 and result.mode == "SIMULATION_ONLY"
    assert result.current_origin == "LOCAL_CURRENT_UNAUTHENTICATED"
    assert result.bound_origin == "CALLER_DECLARED_UNAUTHENTICATED"
    assert len(result.current) == len(result.bound) == 6
    for venue in VENUES:
        current, old = by_venue(result.current)[venue], by_venue(result.bound)[venue]
        assert (current.health.version_id, old.health.version_id) == ("v2", "v1")
        assert (current.simulation_status, old.simulation_status) == ("OK", "WARNING")
        assert (current.simulation_days_remaining, old.simulation_days_remaining) == (395, 30)
        assert current.declaration == old.declaration == "DECLARED"
        assert current.health.status == old.health.status == "UNKNOWN"
        assert current.health.days_remaining is old.health.days_remaining is None
        assert "SOURCE_UNVERIFIED" in current.health.reasons
        assert current.health.shanghai_date == date(2026, 12, 1)


def test_missing_venues_are_not_inferred_and_current_updates(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31), ("SHFE",)), 1)
    first = report(store)
    assert first.bound is None and first.bound_origin == "NOT_SUPPLIED"
    assert by_venue(first.current)["DCE"].simulation_status == "UNKNOWN"
    assert by_venue(first.current)["DCE"].declaration == "UNAVAILABLE"
    publish(store, bundle(date(2027, 12, 31), ("DCE",)), 2)
    second = report(store)
    assert by_venue(first.current)["SHFE"].simulation_days_remaining == 30
    assert by_venue(second.current)["SHFE"].simulation_days_remaining is None
    assert by_venue(second.current)["DCE"].simulation_days_remaining == 395


@pytest.mark.parametrize(
    "instant,claim,error",
    [
        (None, CLOCK, timedelta(0)),
        (NOW, None, timedelta(0)),
        (NOW, CLOCK, None),
        (
            datetime(2026, 11, 30, 15, 59, 59, 900000, tzinfo=UTC),
            CLOCK,
            timedelta(milliseconds=200),
        ),
    ],
)
def test_unknown_clock_suppresses_declared_countdown(tmp_path, instant, claim, error):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31)), 1)
    result = report(store, clock_instant=instant, clock_claim=claim, clock_error_bound=error)
    assert all(row.health.status == row.simulation_status == "UNKNOWN" for row in result.current)
    assert all(row.simulation_days_remaining is None for row in result.current)


@pytest.mark.parametrize("corrupt", ["content", "manifest", "binding", "version", "range"])
def test_mixed_or_invalid_bound_copy_is_unknown(tmp_path, corrupt):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31)), 1)
    source = store.read_current()
    if corrupt == "content":
        source.content["sources"][0]["description"] = "changed"
    elif corrupt == "manifest":
        source = replace(source, coverage_manifest_bytes=source.coverage_manifest_bytes + b" ")
    elif corrupt == "binding":
        source = replace(source, binding_sha256="0" * 64)
    elif corrupt == "version":
        source = replace(source, version_sha256="0" * 64)
    else:
        manifest = json.loads(source.coverage_manifest_bytes)
        manifest["coverage"][0]["coverage_start"] = "2028-01-01"

        def canonical(obj):
            return json.dumps(
                obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()

        def digest(data):
            return hashlib.sha256(data).hexdigest()

        manifest_bytes = canonical(manifest)
        manifest_hash = digest(manifest_bytes)
        binding = json.loads(source.binding_bytes)
        binding["manifest_sha256"] = manifest_hash
        binding_bytes = canonical(binding)
        binding_hash = digest(binding_bytes)
        version = {
            "schema": "bt_api.calendar-store.snapshot/2",
            "content": source.content,
            "manifest": manifest,
            "manifest_sha256": manifest_hash,
            "binding": binding,
            "binding_sha256": binding_hash,
        }
        source = replace(
            source,
            coverage_manifest_bytes=manifest_bytes,
            coverage_manifest_sha256=manifest_hash,
            binding_bytes=binding_bytes,
            binding_sha256=binding_hash,
            version_sha256=digest(canonical(version)),
        )
    result = report(store, bound_snapshot=source)
    assert all(row.simulation_status == "UNKNOWN" for row in result.bound)
    assert all(row.simulation_days_remaining is None for row in result.bound)
    assert all(row.simulation_status == "WARNING" for row in result.current)


def test_absent_store_read_does_not_create_files(tmp_path):
    path = tmp_path / "missing"
    result = report(TradingCalendarStore(path))
    assert not path.exists()
    assert all(row.health.status == row.simulation_status == "UNKNOWN" for row in result.current)


def test_simulation_mode_and_clock_parameters_are_explicit(tmp_path):
    store = TradingCalendarStore(tmp_path)
    with pytest.raises(ValueError, match="SIMULATION_ONLY"):
        read_coverage_status(store, mode="LIVE", clock_instant=NOW, clock_claim=CLOCK)
    with pytest.raises(ValueError, match="threshold_days"):
        report(store, threshold_days=-1)


def test_coverage_read_does_not_mutate_store(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31)), 1)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report(store, bound_snapshot=store.read_current())
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_bound_mixed_with_other_current_manifest_is_unknown(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31)), 1)
    bound = store.read_current()
    publish(store, bundle(date(2027, 12, 31)), 2)
    current = store.read_current()
    mixed = replace(
        bound,
        coverage_manifest_bytes=current.coverage_manifest_bytes,
        coverage_manifest_sha256=current.coverage_manifest_sha256,
    )
    result = report(store, bound_snapshot=mixed)
    assert all(item.simulation_status == "UNKNOWN" for item in result.bound)
    assert all(item.simulation_status == "OK" for item in result.current)


@pytest.mark.parametrize(
    "instant, expected_date, expected_status, expected_days",
    [
        (
            datetime(2025, 12, 31, 15, 59, 59, tzinfo=UTC),
            date(2025, 12, 31),
            "OUTSIDE_COVERAGE",
            None,
        ),
        (datetime(2025, 12, 31, 16, 0, tzinfo=UTC), date(2026, 1, 1), "OK", 364),
        (datetime(2026, 12, 31, 16, 0, tzinfo=UTC), date(2027, 1, 1), "WARNING", -1),
    ],
)
def test_declared_start_and_expiry_use_shanghai_date(
    tmp_path, instant, expected_date, expected_status, expected_days
):
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(date(2026, 12, 31)), 1)
    result = report(store, clock_instant=instant, bound_snapshot=store.read_current())
    for item in (*result.current, *result.bound):
        assert item.health.status == "UNKNOWN" and item.health.days_remaining is None
        assert item.health.shanghai_date == expected_date
        assert item.simulation_status == expected_status
        assert item.simulation_days_remaining == expected_days


def test_each_venue_uses_its_own_declared_start_and_end(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    starts = {
        "SHFE": date(2026, 1, 2),
        "INE": date(2026, 1, 1),
        "CZCE": date(2026, 1, 1),
        "DCE": date(2025, 12, 31),
        "CFFEX": date(2025, 12, 30),
        "GFEX": date(2025, 12, 29),
    }
    ends = {
        "SHFE": date(2026, 12, 31),
        "INE": date(2026, 12, 31),
        "CZCE": date(2026, 1, 1),
        "DCE": date(2025, 12, 31),
        "CFFEX": date(2026, 1, 31),
        "GFEX": date(2026, 2, 1),
    }
    publish(store, bundle(ends, starts=starts), 1)
    rows = by_venue(report(store, clock_instant=datetime(2025, 12, 31, 16, 0, tzinfo=UTC)).current)
    expected = {
        "SHFE": ("OUTSIDE_COVERAGE", None),
        "INE": ("OK", 364),
        "CZCE": ("WARNING", 0),
        "DCE": ("WARNING", -1),
        "CFFEX": ("WARNING", 30),
        "GFEX": ("OK", 31),
    }
    assert {
        venue: (item.simulation_status, item.simulation_days_remaining)
        for venue, item in rows.items()
    } == expected
    assert all(item.health.status == "UNKNOWN" for item in rows.values())


def test_sparse_declaration_never_claims_completeness(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    # Fixture declares an entire year but contains just one trading-day row
    # and one product for the venue.
    publish(store, bundle(date(2026, 12, 31), ("SHFE",)), 1)
    item = by_venue(report(store, clock_instant=datetime(2026, 6, 1, tzinfo=UTC)).current)["SHFE"]
    assert item.simulation_status == "OK" and item.simulation_days_remaining == 213
    assert item.completeness == "NOT_PROVEN"
    assert item.declaration == "DECLARED"
    assert item.health.status == "UNKNOWN" and item.health.days_remaining is None
