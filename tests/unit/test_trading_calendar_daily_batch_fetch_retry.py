"""Bounded, explicitly classified AKShare fetch retry for the daily batch."""

import json

import pytest

from bt_api_py import trading_calendar_daily_batch as batch
from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root, scope
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation

RETRY_FAKE = r"""
import os
from bt_api_py import trading_calendar_mock_akshare as _mock_akshare
_original_observe_once = _mock_akshare.observe_once
_seen = []
def _retrying_observe_once(*args, **kwargs):
    mode = os.environ.get('RETRY_FAKE')
    if mode and not _seen:
        _seen.append(kwargs.get('observation_id'))
        base = {
            'run_id': kwargs.get('run_id'),
            'observation_id': kwargs.get('observation_id'),
            'association': 'DECLARED_ONLY',
        }
        if mode == 'CLAIM':
            return {**base, 'descriptor_state': 'NOT_ATTEMPTED',
                    'observation_state': 'NOT_ATTEMPTED',
                    'projection_state': 'NOT_ATTEMPTED',
                    'observation_status': 'NOT_ATTEMPTED', 'reason': 'CLAIM_UNCERTAIN'}
        if mode == 'BUDGET':
            return {**base, 'descriptor_state': 'NOT_ATTEMPTED',
                    'observation_state': 'NOT_ATTEMPTED',
                    'projection_state': 'NOT_ATTEMPTED',
                    'observation_status': 'NOT_ATTEMPTED', 'reason': 'BUDGET_EXHAUSTED'}
        if mode == 'DESCRIPTOR':
            return {**base, 'descriptor_state': 'ATTEMPT_UNKNOWN',
                    'observation_state': 'NOT_ATTEMPTED',
                    'projection_state': 'NOT_ATTEMPTED',
                    'observation_status': 'NOT_ATTEMPTED', 'reason': 'DESCRIPTOR_UNAVAILABLE'}
        return {**base, 'descriptor_state': 'COMMITTED',
                'observation_state': 'ATTEMPT_UNKNOWN',
                'projection_state': 'UNKNOWN', 'observation_status': 'UNKNOWN',
                'reason': 'OBSERVATION_OUTCOME_UNKNOWN_RECONCILE_ORIGINAL_ID'}
    return _original_observe_once(*args, **kwargs)
_mock_akshare.observe_once = _retrying_observe_once
"""


def _root(tmp_path):
    return _initialized_batch_root(tmp_path, "auto", RETRY_FAKE)


def _run(root, env, original_run, *extra):
    return command(
        env,
        *operation(
            root,
            original_run,
            "mock-batch",
            "--fetch-retry-interval-seconds",
            "0.001",
            *extra,
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )


def _retry(**kwargs):
    now = [0.0]

    def wait(seconds):
        now[0] += seconds

    return batch._fetch_with_retry(
        interval=600, deadline=1801, clock=lambda: now[0], wait=wait, **kwargs
    )


def test_retry_helper_never_retries_unknown_outcome():
    calls = []

    def fetch():
        calls.append(1)
        return {
            "descriptor_state": "COMMITTED",
            "observation_state": "ATTEMPT_UNKNOWN",
            "observation_status": "UNKNOWN",
            "reason": "OBSERVATION_OUTCOME_UNKNOWN_RECONCILE_ORIGINAL_ID",
        }

    value, attempts, outcome = _retry(
        fetch=fetch, max_attempts=3, unused=lambda: True
    )
    assert len(calls) == 1 and outcome == "UNKNOWN_OUTCOME"
    assert attempts[0]["outcome"] == "UNKNOWN_OUTCOME"
    assert attempts[0]["reason"] == "OUTCOME_UNKNOWN"
    assert value["observation_state"] == "ATTEMPT_UNKNOWN"


def test_retry_helper_retries_transient_only_after_unused_readback():
    calls = []
    results = [
        {
            "descriptor_state": "NOT_ATTEMPTED",
            "observation_state": "NOT_ATTEMPTED",
            "observation_status": "NOT_ATTEMPTED",
            "reason": "CLAIM_UNCERTAIN",
        },
        {
            "descriptor_state": "COMMITTED",
            "observation_state": "COMMITTED",
            "observation_status": "SUCCESS",
            "reason": "NONE",
        },
    ]

    def fetch():
        calls.append(1)
        return results[len(calls) - 1]

    value, attempts, outcome = _retry(
        fetch=fetch, max_attempts=3, unused=lambda: True
    )
    assert len(calls) == 2 and outcome == "NOT_RETRIABLE"
    assert [item["outcome"] for item in attempts] == [
        "TRANSIENT_PRECOMMIT",
        "NOT_RETRIABLE",
    ]
    assert value["observation_status"] == "SUCCESS"


def test_retry_helper_does_not_retry_when_readback_is_not_proven():
    calls = []

    def fetch():
        calls.append(1)
        return {
            "descriptor_state": "NOT_ATTEMPTED",
            "observation_state": "NOT_ATTEMPTED",
            "observation_status": "NOT_ATTEMPTED",
            "reason": "CLAIM_UNCERTAIN",
        }

    _, attempts, outcome = _retry(
        fetch=fetch, max_attempts=3, unused=lambda: False
    )
    assert len(calls) == 1 and outcome == "NOT_RETRIABLE"
    assert attempts[0]["reason"] == "PRECOMMIT_NOT_PROVEN"


def test_retry_helper_is_bounded_and_reports_exhaustion():
    calls = []

    def fetch():
        calls.append(1)
        return {
            "descriptor_state": "NOT_ATTEMPTED",
            "observation_state": "NOT_ATTEMPTED",
            "observation_status": "NOT_ATTEMPTED",
            "reason": "CLAIM_UNCERTAIN",
        }

    _, attempts, outcome = _retry(fetch=fetch, max_attempts=3, unused=lambda: True)
    assert len(calls) == 3 and len(attempts) == 3
    assert outcome == "TRANSIENT_EXHAUSTED"
    assert all(item["outcome"] == "TRANSIENT_PRECOMMIT" for item in attempts)
    assert all(set(item) == {"attempt", "at", "outcome", "reason"} for item in attempts)


def test_retry_helper_retries_documented_transient_exception_before_commit():
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("no local record")
        return {
            "descriptor_state": "COMMITTED",
            "observation_state": "COMMITTED",
            "observation_status": "SUCCESS",
            "reason": "NONE",
        }

    _, attempts, outcome = _retry(fetch=fetch, max_attempts=2, unused=lambda: True)
    assert len(calls) == 2 and attempts[0]["reason"] == "TimeoutError"
    assert outcome == "NOT_RETRIABLE"


def test_retry_policy_is_bounded_and_fails_closed(tmp_path):
    result = batch.run(
        root=str(tmp_path / "root"),
        run_id="a" * 32,
        scopes=[],
        fetch_retry=4,
    )
    assert result["status"] == "NOT_COMMITTED"
    assert result["reason"] == "INVALID_FETCH_RETRY"


def test_daily_batch_retries_precommit_transient_and_records_attempts(tmp_path):
    root, env, original_run = _root(tmp_path)
    env["RETRY_FAKE"] = "CLAIM"
    rc, rows, err = _run(root, env, original_run)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    result = rows[-1]
    assert result["fetch_retry"]["max_attempts"] == 3
    assert result["fetch_retry"]["interval_seconds"] == 0.001
    assert result["fetch_retry"]["record"] == "VISIBLE_UNAUTHENTICATED"
    retried = [item for item in result["fetch_retry"]["attempts"] if len(item["attempts"]) > 1]
    assert len(retried) == 1
    attempts = retried[0]["attempts"]
    assert [item["outcome"] for item in attempts] == [
        "TRANSIENT_PRECOMMIT",
        "NOT_RETRIABLE",
    ]
    assert attempts[0]["reason"] == "CLAIM_UNCERTAIN"
    assert result["fetch_retry"]["retried"] == 1
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_daily_batch_cli_custom_attempt_limit(tmp_path):
    root, env, original_run = _root(tmp_path)
    env["RETRY_FAKE"] = "CLAIM"
    rc, rows, err = _run(root, env, original_run, "--fetch-max-attempts", "1")
    assert rc != 0, (rows, err)
    result = rows[-1]
    assert result["fetch_retry"]["max_attempts"] == 1
    assert result["fetch_retry"]["retried"] == 0
    assert result["fetch_retry"]["exhausted"] == 1


def test_daily_batch_reports_retry_budget_exhaustion(tmp_path):
    root, env, original_run = _root(tmp_path)
    env["RETRY_FAKE"] = "CLAIM"
    _, rows, err = _run(
        root, env, original_run, "--batch-budget", "20",
        "--fetch-retry-interval-seconds", "600",
    )
    result = rows[-1]
    assert result["fetch_retry"]["budget_exhausted"] == 1, (rows, err)
    assert result["fetch_retry"]["record"] == "VISIBLE_UNAUTHENTICATED"
    assert result["fetch_retry"]["retried"] == 0


def test_daily_batch_unknown_outcome_is_not_retried(tmp_path):
    root, env, original_run = _root(tmp_path)
    env["RETRY_FAKE"] = "UNKNOWN"
    rc, rows, err = _run(root, env, original_run)
    assert rc != 0
    result = rows[-1]
    assert result["status"] == "UNKNOWN", (result, err)
    assert result["fetch_retry"]["retried"] == 0
    assert result["fetch_retry"]["outcome_unknown"] == 1
    assert len((tmp_path / "calls").read_text().splitlines()) == 1


def test_retry_helper_retries_the_shapes_the_daily_batch_actually_produces():
    for reason, descriptor in (
        ("BUDGET_EXHAUSTED", "NOT_ATTEMPTED"),
        ("DESCRIPTOR_UNAVAILABLE", "ATTEMPT_UNKNOWN"),
    ):
        calls = []

        def fetch(calls=calls, reason=reason, descriptor=descriptor):
            calls.append(1)
            return {
                "descriptor_state": descriptor,
                "observation_state": "NOT_ATTEMPTED",
                "observation_status": "NOT_ATTEMPTED",
                "reason": reason,
            }

        _, attempts, outcome = _retry(
            fetch=fetch, max_attempts=2, unused=lambda: True
        )
        assert len(calls) == 2 and outcome == "TRANSIENT_EXHAUSTED"
        assert attempts[0]["outcome"] == "TRANSIENT_PRECOMMIT"


def test_retry_helper_does_not_retry_a_committed_descriptor_or_definite_error():
    for value in (
        {
            "descriptor_state": "COMMITTED",
            "observation_state": "NOT_ATTEMPTED",
            "observation_status": "NOT_ATTEMPTED",
            "reason": "BUDGET_EXHAUSTED",
        },
        {
            "descriptor_state": "NOT_ATTEMPTED",
            "observation_state": "NOT_ATTEMPTED",
            "observation_status": "NOT_ATTEMPTED",
            "reason": "INVALID_OR_UNAVAILABLE_INPUT",
        },
    ):
        calls = []

        def fetch(calls=calls, value=value):
            calls.append(1)
            return value

        _, _, outcome = _retry(
            fetch=fetch, max_attempts=3, unused=lambda: True
        )
        assert len(calls) == 1
        assert outcome in ("NOT_RETRIABLE", "UNKNOWN_OUTCOME")


def test_daily_batch_retries_the_real_budget_exhausted_shape(tmp_path):
    root, env, original_run = _root(tmp_path)
    env["RETRY_FAKE"] = "BUDGET"
    rc, rows, err = _run(root, env, original_run)
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    result = rows[-1]
    retried = [item for item in result["fetch_retry"]["attempts"] if len(item["attempts"]) > 1]
    assert len(retried) == 1
    assert retried[0]["attempts"][0]["reason"] == "BUDGET_EXHAUSTED"


def test_default_retry_count_interval_and_attempt_end_spacing():
    assert batch._retry_policy(None) == 3
    assert batch._retry_interval(None) == 600
    now = [0.0]
    starts = []
    waits = []

    def fetch():
        starts.append(now[0])
        now[0] += 7
        return {
            "descriptor_state": "NOT_ATTEMPTED",
            "observation_state": "NOT_ATTEMPTED",
            "reason": "CLAIM_UNCERTAIN",
        }

    def wait(seconds):
        waits.append(seconds)
        now[0] += seconds

    _, attempts, outcome = batch._fetch_with_retry(
        fetch=fetch, max_attempts=3, unused=lambda: True, interval=600,
        deadline=1800, clock=lambda: now[0], wait=wait,
    )
    assert starts == [0, 607, 1214]
    assert waits == [600, 600]
    assert len(attempts) == 3 and outcome == "TRANSIENT_EXHAUSTED"


def test_retry_budget_and_recheck_prevent_unpermitted_attempts():
    calls, waits = [], []
    transient = {
        "descriptor_state": "NOT_ATTEMPTED",
        "observation_state": "NOT_ATTEMPTED",
        "reason": "CLAIM_UNCERTAIN",
    }

    def fetch():
        calls.append(1)
        return transient
    _, attempts, outcome = batch._fetch_with_retry(
        fetch=fetch, max_attempts=3, unused=lambda: True, interval=10,
        deadline=10, clock=lambda: 0, wait=waits.append,
    )
    assert len(calls) == len(attempts) == 1 and waits == []
    assert outcome == "RETRY_BUDGET_EXHAUSTED"
    reads = iter((True, False))
    now = [0]

    def wait(seconds):
        now[0] += seconds
    value, attempts, outcome = batch._fetch_with_retry(
        fetch=fetch, max_attempts=3, unused=lambda: next(reads), interval=10,
        deadline=100, clock=lambda: now[0], wait=wait,
    )
    assert len(attempts) == 1 and outcome == "UNKNOWN_OUTCOME"
    assert value["observation_state"] == "UNKNOWN"


def test_retry_interrupted_during_wait_never_retries():
    calls = []

    def fetch():
        calls.append(1)
        return {
            "descriptor_state": "NOT_ATTEMPTED",
            "observation_state": "NOT_ATTEMPTED",
            "reason": "CLAIM_UNCERTAIN",
        }

    def interrupt(seconds):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        batch._fetch_with_retry(fetch=fetch, max_attempts=3, unused=lambda: True,
                                interval=1, deadline=100, clock=lambda: 0, wait=interrupt)
    assert len(calls) == 1


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf"), True, 86401])
def test_invalid_retry_interval_fails_before_any_batch_work(tmp_path, interval):
    result = batch.run(root=str(tmp_path), run_id="a" * 32, scopes=[],
                       fetch_retry_interval_seconds=interval)
    assert result["reason"] == "INVALID_FETCH_RETRY"
