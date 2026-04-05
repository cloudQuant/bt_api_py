#!/usr/bin/env python3
"""Test coverage analysis tool for bt_api_py.

This script analyzes test coverage across exchanges and modules to identify gaps.
"""

import json
import os
from pathlib import Path

import pytest


class CoverageAnalyzer:
    """Analyze test coverage for bt_api_py project."""

    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.feeds_dir = self.project_root / "bt_api_py" / "feeds"
        self.tests_dir = self.project_root / "tests"

    def get_all_exchanges(self) -> list[str]:
        """Get list of all exchange directories."""
        exchanges = []
        for item in self.feeds_dir.iterdir():
            if item.is_dir() and item.name.startswith("live_"):
                exchange_name = item.name[5:]  # Remove 'live_' prefix
                exchanges.append(exchange_name)
        return sorted(exchanges)

    def get_tested_exchanges(self) -> set[str]:
        """Get exchanges that have test files."""
        tested = set()
        for test_file in self.tests_dir.rglob("test_*.py"):
            content = test_file.read_text(encoding="utf-8", errors="ignore").lower()
            for exchange in self.get_all_exchanges():
                if exchange in content:
                    tested.add(exchange)
        return tested

    def analyze_module_coverage(self) -> dict[str, dict]:
        """Analyze coverage by module."""
        modules = {
            "feeds": {"path": self.feeds_dir, "tests": 0, "coverage": 0},
            "containers": {
                "path": self.project_root / "bt_api_py" / "containers",
                "tests": 0,
                "coverage": 0,
            },
            "registry": {
                "path": self.project_root / "bt_api_py" / "registry.py",
                "tests": 0,
                "coverage": 0,
            },
            "event_bus": {
                "path": self.project_root / "bt_api_py" / "event_bus.py",
                "tests": 0,
                "coverage": 0,
            },
            "exceptions": {
                "path": self.project_root / "bt_api_py" / "exceptions.py",
                "tests": 0,
                "coverage": 0,
            },
        }

        # Count test files per module
        for test_file in self.tests_dir.rglob("test_*.py"):
            if "feeds" in str(test_file):
                modules["feeds"]["tests"] += 1
            elif "containers" in str(test_file):
                modules["containers"]["tests"] += 1
            elif "registry" in test_file.name:
                modules["registry"]["tests"] += 1
            elif "event_bus" in test_file.name:
                modules["event_bus"]["tests"] += 1
            elif "exceptions" in test_file.name:
                modules["exceptions"]["tests"] += 1

        return modules

    def run_coverage_analysis(self) -> dict:
        """Run coverage analysis and return results."""
        try:
            coverage_json = self.project_root / "coverage.json"
            previous_cwd = Path.cwd()
            os.chdir(self.project_root)
            try:
                result = pytest.main(
                    [
                        "--cov=bt_api_py",
                        "--cov-report=json",
                        "--tb=no",
                        "-q",
                    ]
                )
            finally:
                os.chdir(previous_cwd)

            if coverage_json.exists():
                with coverage_json.open() as f:
                    coverage_data = json.load(f)
                if result != 0:
                    print(f"Pytest exited with status {result}, using partial coverage data")
                return coverage_data
        except Exception as e:
            print(f"Error running coverage: {e}")

        return {}

    def generate_report(self) -> str:
        """Generate comprehensive coverage report."""
        all_exchanges = self.get_all_exchanges()
        tested_exchanges = self.get_tested_exchanges()
        untested_exchanges = set(all_exchanges) - tested_exchanges

        modules = self.analyze_module_coverage()
        coverage_data = self.run_coverage_analysis()

        report = []
        report.append("# Test Coverage Analysis Report")
        report.append("=" * 50)
        report.append("")

        # Exchange coverage
        tested_percentage = (
            len(tested_exchanges) / len(all_exchanges) * 100 if all_exchanges else 0.0
        )
        report.append("## Exchange Coverage")
        report.append(f"Total exchanges: {len(all_exchanges)}")
        report.append(f"Tested exchanges: {len(tested_exchanges)} ({tested_percentage:.1f}%)")
        report.append(f"Untested exchanges: {len(untested_exchanges)}")

        if untested_exchanges:
            report.append("\n**Untested exchanges:**")
            for exchange in sorted(untested_exchanges):
                report.append(f"- {exchange}")

        # Module coverage
        report.append("\n## Module Test Distribution")
        for module, data in modules.items():
            report.append(f"{module}: {data['tests']} test files")

        # Coverage percentage
        if coverage_data and "totals" in coverage_data:
            total_coverage = coverage_data["totals"]["percent_covered"]
            report.append(f"\n## Overall Coverage: {total_coverage:.1f}%")

            # Files with low coverage
            report.append("\n## Files with Low Coverage (< 60%)")
            for filename, data in coverage_data["files"].items():
                coverage = data["summary"]["percent_covered"]
                if coverage < 60:
                    report.append(f"- {filename}: {coverage:.1f}%")

        # Critical paths analysis
        report.append("\n## Critical Paths Analysis")
        report.append("The following critical paths need more comprehensive testing:")
        report.append("- Exchange connection/error handling")
        report.append("- WebSocket stream reconnection logic")
        report.append("- Rate limiting implementation")
        report.append("- Data normalization across exchanges")

        return "\n".join(report)


def main():
    """Run coverage analysis."""
    analyzer = CoverageAnalyzer()
    report = analyzer.generate_report()
    print(report)

    # Save report
    with open("coverage_analysis.md", "w") as f:
        f.write(report)

    print("\nReport saved to: coverage_analysis.md")


if __name__ == "__main__":
    main()
