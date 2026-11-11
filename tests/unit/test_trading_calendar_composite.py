"""Offline simulation using synthetic venues and the experimental CTP factory."""

import hashlib
import json
from datetime import date

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_composite import SIMULATION_ONLY, compose_simulated
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore as TradingCalendarStore

START = date(2026, 1, 1)
END = date(2026, 1, 31)


def layers(venue, product, contract, start="09:00"):
    refs = ["synthetic-source"]
    return {
        "trading_days": [
            {"venue": venue, "day": "2026-01-02", "status": "open", "source_ids": refs}
        ],
        "trading_hours": [
            {
                "venue": venue,
                "kind": "future",
                "product": product,
                "contracts": [contract],
                "valid_from": "2026-01-01",
                "valid_to": "2026-01-31",
                "day_windows": [
                    {
                        "id": "day",
                        "phase": "continuous",
                        "start": start,
                        "end": "11:00",
                        "end_day_offset": 0,
                    }
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": "2026-01-02", "start_date": None, "source_ids": refs}
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": refs,
            }
        ],
        "exceptions": [],
        "sources": [{"id": "synthetic-source", "description": "fictional test data"}],
    }


def venue(name, product, contract, start="09:00", start_date=START, end_date=END):
    return VenueContent.from_layers(
        name, start_date, end_date, layers(name, product, contract, start)
    )


def compose(*venues, factory=CalendarSnapshot, mode=SIMULATION_ONLY):
    return compose_simulated(mode=mode, venues=tuple(venues), snapshot_factory=factory)


def replace_layers(original, data):
    return VenueContent.from_layers(
        original.venue, original.coverage_start, original.coverage_end, data
    )


def test_two_venues_compose_validate_and_roundtrip_through_real_store(tmp_path):
    a, b = venue("XSH", "x", "x2601"), venue("XDCE", "y", "y2601", "10:00")
    result = compose(a, b)
    assert (result.mode, result.status, result.source_evidence, result.coverage_evidence) == (
        SIMULATION_ONLY,
        "READY_SIMULATED",
        "UNVERIFIED",
        "DECLARED_ONLY",
    )
    payload = json.loads(result.snapshot_bytes)
    manifest = json.loads(result.manifest_bytes)
    assert result.snapshot_sha256 == hashlib.sha256(result.snapshot_bytes).hexdigest()
    assert result.manifest_sha256 == hashlib.sha256(result.manifest_bytes).hexdigest()
    binding = json.loads(result.binding_bytes)
    assert binding["snapshot_sha256"] == result.snapshot_sha256
    assert binding["manifest_sha256"] == result.manifest_sha256
    assert result.binding_sha256 == hashlib.sha256(result.binding_bytes).hexdigest()
    assert manifest["snapshot_sha256"] == result.snapshot_sha256
    assert [item["venue"] for item in manifest["coverage"]] == ["XDCE", "XSH"]
    assert all(item["semantics"] == "DECLARED_INCLUSIVE" for item in manifest["coverage"])
    assert len(payload["sources"]) == 1
    store = TradingCalendarStore(tmp_path)
    assert store.publish(payload, publication_id="p", version_id="v").visibility == "COMMITTED"
    stored = store.read_current()
    assert stored.content_sha256 == result.snapshot_sha256
    snapshot = CalendarSnapshot(stored.content, calendar_ref=stored.version_id)
    for venue_name, product, contract, hour in (
        ("XSH", "x", "x2601", 9),
        ("XDCE", "y", "y2601", 10),
    ):
        got = snapshot.query(
            venue=venue_name,
            kind="future",
            product=product,
            contract=contract,
            trading_day=date(2026, 1, 2),
        )
        assert got.status == "COMPLETE" and got.sources_verified is False
        assert got.windows[0].start_local.hour == hour
    assert (
        snapshot.query(
            venue="XSH", kind="future", product="x", contract="x2601", trading_day=date(2026, 2, 1)
        ).status
        == "UNKNOWN"
    )


def test_shared_equal_sources_deduplicated_but_conflicting_source_fails():
    a, b = venue("XSH", "x", "x2601"), venue("XDCE", "y", "y2601")
    assert len(json.loads(compose(a, b).snapshot_bytes)["sources"]) == 1
    data = json.loads(b.canonical_layers)
    data["sources"][0]["description"] = "different fictional claim"
    result = compose(a, replace_layers(b, data))
    assert result.status == "SOURCE_CONFLICT" and result.snapshot_bytes is None
    assert result.manifest_bytes is None


@pytest.mark.parametrize("where", ["top", "nested", "nested_missing", "cross_venue_only"])
def test_sources_must_be_declared_by_the_same_venue(where):
    a, b = venue("XSH", "x", "x2601"), venue("XDCE", "y", "y2601")
    data = json.loads(b.canonical_layers)
    if where == "top":
        data["trading_days"][0]["source_ids"] = ["missing"]
    elif where == "nested":
        data["trading_hours"][0]["night_mappings"][0]["source_ids"] = ["missing"]
    elif where == "nested_missing":
        del data["trading_hours"][0]["night_mappings"][0]["source_ids"]
    else:
        data["sources"] = [{"id": "different", "description": "separate"}]
    result = compose(a, replace_layers(b, data))
    assert result.status == "SOURCE_REFERENCE_MISSING" and result.snapshot_bytes is None


@pytest.mark.parametrize("layer", ["trading_days", "trading_hours", "exceptions"])
def test_wrong_venue_never_crosses_group_boundary(layer):
    a = venue("XSH", "x", "x2601")
    data = json.loads(a.canonical_layers)
    if layer == "exceptions":
        data[layer].append({"venue": "XDCE", "source_ids": ["synthetic-source"]})
    else:
        data[layer][0]["venue"] = "XDCE"
    assert compose(replace_layers(a, data)).status == "VENUE_MISMATCH"


def test_duplicate_venue_duplicate_records_and_duplicate_local_source_rejected():
    a = venue("XSH", "x", "x2601")
    assert compose(a, a).status == "DUPLICATE_VENUE"
    for layer in ("trading_days", "trading_hours", "sources"):
        data = json.loads(a.canonical_layers)
        data[layer].append(data[layer][0].copy())
        assert compose(replace_layers(a, data)).status == "DUPLICATE_RECORD"


def test_composer_rejects_distinct_records_with_conflicting_identity():
    a = venue("XSH", "x", "x2601")
    data = json.loads(a.canonical_layers)
    duplicate = dict(data["trading_days"][0])
    duplicate["status"] = "closed"
    data["trading_days"].append(duplicate)
    result = compose(replace_layers(a, data))
    assert result.status == "DUPLICATE_RECORD" and result.snapshot_bytes is None


def test_input_order_independent_digest_and_no_mutation_after_input_created():
    source = layers("XSH", "x", "x2601")
    a = VenueContent.from_layers("XSH", START, END, source)
    b = venue("XDCE", "y", "y2601")
    first = compose(a, b)
    source["trading_hours"][0]["day_windows"][0]["start"] = "03:00"
    second = compose(b, a)
    assert (first.snapshot_bytes, first.manifest_bytes) == (
        second.snapshot_bytes,
        second.manifest_bytes,
    )
    assert (first.snapshot_sha256, first.manifest_sha256) == (
        second.snapshot_sha256,
        second.manifest_sha256,
    )
    assert (
        json.loads(first.snapshot_bytes)["trading_hours"][1]["day_windows"][0]["start"] == "09:00"
    )


def test_top_level_record_order_is_canonical_but_no_record_is_lost():
    a = venue("XSH", "x", "x2601")
    data = json.loads(a.canonical_layers)
    second = dict(data["trading_days"][0])
    second["day"] = "2026-01-03"
    second["status"] = "closed"
    data["trading_days"].append(second)
    first = compose(replace_layers(a, data))
    data["trading_days"].reverse()
    again = compose(replace_layers(a, data))
    assert first.status == again.status == "READY_SIMULATED"
    assert first.snapshot_bytes == again.snapshot_bytes
    assert len(json.loads(first.snapshot_bytes)["trading_days"]) == 2


def test_coverage_manifest_preserved_and_bound_separately_to_snapshot():
    a, b = venue("XSH", "x", "x2601"), venue("XDCE", "y", "y2601")
    first = compose(a, b)
    broader = compose(venue("XSH", "x", "x2601", start_date=date(2025, 12, 1)), b)
    changed = compose(a, venue("XDCE", "y", "y2601", "10:00"))
    assert first.snapshot_sha256 == broader.snapshot_sha256
    assert first.manifest_sha256 != broader.manifest_sha256
    assert first.binding_sha256 != broader.binding_sha256
    assert first.snapshot_sha256 != changed.snapshot_sha256
    assert first.manifest_sha256 != changed.manifest_sha256
    assert first.binding_sha256 != changed.binding_sha256
    assert json.loads(first.manifest_bytes)["snapshot_sha256"] == first.snapshot_sha256
    assert json.loads(broader.manifest_bytes)["snapshot_sha256"] == broader.snapshot_sha256


def test_invalid_structures_and_bad_factories_fail_with_finite_status():
    a = venue("XSH", "x", "x2601")
    assert compose().status == "INVALID_INPUT"
    assert compose(a, a, factory=None).status == "INVALID_INPUT"
    data = json.loads(a.canonical_layers)
    data["trading_days"] = {}
    assert compose(replace_layers(a, data)).status == "INVALID_INPUT"
    data = json.loads(a.canonical_layers)
    data["trading_hours"] = []
    assert compose(replace_layers(a, data)).status == "INVALID_INPUT"
    assert compose(a, factory=lambda *args, **kwargs: None).status == "PROVIDER_REJECTED"
    assert (
        compose(
            a, factory=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fake"))
        ).status
        == "PROVIDER_ERROR"
    )

    class BadProvider:
        @property
        def query(self):
            raise RuntimeError("fake query accessor")

    assert compose(a, factory=lambda *args, **kwargs: BadProvider()).status == "PROVIDER_ERROR"
    with pytest.raises(ValueError):
        compose(a, mode="production")


def test_factory_mutation_is_rejected_without_returning_candidate():
    a = venue("XSH", "x", "x2601")

    class Provider:
        def query(self, **kwargs):
            return None

    def factory(data, **kwargs):
        data["trading_days"].clear()
        return Provider()

    result = compose(a, factory=factory)
    assert result.status == "PROVIDER_MUTATED_INPUT"
    assert result.snapshot_bytes is None and result.manifest_bytes is None
