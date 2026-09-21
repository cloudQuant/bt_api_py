"""Offline behavior contracts for the slow-test report parser."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = (
    ROOT / "scripts" / "analyze_slow_tests.py",
    ROOT / "scripts" / "analysis" / "analyze_slow_tests.py",
)


def _load_module(script_path: Path):
    module_name = f"analyze_slow_tests_{script_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_parse_pytest_json_preserves_rows_defaults_and_order(
    tmp_path: Path, script_path: Path
) -> None:
    report_path = tmp_path / "pytest-report.json"
    report_path.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "tests/test_alpha.py::test_first",
                        "call": {"duration": 1.25},
                        "outcome": "passed",
                    },
                    {
                        "nodeid": "tests/test_beta.py::test_missing_duration",
                        "call": {},
                        "outcome": "failed",
                    },
                    {"outcome": "skipped"},
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = _load_module(script_path).parse_pytest_json(report_path)

    assert rows == [
        {
            "nodeid": "tests/test_alpha.py::test_first",
            "duration": 1.25,
            "outcome": "passed",
        },
        {
            "nodeid": "tests/test_beta.py::test_missing_duration",
            "duration": 0,
            "outcome": "failed",
        },
        {"nodeid": "", "duration": 0, "outcome": "skipped"},
    ]
