"""Offline declaration comparison; factory semantics remain a trust dependency."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_interruption_compare_simulation import (
    compare_simulated_interruption as compare,
)
from bt_api_py.trading_calendar_interruption_compare_simulation import (
    merge_simulated_interruption as _merge,
)
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_interruption_view_simulation import fixture, publish


def declared(content):
    return {
        "content": content,
        "data_origin": "MOCK",
        "simulation_provenance": None,
        "coverage": [
            {
                "venue": "SHFE",
                "coverage_start": "2026-01-01",
                "coverage_end": "2026-12-31",
                "semantics": "DECLARED_INCLUSIVE",
            }
        ],
    }


def run(before, after, factory=CalendarSnapshot):
    return compare(before, after, mode="SIMULATION_ONLY", snapshot_factory=factory)


def merge(*args, **kwargs):
    return _merge(*args, **kwargs, data_origin="MOCK", simulation_provenance=None)


def test_real_store_first_same_modified_and_pure_inputs(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    data = fixture()
    publish(store, data, 1)
    current = store.read_current()
    coverage = json.loads(current.coverage_manifest_bytes)["coverage"]
    prior = {
        "content": current.content,
        "coverage": coverage,
        "data_origin": "MOCK",
        "simulation_provenance": current.simulation_provenance,
    }
    first = run(None, prior)
    assert first["status"] == "CHANGED" and first["baseline"] == "CALLER_DECLARED_ABSENT"
    assert len(first["changes"]["exceptions"]) == 2
    assert run(prior, prior)["status"] == "SAME"
    changed = deepcopy(prior)
    changed["content"]["exceptions"][0]["end_utc"] = "2026-06-02T02:30:00.000003Z"
    result = run(prior, changed)
    assert result["status"] == "CHANGED"
    change = result["changes"]["exceptions"][0]
    assert change["operation"] == "modified"
    assert change["before"] == prior["content"]["exceptions"][0]
    assert change["after"] == changed["content"]["exceptions"][0]
    assert len(change["after"]) == 11
    assert result["source_authentication"] == "NOT_PROVEN"
    assert prior["content"] == data


def test_merge_equal_and_conflicting_boundaries():
    data = fixture()
    supplement = {
        "sources": deepcopy(data["sources"]),
        "trading_days": [],
        "trading_hours": [],
        "exceptions": deepcopy(data["exceptions"]),
    }
    outcome = merge(
        data,
        supplement,
        coverage=declared(data)["coverage"],
        mode="SIMULATION_ONLY",
        snapshot_factory=CalendarSnapshot,
    )
    assert outcome["status"] == "READY" and outcome["content"] == data
    supplement["exceptions"][0]["end_utc"] = "2026-06-02T02:30:00.000003Z"
    assert (
        merge(
            data,
            supplement,
            coverage=declared(data)["coverage"],
            mode="SIMULATION_ONLY",
            snapshot_factory=CalendarSnapshot,
        )["status"]
        == "CONFLICT"
    )
    supplement["exceptions"][0] = deepcopy(data["exceptions"][0])
    supplement["exceptions"][0]["exception_schema"] = "unsupported"
    assert (
        merge(
            data,
            supplement,
            coverage=declared(data)["coverage"],
            mode="SIMULATION_ONLY",
            snapshot_factory=CalendarSnapshot,
        )["status"]
        == "CONFLICT"
    )
    supplement["exceptions"][0]["source_ids"] = ["UNKNOWN"]
    assert (
        merge(
            data,
            supplement,
            coverage=declared(data)["coverage"],
            mode="SIMULATION_ONLY",
            snapshot_factory=CalendarSnapshot,
        )["status"]
        == "UNKNOWN"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: x["content"]["exceptions"][0].pop("start_utc"),
        lambda x: x["content"]["exceptions"][0].update(source_ids=["another"]),
        lambda x: x["content"]["exceptions"].append(deepcopy(x["content"]["exceptions"][0])),
        lambda x: x["content"]["exceptions"][0].update(end_utc="2026-06-02T03:00:00Z"),
        lambda x: x["content"]["exceptions"][0].update(trading_day="2026-06-03"),
        lambda x: x["content"]["trading_days"].pop(),
        lambda x: x["content"]["trading_hours"][0].update(windows_complete=False),
        lambda x: x["content"]["sources"].append({"id": "other", "description": "other"}),
        lambda x: x.update(data_origin="AKSHARE_MOCK"),
        lambda x: x.update(simulation_provenance={"mixed": True}),
        lambda x: x["content"]["exceptions"][0].update(exception_schema="other"),
        lambda x: x["coverage"][0].update(coverage_end="2027-12-31"),
    ],
)
def test_each_side_unknown(mutate):
    good = declared(fixture())
    bad = deepcopy(good)
    mutate(bad)
    assert run(good, bad)["status"] == "UNKNOWN"
    assert run(bad, good)["status"] == "UNKNOWN"


def test_old_exception_and_cross_midnight():
    content = fixture()
    content["exceptions"] = [
        {
            "venue": "SHFE",
            "kind": "future",
            "product": "p",
            "contract": None,
            "trading_day": "2026-06-02",
            "action": "cancel_window",
            "window_id": "day",
            "source_ids": ["MOCK.simulation_only"],
        }
    ]
    a = declared(content)
    assert run(a, a)["status"] == "SAME"
    b = declared(fixture())
    assert run(b, b)["status"] == "SAME"
    b["content"]["exceptions"][1]["end_utc"] = "2026-06-01T16:15:00.000003Z"
    assert run(declared(fixture()), b)["changes"]["exceptions"][0]["operation"] == "modified"


def test_scope_query_identity_and_exception_failure_are_unknown():
    good = declared(fixture())

    class Forged:
        def __init__(self, content, calendar_ref):
            self.real = CalendarSnapshot(content, calendar_ref=calendar_ref)

        def query_venue_day(self, **kwargs):
            return self.real.query_venue_day(**kwargs)

        def query(self, **kwargs):
            return replace(self.real.query(**kwargs), contract="mock_other")

    assert run(good, good, Forged)["status"] == "UNKNOWN"

    def failing(*args, **kwargs):
        raise RuntimeError("private")

    assert "private" not in str(run(good, good, failing))
    assert run(good, good, failing)["status"] == "UNKNOWN"


def test_owner_before_coverage_and_work_budget_are_unknown(monkeypatch):
    from bt_api_py import trading_calendar_interruption_compare_simulation as module

    good = declared(fixture())
    outside = deepcopy(good)
    outside["content"]["trading_hours"][0]["night_mappings"][0]["start_date"] = "2025-12-31"
    assert run(good, outside)["status"] == "UNKNOWN"
    monkeypatch.setattr(module, "_MAX_CELLS", 2)
    assert run(good, good)["status"] == "UNKNOWN"


def test_unapplied_and_wildcard_contract_conflicts_are_unknown():
    good = declared(fixture())
    missing_scope = deepcopy(good)
    missing_scope["content"]["exceptions"][0]["contract"] = "mock_missing"
    assert run(good, missing_scope)["status"] == "UNKNOWN"
    collision = deepcopy(good)
    specific = deepcopy(collision["content"]["exceptions"][0])
    specific["contract"] = "mock_f1"
    collision["content"]["exceptions"].append(specific)
    assert run(good, collision)["status"] == "UNKNOWN"


def test_legacy_guard_and_legacy_full_diff_fail_closed():
    from bt_api_py.trading_calendar_candidate_inbox import _validated_baseline
    from bt_api_py.trading_calendar_composite import _require_legacy_exceptions
    from bt_api_py.trading_calendar_full_diff import _normalized

    value = declared(fixture())
    for fn in (_require_legacy_exceptions, _validated_baseline):
        with pytest.raises(ValueError):
            fn(value["content"])
    with pytest.raises(ValueError):
        _normalized(value["content"], {"coverage": value["coverage"]})


def test_real_store_callback_cannot_change_before_or_after_declarations(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, fixture(), 1)
    current = store.read_current()
    before = declared(current.content)
    after = deepcopy(before)
    original_before = deepcopy(before["content"]["exceptions"][0])
    after["content"]["exceptions"][0]["end_utc"] = "2026-06-02T02:30:00.000003Z"
    changed = deepcopy(after["content"]["exceptions"][0])

    def mutating_factory(shard, *, calendar_ref):
        before["content"]["exceptions"][0]["end_utc"] = changed["end_utc"]
        after["content"]["exceptions"].clear()
        return CalendarSnapshot(shard, calendar_ref=calendar_ref)

    result = run(before, after, mutating_factory)
    assert result["status"] == "CHANGED"
    change = result["changes"]["exceptions"][0]
    assert change["before"] == original_before
    assert change["after"] == changed


def test_real_store_exception_outside_coverage_is_not_dropped(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, fixture(), 1)
    good = declared(store.read_current().content)
    bad = deepcopy(good)
    unrelated = deepcopy(bad["content"]["exceptions"][0])
    unrelated["venue"] = "DCE"
    bad["content"]["exceptions"].append(unrelated)
    assert run(bad, bad)["status"] == "UNKNOWN"
    assert run(good, bad)["status"] == "UNKNOWN"
    assert run(bad, good)["status"] == "UNKNOWN"


def test_real_store_factory_cannot_remove_invalid_exception_before_ctp_validation(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, fixture(), 1)
    bad = declared(store.read_current().content)
    bad["content"]["exceptions"][0]["end_utc"] = "2026-06-02T03:00:00Z"

    def mutating_factory(shard, *, calendar_ref):
        shard["exceptions"].clear()
        return CalendarSnapshot(shard, calendar_ref=calendar_ref)

    assert run(bad, bad, mutating_factory)["status"] == "UNKNOWN"


def test_merge_copies_all_inputs_before_factory_callbacks():
    observed = fixture()
    supplement = {
        "sources": deepcopy(observed["sources"]),
        "trading_days": [],
        "trading_hours": [],
        "exceptions": deepcopy(observed["exceptions"]),
    }
    coverage = declared(observed)["coverage"]
    expected, expected_coverage = deepcopy(observed), deepcopy(coverage)

    def mutating_factory(shard, *, calendar_ref):
        observed["exceptions"].clear()
        supplement["exceptions"].clear()
        coverage[0]["coverage_end"] = "2027-01-01"
        return CalendarSnapshot(shard, calendar_ref=calendar_ref)

    result = merge(
        observed,
        supplement,
        coverage=coverage,
        mode="SIMULATION_ONLY",
        snapshot_factory=mutating_factory,
    )
    assert result["status"] == "READY"
    assert result["content"] == expected
    assert result["coverage_declaration"] == expected_coverage
