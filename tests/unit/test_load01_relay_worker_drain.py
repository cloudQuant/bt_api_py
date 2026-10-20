"""Focused deadline and durable-cursor tests for the LOAD01 relay drain helper."""

from __future__ import annotations

import pytest


def _drain_helper():
    from tests.unit.test_load01_hundred_strategy_30min import (
        _load01_drain_relay_until_empty,
    )

    return _load01_drain_relay_until_empty


def test_relay_drain_does_not_start_another_poll_after_deadline() -> None:
    now = [10.0]
    poll_results: list[int] = []

    def clock() -> float:
        return now[0]

    def poll_one() -> int:
        poll_results.append(1)
        now[0] = 20.0
        return 1

    with pytest.raises(
        TimeoutError,
        match="LOAD01 relay drain deadline headroom exhausted",
    ):
        _drain_helper()(
            poll_one,
            lambda: {},
            {"execution": 2},
            2,
            15.0,
            clock=clock,
            rpc_headroom=1.0,
        )

    assert poll_results == [1]


def test_relay_drain_checks_exact_counts_only_after_an_empty_poll() -> None:
    source_rows = {"execution": 3, "cancellation": 2}
    poll_results = iter((1, 1, 1, 1, 1, 0))
    observed_polls: list[int] = []
    observed_terminal_calls: list[int] = []
    state = {
        "execution": {
            "source_rows": 3,
            "source_sequence": 103,
            "checkpoint_sequence": 103,
        },
        "cancellation": {
            "source_rows": 2,
            "source_sequence": 52,
            "checkpoint_sequence": 52,
        },
    }

    def poll_one() -> int:
        result = next(poll_results)
        observed_polls.append(result)
        return result

    def terminal_state():
        observed_terminal_calls.append(len(observed_polls))
        assert observed_polls[-1] == 0
        return state

    result = _drain_helper()(
        poll_one,
        terminal_state,
        source_rows,
        5,
        100.0,
        clock=lambda: 0.0,
        rpc_headroom=1.0,
    )

    assert result == {
        "poll_count": 6,
        "delivered": 5,
        "source_rows": source_rows,
    }
    assert observed_polls == [1, 1, 1, 1, 1, 0]
    assert observed_terminal_calls == [6]


def test_relay_drain_counts_already_delivered_prefix_toward_total() -> None:
    source_rows = {"execution": 3, "cancellation": 2}
    poll_results = iter((1, 1, 1, 0))
    state = {
        "execution": {
            "source_rows": 3,
            "source_sequence": 103,
            "checkpoint_sequence": 103,
        },
        "cancellation": {
            "source_rows": 2,
            "source_sequence": 52,
            "checkpoint_sequence": 52,
        },
    }

    result = _drain_helper()(
        lambda: next(poll_results),
        lambda: state,
        source_rows,
        5,
        100.0,
        clock=lambda: 0.0,
        rpc_headroom=1.0,
        already_delivered=2,
    )

    assert result == {
        "poll_count": 4,
        "delivered": 5,
        "source_rows": source_rows,
    }


def test_relay_drain_rejects_checkpoint_lag() -> None:
    poll_results = iter((1, 0))

    def poll_one() -> int:
        return next(poll_results)

    bad_terminal_state = {
        "execution": {
            "source_rows": 2,
            "source_sequence": 12,
            "checkpoint_sequence": 11,
        },
        "cancellation": {
            "source_rows": 1,
            "source_sequence": 7,
            "checkpoint_sequence": 7,
        },
    }

    with pytest.raises(AssertionError):
        _drain_helper()(
            poll_one,
            lambda: bad_terminal_state,
            {"execution": 2, "cancellation": 1},
            3,
            100.0,
            clock=lambda: 0.0,
            rpc_headroom=1.0,
        )


def test_relay_drain_rejects_total_shortfall_after_prefix() -> None:
    source_rows = {"execution": 3, "cancellation": 2}
    poll_results = iter((1, 1, 0))
    state = {
        "execution": {
            "source_rows": 3,
            "source_sequence": 103,
            "checkpoint_sequence": 103,
        },
        "cancellation": {
            "source_rows": 2,
            "source_sequence": 52,
            "checkpoint_sequence": 52,
        },
    }

    with pytest.raises(AssertionError):
        _drain_helper()(
            lambda: next(poll_results),
            lambda: state,
            source_rows,
            5,
            100.0,
            clock=lambda: 0.0,
            rpc_headroom=1.0,
            already_delivered=2,
        )


def test_relay_drain_rejects_delivery_count_mismatch_after_empty_poll() -> None:
    poll_results = iter((1, 0))
    terminal = {
        "execution": {
            "source_rows": 2,
            "source_sequence": 12,
            "checkpoint_sequence": 12,
        },
    }

    with pytest.raises(AssertionError):
        _drain_helper()(
            lambda: next(poll_results),
            lambda: terminal,
            {"execution": 2},
            2,
            100.0,
            clock=lambda: 0.0,
            rpc_headroom=1.0,
        )


def test_relay_drain_propagates_poll_exception_unchanged() -> None:
    failure = RuntimeError("synthetic relay poll failure")

    def poll_one() -> int:
        raise failure

    with pytest.raises(RuntimeError) as caught:
        _drain_helper()(
            poll_one,
            lambda: {},
            {"execution": 0},
            0,
            100.0,
            clock=lambda: 0.0,
            rpc_headroom=1.0,
        )

    assert caught.value is failure
