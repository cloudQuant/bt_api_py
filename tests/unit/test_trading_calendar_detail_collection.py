"""Offline, bounded collection and recovery tests; no real network."""

import json
import os
import subprocess
import sys

import pytest

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as collection
from tests.unit.test_trading_calendar_observation_navigation import flags
from tests.unit.test_trading_calendar_observation_run import cli
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_network_navigation import inject

RID, CID, ID = "a" * 32, "b" * 32, "c" * 32
DIGEST = "d" * 64


def candidate(n, *, long_paths=False):
    prefix = "x" * 400 if long_paths else "x"
    links = [
        {"path": f"/products/futures/metal/{prefix}{i}_f/", "kind": "future", "label": "label"}
        for i in range(n)
    ]
    return {
        "schema": "bt_api.calendar-preview-candidate/6",
        "payload_sha256": DIGEST,
        "payload": {
            "navigation_fetch": {
                "status": "CANDIDATE",
                "navigation": {"status": "CANDIDATE", "review_reasons": [], "links": links},
            }
        },
    }


def success(args):
    state = detail._state(args["candidate_id"], args["paths"])
    state["candidate_payload_sha256"] = args["expected_candidate_payload_sha256"]
    state["status"] = "OBSERVED_UNVERIFIED"
    for item in state["items"]:
        item.update(
            status="CANDIDATE",
            reason="NONE",
            transport_status="HTTP_OK",
            parse_status="CANDIDATE",
            identity_status="CONSISTENT_FIELDS_UNVERIFIED",
            page_sha256="e" * 64,
            records=[
                {
                    "kind": item["kind"],
                    "symbol": "CU",
                    "venue": "SHFE",
                    "raw_date": "2023-03-29",
                    "trading_hours": "9:00",
                    "unknown_reasons": [],
                    "effective_date": "UNKNOWN",
                }
            ],
        )
    return state


def setup_fake(tmp_path, monkeypatch, n):
    root, _, _, _ = setup_preview(tmp_path)
    data = candidate(n)
    monkeypatch.setattr(
        collection.runs,
        "show_once",
        lambda **kw: {
            "intent": "VISIBLE_UNAUTHENTICATED",
            "record": "VISIBLE_UNAUTHENTICATED",
            "navigation_requested": True,
            "candidate_id": CID,
            "observation": {
                "schema": runs_v3(),
                "candidate_id": CID,
                "candidate_state": "COMMITTED",
            },
        },
    )
    monkeypatch.setattr(collection.inbox, "read_candidate", lambda **kw: data)
    return root, data


def runs_v3():
    return collection.runs._RESULT_SCHEMA_V3


def invoke(root, emit=lambda _: None, **kwargs):
    args = {
        "root": str(root),
        "run_id": RID,
        "filesystem": "TRUSTED_LOCAL_POSIX",
        "budget_seconds": 60,
        "per_request_seconds": 3,
        "collection_id": ID,
        "emit": emit,
    }
    args.update(kwargs)
    return collection.collect_once(**args)


@pytest.mark.parametrize("count", [1, 10, 11, 48, 512])
def test_all_batches_and_complete_path_list(tmp_path, monkeypatch, count):
    root, data = setup_fake(tmp_path, monkeypatch, count)
    calls, allocations = [], []

    def fake(args):
        calls.append(args)
        return success(args)

    monkeypatch.setattr(detail, "_supervise", fake)
    result = invoke(root, allocations.append)
    assert result["status"] == "OBSERVED_UNVERIFIED"
    assert allocations == [
        {
            "event": "COLLECTION_ID_ALLOCATED",
            "collection_id": ID,
            "run_id": RID,
            "candidate_id": CID,
        }
    ]
    assert [p for call in calls for p in call["paths"]] == [
        x["path"] for x in data["payload"]["navigation_fetch"]["navigation"]["links"]
    ]
    assert all(
        len(call["paths"]) <= 10
        and call["expected_candidate_payload_sha256"] == DIGEST
        and call["budget"] <= 60
        for call in calls
    )
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == "OBSERVED_UNVERIFIED" and len(report["paths"]) == count
    assert all(x["status"] == "CANDIDATE" for x in report["paths"])
    assert not (root / "CURRENT.json").exists()
    assert str(root) not in json.dumps(report)


@pytest.mark.parametrize("count", [0, 513])
def test_empty_or_excess_navigation_rejected_without_network(tmp_path, monkeypatch, count):
    root, _ = setup_fake(tmp_path, monkeypatch, count)
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("network"))
    assert invoke(root)["reason"] == "INPUT_UNAVAILABLE"
    assert not (root / "detail-collections").exists()


def test_long_path_intent_exceeds_old_64k_limit():
    data = candidate(512, long_paths=True)
    paths = collection._paths(data, "/private/calendar")
    assert len(paths) == 512 and 65536 < len(collection._json(paths)) < collection._INTENT_LIMIT


def test_conflicting_duplicate_rejected(tmp_path, monkeypatch):
    root, data = setup_fake(tmp_path, monkeypatch, 1)
    links = data["payload"]["navigation_fetch"]["navigation"]["links"]
    links.append({**links[0], "label": "different"})
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("network"))
    assert invoke(root)["reason"] == "INPUT_UNAVAILABLE"


@pytest.mark.parametrize("reason", ["failure", "cleanup", "mismatch"])
def test_failure_stops_and_show_retains_unattempted(tmp_path, monkeypatch, reason):
    root, _ = setup_fake(tmp_path, monkeypatch, 21)
    calls = []

    def fake(args):
        calls.append(args)
        if len(calls) == 1:
            return success(args)
        state = detail._state(CID, args["paths"])
        state["candidate_payload_sha256"] = DIGEST
        if reason == "mismatch":
            state["candidate_payload_sha256"] = "f" * 64
            return detail._reject(state, "CANDIDATE_DIGEST_MISMATCH")
        if reason == "cleanup":
            return detail._interrupted(state, CID, args["paths"], cleanup=True)
        state["status"] = "PARTIAL"
        for item in state["items"]:
            item.update(status="UNKNOWN", reason="FETCH_FAILED", transport_status="TLS_ERROR")
        return state

    monkeypatch.setattr(detail, "_supervise", fake)
    result = invoke(root)
    expected_reason = "BATCH_CLEANUP_UNCERTAIN" if reason == "cleanup" else "BATCH_STOPPED"
    assert result["reason"] == expected_reason
    assert len(calls) == 2
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == ("UNKNOWN" if reason == "cleanup" else "PARTIAL")
    assert report["reason"] == expected_reason and len(report["paths"]) == 21
    if reason == "cleanup":
        assert result["cleanup_status"] == report["cleanup_status"] == "UNCERTAIN"
        assert result["batch_reason"] == report["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    assert report["paths"][-1]["status"] == "NOT_ATTEMPTED"


def test_missing_result_restarts_as_unknown_and_never_retries(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    calls = []

    def fake(args):
        calls.append(args)
        raise RuntimeError("unknown after dispatch")

    monkeypatch.setattr(detail, "_supervise", fake)
    assert invoke(root)["reason"] == "BATCH_UNKNOWN" and len(calls) == 1
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == "UNKNOWN" and len(report["paths"]) == 11
    assert all(item["status"] == "UNKNOWN" for item in report["paths"])
    assert invoke(root)["reason"] == "RUN_ALREADY_CLAIMED" and len(calls) == 1


def test_id_emitted_before_first_write_and_lock_is_exclusive(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)

    def first(event):
        assert event["collection_id"] == ID and not (root / "detail-collections").exists()

    assert invoke(root, first)["status"] == "OBSERVED_UNVERIFIED"
    fds, chain = collection._open_collection(str(root), False)
    fd = fds[-1]
    lock, _ = collection._lock(fd, chain)
    try:
        assert invoke(root, collection_id="e" * 32)["reason"] == "LOCK_UNAVAILABLE"
    finally:
        os.close(lock)
        collection.runs._close(fds)
    assert not (root / ".lock").exists()


def test_cli_e2e_real_v3_to_v6_no_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    prior = subprocess.run(
        cli(root, config, supplement, *flags()), env=env, capture_output=True, text=True, timeout=30
    )
    assert prior.returncode == 10, (prior.stdout, prior.stderr)
    allocation = json.loads(prior.stdout.splitlines()[0])
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "run",
        "--calendar-root",
        str(root),
        "--run-id",
        allocation["run_id"],
        "--budget-seconds",
        "20",
        "--per-request-seconds",
        "3",
        "--trusted-local-posix",
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = [json.loads(x) for x in result.stdout.splitlines()]
    assert len(lines) == 2 and lines[0]["candidate_id"] == allocation["candidate_id"]
    report = collection.show(
        root=str(root), collection_id=lines[0]["collection_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert report["status"] == "OBSERVED_UNVERIFIED" and len(report["paths"]) == 2
    assert str(root) not in result.stdout + result.stderr and not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("mode", ["v1", "v2", "unknown"])
def test_ineligible_run_never_reads_candidate_or_starts(tmp_path, monkeypatch, mode):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    observed = {
        "intent": "VISIBLE_UNAUTHENTICATED",
        "record": "VISIBLE_UNAUTHENTICATED",
        "navigation_requested": True,
        "candidate_id": CID,
        "observation": {"schema": runs_v3(), "candidate_id": CID, "candidate_state": "COMMITTED"},
    }
    if mode == "v1":
        observed["observation"]["schema"] = "bt_api.calendar-observation-run/1"
    if mode == "v2":
        observed["observation"]["schema"] = "bt_api.calendar-observation-run/2"
    if mode == "unknown":
        observed["observation"]["candidate_state"] = "COMMIT_UNKNOWN"
    monkeypatch.setattr(collection.runs, "show_once", lambda **kwargs: observed)
    monkeypatch.setattr(
        collection.inbox, "read_candidate", lambda **kwargs: pytest.fail("candidate read")
    )
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("network"))
    assert invoke(root)["reason"] == "RUN_NOT_ELIGIBLE"
    assert not (root / "detail-collections").exists()


def test_large_intent_can_be_stored_and_read_without_truncation(tmp_path, monkeypatch):
    root, data = setup_fake(tmp_path, monkeypatch, 512)
    data["payload"] = candidate(512, long_paths=True)["payload"]
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    intent = root / "detail-collections" / f"collection-{ID}.intent"
    assert intent.stat().st_size > 65536
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == "OBSERVED_UNVERIFIED" and len(report["paths"]) == 512
    assert len(collection._json(report)) < collection._OUTPUT_LIMIT


@pytest.mark.parametrize("failed", ["intent", "batch_intent", "batch_result", "end"])
def test_unknown_local_commit_or_missing_result_never_retries(tmp_path, monkeypatch, failed):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    calls = []

    def supervised(args):
        calls.append(args)
        return success(args)

    monkeypatch.setattr(detail, "_supervise", supervised)
    original = collection._write

    def broken(fd, chain, name, data, limit):
        matches = (
            (failed == "intent" and name.endswith(".intent") and ".batch-" not in name)
            or (failed == "batch_intent" and ".batch-000.intent" in name)
            or (failed == "batch_result" and ".batch-000.json" in name)
            or (failed == "end" and name.endswith(".end"))
        )
        if matches:
            if failed in {"intent", "end"}:
                assert original(fd, chain, name, data, limit) == "COMMITTED"
            return "COMMIT_UNKNOWN"
        return original(fd, chain, name, data, limit)

    monkeypatch.setattr(collection, "_write", broken)
    result = invoke(root)
    assert result["status"] == "UNKNOWN"
    before = len(calls)
    assert before == {"intent": 0, "batch_intent": 0, "batch_result": 1, "end": 2}[failed]
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    if failed == "batch_result":
        assert report["status"] == "UNKNOWN" and len(report["paths"]) == 11
    invoke(root)
    assert len(calls) == before


def test_budget_before_first_batch_preserves_all_unattempted(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("network"))
    assert invoke(root, budget_seconds=0.1)["reason"] == "BUDGET_EXHAUSTED"
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert len(report["paths"]) == 11 and all(
        row["status"] == "NOT_ATTEMPTED" for row in report["paths"]
    )


def test_symlinked_result_and_tampered_manifest_are_unknown(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    folder = root / "detail-collections"
    result = folder / f"collection-{ID}.batch-000.json"
    result.unlink()
    result.symlink_to(folder / f"collection-{ID}.intent")
    assert (
        collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")[
            "status"
        ]
        == "UNKNOWN"
    )


def test_real_batches_reject_replaced_candidate_before_more_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = "large"
    prior = subprocess.run(
        cli(root, config, supplement, *flags()), env=env, capture_output=True, text=True, timeout=30
    )
    assert prior.returncode == 10, (prior.stdout, prior.stderr)
    allocation = json.loads(prior.stdout.splitlines()[0])
    env["CANDIDATE_FILE"] = str(root / "inbox" / f"candidate-{allocation['candidate_id']}.json")
    with (tmp_path / "inject-network-nav" / "sitecustomize.py").open("a") as file:
        file.write("""
if "--run-id" in sys.argv and "--_worker" not in sys.argv:
    import hashlib
    from bt_api_py import trading_calendar_detail_batch as detail_batch
    from bt_api_py import trading_calendar_candidate_inbox as candidate_inbox
    original_supervise = detail_batch._supervise
    def replace_after_first(args):
        result = original_supervise(args)
        path = Path(os.environ["CANDIDATE_FILE"])
        marker = Path(os.environ["DETAIL_MARKER"] + ".replaced")
        if not marker.exists():
            value = json.loads(path.read_bytes())
            value["payload"]["navigation_fetch"]["navigation"]["links"][0]["label"] += "x"
            value["payload_sha256"] = hashlib.sha256(candidate_inbox._canonical(value["payload"])).hexdigest()
            path.write_bytes(candidate_inbox._canonical(value))
            marker.touch()
        return result
    detail_batch._supervise = replace_after_first
""")
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "run",
        "--calendar-root",
        str(root),
        "--run-id",
        allocation["run_id"],
        "--budget-seconds",
        "30",
        "--per-request-seconds",
        "3",
        "--trusted-local-posix",
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 10, (result.stdout, result.stderr)
    lines = [json.loads(x) for x in result.stdout.splitlines()]
    assert lines[-1]["reason"] == "BATCH_STOPPED"
    assert len((tmp_path / "detail-called").read_text().splitlines()) == 10
    report = collection.show(
        root=str(root), collection_id=lines[0]["collection_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert report["status"] == "PARTIAL" and len(report["paths"]) == 82
    assert [p["status"] for p in report["paths"][:10]] == ["CANDIDATE"] * 10
    assert [p["status"] for p in report["paths"][10:20]] == ["NOT_ATTEMPTED_REJECTED"] * 10
    assert all(p["status"] == "NOT_ATTEMPTED" for p in report["paths"][20:])


def test_directory_replacement_stops_before_network(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    original = collection._write

    def replace_after_intent(fd, chain, name, data, limit):
        outcome = original(fd, chain, name, data, limit)
        if name.endswith(".intent") and ".batch-" not in name:
            old = root / "detail-collections"
            old.rename(root / "detached-test-collection")
            old.mkdir(mode=0o700)
        return outcome

    monkeypatch.setattr(collection, "_write", replace_after_intent)
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("unexpected network"))
    value = invoke(root)
    assert value["status"] == "UNKNOWN" and value["reason"] == "LOCAL_STATE_UNKNOWN"
    assert str(root) not in json.dumps(value)


def test_oversized_and_malformed_records_are_unknown(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    record = root / "detail-collections" / f"collection-{ID}.batch-000.json"
    record.write_bytes(b"x" * (collection._RESULT_LIMIT + 1))
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == "UNKNOWN" and report["reason"] == "RECORD_INVALID_OR_CHANGED"
    assert len(report["paths"]) == 1 and report["paths"][0]["status"] == "UNKNOWN"
    assert str(root) not in json.dumps(report)


def test_persistent_run_claim_blocks_different_collection_id(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    calls = []

    def fake(args):
        calls.append(args)
        raise RuntimeError("after dispatch")

    monkeypatch.setattr(detail, "_supervise", fake)
    assert invoke(root)["reason"] == "BATCH_UNKNOWN"
    again = invoke(root, collection_id="e" * 32)
    assert again["reason"] == "RUN_ALREADY_CLAIMED"
    assert again["observed_claimed_collection_id"] == ID and len(calls) == 1
    assert not (root / "detail-collections" / ("collection-" + "e" * 32 + ".intent")).exists()


@pytest.mark.parametrize("after_link", [False, True])
def test_claim_link_failure_stops_without_request(tmp_path, monkeypatch, after_link):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(collection, "_supported", lambda: True)
    original_link = os.link

    def fail_link(src, dst, *args, **kwargs):
        if dst.endswith(".collection-claim"):
            if after_link:
                original_link(src, dst, *args, **kwargs)
            raise OSError("injected link failure")
        return original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(collection.os, "link", fail_link)
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("unexpected network"))
    first = invoke(root)
    assert first["status"] == "UNKNOWN" and first["reason"] == "CLAIM_COMMIT_UNKNOWN"
    monkeypatch.setattr(collection.os, "link", original_link)
    if after_link:
        second = invoke(root, collection_id="e" * 32)
        assert second["reason"] == "RUN_ALREADY_CLAIMED"
        assert second["observed_claimed_collection_id"] == ID


def test_temp_replacement_is_not_deleted(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    fds, chain = collection._open_collection(str(root), True)
    fd = fds[-1]
    original_link = os.link
    names = []

    def replace_temp(src, dst, *args, **kwargs):
        original_link(src, dst, *args, **kwargs)
        os.unlink(src, dir_fd=fd)
        other = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        os.write(other, b"other owner")
        os.close(other)
        names.append(src)

    monkeypatch.setattr(collection.os, "link", replace_temp)
    try:
        assert collection._write(fd, chain, "target", b"ours", 100) == "COMMIT_UNKNOWN"
        assert (root / "detail-collections" / names[0]).read_bytes() == b"other owner"
    finally:
        collection.runs._close(fds)


@pytest.mark.parametrize(
    "claimed_reason", ["BATCH_STOPPED", "BUDGET_EXHAUSTED", "ALL_OBSERVED_UNVERIFIED"]
)
def test_cleanup_batch_cannot_be_disguised_by_forged_end(tmp_path, monkeypatch, claimed_reason):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)

    def fake(args):
        return detail._interrupted(None, CID, args["paths"], cleanup=True)

    monkeypatch.setattr(detail, "_supervise", fake)
    assert invoke(root)["reason"] == "BATCH_CLEANUP_UNCERTAIN"
    path = root / "detail-collections" / f"collection-{ID}.end"
    body, _ = collection._unwrap(path.read_bytes())
    body["reason"] = claimed_reason
    path.write_bytes(collection._envelope(body))
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == "UNKNOWN" and report["reason"] == "RECORD_INVALID_OR_CHANGED"
    assert len(report["paths"]) == 11 and all(
        item["status"] == "UNKNOWN" for item in report["paths"]
    )


def test_cleanup_remains_visible_if_result_installation_unknown(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    monkeypatch.setattr(
        detail,
        "_supervise",
        lambda args: detail._interrupted(None, CID, args["paths"], cleanup=True),
    )
    original = collection._write

    def fail_result(fd, chain, name, data, limit):
        if ".batch-000.json" in name:
            return "COMMIT_UNKNOWN"
        return original(fd, chain, name, data, limit)

    monkeypatch.setattr(collection, "_write", fail_result)
    result = invoke(root)
    assert result["status"] == "UNKNOWN" and result["reason"] == "BATCH_RESULT_UNKNOWN"
    assert (
        result["cleanup_status"] == "UNCERTAIN"
        and result["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    )
    assert (
        collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")[
            "status"
        ]
        == "UNKNOWN"
    )


def test_cleanup_remains_visible_when_end_is_missing(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    monkeypatch.setattr(
        detail,
        "_supervise",
        lambda args: detail._interrupted(None, CID, args["paths"], cleanup=True),
    )
    assert invoke(root)["reason"] == "BATCH_CLEANUP_UNCERTAIN"
    (root / "detail-collections" / f"collection-{ID}.end").unlink()
    report = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert report["status"] == "UNKNOWN" and report["reason"] == "BATCH_CLEANUP_UNCERTAIN"
    assert (
        report["cleanup_status"] == "UNCERTAIN"
        and report["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    )
    assert len(report["paths"]) == 11 and report["paths"][-1]["status"] == "UNKNOWN"


@pytest.mark.parametrize("mode", ["missing", "different"])
def test_show_rejects_missing_or_mismatched_run_claim(tmp_path, monkeypatch, mode):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    claim = root / "detail-collections" / f"run-{RID}.collection-claim"
    if mode == "missing":
        claim.unlink()
    else:
        body, _ = collection._unwrap(claim.read_bytes())
        body["collection_id"] = "e" * 32
        claim.write_bytes(collection._envelope(body))
    result = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert result["status"] == "UNKNOWN" and result["reason"] == "RECORD_INVALID_OR_CHANGED"
    assert len(result["paths"]) == 1 and result["paths"][0]["status"] == "UNKNOWN"
