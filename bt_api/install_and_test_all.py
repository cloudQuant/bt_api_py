#!/usr/bin/env python3
"""
Install and test all bt_api packages.

This script:
1. Finds all bt_api_* packages under the specified directories
2. Installs each package in editable mode (bt_api_base first)
3. Runs pytest on each package
4. Reports detailed results

Usage:
    python install_and_test_all.py
    python install_and_test_all.py --bt-api-dir /path/to/bt_api
    python install_and_test_all.py --add-package /path/to/bt_api_py
    python install_and_test_all.py --only bt_api_base
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple


class Result(NamedTuple):
    package: str
    success: bool
    duration: float
    output: str
    error: str | None


def setup_logging(verbose: bool = False):
    import logging
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(__name__)


def find_bt_api_packages(bt_api_dir: Path) -> list[Path]:
    """Find all bt_api_* directories that have a pyproject.toml."""
    packages = []
    for item in sorted(bt_api_dir.iterdir()):
        if item.is_dir() and item.name.startswith("bt_api_"):
            pyproject = item / "pyproject.toml"
            if pyproject.exists():
                packages.append(item)
    return packages


def sort_packages(packages: list[Path]) -> list[Path]:
    """Sort packages so bt_api_base is installed first."""
    base_pkg = None
    others = []
    for p in packages:
        if p.name == "bt_api_base":
            base_pkg = p
        else:
            others.append(p)
    result = []
    if base_pkg:
        result.append(base_pkg)
    result.extend(others)
    return result


def install_package(logger, package_path: Path, verbose: bool = False) -> tuple[bool, str]:
    """Install a package in editable mode."""
    package_name = package_path.name
    logger.info(f"[{package_name}] Installing in editable mode...")

    cmd = [
        sys.executable, "-m", "pip", "install", "-e", ".",
        "--quiet" if not verbose else "--verbose",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(package_path),
            capture_output=True,
            text=True,
            timeout=300,
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr
        if success:
            logger.info(f"[{package_name}] Install: SUCCESS")
        else:
            logger.error(f"[{package_name}] Install: FAILED (exit {result.returncode})")
        return success, output
    except subprocess.TimeoutExpired:
        logger.error(f"[{package_name}] Install: TIMEOUT (5min)")
        return False, "Installation timeout (>5min)"
    except Exception as e:
        logger.error(f"[{package_name}] Install: EXCEPTION {e}")
        return False, str(e)


def run_tests(logger, package_path: Path, verbose: bool = False) -> tuple[bool, str, str]:
    """Run pytest on a package."""
    package_name = package_path.name
    tests_dir = package_path / "tests"

    if not tests_dir.exists():
        logger.info(f"[{package_name}] No tests directory, skipping")
        return True, "", ""

    logger.info(f"[{package_name}] Running pytest...")

    cmd = [
        sys.executable, "-m", "pytest",
        str(tests_dir),
        "-v" if verbose else "-q",
        "--tb=short",
        "--color=yes",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(package_path),
            capture_output=True,
            text=True,
            timeout=600,
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr
        if success:
            logger.info(f"[{package_name}] Tests: SUCCESS")
        else:
            logger.error(f"[{package_name}] Tests: FAILED (exit {result.returncode})")
        return success, output, ""
    except subprocess.TimeoutExpired:
        logger.error(f"[{package_name}] Tests: TIMEOUT (10min)")
        return False, "", "Test timeout (>10min)"
    except Exception as e:
        logger.error(f"[{package_name}] Tests: EXCEPTION {e}")
        return False, "", str(e)


def print_summary(logger, results: list[Result], elapsed: float):
    """Print final summary."""
    total = len(results)
    passed = sum(1 for r in results if r.success)
    failed = total - passed

    logger.info("")
    logger.info("=" * 80)
    logger.info(f"FINAL SUMMARY ({elapsed:.1f}s total)")
    logger.info("=" * 80)

    for r in results:
        status = "PASS" if r.success else "FAIL"
        logger.info(f"  [{status}] {r.package}")

    logger.info("")
    logger.info(f"Total: {total} | Passed: {passed} | Failed: {failed}")

    if failed > 0:
        logger.info("")
        logger.info("FAILED PACKAGES:")
        for r in results:
            if not r.success:
                logger.info(f"  - {r.package}")
                if r.error:
                    logger.info(f"    Error: {r.error}")

    logger.info("=" * 80)

    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Install and test all bt_api packages")
    parser.add_argument(
        "--bt-api-dir",
        type=Path,
        default=None,
        help="Directory containing bt_api_* packages (default: script directory)",
    )
    parser.add_argument(
        "--add-package",
        type=Path,
        dest="extra_packages",
        default=[],
        action="append",
        help="Add individual package path (e.g., /path/to/bt_api_py)",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip installation, only run tests",
    )
    parser.add_argument(
        "--only",
        type=str,
        help="Only process this specific package (e.g., bt_api_base)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    all_packages: list[Path] = []
    bt_api_dirs = args.bt_api_dir if args.bt_api_dir else [Path(__file__).parent]

    for bt_api_dir in bt_api_dirs:
        if not bt_api_dir.exists():
            logger.warning(f"Directory not found, skipping: {bt_api_dir}")
            continue
        logger.info(f"Scanning: {bt_api_dir}")
        packages = find_bt_api_packages(bt_api_dir)
        all_packages.extend(packages)
        logger.info(f"  Found {len(packages)} packages: {[p.name for p in packages]}")

    for extra in args.extra_packages:
        if extra.exists() and extra.name.startswith("bt_api_"):
            all_packages.append(extra)
            logger.info(f"Added extra package: {extra.name}")
        else:
            logger.warning(f"Skipping invalid extra package: {extra}")

    all_packages = sort_packages(all_packages)
    logger.info(f"Total packages to process: {len(all_packages)}")

    if not all_packages:
        logger.error("No bt_api packages found!")
        sys.exit(1)

    if args.only:
        all_packages = [p for p in all_packages if p.name == args.only]
        if not all_packages:
            logger.error(f"Package not found: {args.only}")
            sys.exit(1)
        logger.info(f"Filtered to: {[p.name for p in all_packages]}")

    results: list[Result] = []
    start_time = datetime.now()

    for package_path in all_packages:
        package_name = package_path.name
        logger.info("")
        logger.info(f"{'─' * 40}")
        logger.info(f"Processing: {package_name}")
        logger.info(f"{'─' * 40}")

        install_success = True
        install_output = ""
        if not args.skip_install:
            install_start = datetime.now()
            install_success, install_output = install_package(logger, package_path, args.verbose)
            install_duration = (datetime.now() - install_start).total_seconds()
        else:
            install_duration = 0

        if install_success or args.skip_install:
            test_success, test_output, test_error = run_tests(logger, package_path, args.verbose)
        else:
            test_success = False
            test_output = ""
            test_error = "Skipped due to install failure"

        result = Result(
            package=package_name,
            success=install_success and test_success,
            duration=install_duration,
            output=install_output + "\n" + test_output,
            error=test_error if not test_success else None,
        )
        results.append(result)

    elapsed = (datetime.now() - start_time).total_seconds()
    success = print_summary(logger, results, elapsed)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
