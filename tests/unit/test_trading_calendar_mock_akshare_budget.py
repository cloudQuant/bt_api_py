"""One mock-run/AKShare deadline, using a deterministic offline clock."""

from datetime import date

import pytest

from bt_api_py import trading_calendar_mock_akshare as bridge
from bt_api_py import trading_calendar_mock_run as mock_run
from bt_api_py import trading_calendar_observation_run as runs


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


def setup_budget(tmp_path, monkeypatch, *, run_elapsed, body_elapsed=0, write_elapsed=0):
    clock = Clock()
    calls = []
    events = []
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    monkeypatch.setattr(runs.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bridge, "_guard", lambda *_: None)

    def body(**_):
        clock.now += body_elapsed
        return {"fallback": {}}

    monkeypatch.setattr(bridge, "_body", body)

    def run_mock(**kwargs):
        calls.append(("mock_budget", kwargs["budget_seconds"]))
        clock.now += run_elapsed
        return {
            "candidate_state": "COMMITTED",
            "state": "INCOMPLETE",
            "simulation_root": str(tmp_path),
        }

    monkeypatch.setattr(mock_run, "run_mock", run_mock)
    original_write = runs._write_once

    def write(*args):
        result = original_write(*args)
        clock.now += write_elapsed
        return result

    monkeypatch.setattr(runs, "_write_once", write)

    def source(**kwargs):
        calls.append(("source_timeout", kwargs["timeout"]))
        return {"record_state": "ATTEMPT_UNKNOWN", "observation_status": "UNKNOWN"}

    monkeypatch.setattr(bridge.observation, "observe", source)

    def emit(event):
        events.append(event)
        clock.now += 1

    return clock, calls, events, emit


def run(emit, *, budget, timeout):
    return runs.run_once(
        root=None,
        config=None,
        supplement=None,
        budget_seconds=budget,
        enable_shfe=False,
        filesystem="TRUSTED_LOCAL_POSIX",
        run_id="a" * 32,
        capture_id="b" * 32,
        parent_id="c" * 32,
        derived_id="d" * 32,
        mock_year=2026,
        emit=emit,
        akshare_observation_id="e" * 32,
        akshare_request={
            "venue": "SHFE",
            "contract": "RB2701",
            "requested_date": date(2026, 11, 13),
            "kind": "future",
            "timeout": timeout,
        },
    )


def test_slow_mock_exhausts_budget_without_starting_source(tmp_path, monkeypatch):
    _, calls, events, emit = setup_budget(tmp_path, monkeypatch, run_elapsed=10)
    result = run(emit, budget=10, timeout=8)
    source = result["akshare_observation"]
    assert source["observation_state"] == source["observation_status"] == "NOT_ATTEMPTED"
    assert source["reason"] == "BUDGET_EXHAUSTED"
    assert calls == [("mock_budget", 9.0)]
    assert events[0]["observation_id"] == source["observation_id"] == "e" * 32
    assert not (tmp_path / "observation-runs" / f"mock-akshare-{'e' * 32}.json").exists()


def test_allocation_consumes_budget_before_mock_or_source_start(tmp_path, monkeypatch):
    _, calls, events, emit = setup_budget(tmp_path, monkeypatch, run_elapsed=0)
    result = run(emit, budget=0.5, timeout=8)
    assert result["state"] == "NOT_ATTEMPTED" and result["reason"] == "RUN_BUDGET_EXHAUSTED"
    assert result["akshare_observation"]["observation_state"] == "NOT_ATTEMPTED"
    assert result["akshare_observation"]["observation_id"] == events[0]["observation_id"]
    assert not calls


def test_remaining_budget_below_mock_minimum_is_not_reported_as_elapsed(tmp_path, monkeypatch):
    _, calls, _, emit = setup_budget(tmp_path, monkeypatch, run_elapsed=0)
    result = run(emit, budget=1.0005, timeout=8)
    assert result["state"] == "NOT_ATTEMPTED"
    assert result["reason"] == "RUN_BUDGET_BELOW_MINIMUM"
    assert result["akshare_observation"]["reason"] == "BUDGET_BELOW_MINIMUM"
    assert not calls


@pytest.mark.parametrize(("budget", "configured", "expected"), [(10, 8, 4), (20, 4, 4)])
def test_request_timeout_uses_smaller_remaining_or_configured(
    tmp_path, monkeypatch, budget, configured, expected
):
    _, calls, events, emit = setup_budget(tmp_path, monkeypatch, run_elapsed=2, body_elapsed=3)
    result = run(emit, budget=budget, timeout=configured)
    assert calls == [("mock_budget", budget - 1), ("source_timeout", expected)]
    assert result["akshare_observation"]["effective_timeout_seconds"] == expected
    assert result["akshare_observation"]["configured_timeout_seconds"] == configured
    assert events[0]["configured_timeout_seconds"] == configured


def test_deadline_expiring_during_descriptor_write_does_not_start_source(tmp_path, monkeypatch):
    _, calls, _, emit = setup_budget(
        tmp_path, monkeypatch, run_elapsed=2, body_elapsed=2, write_elapsed=5
    )
    result = run(emit, budget=10, timeout=8)
    source = result["akshare_observation"]
    assert calls == [("mock_budget", 9.0)]
    assert source["descriptor_state"] == "COMMITTED"
    assert source["observation_state"] == source["observation_status"] == "NOT_ATTEMPTED"
    assert source["reason"] == "BUDGET_EXHAUSTED"
    assert (tmp_path / "observation-runs" / f"mock-akshare-{'e' * 32}.json").exists()
