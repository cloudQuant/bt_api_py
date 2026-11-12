"""Offline review-bundle references retain and revalidate public evidence."""

import json
import os
import runpy
import subprocess
import sys
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_holiday_evidence_candidate as holiday
from bt_api_py import trading_calendar_hours_evidence_candidate as hours
from bt_api_py import trading_calendar_review_bundle as bundle
from bt_api_py import trading_calendar_source_evidence as evidence
from tests.unit.test_trading_calendar_candidate_inbox import HOURS, holiday_html
from tests.unit.test_trading_calendar_hours_candidate import mapping
from tests.unit.test_trading_calendar_review_bundle import sample
from tests.unit.test_trading_calendar_source_evidence import fake_runner

FS = "TRUSTED_LOCAL_POSIX"
BUNDLE = "f" * 32


def make_candidate(root, venue, digit):
    he, te, hc, cid = (str(digit + n) * 32 for n in range(4))
    if venue == "INE":
        fixture = (
            Path(__file__).resolve().parents[2]
            / "bt_api/bt_api_ctp/tests/test_calendar_ine_holiday_fetch.py"
        )
        holiday_raw = runpy.run_path(str(fixture))["_page"]()
        hours_raw = HOURS.replace(b"21:00-23:00", b"21:00-01:00")
    else:
        holiday_raw, hours_raw = holiday_html(), HOURS
    for kind, eid, raw in (("holiday", he, holiday_raw), ("hours", te, hours_raw)):
        runner = fake_runner(raw, []) if venue == "SHFE" else lambda *_, raw=raw: ("HTTP_OK", raw)
        result = evidence.capture(
            root=str(root),
            evidence_id=eid,
            kind=kind,
            exchange=venue,
            filesystem=FS,
            emit=lambda _: None,
            runner=runner,
        )
        assert result["status"] == "COMMITTED"
    assert (
        holiday.build_once(
            root=str(root), evidence_id=he, candidate_id=hc, filesystem=FS, emit=lambda _: None
        )["status"]
        == "COMMITTED"
    )
    selections = mapping()
    for row in selections:
        row["venue"] = venue
    assert (
        hours.build_once(
            root=str(root),
            evidence_id=te,
            holiday_candidate_id=hc,
            candidate_id=cid,
            selections=selections,
            filesystem=FS,
            emit=lambda _: None,
        )["status"]
        == "COMMITTED"
    )
    return cid, he, te, hc


def build(root, data, ids, candidate_id=BUNDLE):
    return bundle.build_once(
        root=str(root),
        candidate_id=candidate_id,
        input_data=data,
        filesystem=FS,
        emit=lambda _: None,
        hours_candidate_ids=ids,
    )


def show(root, candidate_id=BUNDLE):
    return bundle.show(root=str(root), candidate_id=candidate_id, filesystem=FS)


@pytest.mark.parametrize("venues", [("SHFE",), ("INE",), ("SHFE", "INE")])
def test_select_candidates_retains_full_unresolved_and_existing_input(tmp_path, venues):
    ids = [make_candidate(tmp_path, venue, 1 + 4 * i)[0] for i, venue in enumerate(venues)]
    data = sample()
    original = deepcopy(data)
    assert build(tmp_path, data, ids)["status"] == "COMMITTED"
    result = show(tmp_path)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    body = result["bundle"]
    assert body["schema"].endswith("/3") and body["assembly"]["caller_input"] == original == data
    assert body["report"]["status"] == "PARTIAL"
    assert body["report"]["publication"] == "NOT_AUTHORIZED"
    for venue, cid in zip(venues, ids, strict=True):
        source = hours.show(
            root=str(tmp_path), candidate_id=cid, filesystem=FS, include_source_text=True
        )["candidate"]
        shard = next(s for s in body["input"]["shards"] if s["venue"] == venue)
        unresolved = shard["unresolved"][-1]
        assert unresolved["candidate_id"] == cid
        for key in (
            "remaining_checks",
            "holiday_unresolved_nights",
            "hours_unresolved_nights",
            "page_notes",
            "page_review_reasons",
        ):
            assert unresolved[key] == source[key]
        assert source["remaining_checks"] and source["holiday_unresolved_nights"]
        assert source["hours_unresolved_nights"] and source["page_notes"]
        for layer, rows in source["content"].items():
            if layer == "trading_days":
                for row in rows:
                    assert any(
                        all(current[key] == row[key] for key in ("venue", "day", "status"))
                        and set(row["source_ids"]) <= set(current["source_ids"])
                        for current in shard["layers"][layer]
                    )
            else:
                assert all(row in shard["layers"][layer] for row in rows)
    assert not (tmp_path / "CURRENT.json").exists()


@pytest.mark.parametrize("target", ["hours", "holiday", "holiday_candidate", "candidate"])
def test_removed_sources_invalidate_bundle(tmp_path, target):
    cid, he, te, hc = make_candidate(tmp_path, "SHFE", 1)
    assert build(tmp_path, sample(), [cid])["status"] == "COMMITTED"
    paths = {
        "hours": tmp_path / "source-evidence" / f"result-{te}.json",
        "holiday": tmp_path / "source-evidence" / f"result-{he}.json",
        "holiday_candidate": tmp_path / "holiday-evidence-candidates" / f"candidate-{hc}.json",
        "candidate": tmp_path / "hours-evidence-candidates" / f"candidate-{cid}.json",
    }
    paths[target].unlink()
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_conflicts_missing_and_duplicate_selection_fail_closed(tmp_path):
    cid, *_ = make_candidate(tmp_path, "SHFE", 1)
    data = sample()
    data["shards"][0].pop("unresolved")
    candidate = hours.show(
        root=str(tmp_path), candidate_id=cid, filesystem=FS, include_source_text=True
    )["candidate"]
    source_id = candidate["content"]["sources"][0]["id"]
    data["source_texts"][source_id] = "conflicting source"
    assert build(tmp_path, data, [cid])["status"] == "COMMITTED"
    result = show(tmp_path)["bundle"]
    assert result["report"]["status"] == "PARTIAL"
    assert result["input"]["source_texts"][source_id] == "conflicting source"
    assert result["input"]["shards"][0]["unresolved"][0] == {
        "caller_unresolved": "MISSING_OR_INVALID"
    }
    assert build(tmp_path, sample(), [cid, cid], "e" * 32)["status"] == "NOT_COMMITTED"
    assert build(tmp_path, sample(), ["a" * 32], "d" * 32)["status"] == "NOT_COMMITTED"
    assert build(tmp_path, sample(), [cid])["status"] == "NOT_COMMITTED"


def test_cli_restarts_and_rechecks_sources(tmp_path):
    cid, _, te, _ = make_candidate(tmp_path, "INE", 1)
    command = [sys.executable, "-m", "bt_api_py.trading_calendar_review_bundle"]
    flags = ["--calendar-root", str(tmp_path), "--candidate-id", BUNDLE, "--trusted-local-posix"]
    env = dict(os.environ)
    built = subprocess.run(
        command + ["build"] + flags + ["--hours-candidate-id", cid],
        input=json.dumps(sample()),
        text=True,
        capture_output=True,
        env=env,
    )
    assert built.returncode == 10, built.stdout + built.stderr
    shown = subprocess.run(command + ["show"] + flags, text=True, capture_output=True, env=env)
    assert shown.returncode == 10 and json.loads(shown.stdout)["review_status"] == "PARTIAL"
    (tmp_path / "source-evidence" / f"result-{te}.json").unlink()
    shown = subprocess.run(command + ["show"] + flags, text=True, capture_output=True, env=env)
    assert shown.returncode == 25 and json.loads(shown.stdout)["status"] == "UNKNOWN"
    assert not (tmp_path / "CURRENT.json").exists()


def test_tampered_candidate_and_original_evidence_rejected(tmp_path):
    cid, _, te, _ = make_candidate(tmp_path, "SHFE", 1)
    assert build(tmp_path, sample(), [cid])["status"] == "COMMITTED"
    evidence_file = tmp_path / "source-evidence" / f"result-{te}.json"
    original = evidence_file.read_bytes()
    document = json.loads(original)
    document["body"]["source_sha256"] = "0" * 64
    evidence_file.write_bytes(bundle.storage._envelope(document["body"]))
    assert show(tmp_path)["status"] == "UNKNOWN"
    evidence_file.write_bytes(original)
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    candidate_file = tmp_path / "hours-evidence-candidates" / f"candidate-{cid}.json"
    document = json.loads(candidate_file.read_bytes())
    document["body"]["remaining_checks"] = []
    candidate_file.write_bytes(bundle.storage._envelope(document["body"]))
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_source_changed_before_write_is_not_committed(tmp_path, monkeypatch):
    cid, _, te, _ = make_candidate(tmp_path, "SHFE", 1)
    original = bundle._evaluate_references
    count = 0

    def changed(*args):
        nonlocal count
        count += 1
        if count == 2:
            (tmp_path / "source-evidence" / f"result-{te}.json").unlink()
        return original(*args)

    monkeypatch.setattr(bundle, "_evaluate_references", changed)
    assert build(tmp_path, sample(), [cid])["reason"] == "SOURCE_CHANGED"
    assert not (tmp_path / "review-bundles" / f"candidate-{BUNDLE}.json").exists()


def test_no_network_or_current_access(tmp_path, monkeypatch):
    import socket

    cid, *_ = make_candidate(tmp_path, "SHFE", 1)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network used"))
    current = tmp_path / "CURRENT.json"
    current.symlink_to(tmp_path / "never-read")
    assert build(tmp_path, sample(), [cid])["status"] == "COMMITTED"
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    assert current.is_symlink() and not (tmp_path / "never-read").exists()


@pytest.mark.parametrize("years,overlap", [(1, True), (2, True), (1, False), (2, False)])
def test_day_source_union_and_non_overlapping_coverage(tmp_path, years, overlap):
    cid, *_ = make_candidate(tmp_path, "SHFE", 1)
    source = hours.show(
        root=str(tmp_path), candidate_id=cid, filesystem=FS, include_source_text=True
    )["candidate"]
    source_days = source["content"]["trading_days"]
    selected_day = date.fromisoformat(source_days[0]["day"])
    start = date(selected_day.year if overlap else selected_day.year + 3, 1, 1)
    end = date(start.year + years, 1, 1) - timedelta(days=1)
    data = sample()
    shard = data["shards"][0]
    shard["coverage_start"], shard["coverage_end"] = start.isoformat(), end.isoformat()
    statuses = {row["day"]: row["status"] for row in source_days}
    shard["layers"]["trading_days"] = [
        {
            "venue": "SHFE",
            "day": (start + timedelta(days=i)).isoformat(),
            "status": statuses.get((start + timedelta(days=i)).isoformat(), "closed"),
            "source_ids": ["synthetic"],
        }
        for i in range((end - start).days + 1)
    ]
    original = deepcopy(data)
    assert build(tmp_path, data, [cid])["status"] == "COMMITTED"
    result = show(tmp_path)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    body = result["bundle"]
    assert body["assembly"]["caller_input"] == original == data
    merged = body["input"]["shards"][0]
    for day in source_days:
        matching = [row for row in merged["layers"]["trading_days"] if row["day"] == day["day"]]
        assert len(matching) == 1
        refs = set(day["source_ids"])
        if start <= date.fromisoformat(day["day"]) <= end:
            refs.add("synthetic")
        assert set(matching[0]["source_ids"]) == refs
    assert body["report"]["shard_inputs"][0]["duplicate_date_count"] == 0
    assert body["report"]["checker_status"] != "NOT_RUN"
    assert body["report"]["status"] == "PARTIAL"
    assert merged["unresolved"][-1]["remaining_checks"] == source["remaining_checks"]
    assert show(tmp_path) == result


def test_opposite_day_status_remains_conflicting(tmp_path):
    cid, *_ = make_candidate(tmp_path, "SHFE", 1)
    source = hours.show(
        root=str(tmp_path), candidate_id=cid, filesystem=FS, include_source_text=True
    )["candidate"]
    source_day = source["content"]["trading_days"][0]
    data = sample()
    data["shards"][0]["layers"]["trading_days"] = [
        {
            "venue": "SHFE",
            "day": source_day["day"],
            "status": "open" if source_day["status"] == "closed" else "closed",
            "source_ids": ["synthetic"],
        }
    ]
    assert build(tmp_path, data, [cid])["status"] == "COMMITTED"
    body = show(tmp_path)["bundle"]
    matches = [
        row
        for row in body["input"]["shards"][0]["layers"]["trading_days"]
        if row["day"] == source_day["day"]
    ]
    assert len(matches) == 2 and {row["status"] for row in matches} == {"open", "closed"}
    assert {tuple(row["source_ids"]) for row in matches} == {
        ("synthetic",),
        tuple(source_day["source_ids"]),
    }
    assert body["report"]["status"] == "PARTIAL"
    assert body["report"]["checker_status"] == "NOT_RUN"


def test_extra_fields_and_duplicate_existing_dates_are_not_merged():
    row = {"venue": "SHFE", "day": "2026-01-01", "status": "closed", "source_ids": ["new"]}
    extra = {**row, "source_ids": ["old"], "provenance": "caller"}
    rows = [extra]
    assert not bundle._merge_day_sources(rows, row)
    assert rows == [extra]
    duplicate = {**row, "source_ids": ["old"]}
    rows = [deepcopy(duplicate), deepcopy(duplicate)]
    assert not bundle._merge_day_sources(rows, row)
    assert rows == [duplicate, duplicate]
