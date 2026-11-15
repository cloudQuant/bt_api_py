"""Real temporary Store and CTP, purely fictional interruption publication."""

import os
from copy import deepcopy

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py import trading_calendar_interruption_publication_simulation as target
from bt_api_py import trading_calendar_interruption_review_simulation as review
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from tests.unit.test_trading_calendar_interruption_prepare_simulation import (
    CID,
    files,
)

pytest_plugins = ("tests.unit.test_trading_calendar_interruption_prepare_simulation",)


def arguments(env):
    root, _, current, _, schema, payload = env
    return {
        "root": str(root),
        "candidate_id": CID,
        "review_schema": schema,
        "expected_candidate_payload_sha256": payload,
        "expected_current_publication_id": current.publication_id,
        "expected_current_publication_sha256": current.publication_sha256,
        "filesystem": TRUSTED_LOCAL_POSIX,
        "mode": "SIMULATION_ONLY",
    }


def attempt(env, **changes):
    args = arguments(env)
    proposal = target.observe(**args)
    assert proposal["status"] in {"AUTO_DECLARED", "NEEDS_CONFIRMATION", "SAME_DECLARATION"}
    call = {
        **args,
        "expected_proposal_sha256": proposal["proposal_sha256"],
        "publication_id": "candidate-pub",
        "version_id": "candidate-version",
        **changes,
    }
    return proposal, target.publish(**call)


def test_auto_end_to_end_single_version(environment):
    root, store, current, _, _, _ = environment
    proposal, result = attempt(environment)
    assert proposal["status"] == "AUTO_DECLARED"
    assert result["status"] == "COMMITTED", result
    after = store.read_current_with_consistent_receipts().snapshot
    assert after.snapshot_schema == "bt_api.calendar-store.snapshot/7"
    assert after.version_sha256 == proposal["target"]["version_sha256"]
    assert after.publication_id == "candidate-pub"
    assert after.publication_id != current.publication_id
    assert str(root) not in str(result)
    assert (
        store.read_publication_policy("candidate-pub")["difference"]
        == proposal["publication_difference"]
    )


def test_manual_pending_then_confirmation(environment):
    root, store, _, _, _, _ = environment
    policy = store.read_review_policy()
    assert (
        store.change_review_policy(
            expected_revision=policy.revision,
            mode="manual",
            operation_id="mode-manual",
            actor="mock_actor",
            at_utc="2026-01-01T00:00:00.000000Z",
        ).status
        == "COMMITTED"
    )
    before = files(root)
    proposal, pending = attempt(environment)
    assert pending["status"] == "NEEDS_CONFIRMATION"
    assert files(root) == before
    proposal2, confirmed = attempt(
        environment, reviewer="reviewer1", confirmed_at="2026-01-01T00:00:00.000000Z"
    )
    assert proposal2 == proposal
    assert confirmed["status"] == "COMMITTED", confirmed
    policy_evidence = store.read_publication_policy("candidate-pub")
    assert policy_evidence["reviewer"] == "reviewer1"
    assert (
        policy_evidence["confirmation_difference_sha256"]
        == proposal["publication_difference_sha256"]
    )


def test_auto_rejects_review_and_wrong_proposal(environment):
    root = environment[0]
    before = files(root)
    assert (
        attempt(environment, reviewer="reviewer1", confirmed_at="2026-01-01T00:00:00.000000Z")[1][
            "status"
        ]
        == "UNKNOWN"
    )
    assert attempt(environment, expected_proposal_sha256="f" * 64)[1]["status"] == "UNKNOWN"
    assert files(root) == before


def test_same_remains_original_format_and_needs_no_confirmation(environment):
    root, store, current, value, schema, _ = environment
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
    args = {
        **arguments(environment),
        "candidate_id": "b" * 32,
        "expected_candidate_payload_sha256": saved["payload_sha256"],
    }
    policy = store.read_review_policy()
    assert (
        store.change_review_policy(
            expected_revision=policy.revision,
            mode="manual",
            operation_id="mode-manual",
            actor="mock_actor",
            at_utc="2026-01-01T00:00:00.000000Z",
        ).status
        == "COMMITTED"
    )
    proposal = target.observe(**args)
    assert proposal["status"] == "SAME_DECLARATION"
    before = files(root)
    outcome = target.publish(
        **args,
        expected_proposal_sha256=proposal["proposal_sha256"],
        publication_id="same-pub",
        version_id="same-version",
    )
    assert outcome["status"] == "UNCHANGED", outcome
    assert store.read_current().version_id == current.version_id
    assert store.read_current().snapshot_schema == current.snapshot_schema
    assert files(root) == before


def test_review_changes_inside_locked_guard_fail_closed(environment, monkeypatch):
    root = environment[0]
    original = target.review._load_verified_material
    calls = 0

    def changed(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 6:
            path = root / "review-bundles" / f"candidate-{CID}.json"
            path.write_bytes(path.read_bytes() + b" ")
        return original(**kwargs)

    monkeypatch.setattr(target.review, "_load_verified_material", changed)
    result = attempt(environment)[1]
    assert calls >= 6
    assert result["status"] != "COMMITTED"
    assert not (root / "versions" / "candidate-version.json").exists()
    assert str(root) not in str(result)


def test_policy_changes_after_proposal_fail_closed(environment, monkeypatch):
    root, store, _, _, _, _ = environment
    original = target.prep.prepare

    def changed(**kwargs):
        proposal = original(**kwargs)
        policy = store.read_review_policy()
        assert (
            store.change_review_policy(
                expected_revision=policy.revision,
                mode="manual",
                operation_id="concurrent-policy",
                actor="mock_actor",
                at_utc="2026-01-01T00:00:00.000000Z",
            ).status
            == "COMMITTED"
        )
        return proposal

    args = arguments(environment)
    proposal = target.observe(**args)
    monkeypatch.setattr(target.prep, "prepare", changed)
    result = target.publish(
        **args,
        expected_proposal_sha256=proposal["proposal_sha256"],
        publication_id="candidate-pub",
        version_id="candidate-version",
    )
    assert result["status"] != "COMMITTED"
    assert not (root / "versions" / "candidate-version.json").exists()


def test_receipt_changes_after_observe_fail_under_writer_lock(environment, monkeypatch):
    root, _, current, _, _, _ = environment
    original = target.review._load_verified_material
    calls = 0

    def changed(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 3:
            path = root / "receipts" / f"{current.publication_id}.json"
            import json

            value = json.loads(path.read_bytes())
            value["observation"] = "RECOVERY_CURRENT_READBACK"
            path.write_bytes(target._canonical(value))
        return result

    args = arguments(environment)
    proposal = target.observe(**args)
    monkeypatch.setattr(target.review, "_load_verified_material", changed)
    result = target.publish(
        **args,
        expected_proposal_sha256=proposal["proposal_sha256"],
        publication_id="candidate-pub",
        version_id="candidate-version",
    )
    assert calls >= 3
    assert result["status"] != "COMMITTED"
    assert not (root / "versions" / "candidate-version.json").exists()


def test_pending_receipt_failure_preserves_original_id(environment, monkeypatch):
    root, store, _, _, _, _ = environment
    real_write = target.TradingCalendarStore._write_new

    def fail_receipt(self, path, data):
        if path == root / "receipts" / "candidate-pub.json":
            raise OSError("private detail")
        return real_write(self, path, data)

    args = arguments(environment)
    proposal = target.observe(**args)
    call = {
        **args,
        "expected_proposal_sha256": proposal["proposal_sha256"],
        "publication_id": "candidate-pub",
        "version_id": "candidate-version",
    }
    with monkeypatch.context() as patch:
        patch.setattr(target.TradingCalendarStore, "_write_new", fail_receipt)
        result = target.publish(**call)
    assert (result["status"], result["post_commit"]) == ("COMMITTED", "INCOMPLETE")
    assert result["completion"] == "INCOMPLETE"
    assert str(root) not in str(result) and "private detail" not in str(result)
    again = target.publish(**call)
    assert (again["status"], again["reason"]) == ("UNKNOWN", "ORIGINAL_ID_RECONCILE_FIRST")
    assert store.reconcile_publication("candidate-pub").visibility == "COMMITTED"


def test_lock_busy_does_not_write(environment):
    import fcntl

    root = environment[0]
    args = arguments(environment)
    proposal = target.observe(**args)
    before = files(root)
    with (root / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = target.publish(
            **args,
            expected_proposal_sha256=proposal["proposal_sha256"],
            publication_id="candidate-pub",
            version_id="candidate-version",
        )
    assert result["status"] == "UNKNOWN"
    assert files(root) == before


def test_cli_observe_uses_installed_ctp(environment, capsys):
    args = arguments(environment)
    argv = ["observe"]
    for key, value in args.items():
        argv += ["--" + key.replace("_", "-"), value]
    assert target.main(argv) == 0
    assert '"status": "AUTO_DECLARED"' in capsys.readouterr().out


def test_cli_publish_uses_explicit_proposal(environment, capsys):
    args = arguments(environment)
    proposal = target.observe(**args)
    args.update(
        expected_proposal_sha256=proposal["proposal_sha256"],
        publication_id="candidate-pub",
        version_id="candidate-version",
    )
    argv = ["publish"]
    for key, value in args.items():
        argv += ["--" + key.replace("_", "-"), value]
    assert target.main(argv) == 0
    assert '"status": "COMMITTED"' in capsys.readouterr().out


@pytest.mark.parametrize("after_replace", [False, True])
def test_interrupted_replace_reports_original_ids(environment, monkeypatch, after_replace):
    root, store, _, _, _, _ = environment
    args = arguments(environment)
    proposal = target.observe(**args)
    call = {
        **args,
        "expected_proposal_sha256": proposal["proposal_sha256"],
        "publication_id": "candidate-pub",
        "version_id": "candidate-version",
    }
    original = os.replace

    def interrupt(src, dst):
        if after_replace:
            original(src, dst)
        raise KeyboardInterrupt("private detail")

    with monkeypatch.context() as patch:
        patch.setattr("bt_api_py.trading_calendar_store.os.replace", interrupt)
        with pytest.raises(KeyboardInterrupt) as caught:
            target.publish(**call)
    outcome = caught.value.calendar_outcome
    assert (outcome["status"], outcome["publication_id"], outcome["version_id"]) == (
        "COMMIT_UNKNOWN",
        "candidate-pub",
        "candidate-version",
    )
    assert outcome["completion"] == "INCOMPLETE" and outcome["fence"] == "UNKNOWN"
    assert "private detail" not in str(outcome) and str(root) not in str(outcome)
    assert target.publish(**call)["reason"] == "ORIGINAL_ID_RECONCILE_FIRST"
    result = store.reconcile_publication("candidate-pub")
    assert result.visibility == ("COMMITTED" if after_replace else "COMMIT_UNKNOWN")


def test_cli_interrupt_returns_json_and_nonzero(environment, monkeypatch, capsys):
    import json

    root = environment[0]
    args = arguments(environment)
    proposal = target.observe(**args)
    args.update(
        expected_proposal_sha256=proposal["proposal_sha256"],
        publication_id="candidate-pub",
        version_id="candidate-version",
    )
    argv = ["publish"]
    for key, value in args.items():
        argv += ["--" + key.replace("_", "-"), value]
    original = os.replace

    def interrupt(src, dst):
        original(src, dst)
        raise KeyboardInterrupt("private detail")

    monkeypatch.setattr("bt_api_py.trading_calendar_store.os.replace", interrupt)
    assert target.main(argv) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "COMMIT_UNKNOWN"
    assert result["publication_id"] == "candidate-pub"
    assert result["version_id"] == "candidate-version"
    assert "private detail" not in captured.out and str(root) not in captured.out
    assert not captured.err


def test_runtime_error_after_receipt_retains_original_ids(environment, monkeypatch):
    root, store, _, _, _, _ = environment
    args = arguments(environment)
    proposal = target.observe(**args)
    real_write = target.TradingCalendarStore._write_new

    def interrupted(self, path, data):
        real_write(self, path, data)
        if path == root / "receipts" / "candidate-pub.json":
            raise RuntimeError("private detail")

    with monkeypatch.context() as patch:
        patch.setattr(target.TradingCalendarStore, "_write_new", interrupted)
        result = target.publish(
            **args,
            expected_proposal_sha256=proposal["proposal_sha256"],
            publication_id="candidate-pub",
            version_id="candidate-version",
        )
    assert (result["status"], result["publication_id"], result["version_id"]) == (
        "UNKNOWN",
        "candidate-pub",
        "candidate-version",
    )
    assert "private detail" not in str(result) and str(root) not in str(result)
    assert store.reconcile_publication("candidate-pub").visibility == "COMMITTED"


def test_invalid_ids_and_cli_arguments_are_redacted(environment, capsys):
    import json

    args = arguments(environment)
    bad_id = "/private-sensitive-id"
    result = target.publish(
        **args, expected_proposal_sha256="0" * 64, publication_id=bad_id, version_id="valid-version"
    )
    assert result["status"] == "UNKNOWN" and result["publication_id"] is None
    assert bad_id not in str(result)
    argv = ["observe"]
    args["mode"] = "private-sensitive-mode"
    for key, value in args.items():
        argv += ["--" + key.replace("_", "-"), value]
    assert target.main(argv) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "UNKNOWN"
    assert "private-sensitive-mode" not in captured.out + captured.err
    assert not captured.err
