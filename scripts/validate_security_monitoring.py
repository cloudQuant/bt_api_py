#!/usr/bin/env python3
"""Validate security_monitoring tests with ruff and pytest."""

import subprocess
import sys

def run_cmd(name: str, cmd: list[str], timeout: int = 180) -> int:
    """Run a command with timeout and return exit code."""
    print(f"=== {name} ===", flush=True)
    try:
        r = subprocess.run(cmd, cwd='.', text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"{name} timed out", flush=True)
        return 1
    
    if r.stdout:
        print(r.stdout)
    if r.stderr:
        print("--- STDERR ---")
        print(r.stderr)
    print(f"--- EXIT {r.returncode} ---", flush=True)
    return r.returncode

def main() -> int:
    overall = 0
    
    # 1. ruff check
    rc = run_cmd("ruff-security-compliance-tests", [sys.executable, "-m", "ruff", "check", "tests/test_security_compliance.py"])
    if rc != 0:
        overall = 1
    
    # 2. pytest security monitoring tests
    rc = run_cmd("pytest-security-monitoring-tests", [sys.executable, "-m", "pytest", "tests/test_security_compliance.py::TestSecurityMonitoring", "-v"])
    if rc != 0:
        overall = 1
    
    # 3. coverage for security monitoring module
    rc = run_cmd("coverage-security-monitoring-module", [
        sys.executable, "-m", "pytest", 
        "tests/test_security_compliance.py::TestSecurityMonitoring",
        "--cov=bt_api_py.security_compliance.monitoring.security_monitoring",
        "--cov-report=term",
        "--cov-fail-under=0"
    ])
    if rc != 0:
        overall = 1
    
    return overall

if __name__ == "__main__":
    raise SystemExit(main())
