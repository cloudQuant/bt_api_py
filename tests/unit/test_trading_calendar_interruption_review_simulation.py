"""Detached MOCK review material over a real local synthetic calendar store."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_interruption_review_simulation as target
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_review_bundle as legacy
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import _canonical
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_interruption_view_simulation import fixture, publish

CID = "a" * 32


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "simulation"
    root.mkdir(mode=0o700)
    store = FixtureCalendarStore(root)
    content = fixture()
    content["sources"] = [deepcopy(mock.SOURCE)]
    publish(store, content, 1)
    current = store.read_current()
    manifest = json.loads(current.coverage_manifest_bytes)
    declaration = {
        "content": current.content,
        "coverage": manifest["coverage"],
        "data_origin": "MOCK",
        "simulation_provenance": current.simulation_provenance,
    }
    baseline = {
        "declaration": declaration,
        "version_id": current.version_id,
        "version_sha256": current.version_sha256,
        "content_sha256": current.content_sha256,
        "manifest": manifest,
        "manifest_sha256": current.coverage_manifest_sha256,
        "binding": json.loads(current.binding_bytes),
        "binding_sha256": current.binding_sha256,
    }
    candidate = deepcopy(declaration)
    candidate["content"]["exceptions"][0]["end_utc"] = "2026-06-02T02:30:00.000003Z"
    return root, {"baseline": baseline, "candidate": candidate}


def build(root, value, cid=CID, factory=CalendarSnapshot):
    return target.build_once(
        root=str(root),
        candidate_id=cid,
        input_data=value,
        filesystem=TRUSTED_LOCAL_POSIX,
        snapshot_factory=factory,
        mode="SIMULATION_ONLY",
    )


def show(root, cid=CID, factory=CalendarSnapshot, details=False):
    return target.show(
        root=str(root),
        candidate_id=cid,
        filesystem=TRUSTED_LOCAL_POSIX,
        snapshot_factory=factory,
        mode="SIMULATION_ONLY",
        show_details=details,
    )


def test_real_store_complete_difference_hashes_and_old_readers(sample):
    root, value = sample
    pointer = (root / "CURRENT.json").read_bytes()
    result = build(root, value)
    assert result["status"] == "COMMITTED"
    assert str(root) not in str(result)
    summary = show(root)
    assert summary["status"] == "VISIBLE_UNAUTHENTICATED"
    assert summary["baseline"]["version_id"] == value["baseline"]["version_id"]
    assert summary["version_existence"] == "NOT_PROVEN"
    assert summary["candidate_version"] == "NOT_PUBLISHED"
    assert summary["publication"] == "NOT_AUTHORIZED"
    assert "difference" not in summary
    details = show(root, details=True)
    change = details["difference"]["changes"]["exceptions"][0]
    assert change["operation"] == "modified"
    assert change["before"] == value["baseline"]["declaration"]["content"]["exceptions"][0]
    assert change["after"] == value["candidate"]["content"]["exceptions"][0]
    assert change["before"]["source_ids"] == ["MOCK.simulation_only"]
    assert (
        details["difference_sha256"]
        == hashlib.sha256(_canonical(details["difference"])).hexdigest()
    )
    assert details["input_sha256"] == hashlib.sha256(_canonical(value)).hexdigest()
    path = root / "review-bundles" / f"candidate-{CID}.json"
    body, digest = storage._unwrap(path.read_bytes())
    assert digest == summary["payload_sha256"] == result["payload_sha256"]
    assert hashlib.sha256(storage._json(body)).hexdigest() == digest
    assert (
        legacy.show(root=str(root), candidate_id=CID, filesystem=TRUSTED_LOCAL_POSIX)["status"]
        == "UNKNOWN"
    )
    operator = object.__new__(MockCalendarPublication)
    operator.root = str(root)
    with pytest.raises(ValueError):
        operator._candidate(CID)
    assert (root / "CURRENT.json").read_bytes() == pointer


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.pop("baseline"),
        lambda v: v["baseline"].pop("binding"),
        lambda v: v["baseline"].update(version_sha256="0" * 64),
        lambda v: v["baseline"].update(version_id="../private"),
        lambda v: v["baseline"].update(content_sha256="0" * 64),
        lambda v: v["baseline"]["manifest"].update(snapshot_sha256="0" * 64),
        lambda v: v["baseline"].update(binding_sha256="0" * 64),
        lambda v: v["candidate"].update(data_origin="AKSHARE_MOCK"),
        lambda v: v["candidate"].update(simulation_provenance={"mixed": True}),
        lambda v: v["candidate"]["content"]["exceptions"][0].pop("start_utc"),
        lambda v: v["candidate"]["content"]["exceptions"][0].update(source_ids=["other"]),
        lambda v: v["candidate"]["coverage"][0].update(coverage_end="2026-12-30"),
        lambda v: v["candidate"]["content"]["trading_hours"][0]["contracts"].append("mock_new"),
    ],
)
def test_invalid_inputs_unknown_without_review_directory(sample, mutation):
    root, value = sample
    mutation(value)
    result = build(root, value)
    assert result["status"] == "UNKNOWN"
    assert str(root) not in str(result)
    assert not (root / "review-bundles").exists()


def test_same_and_merge_result_is_valid(sample):
    from bt_api_py.trading_calendar_interruption_compare_simulation import (
        merge_simulated_interruption,
    )

    root, value = sample
    content = value["baseline"]["declaration"]["content"]
    empty = {
        "sources": deepcopy(content["sources"]),
        "trading_days": [],
        "trading_hours": [],
        "exceptions": [],
    }
    merged = merge_simulated_interruption(
        content,
        empty,
        coverage=value["candidate"]["coverage"],
        mode="SIMULATION_ONLY",
        snapshot_factory=CalendarSnapshot,
        data_origin="MOCK",
        simulation_provenance=None,
    )
    assert merged["status"] == "READY"
    value["candidate"]["content"] = merged["content"]
    assert build(root, value)["status"] == "COMMITTED"
    assert show(root)["comparison_status"] == "SAME"


def test_forged_resigned_difference_or_input_is_unknown(sample):
    root, value = sample
    assert build(root, value)["status"] == "COMMITTED"
    path = root / "review-bundles" / f"candidate-{CID}.json"
    original = path.read_bytes()
    body, _ = storage._unwrap(original)
    body["difference"]["changes"]["exceptions"][0]["after"]["end_utc"] = "2026-06-02T02:45:00Z"
    body["difference_sha256"] = hashlib.sha256(_canonical(body["difference"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    assert show(root)["status"] == "UNKNOWN"
    body, _ = storage._unwrap(original)
    body["input"]["candidate"]["content"]["exceptions"][0]["end_utc"] = "2026-06-02T02:45:00Z"
    body["input_sha256"] = hashlib.sha256(_canonical(body["input"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    assert show(root)["status"] == "UNKNOWN"


def test_mutation_during_show_returns_unknown(sample):
    root, value = sample
    assert build(root, value)["status"] == "COMMITTED"
    path = root / "review-bundles" / f"candidate-{CID}.json"
    changed = False

    def factory(shard, *, calendar_ref):
        nonlocal changed
        if not changed:
            changed = True
            path.write_bytes(path.read_bytes() + b" ")
        return CalendarSnapshot(shard, calendar_ref=calendar_ref)

    assert show(root, factory=factory)["status"] == "UNKNOWN"


def test_original_id_conflict_and_concurrency(sample):
    root, value = sample
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: build(root, value), range(2)))
    assert sorted(x["status"] for x in results) == ["COMMITTED", "UNKNOWN"]
    path = root / "review-bundles" / f"candidate-{CID}.json"
    prior = path.read_bytes()
    assert build(root, value)["status"] == "UNKNOWN"
    assert path.read_bytes() == prior
    assert show(root)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_write_uncertain_requires_original_id_inspection(sample, monkeypatch):
    root, value = sample
    original = target.storage._write

    def after_write(*args):
        assert original(*args) == "COMMITTED"
        raise OSError("private error")

    monkeypatch.setattr(target.storage, "_write", after_write)
    result = build(root, value)
    assert result["status"] == "UNKNOWN" and result["candidate_id"] == CID
    assert "private error" not in str(result)
    assert show(root)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_capacity_and_show_missing_do_not_create_directory(sample, monkeypatch):
    root, value = sample
    assert show(root)["status"] == "UNKNOWN"
    assert not (root / "review-bundles").exists()
    monkeypatch.setattr(target.review, "_OUTPUT_LIMIT", 16)
    assert build(root, value)["status"] == "UNKNOWN"
    assert not (root / "review-bundles").exists()


def test_cli_shows_only_opt_in_details(sample, capsys):
    root, value = sample
    assert build(root, value)["status"] == "COMMITTED"
    args = [
        "show",
        "--calendar-root",
        str(root),
        "--candidate-id",
        CID,
        "--trusted-local-posix",
        "--mode",
        "SIMULATION_ONLY",
    ]
    assert target.main(args) == 0
    out = capsys.readouterr()
    assert str(root) not in out.out and 'difference"' not in out.out
    assert target.main(args + ["--show-details"]) == 0
    out = capsys.readouterr()
    assert str(root) not in out.out
    assert (
        json.loads(out.out)["difference"]["changes"]["exceptions"][0]["after"]["end_utc"]
        == "2026-06-02T02:30:00.000003Z"
    )


def test_builder_detaches_inputs_before_factory(sample):
    root, value = sample
    original = deepcopy(value)

    def factory(shard, *, calendar_ref):
        value["baseline"]["declaration"]["content"]["exceptions"].clear()
        value["candidate"]["content"]["exceptions"].clear()
        return CalendarSnapshot(shard, calendar_ref=calendar_ref)

    assert build(root, value, factory=factory)["status"] == "COMMITTED"
    details = show(root, details=True)
    assert (
        details["difference"]["changes"]["exceptions"][0]["before"]
        == original["baseline"]["declaration"]["content"]["exceptions"][0]
    )
    assert (
        details["difference"]["changes"]["exceptions"][0]["after"]
        == original["candidate"]["content"]["exceptions"][0]
    )


def test_write_interrupt_reports_original_id_without_retry(sample, monkeypatch):
    root, value = sample
    original = target.storage._write

    def interrupted(*args):
        assert original(*args) == "COMMITTED"
        raise KeyboardInterrupt()

    monkeypatch.setattr(target.storage, "_write", interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        build(root, value)
    assert caught.value.calendar_outcome["status"] == "UNKNOWN"
    assert caught.value.calendar_outcome["candidate_id"] == CID
    assert show(root)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_bad_cli_arguments_do_not_echo_paths(sample, capsys):
    root, _ = sample
    assert target.main(["show", "--calendar-root", str(root), "--bad-secret", str(root)]) == 2
    result = capsys.readouterr()
    assert str(root) not in result.out + result.err


def test_cli_build_reads_stdin_and_requires_mode(sample, monkeypatch, capsys):
    import io
    import sys

    root, value = sample
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(value).encode())))
    args = [
        "build",
        "--calendar-root",
        str(root),
        "--candidate-id",
        CID,
        "--trusted-local-posix",
        "--mode",
        "SIMULATION_ONLY",
    ]
    assert target.main(args) == 0
    out = capsys.readouterr()
    assert str(root) not in out.out + out.err
    assert json.loads(out.out)["status"] == "COMMITTED"


def test_explicit_mode_and_filesystem_required(sample):
    root, value = sample
    args = {
        "root": str(root),
        "candidate_id": CID,
        "input_data": value,
        "filesystem": TRUSTED_LOCAL_POSIX,
        "snapshot_factory": CalendarSnapshot,
    }
    assert target.build_once(**args, mode="LIVE")["status"] == "UNKNOWN"
    args["filesystem"] = "UNTRUSTED"
    assert target.build_once(**args, mode="SIMULATION_ONLY")["status"] == "UNKNOWN"
    assert not (root / "review-bundles").exists()


@pytest.fixture
def sample_v7(tmp_path, monkeypatch):
    from tests.unit import test_trading_calendar_store_interruption_v7 as v7

    root = tmp_path / "v7-simulation"
    root.mkdir(mode=0o700)

    def mock_fixture(**kwargs):
        content = fixture(**kwargs)
        content["sources"] = [deepcopy(mock.SOURCE)]
        return content

    monkeypatch.setattr(v7, "fixture", mock_fixture)
    store, _, event, version = v7._store_v7(root)
    current = store.read_current()
    assert current.snapshot_schema == "bt_api.calendar-store.snapshot/7"
    manifest = json.loads(current.coverage_manifest_bytes)
    declaration = {
        "content": current.content,
        "coverage": manifest["coverage"],
        "data_origin": "MOCK",
        "simulation_provenance": current.simulation_provenance,
    }
    baseline = {
        "declaration": declaration,
        "snapshot_schema": current.snapshot_schema,
        "version_id": current.version_id,
        "version_sha256": current.version_sha256,
        "content_sha256": current.content_sha256,
        "manifest": manifest,
        "manifest_sha256": current.coverage_manifest_sha256,
        "binding": json.loads(current.binding_bytes),
        "binding_sha256": current.binding_sha256,
    }
    candidate = deepcopy(declaration)
    candidate["content"]["exceptions"][0]["end_utc"] = "2026-06-02T02:30:00.000004Z"
    return root, {"baseline": baseline, "candidate": candidate}, store, event, version


def build_v6(root, value, cid=CID, factory=CalendarSnapshot):
    return target.build_v6(
        root=str(root),
        candidate_id=cid,
        input_data=value,
        filesystem=TRUSTED_LOCAL_POSIX,
        snapshot_factory=factory,
        mode="SIMULATION_ONLY",
    )


def show_v6(root, cid=CID, factory=CalendarSnapshot, details=False):
    return target.show_v6(
        root=str(root),
        candidate_id=cid,
        filesystem=TRUSTED_LOCAL_POSIX,
        snapshot_factory=factory,
        mode="SIMULATION_ONLY",
        show_details=details,
    )


def test_v6_real_v7_version_bytes_full_diff_and_old_reader_rejection(sample_v7):
    root, value, store, _, _ = sample_v7
    pointer = (root / "CURRENT.json").read_bytes()
    version_bytes = (root / "versions" / "v2.json").read_bytes()
    assert hashlib.sha256(version_bytes).hexdigest() == value["baseline"]["version_sha256"]
    result = build_v6(root, value)
    assert result["status"] == "COMMITTED"
    summary = show_v6(root)
    assert summary["status"] == "VISIBLE_UNAUTHENTICATED"
    assert summary["baseline"]["snapshot_schema"] == "bt_api.calendar-store.snapshot/7"
    assert summary["baseline"]["version_sha256"] == hashlib.sha256(version_bytes).hexdigest()
    assert summary["version_existence"] == "NOT_PROVEN"
    assert summary["source_authentication"] == "NOT_PROVEN"
    assert summary["publication"] == "NOT_AUTHORIZED"
    result = show_v6(root, details=True)
    change = result["difference"]["changes"]["exceptions"][0]
    assert change["operation"] == "modified"
    assert change["before"] == value["baseline"]["declaration"]["content"]["exceptions"][0]
    assert change["after"] == value["candidate"]["content"]["exceptions"][0]
    assert len(change["after"]) == 11
    assert (
        result["difference_sha256"] == hashlib.sha256(_canonical(result["difference"])).hexdigest()
    )
    assert result["input_sha256"] == hashlib.sha256(_canonical(value)).hexdigest()
    assert show(root)["status"] == "UNKNOWN"
    assert (
        legacy.show(root=str(root), candidate_id=CID, filesystem=TRUSTED_LOCAL_POSIX)["status"]
        == "UNKNOWN"
    )
    operator = object.__new__(MockCalendarPublication)
    operator.root = str(root)
    with pytest.raises(ValueError):
        operator._candidate(CID)
    assert (root / "CURRENT.json").read_bytes() == pointer
    assert store.read_current().version_sha256 == summary["baseline"]["version_sha256"]


def test_v6_v7_schema_survives_no_interruption(sample_v7):
    from tests.unit import test_trading_calendar_store_interruption_v7 as v7

    root, value, store, event, version = sample_v7
    version["content"]["exceptions"] = []
    v7._rebind_version(root, event, version)
    v7._rehash_diff(event)
    v7._install_event(root, event)
    current = store.read_current()
    baseline = value["baseline"]
    baseline.update(
        declaration={
            "content": current.content,
            "coverage": json.loads(current.coverage_manifest_bytes)["coverage"],
            "data_origin": "MOCK",
            "simulation_provenance": None,
        },
        version_sha256=current.version_sha256,
        content_sha256=current.content_sha256,
        manifest=json.loads(current.coverage_manifest_bytes),
        manifest_sha256=current.coverage_manifest_sha256,
        binding=json.loads(current.binding_bytes),
        binding_sha256=current.binding_sha256,
    )
    value["candidate"] = deepcopy(baseline["declaration"])
    assert (
        current.snapshot_schema == baseline["snapshot_schema"] == "bt_api.calendar-store.snapshot/7"
    )
    assert not current.content["exceptions"]
    assert build_v6(root, value)["status"] == "COMMITTED"
    assert show_v6(root)["comparison_status"] == "SAME"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["baseline"].pop("snapshot_schema"),
        lambda v: v["baseline"].update(snapshot_schema="bt_api.calendar-store.snapshot/2"),
        lambda v: v["baseline"].update(snapshot_schema="bt_api.calendar-store.snapshot/8"),
        lambda v: v["baseline"].update(snapshot_schema=None),
        lambda v: v["baseline"].update(version_sha256="0" * 64),
        lambda v: v["baseline"].update(content_sha256="0" * 64),
        lambda v: v["baseline"].update(binding_sha256="0" * 64),
        lambda v: v["baseline"]["manifest"].update(snapshot_sha256="0" * 64),
        lambda v: v["baseline"]["declaration"].update(simulation_provenance={"mixed": True}),
        lambda v: v["candidate"].update(data_origin="AKSHARE_MOCK"),
        lambda v: v["candidate"]["content"]["sources"].append(
            {"id": "other", "description": "other"}
        ),
        lambda v: v["candidate"]["coverage"][0].update(coverage_end="2026-12-30"),
        lambda v: v["candidate"]["content"]["exceptions"][0].pop("start_utc"),
    ],
)
def test_v6_rejects_unknown_schema_mixed_mismatch_and_missing(sample_v7, mutation):
    root, value, _, _, _ = sample_v7
    mutation(value)
    result = build_v6(root, value)
    assert result["status"] == "UNKNOWN"
    assert str(root) not in str(result)
    assert not (root / "review-bundles").exists()


def test_v6_tampered_resigned_envelope_and_during_show(sample_v7):
    root, value, _, _, _ = sample_v7
    assert build_v6(root, value)["status"] == "COMMITTED"
    path = root / "review-bundles" / f"candidate-{CID}.json"
    original = path.read_bytes()
    body, _ = storage._unwrap(original)
    body["input"]["baseline"]["snapshot_schema"] = "bt_api.calendar-store.snapshot/2"
    body["input_sha256"] = hashlib.sha256(_canonical(body["input"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    assert show_v6(root)["status"] == "UNKNOWN"
    path.write_bytes(original)
    changed = False

    def factory(shard, *, calendar_ref):
        nonlocal changed
        if not changed:
            changed = True
            path.write_bytes(path.read_bytes() + b" ")
        return CalendarSnapshot(shard, calendar_ref=calendar_ref)

    assert show_v6(root, factory=factory)["status"] == "UNKNOWN"


def test_v5_golden_bytes_and_cross_version_id_conflicts(sample, sample_v7):
    _, legacy_value = sample
    root, v7_value, _, _, _ = sample_v7
    assert build(root, legacy_value)["status"] == "COMMITTED"
    path = root / "review-bundles" / f"candidate-{CID}.json"
    original = path.read_bytes()
    assert (
        hashlib.sha256(original).hexdigest()
        == "099239559bf9830cd15e4e40454c57e89d99b1c1fa633a527f3b84f2aaccb0d6"
    )
    assert (
        show(root)["payload_sha256"]
        == "04077eb7a35d6d34d1cc8bd0890cf2a5b2f3dd80c8719f098362bc85d76b43dd"
    )
    assert show_v6(root)["status"] == "UNKNOWN"
    assert build_v6(root, v7_value)["status"] == "UNKNOWN"
    assert path.read_bytes() == original
    other_id = "b" * 32
    assert build_v6(root, v7_value, cid=other_id)["status"] == "COMMITTED"
    other = root / "review-bundles" / f"candidate-{other_id}.json"
    preserved = other.read_bytes()
    assert build(root, legacy_value, cid=other_id)["status"] == "UNKNOWN"
    assert other.read_bytes() == preserved
    assert show(root, cid=other_id)["status"] == "UNKNOWN"
    assert show_v6(root, cid=other_id)["status"] == "VISIBLE_UNAUTHENTICATED"
    legacy_with_new_field = deepcopy(legacy_value)
    legacy_with_new_field["baseline"]["snapshot_schema"] = "bt_api.calendar-store.snapshot/7"
    assert build(root, legacy_with_new_field, cid="c" * 32)["status"] == "UNKNOWN"
    legacy_with_v7_hash = deepcopy(legacy_value)
    legacy_with_v7_hash["baseline"]["version_sha256"] = v7_value["baseline"]["version_sha256"]
    assert build(root, legacy_with_v7_hash, cid="d" * 32)["status"] == "UNKNOWN"


def test_v6_rejects_v2_digest_for_identical_content(sample_v7):
    from bt_api_py.trading_calendar_store import calendar_snapshot_digest

    root, value, _, _, _ = sample_v7
    baseline = value["baseline"]
    version_v2 = calendar_snapshot_digest(
        baseline["declaration"]["content"],
        coverage_manifest_bytes=_canonical(baseline["manifest"]),
        coverage_manifest_sha256=baseline["manifest_sha256"],
        binding_bytes=_canonical(baseline["binding"]),
        binding_sha256=baseline["binding_sha256"],
    )
    assert version_v2 != baseline["version_sha256"]
    baseline["version_sha256"] = version_v2
    assert build_v6(root, value)["status"] == "UNKNOWN"


def test_v6_cli_build_and_show(sample_v7, monkeypatch, capsys):
    import io
    import sys

    root, value, _, _, _ = sample_v7
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(value).encode())))
    common = [
        "--calendar-root",
        str(root),
        "--candidate-id",
        CID,
        "--trusted-local-posix",
        "--mode",
        "SIMULATION_ONLY",
    ]
    assert target.main(["build-v6", *common]) == 0
    result = capsys.readouterr()
    assert str(root) not in result.out + result.err
    assert json.loads(result.out)["status"] == "COMMITTED"
    assert target.main(["show-v6", *common, "--show-details"]) == 0
    result = capsys.readouterr()
    assert str(root) not in result.out + result.err
    result = json.loads(result.out)
    assert result["baseline"]["snapshot_schema"] == "bt_api.calendar-store.snapshot/7"
    assert (
        result["difference"]["changes"]["exceptions"][0]["after"]["end_utc"]
        == "2026-06-02T02:30:00.000004Z"
    )
    assert target.main(["show", *common]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_v6_bounds_and_error_output_are_conservative(sample_v7, monkeypatch):
    root, value, _, _, _ = sample_v7
    assert show_v6(root)["status"] == "UNKNOWN"
    assert not (root / "review-bundles").exists()

    def faulty(*args, **kwargs):
        raise ValueError("private failure at " + str(root))

    result = build_v6(root, value, factory=faulty)
    assert result["status"] == "UNKNOWN" and str(root) not in str(result)
    monkeypatch.setattr(target.review, "_OUTPUT_LIMIT", 16)
    assert build_v6(root, value)["status"] == "UNKNOWN"
    assert not (root / "review-bundles").exists()
