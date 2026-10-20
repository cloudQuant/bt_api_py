"""Pure aggregation helpers for LOAD01's bounded resource diagnostics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def capture_process_resource_row(
    role: str,
    pid: int,
    process_factory: Any,
    *,
    process_gone_errors: tuple[type[BaseException], ...],
    access_denied_errors: tuple[type[BaseException], ...],
) -> tuple[dict[str, Any], Any | None]:
    """Read one process's memory_info once, plus independent thread metadata.

    ``memory_info().private`` is used when the platform provides it. This helper
    deliberately never calls ``memory_full_info`` or substitutes RSS for private
    bytes. The returned process object lets the caller reuse this same psutil
    Process instance for its CPU-time sample.
    """
    row: dict[str, Any] = {
        "role": role,
        "pid": int(pid),
        "rss_bytes": None,
        "private_bytes": None,
        "thread_count": None,
        "process_state": "unavailable",
        "memory_state": "unavailable",
    }
    try:
        process = process_factory(pid)
    except process_gone_errors:
        row["process_state"] = "missing"
        row["memory_state"] = "process_missing"
        return row, None
    except access_denied_errors:
        return row, None

    row["process_state"] = "available"
    try:
        memory = process.memory_info()
    except process_gone_errors:
        row["process_state"] = "missing"
        row["memory_state"] = "process_missing"
        return row, process
    except access_denied_errors:
        row["memory_state"] = "access_denied"
    else:
        row["rss_bytes"] = _nonnegative_int(getattr(memory, "rss", None))
        row["private_bytes"] = _nonnegative_int(getattr(memory, "private", None))
        row["memory_state"] = "available" if row["rss_bytes"] is not None else "unavailable"

    try:
        thread_count = process.num_threads()
    except process_gone_errors:
        row["process_state"] = "missing"
    except access_denied_errors:
        pass
    else:
        row["thread_count"] = _nonnegative_int(thread_count)
    return row, process


def aggregate_role_resources(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate complete per-role values and expose partial coverage explicitly."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("role", "unknown"))].append(row)

    result: dict[str, Any] = {}
    for role in ("strategy", "owner", "market", "monitor", *sorted(grouped)):
        if role in result:
            continue
        role_rows = grouped.get(role, [])
        pid_count = len(role_rows)
        rss_values = [value for row in role_rows if (value := _nonnegative_int(row.get("rss_bytes"))) is not None]
        private_values = [value for row in role_rows if (value := _nonnegative_int(row.get("private_bytes"))) is not None]
        thread_values = [value for row in role_rows if (value := _nonnegative_int(row.get("thread_count"))) is not None]
        missing_pids = [
            int(row["pid"])
            for row in role_rows
            if row.get("process_state") == "missing" and isinstance(row.get("pid"), int)
        ]
        result[role] = {
            "child_pid_count": pid_count,
            "rss_coverage_count": len(rss_values),
            "rss_bytes_sum": sum(rss_values) if pid_count and len(rss_values) == pid_count else None,
            "rss_bytes_max": max(rss_values) if pid_count and len(rss_values) == pid_count else None,
            "private_bytes_coverage_count": len(private_values),
            "private_bytes_sum": (
                sum(private_values) if pid_count and len(private_values) == pid_count else None
            ),
            "private_bytes_max": (
                max(private_values) if pid_count and len(private_values) == pid_count else None
            ),
            "thread_count_coverage_count": len(thread_values),
            "thread_count_sum": (
                sum(thread_values) if pid_count and len(thread_values) == pid_count else None
            ),
            "missing_pids": missing_pids,
        }
    return result


def capture_market_queue_snapshot(
    clients: Iterable[Any],
    *,
    load_start_monotonic_seconds: float,
    monotonic_clock: Any,
    queue_read_errors: tuple[type[BaseException], ...] = (Exception,),
) -> dict[str, Any]:
    """Capture current per-strategy queue totals in one report-time pass.

    Missing or invalid queue values stay null and reduce coverage; they are never
    represented as an empty queue. The caller invokes this only when publishing
    strategy progress or the final strategy snapshot, not in the event loop.
    """
    clients = tuple(clients)
    started = float(monotonic_clock())
    event_values: list[int] = []
    byte_values: list[int] = []
    for client in clients:
        try:
            events = getattr(client, "queue_count")
        except queue_read_errors:
            events = None
        try:
            queued_bytes = getattr(client, "queue_bytes")
        except queue_read_errors:
            queued_bytes = None
        event_value = _nonnegative_int(events)
        byte_value = _nonnegative_int(queued_bytes)
        if event_value is not None:
            event_values.append(event_value)
        if byte_value is not None:
            byte_values.append(byte_value)
    completed = float(monotonic_clock())
    client_count = len(clients)
    event_complete = client_count > 0 and len(event_values) == client_count
    bytes_complete = client_count > 0 and len(byte_values) == client_count
    return {
        "sample_started_monotonic_seconds": started,
        "sample_completed_monotonic_seconds": completed,
        "sample_elapsed_since_load_start_seconds": max(
            0.0, completed - load_start_monotonic_seconds
        ),
        "client_count": client_count,
        "queue_count_coverage_count": len(event_values),
        "queue_count_sum": sum(event_values) if event_complete else None,
        "queue_count_max": max(event_values) if event_complete else None,
        "queue_bytes_coverage_count": len(byte_values),
        "queue_bytes_sum": sum(byte_values) if bytes_complete else None,
        "queue_bytes_max": max(byte_values) if bytes_complete else None,
    }


def aggregate_strategy_queue_snapshots(
    strategy_rows: Iterable[Mapping[str, Any]],
    *,
    expected_strategy_count: int,
    expected_clients_per_strategy: int | None = None,
    expected_clients_by_strategy: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    """Aggregate latest strategy snapshots with explicit coverage and time span.

    Each strategy snapshot walks its clients sequentially, and strategy workers
    publish at different times. The aggregate is therefore not an exact same-time
    system total.
    """
    rows = tuple(strategy_rows)
    row_snapshots = [
        (row, snapshot)
        for row in rows
        if isinstance((snapshot := row.get("market_queue_snapshot")), Mapping)
    ]
    snapshots = [snapshot for _row, snapshot in row_snapshots]
    expected_strategy_count = max(0, int(expected_strategy_count))
    if expected_clients_by_strategy is None:
        if expected_clients_per_strategy is None:
            raise ValueError(
                "expected_clients_per_strategy or expected_clients_by_strategy is required"
            )
        expected_clients_per_strategy = max(0, int(expected_clients_per_strategy))
        normalized_clients_by_strategy = None
        expected_client_count = expected_strategy_count * expected_clients_per_strategy
    else:
        if set(expected_clients_by_strategy) != set(range(expected_strategy_count)):
            raise ValueError("expected client-count mapping must cover every strategy index")
        normalized_clients_by_strategy = {}
        for strategy_index, client_count in expected_clients_by_strategy.items():
            if type(strategy_index) is not int or type(client_count) is not int or client_count < 0:
                raise ValueError("expected client-count mapping must use nonnegative integer values")
            normalized_clients_by_strategy[strategy_index] = client_count
        expected_client_count = sum(normalized_clients_by_strategy.values())
    event_coverage = sum(int(snapshot.get("queue_count_coverage_count", 0)) for snapshot in snapshots)
    byte_coverage = sum(int(snapshot.get("queue_bytes_coverage_count", 0)) for snapshot in snapshots)
    event_sums = [snapshot.get("queue_count_sum") for snapshot in snapshots]
    event_maxima = [snapshot.get("queue_count_max") for snapshot in snapshots]
    byte_sums = [snapshot.get("queue_bytes_sum") for snapshot in snapshots]
    byte_maxima = [snapshot.get("queue_bytes_max") for snapshot in snapshots]
    if normalized_clients_by_strategy is None:
        complete_strategy_snapshots = (
            len(rows) == expected_strategy_count and len(snapshots) == expected_strategy_count
            and all(
                int(snapshot.get("client_count", 0)) == expected_clients_per_strategy
                for snapshot in snapshots
            )
        )
        reported_expected_clients_per_strategy = expected_clients_per_strategy
    else:
        strategy_indices = [row.get("index") for row, _snapshot in row_snapshots]
        complete_strategy_snapshots = (
            len(rows) == expected_strategy_count
            and len(snapshots) == expected_strategy_count
            and len(strategy_indices) == expected_strategy_count
            and all(type(index) is int for index in strategy_indices)
            and set(strategy_indices) == set(normalized_clients_by_strategy)
            and all(
                int(snapshot.get("client_count", 0))
                == normalized_clients_by_strategy[row["index"]]
                for row, snapshot in row_snapshots
                if type(row.get("index")) is int
                and row["index"] in normalized_clients_by_strategy
            )
        )
        reported_expected_clients_per_strategy = (
            next(iter(set(normalized_clients_by_strategy.values())))
            if len(set(normalized_clients_by_strategy.values())) == 1
            else None
        )
    events_complete = complete_strategy_snapshots and event_coverage == expected_client_count and all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (*event_sums, *event_maxima)
    )
    bytes_complete = complete_strategy_snapshots and byte_coverage == expected_client_count and all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (*byte_sums, *byte_maxima)
    )
    sample_starts = [
        float(snapshot["sample_started_monotonic_seconds"])
        for snapshot in snapshots
        if isinstance(snapshot.get("sample_started_monotonic_seconds"), (int, float))
    ]
    sample_ends = [
        float(snapshot["sample_completed_monotonic_seconds"])
        for snapshot in snapshots
        if isinstance(snapshot.get("sample_completed_monotonic_seconds"), (int, float))
    ]
    return {
        "sampling_semantics": (
            "sum of each strategy's most recent sequential client snapshot; "
            "strategy snapshots occur at different times and are not an exact simultaneous total"
        ),
        "expected_strategy_count": expected_strategy_count,
        "expected_clients_per_strategy": reported_expected_clients_per_strategy,
        "expected_clients_by_strategy": (
            {str(index): count for index, count in normalized_clients_by_strategy.items()}
            if normalized_clients_by_strategy is not None
            else None
        ),
        "strategy_snapshot_count": len(snapshots),
        "missing_strategy_snapshot_count": max(0, expected_strategy_count - len(snapshots)),
        "unexpected_strategy_snapshot_count": max(0, len(snapshots) - expected_strategy_count),
        "expected_client_count": expected_client_count,
        "queue_count_coverage_count": event_coverage,
        "queue_count_sum": sum(event_sums) if events_complete else None,
        "queue_count_max": max(event_maxima) if events_complete and event_maxima else None,
        "queue_bytes_coverage_count": byte_coverage,
        "queue_bytes_sum": sum(byte_sums) if bytes_complete else None,
        "queue_bytes_max": max(byte_maxima) if bytes_complete and byte_maxima else None,
        "sample_started_monotonic_min_seconds": min(sample_starts) if sample_starts else None,
        "sample_completed_monotonic_max_seconds": max(sample_ends) if sample_ends else None,
        "snapshot_time_coverage_count": min(len(sample_starts), len(sample_ends)),
        "snapshot_time_span_seconds": (
            max(sample_ends) - min(sample_starts) if sample_starts and sample_ends else None
        ),
    }


def assert_load01_children_alive(records: Mapping[str, Mapping[str, Any]]) -> None:
    """Preserve the existing sustained-load child-exit failure gate."""
    for record in records.values():
        process = record["process"]
        if not process.is_alive():
            raise AssertionError(f"LOAD01 process exited during load: {record['name']}")


def append_and_validate_load01_resource_sample(
    report: dict[str, Any],
    sample: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
) -> None:
    """Persist a diagnostic sample before applying fail-closed child checks."""
    report["resource_samples"].append(dict(sample))
    assert_load01_children_alive(records)
    expected = len(records)
    coverage_fields = (
        "child_pid_coverage_count",
        "child_process_coverage_count",
        "child_rss_coverage_count",
        "child_cpu_time_coverage_count",
    )
    incomplete = [
        f"{field}={sample.get(field)!r}/{expected}"
        for field in coverage_fields
        if sample.get(field) != expected
    ]
    if sample.get("child_pid_count") != expected:
        incomplete.append(f"child_pid_count={sample.get('child_pid_count')!r}/{expected}")
    if incomplete:
        raise RuntimeError(
            "LOAD01 child resource sample incomplete: " + ", ".join(incomplete)
        )
