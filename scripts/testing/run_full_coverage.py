#!/usr/bin/env python3
"""Run full test suite with coverage gate."""

import subprocess
import sys


def main() -> int:
    cmd = [
        sys.executable, "-m", "pytest", "tests",
        "--cov=bt_api_py",
        "--cov-report=term",
        "--cov-fail-under=40",
        "-n", "8",
        "--dist=loadgroup"
    ]

    print("=== Full Coverage Gate ===", flush=True)
    try:
        r = subprocess.run(cmd, cwd='.', text=True, capture_output=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        print("Full coverage gate timed out", flush=True)
        return 1

    if r.stdout:
        print(r.stdout)
    if r.stderr:
        print("--- STDERR ---")
        print(r.stderr)
    print(f"--- EXIT {r.returncode} ---", flush=True)
    return r.returncode

if __name__ == "__main__":
    raise SystemExit(main())
