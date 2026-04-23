#!/usr/bin/env python3
"""Analyze test performance and identify slow tests."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def parse_pytest_json(json_file: Path) -> List[Dict[str, Any]]:
    """Parse pytest JSON report and extract test durations."""
    with open(json_file) as f:
        data = json.load(f)

    tests = []
    for test in data.get("tests", []):
        tests.append(
            {
                "nodeid": test.get("nodeid", ""),
                "duration": test.get("call", {}).get("duration", 0),
                "outcome": test.get("outcome", ""),
            }
        )

    return tests


def analyze_tests(tests: List[Dict[str, Any]], threshold: float = 1.0) -> None:
    """Analyze and report slow tests."""
    # Sort by duration
    sorted_tests = sorted(tests, key=lambda x: x["duration"], reverse=True)

    # Filter slow tests
    slow_tests = [t for t in sorted_tests if t["duration"] > threshold]

    print(f"\n{'=' * 80}")
    print("Test Performance Analysis")
    print(f"{'=' * 80}\n")

    print(f"Total tests: {len(tests)}")
    print(f"Slow tests (>{threshold}s): {len(slow_tests)}")

    if slow_tests:
        print(f"\n{'Top 20 Slowest Tests:'}")
        print(f"{'-' * 80}")
        print(f"{'Duration':<12} {'Status':<10} {'Test'}")
        print(f"{'-' * 80}")

        for test in slow_tests[:20]:
            duration = f"{test['duration']:.2f}s"
            outcome = test["outcome"]
            nodeid = test["nodeid"]

            # Truncate long test names
            if len(nodeid) > 60:
                nodeid = nodeid[:57] + "..."

            print(f"{duration:<12} {outcome:<10} {nodeid}")

    # Statistics
    total_duration = sum(t["duration"] for t in tests)
    slow_duration = sum(t["duration"] for t in slow_tests)

    print(f"\n{'Statistics:'}")
    print(f"{'-' * 80}")
    print(f"Total test time: {total_duration:.2f}s")
    print(f"Slow test time: {slow_duration:.2f}s ({slow_duration / total_duration * 100:.1f}%)")
    print(f"Average test time: {total_duration / len(tests):.2f}s")

    if slow_tests:
        print(f"Average slow test time: {slow_duration / len(slow_tests):.2f}s")

    # Recommendations
    print(f"\n{'Recommendations:'}")
    print(f"{'-' * 80}")

    if len(slow_tests) > 10:
        print("• Consider marking slow tests with @pytest.mark.slow")
        print("• Run slow tests separately: ./scripts/run_tests.sh -m 'not slow'")

    network_tests = [
        t
        for t in slow_tests
        if "network" in t["nodeid"].lower()
        or "request" in t["nodeid"].lower()
        or "wss" in t["nodeid"].lower()
    ]
    if network_tests:
        print(f"• {len(network_tests)} slow tests appear to be network-related")
        print("  Consider using @pytest.mark.network and mocking for unit tests")

    if slow_duration > total_duration * 0.5:
        print("• Slow tests account for >50% of test time")
        print("  Consider optimizing or parallelizing these tests")

    print()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python analyze_slow_tests.py <pytest-json-report>")
        print("\nGenerate report with:")
        print("  pytest --json-report --json-report-file=report.json")
        sys.exit(1)

    json_file = Path(sys.argv[1])

    if not json_file.exists():
        print(f"Error: File not found: {json_file}")
        sys.exit(1)

    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    tests = parse_pytest_json(json_file)
    analyze_tests(tests, threshold)


if __name__ == "__main__":
    main()
