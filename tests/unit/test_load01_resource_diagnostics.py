from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.unit._load01_resource_diagnostics import (
    aggregate_role_resources,
    aggregate_strategy_queue_snapshots,
    append_and_validate_load01_resource_sample,
    assert_load01_children_alive,
    capture_market_queue_snapshot,
    capture_process_resource_row,
)


class ProcessGone(Exception):
    pass


class AccessDenied(Exception):
    pass


class FakeProcess:
    def __init__(self, *, rss: int, private: int | None, threads: int) -> None:
        self.memory_info_calls = 0
        self.rss = rss
        self.private = private
        self.threads = threads

    def memory_info(self) -> SimpleNamespace:
        self.memory_info_calls += 1
        return SimpleNamespace(rss=self.rss, private=self.private)

    def num_threads(self) -> int:
        return self.threads


def test_capture_reuses_one_memory_info_for_rss_and_private() -> None:
    process = FakeProcess(rss=100, private=70, threads=3)
    row, returned = capture_process_resource_row(
        "strategy",
        101,
        lambda pid: process,
        process_gone_errors=(ProcessGone,),
        access_denied_errors=(AccessDenied,),
    )

    assert returned is process
    assert process.memory_info_calls == 1
    assert row["rss_bytes"] == 100
    assert row["private_bytes"] == 70
    assert row["thread_count"] == 3


def test_role_aggregation_reports_sum_max_threads_and_pid_count() -> None:
    rows = [
        {"role": "strategy", "pid": 101, "rss_bytes": 100, "private_bytes": 70, "thread_count": 3},
        {"role": "strategy", "pid": 102, "rss_bytes": 250, "private_bytes": 160, "thread_count": 5},
        {"role": "owner", "pid": 201, "rss_bytes": 40, "private_bytes": 30, "thread_count": 2},
    ]

    result = aggregate_role_resources(rows)

    assert result["strategy"]["child_pid_count"] == 2
    assert result["strategy"]["rss_bytes_sum"] == 350
    assert result["strategy"]["rss_bytes_max"] == 250
    assert result["strategy"]["private_bytes_sum"] == 230
    assert result["strategy"]["private_bytes_max"] == 160
    assert result["strategy"]["thread_count_sum"] == 8
    assert result["owner"]["child_pid_count"] == 1
    assert result["owner"]["rss_bytes_sum"] == 40


def test_missing_private_and_thread_values_are_null_with_coverage() -> None:
    rows = [
        {"role": "market", "pid": 301, "rss_bytes": 90, "private_bytes": None, "thread_count": 2},
        {"role": "market", "pid": 302, "rss_bytes": 110, "private_bytes": 20, "thread_count": None},
    ]

    result = aggregate_role_resources(rows)["market"]

    assert result["child_pid_count"] == 2
    assert result["rss_bytes_sum"] == 200
    assert result["rss_coverage_count"] == 2
    assert result["private_bytes_sum"] is None
    assert result["private_bytes_coverage_count"] == 1
    assert result["thread_count_sum"] is None
    assert result["thread_count_coverage_count"] == 1


def test_access_denied_memory_sample_is_null_not_zero() -> None:
    class DeniedMemory(FakeProcess):
        def memory_info(self):
            raise AccessDenied()

    row, returned = capture_process_resource_row(
        "owner",
        202,
        lambda pid: DeniedMemory(rss=0, private=None, threads=4),
        process_gone_errors=(ProcessGone,),
        access_denied_errors=(AccessDenied,),
    )
    result = aggregate_role_resources([row])["owner"]

    assert returned is not None
    assert row["rss_bytes"] is None
    assert row["private_bytes"] is None
    assert row["thread_count"] == 4
    assert result["rss_bytes_sum"] is None
    assert result["rss_coverage_count"] == 0
    assert result["private_bytes_sum"] is None
    assert result["private_bytes_coverage_count"] == 0


def test_disappeared_child_is_counted_but_original_alive_gate_still_fails() -> None:
    def missing_process(pid: int) -> FakeProcess:
        raise ProcessGone(pid)

    row, process = capture_process_resource_row(
        "monitor",
        401,
        missing_process,
        process_gone_errors=(ProcessGone,),
        access_denied_errors=(AccessDenied,),
    )
    aggregate = aggregate_role_resources([row])["monitor"]

    assert process is None
    assert aggregate["child_pid_count"] == 1
    assert aggregate["rss_bytes_sum"] is None
    assert aggregate["rss_coverage_count"] == 0
    assert aggregate["missing_pids"] == [401]
    with pytest.raises(AssertionError, match="LOAD01 process exited during load: monitor-401"):
        assert_load01_children_alive(
            {"monitor-401": {"name": "monitor-401", "process": SimpleNamespace(is_alive=lambda: False)}}
        )


@pytest.mark.parametrize(
    ("pid_coverage", "process_coverage", "rss_coverage", "cpu_coverage", "failed_field"),
    [
        (0, 1, 1, 1, "child_pid_coverage_count=0/1"),
        (1, 0, 0, 0, "child_process_coverage_count=0/1"),
        (1, 1, 0, 1, "child_rss_coverage_count=0/1"),
        (1, 1, 1, 0, "child_cpu_time_coverage_count=0/1"),
    ],
)
def test_incomplete_resource_sample_is_appended_then_fails_even_if_mp_process_is_alive(
    pid_coverage: int,
    process_coverage: int,
    rss_coverage: int,
    cpu_coverage: int,
    failed_field: str,
) -> None:
    alive_calls = 0

    def is_alive() -> bool:
        nonlocal alive_calls
        alive_calls += 1
        return True

    records = {
        "strategy-401": {
            "name": "strategy-401",
            "process": SimpleNamespace(is_alive=is_alive),
        }
    }
    sample = {
        "child_pid_count": 1,
        "child_pid_coverage_count": pid_coverage,
        "child_process_coverage_count": process_coverage,
        "child_rss_coverage_count": rss_coverage,
        "child_cpu_time_coverage_count": cpu_coverage,
    }
    report = {"resource_samples": []}

    with pytest.raises(RuntimeError, match=failed_field):
        append_and_validate_load01_resource_sample(report, sample, records)

    assert report["resource_samples"] == [sample]
    assert alive_calls == 1


def test_current_market_queue_snapshot_sums_and_maxes_all_clients() -> None:
    clients = [
        SimpleNamespace(queue_count=3, queue_bytes=100),
        SimpleNamespace(queue_count=8, queue_bytes=500),
    ]
    clock_values = iter((10.0, 10.2))
    snapshot = capture_market_queue_snapshot(
        clients,
        load_start_monotonic_seconds=5.0,
        monotonic_clock=lambda: next(clock_values),
    )

    assert snapshot["client_count"] == 2
    assert snapshot["queue_count_coverage_count"] == 2
    assert snapshot["queue_count_sum"] == 11
    assert snapshot["queue_count_max"] == 8
    assert snapshot["queue_bytes_sum"] == 600
    assert snapshot["queue_bytes_max"] == 500
    assert snapshot["sample_elapsed_since_load_start_seconds"] == pytest.approx(5.2)


def test_current_queue_snapshot_missing_values_stay_null_with_coverage() -> None:
    class MissingCount:
        @property
        def queue_count(self):
            raise AccessDenied()

        @property
        def queue_bytes(self):
            return 64

    clock_values = iter((20.0, 20.1))
    snapshot = capture_market_queue_snapshot(
        [MissingCount()],
        load_start_monotonic_seconds=10.0,
        monotonic_clock=lambda: next(clock_values),
        queue_read_errors=(AccessDenied,),
    )

    assert snapshot["client_count"] == 1
    assert snapshot["queue_count_coverage_count"] == 0
    assert snapshot["queue_count_sum"] is None
    assert snapshot["queue_count_max"] is None
    assert snapshot["queue_bytes_coverage_count"] == 1
    assert snapshot["queue_bytes_sum"] == 64


def test_parent_aggregates_latest_strategy_snapshots_and_marks_time_span() -> None:
    rows = [
        {
            "market_queue_snapshot": {
                "sample_started_monotonic_seconds": 10.0,
                "sample_completed_monotonic_seconds": 10.1,
                "client_count": 2,
                "queue_count_coverage_count": 2,
                "queue_count_sum": 3,
                "queue_count_max": 2,
                "queue_bytes_coverage_count": 2,
                "queue_bytes_sum": 30,
                "queue_bytes_max": 20,
            }
        },
        {
            "market_queue_snapshot": {
                "sample_started_monotonic_seconds": 11.0,
                "sample_completed_monotonic_seconds": 11.1,
                "client_count": 2,
                "queue_count_coverage_count": 2,
                "queue_count_sum": 8,
                "queue_count_max": 5,
                "queue_bytes_coverage_count": 2,
                "queue_bytes_sum": 80,
                "queue_bytes_max": 50,
            }
        },
    ]

    summary = aggregate_strategy_queue_snapshots(
        rows,
        expected_strategy_count=2,
        expected_clients_per_strategy=2,
    )

    assert summary["queue_count_coverage_count"] == 4
    assert summary["queue_count_sum"] == 11
    assert summary["queue_count_max"] == 5
    assert summary["queue_bytes_sum"] == 110
    assert summary["queue_bytes_max"] == 50
    assert summary["snapshot_time_span_seconds"] == pytest.approx(1.1)
    assert "not an exact simultaneous total" in summary["sampling_semantics"]


def test_parent_missing_strategy_snapshot_does_not_report_zero_queue() -> None:
    rows = [
        {
            "market_queue_snapshot": {
                "sample_started_monotonic_seconds": 10.0,
                "sample_completed_monotonic_seconds": 10.1,
                "client_count": 2,
                "queue_count_coverage_count": 2,
                "queue_count_sum": 3,
                "queue_count_max": 2,
                "queue_bytes_coverage_count": 2,
                "queue_bytes_sum": 30,
                "queue_bytes_max": 20,
            }
        },
        {},
    ]

    summary = aggregate_strategy_queue_snapshots(
        rows,
        expected_strategy_count=2,
        expected_clients_per_strategy=2,
    )

    assert summary["strategy_snapshot_count"] == 1
    assert summary["missing_strategy_snapshot_count"] == 1
    assert summary["queue_count_coverage_count"] == 2
    assert summary["expected_client_count"] == 4
    assert summary["queue_count_sum"] is None
    assert summary["queue_count_max"] is None
