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


@pytest.mark.parametrize("count", [1, 10])
def test_show_batch_reads_unverified_records_and_cli_is_readonly(tmp_path, monkeypatch, count):
    root, _ = setup_fake(tmp_path, monkeypatch, count)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    before = sorted(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns)
        for p in (root / "detail-collections").iterdir()
    )
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert value["status"] == "VISIBLE_UNAUTHENTICATED"
    assert value["collection_status"] == "OBSERVED_UNVERIFIED"
    assert all(
        value[k] == "NOT_PROVEN"
        for k in ("source_authentication", "coverage", "binding", "completeness", "authorization")
    )
    assert len(value["result"]["items"]) == count
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert summary["status"] == "OBSERVED_UNVERIFIED"
    assert batches == [{"result": value["result"], "retry_attempts": []}]
    item = value["result"]["items"][0]
    assert (item["transport_status"], item["parse_status"], item["identity_status"]) == (
        "HTTP_OK",
        "CANDIDATE",
        "CONSISTENT_FIELDS_UNVERIFIED",
    )
    assert item["page_sha256"] == "e" * 64
    assert item["records"][0] == {
        "kind": "future",
        "symbol": "CU",
        "venue": "SHFE",
        "raw_date": "2023-03-29",
        "trading_hours": "9:00",
        "unknown_reasons": [],
        "effective_date": "UNKNOWN",
    }
    cmd = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "show-batch",
        "--calendar-root",
        str(root),
        "--collection-id",
        ID,
        "--batch-index",
        "0",
        "--trusted-local-posix",
    ]
    guard = tmp_path / "offline-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket\n"
        "class NoNetwork(socket.socket):\n"
        "    def connect(self, *args, **kwargs): raise AssertionError('network forbidden')\n"
        "socket.socket = NoNetwork\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(guard) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
    assert process.returncode == 0 and json.loads(process.stdout) == value
    assert str(root) not in process.stdout + process.stderr
    after = sorted(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns)
        for p in (root / "detail-collections").iterdir()
    )
    assert before == after and not (root / "CURRENT.json").exists()


def test_show_batch_partial_failure_keeps_null_and_status(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)

    def fail(args):
        state = detail._state(CID, args["paths"])
        state["candidate_payload_sha256"] = DIGEST
        state["status"] = "PARTIAL"
        for item in state["items"]:
            item.update(status="UNKNOWN", reason="FETCH_FAILED", transport_status="TLS_ERROR")
        return state

    monkeypatch.setattr(detail, "_supervise", fail)
    assert invoke(root)["reason"] == "BATCH_STOPPED"
    result = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert (
        result["status"] == "VISIBLE_UNAUTHENTICATED" and result["collection_status"] == "PARTIAL"
    )
    item = result["result"]["items"][0]
    assert item["status"] == "UNKNOWN" and item["transport_status"] == "TLS_ERROR"
    assert item["records"] is None and item["page_sha256"] is None
    missing = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=1, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert missing["status"] == "UNKNOWN" and missing["result"] is None
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert summary["paths"][-1]["status"] == "NOT_ATTEMPTED"
    assert batches[0]["result"] == result["result"] and batches[1]["result"] is None


def test_show_batch_missing_end_and_cleanup_are_incomplete(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    monkeypatch.setattr(
        detail,
        "_supervise",
        lambda args: detail._interrupted(None, CID, args["paths"], cleanup=True),
    )
    assert invoke(root)["reason"] == "BATCH_CLEANUP_UNCERTAIN"
    (root / "detail-collections" / f"collection-{ID}.end").unlink()
    result = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    assert result["collection_status"] == result["collection_reason"] == "INCOMPLETE_OR_UNKNOWN"
    assert result["result"]["status"] == "CLEANUP_UNCERTAIN"
    assert result["result"]["process_cleanup"] == "NOT_PROVEN"


@pytest.mark.parametrize("index", [True, False, -1, 52, 10**1000, 1.0, None, "0"])
def test_show_batch_invalid_index_never_reflects_input(tmp_path, index):
    result = collection.show_batch(
        root=str(tmp_path), collection_id=ID, batch_index=index, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert result["status"] == "UNKNOWN" and result["reason"] == "INVALID_ARGUMENTS"
    assert result["batch_index"] is None


def test_show_batch_out_of_range_and_missing_files_are_unknown(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    too_high = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=1, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert too_high["status"] == "UNKNOWN" and too_high["reason"] == "BATCH_INDEX_OUT_OF_RANGE"
    (root / "detail-collections" / f"collection-{ID}.batch-000.json").unlink()
    missing = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert missing["status"] == "UNKNOWN" and missing["result"] is None


@pytest.mark.parametrize(
    "mode",
    [
        "claim",
        "intent",
        "start_index",
        "start_digest",
        "result_index",
        "result_digest",
        "candidate_hash",
        "path",
        "end",
        "sensitive",
        "control",
        "symlink",
        "large",
        "progress",
    ],
)
def test_show_batch_rejects_tampering_and_unsafe_input(tmp_path, monkeypatch, mode):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    folder = root / "detail-collections"
    if mode == "claim":
        path = folder / f"run-{RID}.collection-claim"
    elif mode == "intent":
        path = folder / f"collection-{ID}.intent"
    elif mode.startswith("start"):
        path = folder / f"collection-{ID}.batch-000.intent"
    elif mode == "end":
        path = folder / f"collection-{ID}.end"
    else:
        path = folder / f"collection-{ID}.batch-000.json"
    if mode == "symlink":
        path.unlink()
        path.symlink_to(folder / f"collection-{ID}.intent")
    elif mode == "large":
        path.write_bytes(b"x" * (collection._RESULT_LIMIT + 1))
    else:
        body, _ = collection._unwrap(path.read_bytes())
        if mode == "claim":
            body["collection_id"] = "f" * 32
        elif mode == "intent":
            body["candidate_id"] = "f" * 32
        elif mode in {"start_index", "result_index"}:
            body["index"] = 1
        elif mode in {"start_digest", "result_digest"}:
            body["intent_sha256"] = "0" * 64
        elif mode == "candidate_hash":
            body["result"]["candidate_payload_sha256"] = "f" * 64
        elif mode == "path":
            body["result"]["items"][0]["path"] = "/products/futures/metal/other_f/"
        elif mode == "end":
            body["reason"] = "BATCH_STOPPED"
        elif mode == "sensitive":
            body["result"]["items"][0]["records"][0]["trading_hours"] = str(root)
        elif mode == "control":
            body["result"]["items"][0]["records"][0]["trading_hours"] = "9:00\x1b"
        elif mode == "progress":
            body["result"]["status"] = "IN_PROGRESS"
        path.write_bytes(collection._envelope(body))
    result = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert result["status"] == "UNKNOWN" and result["result"] is None
    assert str(root) not in json.dumps(result)


def test_show_batch_last_of_512_remains_bounded(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 512)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    original_read = collection._read
    reads = []

    def counted(*args):
        reads.append(args[2])
        return original_read(*args)

    monkeypatch.setattr(collection, "_read", counted)
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=51, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert value["status"] == "VISIBLE_UNAUTHENTICATED" and len(value["result"]["items"]) == 2
    assert len(collection._json(value)) <= collection._OUTPUT_LIMIT
    assert len(reads) == 2 + 2 * 52 + 1


@pytest.mark.parametrize("index", ["-1", "52", "true", "01", "9" * 1000])
def test_show_batch_cli_invalid_index_is_bounded_and_readonly(tmp_path, index):
    root, _, _, _ = setup_preview(tmp_path)
    cmd = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "show-batch",
        "--calendar-root",
        str(root),
        "--collection-id",
        ID,
        "--batch-index",
        index,
        "--trusted-local-posix",
    ]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert process.returncode == 2 and json.loads(process.stdout) == {
        "status": "ERROR",
        "reason": "INVALID_ARGUMENTS",
    }
    assert str(root) not in process.stdout + process.stderr
    assert not (root / "detail-collections").exists() and not (root / "CURRENT.json").exists()


def test_show_batch_preserves_later_cleanup_when_end_missing(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    calls = []

    def first_ok_then_unknown(args):
        calls.append(args)
        return (
            success(args)
            if len(calls) == 1
            else detail._interrupted(None, CID, args["paths"], cleanup=True)
        )

    monkeypatch.setattr(detail, "_supervise", first_ok_then_unknown)
    assert invoke(root)["reason"] == "BATCH_CLEANUP_UNCERTAIN"
    (root / "detail-collections" / f"collection-{ID}.end").unlink()
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert value["status"] == "VISIBLE_UNAUTHENTICATED"
    assert value["result"]["status"] == "OBSERVED_UNVERIFIED"
    assert value["collection_status"] == value["collection_reason"] == "INCOMPLETE_OR_UNKNOWN"
    assert (
        value["cleanup_status"] == "UNCERTAIN"
        and value["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    )
    cmd = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "show-batch",
        "--calendar-root",
        str(root),
        "--collection-id",
        ID,
        "--batch-index",
        "0",
        "--trusted-local-posix",
    ]
    process = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert process.returncode == 23 and json.loads(process.stdout) == value
    assert str(root) not in process.stdout + process.stderr


class FakeMonotonic:
    def __init__(self):
        self.now = 10_000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 1.0
        self.sleeps.append(seconds)
        self.now += seconds


def timeout_report(args, transport="NETWORK_TIMEOUT", offsets=None):
    state = success(args)
    state["status"] = "PARTIAL"
    for index in range(len(state["items"])) if offsets is None else offsets:
        item = state["items"][index]
        item.update(
            status="UNKNOWN",
            reason="FETCH_FAILED",
            transport_status=transport,
            parse_status="NOT_OBSERVED",
            identity_status="NOT_ASSESSED",
            page_sha256=None,
            records=None,
        )
    return state


def test_timeout_retry_twice_persists_every_attempt_and_never_regrabs_success(
    tmp_path, monkeypatch
):
    root, data = setup_fake(tmp_path, monkeypatch, 2)
    clock, calls, ends = FakeMonotonic(), [], []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append((clock.now, list(args["paths"]), args["expected_candidate_payload_sha256"]))
        clock.now += 3
        ends.append(clock.now)
        return (
            timeout_report(args, offsets=[1])
            if len(calls) == 1
            else timeout_report(args)
            if len(calls) == 2
            else success(args)
        )

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, budget_seconds=2000, retry_timeouts=True)
    assert outcome["status"] == "OBSERVED_UNVERIFIED" and outcome["retry"] == "D67_20_TIMEOUT_RETRY"
    paths = [x["path"] for x in data["payload"]["navigation_fetch"]["navigation"]["links"]]
    assert [x[1] for x in calls] == [paths, paths[1:], paths[1:]]
    assert all(x[2] == DIGEST for x in calls)
    assert calls[1][0] - ends[0] >= 600 and calls[2][0] - ends[1] >= 600
    assert clock.sleeps and max(clock.sleeps) <= 1
    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert shown["status"] == "OBSERVED_UNVERIFIED" and shown["retry"] == "D67_20_TIMEOUT_RETRY"
    assert [x["status"] for x in shown["paths"]] == ["CANDIDATE", "CANDIDATE"]
    assert [x["attempt"] for x in shown["paths"][1]["attempts"]] == [0, 1, 2]
    batch = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert batch["result"]["items"][1]["transport_status"] == "NETWORK_TIMEOUT"
    assert [x["result"]["items"][0]["transport_status"] for x in batch["retry_attempts"]] == [
        "NETWORK_TIMEOUT",
        "HTTP_OK",
    ]
    assert batch["final_paths"][1]["status"] == "CANDIDATE"
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("transport", ["NETWORK_TIMEOUT", "CHILD_TIMEOUT"])
def test_retry_budget_stops_without_new_request(tmp_path, monkeypatch, transport):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        return timeout_report(args, transport=transport)

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, budget_seconds=20, retry_timeouts=True)
    assert outcome["status"] == "PARTIAL" and outcome["reason"] == "RETRY_BUDGET_EXHAUSTED"
    assert len(calls) == 1 and not clock.sleeps
    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert shown["reason"] == "RETRY_BUDGET_EXHAUSTED" and len(shown["paths"]) == 11
    assert shown["paths"][-1]["status"] == "NOT_ATTEMPTED"


@pytest.mark.parametrize("transport", ["HTTP_ERROR", "TLS_ERROR", "NETWORK_ERROR", "PARSER_ERROR"])
def test_mixed_nonretryable_stops_entire_batch_without_retry(tmp_path, monkeypatch, transport):
    root, _ = setup_fake(tmp_path, monkeypatch, 2)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        state = timeout_report(args, offsets=[0])
        item = state["items"][1]
        item.update(
            status="UNKNOWN",
            reason="FETCH_FAILED",
            transport_status=transport,
            parse_status="NOT_OBSERVED",
            identity_status="NOT_ASSESSED",
            page_sha256=None,
            records=None,
        )
        return state

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, retry_timeouts=True)
    assert outcome["reason"] == "BATCH_STOPPED" and len(calls) == 1 and not clock.sleeps
    assert (
        collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")[
            "reason"
        ]
        == "BATCH_STOPPED"
    )


def test_zero_retry_keeps_initial_timeout_and_exhausted_status(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)
    monkeypatch.setattr(
        detail, "_supervise", lambda args: (calls.append(args), timeout_report(args))[1]
    )
    outcome = invoke(root, retry_timeouts=True, max_retries=0)
    assert outcome["reason"] == "RETRY_EXHAUSTED" and len(calls) == 1 and not clock.sleeps
    assert (
        collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")[
            "reason"
        ]
        == "RETRY_EXHAUSTED"
    )


@pytest.mark.parametrize("transport", ["NETWORK_TIMEOUT", "CHILD_TIMEOUT"])
def test_retry_exhaustion_keeps_all_three_attempts(tmp_path, monkeypatch, transport):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append((clock.now, args["paths"]))
        clock.now += 1
        return timeout_report(args, transport=transport)

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, retry_timeouts=True, budget_seconds=2000)
    assert outcome["reason"] == "RETRY_EXHAUSTED" and len(calls) == 3
    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert shown["status"] == "PARTIAL" and shown["reason"] == "RETRY_EXHAUSTED"
    assert [x["transport_status"] for x in shown["paths"][0]["attempts"]] == [transport] * 3
    batch = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert (
        len(batch["retry_attempts"]) == 2
        and batch["result"]["items"][0]["transport_status"] == transport
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"max_retries": 3},
        {"max_retries": True},
        {"retry_interval_seconds": -1},
        {"retry_interval_seconds": float("nan")},
        {"retry_interval_seconds": float("inf")},
        {"retry_interval_seconds": 86401},
    ],
)
def test_invalid_retry_config_before_write_and_network(tmp_path, monkeypatch, kwargs):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    events = []
    monkeypatch.setattr(detail, "_supervise", lambda _: pytest.fail("unexpected network"))
    value = invoke(root, events.append, retry_timeouts=True, **kwargs)
    assert value["reason"] == "INVALID_ARGUMENTS" and not events
    assert not (root / "detail-collections").exists()


def test_zero_interval_is_explicit_and_retries_once(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        return timeout_report(args) if len(calls) == 1 else success(args)

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, retry_timeouts=True, max_retries=1, retry_interval_seconds=0)
    assert outcome["status"] == "OBSERVED_UNVERIFIED" and len(calls) == 2 and not clock.sleeps
    assert (
        collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")[
            "status"
        ]
        == "OBSERVED_UNVERIFIED"
    )


@pytest.mark.parametrize(
    "mode", ["intent_before", "intent_after", "result_before", "result_after", "raises", "cleanup"]
)
def test_retry_unknown_stops_and_claim_prevents_replay(tmp_path, monkeypatch, mode):
    root, _ = setup_fake(tmp_path, monkeypatch, 2)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        if len(calls) == 1:
            return timeout_report(args, offsets=[0, 1])
        if mode == "raises":
            raise RuntimeError("unknown after request")
        if mode == "cleanup":
            return detail._interrupted(None, CID, args["paths"], cleanup=True)
        return success(args)

    monkeypatch.setattr(detail, "_supervise", fake)
    original = collection._write

    def failing(fd, chain, name, data, limit):
        target = ".retry-000-1." in name and (
            ("intent" in mode and name.endswith(".intent"))
            or ("result" in mode and name.endswith(".json"))
        )
        if target:
            if mode.endswith("after"):
                assert original(fd, chain, name, data, limit) == "COMMITTED"
            return "COMMIT_UNKNOWN"
        return original(fd, chain, name, data, limit)

    monkeypatch.setattr(collection, "_write", failing)
    outcome = invoke(root, budget_seconds=2000, retry_timeouts=True)
    assert outcome["status"] == "UNKNOWN"
    if mode == "cleanup":
        assert outcome["reason"] == "BATCH_CLEANUP_UNCERTAIN"
        assert outcome["cleanup_status"] == "UNCERTAIN"
    assert len(calls) == (1 if mode.startswith("intent") else 2)
    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert shown["status"] == "UNKNOWN" and len(shown["paths"]) == 2
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert summary["status"] == "UNKNOWN" and batches is None
    if mode in {"intent_after", "result_before", "raises"}:
        assert shown["paths"][0]["attempts"][-1] == {
            "attempt": 1,
            "status": "UNKNOWN",
            "transport_status": "UNKNOWN",
        }
    assert (
        invoke(root, retry_timeouts=True, collection_id="f" * 32)["reason"] == "RUN_ALREADY_CLAIMED"
    )
    assert len(calls) == (1 if mode.startswith("intent") else 2)


@pytest.mark.parametrize("end_state", ["present", "missing", "forged_success", "forged_stop"])
def test_retry_candidate_with_uncertain_cleanup_preserves_observations(
    tmp_path, monkeypatch, end_state
):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(collection, "time", FakeMonotonic())
    calls = []

    def fake(args):
        calls.append(args)
        if len(calls) == 1:
            return timeout_report(args)
        return detail._interrupted(success(args), CID, args["paths"], cleanup=True)

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, retry_timeouts=True, retry_interval_seconds=0)
    assert outcome["status"] == "UNKNOWN"
    assert outcome["reason"] == "BATCH_CLEANUP_UNCERTAIN"
    assert outcome["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    end = root / "detail-collections" / f"collection-{ID}.end"
    if end_state == "missing":
        end.unlink()
    elif end_state.startswith("forged_"):
        body, _ = collection._unwrap(end.read_bytes())
        body["reason"] = (
            "ALL_OBSERVED_UNVERIFIED" if end_state == "forged_success" else "BATCH_STOPPED"
        )
        end.write_bytes(collection._envelope(body))

    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    batch = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    if end_state.startswith("forged_"):
        assert shown["status"] == "UNKNOWN" and shown["reason"] == "RECORD_INVALID_OR_CHANGED"
        assert batch["status"] == "UNKNOWN" and batch["result"] is None
        return

    assert shown["status"] == "UNKNOWN"
    assert shown["reason"] == (
        "BATCH_CLEANUP_UNCERTAIN" if end_state == "present" else "INCOMPLETE_OR_UNKNOWN"
    )
    assert shown["cleanup_status"] == batch["cleanup_status"] == "UNCERTAIN"
    assert shown["batch_reason"] == batch["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    assert [a["transport_status"] for a in shown["paths"][0]["attempts"]] == [
        "NETWORK_TIMEOUT",
        "HTTP_OK",
    ]
    assert batch["status"] == "VISIBLE_UNAUTHENTICATED"
    assert batch["collection_status"] == (
        "UNKNOWN" if end_state == "present" else "INCOMPLETE_OR_UNKNOWN"
    )
    assert batch["result"]["items"][0]["transport_status"] == "NETWORK_TIMEOUT"
    assert len(batch["retry_attempts"]) == 1
    assert batch["retry_attempts"][0]["result"]["status"] == "CLEANUP_UNCERTAIN"
    assert batch["retry_attempts"][0]["result"]["items"][0]["status"] == "CANDIDATE"
    assert len(calls) == 2


@pytest.mark.parametrize("failure_call", [1, 2])
def test_supervisor_exception_retains_cleanup_marker_and_stops(tmp_path, monkeypatch, failure_call):
    root, _ = setup_fake(tmp_path, monkeypatch, 11)
    monkeypatch.setattr(collection, "time", FakeMonotonic())
    calls = []

    def core(args):
        calls.append(args)
        if len(calls) == failure_call:
            raise RuntimeError("synthetic supervisor failure")
        return timeout_report(args)

    # Keep the real _supervise wrapper so it marks the raised exception.
    monkeypatch.setattr(detail, "_supervise_core", core)
    outcome = invoke(root, retry_timeouts=True, retry_interval_seconds=0)
    assert outcome["status"] == "UNKNOWN"
    assert outcome["reason"] == ("BATCH_UNKNOWN" if failure_call == 1 else "RETRY_UNKNOWN")
    assert outcome["cleanup_status"] == "UNCERTAIN"
    assert outcome["batch_reason"] == "WORKER_CLEANUP_UNCERTAIN"
    assert len(calls) == failure_call
    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert shown["status"] == "UNKNOWN"
    assert len(shown["paths"]) == 11
    assert (
        invoke(root, collection_id="f" * 32, retry_timeouts=True)["reason"] == "RUN_ALREADY_CLAIMED"
    )
    assert len(calls) == failure_call


def test_candidate_change_in_retry_stops_remaining_paths(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 2)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        if len(calls) == 1:
            return timeout_report(args)
        state = detail._state(CID, args["paths"])
        state["candidate_payload_sha256"] = "f" * 64
        return detail._reject(state, "CANDIDATE_DIGEST_MISMATCH")

    monkeypatch.setattr(detail, "_supervise", fake)
    assert invoke(root, budget_seconds=2000, retry_timeouts=True)["reason"] == "BATCH_STOPPED"
    assert len(calls) == 2 and len(calls[1]["paths"]) == 1
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert (
        value["status"] == "VISIBLE_UNAUTHENTICATED"
        and value["retry_attempts"][0]["result"]["reason"] == "CANDIDATE_DIGEST_MISMATCH"
    )


def test_retry_512_paths_twice_has_bounded_complete_observation(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 512)
    clock, per_path = FakeMonotonic(), {}
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        if len(args["paths"]) > 1:
            return timeout_report(args)
        path = args["paths"][0]
        per_path[path] = per_path.get(path, 0) + 1
        return timeout_report(args) if per_path[path] == 1 else success(args)

    monkeypatch.setattr(detail, "_supervise", fake)
    outcome = invoke(root, retry_timeouts=True, retry_interval_seconds=0, budget_seconds=2000)
    assert outcome["status"] == "OBSERVED_UNVERIFIED" and len(per_path) == 512
    assert all(value == 2 for value in per_path.values())
    original = collection._read
    reads = []

    def counted(*args):
        reads.append(args[2])
        return original(*args)

    monkeypatch.setattr(collection, "_read", counted)
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert value["status"] == "VISIBLE_UNAUTHENTICATED" and len(value["retry_attempts"]) == 20
    assert all(item["transport_status"] == "NETWORK_TIMEOUT" for item in value["result"]["items"])
    assert all(item["status"] == "CANDIDATE" for item in value["final_paths"])
    assert len(collection._json(value)) <= collection._RETRY_OUTPUT_LIMIT
    assert len(reads) == 2 + 2 * 52 + 4 * 512 + 1
    reads.clear()
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert summary["status"] == "OBSERVED_UNVERIFIED" and len(batches) == 52
    assert sum(len(batch["retry_attempts"]) for batch in batches) == 1024
    assert len(reads) == 2 + 2 * 52 + 4 * 512 + 1
    assert not (root / "CURRENT.json").exists()


def test_show_all_over_budget_rejects_without_partial_capture(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    monkeypatch.setattr(collection, "_ALL_CAPTURE_LIMIT", 100)
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert summary["status"] == "UNKNOWN" and summary["reason"] == "OUTPUT_LIMIT"
    assert batches is None


def test_show_all_combined_output_limit_clears_successes(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    monkeypatch.setattr(detail, "_supervise", success)
    assert invoke(root)["status"] == "OBSERVED_UNVERIFIED"
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    initial_size = len(collection._json(batches[0]["result"]))
    assert initial_size < len(collection._json([summary, batches]))
    monkeypatch.setattr(collection, "_ALL_CAPTURE_LIMIT", initial_size)
    summary, batches = collection._show_all(
        root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert summary["status"] == "UNKNOWN" and summary["reason"] == "OUTPUT_LIMIT"
    assert all(path["status"] == "UNKNOWN" for path in summary["paths"])
    assert batches is None


def test_retry_show_batch_output_limit_rejects_without_truncation(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        return timeout_report(args) if len(calls) == 1 else success(args)

    monkeypatch.setattr(detail, "_supervise", fake)
    assert (
        invoke(root, retry_timeouts=True, retry_interval_seconds=0)["status"]
        == "OBSERVED_UNVERIFIED"
    )
    monkeypatch.setattr(collection, "_RETRY_OUTPUT_LIMIT", 100)
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert value["status"] == "UNKNOWN" and value["reason"] == "OUTPUT_LIMIT"
    assert value["result"] is None and "retry_attempts" not in value


@pytest.mark.parametrize(
    "mode",
    ["index", "attempt", "previous_sha", "delay", "digest", "success_before_retry", "end_reason"],
)
def test_retry_record_tampering_is_unknown(tmp_path, monkeypatch, mode):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    clock, calls = FakeMonotonic(), []
    monkeypatch.setattr(collection, "time", clock)

    def fake(args):
        calls.append(args)
        return timeout_report(args) if len(calls) == 1 else success(args)

    monkeypatch.setattr(detail, "_supervise", fake)
    assert (
        invoke(root, retry_timeouts=True, retry_interval_seconds=10)["status"]
        == "OBSERVED_UNVERIFIED"
    )
    folder = root / "detail-collections"
    if mode in {"index", "attempt", "previous_sha", "delay"}:
        path = folder / f"collection-{ID}.retry-000-1.intent"
        body, _ = collection._unwrap(path.read_bytes())
        if mode == "index":
            body["path_index"] = 1
        if mode == "attempt":
            body["attempt"] = 2
        if mode == "previous_sha":
            body["previous_result_sha256"] = "f" * 64
        if mode == "delay":
            body["started_ns"] = body["previous_finished_ns"]
    elif mode == "end_reason":
        path = folder / f"collection-{ID}.end"
        body, _ = collection._unwrap(path.read_bytes())
        body["reason"] = "RETRY_EXHAUSTED"
    else:
        path = folder / (
            f"collection-{ID}.batch-000.json"
            if mode == "success_before_retry"
            else f"collection-{ID}.retry-000-1.json"
        )
        body, _ = collection._unwrap(path.read_bytes())
        if mode == "digest":
            body["result"]["candidate_payload_sha256"] = "f" * 64
        if mode == "success_before_retry":
            body["result"] = success(calls[0])
    path.write_bytes(collection._envelope(body))
    value = collection.show_batch(
        root=str(root), collection_id=ID, batch_index=0, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert value["status"] == "UNKNOWN" and value["result"] is None


def test_retry_cli_with_supervised_synthetic_transport_no_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    prior = subprocess.run(
        cli(root, config, supplement, *flags()), env=env, capture_output=True, text=True, timeout=30
    )
    assert prior.returncode == 10, (prior.stdout, prior.stderr)
    allocation = json.loads(prior.stdout.splitlines()[0])
    with (tmp_path / "inject-network-nav" / "sitecustomize.py").open("a") as file:
        file.write("""
if "--_worker" in sys.argv:
    from datetime import datetime, timezone
    from bt_api_ctp import calendar_shfe_detail_fetch as retry_detail
    previous_detail = retry_detail.collect_detail_page
    def synthetic_timeout_once(*, path, **kwargs):
        marker = Path(os.environ["DETAIL_MARKER"] + ".first-timeout")
        if not marker.exists():
            marker.touch()
            return retry_detail.DetailFetchResult("UNKNOWN", "NETWORK_TIMEOUT", "NOT_OBSERVED", datetime.now(timezone.utc), None, path, retry_detail._path_kind(path), "SUPERVISED_DEFAULT_TLS_POLICY", "NOT_ASSESSED")
        return previous_detail(path=path, **kwargs)
    retry_detail.collect_detail_page = synthetic_timeout_once
""")
    cmd = [
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
        "--retry-timeouts",
        "--retry-interval-seconds",
        "0",
        "--max-retries",
        "2",
        "--trusted-local-posix",
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert (
        lines[-1]["status"] == "OBSERVED_UNVERIFIED"
        and lines[-1]["retry"] == "D67_20_TIMEOUT_RETRY"
    )
    batch = collection.show_batch(
        root=str(root),
        collection_id=lines[0]["collection_id"],
        batch_index=0,
        filesystem="TRUSTED_LOCAL_POSIX",
    )
    assert batch["status"] == "VISIBLE_UNAUTHENTICATED" and len(batch["retry_attempts"]) == 1
    assert batch["result"]["items"][0]["transport_status"] == "NETWORK_TIMEOUT"
    assert batch["retry_attempts"][0]["result"]["items"][0]["transport_status"] == "HTTP_OK"
    assert len((tmp_path / "detail-called").read_text().splitlines()) == 2
    assert str(root) not in result.stdout + result.stderr and not (root / "CURRENT.json").exists()


def test_retry_cli_flags_rejected_without_retry_before_write(tmp_path):
    root, _, _, _ = setup_preview(tmp_path)
    cmd = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "run",
        "--calendar-root",
        str(root),
        "--run-id",
        RID,
        "--budget-seconds",
        "20",
        "--max-retries",
        "1",
        "--trusted-local-posix",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and json.loads(result.stdout)["reason"] == "INVALID_ARGUMENTS"
    assert not (root / "detail-collections").exists()


def test_retry_end_uses_first_unresolved_path_not_later_timeout(tmp_path, monkeypatch):
    root, _ = setup_fake(tmp_path, monkeypatch, 2)
    clock = FakeMonotonic()
    monkeypatch.setattr(collection, "time", clock)
    monkeypatch.setattr(detail, "_supervise", timeout_report)
    outcome = invoke(root, retry_timeouts=True, retry_interval_seconds=0)
    assert outcome["reason"] == "RETRY_EXHAUSTED"
    end = root / "detail-collections" / f"collection-{ID}.end"
    body, _ = collection._unwrap(end.read_bytes())
    body["reason"] = "RETRY_BUDGET_EXHAUSTED"
    end.write_bytes(collection._envelope(body))
    shown = collection.show(root=str(root), collection_id=ID, filesystem="TRUSTED_LOCAL_POSIX")
    assert shown["status"] == "UNKNOWN" and shown["reason"] == "RECORD_INVALID_OR_CHANGED"
