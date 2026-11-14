"""One-shot caller-declared simulation of consecutive missed calendar checks.

Inputs are synthetic declarations, not authenticated clocks, checks, exchange
days, coverage or trading authority. This module has no scheduler or journal.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta

from bt_api_py.trading_calendar_coverage_health import _local_date
from bt_api_py.trading_calendar_staleness import (
    MAX_DAYS,
    DailyObservation,
    EvidenceClaim,
    VenueHistory,
    evaluate_staleness,
)

VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
_MAX_INPUT_BYTES = 4 * 1024 * 1024
_CLAIM = EvidenceClaim("VERIFIED", "simulation:caller-declared-not-authenticated")
_LABELS = {
    "environment": "SIMULATION_ONLY",
    "input_authority": "CALLER_DECLARED_NOT_PROVEN",
    "clock_authentication": "NOT_PROVEN",
    "source_authentication": "NOT_PROVEN",
    "real_health": "UNKNOWN",
    "trading_authorization": "NONE",
    "max_count_none": "THRESHOLD_OR_MORE_OR_UNKNOWN",
}


def _unknown(venue, threshold):
    return {
        "venue": venue,
        "simulation_status": "UNKNOWN",
        "reason": "DECLARATION_UNAVAILABLE",
        "min_failed_trading_days": None,
        "max_failed_trading_days": None,
        "threshold": threshold,
    }


def report_staleness(observations, *, mode, clock_instant, clock_error_seconds, threshold=2):
    """Compute six separate conditional simulations from daily caller assertions.

    Each supplied venue has ``scope_id`` and ``days``. Each day has ISO ``day``,
    ``day_status`` (OPEN/CLOSED/UNKNOWN), ``check_status``
    (SUCCESS/NO_SUCCESS/UNKNOWN), and ``deadline_elapsed`` (bool or null).
    The caller must not modify input during this call. A synthetic VERIFIED flag
    enables arithmetic only; it cannot establish the truth of the declarations.
    """
    try:
        if (
            type(mode) is not str
            or mode != "SIMULATION_ONLY"
            or type(observations) is not dict
            or any(type(key) is not str or key not in VENUES for key in observations)
            or type(threshold) is not int
            or not 1 <= threshold <= MAX_DAYS
            or type(clock_error_seconds) not in (int, float)
            or not math.isfinite(clock_error_seconds)
            or not 0 <= clock_error_seconds <= 86400
            or type(clock_instant) is not datetime
            or clock_instant.tzinfo is None
            or clock_instant.utcoffset() != timedelta(0)
        ):
            raise ValueError("input")
        bound = timedelta(microseconds=math.ceil(clock_error_seconds * 1_000_000))
        today, error = _local_date(clock_instant, bound)
        if error is not None or today is None:
            raise ValueError("clock")
        results = []
        for venue in VENUES:
            try:
                item = observations[venue]
                if (
                    type(item) is not dict
                    or set(item) != {"scope_id", "days"}
                    or type(item["scope_id"]) is not str
                    or not item["scope_id"].strip()
                    or len(item["scope_id"]) > 256
                    or type(item["days"]) is not list
                    or not 1 <= len(item["days"]) <= MAX_DAYS
                ):
                    raise ValueError("scope")
                days = []
                for row in item["days"]:
                    if (
                        type(row) is not dict
                        or set(row) != {"day", "day_status", "check_status", "deadline_elapsed"}
                        or type(row["day"]) is not str
                        or type(row["day_status"]) is not str
                        or row["day_status"] not in ("OPEN", "CLOSED")
                        or type(row["check_status"]) is not str
                        or row["check_status"] not in ("SUCCESS", "NO_SUCCESS")
                        or type(row["deadline_elapsed"]) is not bool
                        or (row["check_status"] == "NO_SUCCESS" and not row["deadline_elapsed"])
                    ):
                        raise ValueError("daily declaration")
                    day = date.fromisoformat(row["day"])
                    if day.isoformat() != row["day"]:
                        raise ValueError("date")
                    days.append(
                        DailyObservation(
                            day,
                            "TRADING" if row["day_status"] == "OPEN" else "CLOSED",
                            row["check_status"],
                            _CLAIM,
                            _CLAIM,
                            _CLAIM if row["deadline_elapsed"] else None,
                        )
                    )
                result = evaluate_staleness(
                    (VenueHistory(venue, item["scope_id"], _CLAIM, tuple(days)),),
                    as_of_date=today,
                    clock_claim=_CLAIM,
                    threshold=threshold,
                )[0]
                results.append(
                    {
                        "venue": venue,
                        "simulation_status": result.status,
                        "reason": result.reasons[0],
                        "min_failed_trading_days": result.min_failed_trading_days,
                        "max_failed_trading_days": result.max_failed_trading_days,
                        "threshold": threshold,
                    }
                )
            except Exception:
                results.append(_unknown(venue, threshold))
        states = {row["simulation_status"] for row in results}
        code = 2 if "UNKNOWN" in states else 1 if "WARNING" in states else 0
        return {
            **_LABELS,
            "status": "UNKNOWN" if code == 2 else "WARNING" if code == 1 else "OK",
            "shanghai_date": today.isoformat(),
            "venues": results,
            "exit_code": code,
        }
    except Exception:
        return {
            **_LABELS,
            "status": "UNKNOWN",
            "shanghai_date": None,
            "venues": [
                _unknown(venue, threshold if type(threshold) is int else None) for venue in VENUES
            ],
            "exit_code": 2,
        }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def main(argv=None):
    parser = _Parser(description="One-shot fictional calendar staleness report; JSON on stdin")
    parser.add_argument("--mode", required=True)
    parser.add_argument("--clock-error-seconds", required=True, type=float)
    parser.add_argument("--threshold", type=int, default=2)
    try:
        args = parser.parse_args(argv)
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("input budget")
        value = json.loads(raw, object_pairs_hook=_unique_pairs)
        result = report_staleness(
            value,
            mode=args.mode,
            clock_instant=datetime.now(UTC),
            clock_error_seconds=args.clock_error_seconds,
            threshold=args.threshold,
        )
    except Exception:
        result = report_staleness(None, mode=None, clock_instant=None, clock_error_seconds=None)
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
