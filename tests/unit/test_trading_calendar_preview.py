"""Offline tests for the experimental preview; no real source or account access."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py import trading_calendar_preview as preview
from bt_api_py.trading_calendar_check import CalendarCheckReport


def checked(**kwargs):
    return CalendarCheckReport(
        "CHECKED",
        "SUPPLEMENT_UNVERIFIED",
        0,
        supplement_status="CANDIDATE",
        source_verification="UNVERIFIED",
    )


def source(transport, page_status=None):
    page = None
    status = transport
    if transport == "HTTP_OK":
        status = page_status or "CANDIDATE"
        page = SimpleNamespace(
            status=status, verification="UNVERIFIED", products=("private raw product",)
        )
    return SimpleNamespace(
        status=status, transport_status=transport, verification="UNVERIFIED", page=page
    )


def holiday(transport="HTTP_OK", page_status="CANDIDATE"):
    page = None
    status = transport
    if transport == "HTTP_OK":
        status = page_status
        page = SimpleNamespace(
            status=status, verification="UNVERIFIED", coverage="NOT_PROVEN", rows=()
        )
    return SimpleNamespace(
        status=status,
        transport_status=transport,
        verification="UNVERIFIED",
        coverage="NOT_PROVEN",
        page=page,
    )


class Clock:
    def __init__(self):
        self.value = 0.0
        self.waits = []

    def now(self):
        return self.value

    def wait(self, duration):
        self.waits.append(duration)
        self.value += duration


def run(**kwargs):
    args = {
        "config_file": "/private/config",
        "calendar_root": "/private/root",
        "supplement_file": "/private/data",
        "budget_seconds": 1300,
        "checker": checked,
        "holiday_collector": lambda **kw: holiday(),
    }
    args.update(kwargs)
    return preview.run_preview(**args)


def test_check_gates_opt_in_and_sanitizes_failure():
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return source("HTTP_OK")

    result = run(collector=collect)
    assert result.reason == "SHFE_NOT_REQUESTED" and calls == []
    result = run(
        enable_shfe=True,
        collector=collect,
        checker=lambda **kw: CalendarCheckReport("ERROR", "CONFIG_YAML_INVALID", 2),
    )
    assert result.reason == "INPUT_CHECK_FAILED" and calls == []
    result = run(
        enable_shfe=True,
        collector=collect,
        checker=lambda **kw: (_ for _ in ()).throw(RuntimeError("private secret")),
    )
    assert result.reason == "INPUT_CHECK_FAILED" and "private" not in json.dumps(result.to_dict())
    assert calls == []


def test_transient_retries_count_from_completion_and_success_stays_incomplete():
    clock = Clock()
    starts = []
    replies = iter([source("NETWORK_TIMEOUT"), source("CHILD_TIMEOUT"), source("HTTP_OK")])

    def collect(**kwargs):
        starts.append((clock.value, kwargs))
        clock.value += 2
        return next(replies)

    result = run(enable_shfe=True, collector=collect, monotonic=clock.now, wait=clock.wait)
    assert [start for start, _ in starts] == [0, 602, 1204]
    assert all(args["total_timeout"] <= 10 for _, args in starts)
    assert max(clock.waits) <= 30 and sum(clock.waits) == 1200
    assert [x.transport_status for x in result.attempts] == [
        "NETWORK_TIMEOUT",
        "CHILD_TIMEOUT",
        "HTTP_OK",
    ]
    assert result.reason == "OTHER_VENUES_NOT_IMPLEMENTED"
    assert result.completeness == "INCOMPLETE" and result.verification == "UNVERIFIED"
    assert result.other_five_venues == "NOT_IMPLEMENTED" and result.publication == "NOT_ATTEMPTED"
    assert result.attempts[-1].product_count == 1
    assert "private" not in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    "transport,page_status,reason",
    [
        ("TLS_ERROR", None, "SHFE_NOT_VERIFIED"),
        ("HTTP_ERROR", None, "SHFE_NOT_VERIFIED"),
        ("REDIRECT", None, "SHFE_NOT_VERIFIED"),
        ("CHILD_CLEANUP_UNCERTAIN", None, "CHILD_CLEANUP_UNCERTAIN"),
        ("HTTP_OK", "STRUCTURE_CHANGED", "SHFE_NOT_VERIFIED"),
        ("HTTP_OK", "NEEDS_REVIEW", "SHFE_NOT_VERIFIED"),
    ],
)
def test_no_blind_retry(transport, page_status, reason):
    clock = Clock()
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return source(transport, page_status)

    report = run(enable_shfe=True, collector=collect, monotonic=clock.now, wait=clock.wait)
    assert report.reason == reason and len(calls) == 1 and not clock.waits
    assert report.source_status == (page_status or transport)


def test_budget_and_retry_limit():
    clock = Clock()
    result = run(
        enable_shfe=True,
        collector=lambda **kw: source("NETWORK_TIMEOUT"),
        monotonic=clock.now,
        wait=clock.wait,
        budget_seconds=30,
    )
    assert result.reason == "RETRY_BUDGET_EXHAUSTED" and len(result.attempts) == 1
    assert clock.waits == []
    result = run(
        enable_shfe=True,
        collector=lambda **kw: source("CHILD_TIMEOUT"),
        monotonic=clock.now,
        wait=clock.wait,
        max_retries=0,
    )
    assert result.reason == "RETRY_LIMIT_REACHED" and len(result.attempts) == 1

    def uncertain(**kwargs):
        clock.value += 20
        return source("CHILD_CLEANUP_UNCERTAIN")

    result = run(
        enable_shfe=True,
        collector=uncertain,
        monotonic=clock.now,
        wait=clock.wait,
        budget_seconds=3,
    )
    assert result.reason == "CHILD_CLEANUP_UNCERTAIN" and result.exit_code == 23


@pytest.mark.parametrize(
    "settings",
    [
        {"budget_seconds": 0},
        {"budget_seconds": float("nan")},
        {"budget_seconds": True},
        {"max_retries": 3},
        {"max_retries": True},
        {"retry_interval_seconds": -1},
        {"enable_shfe": 1},
    ],
)
def test_bad_arguments_are_rejected(settings):
    assert run(**settings).reason == "INVALID_ARGUMENTS"


def test_collector_failures_and_protocol_errors_are_sanitized():
    def bad(**kwargs):
        raise RuntimeError("sensitive message")

    report = run(enable_shfe=True, collector=bad)
    assert report.source_status == "COLLECTOR_ERROR" and "sensitive" not in json.dumps(
        report.to_dict()
    )
    report = run(enable_shfe=True, collector=lambda **kw: SimpleNamespace(status="private"))
    assert report.source_status == "COLLECTOR_PROTOCOL_ERROR"
    assert (
        preview._parse_worker_result(b'{"secret": "private"}', 0).reason == "WORKER_PROTOCOL_ERROR"
    )


def test_supervisor_interrupt_preserves_progress_and_reports_cleanup_uncertainty(monkeypatch):
    pending = preview.Attempt(1, "IN_PROGRESS", "IN_PROGRESS", "UNVERIFIED", 0)
    event = preview.PreviewReport(
        "INCOMPLETE",
        "IN_PROGRESS",
        10,
        input_status="CHECKED",
        input_code="SUPPLEMENT_UNVERIFIED",
        supplement_status="CANDIDATE",
        source_status="IN_PROGRESS",
        attempts=(pending,),
    )
    output = json.dumps(event.to_dict()).encode() + b"\n"

    class Child:
        pid = 32123

        def communicate(self, input=None, timeout=None):
            if input is not None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return output, b""

    times = iter([0, 0.1, 1.1])
    kills = []
    monkeypatch.setattr(preview.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(preview.subprocess, "Popen", lambda *a, **kw: Child())
    monkeypatch.setattr(preview.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    report = preview._supervise({"budget_seconds": 1})
    assert report.reason == "WORKER_CLEANUP_UNCERTAIN" and report.exit_code == 23
    assert report.input_status == "CHECKED" and report.attempts[0].source_status == "INTERRUPTED"
    assert kills == [(32123, preview.signal.SIGKILL)]


def test_real_cli_worker_checks_local_fixture_without_network(tmp_path):
    root = tmp_path / "日历 数据"
    root.mkdir()
    config = tmp_path / "配置.yaml"
    config.write_text("calendars:\n  root: '日历 数据'\nsecret: private-token\n", encoding="utf-8")
    supplement = root / "补充.yaml"
    supplement.write_text(
        "sources:\n  - id: synthetic\n    description: private-source\n"
        "trading_days:\n  - venue: SHFE\n    day: '2026-11-12'\n"
        "    status: open\n    source_ids: [synthetic]\n"
        "trading_hours: []\nexceptions: []\n",
        encoding="utf-8",
    )
    current = root / "CURRENT.json"
    current.write_bytes(b"sentinel")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    sdk = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(sdk), str(sdk / "bt_api/bt_api_ctp/src")))
    cmd = [
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
    ]
    completed = subprocess.run(
        cmd, cwd=sdk, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 10 and report["reason"] == "SHFE_NOT_REQUESTED"
    assert report["input_status"] == "CHECKED" and report["source_status"] == "NOT_REQUESTED"
    assert report["completeness"] == "INCOMPLETE" and report["verification"] == "UNVERIFIED"
    assert "private" not in completed.stdout and str(tmp_path) not in completed.stdout
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    supplement.unlink()
    completed = subprocess.run(
        cmd + ["--enable-shfe"],
        cwd=sdk,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (
        completed.returncode == 20
        and json.loads(completed.stdout)["reason"] == "INPUT_CHECK_FAILED"
    )


def test_actual_input_check_and_shfe_collector_are_composed_offline(tmp_path, monkeypatch):
    sdk = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(sdk / "bt_api/bt_api_ctp/src"))
    from bt_api_ctp.calendar_shfe_fetch import (
        _HOLIDAY_SOURCE,
        _SOURCE,
        collect_shfe_holiday,
        collect_shfe_page,
    )
    from bt_api_ctp.calendar_shfe_holiday import HolidayCandidate
    from bt_api_ctp.calendar_shfe_page import PageCandidate

    root = tmp_path / "calendars"
    root.mkdir()
    config = tmp_path / "settings.yaml"
    config.write_text("calendars:\n  root: calendars\n", encoding="utf-8")
    supplement = root / "input.yaml"
    supplement.write_text(
        "sources:\n  - id: x\n    description: synthetic\n"
        "trading_days:\n  - venue: SHFE\n    day: '2026-11-12'\n"
        "    status: open\n    source_ids: [x]\ntrading_hours: []\nexceptions: []\n",
        encoding="utf-8",
    )
    calls = []

    def fake_runner(*args):
        calls.append(args)
        return "HTTP_OK", PageCandidate(status="NEEDS_REVIEW", source_id=_SOURCE)

    def holiday_runner(*args):
        calls.append(args)
        return "HTTP_OK", HolidayCandidate(status="CANDIDATE", source_id=_HOLIDAY_SOURCE)

    result = preview.run_preview(
        config_file=config,
        calendar_root=root,
        supplement_file=supplement,
        budget_seconds=30,
        enable_shfe=True,
        collector=lambda **kwargs: collect_shfe_page(**kwargs, runner=fake_runner),
        holiday_collector=lambda **kwargs: collect_shfe_holiday(**kwargs, runner=holiday_runner),
    )
    assert result.input_status == "CHECKED" and result.source_status == "NEEDS_REVIEW"
    assert result.reason == "SHFE_NOT_VERIFIED" and len(calls) == 2
    assert result.holiday_status == "CANDIDATE" and result.holiday_coverage == "NOT_PROVEN"
    assert result.completeness == "INCOMPLETE" and result.verification == "UNVERIFIED"


def test_two_sources_have_independent_attempts_and_shared_budget():
    clock = Clock()
    starts = []
    hour_replies = iter([source("NETWORK_TIMEOUT"), source("HTTP_OK")])
    holiday_replies = iter([holiday("CHILD_TIMEOUT"), holiday()])

    def hours(**kwargs):
        starts.append(("hours", clock.value, kwargs["total_timeout"]))
        clock.value += 2
        return next(hour_replies)

    def closures(**kwargs):
        starts.append(("holiday", clock.value, kwargs["total_timeout"]))
        clock.value += 2
        return next(holiday_replies)

    result = run(
        enable_shfe=True,
        collector=hours,
        holiday_collector=closures,
        monotonic=clock.now,
        wait=clock.wait,
    )
    assert [(name, start) for name, start, _ in starts] == [
        ("hours", 0),
        ("hours", 602),
        ("holiday", 604),
        ("holiday", 1206),
    ]
    assert [a.transport_status for a in result.attempts] == ["NETWORK_TIMEOUT", "HTTP_OK"]
    assert [a.transport_status for a in result.holiday_attempts] == ["CHILD_TIMEOUT", "HTTP_OK"]
    assert [a.parse_status for a in result.attempts] == ["NOT_ATTEMPTED", "CANDIDATE"]
    assert [a.parse_status for a in result.holiday_attempts] == ["UNKNOWN", "CANDIDATE"]
    assert result.source_status == result.holiday_status == "CANDIDATE"
    assert result.holiday_coverage == "NOT_PROVEN" and result.verification == "UNVERIFIED"
    assert result.reason == "OTHER_VENUES_NOT_IMPLEMENTED" and result.completeness == "INCOMPLETE"
    assert result.publication == "NOT_ATTEMPTED" and "private" not in json.dumps(result.to_dict())
    assert preview._parse_worker_result(json.dumps(result.to_dict()).encode(), 10) == result


def test_source_failure_does_not_hide_other_source_and_no_retry_for_structure():
    calls = []

    def closures(**kw):
        calls.append(kw)
        return holiday("HTTP_OK", "STRUCTURE_CHANGED")

    result = run(
        enable_shfe=True, collector=lambda **kw: source("HTTP_OK"), holiday_collector=closures
    )
    assert result.source_status == "CANDIDATE" and result.holiday_status == "STRUCTURE_CHANGED"
    assert result.reason == "SHFE_NOT_VERIFIED" and len(calls) == 1
    result = run(
        enable_shfe=True,
        collector=lambda **kw: source("TLS_ERROR"),
        holiday_collector=lambda **kw: holiday(),
    )
    assert result.source_status == "TLS_ERROR" and result.holiday_status == "CANDIDATE"
    assert result.source_reason == "SHFE_NOT_VERIFIED" and result.holiday_reason == "CANDIDATE"


def test_holiday_budget_exhausted_and_coverage_is_not_claimed():
    clock = Clock()

    def hours(**kw):
        clock.value += 4
        return source("HTTP_OK")

    result = run(
        enable_shfe=True,
        collector=hours,
        holiday_collector=lambda **kw: holiday(),
        budget_seconds=4,
        monotonic=clock.now,
        wait=clock.wait,
    )
    assert result.reason == "BUDGET_EXHAUSTED" and result.source_status == "CANDIDATE"
    assert result.holiday_status == "NOT_ATTEMPTED" and not result.holiday_attempts
    result = run(
        enable_shfe=True,
        collector=lambda **kw: source("HTTP_OK"),
        holiday_collector=lambda **kw: SimpleNamespace(
            status="CANDIDATE",
            transport_status="HTTP_OK",
            verification="UNVERIFIED",
            coverage="COMPLETE",
            page=None,
        ),
    )
    assert (
        result.holiday_status == "COLLECTOR_PROTOCOL_ERROR"
        and result.holiday_coverage == "NOT_PROVEN"
    )


def test_holiday_interrupt_preserves_both_source_histories(monkeypatch):
    done = preview.Attempt(1, "HTTP_OK", "CANDIDATE", "UNVERIFIED", 2)
    pending = preview.Attempt(1, "IN_PROGRESS", "IN_PROGRESS", "UNVERIFIED", 0)
    event = preview.PreviewReport(
        "INCOMPLETE",
        "IN_PROGRESS",
        10,
        input_status="CHECKED",
        input_code="SUPPLEMENT_UNVERIFIED",
        supplement_status="CANDIDATE",
        source_status="CANDIDATE",
        attempts=(done,),
        source_reason="CANDIDATE",
        holiday_status="IN_PROGRESS",
        holiday_attempts=(pending,),
    )
    output = json.dumps(event.to_dict()).encode() + b"\n"

    class Child:
        pid = 32123

        def communicate(self, input=None, timeout=None):
            if input is not None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return output, b""

    times = iter([0, 0.1, 1.1])
    monkeypatch.setattr(preview.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(preview.subprocess, "Popen", lambda *a, **kw: Child())
    monkeypatch.setattr(preview.os, "killpg", lambda pid, sig: None)
    result = preview._supervise({"budget_seconds": 1})
    assert result.reason == "WORKER_CLEANUP_UNCERTAIN" and result.source_status == "CANDIDATE"
    assert (
        result.holiday_status == "INTERRUPTED"
        and result.holiday_attempts[0].source_status == "INTERRUPTED"
    )
    assert result.holiday_attempts[0].parse_status == "INTERRUPTED"


def test_insufficient_retry_budget_still_checks_other_source_without_waiting():
    clock = Clock()
    calls = []
    result = run(
        enable_shfe=True,
        collector=lambda **kw: source("NETWORK_TIMEOUT"),
        holiday_collector=lambda **kw: (calls.append(kw), holiday())[1],
        budget_seconds=30,
        monotonic=clock.now,
        wait=clock.wait,
    )
    assert result.source_reason == "RETRY_BUDGET_EXHAUSTED"
    assert result.holiday_status == "CANDIDATE" and len(calls) == 1
    assert result.reason == "RETRY_BUDGET_EXHAUSTED" and not clock.waits


def test_holiday_cleanup_uncertain_stops_without_retry():
    calls = []
    result = run(
        enable_shfe=True,
        collector=lambda **kw: source("HTTP_OK"),
        holiday_collector=lambda **kw: (calls.append(kw), holiday("CHILD_CLEANUP_UNCERTAIN"))[1],
    )
    assert (
        result.source_status == "CANDIDATE" and result.holiday_status == "CHILD_CLEANUP_UNCERTAIN"
    )
    assert result.reason == "CHILD_CLEANUP_UNCERTAIN" and len(calls) == 1


def test_two_sources_are_never_called_when_gate_closed():
    def forbidden(**kwargs):
        raise AssertionError("network port must not be called")

    for options in (
        {},
        {
            "enable_shfe": True,
            "checker": lambda **kw: CalendarCheckReport("ERROR", "CONFIG_READ_FAILED", 2),
        },
    ):
        result = run(collector=forbidden, holiday_collector=forbidden, **options)
        assert result.reason in {"SHFE_NOT_REQUESTED", "INPUT_CHECK_FAILED"}
        assert not result.attempts and not result.holiday_attempts
