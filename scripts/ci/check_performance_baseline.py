"""Validate pytest-benchmark output against the versioned local baseline."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


class PerformanceBaselineError(ValueError):
    """Raised when baseline or benchmark evidence is incomplete or invalid."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PerformanceBaselineError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise PerformanceBaselineError(f"non-standard JSON number: {value}")


def _load_json(path: Path, label: str) -> object:
    try:
        if not path.is_file():
            raise PerformanceBaselineError(f"{label} is missing or not a file: {path}")
        if path.stat().st_size == 0:
            raise PerformanceBaselineError(f"{label} is empty: {path}")
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PerformanceBaselineError(f"cannot read {label}: {exc}") from exc
    except UnicodeError as exc:
        raise PerformanceBaselineError(f"{label} is not valid UTF-8: {path}") from exc

    try:
        return json.loads(
            content,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonstandard_number,
        )
    except PerformanceBaselineError:
        raise
    except json.JSONDecodeError as exc:
        raise PerformanceBaselineError(f"{label} contains invalid JSON: {exc}") from exc
    except ValueError as exc:
        raise PerformanceBaselineError(f"{label} contains invalid JSON: {exc}") from exc


def _is_finite_positive_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def _load_baselines(path: Path) -> dict[str, tuple[float, int]]:
    data = _load_json(path, "baseline")
    if not isinstance(data, dict):
        raise PerformanceBaselineError("baseline top level must be an object")
    if set(data) != {"schema_version", "benchmarks"}:
        raise PerformanceBaselineError(
            "baseline fields must be exactly schema_version and benchmarks"
        )
    version = data["schema_version"]
    if type(version) is not int or version != 1:
        raise PerformanceBaselineError("baseline schema_version must be integer 1")

    raw_benchmarks = data["benchmarks"]
    if not isinstance(raw_benchmarks, dict) or not raw_benchmarks:
        raise PerformanceBaselineError("baseline benchmarks must be a non-empty object")
    if len(raw_benchmarks) != 1:
        raise PerformanceBaselineError("schema version 1 requires exactly one benchmark baseline")

    baselines: dict[str, tuple[float, int]] = {}
    for fullname, config in raw_benchmarks.items():
        if not isinstance(fullname, str) or not fullname.strip():
            raise PerformanceBaselineError("baseline benchmark names must be non-empty strings")
        if not isinstance(config, dict):
            raise PerformanceBaselineError(f"baseline for {fullname!r} must be an object")
        if set(config) != {"max_mean_seconds", "min_rounds"}:
            raise PerformanceBaselineError(
                f"baseline for {fullname!r} must define max_mean_seconds and min_rounds"
            )

        maximum = config["max_mean_seconds"]
        if not _is_finite_positive_number(maximum):
            raise PerformanceBaselineError(
                f"baseline max_mean_seconds for {fullname!r} must be finite and positive"
            )
        min_rounds = config["min_rounds"]
        if type(min_rounds) is not int or min_rounds <= 0:
            raise PerformanceBaselineError(
                f"baseline min_rounds for {fullname!r} must be a positive integer"
            )
        baselines[fullname] = (float(maximum), min_rounds)
    return baselines


def validate_performance_baseline(baseline_path: Path, report_path: Path) -> list[str]:
    """Return one concise success detail per required benchmark or fail closed."""
    baselines = _load_baselines(baseline_path)
    report = _load_json(report_path, "benchmark report")
    if not isinstance(report, dict):
        raise PerformanceBaselineError("benchmark report top level must be an object")

    entries = report.get("benchmarks")
    if not isinstance(entries, list) or not entries:
        raise PerformanceBaselineError("benchmark report benchmarks must be a non-empty list")

    reports_by_name: dict[str, dict[str, object]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PerformanceBaselineError(f"benchmark entry {index} must be an object")
        fullname = entry.get("fullname")
        if not isinstance(fullname, str) or not fullname.strip():
            raise PerformanceBaselineError(
                f"benchmark entry {index} must have a non-empty fullname"
            )
        if fullname in reports_by_name:
            raise PerformanceBaselineError(f"duplicate benchmark report entry: {fullname}")
        reports_by_name[fullname] = entry

    expected_names = set(baselines)
    actual_names = set(reports_by_name)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        raise PerformanceBaselineError(
            "benchmark name set does not match the singleton baseline (" + ", ".join(details) + ")"
        )

    results: list[str] = []
    for fullname, (maximum, min_rounds) in baselines.items():
        entry = reports_by_name[fullname]
        stats = entry.get("stats")
        if not isinstance(stats, dict):
            raise PerformanceBaselineError(f"benchmark stats for {fullname!r} must be an object")

        iterations = stats.get("iterations")
        if type(iterations) is not int or iterations <= 0:
            raise PerformanceBaselineError(
                f"benchmark iterations for {fullname!r} must be a positive integer"
            )
        mean = stats.get("mean")
        if not _is_finite_positive_number(mean):
            raise PerformanceBaselineError(
                f"benchmark mean for {fullname!r} must be finite and positive"
            )
        rounds = stats.get("rounds")
        if type(rounds) is not int or rounds <= 0:
            raise PerformanceBaselineError(
                f"benchmark rounds for {fullname!r} must be a positive integer"
            )
        if rounds < min_rounds:
            raise PerformanceBaselineError(
                f"benchmark rounds for {fullname!r} are {rounds}; minimum is {min_rounds}"
            )
        if mean > maximum:
            raise PerformanceBaselineError(
                f"benchmark mean for {fullname!r} is {mean:.9g}s; maximum is {maximum:.9g}s"
            )
        results.append(
            f"{fullname}: mean={mean:.9g}s (max {maximum:.9g}s), rounds={rounds} (min {min_rounds})"
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        results = validate_performance_baseline(args.baseline, args.report)
    except PerformanceBaselineError as exc:
        print(f"FAIL performance baseline: {exc}", file=sys.stderr)
        return 1

    for result in results:
        print(f"PASS performance baseline: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
