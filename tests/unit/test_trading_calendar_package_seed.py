"""Synthetic in-package calendar seed: loading, coverage and explicit installation.

All data used here is invented. These tests do not prove venue coverage,
source authenticity, calendar correctness, Windows behaviour or a released
wheel; they only exercise the SDK packaging/loader/install contract offline.
"""

import hashlib
import json
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_package_seed as seed_module
from bt_api_py import trading_calendar_store as store_module
from bt_api_py.trading_calendar_package_seed import (
    BINDING_SCHEMA,
    COVERAGE_MANIFEST_SCHEMA,
    DEFAULT_SEED_VERSION,
    SEED_KINDS,
    SEED_VENUES,
    PackageSeedError,
    install_package_seed,
    load_package_seed,
    load_package_seed_from_directory,
    prepare_package_seed_publication,
)
from bt_api_py.trading_calendar_runtime_status import TradingCalendarStatusReader
from bt_api_py.trading_calendar_store import (
    CurrentPrecondition,
    PolicyPublication,
    PublicationResult,
    TradingCalendarStore,
    calendar_snapshot_digest,
)

LABEL = "SYNTHETIC_MOCK"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def seed_directory():
    from importlib import resources

    return resources.files("bt_api_py").joinpath("trading_calendar_seed")


def copied_seed(tmp_path):
    target = tmp_path / "trading_calendar_seed"
    shutil.copytree(str(seed_directory()), target)
    return target


def read_json(path):
    return json.loads(Path(path).read_bytes())


def write_json(path, value):
    # Formatting is irrelevant to the loader; digests are computed by callers.
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rewrite_manifest_digests(root, version=DEFAULT_SEED_VERSION):
    """Recompute layer hashes and the manifest content digest after a file edit."""
    manifest_path = root / version / "manifest.json"
    manifest = read_json(manifest_path)
    for entry in manifest["files"].values():
        path = root / version / entry["path"]
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["content_digest"] = sha({"files": manifest["files"]})
    write_json(manifest_path, manifest)
    return manifest


def seed_store(tmp_path, *, mode="auto", name="calendar"):
    root = Path(os.path.realpath(tmp_path)) / name
    root.mkdir(mode=0o700)
    store = TradingCalendarStore(root)
    store.initialize_review_policy(config_read_succeeded=True, mode=mode)
    return root, store


def other_content(publication="other"):
    return {
        "trading_days": [{"venue": "CFFEX", "day": "2026-01-05", "status": "open"}],
        "trading_hours": [{"venue": "CFFEX", "kind": "future", "product": publication}],
        "exceptions": [],
        "sources": [{"id": "TEST.other", "description": "synthetic other content"}],
    }


def publish_other(store, content, *, publication_id="otherpub", version_id="otherver"):
    policy = store.read_review_policy()
    difference = {"operation": "OTHER"}
    claim = PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        calendar_snapshot_digest(content),
        difference,
        sha(difference),
    )
    return store.publish(
        content,
        publication_id=publication_id,
        version_id=version_id,
        expected_current=CurrentPrecondition.absent(),
        policy_publication=claim,
    )


def store_files(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Loader: coverage and labels
# ---------------------------------------------------------------------------


def test_default_seed_is_versioned_synthetic_and_complete():
    seed = load_package_seed()
    assert seed.seed_version == DEFAULT_SEED_VERSION
    assert seed.synthetic is True
    assert seed.data_origin == LABEL
    assert "not exchange truth" in seed.disclaimer
    assert seed.coverage["venues"] == SEED_VENUES
    assert set(seed.coverage["kinds"]) == set(SEED_KINDS)
    assert seed.coverage["years"] == (2026, 2027)
    assert seed.schema_version == 1
    assert seed.covers_year(2026) and seed.covers_year(2027)
    assert not seed.covers_year(2025)
    assert seed.covers("SHFE", "future") and seed.covers("CFFEX", "option")
    assert not seed.covers("UNKNOWN", "future")


def test_describe_reports_labels_without_raw_rows():
    described = load_package_seed().describe()
    assert described["synthetic"] is True
    assert described["data_origin"] == LABEL
    assert described["disclaimer"]
    assert described["semantics"] == "STORE_INTEGRITY_ONLY"
    assert described["coverage"]["venues"] == list(SEED_VENUES)
    assert described["row_counts"]["trading_days"] > 0
    assert "rows" not in described


def test_every_layer_file_carries_the_synthetic_label():
    root = seed_directory()
    index = read_json(root / "index.json")
    manifest = read_json(root / DEFAULT_SEED_VERSION / "manifest.json")
    for value in (index, manifest):
        assert value["synthetic"] is True
        assert value["data_origin"] == LABEL
        assert value["disclaimer"]
    for entry in manifest["files"].values():
        layer = read_json(root / DEFAULT_SEED_VERSION / entry["path"])
        assert layer["synthetic"] is True
        assert layer["data_origin"] == LABEL
        assert layer["disclaimer"]
        assert layer["records"] == len(layer["rows"])
        assert layer["layer"] in {"sources", "trading_days", "trading_hours", "exceptions"}


def test_materialized_content_has_four_layers_and_all_six_venues():
    seed = load_package_seed()
    content = seed.materialize_content()
    assert set(content) == {"sources", "trading_days", "trading_hours", "exceptions"}
    venues = {row["venue"] for row in content["trading_days"]}
    assert venues == set(SEED_VENUES)
    # 2026 and 2027 are both 365 day years.
    assert len(content["trading_days"]) == len(SEED_VENUES) * 2 * 365
    for venue in SEED_VENUES:
        days = [row for row in content["trading_days"] if row["venue"] == venue]
        assert {row["day"][:4] for row in days} == {"2026", "2027"}
        assert any(row["status"] == "open" for row in days)
        assert any(row["status"] == "closed" for row in days)
    hours = content["trading_hours"]
    assert {(row["venue"], row["kind"]) for row in hours} == {
        (venue, kind) for venue in SEED_VENUES for kind in SEED_KINDS
    }
    assert len(content["exceptions"]) == len(SEED_VENUES) * len(SEED_KINDS)
    source = content["sources"][0]
    assert source["id"] == seed.source_id
    assert "SYNTHETIC" in source["description"].upper()
    for name, layer in content.items():
        for row in layer:
            if name == "sources":
                assert row["id"] == seed.source_id
            else:
                assert seed.source_id in row["source_ids"]


def test_materialized_rows_are_self_consistent():
    content = load_package_seed().materialize_content()
    open_days = {}
    status = {}
    for row in content["trading_days"]:
        open_days.setdefault(row["venue"], []).append(row["day"])
        status[(row["venue"], row["day"])] = row["status"]
    for row in content["trading_hours"]:
        venue, kind = row["venue"], row["kind"]
        open_venue_days = [day for day in open_days[venue] if status[(venue, day)] == "open"]
        assert row["valid_from"] == "2026-01-01" and row["valid_to"] == "2027-12-31"
        assert row["windows_complete"] is True and row["exceptions_complete"] is True
        assert row["contracts"]
        assert row["day_windows"]
        ids = {item["id"] for item in row["day_windows"]}
        assert ids == {"day_am", "day_pm"}
        if venue == "CFFEX":
            assert row["night_windows"] == []
        else:
            assert [item["id"] for item in row["night_windows"]] == ["night"]
        mappings = row["night_mappings"]
        # Every open product-day carries an explicit mapping; a null start date
        # means "no night session".
        assert [item["trading_day"] for item in mappings] == open_venue_days
        if row["night_windows"]:
            previous = None
            for mapping in mappings:
                assert mapping["start_date"] == previous
                previous = mapping["trading_day"]
            assert mappings[0]["start_date"] is None
        else:
            assert all(mapping["start_date"] is None for mapping in mappings)
        assert all(item["source_ids"] == [content["sources"][0]["id"]] for item in mappings)
        assert kind in {"future", "option"}
    for row in content["exceptions"]:
        assert row["action"] in {"close_day", "cancel_window"}
        assert status[(row["venue"], row["trading_day"])] == "open"
        if row["action"] == "close_day":
            assert row["window_id"] is None
        else:
            assert row["window_id"] == "day_pm"


def test_venue_specific_synthetic_holidays_are_materialized():
    content = load_package_seed().materialize_content()
    status = {(row["venue"], row["day"]): row["status"] for row in content["trading_days"]}
    assert status[("SHFE", "2026-01-01")] == "closed"
    assert status[("SHFE", "2026-01-02")] == "open"
    assert status[("SHFE", "2026-02-23")] == "closed"
    assert status[("CFFEX", "2026-02-23")] == "open"


def test_repeated_loads_are_deterministic_and_detached():
    first = load_package_seed()
    second = load_package_seed()
    assert first.content_digest == second.content_digest
    assert first.materialize_content() == second.materialize_content()
    content = first.materialize_content()
    content["trading_days"].clear()
    assert first.materialize_content()["trading_days"]


def test_seed_digests_match_the_store_contract():
    content = load_package_seed().materialize_content()
    staged = prepare_package_seed_publication(load_package_seed())
    assert staged.content_sha256 == hashlib.sha256(canonical(content)).hexdigest()
    assert staged.snapshot_sha256 == calendar_snapshot_digest(
        content,
        coverage_manifest_bytes=staged.coverage_manifest_bytes,
        coverage_manifest_sha256=staged.coverage_manifest_sha256,
        binding_bytes=staged.binding_bytes,
        binding_sha256=staged.binding_sha256,
    )
    assert staged.content_sha256 != staged.snapshot_sha256
    assert staged.content_sha256 == staged.content_sha256.lower()
    assert len(staged.content_sha256) == 64
    assert (
        staged.coverage_manifest_sha256
        == hashlib.sha256(staged.coverage_manifest_bytes).hexdigest()
    )
    assert staged.binding_sha256 == hashlib.sha256(staged.binding_bytes).hexdigest()


def test_coverage_manifest_and_binding_are_canonical_declarations():
    seed = load_package_seed()
    staged = prepare_package_seed_publication(seed)
    manifest = json.loads(staged.coverage_manifest_bytes)
    binding = json.loads(staged.binding_bytes)
    assert staged.coverage_manifest_bytes == canonical(manifest)
    assert staged.binding_bytes == canonical(binding)
    assert manifest == {
        "schema": COVERAGE_MANIFEST_SCHEMA,
        "snapshot_sha256": staged.content_sha256,
        "coverage": [
            {
                "venue": venue,
                "coverage_start": "2026-01-01",
                "coverage_end": "2027-12-31",
                "semantics": "DECLARED_INCLUSIVE",
            }
            for venue in SEED_VENUES
        ],
    }
    venues = [row["venue"] for row in manifest["coverage"]]
    assert venues == sorted(venues)
    assert binding == {
        "schema": BINDING_SCHEMA,
        "snapshot_sha256": staged.content_sha256,
        "manifest_sha256": staged.coverage_manifest_sha256,
    }


def test_staged_publication_ids_are_store_safe_and_deterministic():
    seed = load_package_seed()
    first = prepare_package_seed_publication(seed)
    second = prepare_package_seed_publication()
    assert first.publication_id == seed.seed_version
    assert first.version_id == seed.seed_version
    assert store_module._identifier(first.publication_id)
    assert store_module._identifier(first.version_id)
    assert first.content_sha256 == second.content_sha256
    assert first.content["sources"][0]["id"] == seed.source_id


# ---------------------------------------------------------------------------
# Loader: finite refusal of unknown or damaged seeds
# ---------------------------------------------------------------------------


def test_unknown_version_is_refused():
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_VERSION_UNKNOWN$"):
        load_package_seed("seed-1999-2000-v1")


def test_unsupported_manifest_schema_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    manifest_path = root / DEFAULT_SEED_VERSION / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["schema"] = "bt_api.calendar-package-seed.manifest/999"
    write_json(manifest_path, manifest)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_SCHEMA_UNSUPPORTED$"):
        load_package_seed_from_directory(root)


def test_non_synthetic_seed_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    manifest_path = root / DEFAULT_SEED_VERSION / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["synthetic"] = False
    write_json(manifest_path, manifest)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_NOT_SYNTHETIC$"):
        load_package_seed_from_directory(root)


def test_missing_layer_file_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    (root / DEFAULT_SEED_VERSION / "trading_hours.json").unlink()
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_FILE_MISSING$"):
        load_package_seed_from_directory(root)


def test_layer_digest_mismatch_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    path = root / DEFAULT_SEED_VERSION / "trading_days.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_DIGEST_MISMATCH$"):
        load_package_seed_from_directory(root)


def test_manifest_content_digest_mismatch_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    manifest_path = root / DEFAULT_SEED_VERSION / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["content_digest"] = "0" * 64
    write_json(manifest_path, manifest)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_DIGEST_MISMATCH$"):
        load_package_seed_from_directory(root)


def test_malformed_layer_json_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    path = root / DEFAULT_SEED_VERSION / "exceptions.json"
    path.write_bytes(b"{")
    rewrite_manifest_digests(root)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_FILE_INVALID$"):
        load_package_seed_from_directory(root)


def test_layer_with_wrong_label_or_config_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    path = root / DEFAULT_SEED_VERSION / "sources.json"
    value = read_json(path)
    value["synthetic"] = False
    write_json(path, value)
    rewrite_manifest_digests(root)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_NOT_SYNTHETIC$"):
        load_package_seed_from_directory(root)


@pytest.mark.parametrize(
    "mutation",
    [
        ("venues", ["CFFEX", "CZCE", "DCE", "GFEX", "INE"]),
        ("kinds", ["future"]),
        ("years", [2026]),
        ("layers", ["sources", "trading_days", "trading_hours"]),
    ],
)
def test_incomplete_coverage_is_refused(tmp_path, mutation):
    root = copied_seed(tmp_path)
    key, value = mutation
    manifest_path = root / DEFAULT_SEED_VERSION / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["coverage"][key] = value
    write_json(manifest_path, manifest)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_COVERAGE_INCOMPLETE$"):
        load_package_seed_from_directory(root)


def test_missing_venue_in_layer_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    path = root / DEFAULT_SEED_VERSION / "trading_hours.json"
    value = read_json(path)
    value["rows"] = [row for row in value["rows"] if row["venue"] != "GFEX"]
    value["records"] = len(value["rows"])
    write_json(path, value)
    rewrite_manifest_digests(root)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_COVERAGE_INCOMPLETE$"):
        load_package_seed_from_directory(root)


def test_invalid_index_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    index = read_json(root / "index.json")
    index["default"] = "seed-not-listed"
    write_json(root / "index.json", index)
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_INDEX_INVALID$"):
        load_package_seed_from_directory(root)


def test_missing_index_is_refused(tmp_path):
    root = copied_seed(tmp_path)
    (root / "index.json").unlink()
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_INDEX_MISSING$"):
        load_package_seed_from_directory(root)


# ---------------------------------------------------------------------------
# Installation: uses the managed store, never invents a writer
# ---------------------------------------------------------------------------


def test_dry_run_stages_without_touching_the_store(tmp_path):
    root = tmp_path / "not-created"
    result = install_package_seed(root, dry_run=True)
    staged = prepare_package_seed_publication()
    assert (result.status, result.reason) == ("STAGED", "SEED_STAGED")
    assert result.synthetic is True and result.data_origin == LABEL
    assert not root.exists()
    assert result.content_sha256 == staged.content_sha256
    assert result.coverage_manifest_sha256 == staged.coverage_manifest_sha256
    assert result.binding_sha256 == staged.binding_sha256
    # Staging declares coverage but has not published it to any store.
    assert result.coverage_status is None


def test_install_into_uninitialized_store_is_refused_without_writes(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir()
    before = store_files(root)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "STORE_NOT_INITIALIZED")
    assert result.synthetic is True
    assert store_files(root) == before


def test_install_into_auto_store_publishes_and_labels(tmp_path):
    root, store = seed_store(tmp_path)
    result = install_package_seed(root)
    seed = load_package_seed()
    staged = prepare_package_seed_publication(seed)
    assert (result.status, result.reason) == ("INSTALLED", "SEED_INSTALLED")
    assert (result.visibility, result.verification) == ("COMMITTED", "VISIBLE")
    assert result.publication_id == seed.seed_version
    assert result.version_id == seed.seed_version
    assert result.coverage_status == "DECLARED_UNVERIFIED"
    assert result.coverage_manifest_sha256 == staged.coverage_manifest_sha256
    assert result.binding_sha256 == staged.binding_sha256
    assert result.synthetic is True and result.data_origin == LABEL
    current = store.read_current()
    assert current.publication_id == seed.seed_version
    assert current.content_sha256 == staged.content_sha256
    assert current.content == seed.materialize_content()
    assert current.coverage_status == "DECLARED_UNVERIFIED"
    assert current.coverage_manifest_bytes == staged.coverage_manifest_bytes
    assert current.coverage_manifest_sha256 == staged.coverage_manifest_sha256
    assert current.binding_bytes == staged.binding_bytes
    assert current.binding_sha256 == staged.binding_sha256
    event = read_json(root / "publications" / f"{seed.seed_version}.json")
    assert event["review_policy"]["difference"]["synthetic"] is True
    assert event["review_policy"]["difference"]["data_origin"] == LABEL
    assert event["review_policy"]["difference"]["seed_version"] == seed.seed_version
    assert event["review_policy"]["difference"]["coverage_status"] == "DECLARED_UNVERIFIED"
    described = result.describe()
    assert described["synthetic"] is True
    assert described["data_origin"] == LABEL
    assert described["disclaimer"] == seed.disclaimer
    assert described["semantics"] == "STORE_INTEGRITY_ONLY"
    assert described["venue_semantics"] == "VENUE_SEMANTICS_NOT_VERIFIED"
    assert described["seed_status"] == "SEED_SYNTHETIC_UNVERIFIED"
    assert described["coverage_manifest_sha256"] == staged.coverage_manifest_sha256
    assert described["binding_sha256"] == staged.binding_sha256


def test_install_does_not_treat_orphan_versions_as_absent(tmp_path):
    root, _ = seed_store(tmp_path)
    (root / "versions" / "orphan.json").write_bytes(b"{}")
    before = store_files(root)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "PUBLISH_REJECTED")
    assert not (root / "CURRENT.json").exists()
    assert store_files(root) == before


def test_install_reports_and_keeps_a_pending_operation(tmp_path):
    root, _ = seed_store(tmp_path)
    pending = root / "PENDING.json"
    pending.write_bytes(b"{}")
    before = store_files(root)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("UNKNOWN", "PUBLISH_UNKNOWN")
    assert result.fence == "PRESENT"
    assert pending.read_bytes() == b"{}"
    assert not (root / "CURRENT.json").exists()
    assert store_files(root) == before


def test_installed_seed_is_queryable_through_the_public_reader(tmp_path):
    root, store = seed_store(tmp_path)
    installed = install_package_seed(root)
    assert installed.status == "INSTALLED"
    assert store.read_current().coverage_status == "DECLARED_UNVERIFIED"

    reader = TradingCalendarStatusReader()
    reader.configure({"ctp": str(root)})
    base = {
        "venue": "CFFEX",
        "kind": "future",
        "product": "SYNTH_CFFEX_FUTURE",
        "contract": "SYNTH_CFFEX_FUTURE_MAIN",
    }

    open_day = reader.query("ctp", mode="SIMULATION_ONLY", trading_day=date(2026, 1, 5), **base)
    assert (open_day.status, open_day.reason) == ("COMPLETE", "EXPLICIT_DATA")
    assert [(window.window_id, window.phase) for window in open_day.windows] == [
        ("day_am", "continuous"),
        ("day_pm", "continuous"),
    ]
    assert all(window.trading_day == date(2026, 1, 5) for window in open_day.windows)
    assert [
        (window.start_local.hour, window.start_local.minute) for window in open_day.windows
    ] == [(9, 30), (13, 0)]
    assert [(window.end_local.hour, window.end_local.minute) for window in open_day.windows] == [
        (11, 30),
        (15, 0),
    ]
    assert open_day.coverage_status == "DECLARED_UNVERIFIED"
    assert open_day.environment == "SIMULATION_ONLY"
    assert open_day.account_use == "NOT_AUTHORIZED"
    assert open_day.calendar_ref == installed.version_id

    # 2026-01-01 is a declared closed day: COMPLETE with no windows, not a gap.
    closed_day = reader.query("ctp", mode="SIMULATION_ONLY", trading_day=date(2026, 1, 1), **base)
    assert (closed_day.status, closed_day.reason) == ("COMPLETE", "EXPLICIT_DATA")
    assert closed_day.windows == ()

    unknown_scope = reader.query(
        "ctp",
        mode="SIMULATION_ONLY",
        trading_day=date(2026, 1, 5),
        venue="CFFEX",
        kind="future",
        product="NOT_A_SEED_PRODUCT",
        contract="NOT_A_SEED_CONTRACT",
    )
    assert unknown_scope.status == "UNKNOWN"
    assert unknown_scope.windows == ()

    for outside in (date(2025, 12, 31), date(2028, 1, 3)):
        uncovered = reader.query("ctp", mode="SIMULATION_ONLY", trading_day=outside, **base)
        assert (uncovered.status, uncovered.reason) == ("UNKNOWN", "COVERAGE_UNAVAILABLE")


def test_reinstall_is_idempotent_and_leaves_no_new_artifacts(tmp_path):
    root, store = seed_store(tmp_path)
    first = install_package_seed(root)
    assert first.status == "INSTALLED"
    before = store_files(root)
    second = install_package_seed(root)
    assert (second.status, second.reason) == ("UNCHANGED", "ALREADY_CURRENT")
    assert second.coverage_status == "DECLARED_UNVERIFIED"
    assert second.coverage_manifest_sha256 == first.coverage_manifest_sha256
    assert second.binding_sha256 == first.binding_sha256
    assert second.publication_id == store.read_current().publication_id
    assert store_files(root) == before


def test_install_never_overwrites_non_seed_current(tmp_path):
    root, store = seed_store(tmp_path)
    assert publish_other(store, other_content()).visibility == "COMMITTED"
    before = store_files(root)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "CURRENT_NOT_SEED")
    assert store.read_current().publication_id == "otherpub"
    assert store_files(root) == before


def test_install_refuses_a_different_seed_labelled_current(tmp_path):
    root, store = seed_store(tmp_path)
    content = load_package_seed().materialize_content()
    content["sources"] = [
        {
            "id": "SYNTHETIC_MOCK.package_seed.seed-other",
            "description": "SYNTHETIC_MOCK other seed; not exchange truth",
        }
    ]
    content["trading_days"] = content["trading_days"][:1]
    content["trading_hours"] = content["trading_hours"][:1]
    content["exceptions"] = []
    assert publish_other(store, content).visibility == "COMMITTED"
    before = store_files(root)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "SEED_CONTENT_CONFLICT")
    assert store_files(root) == before


def test_install_into_manual_store_requires_review(tmp_path):
    root, _ = seed_store(tmp_path, mode="manual")
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "MANUAL_REVIEW_REQUIRED")
    assert not (root / "CURRENT.json").exists()


def test_install_reports_unknown_publish_without_retry(tmp_path, monkeypatch):
    root, _ = seed_store(tmp_path)
    calls = []

    def unknown(self, content, **kwargs):
        calls.append(kwargs["publication_id"])
        return PublicationResult(
            "COMMIT_UNKNOWN",
            "UNCONFIRMED",
            kwargs["publication_id"],
            kwargs["version_id"],
            "simulated unknown",
            fence="UNKNOWN",
        )

    monkeypatch.setattr(TradingCalendarStore, "publish", unknown)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("UNKNOWN", "PUBLISH_UNKNOWN")
    assert result.visibility == "COMMIT_UNKNOWN"
    assert calls == [load_package_seed().seed_version]
    assert not (root / "CURRENT.json").exists()


def test_install_reports_unknown_after_unconfirmed_verification(tmp_path, monkeypatch):
    root, store = seed_store(tmp_path)
    original = TradingCalendarStore.publish
    calls = []

    def counted(self, content, **kwargs):
        calls.append(kwargs["publication_id"])
        return original(self, content, **kwargs)

    monkeypatch.setattr(TradingCalendarStore, "publish", counted)
    monkeypatch.setattr(TradingCalendarStore, "verify_publication", lambda self, value: "UNKNOWN")
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("UNKNOWN", "POST_COMMIT_UNCONFIRMED")
    assert calls == [load_package_seed().seed_version]
    assert (root / "CURRENT.json").exists()
    # The visible publication remains; the installer did not retry or delete it.
    assert store.read_current().publication_id == load_package_seed().seed_version


def test_install_refuses_to_guess_a_corrupt_current(tmp_path, monkeypatch):
    root, _ = seed_store(tmp_path)
    (root / "CURRENT.json").write_bytes(b"{}")
    calls = []

    def should_not_publish(self, content, **kwargs):
        calls.append(kwargs["publication_id"])
        raise AssertionError("publish must not run on an unreadable CURRENT")

    monkeypatch.setattr(TradingCalendarStore, "publish", should_not_publish)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("UNKNOWN", "STORE_STATE_UNKNOWN")
    assert calls == []


def test_install_reports_store_rejection_without_retry(tmp_path, monkeypatch):
    root, _ = seed_store(tmp_path)
    calls = []

    def rejected(self, content, **kwargs):
        calls.append(kwargs["publication_id"])
        return PublicationResult(
            "NOT_COMMITTED",
            "UNCONFIRMED",
            kwargs["publication_id"],
            kwargs["version_id"],
            "simulated rejection",
        )

    monkeypatch.setattr(TradingCalendarStore, "publish", rejected)
    result = install_package_seed(root)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "PUBLISH_REJECTED")
    assert result.detail == "simulated rejection"
    assert calls == [load_package_seed().seed_version]


@pytest.mark.parametrize(
    "value",
    ["relative/calendar", "~/calendar", "", " calendar", "cal\x00endar", b"/tmp/calendar", 7, None],
)
def test_install_rejects_non_absolute_roots_without_writes(value, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = install_package_seed(value)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "SEED_ROOT_INVALID")
    assert not (tmp_path / "home").exists()


def test_install_accepts_absolute_root_with_spaces_and_chinese(tmp_path):
    root = tmp_path / "日历 数据" / "calendar"
    root.mkdir(parents=True)
    result = install_package_seed(root, dry_run=True)
    assert (result.status, result.reason) == ("STAGED", "SEED_STAGED")
    assert result.store_root == str(root)


def test_install_does_not_expand_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("BT_SEED_ROOT", str(tmp_path / "expanded"))
    literal = str(tmp_path / "$BT_SEED_ROOT")
    result = install_package_seed(literal, dry_run=True)
    assert (result.status, result.reason) == ("STAGED", "SEED_STAGED")
    assert result.store_root == literal
    assert not (tmp_path / "expanded").exists()
    assert not Path(literal).exists()


def test_version_argument_is_selected_explicitly(tmp_path):
    with pytest.raises(PackageSeedError, match="^PACKAGE_SEED_VERSION_UNKNOWN$"):
        install_package_seed(tmp_path / "calendar", version="seed-unknown", dry_run=True)


def test_seed_module_has_no_store_import_at_load_time():
    # The loader is usable without the store; installation imports it lazily.
    source = Path(seed_module.__file__).read_text(encoding="utf-8")
    assert "trading_calendar_store import" not in source


def test_seed_files_are_not_inside_a_tests_directory():
    root = Path(os.fspath(seed_directory()))
    assert "tests" not in root.parts
    assert (root / "index.json").is_file()
    assert (root / DEFAULT_SEED_VERSION / "manifest.json").is_file()
