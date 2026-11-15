"""Offline CURRENT queries against published, simulated local snapshots."""

from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_runtime_status as runtime
from bt_api_py import trading_calendar_store as store_module
from tests.unit.test_trading_calendar_mixed_simulation_version import (
    capture,
    save_candidate,
    template,
    template_fixture,
)

FS = "TRUSTED_LOCAL_POSIX"
DAY = date(2027, 1, 4)
SCOPE = {
    "venue": "SHFE",
    "kind": "future",
    "product": "pfuture",
    "contract": "mock_shfe_future_contract",
    "trading_day": DAY,
}


def publish(root, *, mixed_version):
    shown = save_candidate(root, "1" * 32, template_fixture())
    provenance = None
    if mixed_version:
        # capture uses the CTP collector with an injected fake response, no network.
        capture(root, "a" * 32, "b" * 32, "日盘 09:00-10:00")
        record = template(
            root,
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
            candidate_id="2" * 32,
        )
        provenance = materialized["provenance"]
        shown = save_candidate(root, "2" * 32, materialized["input"])
    composite = shown["bundle"]["report"]["composite"]
    content = composite["snapshot"]
    artifacts = {
        "coverage_manifest_bytes": mixed._canonical(composite["manifest"]),
        "coverage_manifest_sha256": composite["manifest_sha256"],
        "binding_bytes": mixed._canonical(composite["binding"]),
        "binding_sha256": composite["binding_sha256"],
        "simulation_provenance": provenance,
    }
    store = store_module.TradingCalendarStore(str(root))
    policy = store.initialize_review_policy(config_read_succeeded=True)
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
        publication_id="MOCK_pub",
        version_id="MOCK_ver",
        **artifacts,
        expected_current=store_module.CurrentPrecondition.absent(),
        policy_publication=claim,
    )
    assert result.visibility == "COMMITTED", result


@pytest.fixture
def published(tmp_path):
    publish(tmp_path, mixed_version=True)
    reader = runtime.TradingCalendarStatusReader()
    reader.configure({"route": str(tmp_path)})
    return reader, tmp_path


def query(reader, **scope):
    return reader.query("route", mode="SIMULATION_ONLY", **{**SCOPE, **scope})


def test_mixed_windows_are_attributed_from_same_published_snapshot(published):
    reader, root = published
    result = query(reader)
    assert (result.status, result.source_classification, result.data_origin) == (
        "COMPLETE",
        "AKSHARE_MOCK",
        "AKSHARE_MOCK",
    )
    assert set(result.source_ids) == {"AKSHARE.simulation_unverified", "MOCK.simulation_only"}
    assert (result.bound, result.observation, result.account_use) == (
        "NOT_BOUND",
        "CURRENT_NOT_PINNED",
        "NOT_AUTHORIZED",
    )
    stored = runtime.TradingCalendarStore(str(root)).read_current()
    evidence = result.simulation_provenance
    assert result.version_sha256 == stored.version_sha256
    assert evidence["evidence_locator"] == stored.simulation_provenance["evidence_locator"]
    assert evidence["evidence_locator"]["observation_id"] == "a" * 32
    assert {w["window_id"] for w in evidence["windows"]} == {w.window_id for w in result.windows}
    day = next(w for w in evidence["windows"] if w["source_type"] == "AKSHARE")
    night = next(w for w in evidence["windows"] if w["source_type"] == "MOCK")
    assert day["fields"]["start"] == {"value": "09:00", "basis": "AKSHARE_SYNTAX_CANDIDATE"}
    assert night["fields"]["start"]["basis"] == "MOCK_ASSUMPTION"
    for row in (day, night):
        assert row["simulation"]["phase"]["basis"] == "MOCK_ASSUMPTION"
        assert row["simulation"]["local_date"] == {"value": "UNKNOWN", "basis": "UNKNOWN"}
        assert row["simulation"]["night_ownership"]["basis"] == "UNKNOWN"
        assert all(value == "UNKNOWN" for value in row["observation"].values())
    evidence["windows"].clear()
    assert len(query(reader).simulation_provenance["windows"]) == 2


def test_scope_filter_and_legacy_mock_compatibility(published, tmp_path):
    reader, _ = published
    day_only = query(reader, trading_day=date(2027, 1, 1))
    assert day_only.status == "COMPLETE"
    assert [w["source_type"] for w in day_only.simulation_provenance["windows"]] == ["AKSHARE"]
    option = query(reader, kind="option", product="poption", contract="mock_shfe_option_contract")
    assert (option.status, option.source_classification, option.simulation_provenance) == (
        "COMPLETE",
        "DECLARED_MOCK",
        None,
    )
    assert option.data_origin == "AKSHARE_MOCK"
    assert query(reader, contract="missing").status == "UNKNOWN"
    pure_root = tmp_path / "pure"
    pure_root.mkdir()
    publish(pure_root, mixed_version=False)
    reader.configure({"route": str(pure_root)})
    pure = query(reader)
    assert (
        pure.status,
        pure.source_classification,
        pure.data_origin,
        pure.simulation_provenance,
    ) == ("COMPLETE", "DECLARED_MOCK", "UNSPECIFIED", None)


def test_tamper_and_reconfiguration_are_unknown(published, monkeypatch):
    reader, root = published
    original = runtime.TradingCalendarStore.read_current_with_consistent_receipts

    def altered(store, **kwargs):
        current = original(store, **kwargs)
        provenance = deepcopy(current.snapshot.simulation_provenance)
        provenance["windows"][0]["simulation"]["local_date"]["basis"] = "MOCK_ASSUMPTION"
        return replace(
            current, snapshot=replace(current.snapshot, simulation_provenance=provenance)
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            runtime.TradingCalendarStore, "read_current_with_consistent_receipts", altered
        )
        assert query(reader).reason == "CALENDAR_UNAVAILABLE"

    def reconfigure(store, **kwargs):
        current = original(store, **kwargs)
        reader.configure({"route": str(root)})
        return current

    monkeypatch.setattr(
        runtime.TradingCalendarStore, "read_current_with_consistent_receipts", reconfigure
    )
    result = query(reader)
    assert (result.status, result.reason, result.simulation_provenance) == (
        "UNKNOWN",
        "CONFIGURATION_CHANGED",
        None,
    )


def test_changed_current_after_read_does_not_mix_snapshots(published, monkeypatch):
    reader, root = published
    original = runtime.TradingCalendarStore.read_current_with_consistent_receipts
    pointer = root / "CURRENT.json"
    old_pointer = pointer.read_bytes()

    def invalidate_after_read(store, **kwargs):
        current = original(store, **kwargs)
        pointer.write_bytes(b"invalid")
        return current

    with monkeypatch.context() as patch:
        patch.setattr(
            runtime.TradingCalendarStore,
            "read_current_with_consistent_receipts",
            invalidate_after_read,
        )
        result = query(reader)
        assert result.status == "COMPLETE"
        assert result.simulation_provenance["evidence_locator"]["observation_id"] == "a" * 32
    assert query(reader).reason == "CURRENT_UNAVAILABLE"
    pointer.write_bytes(old_pointer)
    version = root / "versions" / "MOCK_ver.json"
    data = version.read_bytes()
    version.write_bytes(data.replace(b'"source_type":"AKSHARE"', b'"source_type":"MOCK"', 1))
    assert version.read_bytes() != data
    assert query(reader).status == "UNKNOWN"
