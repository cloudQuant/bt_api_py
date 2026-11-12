"""Evidence to partial holiday candidate, entirely offline."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from bt_api_py import trading_calendar_holiday_evidence_candidate as bridge
from bt_api_py import trading_calendar_source_evidence as evidence
from tests.unit.test_trading_calendar_candidate_inbox import holiday_html
from tests.unit.test_trading_calendar_source_evidence import fake_runner

MODE = bridge.TRUSTED_LOCAL_POSIX
EID = "b" * 32
CID = "a" * 32


def capture(root, raw=None):
    return evidence.capture(
        root=str(root),
        evidence_id=EID,
        kind="holiday",
        filesystem=MODE,
        emit=lambda _: None,
        runner=fake_runner(holiday_html() if raw is None else raw, []),
    )


def build(root, candidate_id=CID):
    return bridge.build_once(
        root=str(root),
        evidence_id=EID,
        candidate_id=candidate_id,
        filesystem=MODE,
        emit=lambda _: None,
    )


def read(root, include_source_text=False):
    return bridge.show(
        root=str(root), candidate_id=CID, filesystem=MODE, include_source_text=include_source_text
    )


def test_build_show_revalidates_actual_evidence(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    (tmp_path / "CURRENT.json").write_text("unchanged")
    assert build(tmp_path)["status"] == "COMMITTED"
    result = read(tmp_path, True)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    body = result["candidate"]
    assert body["year"] == 2026 and body["status"] == "PARTIAL"
    assert len(body["unresolved_nights"]) == 7
    assert all(n["trading_day"] is None for n in body["unresolved_nights"])
    assert body["content"]["trading_days"] and all(
        r["status"] == "closed" and r["venue"] == "SHFE" for r in body["content"]["trading_days"]
    )
    assert result["source_text"].encode() == holiday_html()
    assert all(n["raw_text"] for n in body["unresolved_nights"])
    summary = read(tmp_path)
    assert "source_text" not in summary
    assert all("raw_text" not in n for n in summary["candidate"]["unresolved_nights"])
    assert body["publication"] == "NOT_AUTHORIZED"
    assert build(tmp_path)["status"] == "NOT_COMMITTED"
    assert (tmp_path / "CURRENT.json").read_text() == "unchanged"


def test_source_deleted_or_tampered_is_unknown(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    assert build(tmp_path)["status"] == "COMMITTED"
    path = tmp_path / "source-evidence" / f"result-{EID}.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b"HTTP_OK", b"HTTP_NO", 1))
    assert read(tmp_path)["status"] == "UNKNOWN"
    path.unlink()
    assert read(tmp_path)["status"] == "UNKNOWN"


def test_candidate_tampering_and_invalid_source(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    assert build(tmp_path)["status"] == "COMMITTED"
    path = tmp_path / "holiday-evidence-candidates" / f"candidate-{CID}.json"
    body, _ = bridge.storage._unwrap(path.read_bytes())
    body["unresolved_nights"].pop()
    path.write_bytes(bridge.storage._envelope(body))
    assert read(tmp_path)["status"] == "UNKNOWN"


@pytest.mark.parametrize("raw", [b"\xff", b"not a holiday page"])
def test_invalid_or_non_utf8_source_cannot_build(tmp_path, raw):
    assert capture(tmp_path, raw)["status"] == "COMMITTED"
    assert build(tmp_path)["status"] == "NOT_COMMITTED"
    assert read(tmp_path)["status"] == "UNKNOWN"


def test_concurrent_same_id_cannot_overwrite(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: build(tmp_path), range(2)))
    assert sum(r["status"] == "COMMITTED" for r in results) == 1
    assert read(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_cli_show_is_summary_only(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    assert build(tmp_path)["status"] == "COMMITTED"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_holiday_evidence_candidate",
            "show",
            "--calendar-root",
            str(tmp_path),
            "--candidate-id",
            CID,
            "--trusted-local-posix",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 10
    result = json.loads(proc.stdout)
    assert result["unresolved_night_count"] == 7
    assert str(tmp_path) not in proc.stdout and "raw_text" not in proc.stdout


def test_interrupted_write_remains_unknown(tmp_path, monkeypatch):
    assert capture(tmp_path)["status"] == "COMMITTED"

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bridge.storage, "_write", interrupted)
    assert build(tmp_path)["status"] == "COMMIT_UNKNOWN"


def test_source_digest_mismatch_rejected_before_write(tmp_path, monkeypatch):
    assert capture(tmp_path)["status"] == "COMMITTED"
    original = bridge.evidence.show

    def changed(**kwargs):
        result = original(**kwargs)
        result["source_sha256"] = "0" * 64
        return result

    monkeypatch.setattr(bridge.evidence, "show", changed)
    assert build(tmp_path)["status"] == "NOT_COMMITTED"
    assert not (tmp_path / "holiday-evidence-candidates").exists()


def test_changed_source_before_write_is_rejected(tmp_path, monkeypatch):
    assert capture(tmp_path)["status"] == "COMMITTED"
    original = bridge.evidence.show
    count = 0

    def changed(**kwargs):
        nonlocal count
        count += 1
        result = original(**kwargs)
        if count == 2:
            result["payload_sha256"] = "0" * 64
        return result

    monkeypatch.setattr(bridge.evidence, "show", changed)
    result = build(tmp_path)
    assert result["status"] == "NOT_COMMITTED" and result["reason"] == "SOURCE_CHANGED"


def test_candidate_permissions_and_symlink_rejected(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    assert build(tmp_path)["status"] == "COMMITTED"
    path = tmp_path / "holiday-evidence-candidates" / f"candidate-{CID}.json"
    path.chmod(0o644)
    assert read(tmp_path)["status"] == "UNKNOWN"
    path.chmod(0o600)
    moved = path.with_name("saved.json")
    path.rename(moved)
    path.symlink_to(moved.name)
    assert read(tmp_path)["status"] == "UNKNOWN"


def test_write_uncertain_after_commit_uses_original_id(tmp_path, monkeypatch):
    assert capture(tmp_path)["status"] == "COMMITTED"
    original = bridge.storage._write

    def uncertain(*args, **kwargs):
        assert original(*args, **kwargs) == "COMMITTED"
        return "COMMIT_UNKNOWN"

    monkeypatch.setattr(bridge.storage, "_write", uncertain)
    assert build(tmp_path)["status"] == "COMMIT_UNKNOWN"
    assert read(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"


def test_cli_build_emits_id_before_terminal_result(tmp_path):
    assert capture(tmp_path)["status"] == "COMMITTED"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_holiday_evidence_candidate",
            "build",
            "--calendar-root",
            str(tmp_path),
            "--candidate-id",
            CID,
            "--evidence-id",
            EID,
            "--trusted-local-posix",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert proc.returncode == 10
    assert lines[0] == {"event": "HOLIDAY_CANDIDATE_ID_ALLOCATED", "candidate_id": CID}
    assert lines[1]["status"] == "COMMITTED" and lines[1]["candidate_id"] == CID
    assert str(tmp_path) not in proc.stdout and "raw_text" not in proc.stdout
