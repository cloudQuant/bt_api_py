"""Fail-closed checks for the versioned pytest-benchmark baseline."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING

import pytest

from scripts.ci.check_performance_baseline import (
    PerformanceBaselineError,
    main,
    validate_performance_baseline,
)

if TYPE_CHECKING:
    from pathlib import Path

BENCHMARK_FULLNAME = (
    "tests/performance/test_event_normalization_performance.py::"
    "test_normalize_event_orderbook_dict_hot_path"
)


def _valid_documents() -> tuple[dict[str, object], dict[str, object]]:
    baseline = {
        "schema_version": 1,
        "benchmarks": {BENCHMARK_FULLNAME: {"max_mean_seconds": 0.0005, "min_rounds": 1000}},
    }
    report = {
        "benchmarks": [
            {
                "fullname": BENCHMARK_FULLNAME,
                "stats": {"mean": 0.00003, "rounds": 2400, "iterations": 1},
            }
        ]
    }
    return baseline, report


def _write_documents(
    tmp_path: Path,
    baseline: dict[str, object] | None = None,
    report: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    default_baseline, default_report = _valid_documents()
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "benchmark.json"
    baseline_path.write_text(
        json.dumps(deepcopy(default_baseline if baseline is None else baseline)),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(deepcopy(default_report if report is None else report)),
        encoding="utf-8",
    )
    return baseline_path, report_path


def test_valid_singleton_baseline_passes(tmp_path: Path) -> None:
    baseline_path, report_path = _write_documents(tmp_path)

    results = validate_performance_baseline(baseline_path, report_path)

    assert results == [f"{BENCHMARK_FULLNAME}: mean=3e-05s (max 0.0005s), rounds=2400 (min 1000)"]


@pytest.mark.parametrize("missing", ["baseline", "report"])
def test_missing_file_fails_closed(tmp_path: Path, missing: str) -> None:
    baseline_path, report_path = _write_documents(tmp_path)
    (baseline_path if missing == "baseline" else report_path).unlink()

    with pytest.raises(PerformanceBaselineError, match="missing or not a file"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("empty", ["baseline", "report"])
def test_empty_file_fails_closed(tmp_path: Path, empty: str) -> None:
    baseline_path, report_path = _write_documents(tmp_path)
    (baseline_path if empty == "baseline" else report_path).write_text("", encoding="utf-8")

    with pytest.raises(PerformanceBaselineError, match="is empty"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("invalid", ["baseline", "report"])
def test_invalid_json_fails_closed(tmp_path: Path, invalid: str) -> None:
    baseline_path, report_path = _write_documents(tmp_path)
    (baseline_path if invalid == "baseline" else report_path).write_text(
        "{invalid", encoding="utf-8"
    )

    with pytest.raises(PerformanceBaselineError, match="invalid JSON"):
        validate_performance_baseline(baseline_path, report_path)


def test_baseline_must_contain_a_nonempty_benchmarks_object(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    baseline["benchmarks"] = {}
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(
        PerformanceBaselineError, match="baseline benchmarks must be a non-empty object"
    ):
        validate_performance_baseline(baseline_path, report_path)


def test_report_must_contain_a_nonempty_benchmarks_list(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"] = []
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="benchmarks must be a non-empty list"):
        validate_performance_baseline(baseline_path, report_path)


def test_missing_required_benchmark_fails_closed(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"] = [
        {
            "fullname": "tests/performance/test_missing.py::test_missing",
            "stats": {"mean": 0.00003, "rounds": 2400, "iterations": 1},
        }
    ]
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="name set does not match"):
        validate_performance_baseline(baseline_path, report_path)


def test_duplicate_required_benchmark_fails_closed(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    benchmark_rows = report["benchmarks"]
    if not isinstance(benchmark_rows, list):
        raise AssertionError("test fixture must include benchmark rows")
    benchmark_rows.append(deepcopy(benchmark_rows[0]))
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="duplicate benchmark report entry"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("raw_mean", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_nonfinite_mean_fails_closed(tmp_path: Path, raw_mean: str) -> None:
    baseline_path = tmp_path / "baseline.json"
    report_path = tmp_path / "benchmark.json"
    baseline, _ = _valid_documents()
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    report_path.write_text(
        '{"benchmarks":[{"fullname":"'
        + BENCHMARK_FULLNAME
        + '","stats":{"mean":'
        + raw_mean
        + ',"rounds":2400,"iterations":1}}]}',
        encoding="utf-8",
    )

    with pytest.raises(PerformanceBaselineError, match="non-standard JSON number|mean.*finite"):
        validate_performance_baseline(baseline_path, report_path)


def test_mean_above_absolute_limit_fails_closed(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"][0]["stats"]["mean"] = 0.0005001
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="maximum is"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("rounds", [0, -1, 999, True, "2400"])
def test_invalid_or_insufficient_round_count_fails_closed(
    tmp_path: Path,
    rounds: int | str | bool,
) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"][0]["stats"]["rounds"] = rounds
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="rounds"):
        validate_performance_baseline(baseline_path, report_path)


def test_unexpected_benchmark_name_fails_closed(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"][0]["fullname"] = "tests/performance/test_added.py::test_new"
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="name set does not match"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize(
    ("baseline", "message"),
    [
        ({"schema_version": True, "benchmarks": {}}, "schema_version"),
        ({"schema_version": 2, "benchmarks": {}}, "schema_version"),
        ({"schema_version": 1, "benchmarks": []}, "non-empty object"),
        (
            {
                "schema_version": 1,
                "benchmarks": {BENCHMARK_FULLNAME: {"max_mean_seconds": True, "min_rounds": 1000}},
            },
            "max_mean_seconds",
        ),
        (
            {
                "schema_version": 1,
                "benchmarks": {
                    BENCHMARK_FULLNAME: {"max_mean_seconds": 0.0005, "min_rounds": True}
                },
            },
            "min_rounds",
        ),
    ],
)
def test_invalid_baseline_schema_or_types_fail_closed(
    tmp_path: Path,
    baseline: dict[str, object],
    message: str,
) -> None:
    _, report = _valid_documents()
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match=message):
        validate_performance_baseline(baseline_path, report_path)


def test_json_duplicate_keys_fail_closed(tmp_path: Path) -> None:
    _, report = _valid_documents()
    baseline_path, report_path = _write_documents(tmp_path, None, report)
    baseline_path.write_text(
        '{"schema_version":1,"schema_version":1,"benchmarks":{}}', encoding="utf-8"
    )

    with pytest.raises(PerformanceBaselineError, match="duplicate JSON object key"):
        validate_performance_baseline(baseline_path, report_path)


def test_duplicate_report_json_keys_fail_closed(tmp_path: Path) -> None:
    baseline, _ = _valid_documents()
    baseline_path, report_path = _write_documents(tmp_path, baseline, None)
    report_path.write_text('{"benchmarks":[],"benchmarks":[]}', encoding="utf-8")

    with pytest.raises(PerformanceBaselineError, match="duplicate JSON object key"):
        validate_performance_baseline(baseline_path, report_path)


def test_baseline_rejects_multiple_benchmarks_in_schema_version_one(tmp_path: Path) -> None:
    baseline, report = _valid_documents()
    baseline["benchmarks"] = {
        BENCHMARK_FULLNAME: {"max_mean_seconds": 0.0005, "min_rounds": 1000},
        "tests/performance/test_added.py::test_added": {
            "max_mean_seconds": 0.0005,
            "min_rounds": 1000,
        },
    }
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="exactly one benchmark baseline"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("raw_limit", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_baseline_rejects_nonfinite_limit(tmp_path: Path, raw_limit: str) -> None:
    _, report = _valid_documents()
    baseline_path, report_path = _write_documents(tmp_path, None, report)
    baseline_path.write_text(
        '{"schema_version":1,"benchmarks":{"'
        + BENCHMARK_FULLNAME
        + '":{"max_mean_seconds":'
        + raw_limit
        + ',"min_rounds":1000}}}',
        encoding="utf-8",
    )

    with pytest.raises(PerformanceBaselineError, match="non-standard JSON number|max_mean_seconds"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize(
    ("stats", "message"),
    [
        (None, "stats.*object"),
        ({"mean": 0.00003, "rounds": 2400}, "iterations"),
        ({"mean": 0.00003, "rounds": 2400, "iterations": 0}, "iterations"),
        ({"mean": 0.00003, "rounds": 2400, "iterations": True}, "iterations"),
    ],
)
def test_missing_or_malformed_stats_fields_fail_closed(
    tmp_path: Path,
    stats: object,
    message: str,
) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"] = [{"fullname": BENCHMARK_FULLNAME, "stats": stats}]
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match=message):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("mean", [0, -0.1, True, "0.00003"])
def test_invalid_mean_type_or_sign_fails_closed(tmp_path: Path, mean: object) -> None:
    baseline, report = _valid_documents()
    report["benchmarks"] = [
        {
            "fullname": BENCHMARK_FULLNAME,
            "stats": {"mean": mean, "rounds": 2400, "iterations": 1},
        }
    ]
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="mean.*finite and positive"):
        validate_performance_baseline(baseline_path, report_path)


@pytest.mark.parametrize("maximum", [0, -0.0001, True, "0.0005", "NaN"])
def test_invalid_absolute_limit_fails_closed(tmp_path: Path, maximum: object) -> None:
    baseline, report = _valid_documents()
    baseline["benchmarks"] = {BENCHMARK_FULLNAME: {"max_mean_seconds": maximum, "min_rounds": 1000}}
    baseline_path, report_path = _write_documents(tmp_path, baseline, report)

    with pytest.raises(PerformanceBaselineError, match="max_mean_seconds"):
        validate_performance_baseline(baseline_path, report_path)


def test_cli_reports_success_and_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_path, report_path = _write_documents(tmp_path)

    assert main(["--baseline", str(baseline_path), "--report", str(report_path)]) == 0
    assert "PASS performance baseline" in capsys.readouterr().out

    report_path.write_text("{}", encoding="utf-8")
    assert main(["--baseline", str(baseline_path), "--report", str(report_path)]) == 1
    assert "FAIL performance baseline" in capsys.readouterr().err
