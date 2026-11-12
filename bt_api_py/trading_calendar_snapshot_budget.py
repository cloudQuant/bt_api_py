"""Conservative work preflight for CTP calendar snapshot construction.

Limits cover combined six-venue construction, including closed dates. They
bound estimated structural work, not physical memory or elapsed wall time.
"""

from bisect import bisect_left, bisect_right

SNAPSHOT_VALUE_LIMIT = 150_000
SNAPSHOT_SCOPE_INPUT_LIMIT = 150_000
SNAPSHOT_WORK_LIMIT = 25_000_000


def check_snapshot_budget(content, venues):
    """Reject before constructing a snapshot that exceeds the work budget."""
    total_values = total_windows = total_work = total_scopes = max_windows = 0
    total_days = len(content["trading_days"])
    exception_count = len(content["exceptions"])
    max_exception_refs = max((len(row["source_ids"]) for row in content["exceptions"]), default=0)
    for venue in venues:
        days = [row for row in content["trading_days"] if row["venue"] == venue]
        dates = sorted(row["day"] for row in days)
        hours = [row for row in content["trading_hours"] if row["venue"] == venue]
        max_day_refs = max((len(row["source_ids"]) for row in days), default=0)
        scopes = set()
        values = windows = work = scope_inputs = 0
        for row in hours:
            count = bisect_right(dates, row["valid_to"]) - bisect_left(dates, row["valid_from"])
            row_values = count * len(row["contracts"])
            window_count = len(row["day_windows"]) + len(row["night_windows"])
            mapping_refs = max(
                (len(mapping["source_ids"]) for mapping in row["night_mappings"]), default=0
            )
            scope_inputs += len(row["contracts"])
            if scope_inputs > SNAPSHOT_SCOPE_INPUT_LIMIT:
                raise ValueError("snapshot scope budget")
            scopes.update((row["kind"], row["product"], item) for item in row["contracts"])
            values += row_values
            windows += row_values * window_count
            max_windows = max(max_windows, window_count)
            # Combined construction scans all venues' days per rule, including
            # closed dates; window comparisons and source sets are bounded too.
            work += (
                2 * total_days
                + len(row["contracts"])
                + row_values
                * (max_day_refs + len(row["source_ids"]) + mapping_refs + window_count**2)
            )
            if values > SNAPSHOT_VALUE_LIMIT or work > SNAPSHOT_WORK_LIMIT:
                raise ValueError("snapshot work budget")
        total_values += values
        total_windows += windows
        total_work += work
        total_scopes += len(scopes)
        if total_values > SNAPSHOT_VALUE_LIMIT or total_work > SNAPSHOT_WORK_LIMIT:
            raise ValueError("snapshot work budget")
    # CTP scans all combined values for each scope and all exceptions for
    # each value. Include filtering and conservative per-scope sorting work.
    total_work += total_scopes * total_values
    total_work += total_values * exception_count * (max_windows + max_exception_refs + 1)
    total_work += total_windows * (2 + max(1, (total_values * max_windows).bit_length()))
    if total_work > SNAPSHOT_WORK_LIMIT:
        raise ValueError("snapshot work budget")
