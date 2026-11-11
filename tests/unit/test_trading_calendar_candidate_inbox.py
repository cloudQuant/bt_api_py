"""Offline synthetic end-to-end candidate retention; no venue truth or network."""

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
from datetime import UTC, date, datetime

import pytest
from bt_api_ctp.calendar_candidate_merge import merge_calendar_candidates
from bt_api_ctp.calendar_shfe_candidates import convert_shfe_holiday
from bt_api_ctp.calendar_shfe_fetch import ShfeFetchResult, ShfeHolidayFetchResult
from bt_api_ctp.calendar_shfe_holiday import parse_shfe_holiday
from bt_api_ctp.calendar_shfe_page import parse_shfe_page
from bt_api_ctp.calendar_supplement import parse_supplement

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_preview as preview

HOLIDAY_URL = "https://www.shfe.com.cn/services/calenderandholidays/holiday/"
HOURS_URL = "https://www.shfe.com.cn/services/calenderandholidays/tradinghours/"
HOURS = (
    "<table><tr><td>期货：合成甲<br>期权：合成甲期权</td><td>"
    "日盘<br>集合竞价申报 08:55-08:59<br>集合竞价撮合 08:59-09:00<br>"
    "连续竞价交易 09:00-10:15<br>连续竞价交易 10:30-11:30<br>"
    "连续竞价交易 13:30-15:00<br>夜盘<br>集合竞价申报 20:55-20:59<br>"
    "集合竞价撮合 20:59-21:00<br>连续竞价交易 21:00-23:00"
    "</td></tr><tr><td colspan=2>注：合成页面备注</td></tr></table>"
).encode()
ID = "a" * 32


def holiday_html():
    weekdays = "一二三四五六日"
    names = ("元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节")
    months = (1, 2, 4, 5, 6, 9, 10)

    def display(day, year=False):
        return f"{str(day.year) + '年' if year else ''}{day.month}月{day.day}日（星期{weekdays[day.weekday()]}）"

    rows = []
    for index, (name, month) in enumerate(zip(names, months)):  # noqa: B905 - Python 3.9
        start = date(2026, month, 1 if index == 0 else 15)
        end = date(2026, month, 3 if index == 0 else 16)
        opened = date(2026, month, 5 if index == 0 else 17)
        night = date(2025, 12, 31) if index == 0 else date(2026, month, 14)
        rows.append(
            f"<div>{'一二三四五六七'[index]}、{name}：{display(start)}至{display(end)}休市，"
            f"{display(opened)}起照常开市。{display(night, index == 0)}晚上不进行夜盘交易。</div>"
        )
    return (
        '<div class="article_title" id="no_article_empty">2026年休市安排</div>'
        '<div class="article_content_item"><div class="TRS_Editor">'
        + "".join(rows)
        + "</div></div>"
    ).encode()


def input_content():
    return {
        "sources": [{"id": "manual", "description": "synthetic source"}],
        "trading_days": [
            {"venue": "SHFE", "day": "2026-01-02", "status": "closed", "source_ids": ["manual"]}
        ],
        "trading_hours": [],
        "exceptions": [],
    }


def objects():
    hours = parse_shfe_page(source_id=HOURS_URL, html=HOURS)
    holiday = parse_shfe_holiday(source_id=HOLIDAY_URL, html=holiday_html())
    converted = convert_shfe_holiday(holiday)
    merged = merge_calendar_candidates(
        supplement=parse_supplement(json.dumps(input_content()).encode()),
        holiday=converted,
        supplement_coverage="NOT_PROVEN",
        supplement_publication="NOT_AUTHORIZED",
    )
    assert merged.status == "PARTIAL"
    assert hours.status in {"CANDIDATE", "NEEDS_REVIEW"}
    return hours, holiday, merged


def source_checks():
    def attempt(status, count):
        return {
            "number": 1,
            "transport_status": "HTTP_OK",
            "source_status": status,
            "verification": "UNVERIFIED",
            "product_count": count,
            "parse_status": status,
        }

    return {
        "hours": {
            "status": "NEEDS_REVIEW",
            "reason": "SHFE_NOT_VERIFIED",
            "attempts": [attempt("NEEDS_REVIEW", 2)],
        },
        "holiday": {
            "status": "CANDIDATE",
            "reason": "CANDIDATE",
            "attempts": [attempt("CANDIDATE", 0)],
        },
        "holiday_conversion_status": "CANDIDATE",
        "merge_status": "PARTIAL",
        "merge_reason": "NONE",
        "normalization_status": "NORMALIZED",
    }


@pytest.fixture
def root(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    return root


def retain(root, *, candidate_id=ID, hours=None, merged=None):
    default_hours, _, default_merged = objects()
    return inbox.retain_candidate(
        root=root,
        candidate_id=candidate_id,
        merged=default_merged if merged is None else merged,
        hours_page=default_hours if hours is None else hours,
        source_checks=source_checks(),
        filesystem=inbox.TRUSTED_LOCAL_POSIX,
    )


def test_retains_review_material_without_touching_current_or_overwriting(root):
    current = root / "CURRENT.json"
    current.write_bytes(b"opaque-current")
    result = retain(root)
    assert (result.status, result.reason, result.candidate_id) == ("COMMITTED", "NONE", ID)
    path = root / "inbox" / f"candidate-{ID}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    candidate = inbox.read_candidate(
        root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )
    payload = candidate["payload"]
    assert payload["completeness"] == "INCOMPLETE"
    assert payload["publication"] == "NOT_AUTHORIZED"
    assert payload["baseline"] == "NOT_OBSERVED" and payload["difference"] == "NOT_ASSESSED"
    assert len(payload["unresolved_nights"]) == 7
    assert payload["unresolved_nights"][0]["local_date"] == "2025-12-31"
    assert payload["unresolved_nights"][0]["raw_text"]
    assert payload["unresolved_nights"][0]["trading_day"] is None
    assert payload["hours_page_status"] == "CAPTURED_UNVERIFIED"
    assert payload["hours_page"]["products"][0]["name"] == "合成甲"
    assert payload["hours_page"]["raw_notes"] == ["注：合成页面备注"]
    assert payload["identity_mapping"] == payload["night_mapping"] == "UNKNOWN"
    assert payload["clock_status"] == "CLOCK_UNVERIFIED" and payload["observed_at_utc"].endswith(
        "Z"
    )
    assert payload["source_checks"]["holiday"]["attempts"][0]["transport_status"] == "HTTP_OK"
    assert len(payload["content"]["trading_days"]) > 1
    assert retain(root).reason == "ID_EXISTS" and path.read_bytes() == original
    assert current.read_bytes() == b"opaque-current"


def test_missing_page_is_unknown_and_tamper_is_rejected(root):
    _, _, merged = objects()
    result = inbox.retain_candidate(
        root=root,
        candidate_id=ID,
        merged=merged,
        hours_page=None,
        source_checks=source_checks(),
        filesystem=inbox.TRUSTED_LOCAL_POSIX,
    )
    assert result.status == "COMMITTED"
    candidate = inbox.read_candidate(
        root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )
    assert candidate["payload"]["hours_page_status"] == "UNKNOWN"
    path = root / "inbox" / f"candidate-{ID}.json"
    document = json.loads(path.read_text())
    document["payload"]["unresolved_nights"][0]["raw_text"] += " changed"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)


@pytest.mark.parametrize(
    "mutation",
    [
        "not_requested",
        "timeout",
        "status",
        "parse",
        "count",
        "reason_hours",
        "reason_holiday",
        "retry_reason_on_success",
    ],
)
def test_rehashed_candidate_with_contradictory_page_or_attempt_is_rejected(root, mutation):
    assert retain(root).status == "COMMITTED"
    path = root / "inbox" / f"candidate-{ID}.json"
    document = json.loads(path.read_text())
    checks = document["payload"]["source_checks"]
    hours = checks["hours"]
    if mutation == "not_requested":
        hours.update(status="NOT_REQUESTED", reason="NOT_REQUESTED", attempts=[])
    elif mutation == "timeout":
        hours.update(status="NETWORK_TIMEOUT", reason="RETRY_LIMIT_REACHED")
        hours["attempts"][-1].update(
            transport_status="NETWORK_TIMEOUT",
            source_status="NETWORK_TIMEOUT",
            parse_status="NOT_ATTEMPTED",
            product_count=0,
        )
    elif mutation == "status":
        hours["status"] = "CANDIDATE"
        hours["attempts"][-1].update(source_status="CANDIDATE", parse_status="CANDIDATE")
    elif mutation == "parse":
        hours["attempts"][-1]["parse_status"] = "CANDIDATE"
    elif mutation == "count":
        hours["attempts"][-1]["product_count"] += 1
    elif mutation == "reason_hours":
        hours["reason"] = "NOT_REQUESTED"
    elif mutation == "reason_holiday":
        checks["holiday"]["reason"] = "NOT_ATTEMPTED"
    else:
        hours["reason"] = "RETRY_LIMIT_REACHED"
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(document["payload"])).hexdigest()
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)


@pytest.mark.parametrize(
    ("transport", "parse_status"),
    [
        ("PARSER_ERROR", "PARSER_ERROR"),
        ("CHILD_ERROR", "UNKNOWN"),
        ("CHILD_TIMEOUT", "UNKNOWN"),
        ("CHILD_CLEANUP_UNCERTAIN", "UNKNOWN"),
        ("RESULT_TOO_LARGE", "UNKNOWN"),
        ("CTP_UNAVAILABLE", "UNKNOWN"),
        ("COLLECTOR_ERROR", "UNKNOWN"),
        ("COLLECTOR_PROTOCOL_ERROR", "UNKNOWN"),
        ("NETWORK_TIMEOUT", "NOT_ATTEMPTED"),
        ("HTTP_ERROR", "NOT_ATTEMPTED"),
        ("TLS_ERROR", "NOT_ATTEMPTED"),
    ],
)
def test_failed_transport_parse_status_must_match_after_rehash(root, transport, parse_status):
    assert retain(root).status == "COMMITTED"
    path = root / "inbox" / f"candidate-{ID}.json"
    document = json.loads(path.read_text())
    payload = document["payload"]
    payload["hours_page"] = None
    payload["hours_page_status"] = "UNKNOWN"
    hours = payload["source_checks"]["hours"]
    reason = (
        "RETRY_LIMIT_REACHED"
        if transport in {"NETWORK_TIMEOUT", "CHILD_TIMEOUT"}
        else "CHILD_CLEANUP_UNCERTAIN"
        if transport == "CHILD_CLEANUP_UNCERTAIN"
        else "SHFE_NOT_VERIFIED"
    )
    hours.update(status=transport, reason=reason)
    hours["attempts"][-1].update(
        transport_status=transport,
        source_status=transport,
        parse_status=parse_status,
        product_count=0,
    )
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(payload)).hexdigest()
    path.write_text(json.dumps(document))
    assert inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)
    hours["attempts"][-1]["parse_status"] = "CANDIDATE"
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(payload)).hexdigest()
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)


@pytest.mark.parametrize(
    "mutation",
    [
        "source_unavailable",
        "conversion_unattempted",
        "conversion_failed",
        "merge_unattempted",
        "normalization_unattempted",
    ],
)
def test_rehashed_contradictory_conversion_merge_or_normalization_rejected(root, mutation):
    assert retain(root).status == "COMMITTED"
    path = root / "inbox" / f"candidate-{ID}.json"
    document = json.loads(path.read_text())
    payload = document["payload"]
    checks = payload["source_checks"]
    if mutation == "source_unavailable":
        holiday = checks["holiday"]
        holiday.update(status="NETWORK_TIMEOUT", reason="RETRY_LIMIT_REACHED")
        holiday["attempts"][-1].update(
            transport_status="NETWORK_TIMEOUT",
            source_status="NETWORK_TIMEOUT",
            parse_status="NOT_ATTEMPTED",
            product_count=0,
        )
    elif mutation == "conversion_unattempted":
        checks["holiday_conversion_status"] = "NOT_ATTEMPTED"
        checks["holiday"]["reason"] = "SHFE_NOT_VERIFIED"
    elif mutation == "conversion_failed":
        checks["holiday_conversion_status"] = "CONVERSION_ERROR"
        checks["holiday"]["reason"] = "SHFE_NOT_VERIFIED"
    elif mutation == "merge_unattempted":
        checks["merge_status"] = "NOT_ATTEMPTED"
        checks["normalization_status"] = "NOT_ATTEMPTED"
    else:
        checks["normalization_status"] = "NOT_ATTEMPTED"
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(payload)).hexdigest()
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)


def test_yaml_only_cannot_claim_failed_conversion_without_a_source_attempt(root):
    supplement = parse_supplement(json.dumps(input_content()).encode())
    absent = {"status": "NOT_REQUESTED", "reason": "NOT_REQUESTED", "attempts": []}
    checks = {
        "hours": absent,
        "holiday": absent,
        "holiday_conversion_status": "NOT_ATTEMPTED",
        "merge_status": "NOT_ATTEMPTED",
        "merge_reason": "NONE",
        "normalization_status": "NOT_ATTEMPTED",
    }
    assert (
        inbox.retain_candidate(
            root=root,
            candidate_id=ID,
            merged=None,
            supplement=supplement,
            hours_page=None,
            source_checks=checks,
            filesystem=inbox.TRUSTED_LOCAL_POSIX,
        ).status
        == "COMMITTED"
    )
    path = root / "inbox" / f"candidate-{ID}.json"
    document = json.loads(path.read_text())
    document["payload"]["source_checks"]["holiday_conversion_status"] = "CONVERSION_ERROR"
    document["payload_sha256"] = hashlib.sha256(inbox._canonical(document["payload"])).hexdigest()
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)


def test_symlink_directory_rejected(root, tmp_path):
    other = tmp_path.resolve() / "other"
    other.mkdir()
    (root / "inbox").symlink_to(other, target_is_directory=True)
    assert retain(root).reason == "INVALID_DIRECTORY"
    assert list(other.iterdir()) == []


def test_pre_and_post_install_failures_preserve_truth(root, monkeypatch):
    hours, _, merged = objects()
    original = inbox.os.link
    monkeypatch.setattr(inbox, "_supported", lambda: True)

    def before(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr(inbox.os, "link", before)
    assert (
        inbox.retain_candidate(
            root=root,
            candidate_id=ID,
            merged=merged,
            hours_page=hours,
            source_checks=source_checks(),
            filesystem=inbox.TRUSTED_LOCAL_POSIX,
        ).status
        == "COMMIT_UNKNOWN"
    )
    monkeypatch.setattr(inbox.os, "link", original)
    real_write = inbox.os.write
    monkeypatch.setattr(inbox.os, "write", lambda *a: (_ for _ in ()).throw(OSError("synthetic")))
    assert (
        inbox.retain_candidate(
            root=root,
            candidate_id=ID,
            merged=merged,
            hours_page=hours,
            source_checks=source_checks(),
            filesystem=inbox.TRUSTED_LOCAL_POSIX,
        ).status
        == "NOT_COMMITTED"
    )
    monkeypatch.setattr(inbox.os, "write", real_write)

    def after(*args, **kwargs):
        original(*args, **kwargs)
        raise FileExistsError("synthetic")

    monkeypatch.setattr(inbox.os, "link", after)
    assert (
        inbox.retain_candidate(
            root=root,
            candidate_id=ID,
            merged=merged,
            hours_page=hours,
            source_checks=source_checks(),
            filesystem=inbox.TRUSTED_LOCAL_POSIX,
        ).status
        == "COMMIT_UNKNOWN"
    )
    assert (
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)[
            "candidate_id"
        ]
        == ID
    )


def test_real_yaml_check_to_inbox_and_no_default_write(root):
    hours, holiday, _ = objects()
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    now = datetime(2026, 11, 12, tzinfo=UTC)
    kwargs = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 30,
        "enable_shfe": True,
        "max_retries": 0,
        "collector": lambda **kw: ShfeFetchResult(hours.status, "HTTP_OK", now, hours),
        "holiday_collector": lambda **kw: ShfeHolidayFetchResult(
            holiday.status, "HTTP_OK", now, holiday
        ),
    }
    first = preview.run_preview(**kwargs)
    assert first.candidate_status == "NOT_REQUESTED" and not (root / "inbox").exists()
    second = preview.run_preview(
        **kwargs,
        candidate_id=ID,
        candidate_writer=lambda **kw: inbox.retain_candidate(
            root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX, **kw
        ),
    )
    assert (second.candidate_status, second.candidate_id) == ("COMMITTED", ID)
    assert (
        inbox.read_candidate(root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX)[
            "payload"
        ]["hours_page_status"]
        == "CAPTURED_UNVERIFIED"
    )


@pytest.mark.parametrize(
    "source_mode",
    [
        "not_requested",
        "holiday_timeout",
        "holiday_parser_error",
        "holiday_structure",
        "converter_unavailable",
        "converter_error",
        "converter_invalid",
    ],
)
def test_yaml_only_is_retained_without_claiming_holiday_or_night(root, source_mode):
    hours, holiday, _ = objects()
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    now = datetime(2026, 11, 12, tzinfo=UTC)

    def holiday_source(**kwargs):
        if source_mode == "holiday_timeout":
            return ShfeHolidayFetchResult("NETWORK_TIMEOUT", "NETWORK_TIMEOUT", now)
        if source_mode == "holiday_parser_error":
            return ShfeHolidayFetchResult("PARSER_ERROR", "PARSER_ERROR", now)
        page = (
            parse_shfe_holiday(source_id=HOLIDAY_URL, html=b"<div>changed</div>")
            if source_mode == "holiday_structure"
            else holiday
        )
        return ShfeHolidayFetchResult(page.status, "HTTP_OK", now, page)

    def converter(page):
        if source_mode == "converter_unavailable":
            raise ImportError("synthetic")
        if source_mode == "converter_error":
            raise ValueError("synthetic")
        if source_mode == "converter_invalid":
            return None
        return convert_shfe_holiday(page)

    report = preview.run_preview(
        config_file=str(config),
        calendar_root=str(root),
        supplement_file=str(supplement),
        budget_seconds=30,
        enable_shfe=source_mode != "not_requested",
        max_retries=0,
        collector=lambda **kw: ShfeFetchResult(hours.status, "HTTP_OK", now, hours),
        holiday_collector=holiday_source,
        holiday_converter=converter,
        candidate_id=ID,
        candidate_writer=lambda **kw: inbox.retain_candidate(
            root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX, **kw
        ),
    )
    assert report.candidate_status == "COMMITTED"
    payload = inbox.read_candidate(
        root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )["payload"]
    assert payload["input_scope"] == "YAML_ONLY"
    assert payload["holiday_status"] == payload["unresolved_nights_status"] == "UNKNOWN"
    assert payload["unresolved_nights"] is None
    assert payload["hours_page_status"] == (
        "UNKNOWN" if source_mode == "not_requested" else "CAPTURED_UNVERIFIED"
    )
    assert payload["baseline"] == "NOT_OBSERVED" and payload["publication"] == "NOT_AUTHORIZED"
    assert payload["clock_status"] == "CLOCK_UNVERIFIED" and payload["observed_at_utc"].endswith(
        "Z"
    )
    checks = payload["source_checks"]
    if source_mode == "not_requested":
        assert checks["holiday"] == {
            "status": "NOT_REQUESTED",
            "reason": "NOT_REQUESTED",
            "attempts": [],
        }
    elif source_mode == "holiday_timeout":
        assert (
            checks["holiday"]["status"] == "NETWORK_TIMEOUT"
            and len(checks["holiday"]["attempts"]) == 1
        )
    elif source_mode == "holiday_parser_error":
        assert checks["holiday"]["status"] == "PARSER_ERROR"
        assert checks["holiday"]["attempts"][0]["parse_status"] == "PARSER_ERROR"
    elif source_mode == "holiday_structure":
        assert checks["holiday"]["status"] == "STRUCTURE_CHANGED"
    else:
        assert checks["holiday"]["status"] == "CANDIDATE"
        assert checks["holiday_conversion_status"] in {
            "CONVERTER_UNAVAILABLE",
            "CONVERSION_ERROR",
            "CONVERSION_PROTOCOL_ERROR",
        }


def test_source_conflict_does_not_fallback_to_yaml_only(root):
    hours, holiday, _ = objects()
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    content = input_content()
    content["trading_days"][0]["status"] = "open"
    supplement.write_text(json.dumps(content))
    now = datetime(2026, 11, 12, tzinfo=UTC)
    report = preview.run_preview(
        config_file=str(config),
        calendar_root=str(root),
        supplement_file=str(supplement),
        budget_seconds=30,
        enable_shfe=True,
        max_retries=0,
        collector=lambda **kw: ShfeFetchResult(hours.status, "HTTP_OK", now, hours),
        holiday_collector=lambda **kw: ShfeHolidayFetchResult(
            holiday.status, "HTTP_OK", now, holiday
        ),
        candidate_id=ID,
        candidate_writer=lambda **kw: inbox.retain_candidate(
            root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX, **kw
        ),
    )
    assert report.reason == "MERGE_CONFLICT" and report.candidate_status == "NOT_ATTEMPTED"
    assert not (root / "inbox").exists()


@pytest.mark.parametrize("failure", ["exception", "import", "protocol"])
def test_merger_failure_retains_yaml_with_error_and_unknown_holiday(root, failure):
    hours, holiday, _ = objects()
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    now = datetime(2026, 11, 12, tzinfo=UTC)

    def merge_failure(**kwargs):
        if failure == "exception":
            raise ValueError("synthetic")
        if failure == "import":
            raise ImportError("synthetic")
        return None

    report = preview.run_preview(
        config_file=str(config),
        calendar_root=str(root),
        supplement_file=str(supplement),
        budget_seconds=30,
        enable_shfe=True,
        max_retries=0,
        collector=lambda **kw: ShfeFetchResult(hours.status, "HTTP_OK", now, hours),
        holiday_collector=lambda **kw: ShfeHolidayFetchResult(
            holiday.status, "HTTP_OK", now, holiday
        ),
        merger=merge_failure,
        candidate_id=ID,
        candidate_writer=lambda **kw: inbox.retain_candidate(
            root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX, **kw
        ),
    )
    assert report.reason == "MERGE_FAILED" and report.candidate_status == "COMMITTED"
    payload = inbox.read_candidate(
        root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )["payload"]
    assert payload["holiday_status"] == "UNKNOWN" and payload["unresolved_nights"] is None
    assert payload["source_checks"]["merge_status"] in {
        "MERGE_ERROR",
        "MERGE_UNAVAILABLE",
        "MERGE_PROTOCOL_ERROR",
    }
    assert payload["publication"] == "NOT_AUTHORIZED" and payload["difference"] == "NOT_ASSESSED"


def test_invalid_or_conflicting_yaml_does_not_create_candidate(root):
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    common = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 30,
        "candidate_id": ID,
        "candidate_writer": lambda **kw: inbox.retain_candidate(
            root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX, **kw
        ),
    }
    supplement.write_text("sources: [invalid")
    report = preview.run_preview(**common)
    assert report.reason == "INPUT_CHECK_FAILED" and report.candidate_status == "NOT_ATTEMPTED"
    assert not (root / "inbox").exists()
    content = input_content()
    content["trading_days"].append({**content["trading_days"][0], "status": "open"})
    supplement.write_text(json.dumps(content))
    report = preview.run_preview(**common)
    assert report.candidate_status == "NOT_COMMITTED"
    assert report.candidate_reason == "INVALID_OR_TOO_LARGE"
    assert not (root / "inbox" / f"candidate-{ID}.json").exists()


def test_real_cli_yaml_only_and_config_mismatch(root, tmp_path):
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_preview",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "20",
        "--retain-candidate",
        "--trusted-local-posix",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    assert (report["reason"], report["candidate_status"]) == ("SHFE_NOT_REQUESTED", "COMMITTED")
    payload = inbox.read_candidate(
        root=root, candidate_id=report["candidate_id"], filesystem=inbox.TRUSTED_LOCAL_POSIX
    )["payload"]
    assert payload["unresolved_nights"] is None and payload["holiday_status"] == "UNKNOWN"
    conflicting = input_content()
    conflicting["trading_days"].append({**conflicting["trading_days"][0], "status": "open"})
    supplement.write_text(json.dumps(conflicting))
    failed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert failed.returncode == 24, failed.stderr
    assert json.loads(failed.stdout)["candidate_status"] == "NOT_COMMITTED"
    supplement.write_text(json.dumps(input_content()))
    mismatch = tmp_path.resolve() / "other"
    mismatch.mkdir()
    command[command.index(str(root))] = str(mismatch)
    failed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert failed.returncode == 20, failed.stderr
    failure = json.loads(failed.stdout)
    assert failure["candidate_status"] == "NOT_ATTEMPTED" and len(failure["candidate_id"]) == 32
    assert not (mismatch / "inbox").exists()


@pytest.mark.parametrize("declaration", [None, False, "true"])
def test_private_worker_rejects_missing_or_false_filesystem_declaration(root, declaration):
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    values = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 20,
        "enable_shfe": False,
        "gateway_enabled": False,
        "direct_write_enabled": False,
        "max_retries": 0,
        "retry_interval_seconds": 0,
        "candidate_id": ID,
    }
    if declaration is not None:
        values["trusted_local_posix"] = declaration
    result = subprocess.run(
        [sys.executable, "-m", "bt_api_py.trading_calendar_preview", "--_worker"],
        input=json.dumps(values),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "INVALID_ARGUMENTS"
    assert not (root / "inbox").exists()


def _candidate_contender(root, barrier, queue, with_page):
    hours, _, merged = objects()
    barrier.wait(timeout=15)
    result = inbox.retain_candidate(
        root=root,
        candidate_id=ID,
        merged=merged,
        hours_page=hours if with_page else None,
        source_checks=source_checks(),
        filesystem=inbox.TRUSTED_LOCAL_POSIX,
    )
    queue.put((with_page, result.status, result.reason))


def test_two_processes_same_candidate_id_never_overwrite(root):
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    children = [
        ctx.Process(target=_candidate_contender, args=(str(root), barrier, queue, page))
        for page in (True, False)
    ]
    for child in children:
        child.start()
    try:
        outcomes = [queue.get(timeout=25), queue.get(timeout=25)]
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
    winners = [row for row in outcomes if row[1] == "COMMITTED"]
    losers = [row for row in outcomes if row[1] != "COMMITTED"]
    assert len(winners) == len(losers) == 1
    assert losers[0][1:] in [("NOT_COMMITTED", "ID_EXISTS"), ("COMMIT_UNKNOWN", "IO_ERROR")]
    payload = inbox.read_candidate(
        root=root, candidate_id=ID, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )["payload"]
    assert (payload["hours_page"] is not None) is winners[0][0]


@pytest.mark.parametrize("pause", ["before", "after"])
def test_supervised_cli_timeout_returns_original_id_and_unknown(root, tmp_path, pause):
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text("""
import os, time
from bt_api_py import trading_calendar_candidate_inbox as inbox
original = inbox.retain_candidate
def paused(**kwargs):
    if os.environ["ITER67_TEST_PAUSE"] == "before":
        time.sleep(8)
    result = original(**kwargs)
    if os.environ["ITER67_TEST_PAUSE"] == "after":
        time.sleep(8)
    return result
inbox.retain_candidate = paused
""")
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    env = os.environ.copy()
    env["ITER67_TEST_PAUSE"] = pause
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_preview",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "4",
        "--retain-candidate",
        "--trusted-local-posix",
    ]
    if pause == "after":
        command += ["--report-dir", str(root / "missing-report-directory")]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=15, check=False
    )
    assert result.returncode == 25, result.stderr
    decoded = json.loads(result.stdout)
    if pause == "after":
        assert decoded["retention"]["status"] == "NOT_COMMITTED"
        report = decoded["preview"]
    else:
        report = decoded
    assert report["candidate_status"] == "COMMIT_UNKNOWN" and len(report["candidate_id"]) == 32
    path = root / "inbox" / f"candidate-{report['candidate_id']}.json"
    if pause == "before":
        assert not path.exists()
    else:
        assert path.exists()
        payload = inbox.read_candidate(
            root=root, candidate_id=report["candidate_id"], filesystem=inbox.TRUSTED_LOCAL_POSIX
        )["payload"]
        assert payload["input_scope"] == "YAML_ONLY" and payload["unresolved_nights"] is None
    assert not (root / "CURRENT.json").exists()


def test_cli_report_unknown_takes_priority_over_candidate_failure(root, tmp_path):
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text("""
from bt_api_py import trading_calendar_preview_reports as reports
def fail(*args, **kwargs):
    return reports.PreviewRetentionResult("COMMIT_UNKNOWN", "NOT_OBSERVED", run_id="f" * 32, reason="IO_OR_STATE_ERROR")
reports.retain_preview_report = fail
""")
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    conflicting = input_content()
    conflicting["trading_days"].append({**conflicting["trading_days"][0], "status": "open"})
    supplement.write_text(json.dumps(conflicting))
    report_dir = tmp_path.resolve() / "reports"
    report_dir.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_preview",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "20",
        "--retain-candidate",
        "--report-dir",
        str(report_dir),
        "--trusted-local-posix",
    ]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 25, result.stderr
    decoded = json.loads(result.stdout)
    assert decoded["preview"]["candidate_status"] == "NOT_COMMITTED"
    assert decoded["retention"]["status"] == "COMMIT_UNKNOWN"


def test_real_cli_child_retains_and_show_reads_offline(root, tmp_path):
    hours_file = tmp_path / "hours.html"
    holiday_file = tmp_path / "holiday.html"
    hours_file.write_bytes(HOURS)
    holiday_file.write_bytes(holiday_html())
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text("""
from datetime import UTC, datetime
from pathlib import Path
import os
import bt_api_ctp.calendar_shfe_fetch as fetch
from bt_api_ctp.calendar_shfe_page import parse_shfe_page
from bt_api_ctp.calendar_shfe_holiday import parse_shfe_holiday
def hours(**kwargs):
    p = parse_shfe_page(source_id=fetch._SOURCE, html=Path(os.environ["ITER67_TEST_HOURS"]).read_bytes())
    return fetch.ShfeFetchResult(p.status, "HTTP_OK", datetime(2026, 1, 1, tzinfo=UTC), p)
def holiday(**kwargs):
    p = parse_shfe_holiday(source_id=fetch._HOLIDAY_SOURCE, html=Path(os.environ["ITER67_TEST_HOLIDAY"]).read_bytes())
    return fetch.ShfeHolidayFetchResult(p.status, "HTTP_OK", datetime(2026, 1, 1, tzinfo=UTC), p)
fetch.collect_shfe_page = hours
fetch.collect_shfe_holiday = holiday
""")
    config = root.parent / "settings.yaml"
    config.write_text(f"calendars:\n  root: {root}\n")
    supplement = root / "input.yaml"
    supplement.write_text(json.dumps(input_content()))
    env = os.environ.copy()
    env["ITER67_TEST_HOURS"] = str(hours_file)
    env["ITER67_TEST_HOLIDAY"] = str(holiday_file)
    env["PYTHONPATH"] = str(inject) + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_preview",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "30",
        "--enable-shfe",
        "--retain-candidate",
        "--trusted-local-posix",
        "--max-retries",
        "0",
    ]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=40, check=False
    )
    assert result.returncode == 10, result.stderr
    report = json.loads(result.stdout)
    assert report["candidate_status"] == "COMMITTED"
    assert len(report["candidate_id"]) == 32 and str(root) not in result.stdout
    assert "合成甲" not in result.stdout
    shown = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_candidate_inbox",
            "show",
            "--calendar-root",
            str(root),
            "--candidate-id",
            report["candidate_id"],
            "--trusted-local-posix",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["payload"]["hours_page"]["products"][0]["name"] == "合成甲"
    assert not (root / "CURRENT.json").exists()
