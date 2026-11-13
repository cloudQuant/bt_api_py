"""Offline bound-session checks using the real temporary store and fake input."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone

from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_store as store_module
from bt_api_py.trading_calendar_binding import (
    GateDecision,
    SimulationCalendarBinding,
    _declared_mock_windows,
)
from bt_api_py.trading_calendar_bound_session_simulation import evaluate_bound_session
from tests.unit.test_trading_calendar_binding import FakeGate
from tests.unit.test_trading_calendar_mixed_simulation_version import (
    capture,
    save_candidate,
    template,
)
from tests.unit.test_trading_calendar_runtime_mixed_query import FS, SCOPE, publish
from tests.unit.test_trading_calendar_session_simulation import Store, dataset, store_snapshot

UTC = timezone.utc  # noqa: UP017 - Python 3.9
DAY = date(2027, 1, 4)


def bind(store):
    return SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=FakeGate(),
        route="route-a",
        source_generation="gen-1",
        trading_day=date(2027, 1, 1),
        last_sequence=1,
    )


def evaluate(bound, at, *, owner=DAY, **changes):
    scope = {key: value for key, value in SCOPE.items() if key != "trading_day"}
    scope.update(changes)
    return evaluate_bound_session(bound, **scope, owner_day=owner, at_utc=at)


def publish_changed(root, store):
    capture(root, "c" * 32, "d" * 32, "日盘 09:00-10:30")
    record = template(
        root,
        oid="c" * 32,
        rid="d" * 32,
        a={
            "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
            "NIGHT": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        },
    )
    digest = mixed.template_digest(
        record, root=str(root), candidate_root=str(root), filesystem=FS, include_trace=True
    )
    materialized = mixed.materialize_template(
        record,
        root=str(root),
        candidate_root=str(root),
        filesystem=FS,
        alignment="REPEAT_OVER_MOCK_OWNER_DAY_MAPPING",
        expected_evidence_sha256=digest,
        candidate_id="3" * 32,
    )
    candidate = save_candidate(root, "3" * 32, materialized["input"])
    composite = candidate["bundle"]["report"]["composite"]
    content = composite["snapshot"]
    artifacts = {
        "coverage_manifest_bytes": mixed._canonical(composite["manifest"]),
        "coverage_manifest_sha256": composite["manifest_sha256"],
        "binding_bytes": mixed._canonical(composite["binding"]),
        "binding_sha256": composite["binding_sha256"],
        "simulation_provenance": materialized["provenance"],
    }
    policy = store.read_review_policy()
    current = store.read_current()
    difference = {}
    claim = store_module.PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        store_module.calendar_snapshot_digest(content, **artifacts),
        difference,
        store_module._sha(store_module._canonical(difference)),
    )
    result = store.publish(
        content,
        publication_id="MOCK_pub2",
        version_id="MOCK_ver2",
        **artifacts,
        expected_current=store_module.CurrentPrecondition.matching(
            current.publication_id, current.publication_sha256
        ),
        policy_publication=claim,
    )
    assert result.visibility == "COMMITTED", result


def test_pins_version_and_per_window_source_until_gated_switch(tmp_path):
    publish(tmp_path, mixed_version=True)
    store = store_module.TradingCalendarStore(str(tmp_path))
    bound = bind(store)
    at = datetime(2027, 1, 4, 1, 30, tzinfo=UTC)
    before = evaluate(bound, at)
    assert (before.phase, before.binding.calendar_ref) == ("CONTINUOUS", "MOCK_ver")
    assert before.window_provenance["source_type"] == "AKSHARE"
    assert before.window_provenance["fields"]["end"]["basis"] == "AKSHARE_SYNTAX_CANDIDATE"
    assert before.window.end_utc == datetime(2027, 1, 4, 2, tzinfo=UTC)
    publish_changed(tmp_path, store)
    assert evaluate(bound, at) == before
    rejected = bound.activate(GateDecision(False, "route-a", "gen-1", DAY, 2, "switch"))
    assert rejected.outcome == "GATE_REJECTED" and evaluate(bound, at) == before
    changed = bound.activate(GateDecision(True, "route-a", "gen-1", DAY, 2, "switch"))
    assert changed.outcome == "ACTIVATED"
    after = evaluate(bound, at)
    assert (after.phase, after.binding.calendar_ref) == ("CONTINUOUS", "MOCK_ver2")
    assert after.window.end_utc == datetime(2027, 1, 4, 2, 30, tzinfo=UTC)
    assert after.window_provenance["source_type"] == "AKSHARE"
    assert after.binding.version_sha256 != before.binding.version_sha256


def test_night_owner_unknown_scopes_and_missing_binding(tmp_path):
    publish(tmp_path, mixed_version=True)
    bound = bind(store_module.TradingCalendarStore(str(tmp_path)))
    night = datetime(2027, 1, 1, 13, 30, tzinfo=UTC)
    result = evaluate(bound, night)
    assert result.phase == "CONTINUOUS" and result.owner_day == DAY
    assert result.window_provenance["source_type"] == "MOCK"
    assert result.window_provenance["scope"] == "NIGHT"
    assert evaluate(bound, night, owner=date(2027, 1, 1)).phase == "UNKNOWN"
    assert evaluate(bound, datetime(2027, 1, 4, 2, tzinfo=UTC)).phase == "UNKNOWN"
    assert evaluate(bound, night, venue="DCE").phase == "UNKNOWN"
    assert evaluate(bound, night, contract="missing").phase == "UNKNOWN"
    assert evaluate(bound, night.replace(tzinfo=None)).phase == "UNKNOWN"
    option_time = datetime(2027, 1, 4, 1, 30, tzinfo=UTC)
    option = evaluate(
        bound, option_time, kind="option", product="poption", contract="mock_shfe_option_contract"
    )
    assert option.phase == "CONTINUOUS"
    assert option.window_provenance["source_type"] == "MOCK"
    assert option.window_provenance["declaration"] == "PINNED_RULE_AND_OWNER_DAY"
    other = evaluate(bound, option_time, venue="DCE", contract="mock_dce_future_contract")
    assert other.phase == "CONTINUOUS" and other.window_provenance["source_type"] == "MOCK"
    assert (
        evaluate_bound_session(
            None,
            venue="SHFE",
            kind="future",
            product="p",
            contract="c",
            owner_day=DAY,
            at_utc=night,
        ).phase
        == "UNKNOWN"
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    assert evaluate(bind(store_module.TradingCalendarStore(str(empty))), night).phase == "UNKNOWN"


def test_missing_and_ambiguous_window_provenance_are_unknown(tmp_path, monkeypatch):
    publish(tmp_path, mixed_version=True)
    bound = bind(store_module.TradingCalendarStore(str(tmp_path)))
    original = bound.query
    at = datetime(2027, 1, 4, 1, 30, tzinfo=UTC)

    def altered(*, duplicate, **kwargs):
        result = original(**kwargs)
        provenance = deepcopy(result.simulation_provenance)
        if duplicate:
            provenance["windows"].append(deepcopy(provenance["windows"][0]))
        else:
            provenance["windows"] = []
        return replace(result, simulation_provenance=provenance)

    for duplicate in (True, False):
        monkeypatch.setattr(
            bound, "query", lambda _duplicate=duplicate, **kw: altered(duplicate=_duplicate, **kw)
        )
        result = evaluate(bound, at)
        assert (result.phase, result.reason) == ("UNKNOWN", "WINDOW_PROVENANCE_UNAVAILABLE")


def test_switch_after_capture_does_not_mix_window_and_version(tmp_path, monkeypatch):
    publish(tmp_path, mixed_version=True)
    store = store_module.TradingCalendarStore(str(tmp_path))
    bound = bind(store)
    publish_changed(tmp_path, store)
    original = bound.query

    def switch_after_capture(**kwargs):
        captured = original(**kwargs)
        assert (
            bound.activate(GateDecision(True, "route-a", "gen-1", DAY, 2, "switch")).outcome
            == "ACTIVATED"
        )
        return captured

    monkeypatch.setattr(bound, "query", switch_after_capture)
    result = evaluate(bound, datetime(2027, 1, 4, 1, 30, tzinfo=UTC))
    assert result.binding.calendar_ref == "MOCK_ver"
    assert result.window.end_utc == datetime(2027, 1, 4, 2, tzinfo=UTC)
    assert result.window_provenance["source_type"] == "AKSHARE"
    assert bound.status().calendar_ref == "MOCK_ver2"


def test_pure_mock_day_night_option_and_venue(tmp_path):
    publish(tmp_path, mixed_version=False)
    bound = bind(store_module.TradingCalendarStore(str(tmp_path)))
    at = datetime(2027, 1, 4, 1, 30, tzinfo=UTC)
    for scope in (
        {},
        {"kind": "option", "product": "poption", "contract": "mock_shfe_option_contract"},
        {"venue": "DCE", "contract": "mock_dce_future_contract"},
    ):
        result = evaluate(bound, at, **scope)
        assert result.phase == "CONTINUOUS"
        assert result.window_provenance["source_type"] == "MOCK"
        assert result.window_provenance["source_authentication"] == "NOT_PROVEN"
    night = datetime(2027, 1, 1, 13, 30, tzinfo=UTC)
    result = evaluate(
        bound, night, kind="option", product="poption", contract="mock_shfe_option_contract"
    )
    assert result.phase == "CONTINUOUS" and result.window_provenance["scope"] == "NIGHT"
    assert evaluate(bound, night, owner=date(2027, 1, 1)).phase == "UNKNOWN"


def test_mock_attribution_requires_unique_rules_and_explicit_source_rows(tmp_path):
    publish(tmp_path, mixed_version=False)
    store = store_module.TradingCalendarStore(str(tmp_path))
    bound = bind(store)
    captured = bound.query(**SCOPE)
    content = store.read_current().content
    assert _declared_mock_windows(content, SCOPE, captured.result) is not None
    for layer in ("trading_hours", "trading_days"):
        modified = deepcopy(content)
        row = next(
            row
            for row in modified[layer]
            if row["venue"] == "SHFE" and (layer != "trading_days" or row["day"] == DAY.isoformat())
        )
        row["source_ids"].append("other")
        assert _declared_mock_windows(modified, SCOPE, captured.result) is None
    modified = deepcopy(content)
    row = next(
        row
        for row in modified["trading_hours"]
        if row["venue"] == "SHFE" and row["kind"] == "future"
    )
    row["night_mappings"][-1]["source_ids"].append("other")
    assert _declared_mock_windows(modified, SCOPE, captured.result) is None
    modified = deepcopy(content)
    modified["trading_hours"].append(
        deepcopy(
            next(
                row
                for row in modified["trading_hours"]
                if row["venue"] == "SHFE" and row["kind"] == "future"
            )
        )
    )
    assert _declared_mock_windows(modified, SCOPE, captured.result) is None
    modified = deepcopy(content)
    row = next(
        row
        for row in modified["trading_hours"]
        if row["venue"] == "SHFE" and row["kind"] == "future"
    )
    row["day_windows"][0]["start"] = "08:00"
    assert _declared_mock_windows(modified, SCOPE, captured.result) is None
    modified = deepcopy(content)
    modified["exceptions"].append(
        {
            "venue": "SHFE",
            "kind": "future",
            "product": "pfuture",
            "contract": "mock_shfe_future_contract",
            "trading_day": DAY.isoformat(),
            "action": "close_day",
            "window_id": None,
            "source_ids": ["other"],
        }
    )
    assert _declared_mock_windows(modified, SCOPE, captured.result) is None


def test_pure_mock_stays_bound_until_switch_to_mixed_version(tmp_path):
    publish(tmp_path, mixed_version=False)
    store = store_module.TradingCalendarStore(str(tmp_path))
    bound = bind(store)
    at = datetime(2027, 1, 4, 1, 30, tzinfo=UTC)
    before = evaluate(bound, at)
    assert before.window_provenance["source_type"] == "MOCK"
    publish_changed(tmp_path, store)
    assert evaluate(bound, at) == before
    assert (
        bound.activate(GateDecision(True, "route-a", "gen-1", DAY, 2, "switch")).outcome
        == "ACTIVATED"
    )
    after = evaluate(bound, at)
    assert after.phase == "CONTINUOUS" and after.binding.calendar_ref == "MOCK_ver2"
    assert after.window_provenance["source_type"] == "AKSHARE"


def test_mock_night_crosses_midnight_with_explicit_owner_day():
    content = dataset()
    content["sources"][0]["id"] = "MOCK.simulation_only"
    for row in content["trading_days"] + content["trading_hours"] + content["exceptions"]:
        row["source_ids"] = ["MOCK.simulation_only"]
        for mapping in row.get("night_mappings", []):
            mapping["source_ids"] = ["MOCK.simulation_only"]
    night = content["trading_hours"][0]["night_windows"][1]
    night.update(end="01:00", end_day_offset=1)
    bound = bind(Store(store_snapshot(content)))
    at = datetime(2026, 6, 5, 16, 30, tzinfo=UTC)
    result = evaluate_bound_session(
        bound,
        venue="SHFE",
        kind="future",
        product="rb",
        contract="rb2601",
        owner_day=date(2026, 6, 8),
        at_utc=at,
    )
    assert result.phase == "CONTINUOUS" and result.window_provenance["scope"] == "NIGHT"
    assert result.window.end_utc == datetime(2026, 6, 5, 17, tzinfo=UTC)
