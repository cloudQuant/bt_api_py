"""One-shot local simulation declarations; not a real health or trading alert."""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bt_api_py.trading_calendar_coverage_health import EvidenceClaim
from bt_api_py.trading_calendar_coverage_status import read_coverage_status
from bt_api_py.trading_calendar_store import CalendarReadBudget, TradingCalendarStore

_MAX_ERROR_SECONDS = 86400
_READ_BUDGET = CalendarReadBudget(8 * 1024 * 1024, 64 * 1024 * 1024, 2048)


class _BoundedCurrent:
    """Adapt the non-creating, nonblocking, budgeted store read to coverage status."""

    def __init__(self, root):
        self._store = TradingCalendarStore(root)

    def read_current(self):
        return self._store.read_current_with_consistent_receipts(budget=_READ_BUDGET).snapshot


def _error():
    return {
        "environment": "SIMULATION_ONLY",
        "scope": "SIMULATION_COVERAGE_DECLARATIONS_ONLY",
        "real_health": "UNKNOWN",
        "source_authentication": "NOT_PROVEN",
        "completeness": "NOT_PROVEN",
        "clock_authentication": "NOT_PROVEN",
        "trading_authorization": "NONE",
        "read_limits": {
            "max_file_bytes": _READ_BUDGET.max_file_bytes,
            "max_total_bytes": _READ_BUDGET.max_total_bytes,
            "max_publications": _READ_BUDGET.max_publications,
            "wall_clock_timeout": "NOT_GUARANTEED",
            "limit_exceeded": "UNKNOWN",
        },
        "status": "UNKNOWN",
        "reason": "INPUT_OR_CLOCK_UNAVAILABLE",
        "venues": [],
        "exit_code": 2,
    }


def report_coverage(root, *, mode, clock_error_seconds, threshold_days=30, _clock=None):
    """Return a one-shot simulation report without authenticating any inputs.

    ``_clock`` is a test seam; the regular clock is sampled once. The internal
    VERIFIED claim enables the existing conditional simulation arithmetic only:
    it is a caller assumption, not an independent clock verification.
    """
    try:
        if (
            type(root) is not str
            or not Path(root).is_absolute()
            or ".." in Path(root).parts
            or type(mode) is not str
            or mode != "SIMULATION_ONLY"
            or type(clock_error_seconds) not in (int, float)
            or not math.isfinite(clock_error_seconds)
            or not 0 <= clock_error_seconds <= _MAX_ERROR_SECONDS
            or type(threshold_days) is not int
            or threshold_days < 0
        ):
            return _error()
        # Round upward, never claiming a smaller bound than supplied.
        error = timedelta(microseconds=math.ceil(clock_error_seconds * 1_000_000))
        instant = datetime.now(UTC) if _clock is None else _clock()
        if (
            type(instant) is not datetime
            or instant.tzinfo is None
            or instant.utcoffset() != timedelta(0)
        ):
            return _error()
        result = read_coverage_status(
            _BoundedCurrent(root),
            mode=mode,
            clock_instant=instant,
            clock_claim=EvidenceClaim("VERIFIED", "simulation:caller-declared-not-authenticated"),
            clock_error_bound=error,
            threshold_days=threshold_days,
        )
        rows = []
        for item in result.current:
            health = item.health
            rows.append(
                {
                    "venue": health.venue,
                    "version": health.version_id,
                    "coverage_start": item.coverage_start.isoformat()
                    if item.coverage_start is not None
                    else None,
                    "coverage_end": health.coverage_end.isoformat()
                    if health.coverage_end is not None
                    else None,
                    "shanghai_date": health.shanghai_date.isoformat()
                    if health.shanghai_date is not None
                    else None,
                    "simulation_days_remaining": item.simulation_days_remaining,
                    "simulation_status": item.simulation_status,
                    "declaration": item.declaration,
                    "real_health": "UNKNOWN",
                    "source_authentication": "NOT_PROVEN",
                    "completeness": "NOT_PROVEN",
                }
            )
        states = {row["simulation_status"] for row in rows}
        code = 2 if not rows or states - {"OK", "WARNING"} else 1 if "WARNING" in states else 0
        return {
            **_error(),
            "status": "UNKNOWN" if code == 2 else "WARNING" if code == 1 else "OK",
            "reason": "SIMULATION_DECLARATION_ARITHMETIC_ONLY",
            "clock_instant_utc": instant.isoformat(),
            "clock_error_seconds": error.total_seconds(),
            "threshold_days": threshold_days,
            "venues": rows,
            "exit_code": code,
        }
    except Exception:
        return _error()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--clock-error-seconds", required=True, type=float)
    parser.add_argument("--threshold-days", type=int, default=30)
    try:
        args = parser.parse_args(argv)
        result = report_coverage(
            args.root,
            mode=args.mode,
            clock_error_seconds=args.clock_error_seconds,
            threshold_days=args.threshold_days,
        )
    except Exception:
        result = _error()
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
