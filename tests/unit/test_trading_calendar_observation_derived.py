"""Synthetic same-run capture to derived review association."""

import json
import subprocess
import sys

import pytest

from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from tests.unit.test_trading_calendar_observation_run import cli, env_without_network, run_args
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_review_derived import fixture, support_text

FS = "TRUSTED_LOCAL_POSIX"
CAPTURE = "b" * 32
DERIVED = "e" * 32


def setup(tmp_path):
    root, _, config, path = setup_preview(tmp_path)
    parent, supplement, decisions, _ = fixture(root)
    path.write_text(json.dumps(supplement), encoding="utf-8")
    return root, config, path, parent, supplement, decisions


def run(root, config, path, parent, *, texts=None, decisions=None):
    return runs.run_once(
        **run_args(root, config, path),
        capture_id=CAPTURE,
        parent_id=parent["candidate_id"],
        parent_sha256=parent["payload_sha256"],
        derived_id=DERIVED,
        source_texts=texts,
        decisions=decisions,
    )


def test_real_run_to_derived_and_full_diff(tmp_path):
    from tests.unit.test_trading_calendar_review_bundle import published_candidate, sample

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    published_candidate(root, sample())
    before = (root / "CURRENT.json").read_bytes()
    inputs = tmp_path / "test-input.json"
    inputs.write_text(
        json.dumps(
            {
                "texts": {"manual": support_text(parent, supplement, decisions)},
                "decisions": decisions,
            }
        ),
        encoding="utf-8",
    )
    script = """import json,sys
from bt_api_py.trading_calendar_observation_run import run_once
p=json.load(open(sys.argv[5], encoding='utf-8'))
print(json.dumps(run_once(root=sys.argv[1],config=sys.argv[2],supplement=sys.argv[3],
 budget_seconds=15,enable_shfe=False,filesystem='TRUSTED_LOCAL_POSIX',run_id='a'*32,
 capture_id='b'*32,parent_id='f'*32,parent_sha256=sys.argv[4],derived_id='e'*32,
 source_texts=p['texts'],decisions=p['decisions'])))
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            str(config),
            str(path),
            parent["payload_sha256"],
            str(inputs),
        ],
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path),
        check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["derived_state"] == "COMMITTED", json.dumps(result)
    assert result["association_record"] == "COMMITTED"
    shown = runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS)
    assert shown["derived_association"]["state"] == "VISIBLE_UNAUTHENTICATED", shown
    derived = review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)
    assert derived["bundle"]["report"]["status"] == "STRUCTURALLY_READY_UNVERIFIED"
    assert review.compare_full(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"] in {
        "SAME",
        "CHANGED",
    }
    assert (root / "CURRENT.json").read_bytes() == before


def test_run_cli_without_decisions_keeps_original_items_unresolved(tmp_path):
    root, config, path, parent, _, _ = setup(tmp_path)
    cmd = cli(
        root,
        config,
        path,
        "--capture-supplement",
        "--parent-id",
        parent["candidate_id"],
        "--parent-sha256",
        parent["payload_sha256"],
        "--derived-id",
        DERIVED,
    )
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path),
        check=False,
    )
    assert process.returncode == 10, (process.stdout, process.stderr)
    allocation, result = map(json.loads, process.stdout.splitlines())
    assert allocation["derived_id"] == DERIVED and result["derived_state"] == "COMMITTED"
    shown = review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)
    assert shown["bundle"]["report"]["status"] == "PARTIAL"
    assert shown["bundle"]["assembly"]["original_unresolved"]
    assert str(path) not in process.stdout and str(root) not in process.stdout


def use_fake_supervised_capture(monkeypatch, *, state="COMMITTED", replacement=False):
    from bt_api_py import trading_calendar_preview as preview
    from bt_api_py import trading_calendar_supplement_capture as capture
    from bt_api_py.trading_calendar_check import _check_calendar_inputs_with_capture_candidate
    from bt_api_py.trading_calendar_preview import CapturePreviewResult
    from tests.unit.test_trading_calendar_preview_reports import report

    calls = []

    def supervise(args):
        calls.append(True)
        _, candidate, guard = _check_calendar_inputs_with_capture_candidate(
            config_file=args["config_file"],
            calendar_root=args["calendar_root"],
            supplement_file=args["supplement_file"],
        )
        if replacement:
            import os
            from pathlib import Path

            path = Path(args["supplement_file"])
            replacement_path = path.with_suffix(".replacement")
            replacement_path.write_bytes(path.read_bytes())
            os.replace(replacement_path, path)
        result = capture.retain_capture(
            root=args["calendar_root"],
            run_id=args["supplement_capture_run_id"],
            capture_id=args["supplement_capture_id"],
            candidate=candidate,
            guard=guard,
            filesystem=FS,
        )
        assert result["status"] == ("NOT_COMMITTED" if replacement else "COMMITTED")
        return CapturePreviewResult(
            report(),
            args["supplement_capture_id"],
            state,
            result["payload_sha256"] if state == "COMMITTED" else None,
        )

    monkeypatch.setattr(preview, "_supervisor_supported", lambda: True)
    monkeypatch.setattr(runs, "_supervise", supervise)
    return calls


def test_unknown_capture_never_uses_visible_capture(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    calls = use_fake_supervised_capture(monkeypatch, state="COMMIT_UNKNOWN")
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert calls == [True] and result["derived_state"] == "NOT_ATTEMPTED"
    assert (
        runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS)["derived_association"][
            "state"
        ]
        == "UNKNOWN"
    )
    assert review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"


def test_source_replacement_blocks_derivation(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch, state="NOT_COMMITTED", replacement=True)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "NOT_ATTEMPTED"
    assert review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [("payload_sha256", "0" * 64), ("capture_id", "0" * 32)])
def test_private_capture_mismatch_blocks_derivation(tmp_path, monkeypatch, field, value):
    from bt_api_py import trading_calendar_supplement_capture as capture

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    original = capture.read_capture

    def mismatch(**kwargs):
        result = original(**kwargs)
        if kwargs.get("include_content"):
            result[field] = value
        return result

    monkeypatch.setattr(capture, "read_capture", mismatch)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMIT_UNKNOWN"
    assert review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"


def test_derived_unknown_is_not_promoted_from_visible_file(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    original = review.build_derived_once

    def uncertain(**kwargs):
        result = original(**kwargs)
        assert result["status"] == "COMMITTED"
        return {**result, "status": "COMMIT_UNKNOWN"}

    monkeypatch.setattr(review, "build_derived_once", uncertain)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMIT_UNKNOWN"
    assert (
        review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )
    shown = runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS)
    assert shown["derived_association"]["derived_id"] == DERIVED
    assert shown["derived_association"]["state"] == "UNKNOWN"


def test_missing_parent_has_no_side_effect(tmp_path):
    root, config, path, parent, _, _ = setup(tmp_path)
    result = run(root, config, path, {**parent, "payload_sha256": "0" * 64})
    assert result["derived_state"] == "NOT_ATTEMPTED"
    assert not (root / "observation-runs").exists()


def test_restart_rechecks_run_capture_parent_and_derived(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMITTED", result
    rid = "a" * 32

    def association():
        return runs.show_once(root=str(root), run_id=rid, filesystem=FS)["derived_association"][
            "state"
        ]

    assert association() == "VISIBLE_UNAUTHENTICATED"
    paths = [
        root / "observation-runs" / f"derived-claim-{DERIVED}.json",
        root / "observation-runs" / f"run-{rid}.json",
        root / "supplement-captures" / f"capture-{CAPTURE}.json",
        root / "review-bundles" / f"candidate-{parent['candidate_id']}.json",
        root / "review-bundles" / f"candidate-{DERIVED}.json",
    ]
    for target in paths:
        old = target.read_bytes()
        target.unlink()
        assert association() == "UNKNOWN"
        target.write_bytes(old)
        target.chmod(0o600)
        assert association() == "VISIBLE_UNAUTHENTICATED"
    result_path = root / "observation-runs" / f"derived-{rid}.json"
    result_path.unlink()
    assert association() == "UNKNOWN"
    assert (
        review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_association_write_unknown_is_not_returned_as_commit(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    original = runs._write_once

    def uncertain(directory, chain, name, data):
        state, reason = original(directory, chain, name, data)
        if name == f"derived-{'a' * 32}.json":
            assert state == "COMMITTED"
            return "COMMIT_UNKNOWN", "IO_ERROR"
        return state, reason

    monkeypatch.setattr(runs, "_write_once", uncertain)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMIT_UNKNOWN"
    assert result["association_record"] == "COMMIT_UNKNOWN"
    assert result["derived_payload_sha256"] is None
    shown = runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS)
    assert shown["derived_association"]["state"] == "VISIBLE_UNAUTHENTICATED"
    assert "derived_state" not in shown["derived_association"]


def test_concurrent_same_run_does_not_overwrite(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    calls = use_fake_supervised_capture(monkeypatch)

    def invoke(_):
        return run(
            root,
            config,
            path,
            parent,
            texts={"manual": support_text(parent, supplement, decisions)},
            decisions=decisions,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))
    assert len(calls) == 1
    assert sum(result["derived_state"] == "COMMITTED" for result in results) == 1
    assert (
        sum(
            result.get("claim_installation") == "NOT_COMMITTED"
            or result["reason"] == "DERIVED_ID_EXISTS"
            for result in results
        )
        == 1
    )


def test_parent_change_after_capture_never_builds(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    original = runs._supervise

    def mutate_parent(args):
        value = original(args)
        target = root / "review-bundles" / f"candidate-{parent['candidate_id']}.json"
        target.write_bytes(b"changed")
        return value

    monkeypatch.setattr(runs, "_supervise", mutate_parent)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMIT_UNKNOWN"
    assert review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"


def test_separate_processes_never_overwrite_association(tmp_path, monkeypatch):
    import multiprocessing

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    texts = {"manual": support_text(parent, supplement, decisions)}
    context = multiprocessing.get_context("fork")
    queue = context.Queue()

    def invoke():
        queue.put(run(root, config, path, parent, texts=texts, decisions=decisions))

    processes = [context.Process(target=invoke) for _ in range(2)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sum(row["derived_state"] == "COMMITTED" for row in results) == 1
    assert (
        sum(
            row.get("claim_installation") == "NOT_COMMITTED" or row["reason"] == "DERIVED_ID_EXISTS"
            for row in results
        )
        == 1
    )


def test_capture_changed_during_association_write_is_unknown(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    original = runs._write_once

    def mutate(directory, chain, name, data):
        status = original(directory, chain, name, data)
        if name == f"derived-{'a' * 32}.json":
            (root / "supplement-captures" / f"capture-{CAPTURE}.json").write_bytes(b"changed")
        return status

    monkeypatch.setattr(runs, "_write_once", mutate)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMIT_UNKNOWN"
    assert result["association_record"] == "COMMIT_UNKNOWN"
    assert (
        runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS)["derived_association"][
            "state"
        ]
        == "UNKNOWN"
    )


def test_visible_run_result_with_unknown_write_does_not_start_derivation(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    original = runs._write_once

    def uncertain(directory, chain, name, data):
        status = original(directory, chain, name, data)
        if name.startswith("run-") and name.endswith(".json"):
            assert status[0] == "COMMITTED"
            return "COMMIT_UNKNOWN", "IO_ERROR"
        return status

    monkeypatch.setattr(runs, "_write_once", uncertain)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["record_installation"] == "COMMIT_UNKNOWN"
    assert result["derived_state"] == "NOT_ATTEMPTED"
    assert (
        runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS)["derived_association"][
            "state"
        ]
        == "UNKNOWN"
    )
    assert review.show(root=str(root), candidate_id=DERIVED, filesystem=FS)["status"] == "UNKNOWN"


def test_cli_missing_parent_reports_gap_without_starting_run(tmp_path):
    root, config, path, parent, _, _ = setup(tmp_path)
    cmd = cli(
        root,
        config,
        path,
        "--capture-supplement",
        "--parent-id",
        parent["candidate_id"],
        "--parent-sha256",
        "0" * 64,
        "--derived-id",
        DERIVED,
    )
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path),
        check=False,
    )
    assert process.returncode == 24, (process.stdout, process.stderr)
    allocation, event = map(json.loads, process.stdout.splitlines())
    assert allocation["derived_id"] == DERIVED
    assert event["reason"] == "PARENT_UNAVAILABLE_OR_CHANGED"
    assert event["derived_state"] == "NOT_ATTEMPTED"
    assert not (root / "observation-runs").exists()


def test_different_runs_same_derived_claim_cross_process(tmp_path, monkeypatch):
    import multiprocessing

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    texts = {"manual": support_text(parent, supplement, decisions)}
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    queue = context.Queue()
    original = runs._write_once

    def synchronized(directory, chain, name, data):
        if name == f"derived-claim-{DERIVED}.json":
            barrier.wait(timeout=20)
        return original(directory, chain, name, data)

    monkeypatch.setattr(runs, "_write_once", synchronized)

    def invoke(run_id, capture_id):
        queue.put(
            runs.run_once(
                **run_args(root, config, path, run_id=run_id),
                capture_id=capture_id,
                parent_id=parent["candidate_id"],
                parent_sha256=parent["payload_sha256"],
                derived_id=DERIVED,
                source_texts=texts,
                decisions=decisions,
            )
        )

    pairs = [("a" * 32, "b" * 32), ("d" * 32, "c" * 32)]
    processes = [context.Process(target=invoke, args=pair) for pair in pairs]
    for process in processes:
        process.start()
    results = [queue.get(timeout=35) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    (winner,) = [row for row in results if row["derived_state"] == "COMMITTED"]
    (loser,) = [row for row in results if row.get("claim_installation") == "NOT_COMMITTED"]
    assert winner["run_id"] != loser["run_id"]
    assert not (root / "observation-runs" / f"run-{loser['run_id']}.intent").exists()
    assert not (root / "supplement-captures" / f"capture-{loser['capture_id']}.json").exists()
    shown = runs.show_once(
        root=str(root), run_id=winner["run_id"], filesystem=FS, derived_id=DERIVED
    )
    assert shown["derived_claim"]["state"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["derived_claim"]["run_id"] == winner["run_id"]
    assert shown["derived_association"]["state"] == "VISIBLE_UNAUTHENTICATED"


def test_claim_write_unknown_never_starts_worker_and_restarts_by_id(tmp_path, monkeypatch):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    calls = use_fake_supervised_capture(monkeypatch)
    original = runs._write_once

    def uncertain(directory, chain, name, data):
        result = original(directory, chain, name, data)
        if name == f"derived-claim-{DERIVED}.json":
            assert result[0] == "COMMITTED"
            return "COMMIT_UNKNOWN", "IO_ERROR"
        return result

    monkeypatch.setattr(runs, "_write_once", uncertain)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["claim_installation"] == "COMMIT_UNKNOWN"
    assert result["derived_state"] == "NOT_ATTEMPTED" and calls == []
    assert not (root / "observation-runs" / f"run-{'a' * 32}.intent").exists()
    shown = runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS, derived_id=DERIVED)
    assert shown["derived_claim"]["state"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["derived_claim"]["run_id"] == "a" * 32
    assert "derived_association" not in shown


def test_legacy_association_show_does_not_require_claim(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_detail_collection as storage

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    use_fake_supervised_capture(monkeypatch)
    result = run(
        root,
        config,
        path,
        parent,
        texts={"manual": support_text(parent, supplement, decisions)},
        decisions=decisions,
    )
    assert result["derived_state"] == "COMMITTED"
    rid = "a" * 32
    intent_path = root / "observation-runs" / f"derived-{rid}.intent"
    result_path = root / "observation-runs" / f"derived-{rid}.json"
    intent, _ = storage._unwrap(intent_path.read_bytes())
    intent["schema"] = "bt_api.calendar-observation-derived-intent/1"
    intent_raw = storage._envelope(intent)
    body, _ = storage._unwrap(result_path.read_bytes())
    body["intent_sha256"] = storage._unwrap(intent_raw)[1]
    intent_path.write_bytes(intent_raw)
    result_path.write_bytes(storage._envelope(body))
    (root / "observation-runs" / f"derived-claim-{DERIVED}.json").unlink()
    shown = runs.show_once(root=str(root), run_id=rid, filesystem=FS)
    assert shown["derived_association"]["state"] == "VISIBLE_UNAUTHENTICATED"


def test_invalid_observation_inputs_do_not_consume_claim_and_real_retry_succeeds(tmp_path):
    root, config, path, parent, supplement, decisions = setup(tmp_path)
    inputs = tmp_path / "test-input.json"
    inputs.write_text(
        json.dumps(
            {
                "texts": {"manual": support_text(parent, supplement, decisions)},
                "decisions": decisions,
            }
        ),
        encoding="utf-8",
    )
    script = """import json,sys
from pathlib import Path
from bt_api_py.trading_calendar_observation_run import run_once
p=json.load(open(sys.argv[5], encoding='utf-8'))
args=dict(root=sys.argv[1],config=sys.argv[2],supplement=sys.argv[3],budget_seconds=15,
 enable_shfe=False,filesystem='TRUSTED_LOCAL_POSIX',run_id='a'*32,capture_id='b'*32,
 parent_id='f'*32,parent_sha256=sys.argv[4],derived_id='e'*32,
 source_texts=p['texts'],decisions=p['decisions'])
observed=[]
cases=[({'budget_seconds':-1},'INVALID_ARGUMENTS'),({'capture_id':'a'*32},'INVALID_ARGUMENTS'),
 ({'candidate_id':'a'*32},'INVALID_ARGUMENTS'),({'run_id':'invalid'},'INVALID_ARGUMENTS'),
 ({'root':'relative-root'},'INVALID_ABSOLUTE_PATH'),
 ({'config':'relative-config.yaml'},'INVALID_ABSOLUTE_PATH'),
 ({'supplement':'relative-supplement.yaml'},'INVALID_ABSOLUTE_PATH'),
 ({'root':'/x'+chr(0)},'INVALID_ABSOLUTE_PATH'),
 ({'config':'/x'+chr(0)},'INVALID_ABSOLUTE_PATH'),
 ({'supplement':'/x'+chr(0)},'INVALID_ABSOLUTE_PATH'),
 ({'config':'/tmp/../config.yaml'},'INVALID_ABSOLUTE_PATH'),
 ({'supplement':'/'+'x'*4096},'INVALID_ABSOLUTE_PATH')]
for changes,expected in cases:
 result=run_once(**(args|changes))
 observed.append([result['reason'],expected,(Path(sys.argv[1])/'observation-runs').exists()])
result=run_once(**args)
print(json.dumps({'invalid':observed,'retry':result}))
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            str(config),
            str(path),
            parent["payload_sha256"],
            str(inputs),
        ],
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path),
        check=False,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert len(result["invalid"]) == 12
    assert all(
        reason == expected and not created for reason, expected, created in result["invalid"]
    )
    assert result["retry"]["derived_id"] == DERIVED
    assert result["retry"]["derived_state"] == "COMMITTED"
    assert result["retry"]["claim_installation"] == "COMMITTED"
    assert (
        runs.show_once(root=str(root), run_id="a" * 32, filesystem=FS, derived_id=DERIVED)[
            "derived_association"
        ]["state"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_association_coordinator_rechecks_base_arguments_before_claim(tmp_path):
    from bt_api_py.trading_calendar_observation_derived import run_associated

    root, config, path, parent, _, _ = setup(tmp_path)
    kwargs = {**run_args(root, config, path), "capture_id": CAPTURE, "budget_seconds": -1}
    result = run_associated(
        base_kwargs=kwargs,
        parent_id=parent["candidate_id"],
        parent_sha256=parent["payload_sha256"],
        derived_id=DERIVED,
        source_texts={},
        decisions=[],
    )
    assert result["reason"] == "INVALID_ARGUMENTS"
    assert not (root / "observation-runs").exists()


def test_cli_relative_config_does_not_consume_claim(tmp_path):
    root, _, path, parent, _, _ = setup(tmp_path)
    cmd = cli(
        root,
        "relative-config.yaml",
        path,
        "--capture-supplement",
        "--parent-id",
        parent["candidate_id"],
        "--parent-sha256",
        parent["payload_sha256"],
        "--derived-id",
        DERIVED,
    )
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path),
        check=False,
    )
    assert process.returncode == 24, (process.stdout, process.stderr)
    allocation, event = map(json.loads, process.stdout.splitlines())
    assert allocation["derived_id"] == DERIVED
    assert event["reason"] == "INVALID_ABSOLUTE_PATH"
    assert event["derived_state"] == "NOT_ATTEMPTED"
    assert not (root / "observation-runs").exists()


def test_threadpool_host_is_rejected_before_claim(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root, config, path, parent, supplement, decisions = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            run,
            root,
            config,
            path,
            parent,
            texts={"manual": support_text(parent, supplement, decisions)},
            decisions=decisions,
        ).result(timeout=20)
    assert result["reason"] == "SUPERVISOR_UNSUPPORTED"
    assert result["derived_state"] == "NOT_ATTEMPTED"
    assert not (root / "observation-runs").exists()


@pytest.mark.parametrize("unsupported", ["multi_thread", "sigint", "sigchld"])
def test_unsupported_supervisor_process_never_consumes_claim(tmp_path, monkeypatch, unsupported):
    import signal
    import threading

    from bt_api_py import trading_calendar_preview as preview

    root, config, path, parent, _, _ = setup(tmp_path)
    monkeypatch.setattr(
        threading, "active_count", lambda: 2 if unsupported == "multi_thread" else 1
    )

    def disposition(which):
        if which == signal.SIGCHLD:
            return signal.SIG_IGN if unsupported == "sigchld" else signal.SIG_DFL
        if which == signal.SIGINT:
            return signal.SIG_IGN if unsupported == "sigint" else signal.default_int_handler
        raise AssertionError("unexpected signal")

    monkeypatch.setattr(preview.signal, "getsignal", disposition)
    result = run(root, config, path, parent)
    assert result["reason"] == "SUPERVISOR_UNSUPPORTED"
    assert result["derived_state"] == "NOT_ATTEMPTED"
    assert not (root / "observation-runs").exists()
