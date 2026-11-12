"""Offline same-evidence hours/holiday candidate assembly."""

import copy
import json
import runpy
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_holiday_evidence_candidate as holiday
from bt_api_py import trading_calendar_hours_evidence_candidate as bridge
from bt_api_py import trading_calendar_source_evidence as evidence
from tests.unit.test_trading_calendar_candidate_inbox import HOURS, holiday_html
from tests.unit.test_trading_calendar_hours_candidate import mapping
from tests.unit.test_trading_calendar_source_evidence import fake_runner

FS = "TRUSTED_LOCAL_POSIX"
HE, TE, HC, CID = "a" * 32, "b" * 32, "c" * 32, "d" * 32


def selections():
    result = mapping()
    result[1]["venue"] = "SHFE"
    return result


@pytest.fixture
def root(tmp_path):
    for kind, eid, raw in (("holiday", HE, holiday_html()), ("hours", TE, HOURS)):
        assert (
            evidence.capture(
                root=str(tmp_path),
                evidence_id=eid,
                kind=kind,
                filesystem=FS,
                emit=lambda _: None,
                runner=fake_runner(raw, []),
            )["status"]
            == "COMMITTED"
        )
    assert (
        holiday.build_once(
            root=str(tmp_path), evidence_id=HE, candidate_id=HC, filesystem=FS, emit=lambda _: None
        )["status"]
        == "COMMITTED"
    )
    return tmp_path


def build(root, rows=None, candidate_id=CID):
    return bridge.build_once(
        root=str(root),
        evidence_id=TE,
        holiday_candidate_id=HC,
        candidate_id=candidate_id,
        selections=selections() if rows is None else rows,
        filesystem=FS,
        emit=lambda _: None,
    )


def show(root, **kwargs):
    return bridge.show(root=str(root), candidate_id=CID, filesystem=FS, **kwargs)


def test_two_sources_to_partial_candidate_and_public_text_opt_in(root):
    assert build(root)["status"] == "COMMITTED"
    result = show(root)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    body = result["candidate"]
    assert {row["kind"] for row in body["content"]["trading_hours"]} == {"future", "option"}
    assert all(
        not row["windows_complete"] and not row["exceptions_complete"]
        for row in body["content"]["trading_hours"]
    )
    for row in body["content"]["trading_hours"]:
        assert (row["valid_from"], row["valid_to"]) == ("2026-01-01", "2027-12-31")
        assert {(w["start"], w["end"]) for w in row["day_windows"]} == {
            ("08:55", "08:59"),
            ("08:59", "09:00"),
            ("09:00", "10:15"),
            ("10:30", "11:30"),
            ("13:30", "15:00"),
        }
    assert result["page_notes_count"] == 1 and result["page_notes_status"] == "REDACTED"
    assert body["page_notes"] == []
    assert len(body["holiday_unresolved_nights"]) == 7
    assert len(body["hours_unresolved_nights"]) == 2
    assert all(
        item["trading_day"] is None and "raw_text" not in item
        for item in body["holiday_unresolved_nights"]
    )
    assert all(
        "raw_line" not in stage
        for item in body["hours_unresolved_nights"]
        for stage in item["stages"]
    )
    assert body["publication"] == "NOT_AUTHORIZED" and body["coverage"] == "NOT_PROVEN"
    full = show(root, include_source_text=True)
    assert (
        full["hours_source_text"].encode() == HOURS
        and full["holiday_source_text"].encode() == holiday_html()
    )
    assert full["candidate"]["holiday_unresolved_nights"][0]["raw_text"]
    assert full["page_notes_status"] == "INCLUDED" and full["page_notes_count"] == 1
    assert full["candidate"]["page_notes"] == ["注：合成页面备注"]
    for item in full["candidate"]["hours_unresolved_nights"]:
        assert item["venue"] == "SHFE"
        assert item["availability"] == item["trading_day_mapping"] == "UNKNOWN"
        assert [
            (s["session"], s["phase"], s["start"], s["end"], s["day_transition"])
            for s in item["stages"]
        ] == [
            ("NIGHT", "AUCTION_ORDER", "20:55", "20:59", "SAME_CLOCK_DAY"),
            ("NIGHT", "AUCTION_MATCH", "20:59", "21:00", "SAME_CLOCK_DAY"),
            ("NIGHT", "CONTINUOUS", "21:00", "23:00", "SAME_CLOCK_DAY"),
        ]
        assert all(s["raw_line"] for s in item["stages"])
    assert not (root / "CURRENT.json").exists()
    assert build(root)["status"] == "NOT_COMMITTED"


@pytest.mark.parametrize("target", ["hours", "holiday", "candidate"])
def test_removed_or_tampered_sources_fail_closed(root, target):
    assert build(root)["status"] == "COMMITTED"
    path = {
        "hours": root / "source-evidence" / f"result-{TE}.json",
        "holiday": root / "source-evidence" / f"result-{HE}.json",
        "candidate": root / "holiday-evidence-candidates" / f"candidate-{HC}.json",
    }[target]
    path.unlink()
    assert show(root)["status"] == "UNKNOWN"


def test_invalid_scope_and_conflicting_identity_rejected(root):
    bad = selections()
    bad[1]["venue"] = "INE"
    assert build(root, bad)["status"] == "NOT_COMMITTED"
    bad = selections()
    bad[1]["contracts"] = bad[0]["contracts"]
    assert build(root, bad)["status"] == "NOT_COMMITTED"
    assert build(root, [{"venue": "SHFE"}])["status"] == "NOT_COMMITTED"


def test_source_changes_before_write(root, monkeypatch):
    original = bridge._inputs
    count = 0

    def changed(*args):
        nonlocal count
        count += 1
        result = original(*args)
        if count == 2:
            result = copy.deepcopy(result)
            result[0]["payload_sha256"] = "0" * 64
        return result

    monkeypatch.setattr(bridge, "_inputs", changed)
    assert build(root)["reason"] == "SOURCE_CHANGED"
    assert show(root)["status"] == "UNKNOWN"


def test_id_concurrency_and_unknown_result(root, monkeypatch):
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: build(root), range(2)))
    assert sum(item["status"] == "COMMITTED" for item in outcomes) == 1
    assert show(root)["status"] == "VISIBLE_UNAUTHENTICATED"
    monkeypatch.setattr(
        bridge.storage, "_write", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    assert build(root, candidate_id="e" * 32)["status"] == "COMMIT_UNKNOWN"


def test_result_unknown_after_successful_write_can_be_inspected(root, monkeypatch):
    original = bridge.storage._write

    def uncertain(*args):
        assert original(*args) == "COMMITTED"
        raise KeyboardInterrupt

    monkeypatch.setattr(bridge.storage, "_write", uncertain)
    assert build(root)["status"] == "COMMIT_UNKNOWN"
    assert show(root)["status"] == "VISIBLE_UNAUTHENTICATED"


@pytest.fixture
def ine_root(tmp_path):
    path = (
        Path(__file__).resolve().parents[2]
        / "bt_api/bt_api_ctp/tests/test_calendar_ine_holiday_fetch.py"
    )
    holiday_raw = runpy.run_path(str(path))["_page"]()
    hours_raw = HOURS.replace(b"21:00-23:00", b"21:00-01:00")
    for kind, eid, raw in (("holiday", HE, holiday_raw), ("hours", TE, hours_raw)):
        assert (
            evidence.capture(
                root=str(tmp_path),
                evidence_id=eid,
                kind=kind,
                exchange="INE",
                filesystem=FS,
                emit=lambda _: None,
                runner=lambda *_, raw=raw: ("HTTP_OK", raw),
            )["status"]
            == "COMMITTED"
        )
    assert (
        holiday.build_once(
            root=str(tmp_path), evidence_id=HE, candidate_id=HC, filesystem=FS, emit=lambda _: None
        )["status"]
        == "COMMITTED"
    )
    return tmp_path


def ine_selections():
    rows = mapping()
    for row in rows:
        row["venue"] = "INE"
    return rows


def test_ine_evidence_to_candidate_and_both_kinds(ine_root):
    assert build(ine_root, ine_selections())["status"] == "COMMITTED"
    result = show(ine_root)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    body = result["candidate"]
    assert body["schema"].endswith("/2") and body["exchange"] == "INE"
    assert len(body["content"]["trading_days"]) == 39
    assert {row["kind"] for row in body["content"]["trading_hours"]} == {"future", "option"}
    assert {row["venue"] for row in body["content"]["trading_hours"]} == {"INE"}
    assert len(body["holiday_unresolved_nights"]) == 7
    assert body["holiday_unresolved_nights"][0]["local_date"] == "2025-12-31"
    assert len(body["hours_unresolved_nights"]) == 2
    assert all("raw_text" not in row for row in body["holiday_unresolved_nights"])
    assert result["page_notes_count"] == 1 and result["page_notes_status"] == "REDACTED"
    assert body["page_notes"] == []
    assert body["publication"] == "NOT_AUTHORIZED"
    assert not (ine_root / "CURRENT.json").exists()
    assert all(
        item["stages"][-1]["day_transition"] == "INFERRED_NEXT_DAY"
        and item["trading_day_mapping"] == "UNKNOWN"
        for item in body["hours_unresolved_nights"]
    )
    for item in body["hours_unresolved_nights"]:
        assert item["venue"] == "INE" and item["availability"] == "UNKNOWN"
        assert [
            (
                stage["session"],
                stage["phase"],
                stage["start"],
                stage["end"],
                stage["day_transition"],
            )
            for stage in item["stages"]
        ] == [
            ("NIGHT", "AUCTION_ORDER", "20:55", "20:59", "SAME_CLOCK_DAY"),
            ("NIGHT", "AUCTION_MATCH", "20:59", "21:00", "SAME_CLOCK_DAY"),
            ("NIGHT", "CONTINUOUS", "21:00", "01:00", "INFERRED_NEXT_DAY"),
        ]
        assert all("raw_line" not in stage for stage in item["stages"])
    assert show(ine_root, include_source_text=True)["hours_source_text"].encode() == HOURS.replace(
        b"21:00-23:00", b"21:00-01:00"
    )


@pytest.mark.parametrize("source_index", [0, 2])
def test_ine_source_change_before_write_is_rejected(ine_root, monkeypatch, source_index):
    original = bridge._inputs
    count = 0

    def changed(*args):
        nonlocal count
        count += 1
        value = original(*args)
        if count == 2:
            value = copy.deepcopy(value)
            value[source_index]["payload_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(bridge, "_inputs", changed)
    result = build(ine_root, ine_selections())
    assert result["status"] == "NOT_COMMITTED" and result["reason"] == "SOURCE_CHANGED"
    assert show(ine_root)["status"] == "UNKNOWN"


@pytest.mark.parametrize("target", ["hours", "holiday", "candidate"])
def test_ine_source_removal_fails_closed(ine_root, target):
    assert build(ine_root, ine_selections())["status"] == "COMMITTED"
    path = {
        "hours": ine_root / "source-evidence" / f"result-{TE}.json",
        "holiday": ine_root / "source-evidence" / f"result-{HE}.json",
        "candidate": ine_root / "holiday-evidence-candidates" / f"candidate-{HC}.json",
    }[target]
    path.unlink()
    assert show(ine_root)["status"] == "UNKNOWN"


def test_ine_scope_and_source_identity_cannot_be_mixed(ine_root, monkeypatch):
    assert build(ine_root, selections())["status"] == "NOT_COMMITTED"
    assert (
        bridge.build_once(
            root=str(ine_root),
            evidence_id=TE,
            holiday_candidate_id=HC,
            candidate_id=CID,
            selections=ine_selections(),
            filesystem=FS,
            emit=lambda _: None,
        )["status"]
        == "COMMITTED"
    )
    original = bridge._inputs

    def bad_hash(*args):
        value = copy.deepcopy(original(*args))
        value[2]["source_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(bridge, "_inputs", bad_hash)
    assert show(ine_root)["status"] == "UNKNOWN"


def test_ine_access_challenge_not_accepted(tmp_path):
    raw = "WEB 应用防火墙 人机识别".encode()
    assert (
        evidence.capture(
            root=str(tmp_path),
            evidence_id=TE,
            kind="hours",
            exchange="INE",
            filesystem=FS,
            emit=lambda _: None,
            runner=lambda *_: ("HTTP_OK", raw),
        )["status"]
        == "COMMITTED"
    )
    assert build(tmp_path, ine_selections())["status"] == "NOT_COMMITTED"


def test_ine_challenge_rejected_with_existing_holiday(ine_root, monkeypatch):
    original = bridge._inputs

    def invalid(*args):
        value = copy.deepcopy(original(*args))
        value[0]["page_status"] = "ACCESS_CHALLENGE"
        return value

    monkeypatch.setattr(bridge, "_inputs", invalid)
    assert build(ine_root, ine_selections())["status"] == "NOT_COMMITTED"


def test_mixed_exchange_evidence_cannot_be_combined(ine_root):
    other = "e" * 32
    assert (
        evidence.capture(
            root=str(ine_root),
            evidence_id=other,
            kind="hours",
            filesystem=FS,
            emit=lambda _: None,
            runner=fake_runner(HOURS, []),
        )["status"]
        == "COMMITTED"
    )
    assert (
        bridge.build_once(
            root=str(ine_root),
            evidence_id=other,
            holiday_candidate_id=HC,
            candidate_id=CID,
            selections=selections(),
            filesystem=FS,
            emit=lambda _: None,
        )["status"]
        == "NOT_COMMITTED"
    )


def test_ine_concurrent_duplicate_and_uncertain_write(ine_root, monkeypatch):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: build(ine_root, ine_selections()), range(2)))
    assert sum(item["status"] == "COMMITTED" for item in results) == 1
    assert show(ine_root)["status"] == "VISIBLE_UNAUTHENTICATED"
    original = bridge.storage._write

    def uncertain(*args):
        assert original(*args) == "COMMITTED"
        raise KeyboardInterrupt

    monkeypatch.setattr(bridge.storage, "_write", uncertain)
    other = "f" * 32
    assert build(ine_root, ine_selections(), candidate_id=other)["status"] == "COMMIT_UNKNOWN"
    assert (
        bridge.show(root=str(ine_root), candidate_id=other, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_cli_show_summary(root):
    assert build(root)["status"] == "COMMITTED"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            bridge.__name__,
            "show",
            "--calendar-root",
            str(root),
            "--candidate-id",
            CID,
            "--trusted-local-posix",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 10
    assert json.loads(proc.stdout)["holiday_unresolved_count"] == 7
    assert (
        str(root) not in proc.stdout
        and "raw_text" not in proc.stdout
        and "raw_line" not in proc.stdout
    )
