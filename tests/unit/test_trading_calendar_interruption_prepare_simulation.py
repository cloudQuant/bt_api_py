"""Read-only local MOCK preparation against actual temporary version stores."""

import fcntl
import hashlib
import json
import os
from copy import deepcopy

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_interruption_prepare_simulation as target
from bt_api_py import trading_calendar_interruption_review_simulation as review
from bt_api_py import trading_calendar_mock as mock
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import CalendarReadBudget, _canonical
from tests.calendar_historical_interruption import write_historical_interruption
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_interruption_view_simulation import fixture, publish

CID = "a" * 32


@pytest.fixture(params=["v5", "v6"])
def environment(tmp_path, monkeypatch, request):
    root = tmp_path / "simulation"
    root.mkdir(mode=0o700)
    if request.param == "v6":
        from tests.unit import test_trading_calendar_store_interruption_v7 as v7

        def mock_fixture(**kwargs):
            content = fixture(**kwargs)
            content["sources"] = [deepcopy(mock.SOURCE)]
            return content

        monkeypatch.setattr(v7, "fixture", mock_fixture)
        store, _, _, _ = v7._store_v7(root)
    else:
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
    candidate["content"]["exceptions"][0]["end_utc"] = (
        "2026-06-02T02:30:00.000004Z" if request.param == "v6" else "2026-06-02T02:30:00.000003Z"
    )
    schema = review._SCHEMA if request.param == "v5" else review._SCHEMA_V6
    if request.param == "v6":
        baseline["snapshot_schema"] = current.snapshot_schema
    value = {"baseline": baseline, "candidate": candidate}
    build = review.build_once if request.param == "v5" else review.build_v6
    saved = build(
        root=str(root),
        candidate_id=CID,
        input_data=value,
        filesystem=TRUSTED_LOCAL_POSIX,
        snapshot_factory=CalendarSnapshot,
        mode="SIMULATION_ONLY",
    )
    assert saved["status"] == "COMMITTED"
    return root, store, current, value, schema, saved["payload_sha256"]


def prepare(env, **changes):
    root, _, current, _, schema, payload = env
    args = {
        "root": str(root),
        "candidate_id": CID,
        "review_schema": schema,
        "expected_candidate_payload_sha256": payload,
        "expected_current_publication_id": current.publication_id,
        "expected_current_publication_sha256": current.publication_sha256,
        "filesystem": TRUSTED_LOCAL_POSIX,
        "mode": "SIMULATION_ONLY",
        "snapshot_factory": CalendarSnapshot,
    }
    args.update(changes)
    return target.prepare(**args)


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_auto_proposal_reconstructs_target_and_never_writes(environment, monkeypatch):
    root, store, current, value, _, payload = environment
    before = files(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("writer used")

    monkeypatch.setattr(target.TradingCalendarStore, "read_review_policy", forbidden)
    monkeypatch.setattr(target.TradingCalendarStore, "_writer_lock", forbidden)
    result = prepare(environment)
    assert result["status"] == "AUTO_DECLARED"
    assert result["observation"] == "OBSERVATION_ONLY"
    assert result["current_policy_atomicity"] == "NOT_PROVEN"
    assert result["publication"] == "NOT_AUTHORIZED"
    assert result["format_migration"] == "NOT_ATTEMPTED"
    assert result["candidate_payload_sha256"] == payload
    assert result["baseline"]["publication_sha256"] == current.publication_sha256
    assert result["target"]["snapshot_schema"] == "bt_api.calendar-store.snapshot/7"
    content = value["candidate"]["content"]
    content_sha = hashlib.sha256(_canonical(content)).hexdigest()
    manifest = {
        "schema": "bt_api.calendar-simulation-coverage/1",
        "snapshot_sha256": content_sha,
        "coverage": value["candidate"]["coverage"],
    }
    manifest_sha = hashlib.sha256(_canonical(manifest)).hexdigest()
    binding = {
        "schema": "bt_api.calendar-simulation-binding/1",
        "snapshot_sha256": content_sha,
        "manifest_sha256": manifest_sha,
    }
    binding_sha = hashlib.sha256(_canonical(binding)).hexdigest()
    snapshot = {
        "schema": "bt_api.calendar-store.snapshot/7",
        "content": content,
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "binding": binding,
        "binding_sha256": binding_sha,
    }
    assert result["target"]["version_sha256"] == hashlib.sha256(_canonical(snapshot)).hexdigest()
    wrapped = result["publication_difference"]
    assert (
        result["publication_difference_sha256"] == hashlib.sha256(_canonical(wrapped)).hexdigest()
    )
    assert (
        result["declaration_difference_sha256"]
        == hashlib.sha256(_canonical(wrapped["declaration_difference"])).hexdigest()
    )
    assert result["declaration_difference_sha256"] != result["publication_difference_sha256"]
    proposal = dict(result)
    expected = proposal.pop("proposal_sha256")
    assert expected == hashlib.sha256(_canonical(proposal)).hexdigest()
    assert "reviewer" not in result and "confirmed_at" not in result
    assert str(root) not in str(result)
    assert files(root) == before


def test_manual_policy_needs_confirmation_without_writes(environment):
    root, store, _, _, _, _ = environment
    policy = store.read_review_policy()
    changed = store.change_review_policy(
        expected_revision=policy.revision,
        mode="manual",
        operation_id="policy-manual",
        actor="MOCK_reviewer",
        at_utc="2026-06-01T00:00:00.000000Z",
    )
    assert changed.status == "COMMITTED"
    before = files(root)
    result = prepare(environment)
    assert result["status"] == "NEEDS_CONFIRMATION"
    assert result["policy"]["revision"] == changed.policy.revision
    assert result["publication"] == "NOT_AUTHORIZED"
    assert files(root) == before


def test_same_declaration_does_not_claim_schema_migration(environment):
    root, _, current, value, schema, _ = environment
    value = deepcopy(value)
    value["candidate"] = deepcopy(value["baseline"]["declaration"])
    build = review.build_once if schema == review._SCHEMA else review.build_v6
    saved = build(
        root=str(root),
        candidate_id="b" * 32,
        input_data=value,
        filesystem=TRUSTED_LOCAL_POSIX,
        snapshot_factory=CalendarSnapshot,
        mode="SIMULATION_ONLY",
    )
    assert saved["status"] == "COMMITTED"
    before = files(root)
    result = prepare(
        environment,
        candidate_id="b" * 32,
        expected_candidate_payload_sha256=saved["payload_sha256"],
    )
    assert result["status"] == "SAME_DECLARATION"
    assert result["baseline"]["snapshot_schema"] == current.snapshot_schema
    assert result["format_migration"] == "NOT_ATTEMPTED"
    assert files(root) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_candidate_payload_sha256": "0" * 64},
        {"expected_current_publication_sha256": "0" * 64},
        {"expected_current_publication_id": "wrong"},
        {"mode": "LIVE"},
        {"filesystem": "UNTRUSTED"},
        {"review_schema": "bt_api.calendar-review-bundle/99"},
    ],
)
def test_wrong_identity_mode_or_schema_is_unknown(environment, changes):
    root = environment[0]
    before = files(root)
    result = prepare(environment, **changes)
    assert result["status"] == "UNKNOWN"
    assert str(root) not in str(result)
    assert files(root) == before


def test_same_version_different_publication_is_not_accepted(environment):
    root, store, current, _, schema, _ = environment
    if schema == review._SCHEMA_V6:
        pytest.skip("stage A intentionally blocks all /7 writes")
    result = write_historical_interruption(
        store,
        current.content,
        publication_id="republished",
        version_id=current.version_id,
        republish=True,
        coverage_manifest_bytes=current.coverage_manifest_bytes,
        coverage_manifest_sha256=current.coverage_manifest_sha256,
        binding_bytes=current.binding_bytes,
        binding_sha256=current.binding_sha256,
    )
    assert result.visibility == "COMMITTED"
    assert store.read_current().version_sha256 == current.version_sha256
    assert store.read_current().publication_id != current.publication_id
    before = files(root)
    assert prepare(environment)["status"] == "UNKNOWN"
    assert files(root) == before


def test_missing_receipt_or_lock_does_not_create_any_files(environment):
    root, _, current, _, _, _ = environment
    (root / "receipts" / f"{current.publication_id}.json").unlink()
    before = files(root)
    assert prepare(environment)["status"] == "UNKNOWN"
    assert files(root) == before
    (root / ".lock").unlink()
    before = files(root)
    assert prepare(environment)["status"] == "UNKNOWN"
    assert not (root / ".lock").exists()
    assert files(root) == before


def test_store_lock_busy_is_unknown_without_write(environment):
    root = environment[0]
    fd = os.open(root / ".lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = files(root)
        assert prepare(environment)["status"] == "UNKNOWN"
        assert files(root) == before
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_candidate_resigned_tamper_rejected(environment):
    root = environment[0]
    path = root / "review-bundles" / f"candidate-{CID}.json"
    body, _ = storage._unwrap(path.read_bytes())
    body["difference"]["changes"]["exceptions"][0]["after"]["end_utc"] = "2026-06-02T02:45:00Z"
    body["difference_sha256"] = hashlib.sha256(_canonical(body["difference"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    before = files(root)
    assert prepare(environment)["status"] == "UNKNOWN"
    assert files(root) == before


def test_current_change_between_observations_is_unknown(environment, monkeypatch):
    root = environment[0]
    original = target._policy_readonly
    calls = 0

    def mutate(store, current):
        nonlocal calls
        result = original(store, current)
        calls += 1
        if calls == 1:
            (root / "CURRENT.json").write_bytes(b"{}")
        return result

    monkeypatch.setattr(target, "_policy_readonly", mutate)
    assert prepare(environment)["status"] == "UNKNOWN"


def test_policy_change_between_observations_is_unknown(environment, monkeypatch):
    _, store, _, _, _, _ = environment
    original = target._policy_readonly
    calls = 0

    def mutate(current_store, current):
        nonlocal calls
        result = original(current_store, current)
        calls += 1
        if calls == 1:
            changed = store.change_review_policy(
                expected_revision=result[0]["revision"],
                mode="manual",
                operation_id="racing-policy",
                actor="MOCK_reviewer",
                at_utc="2026-06-01T00:00:00.000000Z",
            )
            assert changed.status == "COMMITTED"
        return result

    monkeypatch.setattr(target, "_policy_readonly", mutate)
    assert prepare(environment)["status"] == "UNKNOWN"


def test_candidate_change_between_observations_is_unknown(environment, monkeypatch):
    root = environment[0]
    original = target._policy_readonly
    calls = 0

    def mutate(store, current):
        nonlocal calls
        result = original(store, current)
        calls += 1
        if calls == 1:
            path = root / "review-bundles" / f"candidate-{CID}.json"
            path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(target, "_policy_readonly", mutate)
    assert prepare(environment)["status"] == "UNKNOWN"


def test_budget_factory_failure_and_no_current_are_unknown(environment, monkeypatch):
    root = environment[0]
    normal_budget = target._BUDGET
    monkeypatch.setattr(target, "_BUDGET", CalendarReadBudget(1024, 2048, 1))
    assert prepare(environment)["status"] == "UNKNOWN"

    def fail(*args, **kwargs):
        raise ValueError("private " + str(root))

    result = prepare(environment, snapshot_factory=fail)
    assert result["status"] == "UNKNOWN" and str(root) not in str(result)
    monkeypatch.setattr(target, "_BUDGET", normal_budget)
    (root / "CURRENT.json").unlink()
    before = files(root)
    assert prepare(environment)["status"] == "UNKNOWN"
    assert files(root) == before


def test_valid_receipt_change_between_observations_is_unknown(environment, monkeypatch):
    root, _, current, _, _, _ = environment
    original = target._policy_readonly
    calls = 0

    def mutate(store, observation):
        nonlocal calls
        result = original(store, observation)
        calls += 1
        if calls == 1:
            path = root / "receipts" / f"{current.publication_id}.json"
            receipt = json.loads(path.read_bytes())
            receipt["observation"] = "RECOVERY_CURRENT_READBACK"
            path.write_bytes(_canonical(receipt))
        return result

    monkeypatch.setattr(target, "_policy_readonly", mutate)
    assert prepare(environment)["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["input"]["candidate"].update(simulation_provenance={"mixed": True}),
        lambda body: body["input"]["candidate"]["coverage"][0].update(coverage_end="2026-12-30"),
    ],
)
def test_resigned_mixed_or_scope_changed_material_unknown(environment, mutation):
    root = environment[0]
    path = root / "review-bundles" / f"candidate-{CID}.json"
    body, _ = storage._unwrap(path.read_bytes())
    mutation(body)
    body["input_sha256"] = hashlib.sha256(_canonical(body["input"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    before = files(root)
    assert prepare(environment)["status"] == "UNKNOWN"
    assert files(root) == before


def test_root_or_lock_identity_change_between_observations_is_unknown(environment, monkeypatch):
    original = target._root_lock_identity
    calls = 0

    def changed(root):
        nonlocal calls
        value = original(root)
        calls += 1
        if calls == 2:
            return value[0], (value[1][0], value[1][1] + 1)
        return value

    monkeypatch.setattr(target, "_root_lock_identity", changed)
    assert prepare(environment)["status"] == "UNKNOWN"
