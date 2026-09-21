"""Offline behavior contracts for the coverage report renderer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = (
    ROOT / "scripts" / "analyze_coverage.py",
    ROOT / "scripts" / "analysis" / "analyze_coverage.py",
)


def _load_module(script_path: Path):
    module_name = f"analyze_coverage_{script_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_generate_report_preserves_sorted_rows_and_coverage_order(
    monkeypatch: pytest.MonkeyPatch, script_path: Path
) -> None:
    analyzer = _load_module(script_path).CoverageAnalyzer()
    coverage_data = {
        "totals": {"percent_covered": 73.4},
        "files": {
            "src/zeta.py": {"summary": {"percent_covered": 20.0}},
            "src/high.py": {"summary": {"percent_covered": 80.0}},
            "src/alpha.py": {"summary": {"percent_covered": 59.9}},
            "src/exactly-sixty.py": {"summary": {"percent_covered": 60.0}},
        },
    }
    monkeypatch.setattr(analyzer, "get_all_exchanges", lambda: ["zeta", "alpha", "tested", "beta"])
    monkeypatch.setattr(analyzer, "get_tested_exchanges", lambda: {"tested"})
    monkeypatch.setattr(
        analyzer,
        "analyze_module_coverage",
        lambda: {
            "feeds": {"tests": 2},
            "registry": {"tests": 1},
            "containers": {"tests": 3},
        },
    )
    monkeypatch.setattr(analyzer, "run_coverage_analysis", lambda: coverage_data)

    report = analyzer.generate_report()

    assert report == "\n".join(
        [
            "# Test Coverage Analysis Report",
            "=" * 50,
            "",
            "## Exchange Coverage",
            "Total exchanges: 4",
            "Tested exchanges: 1 (25.0%)",
            "Untested exchanges: 3",
            "\n**Untested exchanges:**",
            "- alpha",
            "- beta",
            "- zeta",
            "\n## Module Test Distribution",
            "feeds: 2 test files",
            "registry: 1 test files",
            "containers: 3 test files",
            "\n## Overall Coverage: 73.4%",
            "\n## Files with Low Coverage (< 60%)",
            "- src/zeta.py: 20.0%",
            "- src/alpha.py: 59.9%",
            "\n## Critical Paths Analysis",
            "The following critical paths need more comprehensive testing:",
            "- Exchange connection/error handling",
            "- WebSocket stream reconnection logic",
            "- Rate limiting implementation",
            "- Data normalization across exchanges",
        ]
    )
